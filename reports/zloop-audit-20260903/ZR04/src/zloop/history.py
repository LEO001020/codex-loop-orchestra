"""zloop.history — H2 programmable exact recall (VOL-06 §3).

Fail-soft derived view over the H0 session journals: bounded substring
search, around-context windows and chain verification. Nothing here ever
raises; a missing or corrupt journal degrades to empty/partial results
(I3). The NDJSON+blob files remain the authority — any index built on top
of this module is a rebuildable derivative (I11).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import evidence

_EVENT_ID_RE = re.compile(r"ev:s:(\d+)\Z")


def _session_files(project_dir: Path) -> list[Path]:
    """All session journals, sorted by name (VOL-04 §2 layout)."""
    return sorted((Path(project_dir) / "history" / "sessions").glob("*.ndjson"))


def history_search(project_dir: Path, query: str, *,
                   session: str | None = None, run_id: str | None = None,
                   limit: int = 50) -> list[dict]:
    """Case-insensitive substring search over session journals (bounded).

    A line matches when the needle occurs in the serialized envelope
    (payload_inline included). Optional filters: single session file,
    exact envelope run_id. Returns at most `limit` envelopes; never raises.
    """
    out: list[dict] = []
    try:
        if limit <= 0:
            return out
        sessions_dir = Path(project_dir) / "history" / "sessions"
        files = ([sessions_dir / f"{session}.ndjson"] if session is not None
                 else sorted(sessions_dir.glob("*.ndjson")))
        needle = str(query).lower()
        for fp in files:
            try:
                for env in evidence.read_journal(fp):
                    if run_id is not None and env.get("run_id") != run_id:
                        continue
                    if needle in json.dumps(env, ensure_ascii=False).lower():
                        out.append(env)
                        if len(out) >= limit:
                            return out
            except Exception:
                continue  # one unreadable journal never fails the search
    except Exception:
        pass
    return out


def history_around(project_dir: Path, event_id: str, *,
                   before: int = 3, after: int = 3) -> dict:
    """Bounded context window around one event id 'ev:s:<seq>'.

    Finds the session journal containing that seq (first in sorted order —
    seq is per-session) and returns {"before": [...], "event": ...,
    "after": [...]} bounded by availability. Missing/invalid event yields
    an empty window; never raises.
    """
    try:
        m = _EVENT_ID_RE.fullmatch(str(event_id).strip())
        if not m:
            return {"before": [], "event": None, "after": []}
        seq = int(m.group(1))
        b, a = max(0, before), max(0, after)
        for fp in _session_files(project_dir):
            try:
                lines = evidence.read_journal(fp)
            except Exception:
                continue
            for i, env in enumerate(lines):
                if env.get("seq") == seq:
                    return {"before": lines[max(0, i - b):i],
                            "event": env,
                            "after": lines[i + 1:i + 1 + a]}
    except Exception:
        pass
    return {"before": [], "event": None, "after": []}


def history_verify(project_dir: Path) -> dict:
    """Line-hash chain + blob existence check over every session journal.

    Aggregates {"sessions": N, "lines": N, "errors": [...], "ok": bool};
    never raises (fail-soft like the plane it reads).
    """
    agg: dict = {"sessions": 0, "lines": 0, "errors": [], "ok": False}
    try:
        blob_root = Path(project_dir) / "blobs" / "sha256"
        for fp in _session_files(project_dir):
            agg["sessions"] += 1
            try:
                res = evidence.verify_chain(fp, blob_root)
            except Exception as exc:
                agg["errors"].append(f"{fp.name}: verify failed: {exc}")
                continue
            agg["lines"] += res.get("lines", 0)
            agg["errors"].extend(f"{fp.name}: {msg}"
                                 for msg in res.get("errors", []))
    except Exception as exc:
        agg["errors"].append(str(exc))
    agg["ok"] = not agg["errors"]
    return agg
