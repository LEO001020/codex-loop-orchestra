#!/usr/bin/env python3
"""orchestration_common.py — Shared foundation for the codex-loop-s-f2 redesign.

This module is the single home for the primitives every new orchestration
component needs:

* cross-platform (Windows / WSL / POSIX) path resolution via :mod:`pathlib`;
* a one-byte advisory file lock that works with both ``msvcrt`` and ``fcntl``;
* atomic JSON read/write and locked NDJSON append helpers;
* fail-closed policy loading for ``config/orchestration_policy_v2.toml`` and
  ``config/refill_policy.toml`` (design invariant 3: single source of truth,
  policy unreadable => fail visible, never a silent divergent default);
* model-pin resolution — models are read from configuration, **never**
  hardcoded (fixes the "hand-copied model name" class of defect; the current
  the active deployment pins execution and review models in config; this
  module intentionally does not assume a vendor or model family);
* semantic idempotency keys (the V10 ``k3IdemKey`` pattern from
  ``verification_kernel-11.md §4.1``).

Every component under ``impl/harness/`` imports from here; the module has no
dependencies outside the Python 3.10+ standard library.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

__all__ = [
    "PolicyError",
    "ModelPinError",
    "LoopPaths",
    "file_lock",
    "read_json",
    "atomic_write_json",
    "append_ndjson",
    "iter_ndjson",
    "OrchestrationPolicy",
    "canonical_policy_path",
    "policy_sha256",
    "layered_authorization",
    "RefillPolicy",
    "idem_key",
    "get_logger",
    "utc_now",
]

_LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger.  Handlers are attached once per process."""
    logger = logging.getLogger(name)
    if not logging.getLogger().handlers and not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(os.environ.get("LOOP_LOG_LEVEL", "INFO").upper())
    return logger


log = get_logger("loop.common")


class PolicyError(RuntimeError):
    """A policy file is missing or malformed.

    Raised instead of falling back to silent divergent defaults — the P0-8.3
    fix direction: policy unreadable => fail closed and visible.
    """


class ModelPinError(PolicyError):
    """A required model pin could not be resolved from configuration."""


def canonical_policy_path(root: Path | str) -> Path:
    """Return the sole production orchestration-policy path."""
    return Path(root).resolve() / "config" / "orchestration_policy_v2.toml"


