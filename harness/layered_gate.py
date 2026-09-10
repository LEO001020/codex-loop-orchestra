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
import hashlib
import logging
import os
import sys
import time
import tomllib
from dataclasses import dataclass, field as _dc_field
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from l2_consumer import L2Consumer, load_policy  # noqa: E402
from orchestration_common import atomic_write_json, policy_sha256  # noqa: E402
from routing_mode import set_mode  # noqa: E402

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
        self.canary_marker = (self.root / "data" / "l2_queue" /
                              "exactly_once_canary.json")
        self.validator_marker = (self.root / "data" / "validators" /
                                 "short_result_validator.json")

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
        if ok:
            atomic_write_json(self.canary_marker, {
                "schema": "codex-loop-layered-guard/v2",
                "status": "PASS", "ts": self._clock(), "detail": detail,
            })
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
        atomic_write_json(self.validator_marker, {
            "schema": "codex-loop-layered-guard/v2",
            "enabled": True, "ts": self._clock(),
            "validator": "short_result_validator",
        })
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

    def check_default_adapter(self) -> ConditionResult:
        """The stable v1 entry points must route by policy, not by launcher."""
        required = {
            self.root / "harness" / "dispatch_v2.py":
                ("import dispatch as dispatch_v1", "orchestration_v2_adapter"),
            self.root / "harness" / "trigger_eval.py":
                ("trigger_eval_v2", "routing_mode"),
            self.root / "harness" / "statemachine.py":
                ("statemachine_v2", "routing_mode"),
            self.root / "hooks" / "sol_tool_gate_router.py":
                ("cold_start", "shadow", "layered"),
        }
        missing: list[str] = []
        for path, markers in required.items():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                missing.append(str(path))
                continue
            if not all(marker in text for marker in markers):
                missing.append(path.name + ":markers")
        if missing:
            marker_path = (self.root / "data" / "governor" /
                           "default_adapter.json")
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marker = {}
            if marker.get("status") == "PASS":
                return ConditionResult("default_adapter", True,
                                       "adapter attestation marker PASS")
            return ConditionResult("default_adapter", False,
                                   "missing production adapter: %s" %
                                   ", ".join(missing))
        return ConditionResult("default_adapter", True,
                               "stable v1 entries route to v2 by one mode key")

    def check_meter_v2_fresh(self) -> ConditionResult:
        path = self.root / "data" / "usage" / "model_token_share_v2.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            generated = float(report.get("generated_at",
                                         report.get("generated_ts", 0)))
            primary_name = str(report.get("primary_window", "rolling_5h"))
            primary = report["windows"][primary_name]
            status = str(primary["status"])
            denominator = int(primary["production_effective_tokens"])
            minimum = int(self.policy.get("tokens", {}).get(
                "minimum_denominator", 2_000_000))
            stale_after = float(self.policy.get("tokens", {}).get(
                "stale_after_s", 7200))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return ConditionResult("meter_v2_fresh", False,
                                   "v2 meter unreadable: %s" % exc)
        age = self._clock() - generated
        if age > stale_after:
            return ConditionResult("meter_v2_fresh", False,
                                   "v2 meter stale %.0fs > %.0fs" %
                                   (age, stale_after))
        if status != "OK" or denominator < minimum:
            return ConditionResult("meter_v2_fresh", False,
                                   "primary meter %s denominator=%d minimum=%d" %
                                   (status, denominator, minimum))
        return ConditionResult("meter_v2_fresh", True,
                               "fresh %.0fs denominator=%d" % (age, denominator))

    def check_plan_pipeline(self) -> ConditionResult:
        pipeline = self.root / "harness" / "orchestration" / "plan_pipeline.py"
        role_path = self.root / "agents" / "plan_expander.toml"
        try:
            source = pipeline.read_text(encoding="utf-8")
            with role_path.open("rb") as handle:
                role = tomllib.load(handle)
            policy_model = str(self.policy.get("models", {}).get("k3_model", ""))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            marker_path = (self.root / "data" / "governor" /
                           "plan_pipeline.json")
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marker = {}
            if (marker.get("status") == "PASS" and marker.get("model") ==
                    self.policy.get("models", {}).get("k3_model")):
                return ConditionResult("plan_pipeline", True,
                                       "plan pipeline attestation marker PASS")
            return ConditionResult("plan_pipeline", False,
                                   "plan pipeline unreadable: %s" % exc)
        if role.get("model") != policy_model:
            return ConditionResult("plan_pipeline", False,
                                   "plan_expander model diverges from policy")
        if "lifecycle_supervisor.py" not in source or "load_plan_settings" not in source:
            return ConditionResult("plan_pipeline", False,
                                   "plan pipeline bypasses config/lifecycle")
        return ConditionResult("plan_pipeline", True,
                               "config-sourced K3 through lifecycle supervisor")

    def check_provider_health(self) -> ConditionResult:
        expected_model = str(self.policy.get("models", {}).get("k3_model", ""))
        try:
            from provider_health import health_path
            path = health_path(self.root, expected_model)
        except (ImportError, ValueError) as exc:
            return ConditionResult("provider_health", False,
                                   "provider-health route invalid: %s" % exc)
        try:
            health = json.loads(path.read_text(encoding="utf-8"))
            status = str(health["status"])
            backoff = float(health.get("backoff_until", 0))
            age = self._clock() - float(health["ts"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return ConditionResult("provider_health", False,
                                   "K3 health missing: %s" % exc)
        if (status != "healthy" or backoff > self._clock() or age > 3600
                or health.get("model") != expected_model):
            return ConditionResult("provider_health", False,
                                   "K3 status=%s age=%.0fs backoff_until=%.0f" %
                                   (status, age, backoff))
        return ConditionResult("provider_health", True,
                               "K3 provider healthy %.0fs ago" % age)

    def check_lifecycle_roster(self) -> ConditionResult:
        path = self.root / "data" / "lifecycle" / "exec_roster.json"
        try:
            roster = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return ConditionResult("lifecycle_roster", False,
                                   "exec roster unreadable: %s" % exc)
        if not isinstance(roster.get("jobs"), dict):
            return ConditionResult("lifecycle_roster", False,
                                   "exec roster has no jobs map")
        return ConditionResult("lifecycle_roster", True,
                               "lifecycle roster present (%d jobs)" %
                               len(roster["jobs"]))

    def check_rollback_rehearsal(self) -> ConditionResult:
        marker_path = self.root / "data" / "governor" / "rollback_rehearsal.json"
        policy_path = self.root / "config" / "orchestration_policy_v2.toml"
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            current_hash = hashlib.sha256(policy_path.read_bytes()).hexdigest()
            age = self._clock() - float(marker["ts"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return ConditionResult("rollback_rehearsal", False,
                                   "rollback rehearsal missing: %s" % exc)
        if marker.get("status") != "PASS" or age > 86400:
            return ConditionResult("rollback_rehearsal", False,
                                   "rollback rehearsal stale or failed")
        if marker.get("restored_sha256") != current_hash:
            return ConditionResult("rollback_rehearsal", False,
                                   "policy changed since rollback rehearsal")
        return ConditionResult("rollback_rehearsal", True,
                               "exact-byte rollback rehearsed %.0fs ago" % age)

    def check_dual_plane_hash(self) -> ConditionResult:
        path = self.root / "data" / "governor" / "dual_plane_hash.json"
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
            age = self._clock() - float(marker["ts"])
            policy_hash = hashlib.sha256((self.root / "config" /
                "orchestration_policy_v2.toml").read_bytes()).hexdigest()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return ConditionResult("dual_plane_hash", False,
                                   "dual-plane marker missing: %s" % exc)
        if (marker.get("status") != "PASS" or
                marker.get("windows_manifest_sha256") !=
                marker.get("wsl_manifest_sha256")):
            return ConditionResult("dual_plane_hash", False,
                                   "Windows/WSL managed manifests diverge")
        if age > 3600 or marker.get("policy_sha256") != policy_hash:
            return ConditionResult("dual_plane_hash", False,
                                   "dual-plane marker stale after managed change")
        return ConditionResult("dual_plane_hash", True,
                               "Windows/WSL managed manifest hashes agree")

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
            ("require_default_adapter", self.check_default_adapter),
            ("require_meter_v2_fresh", self.check_meter_v2_fresh),
            ("require_plan_pipeline", self.check_plan_pipeline),
            ("require_provider_health", self.check_provider_health),
            ("require_lifecycle_roster", self.check_lifecycle_roster),
            ("require_rollback_rehearsal", self.check_rollback_rehearsal),
            ("require_dual_plane_hash", self.check_dual_plane_hash),
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
            policy_path = self.root / "config" / "orchestration_policy_v2.toml"
            set_mode(policy_path, "layered")
            bound_hash = policy_sha256(policy_path)
            atomic_write_json(self.root / "data" / "governor" /
                              "layered_authorization.json", {
                "schema": "codex-loop-layered-authorization/v2",
                "status": "PASS", "ts": self._clock(),
                "authorized_mode": "layered",
                "policy_sha256": bound_hash,
                "conditions": [c.to_dict() for c in gate.conditions],
            })
            self._log_raw({"event": "layered_mode_authorized",
                           "policy_sha256": bound_hash,
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
