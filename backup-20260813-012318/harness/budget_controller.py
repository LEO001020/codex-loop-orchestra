#!/usr/bin/env python3
"""budget_controller.py — Three-tier runtime-enforced token budgets (§5.2/§8).

Implements Layer 1–4 of the governance blueprint:

* **Tier 1 — global quota:** Sol 5h effective-share band with a hard cap of
  15 % and a K3 floor of 20 % (policy ``[tokens].sol_hard_cap`` /
  ``k3_floor``), evaluated against the meter v2 report with a ≥2 M-token
  minimum denominator.
* **Tier 2 — task budget:** every task carries
  ``data/budget/active.json`` = ``{ceiling, allocated, consumed, state}``;
  allocation is distribution-based (P50/P90 per role from meter history with
  elastic headroom — never equal split, never single-run point estimates).
* **Tier 3 — turn ladder:** consumption ratio drives the budget state machine
  ``NORMAL (<60 %) → THROTTLE (≥60 %) → DEGRADE (≥85 %) → BREAK (>100 %)``
  with deterministic degrade paths; a :class:`BudgetExceeded` surfaces as a
  control-loop event, never a bare failure.
* **Reclaim/redistribute:** on packet completion the unused allocation
  returns to the task pool; near-limit agents get a deterministic
  extend-or-wrap-up decision.
* **In-loop signal (BATS):** :meth:`BudgetController.tracker_block` renders a
  Budget-Tracker block for injection after each tool result (appended at the
  END of tool results — never mutating the byte-stable prefix; R10).
* **Hysteresis governor:** budget states move monotonically upward within a
  task and downward only after a cooldown with sustained low usage —
  preventing THROTTLE↔NORMAL oscillation (R4).

Thread-safe: all mutations take a process lock (file lock) plus an in-process
:class:`threading.Lock`.
"""
from __future__ import annotations

import enum
import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping

try:
    from orchestration_common import (LoopPaths, OrchestrationPolicy, PolicyError,
                                      append_ndjson, atomic_write_json, file_lock,
                                      get_logger, read_json, utc_now)
except ImportError:  # pragma: no cover - direct CLI execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from orchestration_common import (LoopPaths, OrchestrationPolicy, PolicyError,
                                      append_ndjson, atomic_write_json, file_lock,
                                      get_logger, read_json, utc_now)

__all__ = [
    "BudgetState",
    "BudgetExceeded",
    "BudgetDecision",
    "RoleCostModel",
    "BudgetController",
]

log = get_logger("loop.budget_controller")


class BudgetState(str, enum.Enum):
    """The four-rung budget ladder (§3 / §8 L3)."""

    NORMAL = "NORMAL"
    THROTTLE = "THROTTLE"
    DEGRADE = "DEGRADE"
    BREAK = "BREAK"


_STATE_ORDER: Final[dict[BudgetState, int]] = {
    BudgetState.NORMAL: 0, BudgetState.THROTTLE: 1,
    BudgetState.DEGRADE: 2, BudgetState.BREAK: 3,
}


class BudgetExceeded(RuntimeError):
    """Raised when an operation would breach a hard budget boundary.

    Callers surface this as a control-loop event (worker: wrap-up injection;
    root: governor deny) — never as a bare failure (invariant 6: exhaustion
    triggers deterministic degrade paths, never mid-flight high-risk actions).
    """

    def __init__(self, message: str, agent_id: str, remaining: int) -> None:
        super().__init__(message)
        self.agent_id = agent_id
        self.remaining = remaining


@dataclass(frozen=True)
class BudgetDecision:
    """Deterministic extend-or-wrap-up decision for a near-limit agent."""

    agent_id: str
    action: str                # "extend" | "wrap_up"
    extension_tokens: int
    reason: str


@dataclass(frozen=True)
class RoleCostModel:
    """P50/P90 token-cost distribution for one role, from meter history.

    Run-to-run variance is large (up to 30×), so allocations are always
    distribution-based with headroom, never point estimates.
    """

    role: str
    p50: int
    p90: int
    samples: int

    def allocation(self, headroom: float) -> int:
        """Initial allocation: P90 padded by the elastic headroom factor."""
        return int(self.p90 * (1.0 + max(0.0, headroom)))


