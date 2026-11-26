"""The hub's documents (roadmap D9, P2 §7): the message of the day and the rules.

Operators edit them through the admin API (``PUT /admin/documents/<name>``), and later through the
hub's own commands (P6). Vanilla NMDC users get them as chat lines from the hub bot: the message
of the day at login, and either one on ``+motd`` and ``+rules``. Native clients read them under
``sublevels.hubs.<hub_id>.documents``, and anyone can read them at ``GET /documents/<name>``.

With a data directory (``HubConfig.data_dir``) they are kept in ``<data_dir>/documents.json``, so
an edit outlives a restart; without one, edits last for the run. Until an operator edits one, a
document is the configured text (``HubConfig.motd``, ``HubConfig.rules``).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time

log = logging.getLogger("sublevels.documents")

NAMES = ("motd", "rules")
MAX_BYTES = 8192  # each, as UTF-8
MAX_LINES = 100


class DocumentError(ValueError):
    pass


class Documents:
    def __init__(self, defaults: dict[str, tuple[str, ...]], data_dir: str = "",
                 clock=time.time):
        self.clock = clock
        self.path = os.path.join(data_dir, "documents.json") if data_dir else ""
        self.docs = {n: {"text": "\n".join(defaults.get(n, ())), "updated": None, "by": ""}
                     for n in NAMES}
        self._topic: dict | None = None  # an admin's !topic (P6 §3), kept with the documents
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved.get("topic"), dict) and \
                        isinstance(saved["topic"].get("text"), str):
                    self._topic = saved["topic"]
                for n in NAMES:
                    if isinstance(saved.get(n), dict) and isinstance(saved[n].get("text"), str):
                        self.docs[n] = {"text": saved[n]["text"],
                                        "updated": saved[n].get("updated"),
                                        "by": str(saved[n].get("by", ""))}
            except (OSError, ValueError) as e:
                log.warning("documents: could not read %s (%s): using the configured text",
                            self.path, e)

    def lines(self, name: str) -> list[str]:
        text = self.docs[name]["text"]
        return text.split("\n") if text else []

    def get(self, name: str) -> dict:
        if name not in NAMES:
            raise KeyError(name)
        return {"name": name, **self.docs[name]}

    def set(self, name: str, text: str, by: str = "") -> dict:
        """Replace document ``name``; the text is checked, saved, and returned as stored."""
        if name not in NAMES:
            raise KeyError(name)
        text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise DocumentError(f"longer than {MAX_BYTES} bytes")
        if text.count("\n") + 1 > MAX_LINES:
            raise DocumentError(f"more than {MAX_LINES} lines")
        if any(ord(c) < 32 and c not in "\n\t" for c in text):
            raise DocumentError("control characters")
        self.docs[name] = {"text": text, "updated": round(self.clock(), 3), "by": by}
        self._save()
        return self.get(name)

    def topic(self, default: str) -> str:
        return self._topic["text"] if self._topic is not None else default

    def set_topic(self, text: str, by: str = "") -> None:
        self._topic = {"text": text, "updated": round(self.clock(), 3), "by": by}
        self._save()

    def _save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".documents.", dir=os.path.dirname(self.path))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # Only what operators edited: the rest follows the configured text.
            saved = {n: d for n, d in self.docs.items() if d["updated"] is not None}
            if self._topic is not None:
                saved["topic"] = self._topic
            json.dump(saved, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    def as_tree(self) -> dict:
        return {n: {"text": d["text"], "updated": d["updated"] or 0} for n, d in self.docs.items()}
