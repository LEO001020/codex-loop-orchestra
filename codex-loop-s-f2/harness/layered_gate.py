#!/usr/bin/env python3
"""layered_gate.py — mechanical gate guards for cold_start → layered flips.

Implements the "no passthrough flip without a consumer" rejection gate as
code (``phase2_architecture_design.md`` §2.1 integration note + migration
strategy): the central trap of the shipped system — flipping routing open
while ``send_l2`` had no consumer — becomes *unrepresentable*. Setting
``routing.mode = "layered"`` is only legal through :meth:`LayeredGate.enable`,
which checks **all six conditions** and refuses on any failure:

1. **consumer heartbeat** — ``data/l2_queue/consumer_heartbeat.json`` exists
   and is fresher than ``consumer_heartbeat_max_age_s``;
2. **exactly-once canary** — :meth:`l2_consumer.L2Consumer.run_canary` passes
   live (3 sends → 3 dispatches; crash-after-claim reaped and re-dispatched
   exactly once; settle drain adds zero);
3. **K3 verifier model pinned** — policy ``[models].k3_model`` is a non-empty
   explicit pin (config-sourced, never a hardcoded default);
4. **short-result validator enabled** — policy ``[validator].enabled`` is
   true and the validator module imports;
5. **state-machine schema compatible** — the declared v2 transition manifest
   exists and carries every required transition (t27–t38) with
   ``SOL_ADJUDICATE`` absent from the terminal set;
6. **rollback key available** — policy ``[routing].rollback_mode`` names a
   valid mode (``cold_start``) so a one-key rollback always exists.

Every check result — pass and fail — is appended to
``data/governor/layered_gate.ndjsonl`` (V10 doctrine: a gate without a proof
of firing is prose).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field as _dc_field
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from l2_consumer import L2Consumer, load_policy  # noqa: E402

__all__ = [
    "ConditionResult",
    "GateResult",
    "LayeredGate",
]

LOG = logging.getLogger("layered_gate")

_REQUIRED_TRANSITIONS_DEFAULT: Final[tuple[str, ...]] = (
    "t27", "t28", "t29", "t30", "t31", "t32",
    "t33", "t34", "t35", "t36", "t37", "t38")


@dataclass(frozen=True)
class ConditionResult:
    """Outcome of one gate condition."""

    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class GateResult:
    """Structured outcome of the full gate check.

    ``allow`` is ``True`` only when **every** condition passed; ``failed``
    lists the names of failing conditions for actionable remediation.
    """

    allow: bool
    conditions: tuple[ConditionResult, ...]
    failed: tuple[str, ...] = _dc_field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {"allow": self.allow,
                "conditions": [c.to_dict() for c in self.conditions],
                "failed": list(self.failed)}


class LayeredGate:
    """The six-condition mechanical gate for enabling layered routing.

    Args:
        root: LOOP root directory.
        policy: parsed ``orchestration_policy_v2.toml``; loaded fail-closed
            when ``None``.
        canary_runner: injectable canary function (tests); defaults to the
            real :meth:`L2Consumer.run_canary`.
    """

    def __init__(self, root: Path | str,
                 policy: Mapping[str, Any] | None = None,
                 canary_runner: Callable[[], Any] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root).resolve()
        self.policy = (policy if policy is not None else
                       load_policy(self.root / "config" /
                                   "orchestration_policy_v2.toml"))
        self._canary = canary_runner or (
            lambda: L2Consumer.run_canary(dict(self.policy)))
        self._clock = clock
        guard = self.policy.get("gate_guard", {})
        self.log_path = self.root / Path(str(
            guard.get("log", "data/governor/layered_gate.ndjsonl")))
        self.required_transitions: tuple[str, ...] = tuple(
            guard.get("statemachine_required_transitions",
                      _REQUIRED_TRANSITIONS_DEFAULT))
        self.schema_file = self.root / Path(str(
            guard.get("statemachine_schema_file",
                      "config/statemachine_v2_transitions.json")))

    # -- individual conditions -------------------------------------------------

    def check_consumer_heartbeat(self) -> ConditionResult:
        """Condition 1: L2 consumer heartbeat exists and is fresh."""
        q = self.policy.get("l2_queue", {})
        max_age = float(q.get("consumer_heartbeat_max_age_s", 300))
        hb_path = (self.root / Path(str(q.get("dir", "data/l2_queue")))
                   / "consumer_heartbeat.json")
        try:
            hb = json.loads(hb_path.read_text(encoding="utf-8"))
            age = self._clock() - float(hb["ts"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return ConditionResult(
                "consumer_heartbeat", False,
                "heartbeat unreadable (%s) — run l2_consumer drain first" % exc)
        if age > max_age:
            return ConditionResult(
                "consumer_heartbeat", False,
                "heartbeat stale: %.0fs > %.0fs" % (age, max_age))
        return ConditionResult("consumer_heartbeat", True,
                               "fresh (%.0fs old)" % age)

    def check_exactly_once_canary(self) -> ConditionResult:
        """Condition 2: the live exactly-once canary passes."""
        try:
            result = self._canary()
        except Exception as exc:
            return ConditionResult("exactly_once_canary", False,
                                   "canary raised: %s" % exc)
        ok = bool(getattr(result, "ok", False))
        detail = str(getattr(result, "detail", result))
        return ConditionResult("exactly_once_canary", ok, detail)

    def check_k3_model_pinned(self) -> ConditionResult:
        """Condition 3: verifier model is an explicit config pin."""
        model = str(self.policy.get("models", {}).get("k3_model", "")).strip()
        if not model:
            return ConditionResult(
                "k3_model_pinned", False,
                "policy [models].k3_model is empty — pin the verifier model "
                "in config (never hardcode)")
        return ConditionResult("k3_model_pinned", True, "pinned: %s" % model)

    def check_validator_enabled(self) -> ConditionResult:
        """Condition 4: short-result validator enabled and importable."""
        if not bool(self.policy.get("validator", {}).get("enabled", False)):
            return ConditionResult("validator_enabled", False,
                                   "policy [validator].enabled is false")
        try:
            import short_result_validator  # noqa: F401, PLC0415
        except ImportError as exc:
            return ConditionResult("validator_enabled", False,
                                   "validator module missing: %s" % exc)
        return ConditionResult("validator_enabled", True, "enabled + importable")

    def check_statemachine_schema(self) -> ConditionResult:
        """Condition 5: v2 transition manifest is present and complete."""
        try:
            manifest = json.loads(self.schema_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return ConditionResult(
                "statemachine_schema", False,
                "transition manifest unreadable (%s): %s"
                % (self.schema_file, exc))
        transitions = manifest.get("transitions", {})
        missing = [t for t in self.required_transitions if t not in transitions]
        if missing:
            return ConditionResult(
                "statemachine_schema", False,
                "missing transitions: %s" % ", ".join(missing))
        terminal = manifest.get("terminal_states", [])
        if "SOL_ADJUDICATE" in terminal:
            return ConditionResult(
                "statemachine_schema", False,
                "SOL_ADJUDICATE still terminal — v2 requires it routable "
                "(design §2.3.4)")
        return ConditionResult(
            "statemachine_schema", True,
            "all %d required transitions declared" % len(self.required_transitions))

    def check_rollback_key(self) -> ConditionResult:
        """Condition 6: a one-key rollback path exists."""
        rollback = str(self.policy.get("routing", {}).get(
            "rollback_mode", "")).strip()
        if rollback != "cold_start":
            return ConditionResult(
                "rollback_key", False,
                "routing.rollback_mode must be 'cold_start' (got %r) — the "
                "single-key rollback is mandatory" % rollback)
        return ConditionResult("rollback_key", True,
                               "rollback_mode=cold_start available")

    # -- the gate ----------------------------------------------------------------

    def check_all(self) -> GateResult:
        """Run every condition; log each result; never short-circuit.

        All conditions run even after a failure so the log names *every*
        broken precondition in one pass (actionable remediation).
        """
        guard = self.policy.get("gate_guard", {})
        checks: list[tuple[str, Callable[[], ConditionResult]]] = [
            ("require_consumer_heartbeat", self.check_consumer_heartbeat),
            ("require_exactly_once_canary", self.check_exactly_once_canary),
            ("require_k3_model_pinned", self.check_k3_model_pinned),
            ("require_validator_enabled", self.check_validator_enabled),
            ("require_statemachine_schema", self.check_statemachine_schema),
            ("require_rollback_key", self.check_rollback_key),
        ]
        results: list[ConditionResult] = []
        for policy_key, fn in checks:
            if not bool(guard.get(policy_key, True)):
                results.append(ConditionResult(
                    fn.__name__.removeprefix("check_"), True,
                    "SKIPPED by policy %s=false (logged)" % policy_key))
                continue
            results.append(fn())
        failed = tuple(r.name for r in results if not r.ok)
        gate = GateResult(allow=not failed, conditions=tuple(results),
                          failed=failed)
        self._log(gate)
        return gate

    def enable(self) -> GateResult:
        """Attempt the cold_start → layered flip.

        Runs :meth:`check_all`; on full pass, records the authorization
        (``layered_mode_authorized``). This method never edits the policy
        file itself — the operator applies the one-line change; the smoke
        gate re-runs this check and REFUSES a layered config whose gate log
        lacks a fresh authorization (config-lint form of §2.1 AC4).
        """
        gate = self.check_all()
        if gate.allow:
            self._log_raw({"event": "layered_mode_authorized",
                           "conditions": [c.to_dict() for c in gate.conditions]})
            LOG.info("layered mode AUTHORIZED — all %d conditions green",
                     len(gate.conditions))
        else:
            self._log_raw({"event": "layered_mode_refused",
                           "failed": list(gate.failed)})
            LOG.error("layered mode REFUSED — failing conditions: %s",
                      ", ".join(gate.failed))
        return gate

    # -- logging -------------------------------------------------------------------

    def _log(self, gate: GateResult) -> None:
        self._log_raw({"event": "gate_check", **gate.to_dict()})

    def _log_raw(self, obj: dict[str, Any]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": self._clock(), **obj},
                                    sort_keys=True) + "\n")
        except OSError as exc:
            LOG.error("gate log write failed: %s", exc)


def _main(argv: Sequence[str]) -> int:
    """CLI: ``layered_gate.py check|enable [--root R]``.

    Exit 0 = all conditions green; 1 = refused (failures listed on stderr).
    """
    import argparse

    ap = argparse.ArgumentParser(description="layered-mode mechanical gate")
    ap.add_argument("command", choices=["check", "enable"])
    ap.add_argument("--root", default=os.environ.get("LOOP_ROOT", "."))
    ap.add_argument("--policy", default=None)
    args = ap.parse_args(argv)

    policy = load_policy(Path(args.policy)) if args.policy else None
    gate = LayeredGate(args.root, policy=policy)
    result = gate.enable() if args.command == "enable" else gate.check_all()
    print(json.dumps(result.to_dict(), indent=1))
    if not result.allow:
        for name in result.failed:
            print("FAILED: %s" % name, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
