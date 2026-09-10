"""Worktree pool: dirty-worktree recovery boundary + merge SHA evidence.

Covers acceptance gate J (cleanup recovery: no silent loss of dirty work)
and the Git-evidence lineage (merged event carries the integration SHA).
Runs the real bash script against a real temp git repo. Skips when flock
is unavailable (Git Bash on Windows); the WSL authority environment
exercises the merge path.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent.parent / "harness"


def find_bash() -> str:
    """Locate a Git-Bash-style bash.

    On Windows the bare name 'bash' can resolve to WSL bash.exe (which
    cannot see /e/-style paths), so probe the standard Git for Windows
    locations first. Non-Windows and explicit overrides pass through.
    """
    import os
    import shutil
    override = os.environ.get("LOOP_TEST_GIT_BASH")
    if override:
        return override
    if os.name != "nt":
        return "bash"
    for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("bash")
    return found or "bash"


BASH = find_bash()


def git(repo: Path, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


def bash(script_args, env_extra, cwd):
    env = dict(subprocess.os.environ)
    env.update(env_extra)
    script = to_bash_path(HARNESS / "worktree_pool.sh")
    return subprocess.run([BASH, script, *script_args],
                          capture_output=True, text=True,
                          env=env, cwd=str(cwd), errors="replace")


def to_bash_path(path: Path) -> str:
    """Windows Git Bash needs /e/-style paths; real POSIX wants as_posix()."""
    try:
        probe = subprocess.run(["cygpath", "-u", str(path)],
                               capture_output=True, text=True,
                               errors="replace")
    except FileNotFoundError:
        return path.as_posix()
    if probe.returncode == 0 and probe.stdout.strip():
        return probe.stdout.strip()
    return path.as_posix()


def read_events(data_dir: Path):
    path = data_dir / "events.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def pool_env(tmp_path):
    repo = tmp_path / "repo"
    root = tmp_path / "loop"
    data = root / "data"
    data.mkdir(parents=True)
    repo.mkdir()
    env = {
        "LOOP_ROOT": to_bash_path(root),
        "LOOP_REPO": to_bash_path(repo),
        "LOOP_WT_DIR": to_bash_path(root / "worktrees"),
        "GIT_AUTHOR_NAME": "loop-test", "GIT_AUTHOR_EMAIL": "loop@test.local",
        "GIT_COMMITTER_NAME": "loop-test",
        "GIT_COMMITTER_EMAIL": "loop@test.local",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    base_env = dict(subprocess.os.environ)
    base_env.update(env)

    def g(repo_dir, *args):
        return subprocess.run(["git", "-C", str(repo_dir), *args],
                              capture_output=True, text=True, check=True,
                              env=base_env, errors="replace")

    g(repo, "init", "-b", "main")
    (repo / "src.txt").write_text("base\n", encoding="utf-8")
    g(repo, "add", "-A")
    g(repo, "commit", "-m", "base")
    return {"repo": repo, "root": root, "data": data, "env": env,
            "git": g, "base_env": base_env}


def test_clean_worktree_releases(pool_env):
    repo, env = pool_env["repo"], pool_env["env"]
    out = bash(["allocate", "p1"], env, repo.parent)
    assert out.returncode == 0
    assert "worktree_allocated" in [e["event"] for e in read_events(
        pool_env["data"])]
    wt = repo.parent / "loop" / "worktrees" / "p1"
    assert wt.is_dir()
    out = bash(["release", "p1"], env, repo.parent)
    assert out.returncode == 0
    assert not wt.exists()
    events = [e["event"] for e in read_events(pool_env["data"])]
    assert events[-1] == "worktree_released"


def test_dirty_worktree_refused_without_representation(pool_env):
    repo, env = pool_env["repo"], pool_env["env"]
    bash(["allocate", "p1"], env, repo.parent)
    wt = repo.parent / "loop" / "worktrees" / "p1"
    # untracked file + tracked modification, no commit, no patch saved
    (wt / "untracked_notes.md").write_text("only copy of a finding\n",
                                           encoding="utf-8")
    (wt / "src.txt").write_text("changed\n", encoding="utf-8")
    out = bash(["release", "p1"], env, repo.parent)
    assert out.returncode == 4
    assert wt.is_dir()  # worktree kept — recoverable boundary held
    assert (wt / "untracked_notes.md").exists()
    events = read_events(pool_env["data"])
    last = events[-1]
    assert last["event"] == "worktree_dirty_preserved"
    assert last["detail"]["dirty_entries"] >= 2


def test_dirty_worktree_saved_patch_archives_exact_delta(pool_env):
    repo, env = pool_env["repo"], pool_env["env"]
    bash(["allocate", "p1"], env, repo.parent)
    wt = repo.parent / "loop" / "worktrees" / "p1"
    (wt / "src.txt").write_text("changed\n", encoding="utf-8")
    (wt / "docs").mkdir()
    (wt / "docs" / "note.md").write_text("untracked content\n",
                                         encoding="utf-8")
    out = bash(["release", "p1", "--save-patch"], env, repo.parent)
    assert out.returncode == 0
    assert not wt.exists()
    archives = list((pool_env["data"] / "reports" / "p1").glob(
        "worktree-dirty-*"))
    assert len(archives) == 1
    archive = archives[0]
    patch = (archive / "tracked.patch").read_text(encoding="utf-8")
    assert "changed" in patch  # exact tracked delta preserved
    assert (archive / "untracked" / "docs" / "note.md").read_text(
        encoding="utf-8") == "untracked content\n"
    events = read_events(pool_env["data"])
    kinds = [e["event"] for e in events]
    assert kinds[-2:] == ["worktree_dirty_archived", "worktree_released"]


@pytest.mark.skipif(subprocess.run([BASH, "-c", "command -v flock"],
                                   capture_output=True).returncode != 0,
                    reason="flock unavailable (Windows Git Bash)")
def test_merged_event_records_integration_sha(pool_env):
    repo, env, g = pool_env["repo"], pool_env["env"], pool_env["git"]
    bash(["allocate", "p1"], env, repo.parent)
    wt = repo.parent / "loop" / "worktrees" / "p1"
    (wt / "src.txt").write_text("feature\n", encoding="utf-8")
    g(wt, "add", "-A")
    g(wt, "commit", "-m", "feature p1")
    out = bash(["merge", "p1"], env, repo.parent)
    assert out.returncode == 0
    events = read_events(pool_env["data"])
    merged = [e for e in events if e["event"] == "merged"]
    assert merged and merged[-1]["packet_id"] == "p1"
    sha = merged[-1]["detail"]["sha"]
    assert len(sha) == 40
    # the recorded sha is the real post-merge integration commit
    head = g(repo, "rev-parse", "loop-integration").stdout.strip()
    assert sha == head
    # and it contains the packet's change
    content = g(repo, "show", "%s:src.txt" % sha).stdout
    assert "feature" in content
