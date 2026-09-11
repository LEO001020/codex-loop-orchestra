#!/usr/bin/env python3
"""Read-only local dashboard for Codex LOOP headless concurrency."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import threading
import time
import tomllib
import uuid
import urllib.request
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # dashboard remains usable without optional host telemetry
    psutil = None


DEFAULT_ROOT = Path(__file__).resolve().parents[1]  # parent of launchers/
DEFAULT_WINDOWS_ROOT = Path(__file__).resolve().parents[1]  # parent of launchers/
DEFAULT_CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
DEFAULT_SESSIONS_ROOT = DEFAULT_CODEX_HOME / "sessions"
GLOBAL_HOOK_PATH = DEFAULT_CODEX_HOME / "hooks.json"
ROLLOUT_SCAN_TTL_SECONDS = 5.0
ROLLOUT_PARENT_FRESH_SECONDS = 600.0
ROLLOUT_FILE_LOOKBACK_SECONDS = 6 * 3600.0
# A non-terminal rollout is an open session, not proof that it is executing
# right now. Recent file activity is the best zero-write Desktop signal;
# older open sessions remain visible but never inflate effective concurrency.
ROLLOUT_ACTIVE_SECONDS = 120.0
_rollout_lock = threading.Lock()
_rollout_cache: dict[str, Any] = {"checked_at": 0.0, "value": {"tasks": [], "updated_at": None}}
# Snapshot cache: serialise concurrent /api/status requests so a slow full scan
# never runs in parallel.  A short TTL (1.5 s) ensures freshness for the 2-second
# client poll while absorbing bursts without stacking threads.
_snapshot_lock = threading.Lock()
_snapshot_cache: dict[str, Any] = {"checked_at": 0.0, "value": None}
_SNAPSHOT_CACHE_TTL = 1.5  # seconds
_task_name_cache: dict[str, str] = {}
_model_cache: dict[str, tuple[float, dict[str, str]]] = {}
_desktop_epoch_cache: dict[str, float | None] = {"checked_at": 0.0, "value": None}
_TASK_NAME = re.compile(r"^\s*任务名\s*[:：]\s*([^\r\n]+)")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_policy(root: Path) -> dict[str, Any]:
    try:
        with (root / "config" / "refill_policy.toml").open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def read_execution_model(root: Path) -> str:
    """Return the active ordinary-execution model from the v2 source of truth."""
    try:
        with (root / "config" / "orchestration_policy_v2.toml").open("rb") as handle:
            return str(tomllib.load(handle).get("models", {}).get("v4_model") or "")
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def read_execution_profile(root: Path) -> dict[str, str]:
    """Return active profile metadata; observed task models remain runtime truth."""
    try:
        with (root / "config" / "model_profiles.toml").open("rb") as handle:
            doc = tomllib.load(handle)
        name = str(doc.get("active_profile") or "")
        profile = (doc.get("profiles") or {}).get(name) or {}
        return {"name": name, "label": str(profile.get("label") or name),
                "model": str(profile.get("execution_model") or ""),
                "reasoning": str(profile.get("execution_reasoning") or ""),
                "review_model": str(profile.get("review_model") or ""),
                "review_reasoning": str(profile.get("review_reasoning") or "")}
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return {"name": "", "label": "", "model": read_execution_model(root),
                "reasoning": "", "review_model": "", "review_reasoning": ""}


def read_global_mode_status(windows_root: Path) -> dict[str, Any]:
    marker = read_json(
        windows_root / "data" / "global-mode" / "global-loop-mode.json", {},
    )
    declared = (
        marker.get("schema") == "codex-loop-global-mode/v1"
        and marker.get("active") is True
        and str(marker.get("control_root") or "").casefold() == str(windows_root).casefold()
    )
    try:
        requirements = (Path.home() / ".codex" / "requirements.toml").read_text(
            encoding="utf-8-sig")
    except OSError:
        requirements = ""
    managed = (
        str(windows_root / "hooks").casefold() in requirements.casefold()
        and "--component spawn-gate" in requirements
        and "hooks = true" in requirements
    )
    try:
        agreement = (Path.home() / ".codex" / "AGENTS.md").read_text(encoding="utf-8-sig")
    except OSError:
        agreement = ""
    agreement_active = (
        "# Active Codex LOOP global mode" in agreement
        and f"LOOP_CONTROL_ROOT={windows_root}" in agreement
        and "Mandatory LOOP model routing" in agreement
    )
    return {
        "declared_active": declared,
        "hooks_trusted_or_managed": managed,
        "active_agreement_present": agreement_active,
        "effective_active": declared and managed and agreement_active,
        "control_root": str(windows_root),
    }


def model_family(model: Any, profile: dict[str, str] | None = None) -> str:
    text = str(model or "").casefold()
    configured = profile or {}
    execution = str(configured.get("model") or "").casefold()
    review = str(configured.get("review_model") or "").casefold()
    if text and text == execution:
        return "execution"
    if text and text == review:
        return "review"
    if "sol" in text:
        return "coordinator"
    return "other"


def pool_for(model: Any, role: Any = None) -> str:
    model_text = str(model or "").casefold()
    role_text = str(role or "").casefold()
    if "sol" in model_text:
        return "sol"
    if "k3" in model_text:
        return "k3"
    if "v4" in model_text:
        return "v4"
    if any(word in role_text for word in ("reviewer", "verifier", "plan_expander")):
        return "k3"
    if any(word in role_text for word in ("worker", "duty_officer", "executor", "scout")):
        return "v4"
    if "sol" in role_text:
        return "sol"
    return "other"


def semantic_task_key(name: Any, pool: str) -> tuple[str, str] | None:
    """Return a stable, display-name-based identity for fallback deduplication.

    A Desktop app-server restart can leave rollout files without a terminal
    marker.  If the same semantic task is already present in the authoritative
    native/exec roster, the rollout is evidence of the task, not another live
    task.  Keep this deliberately narrow: only an exact normalized task name
    and pool match is suppressed, so unrelated same-project work remains
    visible.
    """
    normalized = " ".join(str(name or "").strip().casefold().split())
    return (normalized, pool) if normalized else None


def runtime_id_like(value: Any) -> bool:
    """Recognize transport UUIDs that must never become public task names."""
    try:
        uuid.UUID(str(value or ""))
        return True
    except (ValueError, AttributeError):
        return False


def unnamed_task_label(role: Any, pool: str) -> str:
    """A semantic public fallback; never expose a random runtime UUID."""
    role_text = str(role or "").casefold()
    labels = {
        "reviewer": "未命名发布复审",
        "verifier": "未命名 K3 验证任务",
        "plan_expander": "未命名 K3 规划任务",
        "worker": "未命名执行任务",
        "duty_officer": "未命名故障预审",
    }
    for key, label in labels.items():
        if key in role_text:
            return label
    return "未命名 %s 任务" % ({"k3": "K3", "v4": "执行", "sol": "Sol"}.get(pool, "LOOP"))


def report_title(wsl_path: Any, root: Path) -> str:
    """Read one bounded title from a report strictly inside this LOOP root."""
    value = str(wsl_path or "").strip()
    if not value:
        return ""
    try:
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        reports_root = (root / "data" / "reports").resolve()
        if not path.is_relative_to(reports_root) or not path.is_file():
            return ""
        with path.open(encoding="utf-8", errors="replace") as handle:
            for _ in range(8):
                line = handle.readline()
                if not line:
                    break
                title = line.strip().strip("#* _").strip()
                if title.casefold() in {"verified facts", "已验证事实", "审计正文如下。"}:
                    continue
                if title:
                    return " ".join(title.split())[:96]
    except OSError:
        pass
    return ""


def opencodex_health() -> dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:10100/healthz", timeout=0.8) as response:
            value = json.loads(response.read(65536).decode("utf-8"))
        return {"ok": response.status == 200 and value.get("status") == "ok",
                "pid": value.get("pid"), "uptime": value.get("uptime")}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def merge_native_rosters(*docs: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {"pending": [], "agents": {}}
    pending_keys: set[tuple[Any, Any]] = set()
    updated_at = 0.0
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if isinstance(doc.get("updated_at"), (int, float)):
            updated_at = max(updated_at, float(doc["updated_at"]))
        for row in doc.get("pending") or []:
            if not isinstance(row, dict):
                continue
            key = (row.get("parent_session_id"), row.get("tool_use_id"))
            if key in pending_keys:
                continue
            pending_keys.add(key)
            merged["pending"].append(row)
        for agent_id, row in (doc.get("agents") or {}).items():
            if not isinstance(row, dict):
                continue
            old = merged["agents"].get(agent_id)
            if old is None or float(row.get("updated_at", 0) or 0) >= float(old.get("updated_at", 0) or 0):
                merged["agents"][agent_id] = row
    if updated_at:
        merged["updated_at"] = updated_at
    return merged


def merge_exec_rosters(*docs: dict[str, Any]) -> dict[str, Any]:
    """Merge WSL and Windows headless lifecycle views by newest generation.

    Headless workers may be supervised on either side of the WSL boundary.
    The dashboard previously read only the WSL roster, so a real Windows
    ``codex exec`` wave consumed capacity while appearing as zero concurrency.
    """
    merged: dict[str, Any] = {"schema": "codex-loop-exec-roster/v2", "jobs": {}}
    updated_at = 0.0
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if isinstance(doc.get("updated_at"), (int, float)):
            updated_at = max(updated_at, float(doc["updated_at"]))
        for packet_id, row in (doc.get("jobs") or {}).items():
            if not isinstance(row, dict):
                continue
            old = merged["jobs"].get(packet_id)
            row_ts = max(float(row.get("updated_at", 0) or 0),
                         float(row.get("heartbeat_at", 0) or 0),
                         float(row.get("started_at", 0) or 0))
            old_ts = max(float((old or {}).get("updated_at", 0) or 0),
                         float((old or {}).get("heartbeat_at", 0) or 0),
                         float((old or {}).get("started_at", 0) or 0))
            if old is None or row_ts >= old_ts:
                merged["jobs"][packet_id] = row
            updated_at = max(updated_at, row_ts)
    if updated_at:
        merged["updated_at"] = updated_at
    return merged


def newest_status_doc(*docs: dict[str, Any]) -> dict[str, Any]:
    """Choose the freshest WSL/Windows status snapshot without merging fields."""
    valid = [doc for doc in docs if isinstance(doc, dict)]
    return max(valid, key=lambda doc: float(doc.get("updated_at", 0) or 0),
               default={})


def read_session_meta(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            row = json.loads(handle.readline())
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        return payload if row.get("type") == "session_meta" else None
    except (OSError, ValueError):
        return None


def rollout_created_at(path: Path, meta: dict[str, Any]) -> float:
    """Return immutable session birth time; never use mutable file mtime."""
    value = meta.get("timestamp")
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    try:
        return path.stat().st_ctime
    except OSError:
        return 0.0


def rollout_terminal(path: Path) -> bool:
    """Return whether the latest persisted turn lifecycle state is terminal.

    A resumed Desktop parent rollout legitimately contains many historical
    ``task_complete`` events followed by a newer ``task_started``.  A raw
    substring search therefore marked every resumed parent terminal and hid
    all of its currently running children from the dashboard.
    """
    terminal = {"task_complete", "turn_aborted", "turn_failed", "thread_closed"}
    state: bool | None = None
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 131072))
            tail = handle.read().decode("utf-8", errors="replace")
        for line in tail.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            event = payload.get("type")
            if event == "task_started":
                state = False
            elif event in terminal:
                state = True
        return state is True
    except OSError:
        return False


def read_rollout_task_name(path: Path) -> str:
    """Read the immutable dispatch label once, without hydrating the rollout.

    Current LOOP prompts put ``任务名：...`` at the start of the dedicated user
    message.  The bounded read and process-lifetime cache keep this effectively
    free compared with the existing metadata/tail scan.
    """
    key = str(path)
    if key in _task_name_cache:
        return _task_name_cache[key]
    name = ""
    consumed = 0
    settings_applied = False
    fallback = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                consumed += len(line)
                if index > 256 or consumed > 2 * 1024 * 1024:
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                if (row.get("type") == "event_msg"
                        and payload.get("type") == "thread_settings_applied"):
                    settings_applied = True
                    continue
                candidates: list[str] = []
                if (row.get("type") == "response_item" and payload.get("type") == "message"
                        and payload.get("role") == "user"):
                    for item in payload.get("content") or []:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            candidates.append(item["text"])
                elif row.get("type") == "event_msg" and payload.get("type") == "user_message":
                    if isinstance(payload.get("message"), str):
                        candidates.append(payload["message"])
                for text in candidates:
                    match = _TASK_NAME.match(text)
                    if match:
                        name = " ".join(match.group(1).strip().split())[:96]
                        break
                    if settings_applied and not fallback:
                        first_line = next((item.strip() for item in text.splitlines()
                                           if item.strip() and not item.lstrip().startswith("<")), "")
                        if first_line:
                            fallback = " ".join(first_line.split())[:96]
                if name:
                    break
    except OSError:
        pass
    # A rollout can be observed after session_meta is flushed but before the
    # dedicated spawn prompt arrives.  Caching that transient blank forever
    # turns every semantic task into a random id suffix in the UI.
    name = name or fallback
    if name:
        _task_name_cache[key] = name
    return name


def read_rollout_model_profile(path: Path) -> dict[str, str]:
    """Separate bootstrap history, configured model, and current turn model."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _model_cache.get(key)
    if cached and cached[0] == mtime:
        return dict(cached[1])
    first = ""
    latest = ""
    configured = ""
    consumed = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                consumed += len(line)
                if index > 4096 or consumed > 8 * 1024 * 1024:
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                if row.get("type") == "turn_context" and isinstance(payload.get("model"), str):
                    latest = payload["model"]
                    first = first or latest
                elif row.get("type") == "event_msg" and payload.get("type") == "thread_settings_applied":
                    settings = payload.get("thread_settings")
                    if isinstance(settings, dict) and isinstance(settings.get("model"), str):
                        configured = settings["model"]
    except OSError:
        pass
    active = latest or configured or first
    inherited = first if configured and first and first != configured else ""
    profile = {
        "first_observed_model": first,
        "configured_model": configured,
        "latest_turn_model": latest,
        "active_turn_model": active,
        "inherited_history_model": inherited,
    }
    _model_cache[key] = (mtime, profile)
    return dict(profile)


