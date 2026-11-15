"""The service layer alone: no pycvc, no network."""

from sublevels import __version__
from sublevels.service import Service

T0 = 1_800_000_000.0  # 2027-01-15T08:00:00Z


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Tree:
    """Records set_tree as a Host would apply it."""

    def __init__(self):
        self.trees = {}

    def set_tree(self, key, value):
        self.trees[key] = value


def test_server_info():
    s = Service(clock=Clock())
    assert s.server_info() == {"version": __version__, "started": "2027-01-15T08:00:00+00:00",
                               "status": "starting"}


def test_health_reports_version_and_uptime():
    clock = Clock()
    s = Service(clock=clock)
    clock.t += 2.5
    assert s.health() == {"status": "ok", "version": __version__, "uptime": 2.5}


def test_uptime_never_negative():
    clock = Clock()
    s = Service(clock=clock)
    clock.t -= 10
    assert s.uptime() == 0.0


def test_ping():
    assert Service().ping("alice") == {"pong": True, "version": __version__, "who": "alice"}


def test_start_and_stop_publish_status():
    tree = Tree()
    s = Service(tree, clock=Clock())
    assert tree.trees == {}  # nothing is written until it starts
    s.start()
    assert tree.trees["sublevels.server"]["status"] == "up"
    assert tree.trees["sublevels.server"]["version"] == __version__
    s.stop()
    assert tree.trees["sublevels.server"]["status"] == "stopping"


def test_publish_without_a_tree_is_a_no_op():
    Service().start()