class BudgetController:
    """Per-task runtime budget controller over ``data/budget/active.json``."""

    #: Conservative default cost model used only when meter history has no
    #: samples for a role (labelled as such in the state file — fail-visible).
    _BOOTSTRAP_P50: Final[int] = 20_000
    _BOOTSTRAP_P90: Final[int] = 60_000

    def __init__(self, paths: LoopPaths | None = None,
                 policy: OrchestrationPolicy | None = None) -> None:
        self.paths = paths or LoopPaths.resolve()
        self.policy = policy or OrchestrationPolicy.load(self.paths)
        self.state_path = self.paths.budget_dir / "active.json"
        self.lock_path = self.paths.budget_dir / ".budget.lock"
        self._mem_lock = threading.Lock()
        self.throttle_at = float(self.policy.value("budget", "throttle_at", 0.60))
        self.degrade_at = float(self.policy.value("budget", "degrade_at", 0.85))
        self.break_at = float(self.policy.value("budget", "break_at", 1.00))
        self.cooldown_s = float(self.policy.value("budget", "state_cooldown_s", 60))
        self.headroom = float(self.policy.value("budget", "allocation_headroom", 0.30))

    # ------------------------------------------------------------------ state IO
    def _load(self) -> dict[str, Any]:
        doc = read_json(self.state_path, None)
        if not isinstance(doc, dict):
            doc = {"schema": "codex-loop-budget/v1", "task_id": None,
                   "ceiling": 0, "allocated": {}, "consumed": {},
                   "state": BudgetState.NORMAL.value,
                   "state_changed_at": utc_now(), "pool_returned": 0}
        return doc

    def _save(self, doc: dict[str, Any]) -> None:
        atomic_write_json(self.state_path, doc)

    # ------------------------------------------------------------------ cost model
    def role_cost_model(self, role: str) -> RoleCostModel:
        """P50/P90 for a role from the meter v2 history file
        (``data/usage/role_cost_history.json``, maintained by meter v2)."""
        history = read_json(self.paths.usage_dir / "role_cost_history.json", {}) or {}
        entry = history.get(role)
        if isinstance(entry, dict):
            try:
                return RoleCostModel(role=role, p50=int(entry["p50"]),
                                     p90=int(entry["p90"]),
                                     samples=int(entry.get("samples", 0)))
            except (KeyError, TypeError, ValueError):
                pass
        return RoleCostModel(role=role, p50=self._BOOTSTRAP_P50,
                             p90=self._BOOTSTRAP_P90, samples=0)

    # ------------------------------------------------------------------ lifecycle
    def open_task(self, task_id: str, ceiling: int,
                  roles: Mapping[str, int]) -> dict[str, Any]:
        """Open a task budget.

        ``roles`` maps role name -> number of expected agents.  Allocation is
        drawn from the per-role cost distributions and scaled to fit the
        ceiling while enforcing the Sol hard cap (≤15 % of ceiling) and the
        K3 floor (≥20 % of ceiling) — quota shaping happens at allocation
        time so enforcement never needs to guess intent.
        """
        if ceiling <= 0:
            raise ValueError("ceiling must be positive")
        sol_cap_tokens = int(ceiling * self.policy.sol_hard_cap())
        k3_floor_tokens = int(ceiling * self.policy.k3_floor())

        raw: dict[str, int] = {}
        for role, count in roles.items():
            model = self.role_cost_model(role)
            raw[role] = model.allocation(self.headroom) * max(1, int(count))
        # Shape: Sol capped, K3 floored, then scale the rest to the ceiling.
        sol_roles = {r for r in raw if r == "sol"}
        k3_roles = {r for r in raw if r in ("verifier", "reviewer",
                                            "plan_expander", "k3")}
        for role in sol_roles:
            raw[role] = min(raw[role], sol_cap_tokens)
        k3_sum = sum(raw[r] for r in k3_roles)
        if k3_roles and k3_sum < k3_floor_tokens:
            scale = k3_floor_tokens / max(1, k3_sum)
            for role in k3_roles:
                raw[role] = int(raw[role] * scale)
        other = [r for r in raw if r not in sol_roles | k3_roles]
        fixed = sum(raw[r] for r in sol_roles | k3_roles)
        other_sum = sum(raw[r] for r in other)
        available = max(0, ceiling - fixed)
        if other and other_sum > available:
            scale = available / max(1, other_sum)
            for role in other:
                raw[role] = int(raw[role] * scale)

        with self._mem_lock, file_lock(self.lock_path):
            doc = {"schema": "codex-loop-budget/v1", "task_id": task_id,
                   "ceiling": int(ceiling),
                   "allocated": {f"role:{r}": int(v) for r, v in raw.items()},
                   "consumed": {}, "state": BudgetState.NORMAL.value,
                   "state_changed_at": utc_now(), "pool_returned": 0,
                   "caps": {"sol_hard_cap_tokens": sol_cap_tokens,
                            "k3_floor_tokens": k3_floor_tokens}}
            self._save(doc)
        self._event("budget_opened", {"task_id": task_id, "ceiling": ceiling,
                                      "allocated": doc["allocated"]})
        return doc

    def register_agent(self, agent_id: str, role: str) -> int:
        """Carve an agent allocation out of its role allocation (or the
        returned pool).  Returns the agent's token allocation."""
        model = self.role_cost_model(role)
        want = model.allocation(self.headroom)
        with self._mem_lock, file_lock(self.lock_path):
            doc = self._load()
            if agent_id in doc.get("allocated", {}):
                return int(doc["allocated"][agent_id])
            role_key = f"role:{role}"
            role_pool = int(doc["allocated"].get(role_key, 0))
            pool = int(doc.get("pool_returned", 0))
            available = role_pool + pool
            if available <= 0 and doc.get("task_id") is not None:
                raise BudgetExceeded(
                    "task budget has no allocation left for role %s" % role,
                    agent_id=agent_id, remaining=0)
            # No active task budget is an explicit bootstrap/unbudgeted mode;
            # once open_task() exists, exhaustion is fail-closed above.
            grant = min(want, available) if available > 0 else want
            take_role = min(grant, role_pool)
            doc["allocated"][role_key] = role_pool - take_role
            doc["pool_returned"] = pool - (grant - take_role)
            doc["allocated"][agent_id] = grant
            doc["consumed"].setdefault(agent_id, 0)
            self._save(doc)
        return grant

    # ------------------------------------------------------------------ tracking
    def record_usage(self, agent_id: str, tokens: int) -> BudgetState:
        """Record token consumption (called from token-ledger ingest / the
        in-loop tracker).  Returns the resulting budget state.  Raises
        :class:`BudgetExceeded` when the agent's own allocation is breached
        AND the task is in BREAK — the caller converts this into the wrap-up
        / deny control event."""
        tokens = max(0, int(tokens))
        with self._mem_lock, file_lock(self.lock_path):
            doc = self._load()
            doc["consumed"][agent_id] = int(doc["consumed"].get(agent_id, 0)) + tokens
            state = self._recompute_state(doc)
            self._save(doc)
        if state is BudgetState.BREAK:
            allocated = int(doc["allocated"].get(agent_id, 0))
            consumed = int(doc["consumed"].get(agent_id, 0))
            if consumed > allocated:
                raise BudgetExceeded(
                    f"agent {agent_id} consumed {consumed} > allocation "
                    f"{allocated} with task budget in BREAK",
                    agent_id=agent_id, remaining=allocated - consumed)
        return state

    def _recompute_state(self, doc: dict[str, Any]) -> BudgetState:
        """Ladder + hysteresis: upward transitions are immediate and
        monotonic within a task; downward transitions require the cooldown to
        have elapsed AND usage to sit a full rung lower (prevents flapping)."""
        ceiling = max(1, int(doc.get("ceiling", 0) or 1))
        used = sum(int(v) for v in doc.get("consumed", {}).values())
        ratio = used / ceiling
        current = BudgetState(doc.get("state", "NORMAL"))
        target = (BudgetState.BREAK if ratio > self.break_at else
                  BudgetState.DEGRADE if ratio >= self.degrade_at else
                  BudgetState.THROTTLE if ratio >= self.throttle_at else
                  BudgetState.NORMAL)
        if _STATE_ORDER[target] > _STATE_ORDER[current]:
            doc["state"] = target.value
            doc["state_changed_at"] = utc_now()
            self._event("budget_state_change",
                        {"from": current.value, "to": target.value,
                         "ratio": round(ratio, 4)})
            return target
        if _STATE_ORDER[target] < _STATE_ORDER[current]:
            changed_at = float(doc.get("state_changed_at", 0) or 0)
            cooled = utc_now() - changed_at >= self.cooldown_s
            # Require a full-rung gap: e.g. leave THROTTLE only when back
            # under (throttle_at - one hysteresis band of 5 % of ceiling).
            band = 0.05
            thresholds = {BudgetState.THROTTLE: self.throttle_at,
                          BudgetState.DEGRADE: self.degrade_at,
                          BudgetState.BREAK: self.break_at}
            below_band = ratio < thresholds[current] - band
            if cooled and below_band:
                doc["state"] = target.value
                doc["state_changed_at"] = utc_now()
                self._event("budget_state_change",
                            {"from": current.value, "to": target.value,
                             "ratio": round(ratio, 4), "cooled": True})
                return target
        return current

    # ------------------------------------------------------------------ reclaim
    def reclaim(self, agent_id: str) -> int:
        """On agent completion, return unused allocation to the task pool.
        Returns the number of tokens reclaimed."""
        with self._mem_lock, file_lock(self.lock_path):
            doc = self._load()
            allocated = int(doc["allocated"].pop(agent_id, 0))
            consumed = int(doc["consumed"].get(agent_id, 0))
            unused = max(0, allocated - consumed)
            doc["pool_returned"] = int(doc.get("pool_returned", 0)) + unused
            self._recompute_state(doc)
            self._save(doc)
        if unused:
            self._event("budget_reclaimed",
                        {"agent_id": agent_id, "tokens": unused})
        return unused

    def extend_or_wrap_up(self, agent_id: str,
                          verification_state: str = "CONTINUE") -> BudgetDecision:
        """Deterministic near-limit decision (§5.2): extend iff the pool has
        headroom AND the agent's verification state is CONTINUE; else wrap-up."""
        with self._mem_lock, file_lock(self.lock_path):
            doc = self._load()
            pool = int(doc.get("pool_returned", 0))
            allocated = int(doc["allocated"].get(agent_id, 0))
            consumed = int(doc["consumed"].get(agent_id, 0))
            near_limit = consumed >= 0.85 * max(1, allocated)
            if not near_limit:
                return BudgetDecision(agent_id, "extend", 0, "not near limit")
            extension = min(pool, max(0, allocated) // 2)
            if extension > 0 and verification_state == "CONTINUE" \
                    and BudgetState(doc.get("state", "NORMAL")) is not BudgetState.BREAK:
                doc["pool_returned"] = pool - extension
                doc["allocated"][agent_id] = allocated + extension
                self._save(doc)
                self._event("budget_extended",
                            {"agent_id": agent_id, "tokens": extension})
                return BudgetDecision(agent_id, "extend", extension,
                                      "pool headroom + CONTINUE")
            return BudgetDecision(agent_id, "wrap_up", 0,
                                  "no headroom or verification != CONTINUE")

    # ------------------------------------------------------------------ in-loop signal
    def tracker_block(self, agent_id: str) -> str:
        """Render the BATS Budget-Tracker block for injection AFTER a tool
        result (appended at the end — the byte-stable prompt prefix is never
        mutated; R10)."""
        doc = self._load()
        allocated = int(doc["allocated"].get(agent_id, 0))
        consumed = int(doc["consumed"].get(agent_id, 0))
        ceiling = max(1, int(doc.get("ceiling", 0) or 1))
        used_total = sum(int(v) for v in doc.get("consumed", {}).values())
        state = doc.get("state", "NORMAL")
        remaining = max(0, allocated - consumed)
        lines = [
            "[BUDGET-TRACKER]",
            f"agent: {agent_id}",
            f"agent_tokens: used={consumed} remaining={remaining} "
            f"allocated={allocated}",
            f"task_tokens: used={used_total} ceiling={ceiling} "
            f"({used_total / ceiling:.0%})",
            f"budget_state: {state}",
        ]
        if state == BudgetState.THROTTLE.value:
            lines.append("guidance: compress context; prefer summaries over "
                         "raw reads; delegate bulk work.")
        elif state == BudgetState.DEGRADE.value:
            lines.append("guidance: only adjudication/dispatch actions; new "
                         "work MUST be delegated as packets.")
        elif state == BudgetState.BREAK.value:
            lines.append("guidance: budget exceeded — wrap up now: write the "
                         "short result and stop.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ tier 1
    def global_quota_check(self) -> dict[str, Any]:
        """Tier 1: evaluate the 5h shares against the Sol hard cap (15 %) and
        K3 floor (20 %).  Never actuates under the 2 M denominator floor."""
        report = read_json(self.paths.meter_report, None)
        result: dict[str, Any] = {"status": "UNKNOWN", "sol_ok": None, "k3_ok": None}
        if not isinstance(report, dict):
            result["status"] = "MISSING"
            return result
        windows = report.get("windows", {})
        w5h = windows.get("rolling_5h") if isinstance(windows, dict) else None
        if not isinstance(w5h, dict):
            result["status"] = "MALFORMED"
            return result
        denom = int(w5h.get("production_effective_tokens", 0) or 0)
        if denom < self.policy.minimum_denominator():
            result["status"] = "INSUFFICIENT_DATA"
            return result
        sol = float(w5h.get("sol_share_effective", w5h.get("share_effective", 0)) or 0)
        k3 = float(w5h.get("k3_share_effective", 0) or 0)
        result.update({
            "status": "OK", "sol_share": sol, "k3_share": k3,
            "sol_ok": sol <= self.policy.sol_hard_cap(),
            "k3_ok": k3 >= self.policy.k3_floor(),
            "sol_hard_cap": self.policy.sol_hard_cap(),
            "k3_floor": self.policy.k3_floor(),
            "denominator": denom,
        })
        return result

    # ------------------------------------------------------------------ misc
    def state(self) -> BudgetState:
        return BudgetState(self._load().get("state", "NORMAL"))

    def _event(self, event: str, detail: dict[str, Any]) -> None:
        append_ndjson(self.paths.budget_dir / "events.ndjson",
                      {"ts": utc_now(), "event": event, **detail})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="LOOP-F2 budget controller")
    ap.add_argument("cmd", choices=["status", "open", "record", "reclaim",
                                    "tracker", "quota"])
    ap.add_argument("--task", default="root")
    ap.add_argument("--ceiling", type=int, default=0)
    ap.add_argument("--roles", default="worker=16",
                    help="comma list role=count for 'open'")
    ap.add_argument("--agent", default="root")
    ap.add_argument("--tokens", type=int, default=0)
    args = ap.parse_args(argv)
    try:
        ctl = BudgetController()
    except PolicyError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    if args.cmd == "status":
        print(json.dumps(ctl._load(), indent=2, sort_keys=True))
        return 0
    if args.cmd == "open":
        roles = {}
        for chunk in args.roles.split(","):
            role, _, count = chunk.partition("=")
            roles[role.strip()] = int(count or 1)
        print(json.dumps(ctl.open_task(args.task, args.ceiling, roles)))
        return 0
    if args.cmd == "record":
        try:
            state = ctl.record_usage(args.agent, args.tokens)
        except BudgetExceeded as exc:
            print(json.dumps({"budget_exceeded": True, "agent": exc.agent_id,
                              "remaining": exc.remaining}))
            return 3
        print(json.dumps({"state": state.value}))
        return 0
    if args.cmd == "reclaim":
        print(json.dumps({"reclaimed": ctl.reclaim(args.agent)}))
        return 0
    if args.cmd == "tracker":
        print(ctl.tracker_block(args.agent))
        return 0
    if args.cmd == "quota":
        print(json.dumps(ctl.global_quota_check(), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
