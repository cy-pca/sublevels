"""The entry point's options: no pycvc."""

import pytest

from sublevels.server import DEFAULT_HTTP, parse_args, split_address


def test_split_address():
    assert split_address("127.0.0.1:8411") == ("127.0.0.1", 8411)
    assert split_address("[::1]:0") == ("::1", 0)
    assert split_address(":80") == ("127.0.0.1", 80)
    for bad in ("8411", "host:", "host:http", "host:70000"):
        with pytest.raises(ValueError):
            split_address(bad)


def test_http_listen_default_env_and_flag(monkeypatch):
    monkeypatch.delenv("SUBLEVELS_HTTP_LISTEN", raising=False)
    assert parse_args([]).http_listen == DEFAULT_HTTP
    monkeypatch.setenv("SUBLEVELS_HTTP_LISTEN", "0.0.0.0:9000")
    assert parse_args([]).http_listen == "0.0.0.0:9000"
    assert parse_args(["--http-listen", "off"]).http_listen == "off"


def test_bad_http_listen_is_a_usage_error():
    with pytest.raises(SystemExit) as e:
        parse_args(["--http-listen", "nowhere"])
    assert e.value.code == 2
