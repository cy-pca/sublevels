"""P1: the code page edge, escaping, ``$Lock`` and ``$Key`` (spec/nmdc/P1-framing.md)."""

from __future__ import annotations

import codecs
import secrets
import string

# -- the code page (P1 §2) -------------------------------------------------------------------------


class CodePage:
    """A single-byte code page at the NMDC edge.

    Decoding never fails: a byte the code page leaves unassigned decodes to the code point of the
    same number (Windows-1252's 0x81, 0x8D, 0x8F, 0x90 and 0x9D become U+0081 and so on), so
    ``encode(decode(b)) == b`` for every byte string. Encoding writes ``?`` for a character the code
    page cannot hold, never a numeric character reference.
    """

    def __init__(self, name: str = "cp1252"):
        self.name = codecs.lookup(name).name
        table = []
        for b in range(256):
            try:
                c = bytes([b]).decode(self.name)
            except UnicodeDecodeError:
                c = chr(b)
            if len(c) != 1:
                raise ValueError(f"{name}: not a single-byte code page")
            table.append(c)
        if len(set(table)) != 256 or "é".encode(self.name, errors="ignore") == "é".encode("utf-8"):
            raise ValueError(f"{name}: not a single-byte code page")
        # Decoding: the bytes read as Latin-1 (one code point per byte), then mapped.
        self._decode = {b: ord(c) for b, c in enumerate(table) if ord(c) != b}
        # Encoding: each code point the page holds maps to its byte; every other code point below
        # 256 maps to '?', and Latin-1's own replacement covers the rest.
        to_byte = {ord(c): b for b, c in enumerate(table)}
        self._encode = {cp: ord("?") for cp in range(256) if cp not in to_byte}
        self._encode.update({cp: b for cp, b in to_byte.items() if cp != b})

    def decode(self, data: bytes) -> str:
        return data.decode("latin-1").translate(self._decode)

    def encode(self, text: str) -> bytes:
        return text.translate(self._encode).encode("latin-1", errors="replace")


# -- escaping (P1 §3) ------------------------------------------------------------------------------

DCN_BYTES = (0, 5, 36, 96, 124, 126)


def escape(text: str) -> str:
    """Display text the hub writes: ``&`` as ``&amp;``, ``$`` as ``&#36;``, ``|`` as ``&#124;``."""
    return text.replace("&", "&amp;").replace("$", "&#36;").replace("|", "&#124;")


def unescape(text: str) -> str:
    """Display text the hub reads: the HTML scheme's three escapes and the DCN scheme's ``$`` and
    ``|``, and nothing else."""
    return (text.replace("&#36;", "$").replace("&#124;", "|").replace("/%DCN036%/", "$")
            .replace("/%DCN124%/", "|").replace("&amp;", "&"))


def dcn_escape(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b in DCN_BYTES:
            out += b"/%%DCN%03d%%/" % b
        else:
            out.append(b)
    return bytes(out)


def dcn_unescape(data: bytes) -> bytes:
    for b in DCN_BYTES:
        data = data.replace(b"/%%DCN%03d%%/" % b, bytes([b]))
    return data


# -- $Lock and $Key (P1 §4, §5) --------------------------------------------------------------------

LOCK_PREFIX = b"EXTENDEDPROTOCOL_sublevels_"
_LOCK_ALPHABET = string.ascii_letters + string.digits


def make_lock() -> bytes:
    """A fresh lock: ``EXTENDEDPROTOCOL_sublevels_`` and 24 random letters and digits."""
    return LOCK_PREFIX + "".join(secrets.choice(_LOCK_ALPHABET) for _ in range(24)).encode()


def lock_to_key(lock: bytes) -> bytes:
    """The ``$Key`` for ``lock`` (its bytes before `` Pk=``), DCN-escaped (P1 §5)."""
    n = len(lock)
    if n < 2:
        raise ValueError("a lock has at least two bytes")
    key = bytearray(n)
    for i in range(1, n):
        key[i] = lock[i] ^ lock[i - 1]
    key[0] = lock[0] ^ lock[-1] ^ lock[-2] ^ 5
    for i in range(n):
        key[i] = ((key[i] << 4) & 0xF0) | (key[i] >> 4)
    return dcn_escape(bytes(key))


def key_matches(lock: bytes, received: bytes) -> bool:
    """Whether a ``$Key`` argument as received answers ``lock``. Both sides are compared with the
    DCN escapes undone, so a client that escapes more or fewer bytes still matches."""
    return dcn_unescape(received) == dcn_unescape(lock_to_key(lock))
