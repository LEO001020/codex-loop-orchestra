#!/usr/bin/env python3
"""Import an explicitly supplied parent manifest into the canonical packet ledger.

This is an admission boundary, not a watcher and not a dispatcher.  It reads
one manifest, validates that every task is an explicit read-only leaf packet,
and atomically creates packet files plus ``DISPATCHABLE`` ledger entries.  A
later refill transaction remains the only code allowed to turn those entries
into births.

The importer deliberately does not infer work from Desktop history, a UI
deficit, or an arbitrary parent rollout.  The caller must name the manifest
path explicitly.  That keeps the old parent watcher from becoming a second
scheduler while still providing the missing parent -> ledger handoff.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

try:
    from orchestration_common import (LoopPaths, RefillPolicy, append_ndjson,
                                      atomic_write_json, file_lock, read_json)
except ImportError:  # pragma: no cover - direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from orchestration_common import (LoopPaths, RefillPolicy, append_ndjson,
                                      atomic_write_json, file_lock, read_json)


class ParentManifestError(ValueError):
    """A manifest is not admissible for the packet ledger."""


ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
ROLES = frozenset({"worker", "verifier", "reviewer", "plan_expander"})
K3_ROLES = frozenset({"verifier", "reviewer", "plan_expander"})
TERMINAL_PARENT_STATES = frozenset({
    "completed", "complete", "done", "failed", "cancelled", "canceled",
    "stopped", "terminated", "terminal", "dead_letter",
})
ACTIVE_PARENT_STATES = frozenset({
    "active", "running", "loaded", "execution", "in_progress", "pending",
})


def _text(value: Any, field: str, *, maximum: int = 20000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ParentManifestError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > maximum:
        raise ParentManifestError(f"{field} exceeds {maximum} characters")
    return value


def _id(value: Any, field: str) -> str:
    value = _text(value, field, maximum=96)
    if not ID_RE.fullmatch(value):
        raise ParentManifestError(f"{field} has invalid id syntax")
    return value


def _list_of_text(value: Any, field: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ParentManifestError(f"{field} must be a non-empty list")
    out: list[str] = []
    for index, item in enumerate(value):
        out.append(_text(item, f"{field}[{index}]", maximum=4000))
    return out


def _relative_authorized_paths(value: Any, field: str) -> list[str]:
    paths = _list_of_text(value, field)
    for item in paths:
        path = item.replace("\\", "/")
        if path.startswith("/") or re.match(r"^[A-Za-z]:/", path):
            raise ParentManifestError(f"{field} must contain relative paths")
        parts = [part for part in path.split("/") if part not in ("", ".")]
        if ".." in parts:
            raise ParentManifestError(f"{field} may not escape the task cwd")
    return paths


def allowed_workspace_roots(root: Path,
                            extra_roots: list[Path] | tuple[Path, ...] = ()) -> tuple[Path, ...]:
    """Return explicit read roots; never derive roots from task input."""
    candidates = [root.resolve(), *(Path(item).resolve() for item in extra_roots)]
    out: list[Path] = []
    for candidate in candidates:
        if candidate not in out and candidate.is_dir():
            out.append(candidate)
    return tuple(out)


def _safe_cwd(value: Any, root: Path,
              extra_roots: list[Path] | tuple[Path, ...] = ()) -> str:
    raw = _text(value, "cwd", maximum=1000)
    raw_path = raw.replace("\\", "/")
    if ".." in [part for part in raw_path.split("/") if part]:
        raise ParentManifestError("cwd may not contain parent traversal segments")
    try:
        raw_path_obj = Path(raw).expanduser()
        cwd = (raw_path_obj if raw_path_obj.is_absolute()
               else root / raw_path_obj).resolve()
    except OSError as exc:
        raise ParentManifestError(f"cwd cannot be resolved: {exc}") from exc
    roots = allowed_workspace_roots(root, extra_roots)
    if not any(cwd == allowed or allowed in cwd.parents for allowed in roots):
        raise ParentManifestError(
            f"cwd is outside the explicit read-only workspaces: {cwd}")
    if not cwd.is_dir():
        raise ParentManifestError(f"cwd is not an existing directory: {cwd}")
    return str(cwd)


def _parent_state(doc: Mapping[str, Any]) -> str | None:
    value = doc.get("parent_state", doc.get("status", doc.get("state")))
    if value is None:
        return None
    state = _text(value, "parent_state", maximum=64).lower()
    if state in TERMINAL_PARENT_STATES:
        raise ParentManifestError(f"parent_inactive: terminal parent state {state}")
    if state not in ACTIVE_PARENT_STATES:
        raise ParentManifestError(f"parent_inactive: unsupported parent state {state}")
    return state


def validate_manifest(doc: Any, root: Path,
                      extra_roots: list[Path] | tuple[Path, ...] = ()) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise ParentManifestError("manifest must be a JSON object")
    if doc.get("schema") != "codex-loop-parent-refill/v1":
        raise ParentManifestError("unsupported or missing manifest schema")
    manifest_id = _id(doc.get("manifest_id"), "manifest_id")
    parent_session_id = _text(doc.get("parent_session_id"), "parent_session_id", maximum=64)
    if not UUID_RE.fullmatch(parent_session_id):
        raise ParentManifestError("parent_session_id must be a UUID")
    if doc.get("enabled") is not True:
        raise ParentManifestError("parent_inactive: enabled must be true")
    if doc.get("mode") != "read-only":
        raise ParentManifestError("only mode=read-only is accepted")
    _parent_state(doc)
    # One manifest belongs to one parent/dialogue.  The independent aggregate
    # across parents and planes is governed by target_total.
    refill_policy = RefillPolicy.load(LoopPaths.resolve(root))
    max_active = refill_policy.dialogue_target()
    max_backlog = refill_policy.target_total()
    target = doc.get("target_active")
    if (not isinstance(target, int) or isinstance(target, bool)
            or not 1 <= target <= max_active):
        raise ParentManifestError(
            f"target_active must be an integer in [1, {max_active}]")
    cwd = _safe_cwd(doc.get("cwd"), root, extra_roots)
    tasks = doc.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ParentManifestError("tasks must be a non-empty list")
    if len(tasks) > max_backlog:
        raise ParentManifestError(
            f"manifest may contain at most {max_backlog} tasks")
    seen: set[str] = set()
    clean_tasks: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ParentManifestError(f"tasks[{index}] must be an object")
        task_id = _id(task.get("task_id"), f"tasks[{index}].task_id")
        if task_id in seen:
            raise ParentManifestError(f"duplicate task_id {task_id}")
        seen.add(task_id)
        task_name = _text(task.get("task_name"), f"tasks[{index}].task_name", maximum=160)
        role = task.get("role")
        if role not in ROLES:
            raise ParentManifestError(
                f"tasks[{index}].role must be explicit worker/verifier/reviewer/plan_expander")
        if task.get("sandbox") != "read-only":
            raise ParentManifestError(f"tasks[{index}] must set sandbox=read-only")
        if task.get("allow_write") is True or task.get("workspace_write") is True:
            raise ParentManifestError(f"tasks[{index}] contains a write authorization")
        goal = task.get("goal", task.get("prompt"))
        goal = _text(goal, f"tasks[{index}].goal", maximum=20000)
        authorized = _relative_authorized_paths(
            task.get("authorized_paths"), f"tasks[{index}].authorized_paths")
        acceptance = _list_of_text(task.get("acceptance"),
                                   f"tasks[{index}].acceptance")
        clean_tasks.append({
            "task_id": task_id,
            "task_name": task_name,
            "goal": goal,
            "authorized_paths": authorized,
            "acceptance": acceptance,
            "role": role,
            "sandbox": "read-only",
            "cwd": _safe_cwd(task.get("cwd", cwd), root, extra_roots),
        })
    return {
        "schema": doc["schema"], "manifest_id": manifest_id,
        "parent_session_id": parent_session_id, "enabled": True,
        "target_active": target, "cwd": cwd, "mode": "read-only",
        "tasks": clean_tasks,
    }


def packet_id_for(manifest_id: str, task_id: str) -> str:
    digest = hashlib.sha256(f"{manifest_id}\0{task_id}".encode("utf-8")).hexdigest()
    return f"parent-{digest[:48]}"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def import_manifest(root: Path | str, manifest: Path | str, *,
                    allow_roots: list[Path] | tuple[Path, ...] = ()) -> dict[str, Any]:
    paths = LoopPaths.resolve(root)
    root_path = paths.root
    manifest_path = Path(manifest).expanduser().resolve()
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ParentManifestError(f"manifest unreadable: {exc}") from exc
    clean = validate_manifest(doc, root_path, allow_roots)
    manifest_id = clean["manifest_id"]
    parent_state_path = paths.refill_dir / "parent_sessions.json"
    with file_lock(paths.refill_dir / ".parent_sessions.lock"):
        parent_states = read_json(parent_state_path, {"parents": {}}) or {"parents": {}}
        prior_parent = (parent_states.get("parents") or {}).get(
            clean["parent_session_id"])
        if (isinstance(prior_parent, dict)
                and prior_parent.get("active") is False):
            raise ParentManifestError(
                "parent_inactive: stopped parent cannot be implicitly reactivated")
    packet_dir = paths.data / "packets"
    packet_dir.mkdir(parents=True, exist_ok=True)
    # Reuse the deployed ledger lock name.  A second dot-prefixed lock would
    # look protected while remaining invisible to existing operators/tools.
    ledger_lock = paths.data / "progress_ledger.lock"
    imported = 0
    existing = 0
    with file_lock(ledger_lock):
        ledger = read_json(paths.ledger, {"packets": {}}) or {"packets": {}}
        if not isinstance(ledger, dict) or not isinstance(ledger.get("packets", {}), dict):
            raise ParentManifestError("progress ledger is unreadable or has invalid packets")
        packets = ledger["packets"]
        planned: list[tuple[Path, dict[str, Any], str, dict[str, Any], bool, bool]] = []
        for task in clean["tasks"]:
            packet_id = packet_id_for(manifest_id, task["task_id"])
            packet = {
                "packet_id": packet_id, "goal": task["goal"],
                "authorized_paths": task["authorized_paths"],
                "acceptance": task["acceptance"], "constraints": ["read-only"],
                "task_name": task["task_name"], "role": task["role"],
                "sandbox": "read-only", "cwd": task["cwd"],
                "parent_session_id": clean["parent_session_id"],
                "manifest_id": manifest_id, "parent_enabled": True,
                "created_by": "parent_manifest_importer",
            }
            entry = {
                "state": "DISPATCHABLE", "role": task["role"],
                "history": [], "attempts": 0,
                "pool_hint": "k3" if task["role"] in K3_ROLES else "v4",
                "task_name": task["task_name"], "goal": task["goal"],
                "parent_session_id": clean["parent_session_id"],
                "manifest_id": manifest_id, "parent_enabled": True,
                "created_by": "parent_manifest_importer", "sandbox": "read-only",
                "cwd": task["cwd"], "authorized_paths": task["authorized_paths"],
                "acceptance": task["acceptance"],
            }
            packet_path = packet_dir / f"{packet_id}.json"
            prior_packet = read_json(packet_path, None)
            prior_entry = packets.get(packet_id)
            if prior_packet is not None or prior_entry is not None:
                # Ledger lifecycle fields evolve after admission.  Idempotent
                # re-import compares only the immutable admission contract;
                # state/history/attempts/current_run_id must not make a valid
                # manifest look like a collision, while any task metadata
                # change remains a hard fail.
                immutable_entry = {key: value for key, value in entry.items()
                                   if key not in {"state", "history", "attempts"}}
                prior_immutable = ({key: prior_entry.get(key)
                                    for key in immutable_entry}
                                   if isinstance(prior_entry, dict) else None)
                if ((prior_packet is not None and prior_packet != packet)
                        or (prior_entry is not None
                            and prior_immutable != immutable_entry)):
                    raise ParentManifestError(
                        f"packet collision or content mismatch: {packet_id}")
                if prior_packet is not None and prior_entry is not None:
                    existing += 1
                    continue
            # A crash can happen between the packet-file replace and the
            # ledger replace.  Exact one-sided state is recoverable; different
            # content above remains a hard collision.
            planned.append((packet_path, packet, packet_id, entry,
                            prior_packet is None, prior_entry is None))
        # The entire batch is validated before the first production write.
        # A collision in task N therefore cannot leave tasks 1..N-1 imported.
        for packet_path, packet, packet_id, entry, write_packet, write_entry in planned:
            if write_packet:
                atomic_write_json(packet_path, packet)
            if write_entry:
                packets[packet_id] = entry
            imported += 1
        ledger["schema"] = ledger.get("schema", "codex-loop-statemachine/v2")
        atomic_write_json(paths.ledger, ledger)
    with file_lock(paths.refill_dir / ".parent_sessions.lock"):
        parent_states = read_json(parent_state_path, {"parents": {}}) or {"parents": {}}
        parents = parent_states.setdefault("parents", {})
        current_parent = parents.get(clean["parent_session_id"])
        if (isinstance(current_parent, dict)
                and current_parent.get("active") is False):
            # Stop may race between the admission check and this commit.  The
            # imported packets remain durable and auditable, but an inactive
            # parent must never be silently reactivated or scheduled.
            raise ParentManifestError(
                "parent_inactive: parent stopped while manifest was importing")
        parents[clean["parent_session_id"]] = {
            "active": True, "manifest_id": manifest_id,
            "target_active": clean["target_active"],
            "cwd": clean["cwd"], "mode": clean["mode"],
            "updated_at": __import__("time").time(),
            "source": "parent_manifest_importer",
        }
        atomic_write_json(parent_state_path, parent_states)
    append_ndjson(paths.events, {
        "ts": __import__("time").time(), "event": "parent_manifest_imported",
        "manifest_id": manifest_id, "parent_session_id": clean["parent_session_id"],
        "imported": imported, "existing": existing,
        "packet_ids": [packet_id_for(manifest_id, task["task_id"])
                        for task in clean["tasks"]],
        "created_by": "parent_manifest_importer",
    }, lock_path=paths.events_lock)
    # Admission creates durable demand.  Wake the existing coalesced
    # packet-only actuator immediately; otherwise a perfectly valid manifest
    # can sit DISPATCHABLE until an unrelated child happens to terminate.
    # This is a wake, not a second scheduler: the consumer re-reads the ledger,
    # enforces parent/model/capacity gates and suppresses already-active runs.
    try:
        from refill_consumer_v2 import schedule_run
        actuator = schedule_run(paths.root, source="parent_manifest_imported")
    except (ImportError, OSError, ValueError) as exc:
        actuator = {"status": "failed", "error": "%s: %s" %
                    (type(exc).__name__, exc)}
        append_ndjson(paths.events, {
            "ts": __import__("time").time(), "event": "refill_actuator_failed",
            "source": "parent_manifest_imported", "manifest_id": manifest_id,
            "error": actuator["error"],
        }, lock_path=paths.events_lock)
    return {"status": "imported" if imported else "existing",
            "manifest_id": manifest_id, "parent_session_id": clean["parent_session_id"],
            "imported": imported, "existing": existing,
            "packet_ids": [packet_id_for(manifest_id, task["task_id"])
                            for task in clean["tasks"]],
            "actuator": actuator}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import an explicit read-only parent manifest")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--allow-root", type=Path, action="append", default=[],
                        help="explicit additional read-only workspace root")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(import_manifest(args.root, args.manifest,
                                         allow_roots=args.allow_root), ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
