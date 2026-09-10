"""zloop.paths — data root & physical layout (VOL-04 §2).

%ZLOOP_DATA% defaults to ~/.zloop. Layout per project:
  projects/<project_id>/{control.sqlite3, history/sessions/, blobs/sha256/,
                         workspaces/, runs/, research/, c2c/, stage-snapshots/,
                         checkpoints/, backups/}
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def zloop_data_root() -> Path:
    return Path(os.environ.get("ZLOOP_DATA", str(Path.home() / ".zloop")))


def registry_path() -> Path:
    return zloop_data_root() / "registry.json"


# ---- per-project layout -------------------------------------------------

def project_dir(project_id: str) -> Path:
    return zloop_data_root() / "projects" / project_id


PROJECT_SUBDIRS = (
    "history/sessions",
    "blobs/sha256",
    "workspaces",
    "runs",
    "research",
    "c2c",
    "stage-snapshots",
    "checkpoints",
    "backups",
)


def ensure_project_layout(project_id: str) -> Path:
    d = project_dir(project_id)
    for sub in PROJECT_SUBDIRS:
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def control_db_path(project_id: str) -> Path:
    return project_dir(project_id) / "control.sqlite3"


def history_session_file(project_id: str, session_id: str) -> Path:
    return project_dir(project_id) / "history" / "sessions" / f"{session_id}.ndjson"


def blobs_root(project_id: str) -> Path:
    return project_dir(project_id) / "blobs" / "sha256"


def hygiene_backup_dir() -> Path:
    return zloop_data_root() / "hygiene-backup"


# ---- project registry (machine-owned; never guessed from cwd) -----------

def load_registry() -> Dict[str, Any]:
    p = registry_path()
    if not p.exists():
        return {"projects": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "projects" not in data:
            return {"projects": {}}
        return data
    except Exception:
        return {"projects": {}}


def save_registry(reg: Dict[str, Any]) -> None:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)  # atomic replace (old-LOOP lesson: single replace under no lock)


def find_project_by_git_root(git_root: str) -> Optional[str]:
    reg = load_registry()
    for pid, rec in reg["projects"].items():
        if rec.get("git_root", "").lower() == str(git_root).lower():
            return pid
    return None


def register_project(project_id: str, git_root: str, git_common_dir: str,
                     display_name: str = "") -> None:
    from . import ids
    reg = load_registry()
    reg["projects"][project_id] = {
        "git_root": str(git_root),
        "git_common_dir": str(git_common_dir),
        "display_name": display_name or Path(git_root).name,
        "created_at": ids.now_iso(),
    }
    save_registry(reg)
