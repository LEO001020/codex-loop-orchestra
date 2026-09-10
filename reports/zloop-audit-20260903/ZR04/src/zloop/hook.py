"""zloop.hook — single-entry hook dispatcher (VOL-05).

Fail-soft by construction (I3): the process always exits 0 and never lets an
exception escape. The ONLY stdout outputs are the two documented JSON lines:

* bind confirmation  — VOL-05 §4 (``{"hookSpecificOutput":{"hookEventName":
  "PostToolUse","additionalContext":"[zloop] bound: run …"}}``)
* recovery injection — VOL-05 §5 (``{"hookSpecificOutput":{"hookEventName":
  "SessionStart","additionalContext":<bounded text>}}``)

Everything else is a silent no-op. Capture goes through the H0 journal
(VOL-06, via ``evidence.Journal``); binding is claimed only through the
one-time nonce protocol (I32) — never by cwd / latest-run guessing.

D-16 strict project scoping: capture and bind-token claim proceed ONLY
when the event's ``cwd`` lies inside a registered project's git_root
(``resolve_project_for_cwd``). A session already bound to that project
counts too (its run/stage ride along); anything else — including the old
"single registered project" fallback — journals nothing and stays silent,
so prompts/tool results from unrelated workspaces never leak into a
project's H0. SessionStart recovery is exempt: it works off the exact
session binding regardless of cwd.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional, Tuple

from . import db as zdb
from . import evidence as zev
from . import paths

HOOK_EVENTS = {
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
    "PostToolUse", "PostToolUseFailure", "Stop",
}

# I32: the marker printed by `zloop run start` / `zloop attach` on stdout.
_BIND_TOKEN_RE = re.compile(r"ZLOOP_BIND_TOKEN=([0-9a-f]{64})")

# Self-read guard (VOL-06 §1.2): reading the evidence/control planes must not
# re-enter them (unbounded recursion) and must not trigger the claim parser
# (spec security property: "H0 读历史不触发 claim parser").
_SELF_READ_PREFIXES = ("zloop history", "zloop evidence", "zloop checkpoint")

_RECOVERY_TAIL = ("Current files/runtime/oracles override stale checkpoint "
                  "prose. zloop history search <query> for exact captured "
                  "evidence.")

_RECOVERY_BUDGET = 1200   # chars, total bounded recovery block
_OBJECTIVE_BUDGET = 200   # chars, run objective inside the machine envelope
_CHECKPOINT_BUDGET = 400  # chars, semantic capsule summary
_BIND_LINE_BUDGET = 120   # chars, whole bind confirmation JSON line


def main() -> int:
    """Console entry `zloop-hook` (also runnable as `python -m zloop.hook`).

    Reads ONE line of JSON from stdin and dispatches on hook_event_name
    (args are never trusted to carry the event — VOL-05 §1). Always 0.
    """
    raw = ""
    try:
        raw = sys.stdin.readline()
        event = json.loads(raw)
        if isinstance(event, dict):
            _dispatch(event, raw)
    except Exception:
        pass
    return 0


def _dispatch(event: dict, raw_line: str) -> None:
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    name = event.get("hook_event_name")
    cwd = event.get("cwd")  # D-16: strict project scoping rides on this field
    if name == "PostToolUse":
        _handle_post_tool_use(event, session_id, raw_line, cwd)
        return
    kind, payload, tool = _map_event(name, event)
    if kind is not None:
        _capture(name, kind, session_id, payload, tool, cwd)
    if name == "SessionStart":
        # recovery is a separate, fault-isolated branch (VOL-06 §5) and
        # works off the exact session binding regardless of cwd (D-16)
        _recovery(session_id, event.get("source"))


def _map_event(name: Any, event: dict):
    """(kind, payload, tool) for the six non-PostToolUse events, else (None,..)."""
    if name == "SessionStart":
        return "session_start", {"source": event.get("source")}, None
    if name == "UserPromptSubmit":
        return "prompt", {"prompt": event.get("prompt")}, None
    if name == "PreToolUse":
        return "tool_call", {"tool_name": event.get("tool_name"),
                             "tool_input": event.get("tool_input")}, event.get("tool_name")
    if name == "PermissionRequest":
        return "permission_request", {"tool_name": event.get("tool_name")}, event.get("tool_name")
    if name == "PostToolUseFailure":
        return "tool_failure", {"error": event.get("error"),
                                "is_interrupt": event.get("is_interrupt")}, event.get("tool_name")
    if name == "Stop":
        return "stop", {"last_assistant_message": event.get("last_assistant_message")}, None
    return None, None, None


# ---- capture branch (VOL-05 §3, VOL-06 §1) ---------------------------------

def _handle_post_tool_use(event: dict, session_id: str, raw_line: str,
                          cwd: Any) -> None:
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input")
    tool_response = event.get("tool_response")
    if _is_self_read(tool_input):
        return  # recursion guard: no journaling, no claim parsing
    # claim runs before capture so the binding event itself lands in the
    # journal of the (newly) bound project
    _try_claim(tool_name, tool_response, session_id, raw_line, cwd)
    _capture("PostToolUse", "tool_result", session_id,
             {"tool_name": tool_name, "tool_input": tool_input,
              "tool_response": tool_response}, tool=tool_name, cwd=cwd)


def _is_self_read(tool_input: Any) -> bool:
    if not isinstance(tool_input, dict):
        return False
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        return False
    c = cmd.strip().lower()
    return c.startswith(_SELF_READ_PREFIXES)


def _capture(event_name: str, kind: str, session_id: str, payload: Any,
             tool: Optional[str] = None, cwd: Any = None) -> None:
    try:
        project_id, run_id, stage_id = _resolve_capture_project(session_id, cwd)
        if project_id is None:
            return
        journal = zev.Journal(paths.history_session_file(project_id, session_id),
                              paths.blobs_root(project_id))
        journal.append(kind=kind, session_id=session_id, event=event_name,
                       tool=tool, payload=payload, run_id=run_id, stage_id=stage_id)
    except Exception:
        pass  # H0 is fail-soft (I3): capture must never break a native turn


def resolve_project_for_cwd(cwd: str) -> Optional[dict]:
    """Registered project whose git_root is an ancestor-or-equal of cwd.

    D-16 strict scoping — the only cwd-based resolution left in the hook.
    Both sides are normalized with Path.resolve() and compared
    case-insensitively (os.path.normcase); the ancestor check is
    ``is_relative_to``. Returns the registry record plus ``project_id``,
    or None when cwd lies outside every registered project (and for
    missing/invalid cwd). Never raises.
    """
    if not isinstance(cwd, str) or not cwd:
        return None
    try:
        projects = paths.load_registry().get("projects", {})
        here = _norm_path(cwd)
    except Exception:
        return None
    for pid, rec in projects.items():
        if not isinstance(rec, dict):
            continue
        root = rec.get("git_root")
        if not isinstance(root, str) or not root:
            continue
        try:
            root_p = _norm_path(root)
        except Exception:
            continue
        if here.is_relative_to(root_p):
            out = dict(rec)
            out["project_id"] = pid
            return out
    return None


def _norm_path(p: str) -> Path:
    """Resolved, case-normalized path for comparison (lowercased by
    os.path.normcase on Windows; identity on case-sensitive filesystems)."""
    return Path(os.path.normcase(str(Path(p).resolve())))


def _resolve_capture_project(session_id: str, cwd: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Project for capture under D-16 strict scoping: the session's bound
    project when cwd lies inside its git_root, else the registered project
    containing cwd, else None (= skip capture silently). The former
    single-registered-project fallback is gone — unrelated workspaces are
    never journaled."""
    cwd_hit = resolve_project_for_cwd(cwd)
    hit = _find_binding(session_id)
    if hit is not None:
        pid, binding = hit
        if cwd_hit is not None and cwd_hit["project_id"] == pid:
            return pid, binding.get("run_id"), binding.get("stage_id")
        return None, None, None
    if cwd_hit is not None:
        return cwd_hit["project_id"], None, None
    return None, None, None


