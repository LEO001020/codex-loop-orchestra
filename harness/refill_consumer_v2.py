#!/usr/bin/env python3
"""Mechanical actuator for the canonical v2 refill controller.

This module is deliberately not another controller.  It synchronizes demand
from the packet ledger, asks :class:`RefillControllerV2` for the bounded
deficit, emits a packet-only manifest, and hands that manifest to the existing
``headless_wave`` transport.  It never invents work or embeds a free-form
prompt: every task must resolve to a validated ``data/packets/<id>.json`` and
is physically dispatched by ``DispatcherV2``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

try:
    import headless_wave
    from dispatch_v2 import validate_packet_id
    from orchestration_common import (LoopPaths, atomic_write_json, file_lock,
                                      read_json)
    from refill_controller_v2 import (K3_ROLES, K3_WORK_STATES,
                                      RefillControllerV2, pool_for_packet)
except ImportError:  # pragma: no cover - direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import headless_wave
    from dispatch_v2 import validate_packet_id
    from orchestration_common import (LoopPaths, atomic_write_json, file_lock,
                                      read_json)
    from refill_controller_v2 import (K3_ROLES, K3_WORK_STATES,
                                      RefillControllerV2, pool_for_packet)


K3_ROLE_BY_STATE = {
    "EXPAND_K3": "plan_expander",
    "L2_VERIFY": "verifier",
    "L2_VERIFY_PENDING": "verifier",
    "L2_RANK": "verifier",
}


class RefillConsumerError(RuntimeError):
    pass


def _process_alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                return bool(ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(code))) and code.value == 259
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _proc_start_ticks(pid: int) -> int | None:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return None
            try:
                creation = wintypes.FILETIME()
                exit_time = wintypes.FILETIME()
                kernel = wintypes.FILETIME()
                user = wintypes.FILETIME()
                if not ctypes.windll.kernel32.GetProcessTimes(
                        handle, ctypes.byref(creation), ctypes.byref(exit_time),
                        ctypes.byref(kernel), ctypes.byref(user)):
                    return None
                return ((int(creation.dwHighDateTime) << 32)
                        | int(creation.dwLowDateTime))
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (OSError, TypeError, ValueError):
            return None
    try:
        return int(Path("/proc/%d/stat" % int(pid)).read_text(
            encoding="ascii").split()[21])
    except (OSError, TypeError, ValueError, IndexError):
        return None


def _process_generation_alive(pid: int, ticks: int | None) -> bool:
    return ticks is not None and _process_alive(pid) \
        and _proc_start_ticks(pid) == int(ticks)


def schedule_run(root: Path, *, source: str) -> dict[str, Any]:
    """Coalesce and detach the packet-only refill actuator.

    Desktop lifecycle events must not invoke the full orchestration epilogue:
    that would also drain the shared audit event stream.  This runner performs
    only the existing packet-ledger refill transaction and therefore cannot
    invent work or mutate the state machine from transport-only events.
    """
    root = Path(root).resolve()
    orchestration = root / "data" / "orchestration"
    orchestration.mkdir(parents=True, exist_ok=True)
    marker = orchestration / "refill_actuator.pid"
    requests = orchestration / "refill_requests.json"
    log_path = orchestration / "refill_actuator.log"
    claim = orchestration / ".refill_actuator_schedule.lock"
    with file_lock(claim):
        request_state = read_json(requests, {"requested_seq": 0,
                                             "consumed_seq": 0}) or {}
        request_state["requested_seq"] = int(
            request_state.get("requested_seq", 0) or 0) + 1
        request_state.update(source=source, updated_at=time.time())
        atomic_write_json(requests, request_state)
        try:
            marker_value = read_json(marker, {}) or {}
            prior_pid = int(marker_value["pid"])
            prior_ticks = int(marker_value["proc_start_ticks"])
        except (KeyError, TypeError, ValueError):
            prior_pid, prior_ticks = 0, None
        if prior_pid and _process_generation_alive(prior_pid, prior_ticks):
            return {"status": "coalesced", "pid": prior_pid, "source": source}
        log = log_path.open("ab")
        kwargs: dict[str, Any] = {
            "cwd": str(root), "env": {**os.environ, "LOOP_ROOT": str(root)},
            "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                [sys.executable, str(root / "harness" / "refill_consumer_v2.py"),
                 "--root", str(root), "--source", source], **kwargs)
            atomic_write_json(marker, {"pid": proc.pid,
                                       "proc_start_ticks": _proc_start_ticks(proc.pid)})
            return {"status": "scheduled", "pid": proc.pid, "source": source,
                    "log": str(log_path)}
        finally:
            log.close()


def schedule_delayed_run(root: Path, *, wake_at: float,
                         source: str) -> dict[str, Any]:
    """Schedule one detached wake after provider/transport backoff expires."""
    root = Path(root).resolve()
    orchestration = root / "data" / "orchestration"
    orchestration.mkdir(parents=True, exist_ok=True)
    marker = orchestration / "refill_backoff_wake.json"
    with file_lock(orchestration / ".refill_backoff_wake.lock"):
        old = read_json(marker, {}) or {}
        try:
            old_pid = int(old.get("pid", 0) or 0)
            old_ticks = int(old.get("proc_start_ticks"))
            old_wake = float(old.get("wake_at", 0) or 0)
        except (TypeError, ValueError):
            old_pid, old_ticks, old_wake = 0, None, 0.0
        if (old_pid and _process_generation_alive(old_pid, old_ticks)
                and old_wake <= wake_at):
            return {"status": "coalesced", "pid": old_pid,
                    "wake_at": old_wake, "source": source}
        delay = max(0.0, wake_at - time.time())
        log = (orchestration / "refill_actuator.log").open("ab")
        kwargs: dict[str, Any] = {
            "cwd": str(root), "env": {**os.environ, "LOOP_ROOT": str(root)},
            "stdin": subprocess.DEVNULL, "stdout": log,
            "stderr": subprocess.STDOUT, "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                [sys.executable, str(root / "harness" / "refill_consumer_v2.py"),
                 "--root", str(root), "--source", source,
                 "--delay", "%.3f" % delay], **kwargs)
            atomic_write_json(marker, {"pid": proc.pid,
                                       "proc_start_ticks": _proc_start_ticks(proc.pid),
                                       "wake_at": wake_at, "source": source})
            return {"status": "scheduled", "pid": proc.pid,
                    "wake_at": wake_at, "source": source}
        finally:
            log.close()


def schedule_retry_if_debt(root: Path, state: Mapping[str, Any],
                           *, source: str) -> dict[str, Any] | None:
    if int((state.get("deficit") or {}).get("total", 0) or 0) <= 0:
        return None
    now = time.time()
    wake_at = now + 30.0
    health_dir = Path(root) / "data" / "provider_health"
    for path in health_dir.glob("*.json") if health_dir.exists() else ():
        doc = read_json(path, {}) or {}
        try:
            until = float(doc.get("backoff_until", 0) or 0)
        except (TypeError, ValueError):
            continue
        if until > now:
            wake_at = max(wake_at, until + 1.0)
    return schedule_delayed_run(Path(root), wake_at=wake_at, source=source)


def schedule_followup_if_debt(root: Path, state: Mapping[str, Any], *,
                              failed: bool, source: str) -> dict[str, Any] | None:
    """Keep successful refill passes from decaying between lifecycle edges.

    A provider/transport failure retains the existing bounded backoff.  A
    successful pass that still observes real debt gets one coalesced one-second
    follow-up, covering a concurrent terminal edge that landed during the
    transaction without introducing a poller or a second scheduler.
    """
    if int((state.get("deficit") or {}).get("total", 0) or 0) <= 0:
        return None
    if failed:
        return schedule_retry_if_debt(root, state, source=source)
    return schedule_delayed_run(Path(root), wake_at=time.time() + 1.0,
                                source=source)


def role_for_entry(entry: Mapping[str, Any]) -> str:
    state = str(entry.get("state") or "")
    if state in K3_ROLE_BY_STATE:
        return K3_ROLE_BY_STATE[state]
    role = str(entry.get("role") or "")
    if role in {"worker", "verifier", "reviewer", "plan_expander"}:
        return role
    raise RefillConsumerError(
        "DISPATCHABLE packet lacks an explicit LOOP role; refusing inherited/default model")


def validate_packet(paths: LoopPaths, packet_id: str) -> dict[str, Any]:
    validate_packet_id(packet_id)
    packet = read_json(paths.data / "packets" / (packet_id + ".json"), None)
    if not isinstance(packet, dict) or packet.get("packet_id") != packet_id:
        raise RefillConsumerError("packet file missing or id mismatch: %s" % packet_id)
    for key in ("goal", "authorized_paths", "acceptance"):
        if key not in packet:
            raise RefillConsumerError("packet %s missing %s" % (packet_id, key))
    return packet


def select_tasks(paths: LoopPaths, state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Select real packets within global and per-parent refill debt.

    Parent-managed packets are round-robined by parent id.  A parent therefore
    cannot consume another parent's refill allowance merely because its packet
    ids sort first.  Legacy non-parent packets remain supported as one bucket.
    """
    ledger = read_json(paths.ledger, {"packets": {}}) or {"packets": {}}
    deficits = state.get("deficit") if isinstance(state, Mapping) else {}
    remaining = {pool: max(0, int((deficits or {}).get(pool, 0) or 0))
                 for pool in ("v4", "k3")}
    tasks: list[dict[str, Any]] = []
    parent_states = read_json(paths.refill_dir / "parent_sessions.json",
                              {"parents": {}}) or {"parents": {}}
    state_parents = state.get("parents", {}) if isinstance(state, Mapping) else {}
    parent_remaining: dict[str, dict[str, int]] = {}
    if isinstance(state_parents, Mapping):
        for parent_id, parent_state in state_parents.items():
            if not isinstance(parent_state, Mapping):
                continue
            spawnable = parent_state.get("spawnable", {})
            parent_remaining[str(parent_id)] = {
                pool: max(0, int((spawnable or {}).get(pool, 0) or 0))
                for pool in ("v4", "k3")
            }
    buckets: dict[str, list[tuple[str, dict[str, Any], str, str | None]]] = {}
    for packet_id in sorted((ledger.get("packets") or {}).keys()):
        entry = ledger["packets"].get(packet_id)
        if not isinstance(entry, dict):
            continue
        state_name = str(entry.get("state") or "")
        # Dedicated K3 states are demand signals consumed by L2Consumer or
        # the plan pipeline; dispatching their source packet here would create
        # a duplicate verifier/expander.  This generic actuator owns only the
        # mechanically DISPATCHABLE packet lane.
        if state_name != "DISPATCHABLE":
            continue
        if entry.get("release_review"):
            # Release reviews have a dedicated once-per-wave hard-pinned
            # dispatcher and must never be consumed as ordinary refill work.
            continue
        parent_id = entry.get("parent_session_id")
        if parent_id:
            parent = (parent_states.get("parents") or {}).get(str(parent_id))
            if not isinstance(parent, dict) or parent.get("active") is not True:
                # Imported work cannot outlive its explicitly registered
                # parent.  The deficit remains visible, but no new birth is
                # admitted after Stop/inactivation.
                continue
            if parent.get("manifest_id") != entry.get("manifest_id"):
                # Keep task selection identical to queue_sync_ledger: only the
                # active admission generation supplies demand.  None/None is
                # the sole legacy-compatible case.
                continue
            if str(parent_id) not in parent_remaining:
                # A parent task only counts after the same controller snapshot
                # has established real backlog and per-parent capacity.
                continue
        pool = pool_for_packet(entry)
        key = "parent:" + str(parent_id) if parent_id else "legacy"
        buckets.setdefault(key, []).append(
            (str(packet_id), entry, pool, str(parent_id) if parent_id else None))

    while sum(remaining.values()) > 0:
        progressed = False
        for key in sorted(buckets):
            rows = buckets[key]
            chosen = None
            for index, (_, _, pool, parent_id) in enumerate(rows):
                if remaining[pool] <= 0:
                    continue
                if (parent_id is not None and
                        parent_remaining[parent_id][pool] <= 0):
                    continue
                chosen = index
                break
            if chosen is None:
                continue
            packet_id, entry, pool, parent_id = rows.pop(chosen)
            packet = validate_packet(paths, packet_id)
            role = role_for_entry(entry)
            role_pool = "k3" if role in K3_ROLES else "v4"
            if role_pool != pool:
                raise RefillConsumerError(
                    "packet %s pool/role conflict: %s vs %s" %
                    (packet_id, pool, role))
            tasks.append({
                "task_id": packet_id,
                "task_name": str(entry.get("task_name") or packet.get("goal")
                                 or packet_id)[:160],
                "packet_id": packet_id,
                "role": role,
                **({"parent_session_id": parent_id} if parent_id else {}),
                **({"manifest_id": str(entry["manifest_id"])}
                   if entry.get("manifest_id") else {}),
            })
            remaining[pool] -= 1
            if parent_id is not None:
                parent_remaining[parent_id][pool] -= 1
            progressed = True
        if not progressed:
            break
    return tasks