def read_rollout_model(path: Path) -> str:
    """Read the actual turn model; session_meta often omits it for children.

    Roleless Desktop children inherit the Sol parent unless spawn explicitly
    selects a model.  Treating every non-K3 rollout as the execution pool hid
    that expensive bypass, so classification must use turn_context evidence.
    """
    return read_rollout_model_profile(path).get("active_turn_model", "")


def scan_rollout_subagents(sessions_root: Path) -> dict[str, Any]:
    now = time.time()
    desktop_started_at = desktop_app_server_started_at(now)
    with _rollout_lock:
        if now - float(_rollout_cache.get("checked_at", 0) or 0) < ROLLOUT_SCAN_TTL_SECONDS:
            return dict(_rollout_cache["value"])
        sessions: dict[str, dict[str, Any]] = {}
        if sessions_root.exists():
            for path in sessions_root.rglob("rollout-*.jsonl"):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if now - mtime > ROLLOUT_FILE_LOOKBACK_SECONDS:
                    continue
                # A Desktop restart cannot preserve a native child process.
                # Rollouts without terminal markers from before the current
                # app-server epoch are crash evidence, not live concurrency.
                if desktop_started_at is not None and mtime < desktop_started_at:
                    continue
                meta = read_session_meta(path)
                if not meta or not meta.get("id"):
                    continue
                sessions[str(meta["id"])] = {"meta": meta, "path": path, "mtime": mtime}
        # Take one lifecycle snapshot per rollout for this scan.  Re-reading a
        # live parent in both passes creates an avoidable race where a newly
        # appended task_started/task_complete changes the classification
        # halfway through the same dashboard sample.
        terminal_by_path = {
            str(item["path"]): rollout_terminal(item["path"])
            for item in sessions.values()
        }
        candidates: dict[str, list[dict[str, Any]]] = {}
        newest = None
        for agent_id, item in sessions.items():
            meta, path = item["meta"], item["path"]
            parent_id = meta.get("parent_thread_id")
            if not parent_id:
                continue
            parent = sessions.get(str(parent_id))
            if not parent:
                continue
            # The root can be quiet while it waits on a long-running child.
            # Child rollout activity is therefore equally valid evidence that
            # this parent group belongs to the current live task.
            group_activity = max(float(parent["mtime"]), float(item["mtime"]))
            if now - group_activity > ROLLOUT_PARENT_FRESH_SECONDS:
                continue
            if (terminal_by_path.get(str(parent["path"]), False)
                    or terminal_by_path.get(str(path), False)):
                continue
            source = meta.get("source") if isinstance(meta.get("source"), dict) else {}
            subagent = source.get("subagent") if isinstance(source.get("subagent"), dict) else {}
            spawn = subagent.get("thread_spawn") if isinstance(subagent.get("thread_spawn"), dict) else {}
            model_profile = read_rollout_model_profile(path)
            explicit_role = meta.get("agent_role") or spawn.get("agent_role")
            candidates.setdefault(str(parent_id), []).append({
                "agent_id": agent_id,
                "parent_session_id": str(parent_id),
                "name": read_rollout_task_name(path),
                "role": explicit_role or "agent",
                "agent_role_explicit": bool(explicit_role),
                "model": meta.get("model") or model_profile.get("active_turn_model", ""),
                **model_profile,
                "cwd": meta.get("cwd") or parent["meta"].get("cwd"),
                "updated_at": float(item["mtime"]),
                "created_at": rollout_created_at(path, meta),
                "recent_activity": now - float(item["mtime"]) <= ROLLOUT_ACTIVE_SECONDS,
            })
            newest = max(newest or 0.0, float(parent["mtime"]), float(item["mtime"]))
        tasks: list[dict[str, Any]] = []
        open_sessions = 0
        for parent_id, rows in candidates.items():
            parent = sessions.get(parent_id)
            group_activity = max(
                ([float(parent["mtime"])] if parent else [0.0])
                + [float(row.get("updated_at", 0) or 0) for row in rows])
            if (not parent or terminal_by_path.get(str(parent["path"]), False)
                    or now - group_activity > ROLLOUT_PARENT_FRESH_SECONDS):
                open_sessions += len(rows)
                continue
            # Every current-generation, non-terminal child is occupied until
            # its own rollout records a terminal event.  A later birth is not
            # evidence that an earlier wave ended: wide audits routinely add
            # follow-up packets more than a minute later while the first wave
            # is still working.  The former 90-second wave slicing silently
            # dropped those live children and fed a false deficit back into
            # the refill controller.  Crash-era rows are already fenced by the
            # current app-server epoch above; completed/failed/closed children
            # are excluded by rollout_terminal().
            tasks.extend(sorted(rows, key=lambda row: float(row.get("created_at", 0))))
        value = {"tasks": tasks, "open_sessions": open_sessions,
                 "updated_at": newest,
                 "desktop_started_at": desktop_started_at}
        _rollout_cache.update(checked_at=now, value=value)
        return dict(value)


