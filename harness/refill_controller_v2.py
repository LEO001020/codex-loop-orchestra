#!/usr/bin/env python3
"""refill_controller_v2.py — Pool-aware sustained refill (P0-6 fix, §2.6).

Fixes the demand side of the K3 starvation:

* **``queue_sync_ledger`` is pool-aware and total.**  The shipped
  ``refill_controller.py:203-208`` counted DISPATCHABLE packets into a single
  ``--pool`` argument defaulting to ``"v4"``, so K3-pool demand was never
  written and the configured K3 reservation was permanently borrowed by V4.
  v2 derives the pool per packet from role/``pool_hint`` metadata
  (verifier/reviewer/plan_expander → ``k3``, else ``v4``) and counts the K3
  work states (``L2_VERIFY``, ``EXPAND_K3``, ``L2_RANK``) as K3 demand.  The
  sync writes BOTH pools in one atomic mutation; the ``--pool`` flag no
  longer applies to sync (it stays for manual ``queue-add``/``remove`` ops).
* **Configured K3 target with actual demand matching:** targets/low-waters come
  fail-closed from ``refill_policy.toml`` (the only concurrency authority;
  policy unreadable => :class:`PolicyError`, never the silent divergent
  16/16-vs-48 defaults of v1).  A pool refills only when it has pending work
  AND its running count is below its low water — demand-backed, never idle
  filling (anti-pattern canary: zero demand ⇒ zero K3 spawns).
* **Reservations borrowable (true):** when only one pool has pending work it
  may borrow the other pool's reservation up to ``target_total``; watermark
  logic reclaims borrowed slots naturally once K3 demand exists.
* **Policy-version stamping:** ``refill_state.json`` records the policy
  version + mtime it was computed from; a mismatch on read forces a
  recompute, so a stale state file can never silently win (P0-8.3).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Final, Mapping

try:
    from orchestration_common import (LoopPaths, PolicyError, RefillPolicy,
                                      append_ndjson, atomic_write_json, file_lock,
                                      get_logger, read_json, utc_now)
except ImportError:  # pragma: no cover - direct CLI execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from orchestration_common import (LoopPaths, PolicyError, RefillPolicy,
                                      append_ndjson, atomic_write_json, file_lock,
                                      get_logger, read_json, utc_now)

__all__ = [
    "POOLS",
    "K3_ROLES",
    "K3_WORK_STATES",
    "pool_for_packet",
    "RefillControllerV2",
    "main",
]

log = get_logger("loop.refill_controller_v2")

POOLS: Final[tuple[str, str]] = ("v4", "k3")
STATE_SCHEMA: Final[str] = "codex-loop-refill/v3"
QUEUE_SCHEMA: Final[str] = "codex-loop-refill-queue/v2"

#: Roles whose packets are K3 demand.
K3_ROLES: Final[frozenset[str]] = frozenset({"verifier", "reviewer", "plan_expander"})
#: v2 state-machine states that ARE K3 work (count as K3 demand while active).
K3_WORK_STATES: Final[frozenset[str]] = frozenset(
    {"L2_VERIFY", "L2_RANK", "EXPAND_K3", "L2_VERIFY_PENDING"})
NATIVE_RUNNING_TTL_SECONDS: Final[float] = 1800.0
OBSERVER_URL: Final[str] = "http://127.0.0.1:8765/api/status"


def _process_start_token(pid: Any) -> int | None:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if (not ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(code)) or code.value != 259):
                return None
            creation = wintypes.FILETIME(); exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME(); user = wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return ((int(creation.dwHighDateTime) << 32)
                    | int(creation.dwLowDateTime))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        return int(Path("/proc/%d/stat" % pid).read_text(
            encoding="ascii").split()[21])
    except (OSError, ValueError, IndexError):
        return None


def _process_generation_alive(pid: Any, expected: Any) -> bool:
    actual = _process_start_token(pid)
    if actual is None:
        return False
    try:
        return expected is not None and actual == int(expected)
    except (TypeError, ValueError):
        return False


def pool_for_packet(item: Mapping[str, Any]) -> str:
    """Mechanically derive the pool for a ledger packet entry.

    Priority: explicit ``pool_hint`` > role (verifier/reviewer/plan_expander
    → k3) > default v4.  Zero-model, deterministic (§2.6.1)."""
    hint = item.get("pool_hint")
    if isinstance(hint, str) and hint in POOLS:
        return hint
    role = str(item.get("role") or "")
    return "k3" if role in K3_ROLES else "v4"


def classify_pool(model: Any) -> str:
    """Route an *observed* agent model string into v4/k3 (accounting only)."""
    value = str(model or "").lower()
    return "k3" if ("k3" in value or "r1" in value or "reasoner" in value) else "v4"


class RefillControllerV2:
    """Mechanical, pool-aware refill state machine.

    The controller never calls the host spawn API: it publishes
    machine-readable ``refill_required`` records carrying per-pool deficits
    and keeps unfulfilled debt fail-visible (unchanged v1 contract)."""

    def __init__(self, paths: LoopPaths | None = None) -> None:
        self.paths = paths or LoopPaths.resolve()
        self.policy = RefillPolicy.load(self.paths)   # fail-closed (PolicyError)
        self.refill_dir = self.paths.refill_dir
        self.state_path = self.refill_dir / "refill_state.json"
        self.queue_path = self.refill_dir / "work_queue.json"
        self.events_path = self.refill_dir / "events.ndjson"
        self.lock_path = self.refill_dir / ".refill.lock"
        self.roster_path = self.paths.data / "lifecycle" / "native_roster.json"
        self.exec_roster_path = self.paths.data / "lifecycle" / "exec_roster.json"
        self.parent_sessions_path = self.refill_dir / "parent_sessions.json"
        self.observer_status = "unavailable"

    # ------------------------------------------------------------------ config
    def targets(self) -> dict[str, int]:
        return {"v4": self.policy.v4_target(), "k3": self.policy.k3_target()}

    def low_waters(self) -> dict[str, int]:
        return {"v4": min(self.policy.v4_target(), self.policy.v4_low_water()),
                "k3": min(self.policy.k3_target(), self.policy.k3_low_water())}

    def target_total(self) -> int:
        return self.policy.target_total()

    # ------------------------------------------------------------------ queue
    def read_queue(self) -> dict[str, int]:
        doc = read_json(self.queue_path, {}) or {}
        pools = doc.get("pools", {}) if isinstance(doc, dict) else {}
        return {p: max(0, int(pools.get(p, 0) or 0)) for p in POOLS}

    def read_parent_queue(self) -> dict[str, dict[str, Any]]:
        doc = read_json(self.queue_path, {}) or {}
        source = doc.get("parents", {}) if isinstance(doc, dict) else {}
        out: dict[str, dict[str, Any]] = {}
        source_items = source.items() if isinstance(source, dict) else ()
        for parent_id, row in source_items:
            if not isinstance(row, dict):
                continue
            pools = {p: max(0, int(row.get(p, 0) or 0)) for p in POOLS}
            out[str(parent_id)] = {
                "total": sum(pools.values()), **pools,
                "manifest_id": row.get("manifest_id"),
            }
        return out

    def _write_queue_unlocked(
            self, pools: Mapping[str, int],
            parents: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, int]:
        clean = {p: max(0, int(pools.get(p, 0) or 0)) for p in POOLS}
        if parents is None:
            parents = self.read_parent_queue()
        clean_parents: dict[str, dict[str, Any]] = {}
        for parent_id, row in parents.items():
            parent_pools = {p: max(0, int(row.get(p, 0) or 0)) for p in POOLS}
            clean_parents[str(parent_id)] = {
                "total": sum(parent_pools.values()), **parent_pools,
                "manifest_id": row.get("manifest_id"),
            }
        atomic_write_json(self.queue_path,
                          {"schema": QUEUE_SCHEMA, "pools": clean,
                           "parents": clean_parents,
                           "updated_at": utc_now()})
        return clean

    def _queue_mutate(self, fn: Callable[[dict[str, int]], None]) -> dict[str, int]:
        with file_lock(self.lock_path):
            pools = self.read_queue()
            fn(pools)
            return self._write_queue_unlocked(pools)

    def queue_set(self, count: int, pool: str) -> dict[str, int]:
        return self._queue_mutate(lambda p: p.__setitem__(pool, int(count)))

    def queue_add(self, count: int, pool: str) -> dict[str, int]:
        return self._queue_mutate(
            lambda p: p.__setitem__(pool, p.get(pool, 0) + int(count)))

    def queue_remove(self, count: int, pool: str) -> dict[str, int]:
        return self._queue_mutate(
            lambda p: p.__setitem__(pool, max(0, p.get(pool, 0) - int(count))))

    def queue_clear(self) -> dict[str, int]:
        with file_lock(self.lock_path):
            return self._write_queue_unlocked({p: 0 for p in POOLS})

    def queue_sync_ledger(self) -> dict[str, int]:
        """THE P0-6 FIX: pool-aware, total ledger sync.

        Counts every DISPATCHABLE packet into its mechanically derived pool
        and every K3 work state as K3 demand, then replaces BOTH pool counts
        atomically.  No ``--pool`` parameter: sync is total by definition.
        """
        led = read_json(self.paths.ledger, {"packets": {}}) or {"packets": {}}
        parent_doc = read_json(self.parent_sessions_path, {"parents": {}}) or {"parents": {}}
        registered = parent_doc.get("parents", {}) if isinstance(parent_doc, dict) else {}
        ready = {p: 0 for p in POOLS}
        parent_ready: dict[str, dict[str, Any]] = {}
        for pid, item in led.get("packets", {}).items():
            if not isinstance(item, dict):
                continue
            state = item.get("state")
            if state == "DISPATCHABLE":
                pool = pool_for_packet(item)
                parent_id = item.get("parent_session_id")
                if parent_id:
                    parent = registered.get(str(parent_id)) if isinstance(registered, dict) else None
                    if (not isinstance(parent, dict) or parent.get("active") is not True
                            or parent.get("manifest_id") != item.get("manifest_id")):
                        # Parent demand is manifest-scoped.  Equality is
                        # intentionally strict: legacy None/None remains valid,
                        # while a one-sided missing id cannot claim work from a
                        # different admission generation.
                        continue
                    row = parent_ready.setdefault(str(parent_id), {
                        "v4": 0, "k3": 0,
                        "manifest_id": item.get("manifest_id"),
                    })
                    row[pool] += 1
                ready[pool] += 1
            elif state in K3_WORK_STATES:
                ready["k3"] += 1
        with file_lock(self.lock_path):
            result = self._write_queue_unlocked(ready, parent_ready)
        log.info("queue_sync_ledger: %s", result)
        return result

    # ------------------------------------------------------------------ observed state
    def _read_roster_state(self) -> tuple[dict[str, dict[str, int]],
                                          dict[str, dict[str, int]]]:
        """Running/initializing per pool from native + exec rosters.
        Only ``running`` counts as effective concurrency (v1 contract)."""
        keys = ("initializing", "running", "idle", "completed", "shutdown_pending")
        counts = {p: {k: 0 for k in keys} for p in POOLS}
        parents: dict[str, dict[str, int]] = {}

        def remember_parent(item: Mapping[str, Any], pool: str, key: str) -> None:
            parent_id = item.get("parent_session_id")
            if not parent_id or key not in {"running", "initializing"}:
                return
            row = parents.setdefault(str(parent_id), {
                "running": 0, "initializing": 0,
                "v4_running": 0, "k3_running": 0,
                "v4_initializing": 0, "k3_initializing": 0,
            })
            row[key] += 1
            row[f"{pool}_{key}"] += 1

        now = time.time()
        roster = read_json(self.roster_path, {"agents": {}}) or {"agents": {}}
        for item in (roster.get("agents") or {}).values():
            if not isinstance(item, dict):
                continue
            role = str(item.get("agent_role") or item.get("role") or "")
            pool = ("k3" if role in K3_ROLES else "v4") if role else classify_pool(item.get("model"))
            status = str(item.get("status") or "")
            if status == "running":
                try:
                    observed_at = float(item.get("updated_at") or
                                        item.get("started_at") or 0)
                except (TypeError, ValueError):
                    observed_at = 0.0
                if observed_at <= 0 or now - observed_at > NATIVE_RUNNING_TTL_SECONDS:
                    continue
            if status in counts[pool]:
                counts[pool][status] += 1
                remember_parent(item, pool, status)
        for item in (roster.get("pending") or []):
            if not isinstance(item, dict) or item.get("status") != "pending_start":
                continue
            role = str(item.get("agent_role") or "")
            pool = ("k3" if role in K3_ROLES else "v4") if role else classify_pool(item.get("model"))
            counts[pool]["initializing"] += 1
            remember_parent(item, pool, "initializing")
        exec_roster = read_json(self.exec_roster_path, {"jobs": {}}) or {"jobs": {}}
        for item in (exec_roster.get("jobs") or {}).values():
            if not isinstance(item, dict):
                continue
            state = str(item.get("state") or "")
            key = ("initializing" if state == "starting"
                   else "running" if state == "running" else None)
            if key is None:
                continue
            supervisor_alive = _process_generation_alive(
                item.get("supervisor_pid"), item.get("supervisor_proc_start_ticks"))
            worker_alive = _process_generation_alive(
                item.get("os_pid"), item.get("worker_proc_start_ticks"))
            if not supervisor_alive or (key == "running" and not worker_alive):
                continue
            role = str(item.get("role") or item.get("agent_role") or "")
            pool = ("k3" if role in K3_ROLES else "v4") if role else classify_pool(item.get("model"))
            counts[pool][key] += 1
            remember_parent(item, pool, key)
        # The 8765 observer is the existing cross-plane aggregator.  Current
        # Desktop tasks can predate the lifecycle hook loaded by this root, so
        # native_roster alone may undercount them; WSL and Windows rosters are
        # also physically separate.  Overlay (never add) a fresh snapshot for
        # this exact LOOP root so an imported parent backlog cannot overfill by
        # mistaking already-running Desktop/peer-plane work for free slots.
        observer = self._read_observer_snapshot()
        if observer is not None:
            observed_pools = observer.get("pools", {})
            for pool in POOLS:
                # A fresh observer is the cross-plane authority.  Taking max
                # with a local stale native row could resurrect a crashed
                # Desktop child and suppress real refill debt.
                counts[pool]["running"] = max(
                    0, int((observed_pools or {}).get(pool, 0) or 0))
            for parent_id, observed in (observer.get("parents", {}) or {}).items():
                if not isinstance(observed, dict):
                    continue
                row = parents.setdefault(str(parent_id), {
                    "running": 0, "initializing": 0,
                    "v4_running": 0, "k3_running": 0,
                    "v4_initializing": 0, "k3_initializing": 0,
                })
                row["running"] = max(0, int(observed.get("running", 0) or 0))
                for pool in POOLS:
                    row[f"{pool}_running"] = max(0, int(observed.get(pool, 0) or 0))
        return counts, parents

    def _read_observer_snapshot(self) -> dict[str, Any] | None:
        self.observer_status = "unavailable"
        try:
            with urllib.request.urlopen(OBSERVER_URL, timeout=0.35) as response:
                doc = json.loads(response.read(1024 * 1024).decode("utf-8"))
        except (OSError, ValueError, TimeoutError):
            return None
        if not isinstance(doc, dict) or doc.get("freshness") not in {
                "LIVE", "QUIET", "ESTIMATED"}:
            self.observer_status = "stale"
            return None
        try:
            if time.time() - float(doc.get("timestamp", 0) or 0) > 5.0:
                return None
        except (TypeError, ValueError):
            self.observer_status = "stale"
            return None
        local = str(self.paths.root.resolve()).replace("\\", "/").casefold()
        advertised = {
            str(doc.get(key) or "").replace("\\", "/").casefold()
            for key in ("root", "windows_root")
        }
        same_package_wsl = (os.name != "nt" and local.endswith("/codex-loop-s-f2")
                            and any(path.endswith("/codex-loop-s-f2")
                                    for path in advertised))
        if local not in advertised and not same_package_wsl:
            self.observer_status = "foreign_root"
            return None
        self.observer_status = "fresh"
        return doc

    def read_roster_counts(self) -> dict[str, dict[str, int]]:
        return self._read_roster_state()[0]

    # ------------------------------------------------------------------ recompute
    def recompute(self, emit: bool = True) -> dict[str, Any]:
        """Recompute the refill state with borrowable reservations.

        Demand matching: a pool is refill-required only when IT has pending
        work and its own running count is below its own low water.  With
        ``reservations_borrowable=true`` and only one active pool, that pool
        may use the full ``target_total``; as soon as the other pool gains
        pending work its preferred reservation is honoured again (watermark
        reclaim; §2.6.3)."""
        preferred_targets = self.targets()
        preferred_low_waters = self.low_waters()
        target_total = self.target_total()
        borrowable = self.policy.reservations_borrowable()
        pending = self.read_queue()
        pending_total = sum(pending.values())
        targets = dict(preferred_targets)
        low_waters = dict(preferred_low_waters)
        active_pools = [p for p in POOLS if pending[p] > 0]
        if borrowable and len(active_pools) == 1:
            only = active_pools[0]
            targets = {p: (target_total if p == only else 0) for p in POOLS}
            shared_low = min(target_total, sum(preferred_low_waters.values()))
            low_waters = {p: (shared_low if p == only else 0) for p in POOLS}

        counts, parent_counts = self._read_roster_state()
        running = {p: counts[p]["running"] for p in POOLS}
        initializing = {p: counts[p]["initializing"] for p in POOLS}
        running_total = sum(running.values())
        initializing_total = sum(initializing.values())
        prior = read_json(self.state_path, {}) or {}
        finalized = bool(prior.get("finalized"))
        queue_empty = pending_total <= 0

        # Parent targets are dialogue-level, not a second reservation for one
        # model family. If a parent still has real K3 work while the preferred
        # K3 pool is full but global capacity is available in V4, its debt must
        # borrow that idle family capacity or the parent can be stuck at 18/20
        # with parent deficit > 0 but global k3 deficit == 0.
        parent_registry_doc = read_json(
            self.parent_sessions_path, {"parents": {}}) or {"parents": {}}
        parent_registry = (parent_registry_doc.get("parents", {})
                           if isinstance(parent_registry_doc, dict) else {})
        parent_pending = self.read_parent_queue()
        dialogue_limit = (self.policy.dialogue_target()
                          if isinstance(parent_registry, dict) and parent_registry else 0)
        parent_demand = {p: 0 for p in POOLS}
        for parent_id, registered in (
                parent_registry.items() if isinstance(parent_registry, dict) else ()):
            if not isinstance(registered, dict) or registered.get("active") is not True:
                continue
            try:
                parent_target = int(registered.get("target_active") or dialogue_limit)
            except (TypeError, ValueError):
                parent_target = dialogue_limit
            parent_target = max(1, min(dialogue_limit, parent_target))
            observed = parent_counts.get(str(parent_id), {})
            parent_running = max(0, int(observed.get("running", 0) or 0))
            queued = parent_pending.get(str(parent_id), {})
            queued_pools = {p: max(0, int(queued.get(p, 0) or 0)) for p in POOLS}
            parent_debt = min(sum(queued_pools.values()),
                              max(0, parent_target - parent_running))
            split = self._split_deficit(parent_debt, queued_pools)
            for pool in POOLS:
                parent_demand[pool] += split[pool]

        # Sustained refill means demand-backed slots are restored to target,
        # not left parked at the low-water trigger.  Low water remains an
        # urgency/hysteresis signal exposed in state; the debt target is the
        # declared policy capacity (currently 80/60/20).
        required = {p: bool(not queue_empty and not finalized and pending[p] > 0
                            and (running[p] < targets[p] or parent_demand[p] > 0))
                    for p in POOLS}
        required_any = any(required.values())
        # Debt = min(pending, target − running); initializing births reserve
        # capacity but never clear debt (fail-visible until observed running).
        raw_debt = {p: (max(min(pending[p], max(0, targets[p] - running[p])),
                           parent_demand[p]) if required[p] else 0)
                    for p in POOLS}
        raw_spawn = {p: max(0, raw_debt[p] - initializing[p]) for p in POOLS}
        capacity = max(0, target_total - running_total - initializing_total)
        deficit_total = min(sum(raw_spawn.values()), capacity) if required_any else 0
        deficit = self._split_deficit(deficit_total, raw_spawn)

        parent_states: dict[str, dict[str, Any]] = {}
        for parent_id, registered in sorted(
                parent_registry.items() if isinstance(parent_registry, dict) else ()):
            if not isinstance(registered, dict):
                continue
            active = registered.get("active") is True
            try:
                parent_target = int(registered.get("target_active") or dialogue_limit)
            except (TypeError, ValueError):
                parent_target = dialogue_limit
            parent_target = max(1, min(dialogue_limit, parent_target))
            observed = parent_counts.get(str(parent_id), {})
            parent_running = max(0, int(observed.get("running", 0) or 0))
            parent_initializing = max(0, int(observed.get("initializing", 0) or 0))
            queued = parent_pending.get(str(parent_id), {"total": 0, "v4": 0, "k3": 0})
            queued_pools = {p: max(0, int(queued.get(p, 0) or 0)) for p in POOLS}
            queued_total = sum(queued_pools.values())
            parent_debt = (min(queued_total, max(0, parent_target - parent_running))
                           if active else 0)
            # Starting generations reserve a birth slot but do not clear the
            # visible debt. Only a process-generation-verified running row
            # reduces ``deficit``.
            parent_spawn_total = min(
                parent_debt,
                max(0, parent_target - parent_running - parent_initializing),
                capacity,
            )
            parent_spawn = self._split_deficit(parent_spawn_total, queued_pools)
            if not active:
                parent_reason = "parent_inactive"
            elif parent_running >= parent_target:
                parent_reason = "at_parent_target"
            elif queued_total <= 0:
                parent_reason = "parent_backlog_empty"
            elif parent_spawn_total <= 0 and parent_initializing:
                parent_reason = "parent_initializing"
            elif capacity <= 0:
                parent_reason = "global_at_capacity"
            else:
                parent_reason = "below_parent_target"
            parent_states[str(parent_id)] = {
                "active": active,
                "target": parent_target,
                "running": parent_running,
                "initializing": parent_initializing,
                "pending": {"total": queued_total, **queued_pools},
                "deficit": parent_debt,
                "spawnable": {"total": parent_spawn_total, **parent_spawn},
                "reason": parent_reason,
                "manifest_id": registered.get("manifest_id"),
            }

        if queue_empty:
            reason = "queue_empty"
        elif finalized:
            reason = "release_finalize"
        elif required_any and deficit_total == 0:
            reason = "at_capacity"
        elif required_any:
            reason = ("below_low_water" if any(
                required[p] and running[p] < low_waters[p] for p in POOLS)
                else "below_target")
        else:
            reason = "at_or_above_low_water"

        state: dict[str, Any] = {
            "schema": STATE_SCHEMA,
            "policy_version": self.policy.policy_version(),
            "policy_mtime": self._policy_mtime(),
            "target": targets,
            "preferred_target": preferred_targets,
            "target_total": target_total,
            "low_water": low_waters,
            "preferred_low_water": preferred_low_waters,
            "reservations_borrowable": borrowable,
            "pending": {"total": pending_total, **pending},
            "active": {"total": running_total, **running},
            "initializing": {"total": initializing_total, **initializing},
            "refill_required": required_any,
            "refill_required_by_pool": required,
            "deficit": {"total": deficit_total, **deficit},
            "unfulfilled_demand": {"total": sum(raw_debt.values()), **raw_debt},
            "parents": parent_states,
            "cross_plane_observer": self.observer_status,
            "model_pool": sorted(p for p in POOLS if raw_debt[p] > 0),
            "queue_empty": queue_empty,
            "finalized": finalized,
            "reason": reason,
            "refilled": False,
            "updated_at": utc_now(),
        }
        if emit:
            with file_lock(self.lock_path):
                atomic_write_json(self.state_path, state)
            if required_any:
                self._append_event("refill_required",
                                   {"deficit": deficit,
                                    "model_pool": state["model_pool"],
                                    "pending": pending, "running": running})
            elif prior.get("refill_required"):
                self._append_event("refill_clear", {"pending": pending})
        return state

    @staticmethod
    def _split_deficit(total: int, pending: Mapping[str, int]) -> dict[str, int]:
        """Hamilton (largest remainder) split across pools (v1 algorithm)."""
        out = {p: 0 for p in POOLS}
        pools = [p for p in POOLS if pending.get(p, 0) > 0]
        if total <= 0 or not pools:
            return out
        grand = sum(pending[p] for p in pools)
        floors = {p: total * pending[p] // grand for p in pools}
        remainders = {p: total * pending[p] % grand for p in pools}
        remaining = total - sum(floors.values())
        for p in sorted(pools, key=lambda q: remainders[q], reverse=True):
            if remaining <= 0:
                break
            if floors[p] < pending[p]:
                floors[p] += 1
                remaining -= 1
        for p in pools:
            out[p] = min(floors[p], pending[p])
        return out

    # ------------------------------------------------------------------ state guards
    def read_state(self) -> dict[str, Any]:
        """Read the persisted state; a policy version/mtime mismatch forces a
        recompute so stale numbers can never silently win (P0-8.3)."""
        state = read_json(self.state_path, None)
        if not isinstance(state, dict):
            return self.recompute(emit=True)
        if (state.get("policy_version") != self.policy.policy_version()
                or state.get("policy_mtime") != self._policy_mtime()):
            log.warning("refill state computed from stale policy "
                        "(version %r vs %r) — recomputing",
                        state.get("policy_version"),
                        self.policy.policy_version())
            return self.recompute(emit=True)
        return state

    def _policy_mtime(self) -> float | None:
        try:
            return self.policy.path.stat().st_mtime if self.policy.path else None
        except OSError:
            return None

    # ------------------------------------------------------------------ lifecycle
    def release_finalize(self) -> dict[str, Any]:
        state = read_json(self.state_path, {}) or {}
        state["finalized"] = True
        atomic_write_json(self.state_path, state)
        return self.recompute(emit=True)

    def resume(self) -> dict[str, Any]:
        state = read_json(self.state_path, {}) or {}
        state["finalized"] = False
        atomic_write_json(self.state_path, state)
        return self.recompute(emit=True)

    def _append_event(self, event: str, detail: Mapping[str, Any]) -> None:
        append_ndjson(self.events_path,
                      {"ts": utc_now(), "event": event, **detail})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Pool-aware sustained-refill controller v2. "
                    "Prints refill_state.json after the requested action.")
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--recompute", action="store_true")
    ap.add_argument("--queue-set", type=int, metavar="N")
    ap.add_argument("--queue-add", type=int, metavar="N")
    ap.add_argument("--queue-remove", type=int, metavar="N")
    ap.add_argument("--queue-clear", action="store_true")
    ap.add_argument("--queue-sync-ledger", action="store_true",
                    help="pool-aware TOTAL sync from the progress ledger "
                         "(--pool does not apply; sync writes both pools)")
    ap.add_argument("--pool", choices=list(POOLS), default="v4",
                    help="pool for manual queue-set/add/remove ops only")
    ap.add_argument("--release-finalize", action="store_true")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    try:
        ctl = RefillControllerV2(LoopPaths.resolve(args.root))
    except PolicyError as exc:
        # Fail closed AND visible: emit the error event, exit non-zero.
        print(json.dumps({"error": str(exc), "fail_closed": True}),
              file=sys.stderr)
        return 1
    mutated = False
    if args.queue_set is not None:
        ctl.queue_set(args.queue_set, args.pool); mutated = True
    if args.queue_add is not None:
        ctl.queue_add(args.queue_add, args.pool); mutated = True
    if args.queue_remove is not None:
        ctl.queue_remove(args.queue_remove, args.pool); mutated = True
    if args.queue_clear:
        ctl.queue_clear(); mutated = True
    if args.queue_sync_ledger:
        ctl.queue_sync_ledger(); mutated = True
    if args.release_finalize:
        ctl.release_finalize(); mutated = True
    if args.resume:
        ctl.resume(); mutated = True
    out = ctl.read_state() if (args.status and not mutated) \
        else ctl.recompute(emit=True)
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
