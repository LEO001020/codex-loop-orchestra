"""zloop.backend — execution backends (VOL-12 §1).

``zloop.backend.base`` holds the single ``AgentBackend`` contract.
``zloop.backend.codex_sdk`` (the default backend) is imported lazily via
``__getattr__`` so that importing this package never triggers the guarded
``openai_codex`` import — tests install a fake SDK module first, and
machines without the optional dependency keep working.
"""
from __future__ import annotations

from .base import (
    AgentBackend,
    BackendUnavailable,
    LaunchHandle,
    REPORT_STATUSES,
    WorkerReport,
    WorkerSpec,
)

__all__ = [
    "AgentBackend",
    "BackendUnavailable",
    "CodexSdkBackend",
    "LaunchHandle",
    "REPORT_STATUSES",
    "WorkerReport",
    "WorkerSpec",
]


def __getattr__(name: str):
    # Lazy re-export: keep codex_sdk (and its guarded import) out of every
    # plain ``import zloop.backend`` (see module docstring).
    if name == "CodexSdkBackend":
        from .codex_sdk import CodexSdkBackend
        return CodexSdkBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