def build_manifest(paths: LoopPaths, state: Mapping[str, Any]) -> Path | None:
    tasks = select_tasks(paths, state)
    if not tasks:
        return None
    path = paths.refill_dir / "packet_wave.json"
    atomic_write_json(path, {
        "schema": "codex-loop-packet-wave/v1",
        "policy_version": state.get("policy_version"),
        "deficit_snapshot": state.get("deficit"),
        "tasks": tasks,
    })
    return path


def run_once(root: Path, *, dry_run: bool = False,
             observe_timeout: float = 5.0) -> tuple[int, dict[str, Any]]:
    paths = LoopPaths.resolve(root)
    # One actuator transaction per LOOP root.  The controller has its own
    # short state lock; this separate lock spans selection through stable
    # roster observation and closes the pre-roster double-birth race.
    with file_lock(paths.refill_dir / ".consumer.lock"):
        controller = RefillControllerV2(paths)
        controller.queue_sync_ledger()
        before = controller.recompute()
        manifest = build_manifest(paths, before)
        if manifest is None:
            return 0, {"status": "idle", "state": before, "manifest": None}
        args = argparse.Namespace(
            manifest=manifest, root=paths.root, timeout=1800.0,
            observe_timeout=observe_timeout,
            spawn_interval_ms=controller.policy.spawn_interval_ms(),
            health_every=8, dry_run=dry_run,
        )
        rc = headless_wave.run(args)
        # Dispatch appends canonical ``dispatched`` events.  Consume them in
        # the same actuator transaction so a stably born packet immediately
        # leaves DISPATCHABLE; otherwise the debt remains selectable until an
        # unrelated terminal edge and the same packet can be offered again.
        try:
            from statemachine_v2 import StateMachine
            state_machine = StateMachine(paths).step()
        except Exception as exc:
            state_machine = {"status": "failed",
                             "error": "%s: %s" % (type(exc).__name__, exc)}
            rc = rc or 3
        controller.queue_sync_ledger()
        after = controller.recompute()
        retry_wake = (schedule_followup_if_debt(
            root, after, failed=bool(rc),
            source=("refill_debt_backoff" if rc else "refill_residual_debt"))
            if not dry_run else None)
        return rc, {"status": "dry_run" if dry_run else "dispatched",
                    "manifest": str(manifest), "before": before,
                    "state_machine": state_machine, "after": after,
                    "retry_wake": retry_wake}


