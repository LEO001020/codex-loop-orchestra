#!/usr/bin/env python3
"""Fail-open lifecycle hook for Desktop-native Codex subagents.

This hook maintains a shadow roster and a semantic task-name map.  It never
writes Codex Desktop's ``state_5.sqlite`` and never claims to release a native
runtime slot: the host-only ``close_agent`` tool is represented by an
idempotent close-request record for the orchestrator to consume.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

if os.name == "nt":
    from ctypes import wintypes


TASK_RE = re.compile(r"(?:^|\n)\s*任务名\s*[：:]\s*([^\r\n]{1,160})")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
PENDING_TTL_SECONDS = 600
TERMINAL_MARKERS = {"task_complete", "turn_aborted", "turn_failed", "thread_closed"}
LEGACY_UNRESOLVED_NAMES = {"未命名子任务", "未映射子任务"}
NAME_FALLBACK_SOURCE = "agent_id_fallback"
ATOMIC_REPLACE_TIMEOUT_S = 5.0
RETRY_INITIAL_S = 0.005
RETRY_MAX_S = 0.100
LOCK_TIMEOUT_S = 10.0


def project_root() -> Path:
    explicit = os.environ.get("LOOP_ROOT")
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve()
    # package/hooks/script.py or target/.codex/hooks/script.py
    return here.parents[2] if here.parent.name == "hooks" and here.parent.parent.name == ".codex" else here.parents[1]


def data_dir() -> Path:
    return Path(os.environ.get("LOOP_DATA_DIR", str(project_root() / "data"))).resolve()


def recompute_refill(root: Path | None = None) -> dict[str, Any] | None:
    """Mechanically recompute the sustained-refill state after a lifecycle event.

    The hook never spawns: it imports the refill controller (harness/) and asks
    it to re-derive refill_required / deficit / model_pool from the native
    roster and the work queue.  Failures are audited and never faked as
    refilled.
    """
    base = (root or project_root()).resolve()
    pkg_harness = Path(__file__).resolve().parents[1] / "harness"
    try:
        for candidate in (pkg_harness, base / "harness"):
            if (candidate / "refill_controller_v2.py").exists():
                sys.path.insert(0, str(candidate))
                break
        from orchestration_common import LoopPaths
        from refill_controller_v2 import RefillControllerV2
        controller = RefillControllerV2(LoopPaths.resolve(base))
        controller.queue_sync_ledger()
        return controller.recompute()
    except BaseException as exc:
        try:
            NativeRoster(data_dir()).audit("refill_recompute_degraded",
                                           error="%s: %s" % (type(exc).__name__, exc))
        except BaseException:
            pass
        return None


def schedule_refill_actuator(root: Path | None = None, *, source: str) -> dict[str, Any] | None:
    """Trigger the existing epilogue after a terminal lifecycle edge.

    ``recompute_refill`` is the controller (observe + decide), not the
    actuator.  Desktop-native terminal hooks previously stopped after that
    decision, so a real deficit stayed queued until the root agent manually
    dispatched again.  Reuse the headless supervisor's coalesced epilogue
    launcher; it owns the existing epilogue lock and the packet-only refill
    consumer.  This remains fail-open for the lifecycle hook: a failed trigger
    is audited and never reported as a successful refill.
    """
    base = (root or project_root()).resolve()
    try:
        harness = Path(__file__).resolve().parents[1] / "harness"
        if str(harness) not in sys.path:
            sys.path.insert(0, str(harness))
        from refill_consumer_v2 import schedule_run
        result = schedule_run(base, source=source)
        try:
            NativeRoster(data_dir()).audit("refill_actuator_%s" % result.get("status", "unknown"),
                                           source=source, pid=result.get("pid"))
        except BaseException:
            pass
        return result
    except BaseException as exc:
        try:
            NativeRoster(data_dir()).audit("refill_actuator_failed", source=source,
                                           error="%s: %s" % (type(exc).__name__, exc))
        except BaseException:
            pass
        return None


def refresh_meter(root: Path | None = None) -> dict[str, Any] | None:
    """Refresh the existing rollout-to-meter bridge after lifecycle edges.

    The bridge owns its cross-process lock and policy-configured debounce, so
    invoking it from several Stop events does not multiply full session scans.
    This hook remains fail-open: stale or unavailable session telemetry is
    audited, never presented as a successful refresh.
    """
    base = (root or project_root()).resolve()
    metering = base / "metering"
    try:
        if str(metering) not in sys.path:
            sys.path.insert(0, str(metering))
        from model_token_share_bridge import refresh
        sessions = Path(os.environ.get(
            "CODEX_SESSIONS_DIR",
            str(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"),
        )).resolve()
        return refresh(base, sessions, force=False)
    except BaseException as exc:
        try:
            NativeRoster(data_dir()).audit("meter_refresh_degraded",
                                           error="%s: %s" % (type(exc).__name__, exc))
        except BaseException:
            pass
        return None


@contextlib.contextmanager
def lock(path: Path, *, timeout_s: float = LOCK_TIMEOUT_S):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0"); handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + max(0.0, timeout_s)
            backoff = RETRY_INITIAL_S
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "lifecycle hook lock remained busy for %.3fs: %s"
                            % (timeout_s, exc)) from exc
                    time.sleep(backoff)
                    backoff = min(RETRY_MAX_S, backoff * 2)
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


def atomic_json(path: Path, value: Any, *,
                replace_timeout_s: float = ATOMIC_REPLACE_TIMEOUT_S) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        deadline = time.monotonic() + max(0.0, replace_timeout_s)
        backoff = RETRY_INITIAL_S
        while True:
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "lifecycle hook atomic replace remained blocked for "
                        "%.3fs: %s" % (replace_timeout_s, exc)) from exc
                time.sleep(backoff)
                backoff = min(RETRY_MAX_S, backoff * 2)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class NativeRoster:
    def __init__(self, root: Path):
        self.root = root / "lifecycle"
        self.path = self.root / "native_roster.json"
        self.lock_path = self.root / ".native_roster.lock"
        self.close_path = self.root / "close_requests.ndjson"
        self.events_path = self.root / "events.ndjson"
        self.name_map_path = self.root / "name_map.jsonl"
        self.main_events_path = root / "events.ndjson"

    def transact(self, fn):
        with lock(self.lock_path):
            if self.path.exists():
                try:
                    roster = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise RuntimeError("native roster unreadable; refusing overwrite: %s" % exc) from exc
            else:
                roster = {"schema": "codex-loop-native-roster/v1",
                          "pending": [], "agents": {}}
            result = fn(roster)
            roster["updated_at"] = time.time()
            atomic_json(self.path, roster)
            return result

    def append(self, path: Path, row: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def audit(self, event: str, **detail: Any) -> None:
        with lock(self.lock_path.with_name(".native_events.lock")):
            self.append(self.events_path, {"ts": time.time(), "event": event, **detail})

    def main_event_once(self, event: str, agent_id: str, **detail: Any) -> bool:
        with lock(self.lock_path.with_name(".events.lock")):
            try:
                for line in self.main_events_path.read_text(encoding="utf-8").splitlines():
                    try: row = json.loads(line)
                    except ValueError: continue
                    if row.get("event") == event and row.get("agent_id") == agent_id:
                        return False
            except OSError:
                pass
            self.append(self.main_events_path, {"ts": time.time(), "event": event,
                                                "agent_id": agent_id, **detail})
            return True

    def exec_event_once(self, packet_id: str, run_id: str, attempt: int,
                        event: str, **detail: Any) -> bool:
        with lock(self.lock_path.with_name(".events.lock")):
            try:
                for line in self.main_events_path.read_text(encoding="utf-8").splitlines():
                    try: row = json.loads(line)
                    except ValueError: continue
                    if (row.get("packet_id") == packet_id and row.get("run_id") == run_id
                            and row.get("event") == event):
                        return False
            except OSError:
                pass
            self.append(self.main_events_path, {"ts": time.time(), "packet_id": packet_id,
                "run_id": run_id, "attempt": attempt, "event": event, "detail": detail})
            return True

    def queue_close_locked(self, agent: dict[str, Any], reason: str) -> bool:
        if agent.get("close_request_emitted") or agent.get("close_request_consumed"):
            return False
        row = {"ts": time.time(), "agent_id": agent.get("agent_id"),
               "task_name": agent.get("task_name"), "parent_session_id": agent.get("parent_session_id"),
               "reason": reason, "action": "host_close_agent_required"}
        self.append(self.close_path, row)
        agent["close_request_emitted"] = True
        agent["close_request_reason"] = reason
        return True


def payload_task_name(payload: dict[str, Any]) -> str | None:
    tool_input = payload.get("tool_input") or payload.get("input") or payload.get("arguments") or {}
    if isinstance(tool_input, str):
        try: tool_input = json.loads(tool_input)
        except ValueError: tool_input = {"message": tool_input}
    if not isinstance(tool_input, dict):
        tool_input = {}
    explicit = tool_input.get("task_name") or tool_input.get("name")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()[:160]
    # Current multi-agent payloads may normalize the legacy ``message`` into
    # structured ``items``. Walk only in-memory strings and persist only the
    # matched one-line task name, never the raw tool payload.
    queue: list[Any] = [tool_input]
    while queue:
        value = queue.pop()
        if isinstance(value, str):
            match = TASK_RE.search(value)
            if match:
                return match.group(1).strip()[:160]
        elif isinstance(value, dict):
            queue.extend(value.values())
        elif isinstance(value, list):
            queue.extend(value)
    return None


def resolve_identity(payload: dict[str, Any], roster: NativeRoster,
                     context: str) -> tuple[str | None, str | None] | None:
    """Resolve identity fields, auditing and blocking on conflicting duplicates."""
    agent_id = payload.get("agent_id")
    thread_id = payload.get("thread_id")
    session_id = payload.get("session_id")
    parent_thread_id = payload.get("parent_thread_id")
    if agent_id and thread_id and str(agent_id) != str(thread_id):
        roster.audit("identity_conflict", context=context, pair="agent_id/thread_id",
                     agent_id=str(agent_id), thread_id=str(thread_id), action="fail_closed")
        return None
    if session_id and parent_thread_id and str(session_id) != str(parent_thread_id):
        roster.audit("identity_conflict", context=context, pair="session_id/parent_thread_id",
                     session_id=str(session_id), parent_thread_id=str(parent_thread_id),
                     action="fail_closed")
        return None
    return agent_id or thread_id, session_id or parent_thread_id


def recover_arguments_from_parent_rollout(parent_session_id: str | None,
                                          tool_use_id: str | None,
                                          sessions: Path | None = None) -> dict[str, Any] | None:
    """Recover tool arguments when PreToolUse redacts collaboration input.

    Current Desktop builds expose the tool-use id to the hook but can omit the
    spawn message. The parent rollout is the second truth source and already
    contains the function_call before SubagentStart fires.
    """
    if (not parent_session_id or not tool_use_id or
            not ID_RE.fullmatch(str(parent_session_id)) or
            not ID_RE.fullmatch(str(tool_use_id))):
        return None
    root = sessions or Path(os.environ.get("CODEX_SESSIONS_DIR", str(Path.home() / ".codex" / "sessions")))
    if not root.exists():
        return None
    matches = list(root.rglob("rollout-*%s*.jsonl" % parent_session_id))
    for path in sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try: row = json.loads(line)
                    except ValueError: continue
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    if payload.get("type") != "function_call" or payload.get("call_id") != tool_use_id:
                        continue
                    arguments = payload.get("arguments") or {}
                    if isinstance(arguments, str):
                        try: arguments = json.loads(arguments)
                        except ValueError: arguments = {"message": arguments}
                    return arguments if isinstance(arguments, dict) else None
        except OSError:
            continue
    return None


def recover_spawn_agent_id(parent_session_id: str | None, tool_use_id: str | None,
                           sessions: Path | None = None) -> str | None:
    """Resolve spawn call_id -> returned agent_id from the parent rollout."""
    if (not parent_session_id or not tool_use_id or
            not ID_RE.fullmatch(str(parent_session_id)) or
            not ID_RE.fullmatch(str(tool_use_id))):
        return None
    root = sessions or Path(os.environ.get("CODEX_SESSIONS_DIR", str(Path.home() / ".codex" / "sessions")))
    if not root.exists():
        return None
    for path in sorted(root.rglob("rollout-*%s*.jsonl" % parent_session_id),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if tool_use_id not in line or "function_call_output" not in line:
                        continue
                    try: row = json.loads(line)
                    except ValueError: continue
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    if payload.get("type") != "function_call_output" or payload.get("call_id") != tool_use_id:
                        continue
                    output = payload.get("output") or {}
                    if isinstance(output, str):
                        try: output = json.loads(output)
                        except ValueError: continue
                    candidate = output.get("agent_id") if isinstance(output, dict) else None
                    return str(candidate) if candidate and ID_RE.fullmatch(str(candidate)) else None
        except OSError:
            continue
    return None


def recover_task_from_parent_rollout(parent_session_id: str | None,
                                     tool_use_id: str | None,
                                     sessions: Path | None = None) -> str | None:
    arguments = recover_arguments_from_parent_rollout(parent_session_id, tool_use_id, sessions)
    return payload_task_name({"tool_input": arguments or {}})


def pre_tool(roster: NativeRoster, payload: dict[str, Any]) -> None:
    tool = str(payload.get("tool_name") or payload.get("tool") or payload.get("name") or "")
    resolved = resolve_identity(payload, roster, "PreToolUse")
    if resolved is None:
        return
    parent = resolved[1]
    tool_use_id = payload.get("tool_use_id")
    if "close_agent" in tool:
        tool_input = payload.get("tool_input") or payload.get("input") or payload.get("arguments") or {}
        if isinstance(tool_input, str):
            try: tool_input = json.loads(tool_input)
            except ValueError: tool_input = {}
        if not isinstance(tool_input, dict): tool_input = {}
        target = tool_input.get("target") or tool_input.get("agent_id") or tool_input.get("id")
        if not target:
            recovered = recover_arguments_from_parent_rollout(parent, tool_use_id)
            if recovered:
                target = recovered.get("target") or recovered.get("agent_id") or recovered.get("id")
        if target:
            def confirm(value):
                item = value.setdefault("agents", {}).get(str(target))
                if not item: return None
                if not item.get("host_close_confirmed_at"):
                    item["host_close_confirmed_at"] = time.time()
                    item["close_request_consumed"] = True
                    item["close_observed_before_request"] = not bool(item.get("close_request_emitted"))
                    item["updated_at"] = time.time()
                return dict(item)
            item = roster.transact(confirm)
            if item:
                roster.audit("host_close_observed", agent_id=str(target),
                             task_name=item.get("task_name"), tool_use_id=tool_use_id)
                recompute_refill()
                schedule_refill_actuator(project_root(), source="desktop_close_observed")
        return
    if "spawn_agent" not in tool:
        return
    task_name = payload_task_name(payload)
    item = {"task_name": task_name, "parent_session_id": parent,
            "tool_use_id": tool_use_id, "created_at": time.time(),
            "status": "pending_start"}
    def mutate(value):
        expired = prune_pending(value, item["created_at"])
        pending = value.setdefault("pending", [])
        key = (item["parent_session_id"], item["tool_use_id"])
        if item["tool_use_id"] and any((x.get("parent_session_id"), x.get("tool_use_id")) == key for x in pending):
            return False, expired
        pending.append(item); return True, expired
    added, expired = roster.transact(mutate)
    if expired:
        roster.audit("pending_expired", count=len(expired),
                     tool_use_ids=[x.get("tool_use_id") for x in expired],
                     trigger="PreToolUse")
    if added:
        roster.audit("spawn_pending", **item)


def prune_pending(value: dict[str, Any], now: float) -> list[dict[str, Any]]:
    """Drop expired or malformed pending starts before matching/de-duplication."""
    active, expired = [], []
    for item in value.setdefault("pending", []):
        if item.get("status") != "pending_start":
            continue
        try:
            created_at = float(item.get("created_at", 0) or 0)
        except (TypeError, ValueError):
            created_at = 0.0
        if now - created_at > PENDING_TTL_SECONDS:
            expired.append(item)
        else:
            active.append(item)
    value["pending"] = active
    return expired


def subagent_start(roster: NativeRoster, payload: dict[str, Any]) -> None:
    resolved = resolve_identity(payload, roster, "SubagentStart")
    if resolved is None:
        return
    agent_id, parent = resolved
    if not agent_id:
        roster.audit("subagent_start_degraded", reason="missing_agent_id")
        return
    now = time.time()
    def mutate(value):
        agents = value.setdefault("agents", {})
        if agent_id in agents:
            return agents[agent_id], []
        expired = prune_pending(value, now)
        pending = value.setdefault("pending", [])
        candidates = [x for x in pending
                      if x.get("status") == "pending_start" and
                      (not parent or not x.get("parent_session_id") or x.get("parent_session_id") == parent)]
        exact = [x for x in candidates
                 if recover_spawn_agent_id(parent, x.get("tool_use_id")) == str(agent_id)]
        chosen = min(exact, key=lambda x: x.get("created_at", 0)) if exact else (
                 candidates[0] if len(candidates) == 1 else None)
        if chosen:
            pending.remove(chosen)
            if chosen in exact:
                chosen["mapping_source"] = "parent_rollout_function_call_output"
        task_name = chosen.get("task_name") if chosen else None
        if task_name in LEGACY_UNRESOLVED_NAMES:
            task_name = None
        if chosen and task_name is None:
            recovered = recover_task_from_parent_rollout(parent, chosen.get("tool_use_id"))
            if recovered:
                task_name = recovered
                chosen["task_name"] = recovered
                chosen["mapping_source"] = "parent_rollout_function_call"
        name_degraded = False
        name_source = None
        if not task_name:
            task_name = str(agent_id)
            name_degraded = True
            name_source = NAME_FALLBACK_SOURCE
        item = {"agent_id": agent_id, "task_name": task_name,
                "name_degraded": name_degraded, "name_source": name_source,
                "parent_session_id": parent, "agent_role": payload.get("agent_type") or payload.get("agent_role"),
                "model": payload.get("model") or payload.get("agent_model"),
                "nickname": payload.get("agent_nickname"), "status": "running",
                "started_at": now, "updated_at": now,
                "mapping_confidence": ("tool_use+rollout" if chosen and chosen.get("mapping_source") else
                                       "tool_use" if chosen and chosen.get("tool_use_id") else
                                       "parent_fifo" if chosen else "unmapped")}
        agents[agent_id] = item
        self_row = {**item, "source": "SubagentStart"}
        roster.append(roster.name_map_path, self_row)
        return item, expired
    item, expired = roster.transact(mutate)
    if expired:
        roster.audit("pending_expired", count=len(expired),
                     tool_use_ids=[x.get("tool_use_id") for x in expired])
    roster.main_event_once("SubagentStart", str(agent_id),
                           agent_role=item.get("agent_role"), model=item.get("model"),
                           session_id=item.get("parent_session_id"), task_name=item.get("task_name"))
    roster.audit("subagent_started", **item)


def subagent_stop(roster: NativeRoster, payload: dict[str, Any]) -> None:
    resolved = resolve_identity(payload, roster, "SubagentStop")
    if resolved is None:
        return
    agent_id, _parent = resolved
    if not agent_id:
        roster.audit("subagent_stop_degraded", reason="missing_agent_id")
        return
    now = time.time()
    def mutate(value):
        item = value.setdefault("agents", {}).setdefault(agent_id,
            {"agent_id": agent_id, "task_name": str(agent_id), "started_at": None,
             "name_degraded": True, "name_source": NAME_FALLBACK_SOURCE})
        item.update(status="terminal", terminal_reason="SubagentStop", stopped_at=now, updated_at=now)
        queued = roster.queue_close_locked(item, "subagent_terminal")
        return dict(item), queued
    item, queued = roster.transact(mutate)
    roster.main_event_once("SubagentStop", str(agent_id),
                           session_id=item.get("parent_session_id"), task_name=item.get("task_name"))
    roster.audit("subagent_stopped", agent_id=agent_id, task_name=item.get("task_name"), close_queued=queued)
    recompute_refill()
    schedule_refill_actuator(project_root(), source="desktop_subagent_stop")


def parent_stop(roster: NativeRoster, payload: dict[str, Any]) -> None:
    resolved = resolve_identity(payload, roster, "Stop")
    if resolved is None:
        return
    parent = payload.get("session_id") or payload.get("thread_id")
    if not parent:
        roster.audit("parent_stop_degraded", reason="missing_session_id",
                     native_agents=[], exec_packets=[])
        return
    parent_state_path = data_dir() / "refill" / "parent_sessions.json"
    with lock(data_dir() / "refill" / ".parent_sessions.lock"):
        parent_states = read_json(parent_state_path, {"parents": {}})
        item = parent_states.setdefault("parents", {}).setdefault(str(parent), {})
        item.update(active=False, stopped_at=time.time(), source="parent_stop")
        atomic_json(parent_state_path, parent_states)
    now = time.time()
    def mutate(value):
        changed = []
        for item in value.setdefault("agents", {}).values():
            if item.get("status") == "running" and item.get("parent_session_id") == parent:
                item.update(status="interrupted", terminal_reason="parent_stop", stopped_at=now, updated_at=now)
                roster.queue_close_locked(item, "parent_stop")
                changed.append(item.get("agent_id"))
        return changed
    changed = roster.transact(mutate)

    # Request cancellation for all live codex-exec jobs.  Their supervisors
    # own the strong OS handles and perform the actual process-tree shutdown.
    with lock(roster.root / ".exec_roster.lock"):
        exec_roster = read_json(roster.root / "exec_roster.json", {"jobs": {}})
    cancel_dir = roster.root / "cancel"; cancel_dir.mkdir(parents=True, exist_ok=True)
    exec_cancelled = []
    for packet_id, item in exec_roster.get("jobs", {}).items():
        if (item.get("state") in ("starting", "running") and
                item.get("parent_session_id") == parent and item.get("run_id") is not None):
            atomic_json(cancel_dir / (packet_id + ".json"),
                        {"ts": now, "reason": "parent_stop", "parent_session_id": parent,
                         "run_id": item.get("run_id"), "attempt": item.get("attempt", 0)})
            exec_cancelled.append(packet_id)
    roster.audit("parent_stop", parent_session_id=parent, native_agents=changed, exec_packets=exec_cancelled)
    recompute_refill(project_root())
    schedule_refill_actuator(project_root(), source="desktop_parent_stop")


def rollout_info(path: Path) -> tuple[dict[str, Any], str | None]:
    meta: dict[str, Any] = {}
    terminal = None
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try: row = json.loads(line)
                except ValueError: continue
                kind = row.get("type")
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                if kind == "session_meta":
                    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
                    spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source.get("subagent"), dict) else {}
                    meta = {"nickname": payload.get("agent_nickname") or spawn.get("agent_nickname"),
                            "agent_role": payload.get("agent_role") or spawn.get("agent_role"),
                            "agent_path": payload.get("agent_path") or spawn.get("agent_path"),
                            "rollout_path": str(path)}
                candidates = {kind, payload.get("type"), payload.get("event")}
                hit = next((x for x in candidates if x in TERMINAL_MARKERS), None)
                if hit: terminal = str(hit)
    except OSError:
        pass
    return meta, terminal


def cold_reconcile(roster: NativeRoster, sessions: Path) -> None:
    def snapshot(value):
        return [(aid, dict(item)) for aid, item in value.setdefault("agents", {}).items()
                if item.get("status") in ("running", "interrupted")]
    active = roster.transact(snapshot)
    found = 0
    for agent_id, old in active:
        matches = list(sessions.rglob("rollout-*%s*.jsonl" % agent_id)) if sessions.exists() else []
        if not matches: continue
        path = max(matches, key=lambda p: p.stat().st_mtime)
        meta, terminal = rollout_info(path)
        def mutate(value, aid=agent_id, info=meta, term=terminal):
            item = value.setdefault("agents", {}).get(aid)
            if not item: return False
            for key, val in info.items():
                if val: item[key] = val
            if term:
                item.update(status="terminal", terminal_reason="rollout_%s" % term,
                            stopped_at=time.time(), updated_at=time.time())
                roster.queue_close_locked(item, "cold_start_terminal_reconcile")
            return bool(term)
        if roster.transact(mutate): found += 1
    roster.audit("cold_start_reconcile", candidates=len(active), terminal_recovered=found)


def process_matches(pid: Any, expected_start_ticks: Any = None) -> bool:
    if os.name == "nt":
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        query = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
        handle = ctypes.windll.kernel32.OpenProcess(query, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            if exit_code.value != 259:  # STILL_ACTIVE
                return False
            if expected_start_ticks is None:
                return True
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel), ctypes.byref(user)):
                return False
            actual = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
            return actual == int(expected_start_ticks)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        pid = int(pid)
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    if expected_start_ticks is not None:
        try:
            actual = int(Path("/proc/%d/stat" % pid).read_text(encoding="ascii").split()[21])
            return actual == int(expected_start_ticks)
        except (OSError, ValueError, IndexError):
            return False
    return True


def cold_reconcile_exec(roster: NativeRoster) -> None:
    path = roster.root / "exec_roster.json"
    lock_path = roster.root / ".exec_roster.lock"
    candidates: list[dict[str, Any]] = []
    with lock(lock_path):
        doc = read_json(path, {"schema": "codex-loop-exec-roster/v2", "jobs": {}})
        for packet_id, item in doc.get("jobs", {}).items():
            if item.get("state") not in ("starting", "running"):
                continue
            if process_matches(item.get("supervisor_pid"), item.get("supervisor_proc_start_ticks")):
                continue
            candidates.append({"packet_id": packet_id, **dict(item)})

    terminal: list[dict[str, Any]] = []
    cleanup_failed: list[dict[str, Any]] = []
    for item in candidates:
        # Windows Job Objects kill on supervisor-handle close.  POSIX has no
        # equivalent, so a cold start reaps the still-owned process group.
        cleaned = os.name == "nt"
        if not cleaned:
            try:
                worker_pid = int(item.get("os_pid"))
                proc_path = Path("/proc/%d/stat" % worker_pid)
                # If the former leader PID has already been reused, never
                # signal that unrelated process group.  Its old identity is
                # already gone, so no owned live generation remains.
                if proc_path.exists() and not process_matches(
                        worker_pid, item.get("worker_proc_start_ticks")):
                    cleaned = True
                else:
                    os.killpg(worker_pid, 15)
                    time.sleep(0.05)
                    os.killpg(worker_pid, 9)
                    deadline = time.monotonic() + 0.5
                    while True:
                        try:
                            os.killpg(worker_pid, 0)
                        except ProcessLookupError:
                            cleaned = True
                            break
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(0.02)
            except ProcessLookupError:
                cleaned = True
            except (OSError, TypeError, ValueError):
                cleaned = False

        # Recheck the exact generation under the roster lock.  A concurrent
        # supervisor heartbeat/replacement must never be overwritten by this
        # cold-start observation.
        with lock(lock_path):
            doc = read_json(path, {"schema": "codex-loop-exec-roster/v2", "jobs": {}})
            current = (doc.get("jobs") or {}).get(str(item["packet_id"]))
            if (not isinstance(current, dict)
                    or current.get("run_id") != item.get("run_id")
                    or current.get("state") not in ("starting", "running")
                    or process_matches(current.get("supervisor_pid"),
                                       current.get("supervisor_proc_start_ticks"))):
                continue
            now = time.time()
            if cleaned:
                current.update(state="lost", stop_reason="supervisor_lost",
                               cleanup_status="confirmed_gone", updated_at=now)
                current.setdefault("history", []).append({
                    "ts": now, "state": "lost", "run_id": current.get("run_id"),
                    "attempt": current.get("attempt")})
                terminal.append({"packet_id": item["packet_id"], **dict(current)})
            else:
                # Preserve the active reservation and suppress refill until a
                # later reconcile can prove the old process group is gone.
                current.update(cleanup_status="cleanup_failed_live",
                               cleanup_failed_at=now, updated_at=now)
                cleanup_failed.append({"packet_id": item["packet_id"], **dict(current)})
            atomic_json(path, doc)

    for item in terminal:
        roster.exec_event_once(str(item["packet_id"]), str(item.get("run_id") or "legacy"),
            int(item.get("attempt", 0) or 0), "exec_failed", why="supervisor_lost",
            cancelled=False, cold_start=True)
    roster.audit("cold_start_exec_reconcile", candidates=len(candidates),
                 lost=len(terminal), cleanup_failed=len(cleanup_failed))


def handle(event: str, payload: dict[str, Any], sessions: Path | None = None) -> None:
    roster = NativeRoster(data_dir())
    if event == "PreToolUse": pre_tool(roster, payload)
    elif event == "SubagentStart": subagent_start(roster, payload)
    elif event == "SubagentStop": subagent_stop(roster, payload)
    elif event == "Stop": parent_stop(roster, payload)
    elif event == "SessionStart":
        cold_reconcile(roster, sessions or Path(os.environ.get("CODEX_SESSIONS_DIR", str(Path.home() / ".codex" / "sessions"))))
        cold_reconcile_exec(roster)
        recompute_refill()
        schedule_refill_actuator(project_root(), source="desktop_session_start")
    if event in ("SessionStart", "SubagentStop", "Stop"):
        refresh_meter()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", choices=["PreToolUse", "SubagentStart", "SubagentStop", "Stop", "SessionStart"])
    ap.add_argument("--sessions", type=Path)
    args = ap.parse_args()
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        event = args.event or payload.get("hook_event_name")
        if event: handle(str(event), payload, args.sessions)
    except BaseException as exc:
        try:
            NativeRoster(data_dir()).audit("hook_degraded", error="%s: %s" % (type(exc).__name__, exc))
        except BaseException:
            pass
    # Stop/SubagentStop require JSON on successful exit; one harmless object
    # also keeps the other command-hook events version-tolerant.
    sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
