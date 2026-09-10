#!/usr/bin/env python3
"""agent_router.py — Three-mode routing switch + deterministic-first router.

Replaces the boolean cold-start collapse at ``trigger_eval.py:290-291`` (P0-1)
with a three-mode switch and a table-driven router (architecture §2.1 / §5.1):

* **cold_start** — legacy behavior, byte-identical: every non-``direct_l3``
  action upgrades to ``direct_l3`` (safe default; rollback target).
* **shadow** — actions are *executed* as cold_start but the as-if-layered
  decision is logged to ``data/router/shadow_log.ndjsonl``, accumulating the
  calibration corpus for the confidence calibrator.
* **layered** — K3-first dispatch with calibrated-confidence escalation
  (RouteLLM/FrugalGPT cascade pattern): the trigger-table verdict stands;
  ``send_l2`` routes to the K3 verifier queue; escalation to Sol requires a
  reason code from a closed enum — free-form "Sol will just handle it" is
  unrepresentable.

Mode switching is **mechanical, never manual**: requesting ``layered`` in
policy only takes effect when all six gate guards pass
(:func:`check_layered_gate_guards`); otherwise the effective mode is
downgraded to ``shadow`` (guards failing => observe, never actuate) and the
downgrade is ledgered fail-visible.

Escalation confidence is **calibrated from mechanical signals**, never
self-reported model confidence (poorly calibrated; architecture §4 E6): the
calibrator bins shadow-corpus outcomes per signal fingerprint and maps new
packets onto observed escalation frequencies.

Model pins are read from ``[models]`` in ``orchestration_policy_v2.toml`` — no
model id appears in this file (task constraint).
"""
from __future__ import annotations

import enum
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Mapping

try:
    from orchestration_common import (LoopPaths, ModelPinError, OrchestrationPolicy,
                                      PolicyError, append_ndjson, atomic_write_json,
                                      get_logger, idem_key, layered_authorization,
                                      read_json, utc_now)
except ImportError:  # pragma: no cover - direct CLI execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from orchestration_common import (LoopPaths, ModelPinError, OrchestrationPolicy,
                                      PolicyError, append_ndjson, atomic_write_json,
                                      get_logger, idem_key, layered_authorization,
                                      read_json, utc_now)

__all__ = [
    "RoutingMode",
    "Route",
    "RouteReason",
    "GateGuard",
    "GateGuardReport",
    "RouteDecision",
    "ConfidenceCalibrator",
    "AgentRouter",
    "check_layered_gate_guards",
]

log = get_logger("loop.agent_router")


class RoutingMode(str, enum.Enum):
    """The three routing modes (§2.1)."""

    COLD_START = "cold_start"
    SHADOW = "shadow"
    LAYERED = "layered"


class Route(str, enum.Enum):
    """Closed route enum (§5.1).  Routing produces exactly one of these."""

    V4_DIRECT = "v4_direct"
    K3_EXPAND = "k3_expand"
    K3_VERIFY = "k3_verify"
    K3_RANK = "k3_rank"
    SOL_ADJUDICATE = "sol_adjudicate"
    L4_HUMAN = "l4_human"
    MERGE_QUEUE = "merge_queue"          # mechanical acceptance, zero-model


class RouteReason(str, enum.Enum):
    """Closed reason-code enum.  Routing to Sol REQUIRES one of the
    ``SOL_*`` codes; a free-form reason is unrepresentable by construction."""

    TABLE_PASS = "table_pass"
    TABLE_ANNOTATED_PASS = "table_annotated_pass"
    TABLE_SEND_L2 = "table_send_l2"
    SAMPLED_VERIFICATION = "sampled_verification"
    PLAN_EXPANSION_CRITERIA = "plan_expansion_criteria"
    K3_SUITED_CLASS = "k3_suited_class"
    RANK_REQUESTED = "rank_requested"
    COLD_START_UPGRADE = "cold_start_upgrade"
    SOL_HIGH_RISK = "sol_high_risk"
    SOL_OFF_TABLE = "sol_off_table"
    SOL_L2_ESCALATION = "sol_l2_escalation"
    SOL_SCHEMA_INVALID = "sol_schema_invalid"
    SOL_NEEDS_DECISION = "sol_needs_decision"
    SOL_RETRY_EXHAUSTED = "sol_retry_exhausted"
    SOL_BUDGET_REFUSED_ALTERNATIVE = "sol_budget_refused_alternative"
    L4_CAP_EXCEEDED = "l4_cap_exceeded"


