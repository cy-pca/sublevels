"""Fixtures. The service and HTTP tests need only fastapi and httpx. The Host and harness tests
need pycvc (from cvcpkg: pycvc-cp312), and the harness tests libcvc S7's
``pycvc.ariadne.testing``. Without them those tests skip, unless the environment asks for them:

    SUBLEVELS_REQUIRE_PYCVC=1     fail instead of skipping when pycvc is missing
    SUBLEVELS_REQUIRE_HARNESS=1   fail instead of skipping when pycvc.ariadne.testing is missing
"""

from __future__ import annotations

import os

import pytest


def _need(what: str, env: str, why: str):
    if os.environ.get(env) == "1":
        pytest.fail(f"{what}: {why} ({env}=1)")
    pytest.skip(f"{what}: {why}")


def _ariadne():
    try:
        import pycvc.ariadne as ariadne
    except ImportError as e:
        _need("pycvc.ariadne", "SUBLEVELS_REQUIRE_PYCVC", f"not importable ({e})")
    return ariadne


@pytest.fixture(scope="session")
def server():
    """The service on this process's one pycvc.ariadne Host, started (published), as
    ``python -m sublevels.server`` makes it. A program has one Host, so the session shares it."""
    _ariadne()
    from sublevels.server import build

    s = build()
    s.service.start()
    return s


@pytest.fixture(scope="session")
def harness(server):
    """libcvc S7's ServerHarness over the service's Host, publishing what the server document
    publishes."""
    try:
        from pycvc.ariadne.testing import ServerHarness
    except ImportError as e:
        _need("pycvc.ariadne.testing", "SUBLEVELS_REQUIRE_HARNESS",
              f"this pycvc has no ServerHarness (libcvc S7; {e})")
    from sublevels import NAMESPACE

    h = ServerHarness(server.host, publish=[NAMESPACE])
    yield h
    h.close()