def desktop_app_server_started_at(now: float | None = None) -> float | None:
    """Return the current Windows Codex app-server process epoch, cached.

    This is used only as a freshness fence for rollout fallback. Failure to
    inspect processes degrades to the previous behavior; it never changes
    lifecycle state or stops a process.
    """
    now = time.time() if now is None else now
    if now - float(_desktop_epoch_cache.get("checked_at", 0.0) or 0.0) < 10.0:
        return _desktop_epoch_cache.get("value")
    value = None
    if psutil is not None and os.name == "nt":
        try:
            for proc in psutil.process_iter(["name", "cmdline", "create_time"]):
                name = str(proc.info.get("name") or "").casefold()
                command = " ".join(proc.info.get("cmdline") or [])
                if name == "codex.exe" and "app-server" in command:
                    created = float(proc.info.get("create_time") or 0.0)
                    value = max(value or 0.0, created)
        except (psutil.Error, OSError, ValueError, TypeError):
            value = None
    _desktop_epoch_cache.update(checked_at=now, value=value)
    return value


def snapshot(root: Path, windows_root: Path = DEFAULT_WINDOWS_ROOT,
             sessions_root: Path = DEFAULT_SESSIONS_ROOT) -> dict[str, Any]:
    lifecycle = root / "data" / "lifecycle"
    refill_dir = root / "data" / "refill"
    native = merge_native_rosters(
        read_json(lifecycle / "native_roster.json", {}),
        read_json(windows_root / "data" / "lifecycle" / "native_roster.json", {}),
    )
    exec_roster = merge_exec_rosters(
        read_json(lifecycle / "exec_roster.json", {}),
        read_json(windows_root / "data" / "lifecycle" / "exec_roster.json", {}),
    )
    windows_refill = windows_root / "data" / "refill"
    refill = newest_status_doc(
        read_json(refill_dir / "refill_state.json", {}),
        read_json(windows_refill / "refill_state.json", {}),
    )
    throttle = newest_status_doc(
        read_json(refill_dir / "spawn_throttle_state.json", {}),
        read_json(windows_refill / "spawn_throttle_state.json", {}),
    )
    k3_health = max((
        read_json(root / "data" / "provider_health" / "k3.json", {}),
        read_json(windows_root / "data" / "provider_health" / "k3.json", {}),
    ), key=lambda doc: float((doc or {}).get("ts", 0) or 0), default={})
    sonnet_health = max((
        read_json(root / "data" / "provider_health" / "sonnet.json", {}),
        read_json(windows_root / "data" / "provider_health" / "sonnet.json", {}),
    ), key=lambda doc: float((doc or {}).get("ts", 0) or 0), default={})
    policy = read_policy(root)
    concurrency = policy.get("concurrency", {}) if isinstance(policy, dict) else {}
    execution_profile = read_execution_profile(root)
    rollout = scan_rollout_subagents(sessions_root)
    rollout_by_id = {str(row.get("agent_id")): row
                     for row in (rollout.get("tasks") or [])
                     if row.get("agent_id")}

    def observed_model(agent_id: Any, row: dict[str, Any]) -> Any:
        """Prefer roster evidence, then the same child's real turn_context."""
        return (row.get("model")
                or (rollout_by_id.get(str(agent_id), {}) or {}).get("model"))

    def effective_model(model: Any, role: Any) -> str:
        """Use configured role routing only when runtime evidence is not flushed yet."""
        if model:
            return str(model)
        role_text = str(role or "").casefold()
        if any(word in role_text for word in ("reviewer", "verifier", "plan_expander")):
            return str(execution_profile.get("review_model") or "")
        if any(word in role_text for word in ("worker", "duty_officer", "executor", "scout")):
            return str(execution_profile.get("model") or "")
        return ""

    def routing_metadata(agent_id: Any, role: Any, actual_model: Any) -> dict[str, Any]:
        evidence = rollout_by_id.get(str(agent_id), {}) or {}
        role_text = str(role or "").casefold()
        explicit_role = evidence.get("agent_role_explicit")
        if explicit_role is None:
            explicit_role = bool(role_text and role_text != "agent")
        if any(word in role_text for word in ("reviewer", "verifier", "plan_expander")):
            expected = str(execution_profile.get("review_model") or "")
        elif any(word in role_text for word in ("worker", "duty_officer", "executor", "scout")):
            expected = str(execution_profile.get("model") or "")
        else:
            expected = ""
        actual = str(actual_model or "")
        return {
            "first_observed_model": evidence.get("first_observed_model") or "",
            "latest_turn_model": evidence.get("latest_turn_model") or actual,
            "active_turn_model": evidence.get("active_turn_model") or actual,
            "inherited_history_model": evidence.get("inherited_history_model") or "",
            "expected_model": expected,
            "routing_violation": (not explicit_role) or bool(expected and actual and actual != expected),
        }

    counts = {name: 0 for name in (
        "running", "initializing", "idle", "completed", "failed",
        "estimated", "recent_activity", "open_sessions", "stale_native", "stale_headless")}
    counts["open_sessions"] = int(rollout.get("open_sessions", 0) or 0)
    pools = {"v4": 0, "k3": 0, "sol": 0, "other": 0}
    planes = {"desktop": 0, "headless": 0}
    projects: dict[str, int] = {}
    parents: dict[str, dict[str, Any]] = {}
    tasks: list[dict[str, Any]] = []
    recent_tasks: list[dict[str, Any]] = []
    updated: list[float] = []
    authoritative_task_keys: dict[tuple[str, str], int] = {}
    desktop_rollout_evidence = 0
    headless_updated: list[float] = []

    def remember_authoritative(name: Any, pool: str) -> None:
        key = semantic_task_key(name, pool)
        if key is not None:
            authoritative_task_keys[key] = authoritative_task_keys.get(key, 0) + 1

    def remember_parent(parent: Any, pool: str) -> None:
        if not parent:
            return
        row = parents.setdefault(str(parent), {"running": 0, "v4": 0,
                                                "k3": 0, "sol": 0, "other": 0})
        row["running"] += 1
        row[pool] = row.get(pool, 0) + 1

    for row in (native.get("pending") or []):
        if isinstance(row, dict) and row.get("status") == "pending_start":
            counts["initializing"] += 1
            task_name = row.get("task_name") or row.get("tool_use_id") or "pending"
            model = effective_model(observed_model(row.get("agent_id"), row),
                                    row.get("agent_role"))
            pool = pool_for(model, row.get("agent_role"))
            remember_authoritative(task_name, pool)
            tasks.append({"name": task_name,
                          "role": row.get("agent_role") or "agent", "pool": pool,
                          "model": str(model or ""),
                          "state": "initializing", "plane": "Desktop",
                          **routing_metadata(row.get("agent_id"), row.get("agent_role"), model)})
    for agent_id, row in (native.get("agents") or {}).items():
        if not isinstance(row, dict):
            continue
        model = effective_model(observed_model(agent_id, row), row.get("agent_role"))
        state = str(row.get("status") or "unknown")
        row_updated = float(row.get("updated_at", 0) or 0)
        has_current_rollout = str(agent_id) in rollout_by_id
        # Native hooks are event-driven, but a row without either a recent
        # event or a current-generation non-terminal rollout is stale registry
        # debris and must not inflate effective concurrency.
        if (state == "running" and not has_current_rollout
                and (row_updated <= 0 or time.time() - row_updated > 30)):
            state = "stale_native"
        if state == "running":
            counts["running"] += 1
            planes["desktop"] += 1
            if has_current_rollout:
                desktop_rollout_evidence += 1
                if (rollout_by_id.get(str(agent_id)) or {}).get("recent_activity"):
                    counts["recent_activity"] += 1
            pool = pool_for(model, row.get("agent_role"))
            pools[pool] += 1
            remember_parent(row.get("parent_session_id"), pool)
        elif state == "idle":
            counts["idle"] += 1
        elif state in {"completed", "terminal", "shutdown_pending"}:
            counts["completed"] += 1
        elif state in {"failed", "errored", "interrupted"}:
            counts["failed"] += 1
        elif state == "stale_native":
            counts["stale_native"] += 1
        if state in {"running", "idle", "shutdown_pending"}:
            pool = pool_for(model, row.get("agent_role"))
            raw_name = row.get("task_name")
            task_name = (unnamed_task_label(row.get("agent_role"), pool)
                         if not raw_name or runtime_id_like(raw_name) else raw_name)
            if state != "stale_native":
                remember_authoritative(task_name, pool)
            tasks.append({"name": task_name,
                          "agent_id": str(agent_id),
                          "role": row.get("agent_role") or "agent", "pool": pool,
                          "model": str(model or ""),
                          "state": state, "plane": "Desktop",
                          **routing_metadata(agent_id, row.get("agent_role"), model)})
        if isinstance(row.get("updated_at"), (int, float)):
            updated.append(float(row["updated_at"]))

    for row in (exec_roster.get("jobs") or {}).values():
        if not isinstance(row, dict):
            continue
        state = str(row.get("state") or "unknown")
        actual_model = effective_model(row.get("model"), row.get("role"))
        pool = pool_for(actual_model, row.get("role"))
        heartbeat = max(float(row.get("heartbeat_at", 0) or 0),
                        float(row.get("updated_at", 0) or 0))
        # A supervised headless worker promises a bounded periodic heartbeat.
        # Once that promise is stale it is forensic state, not effective
        # concurrency, even if the last durable roster state says running.
        if (state in {"starting", "running"}
                and (heartbeat <= 0 or time.time() - heartbeat > 30)):
            state = "stale_headless"
        if state == "starting":
            counts["initializing"] += 1
        elif state == "running":
            counts["running"] += 1
            planes["headless"] += 1
            pools[pool] += 1
            remember_parent(row.get("parent_session_id"), pool)
        elif state == "completed":
            counts["completed"] += 1
        elif state in {"failed", "timed_out", "cancelled", "spawn_failed"}:
            counts["failed"] += 1
        elif state == "stale_headless":
            counts["stale_headless"] += 1
        if state in {"starting", "running"}:
            task_name = row.get("task_name") or row.get("packet_id") or "worker"
            if state != "stale_headless":
                remember_authoritative(task_name, pool)
            tasks.append({"name": task_name,
                          "role": row.get("role") or "worker", "pool": pool,
                          "model": actual_model,
                          "state": state, "plane": row.get("plane") or "Headless CLI"})
            projects["codex-LOOP"] = projects.get("codex-LOOP", 0) + 1
        elif state in {"completed", "failed", "timed_out", "cancelled", "spawn_failed"}:
            task_name = (report_title(
                             row.get("published_report") or row.get("report"), root)
                         or row.get("task_name") or row.get("packet_id") or "worker")
            recent_tasks.append({
                "name": task_name,
                "role": row.get("role") or "worker",
                "pool": pool,
                "model": actual_model,
                "state": state,
                "plane": row.get("plane") or "Headless CLI",
                "finished_at": row.get("updated_at") or row.get("heartbeat_at") or row.get("started_at"),
            })
        for key in ("updated_at", "heartbeat_at"):
            if isinstance(row.get(key), (int, float)):
                updated.append(float(row[key]))
                if state in {"starting", "running", "stale_headless"}:
                    headless_updated.append(float(row[key]))

    known_native_ids = set((native.get("agents") or {}).keys())
    for row in rollout.get("tasks") or []:
        if row.get("agent_id") in known_native_ids:
            continue
        # Native/exec rosters do not currently receive every Desktop
        # multi-agent lifecycle event.  Keep the rollout fallback active in
        # the LOOP workspace as well: scan_rollout_subagents() already fences
        # crash-era files by the current app-server epoch, requires a fresh
        # non-terminal parent, and excludes terminal children.  The id/name
        # checks above and below prevent double-counting rostered children.
        actual_model = effective_model(row.get("model"), row.get("role"))
        rollout_key = semantic_task_key(row.get("name"), pool_for(actual_model, row.get("role")))
        if rollout_key is not None and authoritative_task_keys.get(rollout_key, 0) > 0:
            authoritative_task_keys[rollout_key] -= 1
            continue
        counts["running"] += 1
        counts["estimated"] += 1
        if row.get("recent_activity"):
            counts["recent_activity"] += 1
        planes["desktop"] += 1
        desktop_rollout_evidence += 1
        pool = pool_for(actual_model, row.get("role"))
        pools[pool] += 1
        remember_parent(row.get("parent_session_id"), pool)
        project = Path(str(row.get("cwd") or "")).name or "Desktop"
        projects[project] = projects.get(project, 0) + 1
        public_name = row.get("name") or unnamed_task_label(row.get("role"), pool)
        tasks.append({"name": public_name,
                      "role": row.get("role") or "agent", "pool": pool,
                      "model": actual_model,
                      **routing_metadata(row.get("agent_id"), row.get("role"), actual_model),
                      "state": "open~", "plane": "Desktop rollout · " + project})
    if isinstance(rollout.get("updated_at"), (int, float)):
        updated.append(float(rollout["updated_at"]))

    refill_parents = refill.get("parents", {}) if isinstance(refill, dict) else {}
    if isinstance(refill_parents, dict):
        for parent_id, controller_row in refill_parents.items():
            if not isinstance(controller_row, dict):
                continue
            observed = parents.setdefault(str(parent_id), {
                "running": 0, "v4": 0, "k3": 0, "sol": 0, "other": 0})
            observed.update({
                "target": int(controller_row.get("target", 0) or 0),
                "initializing": int(controller_row.get("initializing", 0) or 0),
                "pending": int((controller_row.get("pending") or {}).get("total", 0) or 0),
                "deficit": int(controller_row.get("deficit", 0) or 0),
                "spawnable": int((controller_row.get("spawnable") or {}).get("total", 0) or 0),
                "reason": controller_row.get("reason"),
                "active": controller_row.get("active") is True,
                "manifest_id": controller_row.get("manifest_id"),
                "controller_running": int(controller_row.get("running", 0) or 0),
            })

    for doc in (native, exec_roster, refill, throttle):
        if isinstance(doc.get("updated_at"), (int, float)):
            updated.append(float(doc["updated_at"]))

    # The policy is the source of truth for the next/normal LOOP pool.  A
    # persisted refill snapshot may describe an older run and must not make the
    # dashboard advertise a stale target after policy deployment.
    target = int(concurrency.get("target_total") or refill.get("target_total") or 80)
    k3_concurrency_target = int(concurrency.get("k3_target") or
                                (refill.get("preferred_target") or {}).get("k3") or 20)
    if (execution_profile.get("model")
            and execution_profile.get("model") == execution_profile.get("review_model")):
        preferred = {"shared": target}
    else:
        preferred = {
            "v4": int(concurrency.get("v4_target") or
                      (refill.get("preferred_target") or {}).get("v4") or 60),
            "k3": k3_concurrency_target,
        }
    newest = max(updated) if updated else None
    age = max(0.0, time.time() - newest) if newest else None
    headless_newest = max(headless_updated) if headless_updated else None
    headless_age = (max(0.0, time.time() - headless_newest)
                    if headless_newest is not None else None)
    windows_lifecycle = windows_root / "data" / "lifecycle"
    initialized = (any((base / name).exists()
                       for base in (lifecycle, windows_lifecycle)
                       for name in ("native_roster.json", "exec_roster.json"))
                   or (refill_dir / "refill_state.json").exists()
                   or (windows_refill / "refill_state.json").exists())
    authoritative_running = counts["running"] - counts["estimated"]
    if not initialized:
        freshness = "UNINITIALIZED"
    elif ((counts["stale_native"] > 0 or counts["stale_headless"] > 0)
          and counts["running"] == 0 and counts["initializing"] == 0):
        freshness = "STALE"
    elif counts["running"] == 0 and counts["initializing"] == 0:
        freshness = "IDLE"
    elif counts["estimated"] and authoritative_running == 0 and counts["initializing"] == 0:
        freshness = "ESTIMATED"
    # Headless jobs promise a bounded periodic lifecycle heartbeat, so an old active
    # headless row remains STALE.  Desktop-native jobs are event-driven: after
    # 30 seconds without a tool event they are QUIET only when every displayed
    # Desktop task is independently backed by a non-terminal rollout from the
    # current app-server generation.  A stale native roster without matching
    # rollout evidence remains STALE; elapsed time alone never proves life.
    elif (planes["headless"] > 0
          and (headless_age is None or headless_age > 30)):
        freshness = "STALE"
    elif age is not None and age > 30:
        desktop_quiet_proven = (
            planes["headless"] == 0
            and planes["desktop"] > 0
            and desktop_rollout_evidence == planes["desktop"]
            and rollout.get("desktop_started_at") is not None
        )
        freshness = "QUIET" if desktop_quiet_proven else "STALE"
    else:
        freshness = "LIVE"

    tasks.sort(key=lambda row: (row["state"] != "running", row["pool"], str(row["name"])))
    recent_tasks.sort(key=lambda row: float(row.get("finished_at") or 0), reverse=True)
    model_counts = {
        name: 0 for name in ("execution", "review", "coordinator", "other")
    }
    for row in tasks:
        if row.get("state") in {"running", "open~"}:
            family = model_family(row.get("model"), execution_profile)
            model_counts[family] += 1
    return {
        "timestamp": time.time(), "root": str(root), "windows_root": str(windows_root),
        "freshness": freshness,
        "liveness": {
            "desktop_rollout_evidence": desktop_rollout_evidence,
            "desktop_app_server_started_at": rollout.get("desktop_started_at"),
            "headless_heartbeat_required": True,
            "headless_age_seconds": (round(headless_age, 1)
                                     if headless_age is not None else None),
        },
        "age_seconds": round(age, 1) if age is not None else None,
        # Capacity headroom is not refill debt.  Actual debt is demand-backed
        # and comes from the controller; when its queue is empty this remains
        # zero even if the machine has spare capacity.
        "target": target,
        "headroom": max(0, target - counts["running"]),
        "deficit": int(((refill.get("deficit") or {}).get("total") or 0)),
        "preferred": preferred, "execution": execution_profile,
        "global_mode": read_global_mode_status(windows_root),
        "counts": counts, "pools": pools, "planes": planes,
        "models": model_counts,
        "parents": parents,
        "projects": dict(sorted(projects.items())),
        "tasks": tasks[:64], "recent_tasks": recent_tasks[:24], "opencodex": opencodex_health(),
        "provider_health": {
            "k3": {
                "status": str(k3_health.get("status") or "unknown"),
                "last_error_kind": k3_health.get("last_error_kind"),
                "http_status": k3_health.get("http_status"),
                "backoff_until": float(k3_health.get("backoff_until", 0) or 0),
                "updated_at": float(k3_health.get("ts", 0) or 0),
            },
            "sonnet": {
                "status": str(sonnet_health.get("status") or "unknown"),
                "last_error_kind": sonnet_health.get("last_error_kind"),
                "http_status": sonnet_health.get("http_status"),
                "backoff_until": float(sonnet_health.get("backoff_until", 0) or 0),
                "updated_at": float(sonnet_health.get("ts", 0) or 0),
            },
        },
        "rollout_fallback": {"open_estimate": counts["estimated"],
                              "open_quiet": counts["open_sessions"],
                              "active_seconds": int(ROLLOUT_ACTIVE_SECONDS),
                              "parent_fresh_seconds": int(ROLLOUT_PARENT_FRESH_SECONDS)},
        "throttle": {"backoff_active": float(throttle.get("blocked_until", 0) or 0) > time.time(),
                     "blocked_until": throttle.get("blocked_until"),
                     "last_error": throttle.get("last_error")},
    }


