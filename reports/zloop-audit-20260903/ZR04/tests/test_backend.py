"""CodexSdkBackend tests (VOL-12 §2/§3, D-12 single-runtime v1).

The real ``openai_codex`` package lives only in the project venv; the system
python that runs pytest may not have it — and live auth is broken on this
machine anyway. So every test injects a FAKE ``openai_codex`` module into
``sys.modules`` BEFORE importing ``zloop.backend.codex_sdk`` fresh: the
guarded import then binds to the fake, and the SDK call shapes are asserted
against recorded objects. No network, no subprocess, no auth.
"""
from __future__ import annotations

import collections
import importlib
import sys
import types
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop.backend.base import (  # noqa: E402
    AgentBackend,
    BackendUnavailable,
    WorkerReport,
    WorkerSpec,
)

SDK_MOD = "zloop.backend.codex_sdk"


# ------------------------------------------------------- fake openai_codex

class _FakeSandbox(str, Enum):
    read_only = "read-only"
    workspace_write = "workspace-write"
    full_access = "full-access"


class _FakeCodexConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeTurnHandle:
    def __init__(self, input=None, **kwargs):
        self.turn_kwargs = {"input": input, **kwargs}
        self.interrupted = False
        self.interrupt_error = None
        self.run_result = None
        self.run_error = None
        self.run_calls = 0
        self.stream_events = []

    def run(self):
        self.run_calls += 1
        if self.run_error is not None:
            raise self.run_error
        return self.run_result

    def interrupt(self):
        if self.interrupt_error is not None:
            raise self.interrupt_error
        self.interrupted = True
        return "interrupted"

    def stream(self):
        return iter(list(self.stream_events))


class _FakeThread:
    def __init__(self, **kwargs):
        self.start_kwargs = kwargs
        self.turns = []

    def turn(self, input=None, **kwargs):
        handle = _FakeTurnHandle(input=input, **kwargs)
        self.turns.append(handle)
        return handle


class _FakeCodex:
    instances: list = []

    def __init__(self, config=None):
        self.config = config
        self.threads = []
        _FakeCodex.instances.append(self)

    def thread_start(self, **kwargs):
        thread = _FakeThread(**kwargs)
        self.threads.append(thread)
        return thread


# TurnResult-shaped namedtuple (mirrors openai_codex._run.TurnResult).
TurnResult = collections.namedtuple(
    "TurnResult",
    "id status error started_at completed_at duration_ms final_response items usage",
    defaults=(None, None, None, None, None, [], None),
)


def _fake_module() -> types.ModuleType:
    mod = types.ModuleType("openai_codex")
    mod.Sandbox = _FakeSandbox
    mod.Codex = _FakeCodex
    mod.AsyncCodex = type("AsyncCodex", (), {})  # imported by codex_sdk, unused
    mod.CodexConfig = _FakeCodexConfig
    mod.TurnResult = TurnResult
    return mod


def _fresh_import():
    """Import zloop.backend.codex_sdk anew so its guarded import re-runs."""
    sys.modules.pop(SDK_MOD, None)
    return importlib.import_module(SDK_MOD)


@pytest.fixture()
def codex(monkeypatch):
    mod = _fake_module()
    monkeypatch.setitem(sys.modules, "openai_codex", mod)
    sdk = _fresh_import()
    _FakeCodex.instances.clear()
    yield mod, sdk


def _spec(tmp_path, **kw):
    base = {"launch_id": "L0001", "workspace": tmp_path / "ws",
            "prompt": "implement packet P1"}
    return WorkerSpec(**{**base, **kw})


def _started(backend, spec):
    launch = backend.start(spec)
    client = _FakeCodex.instances[-1]
    thread = client.threads[-1]
    handle = thread.turns[-1]
    return launch, client, thread, handle


# ------------------------------------------------------------- construction

def test_construction_unavailable_raises(monkeypatch):
    # None in sys.modules makes `from openai_codex import ...` raise ImportError.
    monkeypatch.setitem(sys.modules, "openai_codex", None)
    sdk = _fresh_import()
    assert sdk.CODEX_SDK_AVAILABLE is False
    with pytest.raises(BackendUnavailable, match=r"zloop\[codex\]"):
        sdk.CodexSdkBackend()


def test_agents_disabled_config(codex):
    mod, sdk = codex
    sdk.CodexSdkBackend()  # agents_disabled=True by default
    cfg = mod.Codex.instances[-1].config
    assert cfg.config_overrides == ("agents.enabled=false",
                                    "features.multi_agent=false")
    sdk.CodexSdkBackend(agents_disabled=False)
    assert mod.Codex.instances[-1].config.config_overrides == ()


def test_health(codex):
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    assert backend.health() == {"available": True,
                                "auth": "unknown-until-live-test"}


def test_protocol_conformance(codex):
    _mod, sdk = codex
    assert isinstance(sdk.CodexSdkBackend(), AgentBackend)


# --------------------------------------------------------------------- start

def test_start_signatures(codex, tmp_path):
    mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    spec = _spec(tmp_path)
    launch, client, thread, handle = _started(backend, spec)
    assert len(client.threads) == 1
    assert thread.start_kwargs["cwd"] == str(spec.workspace)
    assert thread.start_kwargs["sandbox"] is mod.Sandbox.workspace_write
    assert "model" not in thread.start_kwargs  # nothing configured
    assert handle.turn_kwargs["input"] == spec.prompt
    assert handle.turn_kwargs["sandbox"] is mod.Sandbox.workspace_write
    # thread + handle stored keyed by launch_id; handle is the record itself
    assert backend.launch_record(spec.launch_id) is launch
    assert launch.launch_id == spec.launch_id
    assert launch.thread is thread
    assert launch.handle is handle