def _find_binding(session_id: str) -> Optional[Tuple[str, dict]]:
    """Exact-session binding lookup across all registered projects (db only)."""
    try:
        pids = list(paths.load_registry().get("projects", {}).keys())
    except Exception:
        return None
    for pid in pids:
        conn = None
        try:
            conn = zdb.connect(paths.project_dir(pid), create=False)
            row = conn.execute(
                "SELECT * FROM session_bindings WHERE zcode_session_id=?",
                (session_id,)).fetchone()
            if row is not None:
                return pid, dict(row)
        except Exception:
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return None


# ---- bind-token claim (I32, VOL-05 §4) --------------------------------------

def _try_claim(tool_name: Any, tool_response: Any, session_id: str,
               raw_line: str, cwd: Any) -> None:
    try:
        if tool_name != "Bash":
            return
        # D-16: the claim is attempted ONLY for the registered project
        # containing the hook cwd (the CLI always runs inside the project,
        # so this is safe); an unrelated workspace never claims/binds.
        cwd_hit = resolve_project_for_cwd(cwd)
        if cwd_hit is None:
            return
        try:
            scan_text = json.dumps(tool_response)
        except Exception:
            scan_text = raw_line if isinstance(raw_line, str) else ""
        if not isinstance(scan_text, str):
            return
        m = _BIND_TOKEN_RE.search(scan_text)
        if m is None:
            return
        nonce = m.group(1)
        pid = cwd_hit["project_id"]
        binding: Optional[dict] = None
        conn = None
        try:
            conn = zdb.connect(paths.project_dir(pid), create=True)
            store = zdb.ControlStore(paths.project_dir(pid), conn,
                                     project_id=pid)
            binding = store.claim_binding(nonce, session_id)
        except Exception:
            binding = None  # S busy/corrupt -> give up this claim, exit 0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        if binding is None:
            return  # unknown/expired/replayed/forged/cross-project nonce: stay silent
        run = binding.get("run_id") or "attached"
        line = json.dumps(
            {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                    "additionalContext": f"[zloop] bound: run {str(run)[:60]}"}},
            separators=(",", ":"))
        if len(line) > _BIND_LINE_BUDGET:
            line = json.dumps(
                {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                        "additionalContext": "[zloop] bound"}},
                separators=(",", ":"))
        sys.stdout.write(line + "\n")
    except Exception:
        pass


