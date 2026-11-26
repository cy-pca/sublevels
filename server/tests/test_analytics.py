"""D14: the hub's numbers as time series: sampling, rollups, storage, publishing, the admin API."""

import pytest

from nmdc_fakes import World
from sublevels.nmdc import HubConfig


class Tree:
    def __init__(self):
        self.values, self.series = {}, {}

    def set_tree(self, key, value):
        self.values[key] = value

    def set_series(self, key, values, fmt=".6g", limit=None):
        self.series[key] = list(values)


def world(tree=None):
    w = World(HubConfig(), tree=tree)
    w.wall = [1_800_000_000.0]  # on a 10 s, minute and hour boundary
    w.hub.analytics.clock = lambda: w.wall[0]
    return w


def step(w, seconds=10):
    w.wall[0] += seconds
    w.clock.advance(seconds)
    return w.hub.analytics.tick()


def test_gauges_and_counter_deltas():
    w = world()
    w.login("early")  # before the first sample: counted in it
    w.hub.disconnect(next(iter(w.hub.users.values())))
    first = w.hub.analytics.tick()
    assert first["users"] == 0 and first["logins"] == 1
    a, ca = w.login("alice", ip="10.0.0.1")
    w.login("bob")
    w.send(a, "<alice> hello|")
    v = step(w)
    assert v["users"] == 2 and v["logins"] == 2 and v["chat"] == 1 and v["chat_fanout"] == 2
    assert v["connections.nmdc"] == 2 and v["client.test"] == 2 and v["country.?"] == 2
    assert v["bytes_out"] > 0
    v = step(w)
    assert v["logins"] == 0 and v["chat"] == 0 and v["users"] == 2  # changes, not totals
    w.hub.config  # refusals by reason
    s, c = w.connect()
    w.send(s, "$Supports |", f"$Key {w.key_for(c)}|", "$ValidateNick sublevels|")
    v = step(w)
    assert v["refusals"] == 1 and v["refusal.nick_reserved"] == 1


def test_minute_and_hour_rollups():
    w = world()
    a = w.hub.analytics
    a.tick()
    w.login("alice")
    for _ in range(5):  # 0-50 s: the minute; it closes on the sample at 60 s
        step(w)
    w.send(next(iter(w.hub.users.values())), "<alice> x|")
    step(w)
    (t, users), = a.rings["1m"].query("users", 0, 2e9)
    assert t == 1_800_000_000.0 and users == pytest.approx(5 / 6)  # the mean: 0, then 1 five times
    m = a.rings["1m"].query("logins", 0, 2e9)
    assert m == [(1_800_000_000.0, 1.0)]  # the sum of the minute's changes
    a.stop()  # closes the partial minute and hour
    assert [p[1] for p in a.rings["1m"].query("chat", 0, 2e9)] == [0.0, 1.0]


def test_the_user_count_series_is_published_and_nothing_else():
    tree = Tree()
    w = world(tree)
    w.hub.analytics.tick()
    w.login("alice")
    step(w)
    assert tree.series == {"sublevels.hubs.main.series.users": [0.0, 1.0]}


def test_stored_queried_and_pruned(tmp_path):
    pytest.importorskip("sqlalchemy")
    from sublevels.nmdc.connlog import ConnLog

    w = world()
    w.hub.connlog = ConnLog(f"sqlite:///{tmp_path / 'c.sqlite3'}")
    a = w.hub.analytics
    a.tick()
    w.login("alice")
    step(w)
    old = w.wall[0]
    step(w, 86400 + 3600)  # a day and an hour on: the hour closes, and prunes
    w.hub.connlog.flush()
    w.hub.connlog = log2 = ConnLog(w.hub.connlog.url)
    pts = a.query("users", "10s", since=0, until=2e9)["points"]
    assert [p[0] for p in pts] == [w.wall[0]]  # the day-old 10 s samples are gone
    hours = a.query("users", "1h", since=0, until=2e9)
    assert hours["kind"] == "gauge" and hours["points"][0][0] == 1_800_000_000.0
    assert a.query("logins", "1m", since=0, until=2e9)["kind"] == "counter"
    assert old  # (the sample before the jump was pruned with the rest of its day)
    log2.flush()


def test_admin_api_and_room_call():
    from fastapi.testclient import TestClient

    from sublevels.api import create_app
    from sublevels.service import Service

    w = world()
    w.hub.analytics.tick()
    w.login("alice")
    step(w)
    service = Service()
    service.hub = w.hub
    service.admin_token = "t0ken"
    c = TestClient(create_app(service))
    auth = {"Authorization": "Bearer t0ken"}
    assert c.get("/admin/analytics").status_code == 401
    assert "users" in c.get("/admin/analytics", headers=auth).json()["metrics"]
    r = c.get(f"/admin/analytics?metric=users&since=0&until={w.wall[0]}", headers=auth).json()
    assert [p[1] for p in r["points"]] == [0.0, 1.0]
    assert c.get("/admin/analytics?metric=users&resolution=5s", headers=auth).status_code == 422
    r = service.operator_call({"id": "joe", "roles": ["admin"]}, "analytics", "users", "10s", "0")
    assert r["ok"] and len(r["points"]) == 2
    assert service.operator_call({"id": "joe"}, "analytics", "users", "5s", "")["ok"] is False


def test_the_timer():
    w = world()
    a = w.hub.analytics
    a.start(w.clock.call_later)
    for _ in range(3):
        w.wall[0] += 10
        w.clock.advance(10)
    assert len(a.rings["10s"].query("users", 0, 2e9)) == 3
    a.stop()
    w.clock.advance(30)
    assert len(a.rings["10s"].query("users", 0, 2e9)) == 3
