"""The hub's own accounts (P6 §1): registered nicks, their passwords and their roles.

Each account has a role: ``user``, ``op`` or ``admin``. A nick without an account is always
``user``. Accounts from TX.WTF come with S5; until then the hub reads a file
(``SUBLEVELS_ACCOUNTS_FILE``, by default ``<data_dir>/accounts.json`` when it exists). It looks
again when the file changes, so roles can change while the hub runs.

Passwords are stored as scrypt hashes, never in the clear. The file is managed with::

    python -m sublevels.nmdc.accounts FILE add NICK [--role op]   # the password from stdin
    python -m sublevels.nmdc.accounts FILE role NICK admin
    python -m sublevels.nmdc.accounts FILE remove NICK
    python -m sublevels.nmdc.accounts FILE list
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import time

ROLES = ("user", "op", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${b(salt).decode()}${b(h).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        want = base64.b64decode(h)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n),
                             r=int(r), p=int(p), dklen=len(want))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def _key(nick: str) -> str:
    return nick.casefold()


class FileAccounts:
    """``Accounts`` (hub.py) over a JSON file: ``{"accounts": {nick: {"password", "role"}}}``."""

    def __init__(self, path: str, *, clock=time.monotonic, recheck: float = 5.0):
        self.path = path
        self.clock = clock
        self.recheck = recheck
        self.accounts: dict[str, dict] = {}
        self.mtime = None
        self.checked = -1e18
        self.on_change = None  # called after a reload changed something
        self._load()

    def _load(self) -> None:
        self.checked = self.clock()
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            if self.accounts:
                self.accounts, self.mtime = {}, None
                if self.on_change:
                    self.on_change()
            return
        if mtime == self.mtime:
            return
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        accounts = {}
        for nick, a in (data.get("accounts") or {}).items():
            role = a.get("role", "user")
            if role not in ROLES or not isinstance(a.get("password"), str):
                raise ValueError(f"{self.path}: account {nick!r}: bad role or password")
            accounts[_key(nick)] = {"nick": nick, "password": a["password"], "role": role}
        changed = self.mtime is not None and accounts != self.accounts
        self.accounts, self.mtime = accounts, mtime
        if changed and self.on_change:
            self.on_change()

    def _fresh(self) -> None:
        if self.clock() - self.checked >= self.recheck:
            try:
                self._load()
            except (OSError, ValueError):
                pass  # keep the accounts already read

    def is_registered(self, nick: str) -> bool:
        self._fresh()
        return _key(nick) in self.accounts

    def check(self, nick: str, password: str) -> bool:
        a = self.accounts.get(_key(nick))
        return a is not None and verify_password(password, a["password"])

    def role(self, nick: str) -> str:
        self._fresh()
        a = self.accounts.get(_key(nick))
        return a["role"] if a else "user"

    def is_operator(self, nick: str) -> bool:
        return RANK[self.role(nick)] >= RANK["op"]


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"accounts": {}}


def _write(path: str, data: dict) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".accounts.", dir=d)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m sublevels.nmdc.accounts",
                                description="Manage the hub's accounts file.")
    p.add_argument("file")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="add or replace an account; the password is read from stdin")
    a.add_argument("nick")
    a.add_argument("--role", choices=ROLES, default="user")
    r = sub.add_parser("role", help="change an account's role")
    r.add_argument("nick")
    r.add_argument("role", choices=ROLES)
    rm = sub.add_parser("remove")
    rm.add_argument("nick")
    sub.add_parser("list")
    args = p.parse_args(argv)
    data = _read(args.file)
    accounts = data.setdefault("accounts", {})
    found = next((n for n in accounts if _key(n) == _key(getattr(args, "nick", ""))), None)
    if args.cmd == "list":
        for n, acc in sorted(accounts.items()):
            print(f"{n}\t{acc.get('role', 'user')}")
        return 0
    if args.cmd == "add":
        if sys.stdin.isatty():
            import getpass
            password = getpass.getpass(f"password for {args.nick}: ")
        else:
            password = sys.stdin.readline().rstrip("\n")
        if not password:
            print("no password given", file=sys.stderr)
            return 2
        if found and found != args.nick:
            del accounts[found]
        accounts[args.nick] = {"password": hash_password(password), "role": args.role}
    elif found is None:
        print(f"no account {args.nick!r}", file=sys.stderr)
        return 1
    elif args.cmd == "role":
        accounts[found]["role"] = args.role
    else:
        del accounts[found]
    _write(args.file, data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
