#!/usr/bin/env python3
"""l2_consumer.py — mechanical, exactly-once ``send_l2`` → K3 verifier drain.

Fixes P0-2 (``phase1b_codebase_analysis.md``): ``send_l2`` previously appeared
nowhere outside ``trigger_eval.py`` — the entire L2/K3 verification band was
structurally dead. This module is the missing consumer, built to the design in
``phase2_architecture_design.md`` §2.2 and the V10 verification-kernel
doctrine (append-only ledgers, semantic idempotency keys, fail-visible
down-consumer semantics).

Mechanics — **one ``send_l2`` event → exactly one K3 verifier execution**:

* **Queue**: ``trigger_eval`` (layered mode) appends L2 request records to
  ``data/l2_queue/pending.ndjsonl``. Records carry a semantic idempotency key
  ``k3:l2req:<packet_id>:<b32(sha256(run_id|attempt))[:16]>`` (the V10
  ``k3IdemKey`` pattern) — the key is a function of the *semantic* identity of
  the request, never of wall-clock time.
* **Atomic claim**: a claim file ``claims/<idem_key>.json`` created with
  ``os.open(..., O_CREAT | O_EXCL)`` is the exactly-once boundary. Creation
  either succeeds for exactly one drainer or raises ``FileExistsError`` —
  there is no read-then-write race window.
* **Claim heartbeat**: while a claimed verification is in flight, a daemon
  thread refreshes ``heartbeat_ts`` inside the claim file (atomic
  temp-file + ``os.replace``) every ``claim_heartbeat_interval_s``.
* **Completion marker**: when the K3 verifier finishes, its report is
  validated by :mod:`short_result_validator` and an immutable completion file
  ``completions/<idem_key>.json`` is written. A completed key can never be
  re-dispatched.
* **Stale-claim reaper**: claims whose heartbeat is older than
  ``claim_stale_after_s`` and that have no completion are *reaped* — moved to
  ``reaped/`` for forensics — and the record becomes claimable again (bounded
  by ``claim_max_reclaims``; past the bound the packet escalates ``direct_l3``
  fail-visible, never silently dropped).

Crash-safety matrix (all recovered mechanically, zero Sol involvement):

===============================  ==========================================
Failure                          Recovery
===============================  ==========================================
crash after claim, before        claim exists with initial heartbeat only →
heartbeat thread started         reaped after ``claim_stale_after_s``
crash after model run, before    claim heartbeat goes stale, no completion →
completion publish               reaped after timeout, re-verified once
consumer process restart         drain is stateless; the claim directory IS
                                 the state; incomplete stale claims reaped
consumer down entirely           pending records age past ``l2_max_age_s`` →
                                 ``l2_consumer_stale`` event + ``direct_l3``
===============================  ==========================================

Integration:

* state machine — every claim emits ``l2_requested`` (t30 REPORTED→L2_VERIFY);
  every validated completion emits ``verdict_pass|verdict_redo|
  verdict_escalate_l2_5|verdict_escalate_l3`` (t31–t34); invalid completions
  emit ``exec_failed`` (t35).
* refill — every claim appends a K3-pool demand record so configured K3 capacity
  slots have real demand (P0-6).
* models — the verifier model is read from
  ``config/orchestration_policy_v2.toml [models].k3_model``; **nothing is
  hardcoded** (user constraint).
* paths — everything goes through :mod:`pathlib`; Windows and WSL safe.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

# self-contained-but-importable: allow running from a checkout where the
# sibling module is in the same directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from short_result_validator import (  # noqa: E402
    L2_VERDICTS,
    ShortResultValidator,
    ValidationResult,
)

__all__ = [
    "L2Record",
    "DrainStats",
    "CanaryResult",
    "L2QueuePaths",
    "L2Consumer",
    "make_idem_key",
    "load_policy",
]

LOG = logging.getLogger("l2_consumer")

# Suffix accepts BOTH base32 cases: orchestration_common.idem_key emits
# lowercase, older records uppercase.  Keys are canonicalized to lowercase
# in make_idem_key/from_dict so the same semantic request always maps to
# one claim file regardless of the producer (P0-2 integration fix).
_IDEM_RE = re.compile(r"^k3:l2req:[A-Za-z0-9._-]{1,96}:[A-Za-z2-7]{16}$")
_SAFE_PID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")

# ---------------------------------------------------------------------------
# Policy loading (tomllib on 3.11+, minimal fallback for 3.10)
# ---------------------------------------------------------------------------


def _minimal_toml(text: str) -> dict[str, Any]:
    """Tiny TOML subset parser (sections, str/int/float/bool/str-arrays).

    Only used on Python 3.10 where :mod:`tomllib` is absent; our policy files
    deliberately stay inside this subset.
    """
    out: dict[str, Any] = {}
    section = out
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip() if not raw_line.strip().startswith('"') else raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            section = out.setdefault(name, {})
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value.startswith("[") :
            items = re.findall(r'"([^"]*)"', value)
            section[key] = items
        elif value.startswith('"') and value.endswith('"'):
            section[key] = value[1:-1]
        elif value in ("true", "false"):
            section[key] = value == "true"
        else:
            try:
                section[key] = int(value)
            except ValueError:
                try:
                    section[key] = float(value)
                except ValueError:
                    section[key] = value
    return out


def load_policy(policy_path: Path) -> dict[str, Any]:
    """Load ``orchestration_policy_v2.toml``; **fail closed** if unreadable.

    Raises:
        RuntimeError: when the policy file is missing or unparseable — a
            missing policy must never silently produce divergent defaults
            (P0-8.3 discipline).
    """
    try:
        text = policy_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            "policy unreadable (%s): failing closed — fix %s"
            % (exc, policy_path)) from exc
    try:
        import tomllib
        return tomllib.loads(text)
    except ModuleNotFoundError:
        return _minimal_toml(text)
    except Exception as exc:  # tomllib.TOMLDecodeError
        raise RuntimeError("policy unparseable: %s" % exc) from exc


# ---------------------------------------------------------------------------
# Idempotency key — V10 k3IdemKey pattern
# ---------------------------------------------------------------------------


def make_idem_key(packet_id: str, run_id: str, attempt: int) -> str:
    """Build the semantic idempotency key for one ``send_l2`` request.

    Pattern (design §2.1): ``k3:l2req:<packet_id>:<b32(sha256(run_id|attempt))
    [0:16]>``. Stable across re-runs of the same semantic request; distinct
    per retry attempt (a *new* attempt is a *new* verification request).
    """
    if not _SAFE_PID_RE.fullmatch(packet_id or ""):
        raise ValueError("invalid packet_id %r" % (packet_id,))
    digest = hashlib.sha256(("%s|%d" % (run_id, attempt)).encode("utf-8")).digest()
    suffix = base64.b32encode(digest).decode("ascii").lower()[:16]
    return "k3:l2req:%s:%s" % (packet_id, suffix)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class L2Record:
    """One L2 verification request as it rides ``pending.ndjsonl``."""

    idem_key: str
    packet_id: str
    run_id: str
    attempt: int
    reason: str
    created_ts: float
    request_path: str | None = None   # bounded L2 request record (<=1k tokens)
    revision: int | None = None       # ControlPacket revision stamp

    def to_json(self) -> str:
        """Serialize to a single ndjson line."""
        return json.dumps({
            "idem_key": self.idem_key, "packet_id": self.packet_id,
            "run_id": self.run_id, "attempt": self.attempt,
            "reason": self.reason, "created_ts": self.created_ts,
            "request_path": self.request_path, "revision": self.revision,
        }, sort_keys=True)

    @staticmethod
    def from_dict(obj: dict[str, Any]) -> "L2Record":
        """Parse a pending-queue line; raises ``KeyError``/``ValueError``."""
        key = str(obj["idem_key"])
        if not _IDEM_RE.match(key):
            raise ValueError("malformed idem_key %r" % key)
        head, _, suffix = key.rpartition(":")
        key = "%s:%s" % (head, suffix.lower())  # canonical form
        return L2Record(
            idem_key=key, packet_id=str(obj["packet_id"]),
            run_id=str(obj["run_id"]), attempt=int(obj["attempt"]),
            reason=str(obj.get("reason", "send_l2")),
            created_ts=float(obj.get("created_ts", 0.0)),
            request_path=obj.get("request_path"),
            revision=obj.get("revision"),
        )


@dataclass
class DrainStats:
    """Outcome of one :meth:`L2Consumer.drain` pass."""

    scanned: int = 0
    claimed: int = 0
    dispatched: int = 0
    already_claimed: int = 0
    already_complete: int = 0
    stale_escalated: int = 0
    reaped: int = 0
    provider_backoff: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CanaryResult:
    """Structured result of the exactly-once canary (gate condition 2)."""

    ok: bool
    dispatches: int
    expected: int
    reclaim_dispatches: int
    detail: str


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class L2QueuePaths:
    """All filesystem locations of the queue, derived from LOOP_ROOT+policy."""

    root: Path
    queue_dir: Path
    pending: Path
    claims: Path
    completions: Path
    reaped: Path
    dispatches: Path
    consumer_heartbeat: Path
    events: Path
    k3_demand: Path
    sol_wake: Path

    @staticmethod
    def from_policy(root: Path, policy: dict[str, Any]) -> "L2QueuePaths":
        """Build the path set; directory layout is policy-declared."""
        q = policy.get("l2_queue", {})
        queue_dir = root / Path(str(q.get("dir", "data/l2_queue")))
        return L2QueuePaths(
            root=root,
            queue_dir=queue_dir,
            pending=queue_dir / str(q.get("pending_file", "pending.ndjsonl")),
            claims=queue_dir / "claims",
            completions=queue_dir / "completions",
            reaped=queue_dir / "reaped",
            dispatches=queue_dir / "dispatches",
            consumer_heartbeat=queue_dir / "consumer_heartbeat.json",
            events=root / "data" / "events.ndjson",
            k3_demand=root / "data" / "refill" / "k3_demand.ndjsonl",
            sol_wake=root / "data" / "sol_wake",
        )

    def ensure(self) -> None:
        """Create every directory (idempotent)."""
        for d in (self.queue_dir, self.claims, self.completions, self.reaped,
                  self.dispatches, self.events.parent, self.k3_demand.parent,
                  self.sol_wake):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Atomic file helpers (Windows + WSL safe)
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    """Write JSON atomically: temp file in the same dir + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_line(path: Path, line: str) -> None:
    """Append one ndjson line under a portable lock-directory mutex."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + 10.0
    acquired = False
    while time.monotonic() < deadline:
        try:
            os.mkdir(lock)  # atomic on every platform
            acquired = True
            break
        except FileExistsError:
            time.sleep(0.02)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\n") + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    finally:
        if acquired:
            try:
                os.rmdir(lock)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Heartbeat guard
# ---------------------------------------------------------------------------


class _HeartbeatGuard:
    """Daemon thread refreshing a claim's ``heartbeat_ts`` while in flight."""

    def __init__(self, claim_path: Path, interval_s: float,
                 clock: Callable[[], float]) -> None:
        self._claim_path = claim_path
        self._interval = max(0.5, interval_s)
        self._clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="l2-heartbeat-%s" % claim_path.stem,
            daemon=True)

    def _beat(self) -> None:
        try:
            claim = json.loads(self._claim_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # claim reaped/removed — stop silently, reaper owns it now
        claim["heartbeat_ts"] = self._clock()
        _atomic_write_json(self._claim_path, claim)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self._beat()

    def __enter__(self) -> "_HeartbeatGuard":
        self._beat()
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# The consumer
# ---------------------------------------------------------------------------

Dispatcher = Callable[[L2Record], bool]


class L2Consumer:
    """Exactly-once ``send_l2`` drainer (stateless between runs).

    The claim directory is the *only* state; the process can be killed at any
    point and a later run (statemachine step epilogue / post-report hook /
    refill sync — no daemon required) recovers mechanically.

    Args:
        root: LOOP root directory (contains ``data/``).
        policy: parsed ``orchestration_policy_v2.toml`` mapping.
        dispatcher: callable executing one verifier dispatch for a record;
            returns ``True`` on successful process launch. Defaults to
            :meth:`default_dispatcher`, which shells out to ``dispatch.py
            --role verifier`` when available and otherwise records a
            dispatch-intent file (test/canary plane).
        clock: injectable time source (tests/canaries).
    """

    def __init__(self, root: Path | str, policy: dict[str, Any] | None = None,
                 dispatcher: Dispatcher | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root).resolve()
        if policy is None:
            policy = load_policy(
                self.root / "config" / "orchestration_policy_v2.toml")
        self.policy = policy
        q = policy.get("l2_queue", {})
        ctx = policy.get("context", {})
        self.l2_max_age_s = float(q.get("l2_max_age_s", 900))
        self.heartbeat_interval_s = float(q.get("claim_heartbeat_interval_s", 15))
        self.claim_stale_after_s = float(q.get("claim_stale_after_s", 120))
        self.claim_max_reclaims = int(q.get("claim_max_reclaims", 2))
        self.verifier_model = str(
            policy.get("models", {}).get("k3_model", "")).strip()
        if not self.verifier_model:
            raise RuntimeError(
                "policy [models].k3_model is empty — the verifier model must "
                "be pinned in config, never hardcoded (fail closed)")
        self.paths = L2QueuePaths.from_policy(self.root, policy)
        self.paths.ensure()
        self._dispatcher: Dispatcher = dispatcher or self.default_dispatcher
        self._clock = clock
        self._validator = ShortResultValidator(
            max_tokens=int(ctx.get("child_short_result_max_tokens", 500)),
            max_findings=int(ctx.get("child_short_result_max_findings", 8)),
            require_verdict=True)
        self._lock = threading.Lock()

    # -- producer side ------------------------------------------------------

    def enqueue(self, packet_id: str, run_id: str, attempt: int,
                reason: str = "send_l2", request_path: str | None = None,
                revision: int | None = None) -> L2Record | None:
        """Append one L2 request (idempotent by key). Called by trigger_eval.

        Returns:
            the new :class:`L2Record`, or ``None`` when a record with the same
            idempotency key already exists (re-run produces 0 new records —
            §2.1 AC2).
        """
        key = make_idem_key(packet_id, run_id, attempt)
        with self._lock:
            if any(r.idem_key == key for r in self.iter_pending()):
                LOG.debug("enqueue: %s already pending — idempotent no-op", key)
                return None
            rec = L2Record(idem_key=key, packet_id=packet_id, run_id=run_id,
                           attempt=attempt, reason=reason,
                           created_ts=self._clock(),
                           request_path=request_path, revision=revision)
            _append_line(self.paths.pending, rec.to_json())
        LOG.info("enqueued %s (%s)", key, reason)
        return rec

    def iter_pending(self) -> Iterator[L2Record]:
        """Yield every parseable pending record (malformed lines logged)."""
        try:
            lines = self.paths.pending.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                yield L2Record.from_dict(json.loads(line))
            except (ValueError, KeyError, TypeError) as exc:
                LOG.warning("pending.ndjsonl line %d malformed: %s", i + 1, exc)

    # -- claim primitives ---------------------------------------------------

    def _claim_path(self, idem_key: str) -> Path:
        return self.paths.claims / (idem_key.replace(":", "_") + ".json")

    def _completion_path(self, idem_key: str) -> Path:
        return self.paths.completions / (idem_key.replace(":", "_") + ".json")

    def try_claim(self, rec: L2Record) -> Path | None:
        """Atomically claim *rec*: ``O_CREAT|O_EXCL`` — the exactly-once gate.

        Returns:
            the claim path on success, ``None`` when another drainer holds
            (or held) the claim.
        """
        path = self._claim_path(rec.idem_key)
        now = self._clock()
        body = json.dumps({
            "idem_key": rec.idem_key, "packet_id": rec.packet_id,
            "run_id": rec.run_id, "attempt": rec.attempt,
            "claimed_ts": now, "heartbeat_ts": now,
            "claimer_pid": os.getpid(), "reclaims": self._reclaim_count(rec.idem_key),
        }, sort_keys=True)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None
        try:
            os.write(fd, body.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return path

    def _reclaim_count(self, idem_key: str) -> int:
        stem = idem_key.replace(":", "_")
        return len(list(self.paths.reaped.glob(stem + ".*.json")))

    def heartbeat(self, idem_key: str) -> bool:
        """Refresh the claim heartbeat once; ``False`` if the claim is gone."""
        path = self._claim_path(idem_key)
        try:
            claim = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        claim["heartbeat_ts"] = self._clock()
        _atomic_write_json(path, claim)
        return True

    def heartbeat_guard(self, idem_key: str) -> _HeartbeatGuard:
        """Context manager keeping the claim fresh during a dispatch."""
        return _HeartbeatGuard(self._claim_path(idem_key),
                               self.heartbeat_interval_s, self._clock)

    # -- events / demand ----------------------------------------------------

    def _emit_event(self, packet_id: str, event: str,
                    detail: dict[str, Any]) -> None:
        """Append one state-machine event (t30–t35 producers)."""
        _append_line(self.paths.events, json.dumps({
            "ts": self._clock(), "packet_id": packet_id, "event": event,
            "source": "l2_consumer", "detail": detail}, sort_keys=True))

    def _add_k3_demand(self, rec: L2Record) -> None:
        """Record K3-pool demand for the refill controller (P0-6)."""
        _append_line(self.paths.k3_demand, json.dumps({
            "ts": self._clock(), "pool": "k3", "kind": "l2_verify",
            "idem_key": rec.idem_key, "packet_id": rec.packet_id},
            sort_keys=True))

    def _sol_wake(self, name: str, body: dict[str, Any]) -> None:
        """Fail-visible wake naming a consumer outage (never silent drop)."""
        _atomic_write_json(self.paths.sol_wake / ("%s.json" % name), body)

    # -- consumer heartbeat (config-lint / layered_gate condition 1) --------

    def write_consumer_heartbeat(self) -> None:
        """Refresh ``consumer_heartbeat.json`` (ts + policy version)."""
        _atomic_write_json(self.paths.consumer_heartbeat, {
            "ts": self._clock(),
            "policy_version": self.policy.get("policy_version"),
            "pid": os.getpid()})

    def consumer_heartbeat_age(self) -> float | None:
        """Age in seconds of the consumer heartbeat, or ``None`` if absent."""
        try:
            hb = json.loads(
                self.paths.consumer_heartbeat.read_text(encoding="utf-8"))
            return max(0.0, self._clock() - float(hb["ts"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    # -- default dispatcher --------------------------------------------------

    def default_dispatcher(self, rec: L2Record) -> bool:
        """Dispatch one K3 verifier for *rec* through the supervisor chain.

        Uses ``harness/dispatch.py --role verifier`` when present (the
        existing supervisor chain supports arbitrary roles); on planes without
        it (tests, canaries) records a dispatch-intent file so exactly-once
        accounting still holds. The verifier model comes from policy — never
        hardcoded. ipybox stays disabled for K3 verification per policy
        ``[ipybox].k3_planning_verifying_enabled=false``.
        """
        intent = {
            "idem_key": rec.idem_key, "packet_id": rec.packet_id,
            "run_id": rec.run_id, "attempt": rec.attempt,
            "role": "verifier", "model": self.verifier_model,
            "reasoning": self.policy.get("models", {}).get("k3_reasoning", "max"),
            "ipybox": bool(self.policy.get("ipybox", {}).get(
                "k3_planning_verifying_enabled", False)),
            "l2_request": rec.request_path, "ts": self._clock(),
        }
        dispatch_py = self.root / "harness" / "dispatch.py"
        if dispatch_py.exists():
            # A verifier is a separate physical job.  Reusing the source
            # packet id would overwrite the worker report and would feed a
            # second ``dispatched`` event into the source packet's old state
            # machine.  The deterministic synthetic id owns lifecycle/report
            # files while the verifier report itself names the source packet.
            import dispatch as dispatch_v1  # noqa: PLC0415
            job_id = "l2v-" + hashlib.sha256(
                rec.idem_key.encode("utf-8")).hexdigest()[:24]
            if rec.request_path:
                request_value = Path(rec.request_path)
                request = (request_value if request_value.is_absolute()
                           else self.root / request_value).resolve()
            else:
                request = (self.root / "data" / "packets" /
                           (rec.packet_id + ".json"))
            source_packet = (self.root / "data" / "packets" /
                             (rec.packet_id + ".json"))
            candidate_report = (self.root / "data" / "reports" /
                                rec.packet_id / "report.json")
            revision = int(rec.revision or 1)
            synthetic = {
                "packet_id": job_id,
                "goal": "L2 verify source packet %s" % rec.packet_id,
                "authorized_paths": [str(source_packet), str(request),
                                     str(candidate_report)],
                "acceptance": ["emit one strict short-result JSON object"],
                "constraints": ["read-only", "no child agents",
                                "verdict enum is closed"],
                "needs_code_execution": False,
            }
            packet_path = self.root / "data" / "packets" / (job_id + ".json")
            _atomic_write_json(packet_path, synthetic)

            def verifier_prompt(_packet: dict[str, Any], _worktree: str,
                                _run_id: str) -> str:
                return (
                    "任务名：L2验证 %s\n"
                    "You are the configured K3 verifier. Read only these evidence handles:\n"
                    "source_packet: %s\nrequest: %s\ncandidate_report: %s\n"
                    "Return exactly one JSON object and no markdown. Required keys: "
                    "packet_id, control_packet_id, control_packet_revision, status, "
                    "conclusion, artifact_paths, finding_ids, needs_decision, verdict, "
                    "idem_key. packet_id=%s; control_packet_id=%s; "
                    "control_packet_revision=%d; idem_key=%s. status must be completed "
                    "unless evidence is unreadable. verdict must be one of pass, redo, "
                    "escalate_l2_5, escalate_l3. conclusion <=2000 chars; finding_ids "
                    "has at most 8 strings; needs_decision is null or exactly question, "
                    "decision_refs, evidence_refs. Do not modify files, run code, or "
                    "spawn agents."
                    % (rec.packet_id, source_packet, request, candidate_report,
                       rec.packet_id, rec.idem_key, revision, rec.idem_key))

            try:
                run_id = dispatch_v1.dispatch_single(
                    [job_id], False, role="verifier", mode="l2_verifier",
                    prompt_builder=verifier_prompt, capture_report=True,
                    completion={"idem_key": rec.idem_key,
                                "revision": revision},
                    detail_extra={"orchestration_v2_job": "l2_verifier",
                                  "source_packet_id": rec.packet_id,
                                  "l2_idem_key": rec.idem_key})
            except (OSError, RuntimeError, SystemExit, ValueError) as exc:
                LOG.error("dispatch failed for %s: %s", rec.idem_key, exc)
                return False
            intent["dispatch_rc"] = 0 if run_id else 1
            intent["job_packet_id"] = job_id
            intent["job_run_id"] = run_id
            _atomic_write_json(
                self.paths.dispatches / (rec.idem_key.replace(":", "_") + ".json"),
                intent)
            return bool(run_id)
        _atomic_write_json(
            self.paths.dispatches / (rec.idem_key.replace(":", "_") + ".json"),
            intent)
        return True

    # -- drain / reap --------------------------------------------------------

    def drain(self, max_records: int | None = None) -> DrainStats:
        """One drain pass: claim + dispatch every unclaimed pending record.

        Invoked by the statemachine step epilogue, the post-report reconcile
        path, and refill sync runs — no daemon required. Safe to call
        concurrently from multiple processes: the claim file is the arbiter.
        """
        stats = DrainStats()
        self.write_consumer_heartbeat()
        # Provider transport backoff blocks only new K3 claims/births.  Keep
        # the queue durable and observable; V4/refill work is unaffected and
        # the next pass probes again after the bounded backoff expires.
        from provider_health import backoff_active
        blocked, _health = backoff_active(self.root, self.verifier_model,
                                          now=self._clock())
        if blocked:
            stats.provider_backoff = sum(1 for rec in self.iter_pending()
                                         if not self._completion_path(rec.idem_key).exists())
            stats.scanned = stats.provider_backoff
            self.check_stale_pending(stats)
            self.write_consumer_heartbeat()
            return stats
        for rec in self.iter_pending():
            stats.scanned += 1
            if max_records is not None and stats.dispatched >= max_records:
                break
            if self._completion_path(rec.idem_key).exists():
                stats.already_complete += 1
                continue
            claim_path = self.try_claim(rec)
            if claim_path is None:
                stats.already_claimed += 1
                continue
            stats.claimed += 1
            self._emit_event(rec.packet_id, "l2_requested", {
                "idem_key": rec.idem_key, "run_id": rec.run_id,
                "attempt": rec.attempt, "reason": rec.reason})
            self._add_k3_demand(rec)
            with self.heartbeat_guard(rec.idem_key):
                ok = False
                try:
                    ok = self._dispatcher(rec)
                except Exception as exc:  # dispatcher bugs are fail-visible
                    stats.errors.append("%s: %s" % (rec.idem_key, exc))
                    LOG.exception("dispatcher raised for %s", rec.idem_key)
            if ok:
                stats.dispatched += 1
            else:
                # leave the claim in place: the reaper recovers it after the
                # stale timeout (never double-dispatch inside one pass).
                stats.errors.append("%s: dispatch returned False" % rec.idem_key)
        self.check_stale_pending(stats)
        self.write_consumer_heartbeat()
        return stats

    def reap_stale_claims(self) -> int:
        """Recover claims with stale heartbeats and no completion.

        Each reaped claim is preserved under ``reaped/<key>.<n>.json``; the
        pending record becomes claimable again. Past ``claim_max_reclaims``
        the packet escalates ``direct_l3`` fail-visible.

        Returns:
            number of claims reaped.
        """
        reaped = 0
        now = self._clock()
        for claim_path in sorted(self.paths.claims.glob("*.json")):
            try:
                claim = json.loads(claim_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            key = str(claim.get("idem_key", ""))
            if not key or self._completion_path(key).exists():
                continue
            hb = float(claim.get("heartbeat_ts", claim.get("claimed_ts", 0.0)))
            if now - hb < self.claim_stale_after_s:
                continue
            reclaims = self._reclaim_count(key) + 1
            dest = self.paths.reaped / (
                "%s.%d.json" % (key.replace(":", "_"), reclaims))
            try:
                os.replace(claim_path, dest)
            except OSError as exc:
                LOG.warning("cannot reap %s: %s", claim_path, exc)
                continue
            reaped += 1
            if reclaims > self.claim_max_reclaims:
                self._emit_event(str(claim.get("packet_id", "?")),
                                 "verdict_escalate_l3", {
                    "idem_key": key, "why": "l2_reclaim_budget_exhausted",
                    "reclaims": reclaims})
                # poison-pill completion so drain never re-dispatches it
                _atomic_write_json(self._completion_path(key), {
                    "idem_key": key, "status": "escalated_l3",
                    "why": "reclaim_budget_exhausted", "ts": now})
                self._sol_wake("l2_reclaim_exhausted_%s" % claim.get("packet_id"),
                               {"idem_key": key, "reclaims": reclaims})
            else:
                self._emit_event(str(claim.get("packet_id", "?")),
                                 "l2_claim_reaped", {
                    "idem_key": key, "reclaims": reclaims,
                    "stale_heartbeat_age_s": round(now - hb, 3)})
            LOG.warning("reaped stale claim %s (reclaim #%d)", key, reclaims)
        return reaped

    def recover_after_restart(self) -> int:
        """Restart semantics: reap every incomplete stale claim, then drain.

        Returns the number of claims reaped before the drain.
        """
        reaped = self.reap_stale_claims()
        self.drain()
        return reaped

    def check_stale_pending(self, stats: DrainStats | None = None) -> int:
        """Down-consumer guard: age out unclaimed records fail-visible.

        Records older than ``l2_max_age_s`` with no claim and no completion
        produce ``l2_consumer_stale`` + ``direct_l3`` and a SOL WAKE naming
        the consumer outage — never a silent drop (§2.2 step 4).
        """
        escalated = 0
        now = self._clock()
        for rec in self.iter_pending():
            if (now - rec.created_ts) <= self.l2_max_age_s:
                continue
            if self._claim_path(rec.idem_key).exists():
                continue
            if self._completion_path(rec.idem_key).exists():
                continue
            _atomic_write_json(self._completion_path(rec.idem_key), {
                "idem_key": rec.idem_key, "status": "stale_escalated",
                "ts": now})
            self._emit_event(rec.packet_id, "l2_consumer_stale", {
                "idem_key": rec.idem_key,
                "age_s": round(now - rec.created_ts, 3),
                "action": "direct_l3"})
            self._sol_wake("l2_consumer_stale_%s" % rec.packet_id, {
                "idem_key": rec.idem_key, "packet_id": rec.packet_id,
                "why": "send_l2 record aged past l2_max_age_s with no "
                       "consumer claim — L2 consumer outage", "ts": now})
            escalated += 1
            if stats is not None:
                stats.stale_escalated += 1
        return escalated

    # -- completion side -----------------------------------------------------

    def complete(self, idem_key: str, report_path: Path | str,
                 expected_revision: int | None = None) -> ValidationResult:
        """Publish a K3 verifier result for *idem_key* (verdict return path).

        Validates the report through :class:`ShortResultValidator`
        (``require_verdict=True``); a valid report writes the immutable
        completion marker and emits the matching ``verdict_*`` state-machine
        event (t31–t34). Invalid reports are quarantined via a completion
        marker with ``status="invalid"`` plus an ``exec_failed`` event (t35) —
        fail-closed, evidence preserved.
        """
        comp_path = self._completion_path(idem_key)
        if comp_path.exists():
            LOG.info("completion for %s already published — idempotent", idem_key)
            try:
                prior = json.loads(comp_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prior = {}
            return ValidationResult(
                bool(prior.get("valid")), "OK" if prior.get("valid")
                else "DUPLICATE", "completion already published", None, ())
        report_path = Path(report_path)
        try:
            doc = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            doc = None
            result = ValidationResult(False, "UNREADABLE",
                                      "cannot read verifier report: %s" % exc,
                                      None, ("UNREADABLE:%s" % exc,))
        else:
            result = self._validator.validate(
                doc, expected_revision=expected_revision)

        packet_id = "?"
        verdict = None
        if isinstance(doc, dict):
            packet_id = str(doc.get("packet_id", "?"))
            verdict = doc.get("verdict")

        marker: dict[str, Any] = {
            "idem_key": idem_key, "ts": self._clock(),
            "report_path": str(report_path), "valid": result.ok,
            "validation": result.to_dict(), "verdict": verdict,
        }
        _atomic_write_json(comp_path, marker)

        if result.ok and verdict in L2_VERDICTS:
            self._emit_event(packet_id, "verdict_%s" % verdict, {
                "idem_key": idem_key, "report_path": str(report_path)})
        else:
            self._emit_event(packet_id, "exec_failed", {
                "idem_key": idem_key, "why": "short_result_invalid",
                "validation": result.to_dict()})
        # claim is now historical; remove so the directory reflects in-flight
        try:
            self._claim_path(idem_key).unlink()
        except OSError:
            pass
        return result

    # -- exactly-once canary (layered_gate condition 2) -----------------------

    @staticmethod
    def run_canary(policy: dict[str, Any] | None = None) -> CanaryResult:
        """Prove exactly-once mechanics in an isolated temp root.

        Sequence (design §2.2 AC1 + crash-safety AC):
          1. enqueue 3 records; drain twice → exactly 3 dispatches, 3 claims;
          2. simulate crash-after-claim: age one claim's heartbeat past the
             stale bound, reap, re-drain → exactly 1 reclaim dispatch;
          3. re-run the full drain once more → 0 additional dispatches.
        """
        dispatched: list[str] = []

        def recorder(rec: L2Record) -> bool:
            dispatched.append(rec.idem_key)
            return True

        with tempfile.TemporaryDirectory(prefix="l2canary.") as tmp:
            root = Path(tmp)
            pol = dict(policy or {})
            pol.setdefault("models", {"k3_model": "canary/pinned-model"})
            pol.setdefault("l2_queue", {})
            pol["l2_queue"] = {**pol["l2_queue"],
                               "claim_stale_after_s": 30,
                               "claim_heartbeat_interval_s": 60,
                               "l2_max_age_s": 3600}
            consumer = L2Consumer(root, policy=pol, dispatcher=recorder)
            for i in range(3):
                consumer.enqueue("canary-pkt-%d" % i, "run-%d" % i, 1)
                consumer.enqueue("canary-pkt-%d" % i, "run-%d" % i, 1)  # dup
            consumer.drain()
            consumer.drain()
            first = len(dispatched)
            if first != 3:
                return CanaryResult(False, first, 3, 0,
                                    "expected 3 dispatches, got %d" % first)
            # crash-after-claim simulation: erase heartbeat freshness
            victim = sorted(consumer.paths.claims.glob("*.json"))[0]
            claim = json.loads(victim.read_text(encoding="utf-8"))
            claim["heartbeat_ts"] = 0.0  # crashed worker: heartbeat frozen
            claim["claimed_ts"] = 0.0
            _atomic_write_json(victim, claim)
            consumer.reap_stale_claims()
            consumer.drain()
            reclaims = len(dispatched) - first
            if reclaims != 1:
                return CanaryResult(False, len(dispatched), 4, reclaims,
                                    "expected exactly 1 reclaim dispatch, "
                                    "got %d" % reclaims)
            consumer.drain()
            if len(dispatched) != first + 1:
                return CanaryResult(False, len(dispatched), 4, reclaims,
                                    "extra dispatches after settle")
            return CanaryResult(True, len(dispatched), 4, 1,
                                "exactly-once canary green")


def _main(argv: Sequence[str]) -> int:
    """CLI entry: ``drain | reap | recover | canary | complete | enqueue``."""
    import argparse

    ap = argparse.ArgumentParser(description="exactly-once send_l2 consumer")
    ap.add_argument("command", choices=["drain", "reap", "recover", "canary",
                                        "complete", "enqueue"])
    ap.add_argument("--root", default=os.environ.get("LOOP_ROOT", "."))
    ap.add_argument("--policy", default=None)
    ap.add_argument("--idem-key")
    ap.add_argument("--report")
    ap.add_argument("--packet")
    ap.add_argument("--run-id")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--revision", type=int, default=None)
    args = ap.parse_args(argv)

    if args.command == "canary":
        result = L2Consumer.run_canary()
        print(json.dumps(result.__dict__))
        return 0 if result.ok else 1

    root = Path(args.root)
    policy = load_policy(Path(args.policy)) if args.policy else None
    consumer = L2Consumer(root, policy=policy)
    if args.command == "drain":
        stats = consumer.drain()
        print(json.dumps(stats.__dict__))
        return 0 if not stats.errors else 1
    if args.command == "reap":
        print(json.dumps({"reaped": consumer.reap_stale_claims()}))
        return 0
    if args.command == "recover":
        print(json.dumps({"reaped": consumer.recover_after_restart()}))
        return 0
    if args.command == "complete":
        if not (args.idem_key and args.report):
            ap.error("complete requires --idem-key and --report")
        result = consumer.complete(args.idem_key, args.report,
                                   expected_revision=args.revision)
        print(json.dumps(result.to_dict()))
        return 0 if result.ok else 1
    if args.command == "enqueue":
        if not (args.packet and args.run_id):
            ap.error("enqueue requires --packet and --run-id")
        rec = consumer.enqueue(args.packet, args.run_id, args.attempt,
                               revision=args.revision)
        print(json.dumps({"enqueued": rec is not None,
                          "idem_key": rec.idem_key if rec else None}))
        return 0
    return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(_main(sys.argv[1:]))
