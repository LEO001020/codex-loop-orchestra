"""zloop.backend.codex_sdk — CodexSdkBackend, the default backend (VOL-12 §2).

SDK-first against PyPI ``openai-codex`` (module ``openai_codex``, 0.147.0
pinned on this machine, VOL-02 §4):

    client = Codex(CodexConfig())                  # reuses existing CLI login
    thread = client.thread_start(cwd=..., sandbox=Sandbox.workspace_write)
    handle = thread.turn(input=..., sandbox=Sandbox.workspace_write)
    result = handle.run()                           # TurnResult

Runtime shape is D-12 (v1): ONE supervisor process + ONE client + N
concurrent turns + N independent workspaces. There is no worker-host, no
client sharding, no launch->host mapping (VOL-12 §3); isolation rests on the
per-launch workspace and the fencing in ``zloop.wave``, never on process
count.

Honest v1 limitations (documented, not hidden):
- ``wait()`` supports bounded timeout and interruption on timeout. On an SDK
  error ``wait`` returns ``'unknown'`` — the caller treats the launch as
  ambiguous (I44 discipline: provider status is never S authority, VOL-12 §5).
- ``model`` is passed to ``thread_start`` only when set (spec.model wins
  over the backend default).
- ``agents_disabled`` is applied at client construction via
  ``CodexConfig(config_overrides=("agents.enabled=false",
  "features.multi_agent=false"))`` (VOL-12 §4 gate 1; verifying the actual
  spawned tool catalog is an M8 probe).
- ``spec.network`` / ``spec.max_turns`` / ``spec.env_extra`` are NOT
  physically enforced by this backend yet: ``workspace_write`` implies
  ``network_access=false`` by SDK default, but the double canary is an M8
  probe (VOL-12 §4 gate 2).
- Env: ``worker_env_vars()`` returns the D-5 legacy-hook neutralizer. The
  SDK spawns its own runtime and merges any ``CodexConfig.env`` over a FULL
  ``os.environ`` copy (openai_codex/client.py), so it cannot enforce the
  VOL-17 §3 allowlist for that process; the env dict is applied where zloop
  itself controls process creation (future) and today is returned for
  callers/tests.

Concurrency & Polling Semantics (P0-1 & P1-4):
- ``start()`` registers the turn.
- ``poll()`` provides non-blocking status checking; on first call it lazily
  dispatches ``handle.run()`` to an internal ThreadPoolExecutor (pool size 16
  for 8–15 concurrency). Un-polled calls fallback to synchronous execution in ``wait()``.
- 429/RateLimit backoff: ``_run_turn_with_retry`` intercepts rate-limit errors
  and performs jittered exponential backoff (up to 3 retries) before declaring failure.
"""
from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .. import paths
from .base import BackendUnavailable, WorkerReport, WorkerSpec

# ---------------------------------------------------------------- guarded import

try:  # the venv has openai-codex; the test/system python may not
    from openai_codex import AsyncCodex, Codex, CodexConfig, Sandbox
    CODEX_SDK_AVAILABLE = True
    _IMPORT_ERROR: Exception | None = None
except ImportError as _e:  # pragma: no cover - depends on host
    CODEX_SDK_AVAILABLE = False
    _IMPORT_ERROR = _e
    AsyncCodex = Codex = CodexConfig = Sandbox = None  # type: ignore[assignment]

INSTALL_HINT = "openai-codex not importable; pip install zloop[codex]"

# VOL-12 §4 gate 1: strict workers must not be able to spawn nested agents.
AGENTS_DISABLED_OVERRIDES = (
    "agents.enabled=false",
    "features.multi_agent=false",
)

# D-5 / VOL-02 §3.5: an empty requirements file pointed at by
# CODEX_LOOP_REQUIREMENTS_TOML neutralizes the legacy machine-level LOOP
# hook registration without touching system files. Comment-only content:
# no active settings at all.
_EMPTY_REQUIREMENTS_CONTENT = (
    "# ZLoop empty worker requirements override (D-5, VOL-02 §3.5).\n"
    "# Intentionally no active settings: pointing CODEX_LOOP_REQUIREMENTS_TOML\n"
    "# here isolates worker turns from legacy machine-level LOOP hooks.\n"
)