def test_model_passthrough(codex, tmp_path):
    mod, sdk = codex
    backend = sdk.CodexSdkBackend(model="gpt-5.6")
    _started(backend, _spec(tmp_path))  # spec has no model -> backend default
    assert mod.Codex.instances[-1].threads[-1].start_kwargs["model"] == "gpt-5.6"
    _started(backend, _spec(tmp_path, model="kimi-k3"))  # spec wins
    assert mod.Codex.instances[-1].threads[-1].start_kwargs["model"] == "kimi-k3"


# -------------------------------------------------------------- wait/collect

def test_wait_terminal_then_collect_reuses_result(codex, tmp_path):
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    launch, _c, _t, handle = _started(backend, _spec(tmp_path))
    handle.run_result = TurnResult("t1", "completed")
    assert backend.wait(launch) == "terminal"
    assert handle.run_calls == 1
    report = backend.collect(launch)
    assert handle.run_calls == 1  # collect must not re-run a finished turn
    assert report.status == "completed"


def test_collect_completed_with_none_final_response(codex, tmp_path):
    # VOL-12 §2 hard rule: final_response=None is a legal completion.
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    launch, _c, _t, handle = _started(backend, _spec(tmp_path))
    handle.run_result = TurnResult("t1", "completed", final_response=None)
    report = backend.collect(launch)
    assert report == WorkerReport(launch_id=launch.launch_id,
                                  status="completed",
                                  final_text=None,
                                  terminal_marker_seen=True,
                                  error=None)


def test_collect_completed_with_text(codex, tmp_path):
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    launch, _c, _t, handle = _started(backend, _spec(tmp_path))
    handle.run_result = TurnResult("t1", "completed", final_response="done")
    report = backend.collect(launch)
    assert report.status == "completed"
    assert report.final_text == "done"
    assert report.terminal_marker_seen is True


def test_collect_failed_status(codex, tmp_path):
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    launch, _c, _t, handle = _started(backend, _spec(tmp_path))
    handle.run_result = TurnResult(
        "t1", "failed", error=SimpleNamespace(message="provider 500"))
    report = backend.collect(launch)
    assert report.status == "failed"
    assert report.terminal_marker_seen is False
    assert report.error == "provider 500"


def test_collect_interrupted_is_incomplete(codex, tmp_path):
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    launch, _c, _t, handle = _started(backend, _spec(tmp_path))
    handle.run_result = TurnResult("t1", "interrupted", final_response="partial")
    report = backend.collect(launch)
    assert report.status == "incomplete"
    assert report.terminal_marker_seen is False
    assert report.final_text == "partial"


def test_wait_unknown_maps_to_failed(codex, tmp_path):
    # The real SDK raises RuntimeError for failed turns inside run().
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    launch, _c, _t, handle = _started(backend, _spec(tmp_path))
    handle.run_error = RuntimeError("turn failed: provider 500")
    assert backend.wait(launch) == "unknown"
    report = backend.collect(launch)
    assert report.status == "failed"
    assert "provider 500" in (report.error or "")


def test_collect_without_wait_runs_once(codex, tmp_path):
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    launch, _c, _t, handle = _started(backend, _spec(tmp_path))
    handle.run_result = TurnResult("t1", "completed")
    report = backend.collect(launch)  # self-sufficient collect
    assert report.status == "completed"
    assert handle.run_calls == 1


# ---------------------------------------------------------------- interrupt

def test_interrupt_success_and_failure(codex, tmp_path):
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    launch, _c, _t, handle = _started(backend, _spec(tmp_path))
    assert backend.interrupt(launch) is True
    assert handle.interrupted is True
    handle.interrupt_error = RuntimeError("no active turn")
    assert backend.interrupt(launch) is False


# ------------------------------------------------------------------- stream

def test_stream_passthrough(codex, tmp_path):
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    launch, _c, _t, handle = _started(backend, _spec(tmp_path))
    handle.stream_events = ["turn/started", "turn/completed"]
    assert list(backend.stream(launch)) == ["turn/started", "turn/completed"]


def test_unknown_handle_rejected(codex):
    _mod, sdk = codex
    backend = sdk.CodexSdkBackend()
    with pytest.raises(ValueError):
        backend.wait("no-such-launch")


# -------------------------------------------------- worker_env_vars (D-5)

def test_worker_env_vars_creates_empty_requirements(codex, monkeypatch, tmp_path):
    _mod, sdk = codex
    monkeypatch.setenv("ZLOOP_DATA", str(tmp_path))
    env = sdk.worker_env_vars()
    req = tmp_path / "workers" / "requirements-empty.toml"
    assert env == {"CODEX_LOOP_REQUIREMENTS_TOML": str(req)}
    assert req.exists()
    lines = req.read_text(encoding="utf-8").splitlines()
    # comment-only file: no active settings (D-5 neutralizer)
    assert lines and all(line.startswith("#") for line in lines if line.strip())
    # idempotent + stable content
    assert sdk.worker_env_vars() == env