#: Reasons that legitimise a Sol route.  Anything else routed to Sol is a bug.
SOL_REASONS: Final[frozenset[RouteReason]] = frozenset({
    RouteReason.SOL_HIGH_RISK, RouteReason.SOL_OFF_TABLE,
    RouteReason.SOL_L2_ESCALATION, RouteReason.SOL_SCHEMA_INVALID,
    RouteReason.SOL_NEEDS_DECISION, RouteReason.SOL_RETRY_EXHAUSTED,
    RouteReason.SOL_BUDGET_REFUSED_ALTERNATIVE, RouteReason.COLD_START_UPGRADE,
})

#: Packet classes that default K3-first in layered mode (§4 E6).
K3_SUITED_CLASSES: Final[frozenset[str]] = frozenset({
    "verification", "cross_file_reasoning_review", "plan_shaped_analysis",
    "long_context_single_doc_audit",
})


class GateGuard(str, enum.Enum):
    """The six mechanical gate guards required before ``layered`` engages."""

    CONSUMER_HEARTBEAT_FRESH = "consumer_heartbeat_fresh"
    EXACTLY_ONCE_CANARY = "exactly_once_canary"
    K3_VERIFIER_MODEL_PINNED = "k3_verifier_model_pinned"
    SHORT_RESULT_VALIDATOR_ENABLED = "short_result_validator_enabled"
    STATEMACHINE_SCHEMA_COMPATIBLE = "statemachine_schema_compatible"
    ROLLBACK_KEY_AVAILABLE = "rollback_key_available"
    LAYERED_AUTHORIZATION_CURRENT = "layered_authorization_current"


@dataclass(frozen=True)
class GateGuardReport:
    """Result of evaluating all six guards."""

    passed: bool
    results: dict[str, bool]
    details: dict[str, str]

    def failing(self) -> list[str]:
        return [name for name, ok in self.results.items() if not ok]


@dataclass(frozen=True)
class RouteDecision:
    """One ledgered routing decision."""

    packet_id: str
    route: Route
    reason: RouteReason
    mode: RoutingMode
    requested_mode: RoutingMode
    raw_action: str
    effective_action: str
    confidence: float | None = None
    features: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=utc_now)

    def to_record(self) -> dict[str, Any]:
        return {
            "ts": self.ts, "packet_id": self.packet_id,
            "route": self.route.value, "route_reason": self.reason.value,
            "mode": self.mode.value, "requested_mode": self.requested_mode.value,
            "raw_action": self.raw_action, "effective_action": self.effective_action,
            "confidence": self.confidence, "features": self.features,
        }