# ---- recovery branch (SessionStart only, VOL-05 §5 / VOL-06 §2.3-§4) -------

def _recovery(session_id: str, source: Any) -> None:
    try:
        if not isinstance(source, str):
            return
        hit = _find_binding(session_id)
        if hit is None:
            return  # never fall back to "most recent active run"
        pid, binding = hit
        if source == "clear":
            if not binding.get("resume_after_clear"):
                return
        elif source not in ("compact", "startup", "resume"):
            return
        text = _recovery_text(pid, binding)
        if not text:
            return
        line = json.dumps(
            {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                    "additionalContext": text}},
            separators=(",", ":"))
        sys.stdout.write(line + "\n")
    except Exception:
        pass


def _recovery_text(project_id: str, binding: dict) -> str:
    run_id = binding.get("run_id")
    state, objective, open_stages = "UNKNOWN", "", 0
    pdir = paths.project_dir(project_id)
    conn = None
    try:
        conn = zdb.connect(pdir, create=False)
    except Exception:
        conn = None
    if conn is not None:
        try:
            if run_id:
                row = conn.execute(
                    "SELECT state, objective FROM runs WHERE run_id=?",
                    (run_id,)).fetchone()
                if row is not None:
                    state = row["state"] or "UNKNOWN"
                    objective = str(row["objective"] or "")
            cnt = conn.execute(
                "SELECT COUNT(*) FROM stages WHERE run_id=?"
                " AND state NOT IN ('CLOSED','CANCELLED','PROMOTED')",
                (run_id,)).fetchone()
            open_stages = cnt[0] if cnt is not None else 0
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    lines = [
        f"ACTIVE ZLOOP RUN {run_id or 'attached'} ({state})",
        f"Machine envelope: open stages: {open_stages}; "
        f"objective: {objective[:_OBJECTIVE_BUDGET]}",
    ]
    semantic = _semantic_summary(pdir / "checkpoints" / "current.json")
    if semantic:
        lines.append("Semantic checkpoint: " + semantic[:_CHECKPOINT_BUDGET])
    lines.append(_RECOVERY_TAIL)
    return "\n".join(lines)[:_RECOVERY_BUDGET]


def _semantic_summary(path: Path) -> Optional[str]:
    """Best-effort semantic summary from the latest H1 checkpoint file."""
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return text.strip() or None
    if isinstance(data, dict):
        for key in ("semantic_summary", "summary", "semantic",
                    "objective_slice", "note"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v
        try:
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return None
    if isinstance(data, str):
        return data
    return str(data)


if __name__ == "__main__":
    sys.exit(main())
