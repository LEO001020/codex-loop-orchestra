#!/usr/bin/env python3
"""root_turn_governor.py — Root-turn boundary governor (supersedes
``hooks/sol_tool_gate.py``; P0-5 fix, architecture §2.5).

The decisive change of the governance redesign: the primary actuator moves
from the dispatch boundary (which carries no Sol tokens) to the **root turn
boundary**, where the actual consumer lives.  This module is deployed as a
PreToolUse hook (user level, both planes) and enforces:

* **Bounded planning lease** — "planning" derived from an empty ledger is no
  longer a permanent exemption.  The lease
  (``data/governor/planning_lease.json``) expires after
  ``planning_max_turns`` (default 6) or ``planning_max_new_tokens`` (default
  30,000) without a packet-creation event; expired => gated tools are denied
  with the actionable reason "decompose or dispatch".
* **Attested loop-state** — an explicit ``loop_state`` ledger key is honoured
  only when accompanied by a matching attestation appended through the
  harness CLI (:func:`loop_state_set`).  A bare key written by the
  constrained session is ignored and flagged
  (``governor.state_key_unattested``): the constrained party no longer
  controls its own exemption switch.
* **Fail-closed with audited break-glass** — ledger unreadable / meter STALE
  / share report missing => gated tools deny for non-planning states
  (``governor.fail_closed`` event).  ``LOOP_GOVERNOR_OVERRIDE=<reason>``
  bypasses, but every bypassed call appends a ``governor.break_glass`` event
  — observable, auditable, never silent.
* **Hysteresis** — share-band state (NORMAL/HIGH) enters HIGH only after
  ``enter_samples`` consecutive samples above ``enter_high`` and leaves only
  after ``leave_samples`` below ``leave_high``; kills the bang-bang cycle.
* **Token-share enforcement** — when the Sol 5h share (or per-root 1h share)
  is HIGH, non-exempt Sol tool calls are denied with an actionable
  auto-delegation message naming the dispatch command (xRouter-style: the
  orchestrator pays an explicit, visible cost for doing work itself).
* **Budget integration** — the budget controller's THROTTLE/DEGRADE/BREAK
  states map onto the deny/degrade ladder; state-machine loop-state
  derivation counts ``EXPAND_K3``/``L2_VERIFY``/``L2_RANK`` as *execution*
  (K3 work must never re-open Sol's tool window; §7).
"""
from __future__ import annotations

import enum
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Mapping

try:
    from orchestration_common import (LoopPaths, OrchestrationPolicy, PolicyError,
                                      append_ndjson, atomic_write_json, file_lock,
                                      get_logger, idem_key, read_json, utc_now)
except ImportError:  # pragma: no cover - hook execution path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from orchestration_common import (LoopPaths, OrchestrationPolicy, PolicyError,
                                      append_ndjson, atomic_write_json, file_lock,
                                      get_logger, idem_key, read_json, utc_now)

__all__ = [
    "GovernorDecision",
    "Verdict",
    "PlanningLease",
    "HysteresisController",
    "RootTurnGovernor",
    "loop_state_set",
    "main",
]

log = get_logger("loop.root_turn_governor")

#: Sol may use any tool in these LOOP states (subject to the planning lease).
ALLOWED_STATES: Final[frozenset[str]] = frozenset(
    {"planning", "adjudication", "release_finalize"})

#: Tool-name prefixes gated on the root session (unchanged from v1; plain
#: conversation and packet/dispatch tools are never gated, so "decompose and
#: dispatch" is always available — R3 mitigation).
GATED_TOOLS: Final[tuple[str, ...]] = (
    "shell", "shell_command", "bash", "local_shell", "exec_command",
    "functions.exec", "run_terminal", "terminal", "web_search", "search",
    "grep", "glob", "mcp__", "read_mcp_resource", "list_mcp",
    "read_many_files", "read_file", "list_files", "pytest", "test")

#: Ledger packet states that put the loop in "adjudication".
ADJUDICATION_STATES: Final[frozenset[str]] = frozenset(
    {"SOL_ADJUDICATE", "DEAD_LETTER", "MERGE_CONFLICT", "WAVE_DONE",
     "WAVE_DONE_READY", "SOL_WAKE"})
TERMINAL_STATES: Final[frozenset[str]] = frozenset({"MERGED", "DONE"})
#: §7 loop-state derivation update: K3 work states count as EXECUTION —
#: K3 work must never re-open Sol's tool window.
EXECUTION_K3_STATES: Final[frozenset[str]] = frozenset(
    {"EXPAND_K3", "L2_VERIFY", "L2_RANK"})