# ---------------------------------------------------------------------------
# Gate guards — the mechanical form of "no layered without a consumer".
# ---------------------------------------------------------------------------
def check_layered_gate_guards(paths: LoopPaths,
                              policy: OrchestrationPolicy) -> GateGuardReport:
    """Evaluate ALL six layered-mode gate guards.

    Every guard is a disk/config observation — zero-model, reproducible.
    Failing ANY guard blocks the mode flip (fail-closed) and the report names
    exactly which guard failed (fail-visible).
    """
    now = utc_now()
    results: dict[str, bool] = {}
    details: dict[str, str] = {}

    # 1. consumer heartbeat exists and is fresh (§2.2 heartbeat file).
    hb_max_age = float(policy.value(
        "l2_queue", "consumer_heartbeat_max_age_s", 300))
    hb = read_json(paths.l2_heartbeat, None)
    try:
        hb_age = now - float(hb["ts"]) if isinstance(hb, dict) else None
    except (KeyError, TypeError, ValueError):
        hb_age = None
    ok = hb_age is not None and hb_age <= hb_max_age
    results[GateGuard.CONSUMER_HEARTBEAT_FRESH.value] = ok
    details[GateGuard.CONSUMER_HEARTBEAT_FRESH.value] = (
        f"age={hb_age:.1f}s (max {hb_max_age}s)" if hb_age is not None
        else f"heartbeat missing: {paths.l2_heartbeat}")

    # 2. exactly-once canary passed (stamp written by the canary test run:
    #    N send_l2 records drained twice => exactly N claims).
    canary = read_json(paths.l2_queue_dir / "exactly_once_canary.json", None)
    ok = isinstance(canary, dict) and canary.get("status") == "PASS"
    results[GateGuard.EXACTLY_ONCE_CANARY.value] = ok
    details[GateGuard.EXACTLY_ONCE_CANARY.value] = (
        f"status={canary.get('status')!r}" if isinstance(canary, dict)
        else "canary stamp missing")

    # 3. K3 verifier model is pinned in policy AND agrees with the role TOML.
    try:
        pin = policy.model_pin("k3")
        toml_model = _verifier_toml_model(paths)
        ok = toml_model is None or toml_model == pin
        results[GateGuard.K3_VERIFIER_MODEL_PINNED.value] = ok
        details[GateGuard.K3_VERIFIER_MODEL_PINNED.value] = (
            f"policy pin={pin!r}, verifier.toml={toml_model!r}")
    except ModelPinError as exc:
        results[GateGuard.K3_VERIFIER_MODEL_PINNED.value] = False
        details[GateGuard.K3_VERIFIER_MODEL_PINNED.value] = str(exc)

    # 4. short-result validator is enabled (publish-point enforcement flag).
    srv = read_json(paths.data / "validators" / "short_result_validator.json", None)
    ok = isinstance(srv, dict) and bool(srv.get("enabled"))
    results[GateGuard.SHORT_RESULT_VALIDATOR_ENABLED.value] = ok
    details[GateGuard.SHORT_RESULT_VALIDATOR_ENABLED.value] = (
        f"enabled={srv.get('enabled')}" if isinstance(srv, dict)
        else "validator marker missing")

    # 5. state-machine schema is compatible (t27–t38 must exist on-table).
    required = str(policy.value("gate_guard", "statemachine_schema_required",
                                "codex-loop-statemachine/v2"))
    ledger = read_json(paths.ledger, {}) or {}
    schema = ledger.get("schema")
    ok = schema == required
    if not ok:
        # A fresh ledger has no schema yet — accept when the v2 module is
        # importable and validates (the table itself is the compatibility).
        try:
            import statemachine_v2  # noqa: F401  (same directory)
            ok = statemachine_v2.SCHEMA == required
            schema = statemachine_v2.SCHEMA
        except Exception:  # pragma: no cover - import environment specific
            ok = False
    results[GateGuard.STATEMACHINE_SCHEMA_COMPATIBLE.value] = ok
    details[GateGuard.STATEMACHINE_SCHEMA_COMPATIBLE.value] = (
        f"required={required!r}, found={schema!r}")

    # 6. rollback key is available (single-key revert to cold_start).
    rollback = policy.value("routing", "rollback_mode")
    ok = rollback == "cold_start"
    results[GateGuard.ROLLBACK_KEY_AVAILABLE.value] = ok
    details[GateGuard.ROLLBACK_KEY_AVAILABLE.value] = f"rollback_mode={rollback!r}"

    if policy.routing_mode() == "layered":
        ok, detail = layered_authorization(paths.root, now=now)
    else:
        ok, detail = True, "not required outside requested layered mode"
    results[GateGuard.LAYERED_AUTHORIZATION_CURRENT.value] = ok
    details[GateGuard.LAYERED_AUTHORIZATION_CURRENT.value] = detail

    return GateGuardReport(passed=all(results.values()),
                           results=results, details=details)


