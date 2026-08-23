#!/usr/bin/env python3
"""statemachine_v2.py — Extended deterministic orchestration state machine.

Phase 2 redesign (architecture §7) of ``harness/statemachine.py``:

* **All 26 existing transitions preserved** — t1–t25 verbatim from the shipped
  table, t26 (release-review ``exec_failed`` → ``SOL_ADJUDICATE``) preserved
  as the same hardcoded pre-table rule.
* **3 new states**: ``EXPAND_K3`` (plan expansion), ``L2_VERIFY`` (K3
  verifier), ``L2_RANK`` (L2.5 multi-candidate ranking).
* **12 new transitions t27–t38** (see :data:`T`), making K3 verification,
  plan expansion, timeout retry and dead-letter duty triage *on-table* so they
  can never dead-letter into Sol via the off-table path (fixes P0-3).
* **``SOL_ADJUDICATE`` removed from the terminal set** — it is a routing
  state whose only exits remain t22 (``sol_replan``, replan-capped per H-04)
  and t23 (``release_merge``, human-triggered); both semantics preserved.
* **Event-cursor race fixed** (P1-8, shipped ``statemachine.py:401-403``):
  the cursor only ever advances to the byte offset actually consumed
  (``f.tell()`` after the read loop); it is never jumped to
  ``os.path.getsize()``, so events appended by concurrent writers during
  watchdog processing are re-read next step instead of silently skipped.
  Watchdog-emitted ``timeout`` events therefore re-arrive; they are absorbed
  idempotently (a ``timeout`` for a packet already ``TIMED_OUT`` in the same
  attempt is a confirmation, not an off-table violation).
* **Watchdog blind spot fixed** (P1-9, shipped ``:298-305``): a RUNNING
  packet absent from ``spawn_times.json`` gets a synthetic spawn time from
  its transition-to-RUNNING ledger history entry, so ``elapsed`` is never
  ``None`` and stuck-RUNNING always has a timeout producer.
* :func:`validate_transition_table` checks every structural invariant and is
  run at import time — a bad table edit fails at load, not in production.

Exit codes: 0 ok · 1 I/O error · 2 dead-letters produced this step.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Final

try:
    from orchestration_common import (LoopPaths, append_ndjson, atomic_write_json,
                                      file_lock, get_logger, read_json)
except ImportError:  # pragma: no cover - direct CLI execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from orchestration_common import (LoopPaths, append_ndjson, atomic_write_json,
                                      file_lock, get_logger, read_json)

__all__ = [
    "SCHEMA",
    "STATES",
    "TERMINAL",
    "L2_STATES",
    "T",
    "TransitionError",
    "StateMachine",
    "validate_transition_table",
    "main",
]

log = get_logger("loop.statemachine_v2")

SCHEMA: Final[str] = "codex-loop-statemachine/v2"

#: Packet-id -> filename safety (unchanged from v1): 1-96 ASCII
#: letters/digits/._- only, so a pid can never escape data/ subdirectories.
PACKET_ID_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")

SUPERVISOR_HEARTBEAT_STALE_SECONDS: Final[float] = 5.0
REPLAN_CAP: Final[int] = 2        # H-04, unchanged
TIMEOUT_RETRY_MAX: Final[int] = 1  # t37 budget: only the FIRST timeout retries
L2_ATTEMPTS_MAX: Final[int] = 1    # t35 note: second L2 exec failure => direct_l3
PRE_SPAWN_FAILURE_MAX: Final[int] = 3

STATES: Final[frozenset[str]] = frozenset({
    "NONE", "PLANNED", "DISPATCHABLE", "RUNNING", "REPORTED", "ACCEPTED",
    "FAILED", "TIMED_OUT", "DUTY_REVIEW", "DEAD_LETTER", "MERGED",
    "MERGE_CONFLICT", "WAVE_DONE", "SOL_ADJUDICATE", "DONE",
    # v2 additions (§7):
    "EXPAND_K3", "L2_VERIFY", "L2_RANK",
})

#: §2.3.4 / §7: SOL_ADJUDICATE is a routing state, NOT a grave.
TERMINAL: Final[frozenset[str]] = frozenset({"MERGED", "DEAD_LETTER", "DONE"})

#: L2/K3 states carry BLOCK/ESCALATE-only power — they may never have an edge
#: directly into MERGED or DONE (invariant checked by validate_transition_table).
L2_STATES: Final[frozenset[str]] = frozenset({"EXPAND_K3", "L2_VERIFY", "L2_RANK"})

# ---------------------------------------------------------------------------
# Transition table: (from_state, event) -> (to_state, transition_number).
# t1–t25 are byte-identical to shipped statemachine.py:52-82; t26 stays a
# hardcoded pre-table rule (release-review exec failure); t27–t38 are the §7
# extension. Anything not listed is off-table -> DEAD_LETTER, fail-visible.
# ---------------------------------------------------------------------------
T: Final[dict[tuple[str, str], tuple[str, int]]] = {
    # ---- existing t1–t25 (unchanged) --------------------------------------
    ("NONE",           "planned"):             ("PLANNED",         1),
    ("PLANNED",        "dag_assert_pass"):     ("DISPATCHABLE",    2),
    ("DISPATCHABLE",   "dispatched"):          ("RUNNING",         3),
    ("RUNNING",        "subagent_stop"):       ("REPORTED",        4),   # gated on report file
    ("RUNNING",        "timeout"):             ("TIMED_OUT",       5),
    ("RUNNING",        "exec_failed"):         ("FAILED",          6),
    ("REPORTED",       "acceptance_pass"):     ("ACCEPTED",        7),
    ("REPORTED",       "acceptance_fail"):     ("FAILED",          8),
    # Retry admission is not a physical birth.  It returns the packet to the
    # actuator-owned queue; only a later dispatched(run_id) may claim RUNNING.
    ("FAILED",         "retry_dispatch"):      ("DISPATCHABLE",    9),
    ("FAILED",         "duty_review"):         ("DUTY_REVIEW",    10),
    ("DUTY_REVIEW",    "duty_retryable"):      ("RUNNING",        11),   # gated on duty_officer.enforce
    ("DUTY_REVIEW",    "duty_fixable"):        ("RUNNING",        12),   # gated on duty_officer.enforce
    ("DUTY_REVIEW",    "duty_terminal"):       ("DEAD_LETTER",    13),
    ("TIMED_OUT",      "budget_exhausted"):    ("DEAD_LETTER",    14),
    ("FAILED",         "budget_exhausted"):    ("DEAD_LETTER",    15),
    ("ACCEPTED",       "merged"):              ("MERGED",         16),
    ("ACCEPTED",       "merge_conflict"):      ("MERGE_CONFLICT", 17),
    ("MERGED",         "wave_complete"):       ("WAVE_DONE",      18),
    ("DEAD_LETTER",    "dead_letter_summary"): ("SOL_ADJUDICATE", 19),
    ("MERGE_CONFLICT", "conflict_pointer"):    ("SOL_ADJUDICATE", 20),
    ("WAVE_DONE",      "wave_summary"):        ("SOL_ADJUDICATE", 21),
    ("SOL_ADJUDICATE", "sol_replan"):          ("PLANNED",        22),   # replan-capped (H-04)
    ("SOL_ADJUDICATE", "release_merge"):       ("DONE",           23),   # human-triggered only
    ("REPORTED",       "review_verdict_pass"): ("SOL_ADJUDICATE", 24),
    ("REPORTED",       "review_verdict_fail"): ("SOL_ADJUDICATE", 25),
    # t26 is the hardcoded release-review exec_failed rule (see apply_event).
    # ---- v2 extension t27–t38 (§7) -----------------------------------------
    ("NONE",           "skeleton_ready"):      ("EXPAND_K3",      27),   # plan pipeline entry
    ("EXPAND_K3",      "expansion_valid"):     ("PLANNED",        28),   # schema-valid DAG
    ("EXPAND_K3",      "expansion_invalid"):   ("SOL_ADJUDICATE", 29),   # bounded adjudication
    ("REPORTED",       "l2_requested"):        ("L2_VERIFY",      30),   # l2_consumer claim
    ("L2_VERIFY",      "verdict_pass"):        ("ACCEPTED",       31),   # merge queue + L0 + L4 still follow
    ("L2_VERIFY",      "verdict_redo"):        ("FAILED",         32),   # re-enters retry path
    ("L2_VERIFY",      "verdict_escalate_l2_5"): ("L2_RANK",      33),   # <=3 candidates
    ("L2_VERIFY",      "verdict_escalate_l3"): ("SOL_ADJUDICATE", 34),   # bounded <=1k-token packet
    ("L2_VERIFY",      "exec_failed"):         ("FAILED",         35),   # l2_attempts cap = 1
    ("L2_RANK",        "best_candidate"):      ("L2_VERIFY",      36),   # ranked winner re-verified
    ("TIMED_OUT",      "retry_dispatch"):      ("DISPATCHABLE",   37),   # timeout_retry_max = 1
    ("DEAD_LETTER",    "duty_triage"):         ("DUTY_REVIEW",    38),   # one zero-Sol triage pass
}

#: Audit-only events emitted by harness scripts that are NOT transitions.
INFO_EVENTS: Final[frozenset[str]] = frozenset({
    "worktree_allocated", "worktree_released",
    "SubagentStart", "SubagentStartRecovered", "SubagentStop",
    "exec_spawned", "exec_supervisor_started", "exec_exited", "exec_killed",
    "dispatch_refused", "spawn_health_gate_passed",
    "spawn_lost", "parent_stop", "close_requested", "lifecycle_reconciled",
    "dispatch_dry_run", "sol_budget_blocked",
    # v2 audit events:
    "l2_consumer_stale", "l2_claim_reaped",
    "governor.fail_closed", "governor.break_glass",
    "governor.state_key_unattested", "budget_state_change", "route_decision",
})


class TransitionError(RuntimeError):
    """The transition table violates a structural invariant."""


# ---------------------------------------------------------------------------
# Table validation — run at import; a bad edit fails at load, never silently.
# ---------------------------------------------------------------------------
def validate_transition_table(table: dict[tuple[str, str], tuple[str, int]] = T) -> None:
    """Check every structural invariant of the extended table.

    Raises :class:`TransitionError` on the first violation.  Invariants
    (architecture §7 "Invariants preserved"):

    1. every from/to state is a declared state;
    2. transition numbers are unique, and t1–t25 + t27–t38 are all present
       (t26 is the hardcoded release-review rule, deliberately off-table);
    3. terminal states are exactly ``{MERGED, DEAD_LETTER, DONE}`` and
       ``SOL_ADJUDICATE`` is NOT terminal;
    4. ``SOL_ADJUDICATE`` keeps exactly its two exits: t22 → PLANNED and
       t23 → DONE (replan + human release semantics preserved);
    5. L2 states never edge into MERGED or DONE (BLOCK/ESCALATE-only power);
    6. terminal states other than DEAD_LETTER have no outbound edges;
       DEAD_LETTER's only exits are t19 (summary) and t38 (duty triage);
    7. every non-terminal state has at least one outbound edge (no black
       holes that could only exit via off-table dead-lettering);
    8. TIMED_OUT has a retry edge (t37) — the P0-3 headline fix.
    """
    numbers: dict[int, tuple[str, str]] = {}
    outbound: dict[str, list[tuple[str, str, int]]] = {s: [] for s in STATES}
    for (frm, event), (to, num) in table.items():
        if frm not in STATES:
            raise TransitionError(f"t{num}: unknown from-state {frm!r}")
        if to not in STATES:
            raise TransitionError(f"t{num}: unknown to-state {to!r}")
        if num in numbers:
            raise TransitionError(f"duplicate transition number t{num}: "
                                  f"{numbers[num]} and {(frm, event)}")
        numbers[num] = (frm, event)
        outbound[frm].append((event, to, num))

    expected = set(range(1, 26)) | set(range(27, 39))
    missing = expected - set(numbers)
    if missing:
        raise TransitionError(f"missing transitions: {sorted(missing)}")
    if 26 in numbers:
        raise TransitionError("t26 must stay a hardcoded release-review rule, "
                              "not a table row")

    if "SOL_ADJUDICATE" in TERMINAL:
        raise TransitionError("SOL_ADJUDICATE must not be terminal (§2.3.4)")
    if TERMINAL != {"MERGED", "DEAD_LETTER", "DONE"}:
        raise TransitionError(f"terminal set drifted: {sorted(TERMINAL)}")

    sol_exits = {(e, to, n) for e, to, n in outbound["SOL_ADJUDICATE"]}
    if sol_exits != {("sol_replan", "PLANNED", 22), ("release_merge", "DONE", 23)}:
        raise TransitionError(f"SOL_ADJUDICATE exits drifted: {sorted(sol_exits)}")

    for state in L2_STATES:
        for event, to, num in outbound[state]:
            if to in ("MERGED", "DONE"):
                raise TransitionError(
                    f"t{num}: L2 state {state} may never edge into {to} "
                    f"(BLOCK/ESCALATE-only power semantics)")

    for state in TERMINAL:
        exits = outbound[state]
        if state == "DEAD_LETTER":
            allowed = {("dead_letter_summary", "SOL_ADJUDICATE", 19),
                       ("duty_triage", "DUTY_REVIEW", 38)}
            if set(exits) != allowed:
                raise TransitionError(f"DEAD_LETTER exits drifted: {sorted(exits)}")
        elif state == "MERGED":
            if set(exits) != {("wave_complete", "WAVE_DONE", 18)}:
                raise TransitionError(f"MERGED exits drifted: {sorted(exits)}")
        elif exits:
            raise TransitionError(f"terminal state {state} has outbound edges: {exits}")

    for state in STATES - TERMINAL - {"DONE"}:
        if not outbound[state] and state != "NONE":
            # NONE always has t1/t27; every live state needs an exit.
            raise TransitionError(f"state {state} has no outbound edge")

    if ("TIMED_OUT", "retry_dispatch") not in table:
        raise TransitionError("TIMED_OUT must be retryable (t37, P0-3 fix)")


validate_transition_table()


def safe_pid_filename(pid: Any) -> str:
    """Validate a packet id for filename use.  Fail-visible: ``SystemExit(1)``
    before any write (unchanged v1 discipline)."""
    if not isinstance(pid, str) or not PACKET_ID_RE.fullmatch(pid):
        sys.stderr.write("invalid packet id %r: allowed 1-96 ASCII "
                         "letters/digits/._- only; refusing write\n" % (pid,))
        raise SystemExit(1)
    return pid


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
class StateMachine:
    """Deterministic packet-lifecycle driver over ``data/events.ndjson``.

    Zero-model: no LLM is ever consulted.  All state lives on disk; the class
    holds no cross-invocation state, so it is safe to construct per step.
    """

    def __init__(self, paths: LoopPaths | None = None,
                 enforce_duty: bool = True,
                 job_max_runtime_seconds: int = 1800) -> None:
        """``enforce_duty`` ships **True** in v2 (P0-3.4 fix: duty officer is
        an absorber, not a generator)."""
        self.paths = paths or LoopPaths.resolve()
        self.enforce_duty = enforce_duty
        self.job_max_runtime_seconds = job_max_runtime_seconds
        self.dead_this_step = 0

    # -- ledger IO ------------------------------------------------------------
    def load_ledger(self) -> dict[str, Any]:
        led = read_json(self.paths.ledger, None)
        if not isinstance(led, dict):
            led = {"packets": {}, "waves": []}
        led.setdefault("packets", {})
        led.setdefault("schema", SCHEMA)
        return led

    def save_ledger(self, led: dict[str, Any]) -> None:
        led["schema"] = SCHEMA
        atomic_write_json(self.paths.ledger, led)

    def _append_event(self, obj: dict[str, Any]) -> None:
        append_ndjson(self.paths.events, obj, lock_path=self.paths.events_lock)

    # -- fail-visible sinks -----------------------------------------------------
    def sol_wake(self, pid: str, reason: str, detail: Any) -> Path:
        """Bounded Sol wake summary on disk (fail-visible, spec S3/S5)."""
        safe_pid_filename(pid)
        wake_dir = self.paths.data / "sol_wake"
        wake_dir.mkdir(parents=True, exist_ok=True)
        path = wake_dir / ("%d_%s.md" % (int(time.time()), pid))
        path.write_text(
            "# SOL WAKE — %s\npacket: %s\nreason: %s\ndetail: %s\n"
            "report: data/reports/%s/report.json\n"
            "dead_letter: data/dead_letters/%s.json\n"
            % (reason, pid, reason, json.dumps(detail)[:800], pid, pid),
            encoding="utf-8")
        append_ndjson(self.paths.data / "escalation_log.jsonl",
                      {"ts": time.time(), "packet_id": pid, "level": "SOL_WAKE",
                       "reason": reason, "summary_path": str(path)})
        return path

    def l4_summary(self, pid: str, reason: str, detail: Any) -> Path:
        """Queue a bounded direct_l4 summary for the HUMAN release gate."""
        safe_pid_filename(pid)
        l4_dir = self.paths.data / "l4_queue"
        l4_dir.mkdir(parents=True, exist_ok=True)
        path = l4_dir / ("%d_%s.md" % (int(time.time()), pid))
        path.write_text(
            "# DIRECT_L4 — %s\npacket: %s\nreason: %s\ndetail: %s\n"
            "report: data/reports/%s/report.json\n"
            % (reason, pid, reason, json.dumps(detail)[:800], pid),
            encoding="utf-8")
        append_ndjson(self.paths.data / "escalation_log.jsonl",
                      {"ts": time.time(), "packet_id": pid, "level": "DIRECT_L4",
                       "reason": reason, "summary_path": str(path)})
        return path

    def to_dead_letter(self, led: dict[str, Any], pid: str,
                       reason: str, detail: Any) -> None:
        safe_pid_filename(pid)
        pkt = led["packets"].setdefault(
            pid, {"state": "NONE", "history": [], "attempts": 0})
        pkt["state"] = "DEAD_LETTER"
        pkt["history"].append({"ts": time.time(), "to": "DEAD_LETTER", "via": reason})
        dl_dir = self.paths.data / "dead_letters"
        dl_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(dl_dir / (pid + ".json"),
                          {"packet_id": pid, "reason": reason, "detail": detail,
                           "prior_history": pkt["history"][-10:], "ts": time.time()})
        self.sol_wake(pid, reason, detail)
        self.dead_this_step += 1

    # -- caps ----------------------------------------------------------------------
    def _bump_counter(self, path: Path, pid: str, cap: int) -> tuple[int, bool]:
        """Shared per-packet counter (replan/timeout-retry).  Counter I/O
        failure counts as exceeded — fail toward the human gate."""
        try:
            counters = read_json(path, {}) or {}
            count = int(counters.get(pid, 0)) + 1
            counters[pid] = count
            atomic_write_json(path, counters)
        except (OSError, ValueError, TypeError):
            return cap + 1, True
        return count, count > cap

    def bump_replan_counter(self, pid: str) -> tuple[int, bool]:
        return self._bump_counter(self.paths.data / "replan_counters.json",
                                  pid, REPLAN_CAP)

    def bump_timeout_retry_counter(self, pid: str) -> tuple[int, bool]:
        return self._bump_counter(self.paths.data / "timeout_retry_counters.json",
                                  pid, TIMEOUT_RETRY_MAX)

    # -- report currency (unchanged v1 semantics) ------------------------------------
    def report_is_current(self, pid: str) -> bool:
        report = self.paths.data / "reports" / pid / "report.json"
        if not report.exists():
            return False
        # Existence alone is not a second truth: a zero-byte/truncated/null
        # placeholder may be left by a crashed publisher.  Keep the legacy
        # report contract broad, but require a non-empty JSON object and reject
        # an explicitly mismatched packet identity.
        try:
            value = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(value, dict) or not value:
            return False
        if value.get("packet_id") not in (None, pid):
            return False
        times = read_json(self.paths.data / "spawn_times.json", {})
        stamp = times.get(pid) if isinstance(times, dict) else None
        if not isinstance(stamp, dict) or stamp.get("ts") is None:
            return True  # legacy dispatch has no generation timestamp
        try:
            return report.stat().st_mtime >= float(stamp["ts"])
        except (OSError, ValueError, TypeError):
            return True

    # -- event application --------------------------------------------------------------
    def apply_event(self, led: dict[str, Any], ev: dict[str, Any]) -> str | None:
        """Apply one event; returns the resulting state or ``None`` for
        info-only events.  Off-table events dead-letter, fail-visible."""
        pid = ev.get("packet_id", "?")
        name = ev.get("event", "?")
        if isinstance(pid, str) and pid.startswith(("l2v-", "v2job-")):
            # L2 verifier execution is governed by the exactly-once queue;
            # only its verdict event (emitted against the source packet) is a
            # packet-state transition.
            return None
        if name in INFO_EVENTS:
            return None
        if pid not in led["packets"] and name not in {"planned", "skeleton_ready"}:
            # Transport/supervisor events may carry an adhoc run id rather
            # than a canonical packet id.  They are lifecycle audit evidence,
            # not authority to create a new state-machine packet.
            return None
        pkt = led["packets"].setdefault(
            pid, {"state": "NONE", "history": [], "attempts": 0})
        detail = ev.get("detail") if isinstance(ev.get("detail"), dict) else {}
        event_run_id = ev.get("run_id") or detail.get("run_id")
        current_run_id = pkt.get("current_run_id")
        event_attempt = ev.get("attempt", detail.get("attempt"))
        try:
            event_attempt = int(event_attempt) if event_attempt is not None else None
        except (TypeError, ValueError):
            event_attempt = None
        current_attempt = int(pkt.get("attempts", 0) or 0)
        if event_attempt is not None and event_attempt < current_attempt:
            pkt["history"].append({"ts": time.time(), "to": pkt["state"],
                                   "via": "stale_generation_ignored", "event": name,
                                   "event_attempt": event_attempt,
                                   "current_attempt": current_attempt})
            return pkt["state"]

        # Admit exactly one authoritative transport generation.  A terminal
        # edge can race the detached refill actuator: the controller may have
        # selected the packet while its ledger entry still said DISPATCHABLE,
        # producing a duplicate run with the same numeric attempt.  run_id,
        # not the display/attempt counter, disambiguates that race.
        if (name in {"subagent_stop", "exec_failed", "timeout"}
                and current_run_id and event_run_id
                and event_run_id != current_run_id):
            pkt["history"].append({"ts": time.time(), "to": pkt["state"],
                                   "via": "stale_run_id_ignored",
                                   "event": name,
                                   "event_run_id": event_run_id,
                                   "current_run_id": current_run_id})
            return pkt["state"]
        if (name == "dispatched" and event_run_id and current_run_id
                and event_run_id != current_run_id
                and pkt["state"] in {"RUNNING", "REPORTED", "ACCEPTED",
                                     "MERGED", "WAVE_DONE", "DONE"}):
            pkt["history"].append({"ts": time.time(), "to": pkt["state"],
                                   "via": "duplicate_generation_ignored",
                                   "event": name,
                                   "event_run_id": event_run_id,
                                   "current_run_id": current_run_id})
            return pkt["state"]

        # CreateProcess/pre-spawn failure means no worker generation ran.
        # Preserve the bounded packet as refill debt instead of turning a
        # transport admission failure into a canonical execution failure.
        if (name == "exec_failed" and detail.get("phase") == "pre_spawn"
                and pkt["state"] in {"DISPATCHABLE", "RUNNING"}):
            failures = int(pkt.get("pre_spawn_failures", 0) or 0) + 1
            pkt["pre_spawn_failures"] = failures
            if failures > PRE_SPAWN_FAILURE_MAX:
                pkt["state"] = "DUTY_REVIEW"
                pkt.pop("current_run_id", None)
                pkt["history"].append({"ts": time.time(),
                                       "to": "DUTY_REVIEW",
                                       "via": "pre_spawn_failure_exhausted",
                                       "pre_spawn_failures": failures,
                                       "cap": PRE_SPAWN_FAILURE_MAX,
                                       "detail": detail})
                return "DUTY_REVIEW"
            pkt["state"] = "DISPATCHABLE"
            if (not current_run_id or not event_run_id
                    or event_run_id == current_run_id):
                pkt.pop("current_run_id", None)
            pkt["history"].append({"ts": time.time(),
                                   "to": "DISPATCHABLE",
                                   "via": "pre_spawn_failure_retryable",
                                   "event_run_id": event_run_id,
                                   "detail": detail})
            return "DISPATCHABLE"

        # Idempotent confirmations (v1 rules + the v2 timeout re-read rule).
        if (pkt["state"] == "REPORTED" and name == "subagent_stop"
                and (self.paths.data / "reports" / pid / "report.json").exists()):
            pkt["history"].append({"ts": time.time(), "to": "REPORTED",
                                   "via": "late_subagent_stop_confirmed",
                                   "event_attempt": event_attempt})
            return "REPORTED"
        if pkt["state"] == "RUNNING" and name == "dispatched" \
                and event_attempt == current_attempt \
                and (not current_run_id or not event_run_id
                     or event_run_id == current_run_id):
            pkt["history"].append({"ts": time.time(), "to": "RUNNING",
                                   "via": "generation_dispatch_confirmed",
                                   "event_attempt": event_attempt})
            return "RUNNING"
        # v2 cursor-race companion rule: with the cursor no longer jumped past
        # watchdog appends (P1-8 fix), the watchdog's own 'timeout' audit event
        # is re-read on the next step.  A timeout for a packet ALREADY
        # TIMED_OUT in the same generation is an idempotent confirmation.
        if pkt["state"] == "TIMED_OUT" and name == "timeout":
            pkt["history"].append({"ts": time.time(), "to": "TIMED_OUT",
                                   "via": "timeout_confirmed_idempotent",
                                   "event_attempt": event_attempt})
            return "TIMED_OUT"

        # t26 (hardcoded, unchanged): flagged release-review packet whose
        # worker fails goes straight back to SOL_ADJUDICATE.
        if pkt.get("release_review") and pkt["state"] == "RUNNING" \
                and name == "exec_failed":
            pkt["state"] = "SOL_ADJUDICATE"
            pkt["history"].append({"ts": time.time(), "to": "SOL_ADJUDICATE",
                                   "via": name, "t": 26, "release_review": True})
            return "SOL_ADJUDICATE"

        key = (pkt["state"], name)
        if key not in T:
            self.to_dead_letter(led, pid, "off_table_event",
                                {"event": name, "from_state": pkt["state"]})
            return "DEAD_LETTER"
        to_state, num = T[key]

        # t4 gate: hook is fast path only; report file is the second truth.
        if num == 4 and not self.report_is_current(pid):
            self.to_dead_letter(led, pid, "report_missing_on_stop", {"event": name})
            return "DEAD_LETTER"
        # Duty enforcement (t11/t12) — v2 ships enforce=true; the record-only
        # legacy path is preserved for explicit cold-start rollback.
        if num in (11, 12) and not self.enforce_duty:
            append_ndjson(self.paths.data / "escalation_log.jsonl",
                          {"ts": time.time(), "packet_id": pid,
                           "level": "DUTY_RECORD_ONLY", "ruling": name,
                           "routed": False})
            self.to_dead_letter(led, pid, "duty_ruling_recorded_not_routed",
                                {"ruling": name})
            return "DEAD_LETTER"
        # t22 replan cap (H-04, unchanged semantics).
        if num == 22:
            count, exceeded = self.bump_replan_counter(pid)
            if exceeded:
                self.l4_summary(pid, "replan_cap_exceeded",
                                {"replan_count": count, "cap": REPLAN_CAP})
                pkt["history"].append({"ts": time.time(), "to": "SOL_ADJUDICATE",
                                       "via": "replan_cap_forced_l4", "t": 22})
                return "SOL_ADJUDICATE"
        # t37 timeout-retry budget: only the first timeout re-dispatches; the
        # second dead-letters (then t38 duty triage gives it one zero-Sol pass).
        if num == 37:
            count, exceeded = self.bump_timeout_retry_counter(pid)
            if exceeded:
                self.to_dead_letter(led, pid, "timeout_retry_exhausted",
                                    {"timeout_retries": count,
                                     "cap": TIMEOUT_RETRY_MAX})
                return "DEAD_LETTER"
        # t35 L2 attempts cap: a second L2 exec failure escalates direct_l3
        # (bounded Sol adjudication) instead of looping the verifier.
        if num == 35:
            l2_attempts = int(pkt.get("l2_attempts", 0) or 0) + 1
            pkt["l2_attempts"] = l2_attempts
            if l2_attempts > L2_ATTEMPTS_MAX:
                pkt["state"] = "SOL_ADJUDICATE"
                pkt["history"].append({"ts": time.time(), "to": "SOL_ADJUDICATE",
                                       "via": "l2_attempts_exhausted", "t": 35})
                return "SOL_ADJUDICATE"

        pkt["state"] = to_state
        pkt["history"].append({"ts": time.time(), "to": to_state,
                               "via": name, "t": num})
        if name == "retry_dispatch":
            # The failed generation is closed.  The next authoritative run id
            # is assigned only by the physical dispatch event.
            pkt.pop("current_run_id", None)
        if name == "dispatched" and event_run_id:
            pkt["current_run_id"] = event_run_id
        if name == "dispatched":
            pkt["pre_spawn_failures"] = 0
        if num in (9, 37):
            pkt["attempts"] = int(pkt.get("attempts", 0) or 0) + 1
        return to_state

    # -- reconcile / watchdog --------------------------------------------------------
    def reconcile(self, led: dict[str, Any]) -> None:
        """Fallback: hook event lost but report file landed -> REPORTED."""
        for pid, pkt in led["packets"].items():
            if pkt["state"] == "RUNNING" and self.report_is_current(pid):
                pkt["state"] = "REPORTED"
                pkt["history"].append({"ts": time.time(), "to": "REPORTED",
                                       "via": "report_file_fallback", "t": 4})

    def _synthetic_spawn_ts(self, pkt: dict[str, Any]) -> float | None:
        """P1-9 fix: derive a spawn time from the packet's own history — the
        ts of its most recent transition into RUNNING."""
        for entry in reversed(pkt.get("history", [])):
            if entry.get("to") == "RUNNING":
                try:
                    return float(entry.get("ts"))
                except (TypeError, ValueError):
                    return None
        return None

    def watchdog(self, led: dict[str, Any]) -> list[str]:
        """H-02 producer for t5 with the blind spot closed: a RUNNING packet
        missing from ``spawn_times.json`` uses its ledger transition ts, so
        ``elapsed`` is never ``None`` and stuck-RUNNING always times out."""
        times = read_json(self.paths.data / "spawn_times.json", {}) or {}
        exec_roster = read_json(self.paths.data / "lifecycle" / "exec_roster.json",
                                {"jobs": {}}) or {"jobs": {}}
        limit = self.job_max_runtime_seconds
        now, fired = time.time(), []
        for pid, pkt in led["packets"].items():
            if pkt["state"] != "RUNNING":
                continue
            stamp = times.get(pid)
            mode = stamp.get("mode") if isinstance(stamp, dict) else "legacy"
            ts = stamp.get("ts") if isinstance(stamp, dict) else stamp
            if ts is None:
                ts = self._synthetic_spawn_ts(pkt)   # P1-9 fallback
                mode = "ledger_synthetic"
            try:
                elapsed = now - float(ts) if ts is not None else None
            except (TypeError, ValueError):
                elapsed = None
            if elapsed is None or elapsed <= limit:
                continue
            job = exec_roster.get("jobs", {}).get(pid, {})
            run_id = stamp.get("run_id") if isinstance(stamp, dict) else None
            heartbeat = job.get("heartbeat_at")
            try:
                heartbeat_fresh = (heartbeat is not None and
                                   now - float(heartbeat)
                                   <= SUPERVISOR_HEARTBEAT_STALE_SECONDS)
            except (TypeError, ValueError):
                heartbeat_fresh = False
            if (mode == "single" and job.get("run_id") == run_id
                    and job.get("state") in ("starting", "running")
                    and heartbeat_fresh):
                continue  # supervisor owns this generation
            self._append_event({"ts": now, "packet_id": pid, "event": "timeout",
                                "attempt": (stamp.get("attempt")
                                            if isinstance(stamp, dict) else None),
                                "run_id": run_id,
                                "detail": {"why": "watchdog", "spawn_ts": ts,
                                           "mode": mode, "limit_s": limit}})
            pkt["state"] = "TIMED_OUT"
            pkt["history"].append({"ts": now, "to": "TIMED_OUT",
                                   "via": "timeout", "t": 5})
            fired.append(pid)
        return fired

    # -- step -------------------------------------------------------------------------
    def step(self) -> dict[str, str]:
        """One deterministic step: drain new events, reconcile, watchdog.

        **Cursor-race fix (P1-8):** the cursor is written exactly once, to the
        byte offset the reader actually consumed (``f.tell()``).  It is never
        advanced to ``getsize()`` after watchdog appends — concurrent writers
        can no longer have their events silently skipped.  The watchdog's own
        appended events are re-read next step and absorbed idempotently.
        """
        with file_lock(self.paths.data / "progress_ledger.lock"):
            led = self.load_ledger()
            self.dead_this_step = 0
            cursor_path = self.paths.data / ".sm_cursor"
            ledger_cursor = led.get("event_cursor")
            if isinstance(ledger_cursor, int) and not isinstance(ledger_cursor, bool):
                offset = max(0, ledger_cursor)
            else:
                # One-time compatibility with ledgers produced before the
                # cursor became part of the atomic ledger commit.
                try:
                    offset = int(cursor_path.read_text().strip() or 0) \
                        if cursor_path.exists() else 0
                except (OSError, ValueError):
                    offset = 0
            consumed = offset
            events_path = self.paths.events
            if events_path.exists():
                with events_path.open(encoding="utf-8") as handle:
                    handle.seek(offset)
                    while True:
                        line = handle.readline()
                        if not line or not line.endswith("\n"):
                            break
                        stripped = line.strip()
                        if stripped:
                            try:
                                ev = json.loads(stripped)
                            except ValueError:
                                ev = {"packet_id": "malformed",
                                      "event": "unparseable", "raw": stripped[:200]}
                            self.apply_event(led, ev)
                        consumed = handle.tell()
            self.reconcile(led)
            self.watchdog(led)
            # Commit state and its consumed event offset in the same atomic
            # JSON replace.  If this save fails, neither becomes authoritative
            # and the events are safely replayed on the next step.
            led["event_cursor"] = consumed
            self.save_ledger(led)
            # Legacy v1/read-only tooling still observes .sm_cursor.  It is a
            # cache only; the ledger field above is the recovery authority.
            cursor_path.write_text(str(consumed), encoding="utf-8")
            return {pid: pkt["state"] for pid, pkt in led["packets"].items()}

    def wave_check(self, *, manifest_id: str | None = None,
                   parent_session_id: str | None = None) -> bool:
        """Check terminal/report completeness within one explicit wave scope.

        An unscoped call preserves the legacy global-ledger check.  Callers
        operating on parent-manifest work should pass ``manifest_id`` so an
        older, unrelated manifest cannot block the current wave forever.
        """
        if manifest_id is not None and parent_session_id is not None:
            raise ValueError("wave_check accepts only one scope key")
        led = self.load_ledger()
        packets = led["packets"]
        if manifest_id is not None:
            packets = {pid: pkt for pid, pkt in packets.items()
                       if pkt.get("manifest_id") == manifest_id}
        elif parent_session_id is not None:
            packets = {pid: pkt for pid, pkt in packets.items()
                       if pkt.get("parent_session_id") == parent_session_id}
        states = [pkt["state"] for pkt in packets.values()]
        all_term = bool(states) and all(
            s in TERMINAL or s in ("WAVE_DONE", "SOL_ADJUDICATE") for s in states)
        reports_ok = all(
            (self.paths.data / "reports" / pid / "report.json").is_file()
            and (self.paths.data / "reports" / pid / "report.json").stat().st_size > 0
            for pid in packets)
        return all_term and reports_ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="LOOP-F2 deterministic state machine v2 (37 transitions)")
    ap.add_argument("cmd", choices=["step", "reconcile", "wave-check",
                                    "state", "validate"])
    ap.add_argument("--packet", help="packet id for 'state'")
    wave_scope = ap.add_mutually_exclusive_group()
    wave_scope.add_argument("--manifest-id",
                            help="limit wave-check to one manifest generation")
    wave_scope.add_argument("--parent-session-id",
                            help="limit wave-check to one parent session")
    ap.add_argument("--no-enforce-duty", action="store_true",
                    help="legacy cold-start duty semantics (record-only)")
    args = ap.parse_args(argv)
    sm = StateMachine(enforce_duty=not args.no_enforce_duty)
    if args.cmd == "validate":
        validate_transition_table()
        print(json.dumps({"schema": SCHEMA, "transitions": len(T),
                          "states": len(STATES), "valid": True}))
        return 0
    if args.cmd == "state":
        led = sm.load_ledger()
        pkt = led["packets"].get(args.packet or "", {"state": "NONE"})
        print(pkt["state"])
        return 0
    if args.cmd == "wave-check":
        ok = sm.wave_check(manifest_id=args.manifest_id,
                           parent_session_id=args.parent_session_id)
        print("WAVE_DONE_READY" if ok else "WAVE_INCOMPLETE")
        return 0 if ok else 1
    try:
        states = sm.step()
    except OSError as exc:
        log.error("statemachine step I/O error: %s", exc)
        return 1
    try:
        from orchestration_epilogue import run_epilogue
        run_epilogue(sm.paths.root, source="statemachine_v2_%s" % args.cmd)
    except Exception as exc:
        log.error("orchestration epilogue failed visibly: %s", exc)
    print(json.dumps(states))
    return 2 if sm.dead_this_step else 0


if __name__ == "__main__":
    sys.exit(main())
