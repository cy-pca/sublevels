"""TX.WTF beside the hub: embedded by default, a separate instance, or off."""

import sys
import types

import pytest

from sublevels import txwtf_service as tx


def test_plans():
    assert tx.plan(None, None).mode == "embedded"
    assert tx.plan(None, None).url == "http://127.0.0.1:8086"
    assert tx.plan("embedded", "0.0.0.0:9000").listen == "0.0.0.0:9000"
    ext = tx.plan("https://tx.example", None)
    assert (ext.mode, ext.url) == ("external", "https://tx.example")
    assert tx.plan("off", None).mode == "off"
    with pytest.raises(ValueError):
        tx.plan("sometimes", None)


@pytest.fixture
def fake_txwtf(monkeypatch):
    """A stand-in txwtf.api with a create_app, so no TX.WTF is needed."""
    pkg, api = types.ModuleType("txwtf"), types.ModuleType("txwtf.api")
    app = object()
    api.create_app = lambda: app
    pkg.api = api
    monkeypatch.setitem(sys.modules, "txwtf", pkg)
    monkeypatch.setitem(sys.modules, "txwtf.api", api)
    return app


def test_embedded_serves_txwtf_on_the_loop(fake_txwtf):
    served = []
    p = tx.start(None, "127.0.0.1:9123", lambda app, host, port: served.append((app, host, port)))
    assert served == [(fake_txwtf, "127.0.0.1", 9123)]
    assert p.as_tree() == {"mode": "embedded", "url": "http://127.0.0.1:9123"}


def test_default_without_txwtf_warns_and_runs_without_it(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "txwtf", None)  # importing it raises ImportError
    served = []
    p = tx.start(None, None, lambda *a, **k: served.append(a))
    assert p.mode == "off" and served == [] and "not installed" in caplog.text


def test_embedded_asked_for_without_txwtf_stops(monkeypatch):
    monkeypatch.setitem(sys.modules, "txwtf", None)
    with pytest.raises(SystemExit):
        tx.start("embedded", None, lambda *a, **k: None)


def test_external_and_off_serve_nothing():
    served = []
    assert tx.start("https://tx.example", None, lambda *a, **k: served.append(a)).mode == "external"
    assert tx.start("off", None, lambda *a, **k: served.append(a)).mode == "off"
    assert served == []
