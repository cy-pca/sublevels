"""D9, P2 §7: the message of the day and the rules, as documents operators edit."""

import json

import pytest
from fastapi.testclient import TestClient

from nmdc_fakes import World
from sublevels.api import create_app
from sublevels.nmdc import HubConfig
from sublevels.nmdc.documents import MAX_BYTES, DocumentError, Documents
from sublevels.service import Service


class Tree:
    def __init__(self):
        self.values = {}

    def set_tree(self, key, value):
        self.values[key] = value


def bot_lines(conn):
    return [m[len("<sublevels> "):-1] for m in conn.take() if m.startswith("<sublevels> ")]


def test_configured_text_until_edited():
    w = World(HubConfig(motd=("Hello.", "Second line."), rules=("1. Be kind.",)))
    s, conn = w.login("alice")
    assert bot_lines(conn) == ["Hello.", "Second line.",
                               "Type +rules to read the rules, +help for the hub's commands."]
    w.send(s, "<alice> +rules|")
    assert bot_lines(conn) == ["1. Be kind."]


def test_an_edit_reaches_login_commands_and_the_tree():
    tree = Tree()
    w = World(HubConfig(), tree=tree)
    w.hub.set_document("motd", "New $motd |here\r\nline two\n", by="op")
    w.hub.set_document("rules", "No spam.")
    s, conn = w.login("alice")
    assert bot_lines(conn)[:2] == ["New &#36;motd &#124;here", "line two"]
    w.send(s, "$To: sublevels From: alice $<alice> +motd|")
    assert [m for m in conn.take() if "line two" in m] == [
        "$To: alice From: sublevels $<sublevels> line two|"]
    w.clock.advance(2)  # the publish timer
    docs = tree.values["sublevels.hubs.main"]["documents"]
    assert docs["motd"]["text"] == "New $motd |here\nline two" and docs["rules"]["text"] == "No spam."


def test_an_empty_document():
    w = World()
    w.hub.set_document("rules", "")
    s, conn = w.login("alice")
    w.send(s, "<alice> +rules|")
    assert bot_lines(conn)[-1] == "(none yet)"


def test_limits():
    d = Documents({})
    for bad in ("x" * (MAX_BYTES + 1), "\n".join(["l"] * 101), "bell\x07"):
        with pytest.raises(DocumentError):
            d.set("motd", bad)
    with pytest.raises(KeyError):
        d.set("faq", "x")


def test_kept_in_the_data_dir(tmp_path):
    d = Documents({"motd": ("default",)}, str(tmp_path))
    assert d.lines("motd") == ["default"]
    d.set("motd", "edited", by="op")
    saved = json.loads((tmp_path / "documents.json").read_text())
    assert saved["motd"]["text"] == "edited" and saved["motd"]["by"] == "op"
    again = Documents({"motd": ("default",), "rules": ("r",)}, str(tmp_path))
    assert again.lines("motd") == ["edited"] and again.lines("rules") == ["r"]


def test_a_damaged_file_falls_back(tmp_path):
    (tmp_path / "documents.json").write_text("{not json")
    assert Documents({"motd": ("default",)}, str(tmp_path)).lines("motd") == ["default"]


def test_motd_and_rules_files(tmp_path):
    (tmp_path / "motd.txt").write_text("from a file\nline 2\n")
    cfg = HubConfig.from_env({"SUBLEVELS_MOTD_FILE": str(tmp_path / "motd.txt"),
                              "SUBLEVELS_DATA_DIR": str(tmp_path / "data")})
    assert cfg.motd == ("from a file", "line 2") and cfg.data_dir == str(tmp_path / "data")


def test_api():
    w = World()
    service = Service()
    service.hub = w.hub
    service.admin_token = "t0ken"
    c = TestClient(create_app(service))
    assert c.get("/documents/rules").json()["text"] == "\n".join(HubConfig().rules)
    assert c.get("/documents/faq").status_code == 404
    assert c.put("/admin/documents/motd", json={"text": "hi"}).status_code == 401
    auth = {"Authorization": "Bearer t0ken"}
    r = c.put("/admin/documents/motd", json={"text": "hi", "by": "op"}, headers=auth)
    assert r.status_code == 200 and r.json()["by"] == "op"
    assert c.get("/documents/motd").json()["text"] == "hi"
    assert c.put("/admin/documents/motd", json={"text": "x" * 9000}, headers=auth).status_code == 422