def worker_env_vars() -> dict:
    """Env vars every spawned worker must carry (D-5; see module docstring).

    Returns ``{"CODEX_LOOP_REQUIREMENTS_TOML": <empty requirements path>}``.
    The file is created lazily (idempotently) under the zloop data root.
    """
    root = paths.zloop_data_root()
    req = root / "workers" / "requirements-empty.toml"
    if not req.exists() or req.read_text(encoding="utf-8") != _EMPTY_REQUIREMENTS_CONTENT:
        req.parent.mkdir(parents=True, exist_ok=True)
        req.write_text(_EMPTY_REQUIREMENTS_CONTENT, encoding="utf-8")
    return {"CODEX_LOOP_REQUIREMENTS_TOML": str(req)}


# ---------------------------------------------------------------- launch record


@dataclass(slots=True)
class CodexLaunch:
    """Opaque handle for one started worker turn (also stored per launch_id)."""

    launch_id: str
    thread: Any
    handle: Any
    result: Any = None
    error: str | None = None
    extras: dict = field(default_factory=dict)


# ---------------------------------------------------------------------- backend


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect 429 / RateLimit errors across SDK exceptions and message strings."""
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


class CodexSdkBackend:
    """AgentBackend over the Codex SDK with non-blocking execution (VOL-12 §2, D-12 v1)."""

    def __init__(self, *, model: str | None = None, agents_disabled: bool = True,
                 max_workers: int = 16, max_retries: int = 3):
        if not CODEX_SDK_AVAILABLE:
            raise BackendUnavailable(
                f"{INSTALL_HINT} ({_IMPORT_ERROR!r})" if _IMPORT_ERROR else INSTALL_HINT)
        self._model = model
        self._agents_disabled = agents_disabled
        self._max_retries = max_retries
        overrides = AGENTS_DISABLED_OVERRIDES if agents_disabled else ()
        # Default auth reuse: no explicit login_* call — the client picks up
        # the existing Codex CLI login (verified, VOL-02 §4).
        self._client = Codex(CodexConfig(config_overrides=overrides))
        self._launches: dict[str, CodexLaunch] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="zloop-worker")
        self._futures: dict[str, Any] = {}

    # -- AgentBackend ------------------------------------------------------

    def start(self, spec: WorkerSpec) -> CodexLaunch:
        """Start one worker turn in its own workspace (I34).

        Per VOL-12 §2: ``thread_start(cwd=workspace, sandbox=workspace_write)``
        then ``turn(input=prompt, sandbox=workspace_write)`` (per-turn
        override). One client, N threads — no per-launch runtime.
        """
        model = spec.model or self._model
        thread_kwargs: dict = {"cwd": str(spec.workspace),
                               "sandbox": Sandbox.workspace_write}
        if model is not None:
            thread_kwargs["model"] = model
        thread = self._client.thread_start(**thread_kwargs)
        handle = thread.turn(input=spec.prompt, sandbox=Sandbox.workspace_write)
        launch = CodexLaunch(launch_id=spec.launch_id, thread=thread, handle=handle)
        self._launches[spec.launch_id] = launch
        return launch

    def _execute_turn_with_retry(self, launch: CodexLaunch) -> Any:
        """Execute handle.run() with jittered exponential backoff on 429/RateLimit."""
        for attempt in range(self._max_retries + 1):
            try:
                launch.result = launch.handle.run()
                return launch.result
            except Exception as e:
                if _is_rate_limit_error(e) and attempt < self._max_retries:
                    delay = 0.5 * (2 ** attempt) + random.uniform(0.1, 0.4)
                    time.sleep(delay)
                    continue
                launch.error = f"{type(e).__name__}: {e}"
                raise

    def _ensure_dispatched(self, launch: CodexLaunch) -> Any:
        """Ensure the turn execution is running in the background thread pool."""
        future = self._futures.get(launch.launch_id)
        if future is None and launch.result is None and launch.error is None:
            def _run():
                return self._execute_turn_with_retry(launch)
            future = self._executor.submit(_run)
            self._futures[launch.launch_id] = future
        return future

    def poll(self, handle) -> bool:
        """Non-blocking status check (P0-1 Fix).

        When called by supervisor._result_ready, automatically dispatches
        the execution to the thread pool and checks completion non-blockingly.
        """
        launch = self._resolve(handle)
        if launch.result is not None or launch.error is not None:
            return True
        future = self._ensure_dispatched(launch)
        return future.done() if future is not None else True

    def wait(self, handle, timeout: float | None = None) -> str:
        """Block until terminal. Supports bounded timeout and thread pool waiting."""
        launch = self._resolve(handle)
        if launch.result is not None:
            return "terminal"
        if launch.error is not None:
            return "unknown"

        future = self._futures.get(launch.launch_id)
        if future is not None:
            try:
                future.result(timeout=timeout)
                return "terminal"
            except TimeoutError:
                self.interrupt(handle)
                launch.error = f"TimeoutExpired: worker turn timed out after {timeout}s"
                return "timeout"
            except Exception:
                return "unknown"

        # Direct synchronous invocation path (compatible with unpolled callers & mock unit tests)
        try:
            launch.result = self._execute_turn_with_retry(launch)
            return "terminal"
        except Exception as e:
            launch.error = f"{type(e).__name__}: {e}"
            return "unknown"

    def stream(self, handle) -> Iterator[Any]:
        """Pass-through of the SDK notification iterator (``turn/completed``
        is the terminal criterion, VOL-02 §4). Future event plane."""
        return iter(self._resolve(handle).handle.stream())

    def interrupt(self, handle) -> bool:
        """Best-effort: ``handle.interrupt()``; any failure -> False."""
        try:
            self._resolve(handle).handle.interrupt()
            return True
        except Exception:
            return False

    def collect(self, handle) -> WorkerReport:
        """Map the TurnResult to a WorkerReport (VOL-04 §9 shape).

        Completion is the ``turn/completed`` notification behind ``run()``
        with status ``completed`` — ``final_response`` may legitimately be
        None and never gates completion (I27). SDK failures (run raised —
        the SDK raises RuntimeError for failed turns) or status ``failed``
        map to ``'failed'``; ``interrupted``/anything else maps to
        ``'incomplete'``.
        """
        launch = self._resolve(handle)
        if launch.result is None and launch.error is None:
            self.wait(launch)  # self-sufficient collect: run once if needed
        if launch.result is None:
            return WorkerReport(launch_id=launch.launch_id, status="failed",
                                final_text=None, terminal_marker_seen=False,
                                error=launch.error)
        result = launch.result
        raw = getattr(result, "status", None)
        raw = getattr(raw, "value", raw)  # TurnStatus enum -> str
        final_text = getattr(result, "final_response", None)
        err = getattr(result, "error", None)
        err_text = (getattr(err, "message", None) or (str(err) if err is not None else None))
        if raw == "completed":
            return WorkerReport(launch_id=launch.launch_id, status="completed",
                                final_text=final_text, terminal_marker_seen=True,
                                error=None)
        if raw == "failed":
            return WorkerReport(launch_id=launch.launch_id, status="failed",
                                final_text=final_text, terminal_marker_seen=False,
                                error=err_text or launch.error
                                or f"turn failed with status {raw!r}")
        return WorkerReport(launch_id=launch.launch_id, status="incomplete",
                           final_text=final_text, terminal_marker_seen=False,
                           error=err_text or launch.error)

    def health(self) -> dict:
        """Cheap probe; auth state is only knowable via live login check
        (currently broken on this machine — P-CDX1, VOL-02 §3.5)."""
        return {"available": CODEX_SDK_AVAILABLE, "auth": "unknown-until-live-test"}

    # -- non-protocol helpers ----------------------------------------------

    def launch_record(self, launch_id: str) -> CodexLaunch | None:
        return self._launches.get(launch_id)

    def close(self) -> None:
        """Release the SDK runtime and thread pool."""
        self._executor.shutdown(wait=False, cancel_futures=True)
        close = getattr(self._client, "close", None)
        if close is not None:
            close()

    def _resolve(self, handle) -> CodexLaunch:
        if isinstance(handle, CodexLaunch):
            return handle
        record = self._launches.get(handle)  # tolerate a bare launch_id
        if record is None:
            raise ValueError(f"unknown launch handle {handle!r}")
        return record