def drain_requested(root: Path, *, source: str, dry_run: bool = False,
                    observe_timeout: float = 5.0) -> tuple[int, dict[str, Any]]:
    """Drain every durable refill edge, including edges coalesced mid-run."""
    root = Path(root).resolve()
    orchestration = root / "data" / "orchestration"
    orchestration.mkdir(parents=True, exist_ok=True)
    requests = orchestration / "refill_requests.json"
    marker = orchestration / "refill_actuator.pid"
    claim = orchestration / ".refill_actuator_schedule.lock"
    overall_rc = 0
    passes: list[dict[str, Any]] = []
    while True:
        request_state = read_json(requests, {"requested_seq": 1,
                                             "consumed_seq": 0}) or {}
        requested = int(request_state.get("requested_seq", 1) or 1)
        rc, result = run_once(root, dry_run=dry_run,
                              observe_timeout=observe_timeout)
        overall_rc = overall_rc or rc
        passes.append(result)
        with file_lock(claim):
            latest = read_json(requests, request_state) or request_state
            latest["consumed_seq"] = max(
                int(latest.get("consumed_seq", 0) or 0), requested)
            latest["consumed_at"] = time.time()
            atomic_write_json(requests, latest)
            if int(latest.get("requested_seq", 0) or 0) <= requested:
                marker_value = read_json(marker, {}) or {}
                if (isinstance(marker_value, dict)
                        and int(marker_value.get("pid", -1) or -1) == os.getpid()):
                    try:
                        marker.unlink()
                    except FileNotFoundError:
                        pass
                return overall_rc, {"status": passes[-1].get("status", "idle"),
                                    "source": source, "passes": passes,
                                    "requested_seq": requested,
                                    "consumed_seq": latest["consumed_seq"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Actuate real v2 refill packets once")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--observe-timeout", type=float, default=5.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source", default="cli")
    ap.add_argument("--delay", type=float, default=0.0)
    args = ap.parse_args(argv)
    if args.delay > 0:
        time.sleep(args.delay)
        wake_marker = (args.root / "data" / "orchestration" /
                       "refill_backoff_wake.json")
        wake = read_json(wake_marker, {}) or {}
        if isinstance(wake, dict) and int(wake.get("pid", -1) or -1) == os.getpid():
            try:
                wake_marker.unlink()
            except FileNotFoundError:
                pass
        print(json.dumps(schedule_run(args.root, source=args.source),
                         ensure_ascii=False))
        return 0
    try:
        rc, result = drain_requested(args.root, source=args.source,
                                     dry_run=args.dry_run,
                                     observe_timeout=args.observe_timeout)
    except (OSError, ValueError, RefillConsumerError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 3
    print(json.dumps({**result, "source": args.source, "ts": time.time()},
                     ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
