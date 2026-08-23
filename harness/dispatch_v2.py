#!/usr/bin/env python3
"""dispatch_v2.py — Mode/budget/governance-aware dispatch (P0-5.1 fix, §5.5).

Rewrites the dispatch mechanism around four integrations:

1. **The Sol budget block actually works.**  The shipped
   ``dispatch.py:729-737`` gate compared the *dispatched role's model* against
   ``SOL_MODELS`` — a null set, since every role TOML pins V4/K3.  v2 keeps
   that check as belt-and-suspenders (any future Sol-model role IS blocked)
   and adds the binding checks that were missing:

   * the meter-driven BLOCK/hysteresis band and the budget controller's
     DEGRADE/BREAK states gate *all* new dispatch of Sol-bound work (routes
     whose target is ``sol_adjudicate``);
   * the loop-state exemption is the **attested** state from the Root-Turn
     Governor, not the self-derived empty-ledger "planning".

2. **Router integration:** every dispatch consults :class:`AgentRouter` for
   the effective mode; in ``layered`` mode K3-suited packets dispatch K3-first
   with mechanical escalation; Sol is the fallback only when the budget allows
   AND K3 is unavailable (queue stale / guard failure), and the fallback is
   ledgered with a closed reason code.

3. **Budget integration:** each spawn registers with the
   :class:`BudgetController`; a task in BREAK refuses new dispatch with a
   ``BudgetExceeded`` control event (never a bare failure).

4. **Attribution + demand side effects:** each spawn writes
   ``data/usage/run_role_map.json`` (meter v2 role attribution, P0-4.4) and
   K3-pool demand for K3 roles (``refill_controller_v2`` reads it, P0-6).

Model pins come exclusively from configuration (``[models]`` in the
orchestration policy + role TOMLs, which must agree) — no model string is
hardcoded here.  ipybox policy (task constraint): Desktop-native stays
disabled; WSL/headless exec workers get explicit ipybox; K3
planning/verifying roles run without ipybox unless the packet declares
``needs_code_execution``.
"""
from __future__ import annotations

import argparse
import enum
import json
import os
import platform
import re
import subprocess
import sys
import time
import tomllib
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

try:
    from orchestration_common import (LoopPaths, ModelPinError, OrchestrationPolicy,
                                      PolicyError, RefillPolicy, append_ndjson,
                                      atomic_write_json, file_lock, get_logger,
                                      read_json, utc_now)
    from agent_router import AgentRouter, Route, RouteReason, RoutingMode
    from budget_controller import BudgetController, BudgetExceeded, BudgetState
    from root_turn_governor import RootTurnGovernor
except ImportError:  # pragma: no cover - direct CLI execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from orchestration_common import (LoopPaths, ModelPinError, OrchestrationPolicy,
                                      PolicyError, RefillPolicy, append_ndjson,
                                      atomic_write_json, file_lock, get_logger,
                                      read_json, utc_now)
    from agent_router import AgentRouter, Route, RouteReason, RoutingMode
    from budget_controller import BudgetController, BudgetExceeded, BudgetState
    from root_turn_governor import RootTurnGovernor

__all__ = [
    "DispatchBlocked",
    "RolePin",
    "resolve_role_pin",
    "ipybox_enabled_for",
    "sol_budget_block_v2",
    "DispatcherV2",
    "main",
]

log = get_logger("loop.dispatch_v2")

PACKET_ID_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")

#: Role -> model family (used for pin agreement + pool hints; role list is
#: config-extensible via roles.yaml — this map covers the dispatchable set).
ROLE_FAMILY: Final[dict[str, str]] = {
    "worker": "v4",
    "scout": "v4",
    "duty_officer": "v4",
    "verifier": "k3",
    "reviewer": "k3",
    "plan_expander": "k3",
    "sol": "sol",
}

#: K3 planning/verifying roles: ipybox disabled unless the packet needs code
#: execution (task constraint).
K3_ROLES: Final[frozenset[str]] = frozenset({"verifier", "reviewer", "plan_expander"})

DISPATCHABLE_ROLES: Final[tuple[str, ...]] = (
    "worker", "reviewer", "verifier", "duty_officer", "plan_expander")