# The dashboard intentionally presents observed runtime state only.  Controller
# debt, stale-session forensics and historical counters remain in /api/status,
# but are omitted here so capacity, demand and model identity cannot be confused.
_LEGACY_PAGE = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codex LOOP 实时状态</title><style>
:root{color-scheme:light dark;--bg:#f5f5f5;--fg:#171717;--muted:#666;--card:#fff;--line:#ddd;--ok:#16803c;--bad:#b42318;--chip:#eef2ff}
@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--muted:#aaa;--card:#1b1b1b;--line:#333;--ok:#54c77a;--bad:#ff746a;--chip:#252a3a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,"Segoe UI",sans-serif}main{max-width:980px;margin:auto;padding:18px}h1{font-size:20px;margin:0}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:14px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.label{color:var(--muted)}.value{font-size:30px;font-weight:650}.models{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}.model{background:var(--chip);border-radius:8px;padding:7px 10px}.model b{font-size:18px;margin-left:5px}.ok{color:var(--ok)}.bad{color:var(--bad)}table{width:100%;border-collapse:collapse;margin-top:14px}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:500}.task-model{white-space:nowrap}@media(max-width:650px){.grid{grid-template-columns:1fr}th:nth-child(3),td:nth-child(3){display:none}}
</style></head><body><main><div class="top"><h1>Codex LOOP · 实时状态</h1><span id="stamp" class="muted">连接中…</span></div>
<div class="grid">
<section class="card"><div class="label">当前运行</div><div class="value" id="running">–</div><div class="muted">全局容量 <span id="target">80</span> · 可用余量 <span id="headroom">–</span></div></section>
<section class="card"><div class="label">承载平面</div><div class="value"><span id="desktop">0</span> / <span id="headless">0</span></div><div class="muted">Desktop / Headless</div></section>
<section class="card"><div class="label">OpenCodex</div><div class="value" id="gateway">–</div><div class="muted" id="gateway-detail"></div></section>
</div>
<section class="card"><div class="label">实际运行模型</div><div class="models"><span class="model">执行 <b id="model-execution">0</b></span><span class="model">审查 <b id="model-review">0</b></span><span class="model">协调 <b id="model-coordinator">0</b></span><span class="model">其他 <b id="model-other">0</b></span></div><div id="profile" class="muted"></div><div id="provider-health" class="muted" hidden></div></section>
<table aria-label="当前运行任务"><thead><tr><th>具体任务</th><th>实际模型</th><th>执行面</th></tr></thead><tbody id="tasks"><tr><td colspan="3" class="muted">暂无运行任务</td></tr></tbody></table>
<script>
const $=id=>document.getElementById(id),clean=x=>String(x??'');
function shortModel(value){const v=clean(value);return v?(v.split('/').pop()||v):'—'}
function taskModel(x){let label=clean(x.model)?shortModel(x.model):(x.pool==='k3'?'审核池·模型待观测':x.pool==='v4'?'执行池·模型待观测':'模型待观测');if(x.routing_violation)label+=' ⚠ 路由违规';else if(clean(x.inherited_history_model))label+=' · 启动历史 '+shortModel(x.inherited_history_model);return label}
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const d=await r.json(),m=d.models||{};
$('running').textContent=d.counts.running;$('target').textContent=d.target;$('headroom').textContent=d.headroom;$('desktop').textContent=d.planes.desktop;$('headless').textContent=d.planes.headless;
$('model-execution').textContent=m.execution||0;$('model-review').textContent=m.review||0;$('model-coordinator').textContent=m.coordinator||0;$('model-other').textContent=m.other||0;
$('profile').textContent='活动配置：'+clean(d.execution?.name||'—')+' · 执行 '+shortModel(d.execution?.model)+' · 审核 '+shortModel(d.execution?.review_model);
const kh=d.provider_health?.k3||{},reviewActive=(m.review||0)>0,backoff=Math.max(0,Math.ceil((kh.backoff_until||0)-d.timestamp)),ph=$('provider-health');ph.hidden=!reviewActive;if(reviewActive){ph.textContent='审查上游：'+clean(kh.status||'unknown')+(kh.http_status?(' · HTTP '+kh.http_status):'')+(backoff?(' · 退避 '+backoff+' 秒'):'');ph.className='muted '+(kh.status==='healthy'?'ok':kh.status==='unhealthy'?'bad':'')}
$('gateway').textContent=d.opencodex.ok?'健康':'异常';$('gateway').className='value '+(d.opencodex.ok?'ok':'bad');$('gateway-detail').textContent=d.opencodex.ok?('PID '+clean(d.opencodex.pid)):clean(d.opencodex.error);
$('stamp').textContent=clean(d.freshness)+' · '+new Date(d.timestamp*1000).toLocaleTimeString();
const body=$('tasks');body.replaceChildren();const tasks=d.tasks||[];if(!tasks.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=3;td.className='muted';td.textContent='暂无运行任务';tr.append(td);body.append(tr)}
for(const x of tasks){const tr=document.createElement('tr');for(const [value,cls] of [[x.name,''],[taskModel(x),'task-model'],[x.plane,'']]){const td=document.createElement('td');td.textContent=clean(value);td.className=cls;tr.append(td)}body.append(tr)}
}catch(e){$('stamp').textContent='监视器异常 · '+e.message}}
function scheduleRefresh(){setTimeout(function(){refresh().finally(scheduleRefresh)},2000)}
refresh().finally(scheduleRefresh);
</script></main></body></html>"""


PAGE = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codex LOOP 实时状态</title><style>
:root{color-scheme:light dark;--bg:#f5f5f5;--fg:#171717;--muted:#666;--card:#fff;--line:#ddd;--ok:#16803c;--bad:#b42318;--chip:#eef2ff}
@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--muted:#aaa;--card:#1b1b1b;--line:#333;--ok:#54c77a;--bad:#ff746a;--chip:#252a3a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,"Segoe UI",sans-serif}main{max-width:1080px;margin:auto;padding:18px}h1{font-size:20px;margin:0}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:14px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.label{color:var(--muted)}.value{font-size:26px;font-weight:650}.models{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}.model{background:var(--chip);border-radius:8px;padding:7px 10px}.model b{font-size:18px;margin-left:5px}.ok{color:var(--ok)}.bad{color:var(--bad)}table{width:100%;border-collapse:collapse;margin-top:14px}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:500}.task-model{white-space:nowrap}@media(max-width:780px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:520px){.grid{grid-template-columns:1fr}th:nth-child(3),td:nth-child(3){display:none}}
</style></head><body><main><div class="top"><h1>Codex LOOP · 实时状态</h1><span id="stamp" class="muted">连接中…</span></div>
<div class="grid">
<section class="card"><div class="label">LOOP 全局模式</div><div class="value" id="loop-mode">—</div><div class="muted" id="loop-detail"></div></section>
<section class="card"><div class="label">当前运行</div><div class="value" id="running">—</div><div class="muted">全局容量 <span id="target">80</span> · 可用余量 <span id="headroom">—</span></div></section>
<section class="card"><div class="label">承载平面</div><div class="value"><span id="desktop">0</span> / <span id="headless">0</span></div><div class="muted">Desktop / Headless</div></section>
<section class="card"><div class="label">OpenCodex</div><div class="value" id="gateway">—</div><div class="muted" id="gateway-detail"></div></section>
</div>
<section class="card"><div class="label">实际运行模型</div><div class="models"><span class="model">执行 <b id="model-execution">0</b></span><span class="model">审查 <b id="model-review">0</b></span><span class="model">协调 <b id="model-coordinator">0</b></span><span class="model">其他 <b id="model-other">0</b></span></div><div id="profile" class="muted"></div><div id="provider-health" class="muted" hidden></div></section>
<table aria-label="当前运行任务"><thead><tr><th>具体任务</th><th>实际模型</th><th>执行平面</th></tr></thead><tbody id="tasks"><tr><td colspan="3" class="muted">暂无运行任务</td></tr></tbody></table>
<script>
const $=id=>document.getElementById(id),clean=x=>String(x??'');
function shortModel(value){const v=clean(value);return v?(v.split('/').pop()||v):'—'}
function taskModel(x){let label=clean(x.model)?shortModel(x.model):(x.pool==='k3'?'审核池·模型待观测':x.pool==='v4'?'执行池·模型待观测':'模型待观测');if(x.routing_violation)label+=' ⚠ 路由违规';else if(clean(x.inherited_history_model))label+=' · 启动历史 '+shortModel(x.inherited_history_model);return label}
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const d=await r.json(),m=d.models||{},gm=d.global_mode||{};
const effective=gm.effective_active===true;$('loop-mode').textContent=effective?'有效':'未生效';$('loop-mode').className='value '+(effective?'ok':'bad');$('loop-detail').textContent='声明 '+(gm.declared_active?'是':'否')+' · 托管 Hook '+(gm.hooks_trusted_or_managed?'是':'否')+' · 全局协议 '+(gm.active_agreement_present?'是':'否');
$('running').textContent=d.counts.running;$('target').textContent=d.target;$('headroom').textContent=d.headroom;$('desktop').textContent=d.planes.desktop;$('headless').textContent=d.planes.headless;
$('model-execution').textContent=m.execution||0;$('model-review').textContent=m.review||0;$('model-coordinator').textContent=m.coordinator||0;$('model-other').textContent=m.other||0;
$('profile').textContent='活动配置：'+clean(d.execution?.name||'—')+' · 执行 '+shortModel(d.execution?.model)+' · 审核 '+shortModel(d.execution?.review_model);
const kh=d.provider_health?.k3||{},reviewActive=(m.review||0)>0,backoff=Math.max(0,Math.ceil((kh.backoff_until||0)-d.timestamp)),ph=$('provider-health');ph.hidden=!reviewActive;if(reviewActive){ph.textContent='审查上游：'+clean(kh.status||'unknown')+(kh.http_status?(' · HTTP '+kh.http_status):'')+(backoff?(' · 退避 '+backoff+' 秒'):'');ph.className='muted '+(kh.status==='healthy'?'ok':kh.status==='unhealthy'?'bad':'')}
$('gateway').textContent=d.opencodex.ok?'健康':'异常';$('gateway').className='value '+(d.opencodex.ok?'ok':'bad');$('gateway-detail').textContent=d.opencodex.ok?('PID '+clean(d.opencodex.pid)):clean(d.opencodex.error);
$('stamp').textContent=clean(d.freshness)+' · '+new Date(d.timestamp*1000).toLocaleTimeString();
const body=$('tasks');body.replaceChildren();const tasks=d.tasks||[];if(!tasks.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=3;td.className='muted';td.textContent='暂无运行任务';tr.append(td);body.append(tr)}
for(const x of tasks){const tr=document.createElement('tr');for(const [value,cls] of [[x.name,''],[taskModel(x),'task-model'],[x.plane,'']]){const td=document.createElement('td');td.textContent=clean(value);td.className=cls;tr.append(td)}body.append(tr)}
}catch(e){$('stamp').textContent='监视器异常 · '+e.message}}
function scheduleRefresh(){setTimeout(function(){refresh().finally(scheduleRefresh)},2000)}refresh().finally(scheduleRefresh);
</script></main></body></html>"""


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """HTTP server with single-owner bind semantics on Windows."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        # On Windows SO_REUSEADDR permits multiple unrelated processes to bind
        # the same listening address.  SO_EXCLUSIVEADDRUSE must be set before
        # bind(), so doing this after ThreadingHTTPServer.__init__ is too late.
        if os.name == "nt":
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", 12)
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    root: Path
    windows_root: Path
    sessions_root: Path

    def send_bytes(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/status":
            now = time.time()
            with _snapshot_lock:
                if (now - float(_snapshot_cache.get("checked_at", 0) or 0) >= _SNAPSHOT_CACHE_TTL
                        or _snapshot_cache.get("value") is None):
                    _snapshot_cache["value"] = snapshot(self.root, self.windows_root, self.sessions_root)
                    _snapshot_cache["checked_at"] = time.time()
                data = _snapshot_cache["value"]
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_bytes(200, "application/json; charset=utf-8", body)
        elif self.path == "/healthz":
            self.send_bytes(200, "application/json", b'{"status":"ok"}')
        elif self.path in {"/", "/index.html"}:
            self.send_bytes(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        else:
            self.send_bytes(404, "text/plain; charset=utf-8", "not found".encode())

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path(os.environ.get("CODEX_LOOP_MONITOR_ROOT", str(DEFAULT_ROOT))))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--windows-root", type=Path, default=DEFAULT_WINDOWS_ROOT)
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    args = parser.parse_args()
    Handler.root = args.root
    Handler.windows_root = args.windows_root
    Handler.sessions_root = args.sessions_root
    try:
        server = ExclusiveThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        print(f"[loop_monitor_server] bind failed on port {args.port}: {exc}", file=sys.stderr)
        return 1
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
