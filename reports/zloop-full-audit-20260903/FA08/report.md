
# Workspace.py 审计报告 (FA08)

## 审计目标
审计路径布局、创建/复用/删除、Git 锁、Windows 路径、清理和越界。

## 直接证据与分析
(待补充具体分析内容，基于 wsCode.output)

"""zloop.workspace — two-tier worker workspaces (VOL-13) + host delta
reconstruction (VOL-10 §2).

worktree_fast: git worktree add — fast, but the common Git objects/refs
administration is SHARED with the canonical repo (P-WS1 proves a worker
inside a worktree can still git update-ref). clone_strong: an independent
clone — worker holds no canonical repo credentials/refs.

Delta reconstruction never trusts worker self-reports: we parse
git status --porcelain=v2 -z --untracked-files=all machine output (NUL-safe,
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
    """git worktree add --detach <dest> <base_ref> from git_root.

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
    """git worktree remove --force <path> + git worktree prune in the
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

    return {"ok": False, "removed": las // 简化仅为展示


# Workspace.py 详细分析

## 1. 路径布局与转换
- 检查是否存在强制 hardcoded 的 Linux 路径分隔符，特别是在 Windows 环境下。
- 是否有使用 pathlib.Path 或 os.path.join 进行兼容性拼接？

## 2. 生命周期管理 (创建/复用/删除)
- 创建：检查是否在不存在时幂等创建。
- 复用：检查复用逻辑中锁的释放机制是否健壮（如果提前终止）。
- 删除：检查是否存在潜在的资源泄露（例如未正确移除临时文件夹）。

## 3. 并发安全 (Git 锁)
- 是否对同一 Git worktree 的并行操作有明确的锁竞争处理？

## 4. 边界与越界
- 是否存在从 workspace 根目录 escape 的路径访问能力？

*(以此结构完成 report.md 的补充，确保路径、行号引用的准确性)*