def policy_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def layered_authorization(root: Path | str, *, now: float | None = None,
                          max_age_s: float = 3600) -> tuple[bool, str]:
    """Validate that layered authorization is fresh and byte-bound."""
    root = Path(root).resolve()
    marker_path = root / "data" / "governor" / "layered_authorization.json"
    policy_path = canonical_policy_path(root)
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        age = (time.time() if now is None else now) - float(marker["ts"])
        current_hash = policy_sha256(policy_path)
        conditions = marker["conditions"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return False, f"authorization unreadable: {exc}"
    if marker.get("status") != "PASS" or marker.get("authorized_mode") != "layered":
        return False, "authorization status/mode mismatch"
    if age < 0 or age > max_age_s:
        return False, f"authorization age={age:.1f}s exceeds {max_age_s:.1f}s"
    if marker.get("policy_sha256") != current_hash:
        return False, "authorization policy hash mismatch"
    if not isinstance(conditions, list) or not conditions or not all(
            isinstance(item, dict) and bool(item.get("ok"))
            for item in conditions):
        return False, "authorization conditions incomplete"
    return True, f"authorization current age={age:.1f}s"


def utc_now() -> float:
    """Wall-clock epoch seconds (single definition so tests can monkeypatch)."""
    return time.time()


# ---------------------------------------------------------------------------
# Paths — Windows/WSL compatible.  All joins go through pathlib; no separator
# is ever hardcoded (task constraint: cross-platform path compatibility).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LoopPaths:
    """Resolved directory layout for one LOOP root.

    ``root`` honours ``$LOOP_ROOT`` when constructed via :meth:`resolve`,
    falling back to the package root two levels above this file.
    """

    root: Path

    @classmethod
    def resolve(cls, root: Path | str | None = None) -> "LoopPaths":
        """Resolve the LOOP root: explicit arg > $LOOP_ROOT > package root."""
        if root is not None:
            return cls(Path(root).resolve())
        env = os.environ.get("LOOP_ROOT")
        if env:
            return cls(Path(env).resolve())
        return cls(Path(__file__).resolve().parents[1])

    # -- directories --------------------------------------------------------
    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def events(self) -> Path:
        return self.data / "events.ndjson"

    @property
    def events_lock(self) -> Path:
        return self.data / "lifecycle" / ".events.lock"

    @property
    def ledger(self) -> Path:
        return self.data / "progress_ledger.json"

    @property
    def l2_queue_dir(self) -> Path:
        return self.data / "l2_queue"

    @property
    def l2_pending(self) -> Path:
        return self.l2_queue_dir / "pending.ndjsonl"

    @property
    def l2_claims_dir(self) -> Path:
        return self.l2_queue_dir / "claims"

    @property
    def l2_heartbeat(self) -> Path:
        return self.l2_queue_dir / "consumer_heartbeat.json"

    @property
    def governor_dir(self) -> Path:
        return self.data / "governor"

    @property
    def budget_dir(self) -> Path:
        return self.data / "budget"

    @property
    def router_dir(self) -> Path:
        return self.data / "router"

    @property
    def usage_dir(self) -> Path:
        return self.data / "usage"

    @property
    def refill_dir(self) -> Path:
        return self.data / "refill"

    @property
    def meter_report(self) -> Path:
        """Resolve the newest meter contract before compatibility fallbacks.

        ``model_token_share_v2.py`` writes ``model_token_share_v2.json``.
        Older F2 components used ``meter_v2_report.json`` and v1 used
        ``model_token_share.json``; keep those readable in that order without
        pointing the governor at a path that no producer writes.
        """
        candidates = (
            self.usage_dir / "model_token_share_v2.json",
            self.usage_dir / "meter_v2_report.json",
            self.usage_dir / "model_token_share.json",
        )
        return next((path for path in candidates if path.exists()), candidates[0])

    @property
    def token_ledger(self) -> Path:
        return self.usage_dir / "token_ledger.ndjsonl"

    @property
    def run_role_map(self) -> Path:
        return self.usage_dir / "run_role_map.json"


# ---------------------------------------------------------------------------
# Locking / atomic IO
# ---------------------------------------------------------------------------
_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Cross-process one-byte advisory lock (same protocol the shipped
    ``lifecycle_supervisor.locked`` uses, so v2 components interoperate with
    v1 writers on the same lock files)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve()).casefold() if os.name == "nt" else str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with thread_lock:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as created:
                created.write(b"\0")
                created.flush()
                os.fsync(created.fileno())
        with path.open("r+b") as handle:
            handle.seek(0)
            if os.name == "nt":  # pragma: no cover - Windows plane
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":  # pragma: no cover - Windows plane
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_json(path: Path | str, default: Any = None) -> Any:
    """Read JSON, returning ``default`` on any IO/parse failure."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def atomic_write_json(path: Path | str, value: Any) -> None:
    """Write JSON atomically (tmp file + ``os.replace``); fsynced."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def append_ndjson(path: Path | str, obj: Mapping[str, Any],
                  lock_path: Path | None = None) -> None:
    """Append one JSON line under an advisory lock (append-only ledger)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path if lock_path is not None else path.with_suffix(path.suffix + ".lock")
    with file_lock(lock):
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(obj, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")


def iter_ndjson(path: Path | str) -> Iterator[dict[str, Any]]:
    """Yield parsed records from an NDJSON file; malformed lines are skipped
    with a warning (fail-visible via log, never a crash)."""
    path = Path(path)
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                log.warning("iter_ndjson: %s:%d unparseable line skipped", path, lineno)
                continue
            if isinstance(rec, dict):
                yield rec


# ---------------------------------------------------------------------------
# Semantic idempotency keys (V10 k3IdemKey pattern)
# ---------------------------------------------------------------------------
def idem_key(kind: str, scope: str, *semantic_fields: str) -> str:
    """Build ``k3:<kind>:<scope>:<b32(sha256(fields))[:16]>``.

    The key is derived from *semantic* fields (never timestamps), so the same
    logical request always maps to the same key — the exactly-once boundary
    for the l2 consumer and every append-only ledger record.
    """
    digest = hashlib.sha256("|".join(semantic_fields).encode("utf-8")).digest()
    suffix = base64.b32encode(digest).decode("ascii").lower().rstrip("=")[:16]
    return f"k3:{kind}:{scope}:{suffix}"


# ---------------------------------------------------------------------------
# Policy loading — fail-closed
# ---------------------------------------------------------------------------
def _load_toml(path: Path, what: str) -> dict[str, Any]:
    if not path.exists():
        raise PolicyError(f"{what} missing: {path} — refusing silent defaults "
                          f"(P0-8.3 fail-closed policy reads)")
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"{what} unreadable: {path}: {exc}") from exc


@dataclass
class OrchestrationPolicy:
    """Typed view over the canonical ``config/orchestration_policy_v2.toml``.

    Declaration face = enforcement face (design invariant 2): every limit the
    file declares is read here and enforced by the component that owns it.
    Concurrency numbers are **not** duplicated here — they live only in
    ``refill_policy.toml`` (invariant 3); this policy carries a
    ``refill_policy_ref`` key instead.
    """

    doc: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, paths: LoopPaths | None = None) -> "OrchestrationPolicy":
        paths = paths or LoopPaths.resolve()
        # The policy path is intentionally not environment-overridable.  A
        # process-local override created a second production authority: typed
        # consumers could see a different routing mode and model pin from the
        # hooks, meter and layered gate.  Tests that need a synthetic policy
        # construct OrchestrationPolicy directly inside their isolated root.
        policy_path = canonical_policy_path(paths.root)
        return cls(_load_toml(policy_path, "orchestration policy"), policy_path)

    # -- generic accessors ----------------------------------------------------
    def value(self, section: str, key: str, default: Any = None) -> Any:
        table = self.doc.get(section, {})
        return table.get(key, default) if isinstance(table, dict) else default

    def require(self, section: str, key: str) -> Any:
        value = self.value(section, key, None)
        if value is None:
            raise PolicyError(
                f"orchestration policy [{section}].{key} missing in {self.path} "
                f"— fail-closed (declaration face = enforcement face)")
        return value

    # -- routing ---------------------------------------------------------------
    def routing_mode(self) -> str:
        """``cold_start`` | ``shadow`` | ``layered``.

        ``passthrough_enabled`` (legacy boolean) is honoured only as an alias:
        ``false -> cold_start``, ``true -> layered``.  Missing everything =>
        cold_start (safe default, byte-identical legacy behavior).
        """
        mode = self.value("routing", "mode")
        if isinstance(mode, str) and mode in ("cold_start", "shadow", "layered"):
            return mode
        legacy = self.value("routing", "passthrough_enabled")
        if isinstance(legacy, bool):
            return "layered" if legacy else "cold_start"
        return "cold_start"

    def verify_sample_rate(self) -> float:
        value = self.value("routing", "verify_sample_rate", 0.10)
        return float(value) if isinstance(value, (int, float)) else 0.10

    # -- model pins --------------------------------------------------------------
    def model_pin(self, role_family: str) -> str:
        """Resolve the model id for a role family (``sol`` / ``k3`` / ``v4``).

        Read from ``[models]``; missing pin raises :class:`ModelPinError` —
        silently falling back to a hardcoded model string is exactly the
        defect class being fixed (task constraint: never hardcode
        ``provider-x/model-y`` or any other model id in code).
        """
        models = self.doc.get("models", {})
        key = f"{role_family}_model"
        pin = models.get(key) if isinstance(models, dict) else None
        if not isinstance(pin, str) or not pin:
            raise ModelPinError(
                f"orchestration policy [models].{role_family} missing in "
                f"{self.path}; refusing to guess a model id (fail-visible)")
        return pin

    def model_context(self, role_family: str, key: str) -> int | None:
        """Optional context sizing from the canonical flat ``[models]`` keys."""
        suffix = {"context_window": "context_tokens",
                  "compaction": "compaction_tokens"}.get(key)
        models = self.doc.get("models", {})
        value = models.get(f"{role_family}_{suffix}") \
            if isinstance(models, dict) and suffix else None
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    def model_reasoning(self, role_family: str) -> str | None:
        models = self.doc.get("models", {})
        value = models.get(f"{role_family}_reasoning") \
            if isinstance(models, dict) else None
        return value if isinstance(value, str) and value else None

    # -- token bands / hysteresis ---------------------------------------------------
    def sol_hard_cap(self) -> float:
        return float(self.value("tokens", "sol_allocation_cap",
                                self.value("tokens", "sol_hard_cap", 0.15)))

    def k3_floor(self) -> float:
        return float(self.value("tokens", "k3_floor", 0.20))

    def minimum_denominator(self) -> int:
        return int(self.value("tokens", "minimum_denominator", 2_000_000))

    def hysteresis(self) -> dict[str, float]:
        return {
            "enter_high": float(self.value("hysteresis", "enter_high_sol_share", 0.25)),
            "enter_samples": int(self.value("hysteresis", "enter_samples", 2)),
            "leave_high": float(self.value("hysteresis", "leave_high_sol_share", 0.22)),
            "leave_samples": int(self.value("hysteresis", "leave_samples", 2)),
            "critical_1h_share": float(self.value("hysteresis", "critical_1h_share", 0.35)),
        }

    # -- governor bounds ---------------------------------------------------------------
    def planning_max_turns(self) -> int:
        return int(self.value("budget", "planning_max_turns", 6))

    def planning_max_new_tokens(self) -> int:
        return int(self.value("budget", "planning_max_new_tokens", 30_000))

    def l2_max_age_s(self) -> float:
        return float(self.value("l2_queue", "l2_max_age_s", 900))

    def meter_stale_after_s(self) -> float:
        return float(self.value("tokens", "stale_after_s", 7200))


@dataclass
class RefillPolicy:
    """Typed, fail-closed view over ``config/refill_policy.toml`` — the ONLY
    concurrency authority (P0-8.3).  Any read failure raises
    :class:`PolicyError` instead of returning a divergent default."""

    doc: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, paths: LoopPaths | None = None) -> "RefillPolicy":
        paths = paths or LoopPaths.resolve()
        explicit = os.environ.get("LOOP_REFILL_POLICY")
        policy_path = (Path(explicit).resolve() if explicit
                       else paths.config / "refill_policy.toml")
        return cls(_load_toml(policy_path, "refill policy"), policy_path)

    def _int(self, section: str, key: str) -> int:
        table = self.doc.get(section, {})
        value = table.get(key) if isinstance(table, dict) else None
        if not isinstance(value, int) or isinstance(value, bool):
            raise PolicyError(f"refill policy [{section}].{key} missing/invalid "
                              f"in {self.path} — fail-closed, no silent default")
        return value

    def target_total(self) -> int:
        return self._int("concurrency", "target_total")

    def dialogue_target(self) -> int:
        return self._int("concurrency", "dialogue_target")

    def v4_target(self) -> int:
        return self._int("concurrency", "v4_target")

    def k3_target(self) -> int:
        return self._int("concurrency", "k3_target")

    def v4_low_water(self) -> int:
        return self._int("concurrency", "v4_low_water")

    def k3_low_water(self) -> int:
        return self._int("concurrency", "k3_low_water")

    def spawn_interval_ms(self) -> int:
        return self._int("spawn_throttle", "spawn_interval_ms")   # 1000

    def reservations_borrowable(self) -> bool:
        table = self.doc.get("concurrency", {})
        value = table.get("reservations_borrowable", True) if isinstance(table, dict) else True
        return bool(value)

    def policy_version(self) -> str:
        table = self.doc.get("meta", {})
        value = table.get("policy_version") if isinstance(table, dict) else None
        return str(value) if value is not None else "unversioned"
