"""Workspace tests: worktree lifecycle, delta enumeration, write_scope
checking (VOL-13, VOL-10 §2). Temp git repos via subprocess helper."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import workspace as zw  # noqa: E402


def git(*args: str, cwd: Path) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd),
                        capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "T", cwd=root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    (root / "old_name.txt").write_text("renamed content\n", encoding="utf-8")
    (root / "src" / "foo").mkdir(parents=True)
    (root / "src" / "foo" / "a.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "init", cwd=root)
    return root


@pytest.fixture()
def worktree(repo: Path, tmp_path: Path):
    wt = tmp_path / "ws" / "launch-1"
    wt.parent.mkdir(parents=True, exist_ok=True)
    r = zw.create_worktree(repo, wt)
    assert r["ok"], r
    yield wt
    zw.remove_worktree(wt)


# ---- worktree lifecycle -----------------------------------------------------

def test_create_and_remove_worktree(repo: Path, tmp_path: Path):
    wt = tmp_path / "ws" / "launch-1"
    wt.parent.mkdir()
    r = zw.create_worktree(repo, wt)
    assert r["ok"] is True
    assert r["path"] == str(wt)
    assert (wt / "README.md").is_file()          # base_ref=HEAD content present
    # detached HEAD: no branch checked out in the worktree
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt).strip() == "HEAD"
    # independent working tree: a new file does not leak into the main repo
    (wt / "wt-only.txt").write_text("x", encoding="utf-8")
    assert not (repo / "wt-only.txt").exists()

    r2 = zw.remove_worktree(wt)
    assert r2["ok"] is True and r2["removed"] is True and r2["pruned"] is True
    assert not wt.exists()
    listing = [Path(l.split(" ", 1)[1]) for l in
               git("worktree", "list", "--porcelain", cwd=repo).splitlines()
               if l.startswith("worktree ")]
    assert listing == [repo]                  # only the main tree remains


def test_create_worktree_requires_existing_parent(repo: Path, tmp_path: Path):
    r = zw.create_worktree(repo, tmp_path / "missing" / "wt")
    assert r["ok"] is False
    assert "dest parent" in r["reason"]


def test_create_worktree_base_ref(repo: Path, tmp_path: Path):
    (repo / "README.md").write_text("v2\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "second", cwd=repo)
    wt = tmp_path / "ws" / "at-base"
    wt.parent.mkdir()
    first = git("rev-list", "--max-parents=0", "HEAD", cwd=repo).strip()
    r = zw.create_worktree(repo, wt, base_ref=first)
    assert r["ok"] is True
    assert (wt / "README.md").read_text(encoding="utf-8") == "hello\n"
    zw.remove_worktree(wt)


def test_create_clone(repo: Path, tmp_path: Path):
    dest = tmp_path / "clone-1"
    r = zw.create_clone(repo, dest)
    assert r["ok"] is True, r
    assert r["path"] == str(dest)
    assert "clone_strong" in r["note"]
    assert (dest / "README.md").is_file()
    # independent refs: a branch created in the clone stays out of canonical
    git("checkout", "-q", "-b", "worker-branch", cwd=dest)
    assert "worker-branch" in git("branch", "--list", "worker-branch", cwd=dest)
    assert git("branch", "--list", "worker-branch", cwd=repo).strip() == ""
    # refuses an existing dest
    r2 = zw.create_clone(repo, dest)
    assert r2["ok"] is False and "already exists" in r2["reason"]


# ---- enumerate_delta (VOL-10 §2) --------------------------------------------

def test_enumerate_delta_untracked_modified_renamed(repo: Path, worktree: Path):
    (worktree / "untracked.txt").write_text("new\n", encoding="utf-8")
    (worktree / "has space.txt").write_text("spaced\n", encoding="utf-8")
    (worktree / "src" / "new").mkdir(parents=True)
    (worktree / "src" / "new" / "c.txt").write_text("nested\n", encoding="utf-8")
    (worktree / "README.md").write_text("modified\n", encoding="utf-8")
    git("mv", "old_name.txt", "new_name.txt", cwd=worktree)

    entries = zw.enumerate_delta(worktree)
    untracked = [e["path"] for e in entries if e["record_type"] == "?"]
    assert "untracked.txt" in untracked
    assert "has space.txt" in untracked           # NUL-safe, space kept whole
    assert "src/new/c.txt" in untracked           # --untracked-files=all expands dirs

    modified = [e for e in entries if e["record_type"] == "1"
                and e["path"] == "README.md"]
    assert len(modified) == 1
    assert "M" in modified[0]["xy"]

    renamed = [e for e in entries if e["record_type"] == "2"]
    assert len(renamed) == 1
    assert renamed[0]["path"] == "new_name.txt"
    assert renamed[0]["orig_path"] == "old_name.txt"

    # the canonical repo stays clean of these changes
    assert zw.enumerate_delta(repo) == []


def test_enumerate_delta_clean_and_deleted(repo: Path, worktree: Path):
    assert zw.enumerate_delta(worktree) == []      # pristine worktree
    (worktree / "README.md").unlink()
    entries = zw.enumerate_delta(worktree)
    assert [e["record_type"] for e in entries] == ["1"]
    assert entries[0]["path"] == "README.md"
    assert "D" in entries[0]["xy"]


# ---- paths_within_scope (VOL-10 §2 / VOL-13 §4) ------------------------------

def test_scope_recursive_pattern():
    scope = ["src/foo/**"]
    ok, viol = zw.paths_within_scope([{"path": "src/foo/a.py"}], scope)
    assert ok and viol == []
    ok, _ = zw.paths_within_scope([{"path": "src/foo/deep/nested/b.py"}], scope)
    assert ok
    ok, viol = zw.paths_within_scope([{"path": "other/x"}], scope)
    assert not ok and viol == ["other/x"]


def test_scope_rejects_traversal():
    ok, viol = zw.paths_within_scope([{"path": "src/foo/../evil.py"}],
                                     ["src/foo/**"])
    assert not ok
    assert viol == ["src/foo/../evil.py"]
    ok, _ = zw.paths_within_scope([{"path": "../escape.txt"}], ["src/foo/**"])
    assert not ok


def test_scope_rejects_git_admin_even_if_patterned():
    # Git-admin paths are ALWAYS out of scope (VOL-13 §4)
    cases = [{"path": ".git/config"}, {"path": ".git"}, {"path": ".gitmodules"}]
    ok, viol = zw.paths_within_scope(cases, ["src/foo/**", ".git/**"])
    assert not ok
    assert viol == [".git/config", ".git", ".gitmodules"]


def test_scope_one_level_and_prefix_dir():
    ok, _ = zw.paths_within_scope([{"path": "src/foo/a.py"}], ["src/foo/*"])
    assert ok
    ok, viol = zw.paths_within_scope([{"path": "src/foo/sub/b.py"}], ["src/foo/*"])
    assert not ok                            # deeper than one level
    ok, _ = zw.paths_within_scope([{"path": "src/foo/a.py"}], ["src/foo"])
    assert ok                                 # pattern without globs: exact-or-prefix-dir
    ok, viol = zw.paths_within_scope([{"path": "src/foobar/x.py"}], ["src/foo"])
    assert not ok                             # prefix must be a directory boundary


def test_scope_checks_rename_orig_path_and_normalizes():
    scope = ["src/foo/**"]
    ok, _ = zw.paths_within_scope(
        [{"path": "src/foo/b.py", "orig_path": "src/foo/a.py"}], scope)
    assert ok
    ok, viol = zw.paths_within_scope(
        [{"path": "src/foo/b.py", "orig_path": "outside/a.py"}], scope)
    assert not ok and viol == ["outside/a.py"]
    ok, _ = zw.paths_within_scope([{"path": "src\\foo\\win.py"}], scope)
    assert ok                                 # backslashes normalized to forward
