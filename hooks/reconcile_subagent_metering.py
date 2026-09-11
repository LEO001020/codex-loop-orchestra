#!/usr/bin/env python3
"""Recover missing F2 SubagentStart records from Codex rollout metadata.

This is the F2 second truth source for any collaboration spawn whose native
SubagentStart record is absent: a trusted Stop hook passes the explicit root
session id, and this script appends only records that can be verified from that
root's structured child rollout metadata.

Recovered records never claim that the native SubagentStart hook ran.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


ROLE_MARKERS = {
    "worker": "You are an Executor",
    "reviewer": "You are the release-gate Reviewer",
    "verifier": "You are the L2 Verifier",
    "duty_officer": "You are the Duty Officer",
}

# Native rollout filenames are rollout-<ts>-<session id>.jsonl.  The id is
# the payload `id` for every rollout type observed in the real sessions tree
# (user roots and subagents alike), so the filename must be parsed strictly
# instead of substring-matched.
ROLLOUT_FILENAME_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)


def load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def filename_session_id(path: Path) -> str | None:
    match = ROLLOUT_FILENAME_RE.match(path.name)
    return match.group(1) if match else None


def _same_normcase(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str) or not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _meta_identity(payload: dict) -> dict:
    """Critical self-meta fields of one session_meta payload."""
    return {
        "id": payload.get("id"),
        "session_id": payload.get("session_id"),
        "thread_source": payload.get("thread_source"),
        "cwd": payload.get("cwd"),
        "agent_id": payload.get("agent_id"),
        "agent_role": payload.get("agent_role"),
    }


def _identity_conflict(metas: list[dict], filename_id: str) -> bool:
    """True when multiple self session_meta payloads disagree on identity.

    A self meta is one whose `id` equals the rollout filename id.  Embedded
    parent/root session_meta records (id != filename id) are context, not
    identity claims for this session, and never trigger a conflict.  Any
    disagreement on a critical field fails closed instead of letting the
    last-written payload silently override the earlier one.
    """
    self_metas = [meta for meta in metas if meta.get("id") == filename_id]
    if len(self_metas) <= 1:
        return False
    first = _meta_identity(self_metas[0])
    return any(_meta_identity(other) != first for other in self_metas[1:])


def session_meta(records: list[dict], filename_id: str) -> dict:
    all_metas = [
        record.get("payload", {})
        for record in records
        if record.get("type") == "session_meta"
        and isinstance(record.get("payload", {}), dict)
    ]
    # Only self metas describe this session; embedded parent/root session_meta
    # records are context and must not override the child's own fields.
    metas = [meta for meta in all_metas if meta.get("id") == filename_id]
    if not metas or _identity_conflict(metas, filename_id):
        return {}
    merged: dict = {}
    for payload in metas:
        merged.update(payload)
    return merged


def message_text(payload: dict) -> str:
    parts: list[str] = []
    for item in payload.get("content", []):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def structured_role(records: list[dict]) -> str | None:
    developer_messages: list[str] = []
    for record in records:
        if record.get("type") != "response_item":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "message" and payload.get("role") == "developer":
            developer_messages.append(message_text(payload))

    matches = {
        role
        for role, marker in ROLE_MARKERS.items()
        if any(text.startswith(marker) for text in developer_messages)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def initial_turn_context(records: list[dict]) -> dict:
    for record in records:
        if record.get("type") == "turn_context":
            payload = record.get("payload", {})
            return payload if isinstance(payload, dict) else {}
    return {}


def same_path(left: object, right: Path) -> bool:
    if not isinstance(left, str) or not left:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def recover(args: argparse.Namespace) -> dict[str, dict]:
    files = sorted(args.sessions.rglob("rollout-*.jsonl"))

    root_matches: list[Path] = []
    for path in files:
        filename_id = filename_session_id(path)
        if not filename_id:
            continue
        meta = session_meta(load_records(path), filename_id)
        # The root rollout itself is located by filename id: the id in the
        # filename must equal the id/session_id recorded in its metadata.
        # Root must be unambiguous: id == filename id == session_id == the
        # explicit root session id, all four agreeing.
        if (meta.get("thread_source") == "user"
                and meta.get("id") == args.root_session_id
                and meta.get("session_id") == args.root_session_id
                and filename_id == args.root_session_id
                and same_path(meta.get("cwd"), args.expected_cwd)):
            root_matches.append(path)
    if len(root_matches) != 1:
        return {}

    recovered: dict[str, dict] = {}
    for path in files:
        records = load_records(path)
        filename_id = filename_session_id(path)
        if not filename_id:
            continue
        meta = session_meta(records, filename_id)
        if meta.get("thread_source") != "subagent" or meta.get("session_id") != args.root_session_id:
            continue
        if not same_path(meta.get("cwd"), args.expected_cwd):
            continue

        agent_id = meta.get("id") or meta.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        # Strict three-way identity: the filename id must equal the child's
        # own session_meta id, and when the real agent_id field is present it
        # must agree with both.  A rollout without agent_id is valid: the
        # session_meta id is the native agent identity for subagents.
        if filename_id != agent_id:
            continue
        agent_id_field = meta.get("agent_id")
        if agent_id_field is not None and agent_id_field != filename_id:
            continue
        role = str(meta.get("agent_role") or "").lower().strip()
        if role not in ROLE_MARKERS:
            role = structured_role(records)
        if not role or role in recovered:
            continue

        turn = initial_turn_context(records)
        model = turn.get("model")
        if not isinstance(model, str) or not model:
            continue
        sandbox = turn.get("sandbox_policy", {})
        permission_mode = sandbox.get("type") if isinstance(sandbox, dict) else None

        recovered[role] = {
            "event": "SubagentStartRecovered",
            "ts_utc": meta.get("timestamp"),
            "model": model,
            "effort": turn.get("effort"),
            "cwd": meta.get("cwd"),
            "agent_role": role,
            "agent_id": agent_id,
            "agent_id_source": (
                "session_meta.agent_id" if agent_id_field is not None
                else "session_meta.id"
            ),
            "session_id": args.root_session_id,
            "parent_thread_id": meta.get("parent_thread_id") or args.root_session_id,
            "rollout_path": str(path),
            "identity_path_match": True,
            "permission_mode": permission_mode,
            "source": "codex_rollout_metadata_recovery",
            "hook_observed": False,
        }
    return recovered


def append_idempotently(output: Path, recovered: dict[str, dict]) -> int:
    if set(recovered) != set(ROLE_MARKERS):
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    lock_key = hashlib.sha256(str(output.resolve()).encode("utf-8")).hexdigest()[:16]
    lock_path = Path(tempfile.gettempdir()) / f"codex-loop-orchestra-meter-{lock_key}.lock"

    # Stop hooks may overlap.  PowerShell serializes the normal Windows path;
    # WSL/headless sessions invoke the same reconciler on Linux.  Keep the lock
    # file outside the worktree and use the platform's native advisory lock.
    if os.name == "nt":
        import msvcrt

        def acquire_lock(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

        def release_lock(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        def acquire_lock(handle):
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

        def release_lock(handle):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    with lock_path.open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        acquire_lock(lock)
        try:
            existing = load_records(output) if output.exists() else []
            existing_ids = {
                record.get("agent_id")
                for record in existing
                if isinstance(record.get("agent_id"), str)
            }
            pending = [
                recovered[role]
                for role in ROLE_MARKERS
                if recovered[role]["agent_id"] not in existing_ids
            ]
            if not pending:
                return 0
            with output.open("a", encoding="utf-8", newline="\n") as handle:
                for record in pending:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            return len(pending)
        finally:
            release_lock(lock)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--root-session-id", required=True)
    parser.add_argument("--expected-cwd", required=True, type=Path)
    args = parser.parse_args()

    recovered = recover(args)
    appended = append_idempotently(args.output, recovered)
    print(json.dumps({
        "session_id": args.root_session_id,
        "roles_found": sorted(recovered),
        "records_appended": appended,
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
