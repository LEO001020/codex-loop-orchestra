#!/usr/bin/env python3
# ============================================================================
# refill_controller.py -- mechanical sustained-refill state machine
# Purpose : Own the refill contract between the lifecycle hooks (which observe
#           SubagentStart/SubagentStop/close) and the main agent / scheduler
#           (which holds the host spawn capability).  The controller never
#           calls the host spawn API itself: while pending/ready work exists
#           and active agents are below the low-water mark it writes a
#           machine-readable refill_required record carrying the deficit and
#           model_pool, and it keeps unfulfilled debt fail-visible.  Only
#           observed RUNNING agents count as effective concurrency: idle,
#           completed and shutdown_pending agents never count toward the
#           target, and they surface as reuse/assign suggestions first, then
#           as idle_reclaim_required + host_close_agent_required when they
#           cannot be reused or exceed the configurable idle_reclaim_threshold.
#           No command can claim "refilled" without host action; the record
#           clears only when the work queue empties or an explicit
#           release_finalize is issued.
# Input   : config [agents] normal_wave_concurrency (target),
#           normal_wave_low_water, max_concurrent_threads_per_session (cap);
#           data/refill/work_queue.json (maintained by CLI or ledger sync);
#           data/lifecycle/native_roster.json (running agents by model pool)
# Output  : data/refill/refill_state.json (authoritative, JSON), plus
#           data/refill/events.ndjson (append-only signal for the scheduler).
#           CLI: --status / --recompute / --queue-set / --queue-add /
#           --queue-remove / --queue-clear / --queue-sync-ledger /
#           --spawn-intent / --release-finalize / --resume
# ============================================================================
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from loop_config import config_bool, config_int, policy_value
except ImportError:  # pragma: no cover - CLI entry resolves harness/ itself
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from loop_config import config_bool, config_int, policy_value

POOLS = ("v4", "k3")
STATE_SCHEMA = "codex-loop-refill/v2"
QUEUE_SCHEMA = "codex-loop-refill-queue/v1"
RECLAIMABLE_STATUSES = ("idle", "completed", "shutdown_pending")
DEFAULT_TARGETS = {"v4": 16, "k3": 16}
DEFAULT_LOW_WATERS = {"v4": 12, "k3": 12}
DEFAULT_CAP = 50
DEFAULT_MAX_INITIALIZING = 4


def root_path() -> Path:
    return Path(os.environ.get("LOOP_ROOT", Path(__file__).resolve().parents[1])).resolve()


def classify_pool(model: Any) -> str:
    """Route an observed agent model into the v4 or k3 pool."""
    value = str(model or "").lower()
    return "k3" if ("k3" in value or "r1" in value or "reasoner" in value) else "v4"


