"""Tests for the optional external readiness gate.

Scope discipline: LOOP owns exactly one external fact -- an operator may
configure ``CODEX_LOOP_EXTERNAL_READY_URL``; only a 2xx response admits new
worker births.  These tests pin that contract and the portable regression
(no URL configured => ready, zero HTTP traffic), not any gateway protocol.
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

from harness.orchestration_common import (EXTERNAL_READY_URL_ENV,
                                          external_ready)

REPO = Path(__file__).resolve().parents[2]


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, n: int = -1) -> bytes:
        return b"{}"


# ---------------------------------------------------------------------------
# Tests 1: portable regression -- unset/blank URL must be ready and must not
# produce any HTTP request at all.
# ---------------------------------------------------------------------------

def _forbid_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("external_ready issued an HTTP request")

    monkeypatch.setattr("urllib.request.urlopen", _fail)


@pytest.mark.parametrize("value", [None, "", "   ", "\t"])
def test_unset_or_blank_url_is_ready_without_http(
        monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    _forbid_http(monkeypatch)
    if value is None:
        monkeypatch.delenv(EXTERNAL_READY_URL_ENV, raising=False)
    else:
        monkeypatch.setenv(EXTERNAL_READY_URL_ENV, value)
    assert external_ready() is True


# ---------------------------------------------------------------------------
# Tests 2-4: when set, only a 2xx status admits births; the body is never
# parsed (a non-JSON body with 200 stays ready, a JSON body with 503 does not).
# ---------------------------------------------------------------------------

def test_200_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXTERNAL_READY_URL_ENV, "http://127.0.0.1:9/readyz")

    def _fake(url: object, timeout: object = None) -> _FakeResponse:
        assert "9/readyz" in str(url)
        return _FakeResponse(200)

    monkeypatch.setattr("urllib.request.urlopen", _fake)
    assert external_ready() is True


def test_204_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXTERNAL_READY_URL_ENV, "http://example.invalid/readyz")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda url, timeout=None: _FakeResponse(204))
    assert external_ready() is True


def test_503_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXTERNAL_READY_URL_ENV, "http://example.invalid/readyz")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda url, timeout=None: _FakeResponse(503))
    assert external_ready() is False


def test_404_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXTERNAL_READY_URL_ENV, "http://example.invalid/readyz")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda url, timeout=None: _FakeResponse(404))
    assert external_ready() is False


# ---------------------------------------------------------------------------
# Tests 5-6: transport failures are contained -- refused / timeout must return
# False without raising, using real sockets (no protocol mocks).
# ---------------------------------------------------------------------------

def test_connection_refused_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # The listener is gone: nothing accepts on this port any more.
    monkeypatch.setenv(EXTERNAL_READY_URL_ENV,
                       "http://127.0.0.1:%d/readyz" % port)
    assert external_ready(timeout=2.0) is False


def test_timeout_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def hold() -> None:
        try:
            conn, _ = srv.accept()
            time.sleep(2.0)  # never answer within the probe timeout
            conn.close()
        except OSError:
            pass
        finally:
            srv.close()

    worker = threading.Thread(target=hold, daemon=True)
    worker.start()
    try:
        monkeypatch.setenv(EXTERNAL_READY_URL_ENV,
                           "http://127.0.0.1:%d/readyz" % port)
        assert external_ready(timeout=0.3) is False
    finally:
        worker.join(timeout=4.0)


def test_transport_exception_is_contained(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXTERNAL_READY_URL_ENV, "http://example.invalid/readyz")

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert external_ready() is False


# ---------------------------------------------------------------------------
# dispatch.py birth gate: same contract, same single entry point.
# ---------------------------------------------------------------------------

def test_dispatch_health_gate_portable_mode_skips_http(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import dispatch as dispatch_mod

    _forbid_http(monkeypatch)
    monkeypatch.delenv(EXTERNAL_READY_URL_ENV, raising=False)
    recorded: list[dict] = []
    monkeypatch.setattr(dispatch_mod, "update_throttle_state",
                        lambda **kwargs: recorded.append(kwargs))
    events: list[tuple] = []
    monkeypatch.setattr(dispatch_mod, "append_event",
                        lambda pid, name, detail: events.append((pid, name)))

    dispatch_mod.health_gate("pkt-1")

    assert recorded and recorded[0]["last_health"]["status"] == "disabled"
    assert ("pkt-1", "spawn_health_gate_passed") in events


def test_dispatch_health_gate_failure_blocks_births(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import dispatch as dispatch_mod

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv(EXTERNAL_READY_URL_ENV,
                       "http://127.0.0.1:%d/readyz" % port)
    recorded: list[dict] = []
    monkeypatch.setattr(dispatch_mod, "update_throttle_state",
                        lambda **kwargs: recorded.append(kwargs))
    monkeypatch.setattr(dispatch_mod, "append_event", lambda *a: None)

    with pytest.raises(dispatch_mod.BirthThrottleError):
        dispatch_mod.health_gate("pkt-1")

    assert recorded[0]["blocked_until"] > 0


# ---------------------------------------------------------------------------
# Test 7: source boundary -- production source no longer knows OpenCodex's
# host, port, path, or the retired env-var name.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("subdir", ["harness", "hooks", "metering"])
def test_production_source_has_no_gateway_specific_readiness(
        subdir: str) -> None:
    banned = ("127.0.0.1:10100", "/healthz", "LOOP_OPENCODEX_HEALTH_URL",
              "opencodex_healthy")
    for py in sorted((REPO / subdir).glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, "%s still references %s" % (py.name, token)
