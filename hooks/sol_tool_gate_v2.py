#!/usr/bin/env python3
"""sol_tool_gate_v2.py — fail-closed, externally-attested root-turn governor.

Supersedes ``hooks/sol_tool_gate.py`` and fixes P0-5
(``phase1b_codebase_analysis.md``): the v1 gate failed **open** on every
error, exempted any session whose ledger was empty ("planning" forever), and
honored a ``loop_state`` key written *by the very session it constrains* —
the constrained party controlled its own exemption switch.

v2 inverts every one of those properties (design §2.5):

1. **Fail-closed** — an unreadable ledger, a STALE/MISSING meter report, or
   any internal error **denies** gated tools for non-planning states, with a
   ``governor.fail_closed`` audit event. Plain conversation and
   packet/dispatch tools are never gated, so "decompose and dispatch" is
   always available.
2. **No self-control** — an explicit ``loop_state`` ledger key is honored
   only when accompanied by a matching attestation appended through the
   harness CLI (``loop_state_set`` writes ``governor.state_set`` records into
   ``data/governor/state_attestations.ndjsonl`` with a reason + idempotency
   key). A bare key is ignored and flagged
   (``governor.state_key_unattested``).
3. **Bounded planning lease** — "planning" derived from an empty ledger
   carries a lease (``planning_max_turns`` / ``planning_max_new_tokens``
   without a packet-creation event). On expiry, gated tools are denied with
   the actionable reason "decompose or dispatch".
4. **Budget actuation at the right boundary** — the gate reads meter v2
   (:mod:`model_token_share_v2`): budget state ``HIGH`` (5h hysteresis) or a
   critical per-root 1h share denies non-exempt gated calls with an
   actionable delegate instruction. This finally binds the actual token
   consumer: root-session *turns*, not Sol-model dispatches (a null set).
5. **Audit everything** — every decision (allow *and* deny) is appended to
   ``data/governor/gate_decisions.ndjsonl``. Break-glass
   (``LOOP_GOVERNOR_OVERRIDE=<reason>``) allows a bypass but appends a
   ``governor.break_glass`` event per call — observable, never silent.

Model families come from ``config/orchestration_policy_v2.toml [models]`` —
the Sol model is **not hardcoded** (execution currently uses V4 Flash).

Hook contract: PreToolUse payload (one JSON object) on stdin; deny emits
``hookSpecificOutput.permissionDecision = "deny"`` on stdout; exit 0 always
(the *decision*, not the exit code, carries the outcome).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

__all__ = [
    "GATED_TOOL_PREFIXES",
    "GateDecision",
    "SolToolGateV2",
    "main",
]

LOG = logging.getLogger("sol_tool_gate_v2")

# Tool names (lowercased, prefix-matched) that constitute L0 data processing
# when issued from the root Sol session. Packet/dispatch tools are absent by
# design: delegation must always remain available (fail-closed escape hatch).
GATED_TOOL_PREFIXES: Final[tuple[str, ...]] = (
    "shell", "shell_command", "bash", "local_shell", "exec_command",
    "functions.exec", "run_terminal", "terminal", "web_search", "search",
    "grep", "glob", "mcp__", "read_mcp_resource", "list_mcp",
    "read_many_files", "read_file", "list_files", "pytest", "test",
)

_ADJUDICATION_STATES: Final[frozenset[str]] = frozenset(
    {"SOL_ADJUDICATE", "DEAD_LETTER", "MERGE_CONFLICT", "WAVE_DONE",
     "WAVE_DONE_READY", "SOL_WAKE"})
_TERMINAL_STATES: Final[frozenset[str]] = frozenset({"MERGED", "DONE"})
# K3 work states count as EXECUTION — K3 work must never re-open Sol's tool
# window (design §7 loop-state derivation update).
_EXECUTION_EXTRA: Final[frozenset[str]] = frozenset(
    {"EXPAND_K3", "L2_VERIFY", "L2_RANK"})


@dataclass(frozen=True)
class GateDecision:
    """Structured outcome of one gate evaluation."""

    allow: bool
    reason: str
    state: str | None = None
    rule: str = ""
    break_glass: bool = False

    def to_hook_output(self) -> str | None:
        """PreToolUse deny JSON, or ``None`` when allowed."""
        if self.allow:
            return None
        return json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": self.reason}})


def _load_policy(policy_path: Path) -> dict[str, Any]:
    """Fail-closed TOML load (tomllib; minimal fallback on 3.10)."""
    text = policy_path.read_text(encoding="utf-8")  # OSError propagates
    try:
        import tomllib
        return tomllib.loads(text)
    except ModuleNotFoundError:
        harness = policy_path.resolve().parent.parent / "harness"
        sys.path.insert(0, str(harness))
        from l2_consumer import _minimal_toml  # noqa: PLC0415
        return _minimal_toml(text)


class SolToolGateV2:
    """The governor. One instance per hook invocation (stateless on disk).

    Args:
        root: LOOP root directory.
        policy: parsed policy mapping; loaded fail-closed when ``None``.
        clock: injectable time source.
    """

    def __init__(self, root: Path | str,
                 policy: Mapping[str, Any] | None = None,
                 clock: Any = time.time) -> None:
        self.root = Path(root).resolve()
        self._clock = clock
        self._policy_error: str | None = None
        if policy is None:
            try:
                policy = _load_policy(
                    self.root / "config" / "orchestration_policy_v2.toml")
            except Exception as exc:  # fail closed, remembered for evaluate()
                self._policy_error = "policy unreadable: %s" % exc
                policy = {}
        self.policy = policy
        gov = policy.get("governor", {})
        models = policy.get("models", {})
        budget = policy.get("budget", {})
        self.sol_model = str(models.get("sol_model", "")).strip().lower()
        self.v4_model = str(models.get("v4_model", "")).strip().lower()
        self.k3_model = str(models.get("k3_model", "")).strip().lower()
        self.allowed_states = frozenset(
            gov.get("allowed_states",
                    ["planning", "adjudication", "release_finalize"]))
        self.break_glass_env = str(
            gov.get("break_glass_env", "LOOP_GOVERNOR_OVERRIDE"))
        self.decision_log = self.root / Path(str(
            gov.get("decision_log", "data/governor/gate_decisions.ndjsonl")))
        self.lease_path = self.root / Path(str(
            gov.get("planning_lease_file", "data/governor/planning_lease.json")))
        self.attestations = self.root / Path(str(
            gov.get("attestation_ledger",
                    "data/governor/state_attestations.ndjsonl")))
        self.share_report = self.root / Path(str(
            gov.get("share_report", "data/usage/model_token_share_v2.json")))
        self.stale_after_s = float(
            policy.get("tokens", {}).get("stale_after_s", 7200))
        self.planning_max_turns = int(budget.get("planning_max_turns", 6))
        self.planning_max_new_tokens = int(
            budget.get("planning_max_new_tokens", 30000))
        self.critical_1h_share = float(
            policy.get("hysteresis", {}).get("critical_1h_share", 0.35))

    # -- audit ----------------------------------------------------------------

    def _audit(self, event: str, detail: dict[str, Any]) -> None:
        """Append one audit record; auditing failures degrade to stderr but
        never change the decision (the decision is computed first)."""
        try:
            self.decision_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.decision_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": self._clock(), "event": event,
                                     **detail}, sort_keys=True) + "\n")
        except OSError as exc:
            sys.stderr.write("sol_tool_gate_v2: audit write failed: %s\n" % exc)

    # -- state derivation -------------------------------------------------------

    def _attested_state(self, explicit: str) -> bool:
        """``True`` iff the newest attestation matches *explicit*.

        Attestations are appended by the harness CLI (``loop_state_set``) —
        a channel the constrained session does not control — as
        ``{"event": "governor.state_set", "state": ..., "reason": ...,
        "idem_key": ...}`` records.
        """
        latest: str | None = None
        try:
            with open(self.attestations, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("event") == "governor.state_set":
                        latest = str(obj.get("state", ""))
        except OSError:
            return False
        return latest == explicit

    def loop_state(self) -> str:
        """Derive the LOOP state; raises on unreadable ledger (fail closed).

        Explicit ``loop_state`` keys are honored only when attested; a bare
        key is ignored and flagged ``governor.state_key_unattested``.
        """
        path = self.root / "data" / "progress_ledger.json"
        led = json.loads(path.read_text(encoding="utf-8"))  # raises → deny
        explicit = led.get("loop_state")
        if isinstance(explicit, str) and explicit:
            if self._attested_state(explicit):
                return explicit
            self._audit("governor.state_key_unattested",
                        {"claimed_state": explicit})
        states = [str(p.get("state")) for p in led.get("packets", {}).values()]
        if not states:
            return "planning"
        if any(s in _ADJUDICATION_STATES for s in states):
            return "adjudication"
        if all(s in _TERMINAL_STATES for s in states):
            return "release_finalize"
        return "execution"  # includes EXPAND_K3 / L2_VERIFY / L2_RANK

    # -- planning lease -----------------------------------------------------------

    def _lease_check(self, new_tokens: int) -> GateDecision | None:
        """Enforce the bounded planning lease; ``None`` = lease healthy.

        The lease counts turns and new tokens since grant; a packet-creation
        event (non-empty ledger) renews it implicitly because the derived
        state stops being "planning".
        """
        now = self._clock()
        lease: dict[str, Any]
        try:
            lease = json.loads(self.lease_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            lease = {"granted_ts": now, "turns_used": 0, "new_tokens_used": 0}
        lease["turns_used"] = int(lease.get("turns_used", 0)) + 1
        lease["new_tokens_used"] = (int(lease.get("new_tokens_used", 0))
                                    + max(0, new_tokens))
        self._write_json(self.lease_path, lease)
        if (lease["turns_used"] > self.planning_max_turns
                or lease["new_tokens_used"] > self.planning_max_new_tokens):
            return GateDecision(
                False,
                "decompose or dispatch: planning lease exhausted (%d turns, "
                "%d new tokens) — emit packets/*.json + dag.json or open an "
                "adjudication packet" % (lease["turns_used"],
                                         lease["new_tokens_used"]),
                state="planning", rule="planning_lease_exhausted")
        return None

    @staticmethod
    def _write_json(path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- meter integration ---------------------------------------------------------

    def _budget_deny(self, state: str) -> GateDecision | None:
        """Meter v2 actuation: HIGH budget state / critical root ⇒ deny.

        STALE or MISSING reports fail closed for non-planning states — the
        sensor being blind is itself a reason for restraint (§2.4 AC5).
        """
        try:
            report = json.loads(self.share_report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
        if report is None or (
                self._clock() - float(report.get("generated_ts", 0))
                > self.stale_after_s):
            if state == "planning":
                return None  # planning turns stay usable while sensors heal
            return GateDecision(
                False,
                "meter report %s — governance sensor blind; fail-closed for "
                "non-planning gated tools. Delegate: dispatch as packet or "
                "open a bounded adjudication packet."
                % ("missing" if report is None else "STALE"),
                state=state, rule="meter_stale_fail_closed")
        if report.get("budget_state") == "HIGH":
            return GateDecision(
                False,
                "Sol 5h effective share %.4f is in HIGH budget state — "
                "delegate: dispatch as packet (dispatch.py --role worker/"
                "verifier) or open a bounded adjudication packet."
                % float(report.get("sol_share_5h_effective") or 0.0),
                state=state, rule="budget_high")
        one_hour = (report.get("windows", {}) or {}).get("rolling_1h", {})
        if one_hour.get("critical_roots"):
            return GateDecision(
                False,
                "a root session exceeds the %.2f 1h critical share "
                "(runaway-root guard) — gated tools denied; delegate."
                % self.critical_1h_share,
                state=state, rule="critical_root_1h")
        return None

    # -- main evaluation --------------------------------------------------------------

    def evaluate(self, payload: Mapping[str, Any]) -> GateDecision:
        """Evaluate one PreToolUse payload. NEVER raises: errors → deny."""
        tool = str(payload.get("tool_name") or "").lower()
        if not tool.startswith(GATED_TOOL_PREFIXES):
            return GateDecision(True, "tool not gated", rule="ungated_tool")

        model = str(payload.get("model") or "").strip().lower()
        role = str(payload.get("agent_type") or payload.get("role") or "").strip().lower()
        family = None
        if role in {"worker", "duty_officer", "executor", "scout"}:
            family = "worker" if model == self.v4_model else None
        elif role in {"verifier", "reviewer", "plan_expander"}:
            family = "worker" if model == self.k3_model else None
        elif model:
            if model == self.sol_model:
                family = "sol"
            elif model == self.v4_model:
                family = "worker"
        if family == "worker":
            # V4/K3 children are exactly where the work SHOULD run.
            return GateDecision(True, "worker-family model", rule="worker_model")
        if family != "sol":
            # Unknown/missing model on a gated tool: a gated operation cannot
            # bypass Sol policy by omitting identity — fail closed.
            return GateDecision(
                False,
                "PreToolUse payload.model is missing or unknown (%r); gated "
                "operations cannot bypass the Sol policy by withholding "
                "identity (fail-closed)" % model,
                rule="unknown_model_fail_closed")

        if self._policy_error:
            return GateDecision(False,
                                "%s — governor cannot read its policy; "
                                "fail-closed" % self._policy_error,
                                rule="policy_unreadable")

        # break-glass: allowed but LOUD — one audit event per bypassed call.
        override = os.environ.get(self.break_glass_env, "").strip()
        if override:
            self._audit("governor.break_glass", {
                "reason": override, "tool": tool})
            return GateDecision(True, "break-glass override: %s" % override,
                                rule="break_glass", break_glass=True)

        try:
            state = self.loop_state()
        except (OSError, ValueError, KeyError) as exc:
            self._audit("governor.fail_closed", {"why": str(exc), "tool": tool})
            return GateDecision(
                False,
                "progress ledger unreadable (%s) — governor fails CLOSED for "
                "gated tools. Packet/dispatch tools remain available: "
                "decompose and dispatch." % exc,
                rule="ledger_unreadable_fail_closed")

        if state == "planning":
            lease_deny = self._lease_check(
                int(payload.get("estimated_new_tokens", 0) or 0))
            if lease_deny is not None:
                return lease_deny

        budget_deny = self._budget_deny(state)
        if budget_deny is not None:
            return budget_deny

        if state in self.allowed_states:
            return GateDecision(True, "state %s allows gated tools" % state,
                                state=state, rule="allowed_state")

        return GateDecision(
            False,
            "LOOP state is %s: this operation belongs to the zero-token "
            "layer or a worker packet (AGENTS.md §2). Dispatch it: "
            "dispatch.py --role worker --packet <id>, or route a "
            "verification-shaped question to the K3 verifier." % state,
            state=state, rule="state_gated")

    def decide(self, payload: Mapping[str, Any]) -> GateDecision:
        """Evaluate + audit one payload (the public entry point)."""
        try:
            decision = self.evaluate(payload)
        except Exception as exc:  # absolute backstop: unknown bug → deny
            decision = GateDecision(
                False, "governor internal error (%s) — fail-closed" % exc,
                rule="internal_error_fail_closed")
        self._audit("governor.decision", {
            "tool": payload.get("tool_name"),
            "allow": decision.allow, "rule": decision.rule,
            "state": decision.state, "reason": decision.reason[:400]})
        return decision


def main(stdin_text: str | None = None) -> int:
    """Hook entry: read the PreToolUse payload, print deny JSON if denied."""
    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not payload:
        # No payload at all: nothing identifiable to gate; deny gated-shaped
        # invocations is impossible without a tool name — treat as no-op.
        return 0
    root = os.environ.get("LOOP_ROOT")
    if root is None:
        start = Path(payload.get("cwd") or os.getcwd()).resolve()
        root = str(start)
        for candidate in (start, *start.parents):
            if (candidate / "data" / "progress_ledger.json").exists():
                root = str(candidate)
                break
    gate = SolToolGateV2(root)
    decision = gate.decide(payload)
    output = decision.to_hook_output()
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