@contextlib.contextmanager
def lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class RefillController:
    """Mechanical refill state machine (see module docstring)."""

    def __init__(self, root: Path | None = None):
        self.root = (Path(root) if root is not None else root_path()).resolve()
        self.refill_dir = self.root / "data" / "refill"
        self.state_path = self.refill_dir / "refill_state.json"
        self.queue_path = self.refill_dir / "work_queue.json"
        self.events_path = self.refill_dir / "events.ndjson"
        self.lock_path = self.refill_dir / ".refill.lock"
        self.roster_path = self.root / "data" / "lifecycle" / "native_roster.json"
        self.exec_roster_path = self.root / "data" / "lifecycle" / "exec_roster.json"
        self.throttle_state_path = self.refill_dir / "spawn_throttle_state.json"
        self.ledger_path = self.root / "data" / "progress_ledger.json"

    # ---- configuration ------------------------------------------------------
    def target(self, pool: str = "v4") -> int:
        value = policy_value("concurrency", "%s_target" % pool, None, self.root)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
        legacy = ("normal_k3_wave_concurrency" if pool == "k3"
                  else "normal_wave_concurrency")
        return max(0, config_int("agents", legacy, DEFAULT_TARGETS[pool], self.root))

    def target_total(self) -> int:
        value = policy_value("concurrency", "target_total", None, self.root)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
        return sum(self.target(pool) for pool in POOLS)

    def low_water(self, pool: str = "v4") -> int:
        value = policy_value("concurrency", "%s_low_water" % pool, None, self.root)
        if not isinstance(value, int) or isinstance(value, bool):
            legacy = ("normal_k3_wave_low_water" if pool == "k3"
                      else "normal_wave_low_water")
            value = config_int("agents", legacy, DEFAULT_LOW_WATERS[pool], self.root)
        return min(self.target(pool), value)

    def cap(self) -> int:
        return config_int("agents", "max_concurrent_threads_per_session", DEFAULT_CAP, self.root)

    def max_initializing(self) -> int:
        value = policy_value("spawn_throttle", "max_initializing", None, self.root)
        return max(1, value if isinstance(value, int) and not isinstance(value, bool)
                   else self.cap())

    def idle_reclaim_threshold(self) -> int:
        """Idle agents beyond this count (that cannot be reused) must be
        reclaimed before/while refilling.  Configurable per deployment."""
        return max(0, config_int("agents", "idle_reclaim_threshold", 0, self.root))

    def host_spawn_available(self) -> bool:
        return config_bool("agents", "refill_direct_spawn", False, self.root)

    # ---- work queue -----------------------------------------------------------
    def read_queue(self) -> dict[str, int]:
        doc = read_json(self.queue_path, {})
        pools = doc.get("pools", {}) if isinstance(doc, dict) else {}
        return {p: max(0, int(pools.get(p, 0) or 0)) for p in POOLS}

    def write_queue(self, pools: dict[str, int]) -> dict[str, int]:
        clean = {p: max(0, int(pools.get(p, 0) or 0)) for p in POOLS}
        with lock(self.lock_path):
            self._write_queue_unlocked(clean)
        return clean

    def _write_queue_unlocked(self, clean: dict[str, int]) -> None:
        atomic_json(self.queue_path, {"schema": QUEUE_SCHEMA, "pools": clean,
                                      "updated_at": time.time()})

    def _queue_mutate(self, fn) -> dict[str, int]:
        with lock(self.lock_path):
            pools = self.read_queue()
            fn(pools)
            clean = {p: max(0, int(pools.get(p, 0) or 0)) for p in POOLS}
            self._write_queue_unlocked(clean)
            return clean

    def queue_set(self, count: int, pool: str = "v4") -> dict[str, int]:
        return self._queue_mutate(lambda pools: pools.__setitem__(pool, int(count)))

    def queue_add(self, count: int, pool: str = "v4") -> dict[str, int]:
        return self._queue_mutate(lambda pools: pools.__setitem__(
            pool, pools.get(pool, 0) + int(count)))

    def queue_remove(self, count: int, pool: str = "v4") -> dict[str, int]:
        return self._queue_mutate(lambda pools: pools.__setitem__(
            pool, max(0, pools.get(pool, 0) - int(count))))

    def queue_clear(self) -> dict[str, int]:
        return self.write_queue({p: 0 for p in POOLS})

    def queue_sync_ledger(self, pool: str = "v4") -> dict[str, int]:
        """Count DISPATCHABLE packets from the progress ledger into one pool."""
        led = read_json(self.ledger_path, {"packets": {}})
        ready = sum(1 for item in led.get("packets", {}).values()
                    if isinstance(item, dict) and item.get("state") == "DISPATCHABLE")
        return self._queue_mutate(lambda pools: pools.__setitem__(pool, ready))

    # ---- observed state ---------------------------------------------------------
    def read_roster_state(self) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, list[str]]]]:
        """Combine Desktop-native and headless exec lifecycle state.

        Only ``running`` counts as effective concurrency.  ``idle`` (UI open, no
        active turn), ``completed`` and ``shutdown_pending`` hold host slots but
        never count toward the target. ``initializing`` reserves birth capacity
        but is not effective concurrency. Terminal headless jobs hold no host slot
        and therefore are not added to the reclaimable Desktop counts.
        """
        roster = read_json(self.roster_path, {"agents": {}})
        keys = ("initializing", "running", "idle", "completed", "shutdown_pending")
        counts = {p: {key: 0 for key in keys}
                  for p in POOLS}
        ids = {p: {key: [] for key in keys}
               for p in POOLS}
        for item in (roster.get("agents") or {}).values():
            if not isinstance(item, dict):
                continue
            pool = classify_pool(item.get("model"))
            status = str(item.get("status") or "")
            key = status if status in counts[pool] else None
            if key is None:
                continue
            counts[pool][key] += 1
            aid = item.get("agent_id") or item.get("task_name") or "unknown"
            ids[pool][key].append(str(aid))

        # A PreToolUse birth is initializing until SubagentStart binds it.
        for item in (roster.get("pending") or []):
            if not isinstance(item, dict) or item.get("status") != "pending_start":
                continue
            role = str(item.get("agent_role") or "")
            pool = ("k3" if role in {"verifier", "reviewer"}
                    else classify_pool(item.get("model")))
            aid = item.get("agent_id") or item.get("tool_use_id") or item.get("task_name") or "pending"
            counts[pool]["initializing"] += 1
            ids[pool]["initializing"].append(str(aid))

        # Headless WSL `codex exec` workers are the steady-state execution plane.
        exec_roster = read_json(self.exec_roster_path, {"jobs": {}})
        for item in (exec_roster.get("jobs") or {}).values():
            if not isinstance(item, dict):
                continue
            state = str(item.get("state") or "")
            key = "initializing" if state == "starting" else ("running" if state == "running" else None)
            if key is None:
                continue
            pool = classify_pool(item.get("model"))
            aid = item.get("run_id") or item.get("packet_id") or item.get("task_name") or "exec"
            counts[pool][key] += 1
            ids[pool][key].append(str(aid))
        return counts, ids


    def read_active(self) -> dict[str, int]:
        """Effective concurrency: running agents only, per model pool."""
        counts, _ = self.read_roster_state()
        return {p: counts[p]["running"] for p in POOLS}

    def read_state(self) -> dict[str, Any]:
        return read_json(self.state_path, {"schema": STATE_SCHEMA, "finalized": False,
                                           "refill_required": False})

    # ---- deficit math ------------------------------------------------------------
    @staticmethod
    def split_deficit(total: int, pending: dict[str, int]) -> dict[str, int]:
        """Hamilton (largest remainder) split of the deficit across pools."""
        out = {p: 0 for p in POOLS}
        pools = [p for p in POOLS if pending.get(p, 0) > 0]
        if total <= 0 or not pools:
            return out
        grand = sum(pending[p] for p in pools)
        floors = {p: total * pending[p] // grand for p in pools}
        remainders = {p: total * pending[p] % grand for p in pools}
        remaining = total - sum(floors.values())
        for p in sorted(pools, key=lambda p: remainders[p], reverse=True):
            if remaining <= 0:
                break
            if floors[p] < pending[p]:
                floors[p] += 1
                remaining -= 1
        for p in pools:
            floors[p] = min(floors[p], pending[p])
        for p in POOLS:
            out[p] = floors.get(p, 0)
        return out

    # ---- recompute -----------------------------------------------------------------
    def recompute(self, emit: bool = True) -> dict[str, Any]:
        preferred_targets = {p: self.target(p) for p in POOLS}
        target_total = self.target_total()
        preferred_low_waters = {p: self.low_water(p) for p in POOLS}
        cap = self.cap()
        max_initializing = self.max_initializing()
        threshold = self.idle_reclaim_threshold()
        pending = self.read_queue()
        pending_total = sum(pending.values())
        targets = dict(preferred_targets)
        low_waters = dict(preferred_low_waters)
        shared_total = policy_value("concurrency", "target_total", None, self.root)
        active_pools = [p for p in POOLS if pending[p] > 0]
        # 12/4 is a preference, not a partition. If only one pool has work it
        # borrows the other pool's reservation and may use the full shared 16.
        if isinstance(shared_total, int) and not isinstance(shared_total, bool):
            if len(active_pools) == 1:
                only = active_pools[0]
                targets = {p: (target_total if p == only else 0) for p in POOLS}
                shared_low = min(target_total, sum(preferred_low_waters.values()))
                low_waters = {p: (shared_low if p == only else 0) for p in POOLS}
        counts, ids = self.read_roster_state()
        initializing = {p: counts[p]["initializing"] for p in POOLS}
        running = {p: counts[p]["running"] for p in POOLS}
        idle = {p: counts[p]["idle"] for p in POOLS}
        completed = {p: counts[p]["completed"] for p in POOLS}
        shutdown_pending = {p: counts[p]["shutdown_pending"] for p in POOLS}
        reclaimable = {p: idle[p] + completed[p] + shutdown_pending[p] for p in POOLS}
        initializing_total = sum(initializing.values())
        running_total = sum(running.values())
        reclaimable_total = sum(reclaimable.values())
        occupied_total = initializing_total + running_total + reclaimable_total
        prior = self.read_state()
        throttle = read_json(self.throttle_state_path, {})
        blocked_until = float(throttle.get("blocked_until", 0) or 0)
        backoff_active = blocked_until > time.time()
        finalized = bool(prior.get("finalized"))
        queue_empty = pending_total <= 0

        # Per-pool watermarks: a pool is refill-required only when IT has
        # pending work and its own running count is below its own low water.
        required = {p: bool(not queue_empty and not finalized and
                            pending[p] > 0 and running[p] < low_waters[p])
                    for p in POOLS}
        required_any = any(required.values())
        # Debt is based on effective RUNNING agents only. Initializing births do
        # not clear debt, but they reserve spawn capacity to prevent overshoot.
        raw_debt = {p: (min(pending[p], max(0, targets[p] - running[p]))
                        if required[p] else 0)
                    for p in POOLS}
        raw_spawn = {p: max(0, raw_debt[p] - initializing[p]) for p in POOLS}
        host_capacity = max(0, cap - occupied_total)
        target_capacity = max(0, target_total - running_total - initializing_total)
        birth_capacity = max(0, max_initializing - initializing_total)
        spawn_capacity_now = min(host_capacity, target_capacity, birth_capacity)
        if backoff_active:
            spawn_capacity_now = 0
        deficit_total = min(sum(raw_spawn.values()), spawn_capacity_now) if required_any else 0
        deficit = self.split_deficit(deficit_total, raw_spawn)
        debt_total = sum(raw_debt.values()) if required_any else 0
        model_pool = sorted(p for p in POOLS
                            if raw_debt[p] > 0 or (required[p] and pending[p] > 0))

        # Reuse/assign first: idle agents in pools with pending work are
        # suggested for assignment, up to the number of pending items.
        reuse_ids = {p: (list(ids[p]["idle"][:min(len(ids[p]["idle"]),
                                                  pending[p])])
                         if required[p] else [])
                     for p in POOLS}
        reuse_total = sum(len(v) for v in reuse_ids.values())
        reuse_required = bool(required_any and reuse_total > 0)
        # Per-pool reclaim: excess idle (beyond reuse) plus completed and
        # shutdown_pending agents of pools that actually need refilling.
        excess_ids = {p: ((ids[p]["idle"][len(reuse_ids[p]):] +
                           ids[p]["completed"] + ids[p]["shutdown_pending"])
                          if required[p] else [])
                      for p in POOLS}
        excess_total = sum(len(v) for v in excess_ids.values())
        idle_reclaim_required_pool = {
            p: bool(required[p] and len(excess_ids[p]) > threshold)
            for p in POOLS}
        idle_reclaim_required = any(idle_reclaim_required_pool.values())
        host_close_agent = sorted({aid for p in POOLS
                                   if idle_reclaim_required_pool[p]
                                   for aid in excess_ids[p]})
        # Reclaim-first capacity case: slots are full but reclaimable agents hold
        # them; the scheduler is allowed (and required) to close then refill.
        reclaim_first = bool(required_any and occupied_total >= cap
                             and reclaimable_total > 0)
        if reclaim_first and not idle_reclaim_required:
            idle_reclaim_required = True
            host_close_agent = sorted({aid
                                       for pool in POOLS
                                       for aid in (ids[pool]["idle"] +
                                                   ids[pool]["completed"] +
                                                   ids[pool]["shutdown_pending"])})

        if queue_empty:
            reason = "queue_empty"
        elif finalized:
            reason = "release_finalize"
        elif required_any and backoff_active:
            reason = "spawn_backoff"
        elif required_any and deficit_total == 0 and initializing_total >= max_initializing:
            reason = "initializing_cap"
        elif required_any and deficit_total == 0 and running_total + initializing_total >= target_total:
            reason = "at_total_target"
        elif required_any and deficit_total == 0 and occupied_total >= cap:
            reason = "at_cap"
        elif required_any:
            reason = "below_low_water"
        else:
            reason = "at_or_above_low_water"
        state = {
            "schema": STATE_SCHEMA,
            "target": targets,
            "preferred_target": preferred_targets,
            "target_total": target_total,
            "configured_target_sum": sum(preferred_targets.values()),
            "low_water": low_waters,
            "preferred_low_water": preferred_low_waters,
            "low_water_total": sum(low_waters.values()),
            "cap": cap,
            "idle_reclaim_threshold": threshold,
            "max_initializing": max_initializing,
            "effective_concurrency": running_total,
            "initializing": {"total": initializing_total, **initializing},
            "active": {"total": running_total, **running},
            "idle": {"total": sum(idle.values()), **idle},
            "completed": {"total": sum(completed.values()), **completed},
            "shutdown_pending": {"total": sum(shutdown_pending.values()), **shutdown_pending},
            "occupied": {"total": occupied_total,
                         **{p: running[p] + reclaimable[p] for p in POOLS}},
            "reclaimable": {"total": reclaimable_total, **reclaimable},
            "pending": {"total": pending_total, **pending},
            "refill_required": required_any,
            "refill_required_by_pool": required,
            "reuse_required": reuse_required,
            "reuse": reuse_ids,
            "idle_reclaim_required": idle_reclaim_required,
            "idle_reclaim_required_by_pool": idle_reclaim_required_pool,
            "host_close_agent_required": idle_reclaim_required,
            "host_close_agent": host_close_agent if idle_reclaim_required else [],
            "reclaim_first": reclaim_first,
            "spawn_capacity_now": spawn_capacity_now,
            "spawn_capacity": {"host": host_capacity, "target": target_capacity,
                               "initializing": birth_capacity},
            "spawn_throttle": {
                "backoff_active": backoff_active,
                "blocked_until": blocked_until,
                "last_spawn_at": throttle.get("last_spawn_at"),
                "last_health": throttle.get("last_health"),
                "last_error": throttle.get("last_error"),
            },
            "deficit": {"total": deficit_total, **deficit},
            "model_pool": model_pool,
            "debt_held": {"total": deficit_total if required_any else 0,
                          **{p: (deficit[p] if required[p] else 0)
                             for p in POOLS}},
            "unfulfilled_demand": {"total": debt_total,
                                   **{p: (raw_debt[p] if required[p] else 0)
                                      for p in POOLS}},
            "queue_empty": queue_empty,
            "finalized": finalized,
            "reason": reason,
            "spawn_mechanism": ("host_spawn_required" if not self.host_spawn_available()
                                else "host_direct"),
            "host_spawn_available": self.host_spawn_available(),
            "refilled": False,
            "updated_at": time.time(),
        }
        if emit:
            with lock(self.lock_path):
                atomic_json(self.state_path, state)
                slim = {k: state[k] for k in
                         ("refill_required", "deficit", "model_pool", "reason",
                          "initializing", "active", "idle", "completed", "shutdown_pending",
                          "occupied", "reclaimable", "pending", "debt_held",
                          "unfulfilled_demand",
                          "reuse_required", "reuse", "idle_reclaim_required",
                          "idle_reclaim_required_by_pool", "host_close_agent",
                          "reclaim_first", "spawn_capacity_now", "spawn_throttle",
                          "refill_required_by_pool")}
                if required_any:
                    self._append_event("refill_required", slim)
                    if reuse_required:
                        self._append_event("reuse_required",
                                           {"reuse": reuse_ids, "deficit": deficit,
                                            "required_by_pool": required})
                    if idle_reclaim_required:
                        self._append_event("idle_reclaim_required",
                                           {"host_close_agent": host_close_agent,
                                            "reclaim_first": reclaim_first})
                elif prior.get("refill_required"):
                    self._append_event("refill_clear", slim)
            return state

    # ---- lifecycle commands -----------------------------------------------------------
    def release_finalize(self) -> dict[str, Any]:
        """Explicit finalization: clear refill_required and debt until resumed."""
        state = self.read_state()
        state["finalized"] = True
        atomic_json(self.state_path, state)
        return self.recompute(emit=True)

    def resume(self) -> dict[str, Any]:
        state = self.read_state()
        state["finalized"] = False
        atomic_json(self.state_path, state)
        return self.recompute(emit=True)

    def spawn_intent(self, count: int, pool: str = "v4") -> dict[str, Any]:
        """Record that the host intends to spawn; NEVER clears debt or deficit.

        Only observed running agents (native roster) reduce the deficit, so an
        intent that never materializes stays fail-visible in refill_state.
        """
        with lock(self.lock_path):
            self._append_event("spawn_intent", {"count": int(count), "pool": pool,
                                                "debt_cleared": False})
        return self.read_state()

    def _append_event(self, event: str, detail: dict[str, Any]) -> None:
        self.refill_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"ts": time.time(), "event": event, **detail},
                                    ensure_ascii=False, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Mechanical sustained-refill controller. Prints refill_state.json "
                    "as JSON after the requested action.")
    ap.add_argument("--root", type=Path, default=None,
                    help="LOOP root (default: $LOOP_ROOT or the package root)")
    ap.add_argument("--status", action="store_true",
                    help="print the current refill state (read-only)")
    ap.add_argument("--recompute", action="store_true",
                    help="recompute and rewrite the refill state")
    ap.add_argument("--queue-set", type=int, metavar="N",
                    help="set the work queue count for --pool")
    ap.add_argument("--queue-add", type=int, metavar="N",
                    help="add N pending work items for --pool")
    ap.add_argument("--queue-remove", type=int, metavar="N",
                    help="remove N pending work items for --pool")
    ap.add_argument("--queue-clear", action="store_true",
                    help="empty the work queue (clears refill_required)")
    ap.add_argument("--queue-sync-ledger", action="store_true",
                    help="count DISPATCHABLE packets into --pool from the progress ledger")
    ap.add_argument("--pool", choices=list(POOLS), default=None,
                    help="model pool for queue/intent commands (default: v4)")
    ap.add_argument("--spawn-intent", type=int, metavar="N",
                    help="record host spawn intent for N agents (debt is NOT cleared)")
    ap.add_argument("--release-finalize", action="store_true",
                    help="explicitly clear refill_required and debt (finalize)")
    ap.add_argument("--resume", action="store_true",
                    help="undo --release-finalize")
    args = ap.parse_args(argv)

    ctl = RefillController(args.root)
    pool = args.pool or "v4"
    mutated = False
    if args.queue_set is not None:
        ctl.queue_set(args.queue_set, pool)
        mutated = True
    if args.queue_add is not None:
        ctl.queue_add(args.queue_add, pool)
        mutated = True
    if args.queue_remove is not None:
        ctl.queue_remove(args.queue_remove, pool)
        mutated = True
    if args.queue_clear:
        ctl.queue_clear()
        mutated = True
    if args.queue_sync_ledger:
        ctl.queue_sync_ledger(pool)
        mutated = True
    if args.spawn_intent is not None:
        ctl.spawn_intent(args.spawn_intent, pool)
    if args.release_finalize:
        ctl.release_finalize()
        mutated = True
    if args.resume:
        ctl.resume()
        mutated = True

    if args.status and not mutated:
        out = ctl.read_state()
    else:
        out = ctl.recompute(emit=True)
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
