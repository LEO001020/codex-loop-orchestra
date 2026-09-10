"""zloop.checkpoint — H1.semantic store (VOL-04 §7.2, VOL-06 §2).

Durable semantic capsules: evidence-ref validation with demotion to
unverified_notes (I15), a 16KB serialized hard cap, semantic_state_hash
dedupe (consecutive identical semantic state is not re-written), and
mandatory machine-field stripping (I14: machine state is rebuilt from
current reality and never persists in H1.semantic). Storage is plain
JSON: checkpoints/cp_<n:04d>.json plus a current.json pointer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from . import ids

CAP = 16 * 1024  # bytes, serialized hard cap (VOL-04 §7.2)

# Machine-state fields that must never be stored here (H1.machine, I14).
MACHINE_FIELDS = (
    "run_id", "stage_state", "canonical_head", "packet_states",
    "active_launch_ids",
)

# The six semantic lists covered by semantic_state_hash.
SEMANTIC_LISTS = (
    "established_facts", "decisions", "rejected_hypotheses",
    "unresolved_questions", "next_frontier", "risk_notes",
)

# Fields whose entries require resolvable evidence refs (VOL-04 §1).
_EVIDENCE_FIELDS = ("established_facts", "decisions", "rejected_hypotheses")

EVIDENCE_REF_RE = re.compile(
    r"^(ev:s:\d+|ev:blob:sha256:[0-9a-f]{64}|ev:git:[0-9a-f]{7,40})$")

_CP_ID_RE = re.compile(r"cp_\d{4,}")


def _checkpoints_dir(project_dir: Path) -> Path:
    return Path(project_dir) / "checkpoints"


def _cp_num(p: Path) -> int | None:
    """Sequence number of a cp_*.json file name, or None if not ours."""
    m = _CP_ID_RE.fullmatch(p.stem)
    return int(p.stem[3:]) if m else None


def _valid_evidence_entry(entry: object) -> bool:
    """Dict with a non-empty evidence_refs list of well-formed refs."""
    if not isinstance(entry, dict):
        return False
    refs = entry.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        return False
    return all(isinstance(r, str) and EVIDENCE_REF_RE.fullmatch(r)
               for r in refs)


def _semantic_hash(capsule: dict) -> str:
    """sha256 over canonical JSON of the six semantic lists."""
    core = {f: capsule[f] if isinstance(capsule.get(f), list) else []
            for f in SEMANTIC_LISTS}
    canon = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_cp_file(project_dir: Path, cp_id: object) -> dict | None:
    if not isinstance(cp_id, str) or not _CP_ID_RE.fullmatch(cp_id):
        return None
    p = _checkpoints_dir(project_dir) / f"{cp_id}.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _current_id(project_dir: Path) -> str | None:
    try:
        p = _checkpoints_dir(project_dir) / "current.json"
        if not p.exists():
            return None
        ptr = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(ptr, dict) and isinstance(ptr.get("id"), str):
            return ptr["id"]
    except Exception:
        pass
    return None


def checkpoint_write(project_dir: Path, capsule: dict) -> str | None:
    """Validate, normalize and persist one H1.semantic capsule.

    Returns the new checkpoint id, the existing id when the semantic state
    is unchanged (dedupe), or None when the capsule is invalid / oversized
    / unwritable. Never raises.
    """
    try:
        if not isinstance(capsule, dict):
            return None
        cp = dict(capsule)  # shallow copy: the caller's capsule is untouched

        # I14: machine state never persists in H1.semantic. Strip the
        # fields and record only the fact of stripping — the values
        # themselves belong to H1.machine (rebuilt from current reality).
        stripped = [k for k in MACHINE_FIELDS if k in cp]
        for k in stripped:
            del cp[k]
        prev_stripped = cp.get("stripped_machine_fields")
        if stripped or isinstance(prev_stripped, list):
            names = (set(prev_stripped) if isinstance(prev_stripped, list)
                     else set())
            names.update(stripped)
            cp["stripped_machine_fields"] = sorted(names)

        # Normalize the six semantic lists; entries with unresolvable
        # evidence_refs are demoted into unverified_notes (I15).
        raw_unv = cp.get("unverified_notes")
        unverified = list(raw_unv) if isinstance(raw_unv, list) else []
        demoted = False
        for field in SEMANTIC_LISTS:
            val = cp.get(field)
            if not isinstance(val, list):
                val = []
            if field in _EVIDENCE_FIELDS:
                keep = [e for e in val if _valid_evidence_entry(e)]
                drop = [e for e in val if not _valid_evidence_entry(e)]
                if drop:
                    demoted = True
                    unverified.extend(drop)
                val = keep
            cp[field] = val
        if demoted or isinstance(raw_unv, list):
            cp["unverified_notes"] = unverified

        obj = cp.get("objective_slice")
        cp["objective_slice"] = (obj if isinstance(obj, str)
                                 else "" if obj is None else str(obj))

        digest = _semantic_hash(cp)
        cp["semantic_state_hash"] = "sha256:" + digest

        # Hard cap on the serialized capsule (what actually lands on disk).
        try:
            blob = json.dumps(cp, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False)
        except (TypeError, ValueError):
            return None
        if len(blob.encode("utf-8")) > CAP:
            return None

        # Dedupe: identical current semantic state rewrites nothing.
        cur_id = _current_id(project_dir)
        if cur_id is not None:
            cur = _load_cp_file(project_dir, cur_id)
            if cur is not None and (
                    cur.get("semantic_state_hash") == cp["semantic_state_hash"]
                    or _semantic_hash(cur) == digest):
                return cur_id

        cdir = _checkpoints_dir(project_dir)
        cdir.mkdir(parents=True, exist_ok=True)
        nums = [n for n in (_cp_num(p) for p in cdir.glob("cp_*.json"))
                if n is not None]
        cp_id = f"cp_{(max(nums) + 1) if nums else 1:04d}"
        _atomic_write(cdir / f"{cp_id}.json", blob)
        _atomic_write(cdir / "current.json",
                      json.dumps({"id": cp_id, "ts": ids.now_iso()},
                                 ensure_ascii=False, separators=(",", ":")))
        return cp_id
    except Exception:
        return None


def checkpoint_current(project_dir: Path) -> dict | None:
    """Load the checkpoint the current.json pointer names; None if absent."""
    cp_id = _current_id(project_dir)
    if cp_id is None:
        return None
    return _load_cp_file(project_dir, cp_id)


def checkpoint_show(project_dir: Path, cp_id: str) -> dict | None:
    """Load one checkpoint by id ('cp_0007'); None if absent/invalid."""
    try:
        return _load_cp_file(project_dir, cp_id)
    except Exception:
        return None