class DispatchBlocked(RuntimeError):
    """Dispatch refused by a budget/governance gate (fail-visible)."""

    def __init__(self, message: str, detail: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.detail = dict(detail)


class ExecutionPlane(str, enum.Enum):
    """Where a worker physically runs — drives the ipybox decision."""

    WSL_HEADLESS = "wsl_headless"
    DESKTOP_NATIVE = "desktop_native"


@dataclass(frozen=True)
class RolePin:
    """Resolved (model, effort, sandbox, context) pins for one role."""

    role: str
    family: str
    model: str
    effort: str
    sandbox: str
    context_window: int | None
    compaction: int | None

    def cli_overrides(self) -> list[str]:
        """CLI ``-m/-c`` overrides — exec top-level processes do not load
        agents/*.toml, so pins MUST ride the command line (v1 P0-1 fix,
        preserved)."""
        overrides = ["-m", self.model, "-c",
                     f"model_reasoning_effort={self.effort}"]
        if self.context_window:
            overrides += ["-c", f"model_context_window={self.context_window}"]
        if self.compaction:
            overrides += ["-c",
                          f"model_auto_compact_token_limit={self.compaction}"]
        return overrides


def validate_packet_id(pid: Any) -> str:
    if not isinstance(pid, str) or not PACKET_ID_RE.fullmatch(pid):
        raise DispatchBlocked(f"invalid packet id {pid!r}",
                              {"why": "packet_id_invalid"})
    return pid


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------
def resolve_role_pin(role: str, paths: LoopPaths,
                     policy: OrchestrationPolicy) -> RolePin:
    """Resolve model/effort/sandbox for a role.

    Order of truth: the role TOML supplies effort/sandbox; the model must
    AGREE with the ``[models]`` policy pin for the role's family — a mismatch
    is a :class:`ModelPinError` (fail-visible), never a silent preference.
    A missing role TOML falls back to the policy pin + ``[model_context]``
    sizing, so new roles (plan_expander) work before their TOML ships.
    """
    family = ROLE_FAMILY.get(role)
    if family is None:
        raise ModelPinError(f"unknown role {role!r}: no model family mapping")
    policy_pin = policy.model_pin(family)

    toml_doc: dict[str, Any] = {}
    for candidate in (paths.root / "agents" / f"{role}.toml",
                      Path(os.environ.get("CODEX_HOME",
                                          str(Path.home() / ".codex")))
                      / "agents" / f"{role}.toml"):
        if candidate.exists():
            try:
                with candidate.open("rb") as handle:
                    toml_doc = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise ModelPinError(f"agent TOML {candidate} unreadable: {exc}")
            break

    toml_model = toml_doc.get("model")
    if toml_model is not None and toml_model != policy_pin:
        raise ModelPinError(
            f"role {role!r}: agents TOML pins {toml_model!r} but policy "
            f"[models].{family} pins {policy_pin!r} — refusing divergent pins")

    ctx = toml_doc.get("model_context_window") \
        or policy.model_context(family, "context_window")
    compaction = toml_doc.get("model_auto_compact_token_limit") \
        or policy.model_context(family, "compaction")
    effort = toml_doc.get("model_reasoning_effort") \
        or policy.model_reasoning(family)
    if not effort:
        raise ModelPinError(f"role {role!r}: reasoning effort unresolvable "
                            f"from TOML or policy — fail-visible")
    sandbox = toml_doc.get("sandbox_mode",
                           "read-only" if family == "k3" else "workspace-write")
    return RolePin(role=role, family=family, model=policy_pin,
                   effort=str(effort), sandbox=str(sandbox),
                   context_window=ctx, compaction=compaction)


# ---------------------------------------------------------------------------
# ipybox policy (task constraint — do NOT re-enable Desktop ipybox)
# ---------------------------------------------------------------------------
def detect_plane() -> ExecutionPlane:
    """WSL/headless vs Desktop-native, cross-platform."""
    if os.environ.get("LOOP_EXECUTION_PLANE") == "desktop_native":
        return ExecutionPlane.DESKTOP_NATIVE
    if platform.system() == "Windows":
        return ExecutionPlane.DESKTOP_NATIVE
    release = platform.uname().release.lower()
    if "microsoft" in release or os.environ.get("WSL_DISTRO_NAME"):
        return ExecutionPlane.WSL_HEADLESS
    return ExecutionPlane.WSL_HEADLESS  # generic headless POSIX ≡ worker plane


def ipybox_enabled_for(role: str, plane: ExecutionPlane,
                       policy: OrchestrationPolicy,
                       packet: Mapping[str, Any] | None = None) -> bool:
    """The three-rule ipybox decision:

    * Desktop-native: DISABLED (``[ipybox].desktop_native_enabled=false``);
    * WSL/headless execution workers: explicit ENABLED;
    * K3 planning/verifying roles: DISABLED unless the packet declares
      ``needs_code_execution=true`` (and the policy allows the exception).
    """
    if plane is ExecutionPlane.DESKTOP_NATIVE:
        return bool(policy.value("ipybox", "desktop_native_enabled", False))
    if role in K3_ROLES:
        if bool(policy.value("ipybox", "k3_planning_verifying_enabled", False)):
            return True
        needs_code = bool((packet or {}).get("needs_code_execution"))
        return needs_code and bool(
            policy.value("ipybox", "k3_code_execution_exception", True))
    if role != "worker":
        return False
    return bool(policy.value("ipybox", "wsl_headless_worker_enabled", True))


# ---------------------------------------------------------------------------
# The (now live) Sol budget block
# ---------------------------------------------------------------------------
def sol_budget_block_v2(paths: LoopPaths, policy: OrchestrationPolicy,
                        role_pin: RolePin, route: Route | None,
                        governor: RootTurnGovernor,
                        budget: BudgetController) -> dict[str, Any] | None:
    """Return a block record when new Sol-bound work must be refused.

    Fixes the dead gate: the decision binds on (a) any *Sol-model* dispatch
    (belt-and-suspenders — the v1 check, kept), (b) any route that terminates
    at Sol (``sol_adjudicate``), and (c) the budget controller's DEGRADE/BREAK
    states.  Exemptions use the governor's **attested** loop state, so an
    empty ledger no longer grants a permanent pass (P0-5.2).
    ``None`` means dispatch may proceed.
    """
    state, trusted = governor.loop_state()
    exempt = trusted and state in ("planning", "adjudication", "release_finalize")
    sol_bound = (role_pin.family == "sol"
                 or route is Route.SOL_ADJUDICATE)
    if not sol_bound:
        # V4/K3 dispatch is never budget-blocked at Tier 1 — it reduces the
        # Sol share.  Tier 2 BREAK still refuses everything below.
        if budget.state() is BudgetState.BREAK:
            return {"reason": "task budget BREAK: no new dispatch",
                    "budget_state": "BREAK"}
        return None
    if exempt and state == "planning":
        # Planning exemption is bounded by the governor's lease, not open-ended.
        lease_doc = read_json(paths.governor_dir / "planning_lease.json", {}) or {}
        if int(lease_doc.get("turns_used", 0)) >= policy.planning_max_turns():
            exempt = False
    if exempt:
        return None
    band = governor.hysteresis.band()
    share, meter_status = governor.meter_status()
    if meter_status in ("STALE", "MISSING", "MALFORMED"):
        return {"reason": f"Sol token meter {meter_status} — failing closed "
                          f"for new Sol-bound work", "meter_status": meter_status}
    if band == "HIGH":
        return {"reason": "Sol token-share hard cap (hysteresis HIGH band)",
                "sol_5h_share": share, "band": band}
    if budget.state() in (BudgetState.DEGRADE, BudgetState.BREAK):
        return {"reason": f"task budget {budget.state().value}: Sol-bound "
                          f"dispatch refused; delegate to V4/K3",
                "budget_state": budget.state().value}
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
class DispatcherV2:
    """Route-aware packet dispatcher.

    The physical spawn path (worktree allocation, lifecycle supervisor,
    throttle) is delegated to the existing v1 machinery via subprocess when
    present; this class owns the *decision* layer that v1 lacked.
    """

    def __init__(self, paths: LoopPaths | None = None) -> None:
        self.paths = paths or LoopPaths.resolve()
        self.policy = OrchestrationPolicy.load(self.paths)
        self.refill_policy = RefillPolicy.load(self.paths)
        self.router = AgentRouter(self.paths, self.policy)
        self.budget = BudgetController(self.paths, self.policy)
        self.governor = RootTurnGovernor(self.paths, self.policy)
        self.plane = detect_plane()

    # -- attribution ------------------------------------------------------------
    def record_run_role(self, run_id: str, role: str, model: str,
                        packet_id: str) -> None:
        """Write ``run_id -> {role, model, packet_id}`` for meter v2
        attribution (P0-4.4: role by dispatch record, not model string)."""
        path = self.paths.run_role_map
        with file_lock(path.with_suffix(".lock")):
            doc = read_json(path, {}) or {}
            doc[run_id] = {"role": role, "model": model,
                           "packet_id": packet_id, "ts": utc_now()}
            atomic_write_json(path, doc)

    # -- K3 demand side effect (P0-6) ------------------------------------------------
    def add_k3_demand(self, count: int = 1) -> None:
        """Record K3-pool demand for the refill controller — demand arrives as
        a side effect of real work, never idle filling."""
        try:
            from refill_controller_v2 import RefillControllerV2
            RefillControllerV2(self.paths).queue_add(count, "k3")
        except (ImportError, PolicyError) as exc:  # pragma: no cover
            log.warning("k3 demand write skipped: %s", exc)

    # -- packet loading -----------------------------------------------------------------
    def load_packet(self, pid: str) -> dict[str, Any]:
        validate_packet_id(pid)
        packet = read_json(self.paths.data / "packets" / f"{pid}.json", None)
        if not isinstance(packet, dict):
            raise DispatchBlocked(f"packet {pid} unreadable",
                                  {"why": "packet_missing"})
        for key in ("packet_id", "goal", "authorized_paths", "acceptance"):
            if key not in packet:
                raise DispatchBlocked(f"packet {pid} missing field {key}",
                                      {"why": "packet_invalid", "field": key})
        return packet

    # -- role selection ---------------------------------------------------------------------
    def role_for_route(self, route: Route) -> str | None:
        return {
            Route.V4_DIRECT: "worker",
            Route.K3_EXPAND: "plan_expander",
            Route.K3_VERIFY: "verifier",
            Route.K3_RANK: "verifier",
            Route.MERGE_QUEUE: None,           # zero-model
            Route.SOL_ADJUDICATE: None,        # Sol turn, not a spawn
            Route.L4_HUMAN: None,              # human gate
        }.get(route)

    def k3_available(self) -> bool:
        """Whether new K3 births are allowed by provider backoff state.

        L2-consumer freshness is a layered-mode admission guard, not provider
        health.  Using it here caused healthy direct/refill K3 births to be
        suppressed merely because the queue consumer was quiet.
        """
        from provider_health import backoff_active
        pin = resolve_role_pin("verifier", self.paths, self.policy)
        blocked, _ = backoff_active(self.paths.root, pin.model)
        return not blocked

    def retain_k3_debt(self, pid: str, role: str | None,
                       route: Route) -> None:
        append_ndjson(self.paths.router_dir / "route_ledger.ndjsonl",
                      {"ts": utc_now(), "packet_id": pid,
                       "event": "k3_unavailable_debt_retained",
                       "role": role, "route": route.value})
        raise DispatchBlocked(
            "K3 unavailable; retaining K3 packet/refill debt",
            {"why": "k3_unavailable", "role": role,
             "route": route.value, "retryable": True})

    # -- dispatch decision ----------------------------------------------------------------------
    def decide(self, pid: str, requested_role: str | None = None,
               signals: Mapping[str, Any] | None = None,
               action: str = "pass") -> tuple[str, Route, RouteReason]:
        """Decide (role, route, reason) for one packet.

        In ``layered`` mode the router's K3-first cascade applies.  A K3 route
        remains K3-owned while its provider is temporarily unavailable: the
        packet is refused visibly and stays dispatchable for a later refill.
        It must never inherit Sol or silently degrade to V4.
        """
        packet = self.load_packet(pid)
        decision = self.router.route_action(
            pid, action, signals=signals or {},
            packet_meta={"class": packet.get("class", ""),
                         "risk_tags": packet.get("risk_tags", [])},
            high_risk=bool(packet.get("high_risk")))
        route, reason = decision.route, decision.reason

        if requested_role:
            # An explicit role is a hard model-family pin.  Preserve that role
            # in the semantic route as well so a verifier never receives an
            # Executor prompt merely because cold-start routing returned V4.
            explicit_route = {
                "worker": Route.V4_DIRECT,
                "verifier": Route.K3_VERIFY,
                "reviewer": Route.K3_VERIFY,
                "plan_expander": Route.K3_EXPAND,
            }.get(requested_role, route)
            return requested_role, explicit_route, reason

        role = self.role_for_route(route)
        if route in (Route.K3_VERIFY, Route.K3_EXPAND, Route.K3_RANK) \
                and not self.k3_available():
            self.retain_k3_debt(pid, role, route)
        if role is None:
            role = "worker"
        return role, route, reason

    # -- physical spawn --------------------------------------------------------------------------
    def build_exec_command(self, pid: str, role: str, prompt: str,
                           packet: Mapping[str, Any],
                           sandbox: str | None = None) -> tuple[list[str], RolePin]:
        """Build the ``codex exec`` command with pins + ipybox policy."""
        pin = resolve_role_pin(role, self.paths, self.policy)
        effective_sandbox = sandbox or pin.sandbox
        ipybox = ipybox_enabled_for(role, self.plane, self.policy, packet)
        rdir = self.paths.data / "reports" / pid
        from dispatch import resolve_codex_binary
        cmd = [resolve_codex_binary(), "exec", "--skip-git-repo-check",
               "--sandbox", effective_sandbox,
               *replace(pin, sandbox=effective_sandbox).cli_overrides(),
               "-c", f"mcp_servers.ipybox.enabled={'true' if ipybox else 'false'}"]
        if os.name == "nt":
            cmd += ["-c", "mcp_servers.node_repl.enabled=false"]
        cmd += ["--json", "-o", str(rdir / "last_message.txt"), prompt]
        return cmd, replace(pin, sandbox=effective_sandbox)

    def dispatch(self, pids: Sequence[str], role: str | None = None,
                 dry_run: bool = False, action: str = "pass",
                 wave_idx: int = 0) -> int:
        """Dispatch packets route-aware.  Returns a process exit code."""
        rc = 0
        for pid in pids:
            try:
                chosen_role, route, reason = self.decide(pid, role, action=action)
            except (DispatchBlocked, PolicyError, ModelPinError) as exc:
                log.error("dispatch %s refused: %s", pid, exc)
                self._event(pid, "dispatch_refused", {"error": str(exc)})
                rc = max(rc, 3)
                continue
            if route is Route.MERGE_QUEUE:
                self._event(pid, "merge_queue_enqueued",
                            {"route_reason": reason.value})
                continue
            if route is Route.L4_HUMAN:
                self._event(pid, "l4_queued", {"route_reason": reason.value})
                continue
            if chosen_role == "sol" or route is Route.SOL_ADJUDICATE:
                # Sol is a turn, not a spawn: emit the bounded adjudication
                # request; the governor bounds the turn itself.
                self._event(pid, "sol_adjudication_requested",
                            {"route_reason": reason.value})
                continue
            try:
                rc = max(rc, self._spawn(pid, chosen_role, route, reason,
                                         dry_run, wave_idx))
            except (DispatchBlocked, BudgetExceeded, PolicyError,
                    ModelPinError) as exc:
                log.error("spawn %s refused: %s", pid, exc)
                self._event(pid, "dispatch_refused", {"error": str(exc)})
                rc = max(rc, 3)
        return rc

    def _spawn(self, pid: str, role: str, route: Route, reason: RouteReason,
               dry_run: bool, wave_idx: int) -> int:
        packet = self.load_packet(pid)
        if (not dry_run
                and route in (Route.K3_VERIFY, Route.K3_EXPAND, Route.K3_RANK)
                and not self.k3_available()):
            # Explicit-role refill manifests bypass automatic route selection,
            # so enforce the same no-fallback contract at the final birth edge.
            self.retain_k3_debt(pid, role, route)
        pin = resolve_role_pin(role, self.paths, self.policy)
        # Parent-manifest packets are admitted read-only.  Keep the role/model
        # pin intact, but enforce the packet's stricter sandbox at the final
        # command boundary so a read-only parent can never inherit the worker
        # role's normal workspace-write default.
        effective_sandbox = pin.sandbox
        if packet.get("parent_enabled"):
            if packet.get("sandbox") != "read-only":
                raise DispatchBlocked("parent packet sandbox is not read-only",
                                      {"why": "parent_sandbox_invalid"})
            effective_sandbox = "read-only"
        # Belt-and-suspenders + live budget gate.
        block = sol_budget_block_v2(self.paths, self.policy, pin, route,
                                    self.governor, self.budget)
        if block is not None:
            self._event(pid, "sol_budget_blocked",
                        {**block, "role": role, "model": pin.model})
            raise DispatchBlocked(str(block["reason"]), block)

        attempt = self._packet_attempt(pid)
        run_id = f"{pid}-a{attempt}-{uuid.uuid4().hex}"
        reservation_id = run_id
        prompt = self._spawn_prompt(packet, route)
        cmd, pin = self.build_exec_command(pid, role, prompt, packet,
                                           sandbox=effective_sandbox)
        detail = {"mode": "single_v2", "role": role, "model": pin.model,
                  "reasoning_effort": pin.effort, "route": route.value,
                  "route_reason": reason.value, "wave": wave_idx,
                  "attempt": attempt, "run_id": run_id, "dry_run": dry_run,
                  "plane": self.plane.value,
                  "ipybox_enabled": "mcp_servers.ipybox.enabled=true" in " ".join(cmd)}
        if dry_run:
            print(f"DRY-RUN {pid}: {json.dumps(cmd)}")
            self._event(pid, "dispatch_dry_run", detail)
            return 0
        # The v2 layer owns the decision, but the shipped v1 dispatcher remains
        # the physical authority for worktree allocation, birth throttling,
        # lifecycle supervision, roster publication and report publication.
        # This is the production bridge; direct Popen here would create a
        # second, weaker execution plane.
        reserved = False
        try:
            # Reserve before the physical birth.  A post-spawn registration
            # races the budget and can leave an unaccounted live child.
            self.budget.register_agent(reservation_id, role)
            reserved = True
            import dispatch as dispatch_v1  # noqa: PLC0415
            run_id = dispatch_v1.dispatch_single(
                [pid], False, role=role, wave_idx=wave_idx,
                pinned=(pin.cli_overrides(), effective_sandbox,
                        pin.model, pin.effort),
                mode="single_v2",
                prompt_builder=lambda _packet, worktree, _run_id:
                    self._spawn_prompt(
                        packet, route, worktree,
                        task_label=dispatch_v1.task_name(packet),
                        role=role,
                        previous_attempt=dispatch_v1.previous_attempt_line(pid)),
                capture_report=(role in ("verifier", "reviewer", "plan_expander")
                                or bool(packet.get("parent_enabled"))),
                run_id_overrides={pid: reservation_id},
                parent_session_id_overrides={
                    pid: str(packet["parent_session_id"])
                } if packet.get("parent_session_id") else None,
                readonly_cwd_overrides={
                    pid: str(packet["cwd"])
                } if packet.get("parent_enabled") and packet.get("cwd") else None,
                detail_extra={"route": route.value,
                              "route_reason": reason.value,
                              "orchestration_v2_adapter": True})
        except (OSError, RuntimeError, SystemExit, ValueError) as exc:
            if reserved:
                self.budget.reclaim(reservation_id)
            self._event(pid, "exec_failed",
                        {"why": "spawn_failed", "phase": "pre_spawn",
                          "error": str(exc),
                          "run_id": run_id, "attempt": attempt})
            raise DispatchBlocked(f"spawn failed: {exc}", {"why": "spawn_failed"})
        if not run_id:
            if reserved:
                self.budget.reclaim(reservation_id)
            raise DispatchBlocked("physical dispatcher returned no run id",
                                  {"why": "spawn_failed"})
        try:
            self.record_run_role(run_id, role, pin.model, pid)
        except OSError as exc:
            # The child is already live.  Attribution failure is visible but
            # never converted into a false spawn failure or budget reclaim.
            try:
                self._event(pid, "post_spawn_accounting_degraded",
                            {"why": "run_role_map", "error": str(exc),
                             "run_id": run_id})
            except OSError:
                pass
        if pin.family == "k3":
            self.add_k3_demand(1)
        return 0

    # -- helpers -------------------------------------------------------------------------------------
    def _spawn_prompt(self, packet: Mapping[str, Any], route: Route,
                      worktree: str | None = None,
                      task_label: str | None = None,
                      role: str | None = None,
                      previous_attempt: str = "") -> str:
        pid = packet["packet_id"]
        role_line = {"k3_verify": "You are an L2 Verifier (BLOCK/ESCALATE "
                                  "power only; you can never release).",
                     "k3_rank": "You are a K3 candidate ranker. Rank at most "
                                "three candidates; you can never release.",
                     "k3_expand": "You are a Plan Expander. Output must "
                                  "validate against plan_expander.schema.json.",
                     }.get(route.value, "You are an Executor.")
        if role == "reviewer":
            role_line = ("You are the release-gate Reviewer. Perform a "
                         "falsification review; you can never release.")
        parent_readonly = bool(packet.get("parent_enabled"))
        return (
            f"任务名：{task_label or ('执行数据包 ' + pid + ' — ' + str(packet.get('goal') or ''))[:160]}\n"
            f"{role_line}\n"
            + (f"Work ONLY inside {worktree}.\n" if worktree else "") +
            ("This is a read-only parent packet. Do not create, modify, rename, "
             "or delete files; do not start services or delegate. The lifecycle "
             "supervisor captures your final response externally.\n"
             if parent_readonly else "") +
            f"goal: {packet['goal']}\n"
            f"authorized_paths: {json.dumps(packet['authorized_paths'])}\n"
            f"acceptance: {json.dumps(packet['acceptance'])}\n"
            f"constraints: {json.dumps(packet.get('constraints', []))}\n"
            f"{previous_attempt}" +
            (("Return a concise evidence-backed conclusion in the final response; "
              "do not write a report file.") if parent_readonly else
             (f"On completion write data/reports/{pid}/report.json "
              '{"packet_id","status":"done|failed","summary"(<=500 tokens),'
              '"diff_stat"} and reply with 1 line: conclusion + artifact path.')))

    def _packet_attempt(self, pid: str) -> int:
        led = read_json(self.paths.ledger, {}) or {}
        try:
            return int(led.get("packets", {}).get(pid, {}).get("attempts", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _record_spawn_time(self, pid: str, run_id: str, attempt: int) -> None:
        path = self.paths.data / "spawn_times.json"
        with file_lock(path.with_suffix(".lock")):
            times = read_json(path, {}) or {}
            times[pid] = {"ts": utc_now(), "mode": "single_v2",
                          "run_id": run_id, "attempt": attempt}
            atomic_write_json(path, times)

    def _event(self, pid: str, event: str, detail: Mapping[str, Any]) -> None:
        append_ndjson(self.paths.events,
                      {"ts": utc_now(), "packet_id": pid, "event": event,
                       "detail": dict(detail)},
                      lock_path=self.paths.events_lock)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="LOOP-F2 dispatcher v2 (route/budget/governance aware)")
    ap.add_argument("--packet", action="append", required=True,
                    help="packet id(s) to dispatch")
    ap.add_argument("--role", default=None, choices=DISPATCHABLE_ROLES,
                    help="explicit role (manual ops); default: router decides")
    ap.add_argument("--action", default="pass",
                    help="trigger-table action feeding the router")
    ap.add_argument("--wave", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        dispatcher = DispatcherV2()
    except PolicyError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    return dispatcher.dispatch(args.packet, role=args.role,
                               dry_run=args.dry_run, action=args.action,
                               wave_idx=args.wave)


if __name__ == "__main__":
    sys.exit(main())