BREAK_GLASS_ENV: Final[str] = "LOOP_GOVERNOR_OVERRIDE"


class Verdict(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class GovernorDecision:
    """One governor evaluation, ready to serialize as hook output."""

    verdict: Verdict
    reason: str
    loop_state: str
    events: tuple[str, ...] = ()

    def hook_output(self) -> dict[str, Any] | None:
        if self.verdict is Verdict.ALLOW:
            return None
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": self.reason}}


# ---------------------------------------------------------------------------
# Planning lease
# ---------------------------------------------------------------------------
@dataclass
class PlanningLease:
    """Bounded planning state (§2.5.1).

    The lease is granted implicitly on first observation of a planning state
    and consumed per gated tool call.  It renews on packet creation (the
    escape hatch the deny message names).
    """

    granted_ts: float
    turns_used: int = 0
    new_tokens_used: int = 0

    @classmethod
    def load(cls, paths: LoopPaths) -> "PlanningLease":
        doc = read_json(paths.governor_dir / "planning_lease.json", None)
        if isinstance(doc, dict):
            try:
                return cls(granted_ts=float(doc["granted_ts"]),
                           turns_used=int(doc.get("turns_used", 0)),
                           new_tokens_used=int(doc.get("new_tokens_used", 0)))
            except (KeyError, TypeError, ValueError):
                pass
        return cls(granted_ts=utc_now())

    def save(self, paths: LoopPaths) -> None:
        atomic_write_json(paths.governor_dir / "planning_lease.json",
                          {"granted_ts": self.granted_ts,
                           "turns_used": self.turns_used,
                           "new_tokens_used": self.new_tokens_used})

    def exhausted(self, max_turns: int, max_new_tokens: int) -> bool:
        return (self.turns_used >= max_turns
                or self.new_tokens_used >= max_new_tokens)

    def consume_turn(self, paths: LoopPaths, new_tokens: int = 0) -> None:
        self.turns_used += 1
        self.new_tokens_used += max(0, int(new_tokens))
        self.save(paths)

    @staticmethod
    def renew(paths: LoopPaths, reason: str) -> None:
        """Reset the lease — called on packet-creation events."""
        atomic_write_json(paths.governor_dir / "planning_lease.json",
                          {"granted_ts": utc_now(), "turns_used": 0,
                           "new_tokens_used": 0, "renewed_for": reason})


# ---------------------------------------------------------------------------
# Hysteresis controller (share-band state, persisted)
# ---------------------------------------------------------------------------
class HysteresisController:
    """Two-threshold, two-sample hysteresis over the Sol share signal.

    Enter HIGH at share > ``enter_high`` for ``enter_samples`` consecutive
    samples; leave at share < ``leave_high`` for ``leave_samples`` samples.
    State persists in ``data/governor/hysteresis.json`` so hook invocations
    (separate processes) share it — this is what prevents oscillation between
    modes (§2.4.5 / R4).
    """

    def __init__(self, paths: LoopPaths, params: Mapping[str, float]) -> None:
        self.paths = paths
        self.state_path = paths.governor_dir / "hysteresis.json"
        self.enter_high = float(params.get("enter_high", 0.25))
        self.enter_samples = int(params.get("enter_samples", 2))
        self.leave_high = float(params.get("leave_high", 0.22))
        self.leave_samples = int(params.get("leave_samples", 2))

    def observe(self, share: float, sample_id: str) -> str:
        """Feed one share sample; returns the resulting band (NORMAL|HIGH).

        ``sample_id`` deduplicates repeated observations of the same meter
        report so a chatty hook cannot fast-forward the sample counters.
        """
        with file_lock(self.state_path.with_suffix(".lock")):
            doc = read_json(self.state_path, {}) or {}
            if doc.get("last_sample_id") == sample_id:
                return str(doc.get("band", "NORMAL"))
            band = str(doc.get("band", "NORMAL"))
            above = int(doc.get("above_count", 0))
            below = int(doc.get("below_count", 0))
            if band == "NORMAL":
                above = above + 1 if share > self.enter_high else 0
                if above >= self.enter_samples:
                    band, above, below = "HIGH", 0, 0
            else:
                below = below + 1 if share < self.leave_high else 0
                if below >= self.leave_samples:
                    band, above, below = "NORMAL", 0, 0
            atomic_write_json(self.state_path,
                              {"band": band, "above_count": above,
                               "below_count": below, "last_share": share,
                               "last_sample_id": sample_id, "ts": utc_now()})
            return band

    def band(self) -> str:
        doc = read_json(self.state_path, {}) or {}
        return str(doc.get("band", "NORMAL"))


# ---------------------------------------------------------------------------
# Attested loop-state CLI
# ---------------------------------------------------------------------------
def loop_state_set(state: str, reason: str,
                   paths: LoopPaths | None = None) -> dict[str, Any]:
    """Set an explicit loop state THROUGH THE HARNESS (the only honoured
    channel; §2.5.2).  Appends a signed ``governor.state_set`` attestation
    with a semantic idempotency key and writes the ledger key to match."""
    paths = paths or LoopPaths.resolve()
    if state not in ALLOWED_STATES | {"execution"}:
        raise ValueError(f"unknown loop state {state!r}")
    key = idem_key("state_set", state, reason, str(int(utc_now())))
    record = {"ts": utc_now(), "event": "governor.state_set", "state": state,
              "reason": reason, "idem_key": key}
    append_ndjson(paths.governor_dir / "state_attestations.ndjsonl", record)
    with file_lock(paths.ledger.with_suffix(".lock")):
        led = read_json(paths.ledger, {"packets": {}}) or {"packets": {}}
        led["loop_state"] = state
        led["loop_state_attestation"] = key
        atomic_write_json(paths.ledger, led)
    return record


def _attested_state(paths: LoopPaths, led: Mapping[str, Any]) -> str | None:
    """Return the explicit loop state only if its attestation checks out."""
    explicit = led.get("loop_state")
    attestation = led.get("loop_state_attestation")
    if not isinstance(explicit, str) or not explicit:
        return None
    if isinstance(attestation, str) and attestation:
        for rec in _iter_attestations(paths):
            if rec.get("idem_key") == attestation and rec.get("state") == explicit:
                return explicit
    # Bare key: ignored and flagged (fail-visible).
    append_ndjson(paths.events,
                  {"ts": utc_now(), "packet_id": "-",
                   "event": "governor.state_key_unattested",
                   "detail": {"claimed_state": explicit}},
                  lock_path=paths.events_lock)
    return None


def _iter_attestations(paths: LoopPaths):
    path = paths.governor_dir / "state_attestations.ndjsonl"
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


# ---------------------------------------------------------------------------
# Governor
# ---------------------------------------------------------------------------
class RootTurnGovernor:
    """Fail-closed PreToolUse governor for the root (Sol) session."""

    def __init__(self, paths: LoopPaths | None = None,
                 policy: OrchestrationPolicy | None = None) -> None:
        self.paths = paths or LoopPaths.resolve()
        self.policy = policy or OrchestrationPolicy.load(self.paths)
        self.hysteresis = HysteresisController(self.paths,
                                               self.policy.hysteresis())

    # -- loop state ----------------------------------------------------------
    def loop_state(self) -> tuple[str, bool]:
        """Return ``(state, trusted)``.  ``trusted=False`` means the ledger
        was unreadable — the caller must fail closed for non-planning."""
        led = read_json(self.paths.ledger, None)
        if led is None:
            if self.paths.ledger.exists():
                return "unknown", False       # present but unreadable: fail closed
            return "planning", True           # genuinely fresh root: lease governs
        attested = _attested_state(self.paths, led)
        if attested:
            return attested, True
        states = [p.get("state") for p in led.get("packets", {}).values()]
        if not states:
            return "planning", True
        if any(s in ADJUDICATION_STATES for s in states):
            return "adjudication", True
        if all(s in TERMINAL_STATES for s in states):
            return "release_finalize", True
        # EXPAND_K3 / L2_VERIFY / L2_RANK land here: execution (§7).
        return "execution", True

    # -- meter --------------------------------------------------------------------
    def meter_status(self) -> tuple[float | None, str]:
        """Return ``(sol_5h_effective_share, status)`` from the meter report.

        ``status`` ∈ {OK, STALE, MISSING, MALFORMED, INSUFFICIENT_DATA}.
        STALE/MISSING/MALFORMED are fail-closed inputs for non-planning turns.
        """
        path = self.paths.meter_report
        if not path.exists():
            return None, "MISSING"
        report = read_json(path, None)
        if not isinstance(report, dict):
            return None, "MALFORMED"
        stale_after = self.policy.meter_stale_after_s()
        generated = report.get("generated_at", report.get("generated_ts"))
        if generated is None:
            # A report with no timestamp cannot prove freshness.
            return None, "MALFORMED"
        try:
            if utc_now() - float(generated) > stale_after:
                return None, "STALE"
        except (TypeError, ValueError):
            return None, "MALFORMED"
        windows = report.get("windows", {})
        w5h = windows.get("rolling_5h") if isinstance(windows, dict) else None
        if not isinstance(w5h, dict):
            return None, "MALFORMED"
        if w5h.get("status") == "INSUFFICIENT_DATA":
            return None, "INSUFFICIENT_DATA"
        share = w5h.get("sol_share_effective", w5h.get("share_effective"))
        try:
            return float(share), "OK"
        except (TypeError, ValueError):
            return None, "MALFORMED"

    # -- budget integration -----------------------------------------------------------
    def budget_state(self) -> str:
        """Budget controller state for the root task (NORMAL when absent)."""
        doc = read_json(self.paths.budget_dir / "active.json", None)
        if isinstance(doc, dict):
            state = doc.get("state")
            if state in ("NORMAL", "THROTTLE", "DEGRADE", "BREAK"):
                return str(state)
        return "NORMAL"

    # -- evaluation ----------------------------------------------------------------------
    def evaluate(self, tool_name: str, model: str | None = None,
                 estimated_new_tokens: int = 0) -> GovernorDecision:
        """Evaluate one root-session tool call.  Deterministic, zero-model."""
        tool = (tool_name or "").lower()
        if not tool.startswith(GATED_TOOLS):
            return GovernorDecision(Verdict.ALLOW, "tool not gated", "-")

        # Model-family screen: dispatch-target families are never gated; an
        # unknown/absent model cannot bypass by omitting the field.
        family = self._model_family(model)
        if family in ("v4", "k3"):
            return GovernorDecision(Verdict.ALLOW, "dispatch-target family", "-")
        if family != "sol":
            return self._deny(
                "PreToolUse payload.model is missing or unknown (%r); a gated "
                "operation cannot bypass the Sol policy by omitting model"
                % (model,), "-", ("governor.fail_closed",))

        state, trusted = self.loop_state()

        # Break-glass: observable, audited, never silent.
        override = os.environ.get(BREAK_GLASS_ENV)
        if override:
            self._event("governor.break_glass",
                        {"tool": tool, "reason": override, "loop_state": state})
            return GovernorDecision(Verdict.ALLOW,
                                    f"break-glass override: {override}", state,
                                    ("governor.break_glass",))

        # Fail-closed on unreadable ledger for anything but a fresh root.
        if not trusted:
            self._event("governor.fail_closed",
                        {"tool": tool, "why": "ledger_unreadable"})
            return self._deny(
                "progress ledger unreadable — failing closed. Gated tools deny "
                "until the ledger is restored; packet/dispatch tools remain "
                "available (decompose and dispatch).", state,
                ("governor.fail_closed",))

        # Budget ladder (Tier 2/3 integration): DEGRADE/BREAK deny gated tools.
        budget = self.budget_state()
        if budget in ("DEGRADE", "BREAK"):
            self._event("governor.fail_closed",
                        {"tool": tool, "why": f"budget_{budget.lower()}"})
            return self._deny(
                "task budget state is %s: only adjudication/dispatch tools are "
                "allowed. Delegate: dispatch.py --role worker --packet <id> "
                "(or open a bounded adjudication packet)." % budget, state,
                ("governor.fail_closed",))

        # Planning lease (the P0-5.2 fix): bounded, renewable exemption.
        if state == "planning":
            lease = PlanningLease.load(self.paths)
            max_turns = self.policy.planning_max_turns()
            max_tokens = self.policy.planning_max_new_tokens()
            if lease.exhausted(max_turns, max_tokens):
                self._event("governor.lease_denied",
                            {"tool": tool, "turns_used": lease.turns_used,
                             "new_tokens_used": lease.new_tokens_used})
                return self._deny(
                    "decompose or dispatch: planning lease exhausted "
                    "(%d/%d turns, %d/%d new tokens) — emit packets/*.json + "
                    "dag.json or open an adjudication packet; packet creation "
                    "renews the lease."
                    % (lease.turns_used, max_turns,
                       lease.new_tokens_used, max_tokens), state)
            lease.consume_turn(self.paths, estimated_new_tokens)
            return GovernorDecision(Verdict.ALLOW, "planning lease", state)

        if state in ("adjudication", "release_finalize"):
            return GovernorDecision(Verdict.ALLOW, f"{state} state", state)

        # Execution (incl. K3 work states): meter-driven share enforcement.
        share, status = self.meter_status()
        if status in ("MISSING", "MALFORMED", "STALE"):
            self._event("governor.fail_closed",
                        {"tool": tool, "why": f"meter_{status.lower()}"})
            return self._deny(
                "token meter is %s — failing closed for non-planning Sol "
                "turns. Delegate: dispatch as a packet, or restore the meter."
                % status, state, ("governor.fail_closed",))
        if status == "OK" and share is not None:
            band = self.hysteresis.observe(
                share, sample_id=f"{self.paths.meter_report}:{share:.6f}")
            if band == "HIGH":
                self._event("governor.share_denied",
                            {"tool": tool, "share_5h": share})
                return self._deny(
                    "Sol 5h effective share %.2f%% is in the HIGH band — "
                    "delegate: route to k3 verifier "
                    "(dispatch.py --role verifier --packet <id>) or dispatch "
                    "the work as a V4 packet. Auto-delegation is the "
                    "reward-optimal action; this turn is refused."
                    % (share * 100), state)
        # INSUFFICIENT_DATA never actuates (denominator floor, §2.4.4).
        return self._deny(
            "LOOP state is %s: this operation belongs in an L0/L1 packet "
            "(AGENTS.md §2 — Sol rounds are for planning/adjudication). "
            "Dispatch it: dispatch.py --role worker --packet <id>." % state,
            state)

    # -- helpers ------------------------------------------------------------------------
    def _model_family(self, model: str | None) -> str | None:
        value = (model or "").strip()
        if not value:
            return None
        try:
            pins = {family: self.policy.model_pin(family)
                    for family in ("sol", "k3", "v4")}
        except PolicyError:
            return None
        for family, pin in pins.items():
            if value == pin:
                return family
        return None

    def _deny(self, reason: str, state: str,
              events: tuple[str, ...] = ()) -> GovernorDecision:
        return GovernorDecision(Verdict.DENY, reason, state, events)

    def _event(self, event: str, detail: dict[str, Any]) -> None:
        append_ndjson(self.paths.events,
                      {"ts": utc_now(), "packet_id": "-", "event": event,
                       "detail": detail},
                      lock_path=self.paths.events_lock)


# ---------------------------------------------------------------------------
# Hook / CLI entry
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """PreToolUse hook entry: one JSON payload on stdin -> deny JSON on
    stdout (allow = no output).  Also exposes the attested state CLI:
    ``root_turn_governor.py set-state <state> --reason <why>`` and
    ``renew-lease``."""
    import argparse

    ap = argparse.ArgumentParser(description="Root-Turn Governor")
    ap.add_argument("cmd", nargs="?", default="hook",
                    choices=["hook", "set-state", "renew-lease", "status"])
    ap.add_argument("state", nargs="?", help="loop state for set-state")
    ap.add_argument("--reason", default="", help="attestation reason")
    args = ap.parse_args(argv)
    paths = LoopPaths.resolve()

    if args.cmd == "set-state":
        if not args.state or not args.reason:
            print("set-state requires <state> and --reason", file=sys.stderr)
            return 2
        record = loop_state_set(args.state, args.reason, paths)
        print(json.dumps(record))
        return 0
    if args.cmd == "renew-lease":
        PlanningLease.renew(paths, args.reason or "manual")
        print(json.dumps({"renewed": True}))
        return 0
    if args.cmd == "status":
        try:
            gov = RootTurnGovernor(paths)
        except PolicyError as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 1
        state, trusted = gov.loop_state()
        share, meter = gov.meter_status()
        print(json.dumps({"loop_state": state, "ledger_trusted": trusted,
                          "sol_5h_share": share, "meter_status": meter,
                          "hysteresis_band": gov.hysteresis.band(),
                          "budget_state": gov.budget_state()}))
        return 0

    # hook mode ---------------------------------------------------------------
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # no payload, nothing to judge
    try:
        gov = RootTurnGovernor(paths)
    except PolicyError as exc:
        # Policy unreadable: fail CLOSED for gated tools (the inversion of the
        # v1 fail-open posture), fail-visible via stderr + deny reason.
        tool = (payload.get("tool_name") or "").lower()
        if tool.startswith(GATED_TOOLS):
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason":
                    f"governor policy unreadable ({exc}) — failing closed"}}))
        return 0
    decision = gov.evaluate(payload.get("tool_name") or "",
                            payload.get("model"),
                            int(payload.get("estimated_new_tokens", 0) or 0))
    output = decision.hook_output()
    if output is not None:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
