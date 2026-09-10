"""zloop.backend.base — the single backend contract (VOL-12 §1).

Every execution backend (CodexSdkBackend today, AppServerBackend /
ZCodeBackend later) implements ``AgentBackend`` and is exercised by the same
contract/chaos tests — that is the only substitution standard.

Shape notes (M7 v1):

- ``WorkerSpec.prompt`` is the flat text handed to the worker (the packet
  envelope header is a supervisor concern, VOL-12 §2); ``network`` /
  ``max_turns`` / ``env_extra`` are carried for the strict worker contract
  even where the current backend cannot yet enforce all of them physically
  (enforcement probes are M8+, VOL-12 §4).
- ``WorkerReport`` is the VOL-04 §9 worker report reduced to what the
  backend plane can state truthfully. ``terminal_marker_seen`` is the I27
  discipline: it is True only when the backend observed an explicit terminal
  completion signal (e.g. the Codex ``turn/completed`` notification), never
  merely because a stream ended or an exit code was 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

# Opaque, backend-specific handle returned by ``AgentBackend.start`` and
# accepted by every other method. Callers must treat it as a token.
LaunchHandle = Any

# WorkerReport.status values (VOL-04 §9).
REPORT_STATUSES = ("completed", "incomplete", "failed")


class BackendUnavailable(Exception):
    """Raised at backend construction when its runtime dependency is missing.

    Deliberately a construction-time (not import-time) failure: importing the
    backend module must stay side-effect free so the rest of zloop works on
    machines without the optional dependency (``pip install zloop[codex]``).
    """


@dataclass(slots=True)
class WorkerSpec:
    """One worker launch request (one launch = one workspace, I34)."""

    launch_id: str
    workspace: Path
    prompt: str
    network: str = "none"            # VOL-09 network policy id
    max_turns: int = 0               # 0 = backend default
    model: str | None = None         # None = backend/client default
    env_extra: dict | None = None    # packet-declared minimal vars (VOL-17 §3)


@dataclass(slots=True)
class WorkerReport:
    """Collected outcome of one launch (backend-plane truth only).

    ``status``:
      - ``completed``: the worker turn reached its explicit terminal
        completion (Codex ``turn/completed`` with status ``completed``).
      - ``incomplete``: terminated without completion (interrupted,
        in-progress at collection, ambiguous).
      - ``failed``: the backend/turn errored (``error`` carries the cause).

    ``final_text`` may legitimately be None even for ``completed`` turns —
    completion NEVER depends on a final sentence being present (VOL-02 §4,
    I27). Acceptance authority stays with the host (VOL-10).
    """

    launch_id: str
    status: str                      # one of REPORT_STATUSES
    final_text: str | None = None
    terminal_marker_seen: bool = False
    error: str | None = None


@runtime_checkable
class AgentBackend(Protocol):
    """The only execution-backend interface (VOL-12 §1)."""

    def start(self, spec: WorkerSpec) -> LaunchHandle:
        """Spawn one worker turn for ``spec``; return its opaque handle."""
        ...

    def wait(self, handle: LaunchHandle, timeout: float | None = None) -> str:
        """Block until the launch is terminal. Returns ``'terminal'`` or
        ``'unknown'`` (backend could not determine/complete the wait).
        Backends that cannot bound the wait internally document the
        limitation; ``'timeout'`` is reserved for backends that can."""
        ...

    def stream(self, handle: LaunchHandle) -> Iterator[Any]:
        """Yield raw backend events for the launch (future event plane)."""
        ...

    def interrupt(self, handle: LaunchHandle) -> bool:
        """Best-effort cancellation. Correctness never depends on it
        (VOL-12 §6): ambiguous launches go to quarantine, not to kill."""
        ...

    def collect(self, handle: LaunchHandle) -> WorkerReport:
        """Return the launch's report (must only be trusted after wait)."""
        ...

    def health(self) -> dict:
        """Cheap liveness/capability probe for the backend plane."""
        ...
