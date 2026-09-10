"""zloop.workspace — two-tier worker workspaces (VOL-13) + host delta
reconstruction (VOL-10 §2).

worktree_fast: `git worktree add` — fast, but the common Git objects/refs
administration is SHARED with the canonical repo (P-WS1 proves a worker
inside a worktree can still `git update-ref`). clone_strong: an independent
clone — worker holds no canonical repo credentials/refs.

Delta reconstruction never trusts worker self-reports: we parse
`git status --porcelain=v2 -z --untracked-files=all` machine output (NUL-safe,
no C-quoting in -z mode) and scope-check every changed path against
write_scope, rejecting Git-admin paths outright (VOL-13 §4 Git-admin escape).
"""
from __future__ import annotations

import posixpath
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

# Paths that are Git administration, not project content (VOL-13 §4 /
# VOL-10 §2: .gitmodules and Git-managed refs/config changes need explicit
# approval — here we reject them outright).
_GIT_ADMIN_EXACT = {".git", ".gitmodules"}
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_GLOB_RE = re.compile(r"[*?\[]")

CLONE_STRONG_NOTE = (
    "clone_strong: worker has no access to canonical repo credentials; "
    "remote access only if network allowlisted"
)


def _run(args: list[str], cwd: Optional[Path] = None,
         timeout: float = 120.0) -> subprocess.CompletedProcess:
    """Run git (real exe on PATH). Bytes mode: paths may contain any bytes."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        timeout=timeout,
    )


def _err(proc: subprocess.CompletedProcess, limit: int = 500) -> str:
    return proc.stderr.decode("utf-8", errors="replace").strip()[:limit]


# ---- worktree_fast (NORMAL tier) -------------------------------------------

def create_worktree(git_root: Path, dest: Path, base_ref: str = "HEAD", max_retries: int = 4) -> dict:
    """`git worktree add --detach <dest> <base_ref>` from git_root.

    Includes retry with jittered exponential backoff against .git/index.lock
    contention under 8–15 concurrency (P1-1 Fix).
    """
    git_root, dest = Path(git_root), Path(dest)
    if not git_root.is_dir():
        return {"ok": False, "path": str(dest), "reason": "git_root does not exist"}
    if not dest.parent.exists():
        return {"ok": False, "path": str(dest),
                "reason": "dest parent does not exist"}

    last_proc = None
    for attempt in range(max_retries):
        try:
            proc = _run(["worktree", "add", "--detach", str(dest), base_ref],
                        cwd=git_root)
            if proc.returncode == 0:
                return {"ok": True, "path": str(dest), "stderr_summary": ""}
            last_proc = proc
            err = _err(proc)
            if ("index.lock" in err or "already locked" in err) and attempt < max_retries - 1:
                time.sleep(0.08 * (2 ** attempt) + random.uniform(0.02, 0.08))
                continue
            break
        except (OSError, subprocess.TimeoutExpired) as e:
            if attempt == max_retries - 1:
                return {"ok": False, "path": str(dest), "reason": repr(e)[:200]}
            time.sleep(0.1)

    return {"ok": False, "path": str(dest),
            "stderr_summary": _err(last_proc) if last_proc else "failed"}


def remove_worktree(path: Path, max_retries: int = 3) -> dict:
    """`git worktree remove --force <path>` + `git worktree prune` in the
    original (main) repo — with retry for Windows delayed handle release (P1-3 Fix).
    """
    path = Path(path)
    try:
        probe = _run(["rev-parse", "--path-format=absolute", "--git-common-dir"],
                     cwd=path if path.is_dir() else path.parent)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "removed": False, "pruned": False,
                "reason": repr(e)[:200]}
    if probe.returncode != 0:
        return {"ok": False, "removed": False, "pruned": False,
                "reason": "not a git worktree",
                "stderr_summary": _err(probe)}
    main_root = Path(probe.stdout.decode("utf-8", errors="replace").strip()).parent

    last_rm = None
    last_prune = None
    for attempt in range(max_retries):
        try:
            rm = _run(["worktree", "remove", "--force", str(path)], cwd=main_root)
            prune = _run(["worktree", "prune"], cwd=main_root)
            last_rm, last_prune = rm, prune
            if rm.returncode == 0 and prune.returncode == 0:
                return {"ok": True, "removed": True, "pruned": True, "stderr_summary": ""}
            time.sleep(0.15 * (attempt + 1))
        except (OSError, subprocess.TimeoutExpired) as e:
            if attempt == max_retries - 1:
                return {"ok": False, "removed": False, "pruned": False,
                        "reason": repr(e)[:200]}
            time.sleep(0.15)

    return {"ok": False, "removed": last_rm.returncode == 0 if last_rm else False,
            "pruned": last_prune.returncode == 0 if last_prune else False,
            "stderr_summary": (_err(last_rm) if last_rm else "") or (_err(last_prune) if last_prune else "")}


# ---- clone_strong (HIGH/CRITICAL tier) -------------------------------------

def create_clone(git_root: Path, dest: Path, base_ref: str = "HEAD") -> dict:
    """Independent clone + checkout base_ref. Worker sees its own refs/config;
    no canonical credentials reach it (VOL-13 §2)."""
    git_root, dest = Path(git_root), Path(dest)
    result = {"ok": False, "path": str(dest), "note": CLONE_STRONG_NOTE}
    if not git_root.is_dir():
        return {**result, "reason": "git_root does not exist"}
    if not dest.parent.exists():
        return {**result, "reason": "dest parent does not exist"}
    if dest.exists():
        return {**result, "reason": "dest already exists"}
    try:
        clone = _run(["clone", str(git_root), str(dest)], cwd=dest.parent)
        if clone.returncode != 0:
            return {**result, "reason": "git clone failed",
                    "stderr_summary": _err(clone)}
        co = _run(["checkout", "--detach", base_ref], cwd=dest)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {**result, "reason": repr(e)[:200]}
    return {**result, "ok": co.returncode == 0,
            "stderr_summary": _err(co)}


# ---- host delta reconstruction (VOL-10 §2) ----------------------------------

def enumerate_delta(workspace: Path) -> list[dict]:
    """Machine-parse `git status --porcelain=v2 -z --untracked-files=all`.

    NUL-safe: stdout is bytes, split on NUL; a rename record's <path> and
    <origPath> are consecutive NUL-terminated tokens. Paths decode with
    surrogateescape so any byte round-trips. -z mode never C-quotes paths.
    """
    workspace = Path(workspace)
    proc = _run(["status", "--porcelain=v2", "-z", "--untracked-files=all"],
                cwd=workspace)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git status failed in {workspace}: {_err(proc)}")

    def dec(b: bytes) -> str:
        return b.decode("utf-8", errors="surrogateescape")

    tokens = [dec(t) for t in proc.stdout.split(b"\x00")]
    entries: list[dict] = []
    i = 0
    while i < len(tokens):
        rec = tokens[i]
        if not rec:
            i += 1
            continue
        kind = rec[:1]
        if kind == "1":
            # 1 <xy> <sub> <mH> <mI> <mW> <hH> <hI> <path>
            parts = rec.split(" ", 8)
            if len(parts) != 9:
                raise ValueError(f"malformed v2 record: {rec[:120]!r}")
            entries.append({"record_type": "1", "xy": parts[1], "sub": parts[2],
                            "path": parts[8]})
        elif kind == "2":
            # 2 <xy> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path> \0 <origPath>
            parts = rec.split(" ", 9)
            if len(parts) != 10 or i + 1 >= len(tokens):
                raise ValueError(f"malformed v2 rename record: {rec[:120]!r}")
            entries.append({"record_type": "2", "xy": parts[1], "sub": parts[2],
                            "path": parts[9], "orig_path": tokens[i + 1]})
            i += 1  # consumed origPath token
        elif kind == "u":
            # u <xy> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
            parts = rec.split(" ", 10)
            if len(parts) != 11:
                raise ValueError(f"malformed v2 unmerged record: {rec[:120]!r}")
            entries.append({"record_type": "u", "xy": parts[1], "sub": parts[2],
                            "path": parts[10]})
        elif kind in ("?", "!"):
            entries.append({"record_type": kind, "path": rec[2:]})
        else:
            raise ValueError(f"unknown v2 record type: {rec[:120]!r}")
        i += 1
    return entries


def _norm_path(path: str) -> str:
    """Forward slashes + posix normalization (input may be Windows-style)."""
    return path.replace("\\", "/")


def _norm_pattern(pattern: str) -> str:
    p = posixpath.normpath(_norm_path(pattern).strip())
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/") if p not in ("", "/") else p


def _is_git_admin(norm: str) -> bool:
    return norm in _GIT_ADMIN_EXACT or norm.startswith(".git/")


def _matches(norm: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        # src/foo/** -> anything under src/foo/ (any depth)
        return norm.startswith(pattern[:-2])
    if pattern.endswith("/*"):
        # src/foo/* -> exactly one level under src/foo/
        prefix = pattern[:-1]
        return norm.startswith(prefix) and "/" not in norm[len(prefix):]
    if _GLOB_RE.search(pattern):
        # unsupported glob form: fail closed (never matches)
        return False
    return norm == pattern or norm.startswith(pattern + "/")


def _check_one(raw: str, patterns: list[str]) -> Optional[str]:
    """Return the violation reason-check: None if in scope, else the
    forward-slashed original path (identifiable in reports)."""
    flat = _norm_path(raw)
    if flat.startswith("/") or flat.startswith("//") or _DRIVE_RE.match(flat):
        return flat  # absolute/UNC path is not repo-relative
    if any(part == ".." for part in flat.split("/")):
        return flat  # '..' traversal rejected outright
    norm = posixpath.normpath(flat)
    if norm.startswith("../") or norm in ("..", "."):
        return flat
    if _is_git_admin(norm):
        return flat  # Git-admin escape: ALWAYS out of scope (VOL-13 §4)
    if not any(_matches(norm, p) for p in patterns):
        return flat
    return None


def paths_within_scope(changed: list[dict],
                       write_scope: list[str]) -> tuple[bool, list[str]]:
    """Check actual_changed_paths ⊆ write_scope (VOL-10 §2).

    A rename entry changes both endpoints, so both `path` and `orig_path`
    are checked. Returns (ok, violations); ok False on any violation.
    """
    patterns = [_norm_pattern(p) for p in write_scope if str(p).strip()]
    violations: list[str] = []
    for entry in changed:
        for key in ("path", "orig_path"):
            raw = entry.get(key)
            if raw is None:
                continue
            bad = _check_one(str(raw), patterns)
            if bad is not None and bad not in violations:
                violations.append(bad)
    return (not violations), violations