def _verifier_toml_model(paths: LoopPaths) -> str | None:
    """Best-effort read of ``agents/verifier.toml`` model for pin agreement."""
    import tomllib
    toml_path = paths.root / "agents" / "verifier.toml"
    if not toml_path.exists():
        return None
    try:
        with toml_path.open("rb") as handle:
            return tomllib.load(handle).get("model")
    except (OSError, tomllib.TOMLDecodeError):
        return None


# ---------------------------------------------------------------------------
# Confidence calibration — mechanical-signal bins over the shadow corpus.
# ---------------------------------------------------------------------------
class ConfidenceCalibrator:
    """Calibrated escalation confidence from mechanical signals.

    Never consumes self-reported model confidence.  The calibrator maintains
    per-fingerprint outcome counts (``data/router/calibration.json``): a
    fingerprint is the sorted tuple of trigger rules hit plus coarse signal
    buckets.  ``p_escalate(fingerprint)`` is the Laplace-smoothed frequency
    with which packets carrying that fingerprint historically required L3+
    escalation.  With no history the calibrator returns the conservative
    prior (escalate), which preserves cold-start safety.
    """

    #: Laplace smoothing pseudo-counts (conservative prior: escalate).
    PRIOR_ESCALATE: Final[float] = 1.0
    PRIOR_TOTAL: Final[float] = 2.0

    def __init__(self, paths: LoopPaths) -> None:
        self.paths = paths
        self.path = paths.router_dir / "calibration.json"
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(signals: Mapping[str, Any], rules_hit: list[str]) -> str:
        """Deterministic feature fingerprint from mechanical signals only."""
        exit_codes = signals.get("exit_codes") or []
        buckets = (
            "rc0" if exit_codes == [0] else
            "rcmix" if any(c == 0 for c in exit_codes) else "rcfail",
            f"retry{min(int(signals.get('retry_count', 0) or 0), 3)}",
            "diff0" if not signals.get("diff_lines") else "diff+",
        )
        return "|".join(sorted(set(rules_hit)) + list(buckets))

    def p_escalate(self, fingerprint: str) -> float:
        """Calibrated probability that this fingerprint needs L3+ review."""
        doc = read_json(self.path, {}) or {}
        entry = doc.get(fingerprint, {})
        escalated = float(entry.get("escalated", 0)) + self.PRIOR_ESCALATE
        total = float(entry.get("total", 0)) + self.PRIOR_TOTAL
        return escalated / total

    def record_outcome(self, fingerprint: str, escalated: bool) -> None:
        """Feed a downstream outcome back into the calibration corpus."""
        with self._lock:
            doc = read_json(self.path, {}) or {}
            entry = doc.setdefault(fingerprint, {"escalated": 0, "total": 0})
            entry["total"] = int(entry.get("total", 0)) + 1
            if escalated:
                entry["escalated"] = int(entry.get("escalated", 0)) + 1
            atomic_write_json(self.path, doc)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class AgentRouter:
    """Deterministic-first router with mode-aware execution (§5.1).

    High-risk and L3-cap rails live ABOVE this router in ``trigger_eval_v2``
    and are never overridable here — the router only sees actions those rails
    already blessed.
    """

    #: Escalation threshold on calibrated confidence: fingerprints whose
    #: historical escalation frequency exceeds this route conservatively.
    ESCALATE_THRESHOLD: Final[float] = 0.5

    def __init__(self, paths: LoopPaths | None = None,
                 policy: OrchestrationPolicy | None = None) -> None:
        self.paths = paths or LoopPaths.resolve()
        self.policy = policy or OrchestrationPolicy.load(self.paths)
        self.calibrator = ConfidenceCalibrator(self.paths)
        self._lock = threading.Lock()

    # -- mode resolution ------------------------------------------------------
    def effective_mode(self) -> tuple[RoutingMode, GateGuardReport | None]:
        """Resolve the effective mode: requested policy mode, downgraded to
        ``shadow`` mechanically when any layered gate guard fails."""
        requested = RoutingMode(self.policy.routing_mode())
        if requested is not RoutingMode.LAYERED:
            return requested, None
        report = check_layered_gate_guards(self.paths, self.policy)
        if report.passed:
            return RoutingMode.LAYERED, report
        log.warning("layered mode requested but gate guards failed: %s — "
                    "downgrading to shadow (fail-closed)", report.failing())
        append_ndjson(self.paths.router_dir / "mode_downgrades.ndjsonl",
                      {"ts": utc_now(), "requested": requested.value,
                       "effective": RoutingMode.SHADOW.value,
                       "failing_guards": report.failing(),
                       "details": report.details})
        return RoutingMode.SHADOW, report

    # -- model pins --------------------------------------------------------------
    def model_pin(self, role_family: str) -> str:
        """Model pin for a role family from config (never hardcoded)."""
        return self.policy.model_pin(role_family)

    def enforce_model_pin(self, role_family: str, proposed_model: str) -> str:
        """Refuse a dispatch whose model diverges from the configured pin."""
        pin = self.model_pin(role_family)
        if proposed_model != pin:
            raise ModelPinError(
                f"model pin violation for {role_family!r}: proposed "
                f"{proposed_model!r} but policy pins {pin!r}")
        return pin

    # -- routing ------------------------------------------------------------------
    def route_action(self, packet_id: str, action: str,
                     signals: Mapping[str, Any] | None = None,
                     rules_hit: list[str] | None = None,
                     packet_meta: Mapping[str, Any] | None = None,
                     high_risk: bool = False) -> RouteDecision:
        """Map one trigger-table action to a ledgered :class:`RouteDecision`.

        ``action`` is the raw trigger verdict (post high-risk/L3-cap rails).
        The effective action executed depends on the mode:

        * cold_start: every non-``direct_l3``/``direct_l4`` action upgrades;
        * shadow: same execution as cold_start, layered decision logged;
        * layered: table verdict stands and is routed on the layered graph.
        """
        signals = signals or {}
        rules_hit = rules_hit or []
        packet_meta = packet_meta or {}
        requested = RoutingMode(self.policy.routing_mode())
        mode, _guards = self.effective_mode()
        fingerprint = ConfidenceCalibrator.fingerprint(signals, rules_hit)
        confidence = self.calibrator.p_escalate(fingerprint)

        layered_decision = self._layered_route(
            packet_id, action, confidence, packet_meta, high_risk)

        if mode is RoutingMode.LAYERED:
            decision = RouteDecision(
                packet_id=packet_id, route=layered_decision[0],
                reason=layered_decision[1], mode=mode, requested_mode=requested,
                raw_action=action, effective_action=layered_decision[2],
                confidence=confidence,
                features={"fingerprint": fingerprint, "rules_hit": rules_hit})
        else:
            # cold_start / shadow: legacy execution (everything non-l3/l4
            # upgrades to direct_l3 => Sol).  Shadow additionally logs the
            # counterfactual layered decision as the calibration corpus.
            effective = action if action in ("direct_l3", "direct_l4") else "direct_l3"
            reason = (RouteReason.SOL_HIGH_RISK if high_risk
                      else RouteReason.COLD_START_UPGRADE
                      if effective != action else RouteReason.SOL_OFF_TABLE)
            route = (Route.L4_HUMAN if effective == "direct_l4"
                     else Route.SOL_ADJUDICATE)
            decision = RouteDecision(
                packet_id=packet_id, route=route, reason=reason, mode=mode,
                requested_mode=requested, raw_action=action,
                effective_action=effective, confidence=confidence,
                features={"fingerprint": fingerprint, "rules_hit": rules_hit})
            if mode is RoutingMode.SHADOW:
                append_ndjson(self.paths.router_dir / "shadow_log.ndjsonl",
                              {**decision.to_record(),
                               "shadow_route": layered_decision[0].value,
                               "shadow_reason": layered_decision[1].value,
                               "shadow_action": layered_decision[2]})

        self._ledger(decision)
        return decision

    def _layered_route(self, packet_id: str, action: str, confidence: float,
                       packet_meta: Mapping[str, Any],
                       high_risk: bool) -> tuple[Route, RouteReason, str]:
        """Pure layered routing function (no IO): action -> (route, reason,
        effective_action)."""
        if high_risk or action == "direct_l3":
            return Route.SOL_ADJUDICATE, (
                RouteReason.SOL_HIGH_RISK if high_risk
                else RouteReason.SOL_L2_ESCALATION), "direct_l3"
        if action == "direct_l4":
            return Route.L4_HUMAN, RouteReason.L4_CAP_EXCEEDED, "direct_l4"
        if action == "send_l2":
            return Route.K3_VERIFY, RouteReason.TABLE_SEND_L2, "send_l2"
        if action in ("pass", "annotated_pass"):
            # K3-first cascade for K3-suited classes (E6); calibrated
            # low-confidence fingerprints get a verification pass instead of
            # a silent merge.  Escalation triggers stay mechanical.
            pkt_class = str(packet_meta.get("class", ""))
            if pkt_class in K3_SUITED_CLASSES:
                return Route.K3_VERIFY, RouteReason.K3_SUITED_CLASS, "send_l2"
            if confidence >= self.ESCALATE_THRESHOLD:
                return Route.K3_VERIFY, RouteReason.SAMPLED_VERIFICATION, "send_l2"
            reason = (RouteReason.TABLE_PASS if action == "pass"
                      else RouteReason.TABLE_ANNOTATED_PASS)
            return Route.MERGE_QUEUE, reason, action
        if action == "spawn_duty_officer":
            return Route.V4_DIRECT, RouteReason.TABLE_PASS, action
        # Unknown action: fail toward Sol, ledgered with a closed reason code.
        return Route.SOL_ADJUDICATE, RouteReason.SOL_OFF_TABLE, "direct_l3"

    # -- side effects -----------------------------------------------------------------
    def emit_l2_request(self, packet_id: str, run_id: str, attempt: int,
                        decision: RouteDecision) -> str:
        """Append an L2 request record with a semantic idempotency key
        (consumed by the ``l2_consumer``; §2.1/§2.2)."""
        key = idem_key("l2req", packet_id, run_id, str(attempt))
        now = utc_now()
        append_ndjson(self.paths.l2_pending,
                      {"ts": now, "created_ts": now,
                       "reason": decision.reason.value,
                       "packet_id": packet_id, "run_id": run_id,
                       "attempt": attempt, "idem_key": key,
                       "route_reason": decision.reason.value})
        return key

    def _ledger(self, decision: RouteDecision) -> None:
        append_ndjson(self.paths.router_dir / "route_ledger.ndjsonl",
                      decision.to_record())


# ---------------------------------------------------------------------------
# CLI (guard check / one-shot routing for harness scripts)
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="LOOP-F2 agent router")
    ap.add_argument("cmd", choices=["mode", "guards", "route"])
    ap.add_argument("--packet", default="?")
    ap.add_argument("--action", default="pass")
    ap.add_argument("--signals", help="path to signals JSON")
    ap.add_argument("--high-risk", action="store_true")
    args = ap.parse_args(argv)
    try:
        router = AgentRouter()
    except PolicyError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    if args.cmd == "mode":
        mode, _ = router.effective_mode()
        print(json.dumps({"requested": router.policy.routing_mode(),
                          "effective": mode.value}))
        return 0
    if args.cmd == "guards":
        report = check_layered_gate_guards(router.paths, router.policy)
        print(json.dumps({"passed": report.passed, "results": report.results,
                          "details": report.details}, indent=2))
        return 0 if report.passed else 1
    signals = read_json(args.signals, {}) if args.signals else {}
    decision = router.route_action(args.packet, args.action,
                                   signals=signals, high_risk=args.high_risk)
    print(json.dumps(decision.to_record()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
