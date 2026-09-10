"""Tests for 8–15 concurrency fixes:
- Non-blocking poll & thread pool in CodexSdkBackend (P0-1)
- Atomic staging rollback on acceptance failure (P0-2)
- Empty parent directory pruning on deletion (P0-4)
- Git worktree retry mechanism under contention (P1-1 / P1-3)
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zloop import db as zdb
from zloop import materialize as zmat
from zloop import stage as zstage
from zloop import workspace as zw
from zloop.backend.base import WorkerSpec
from zloop.backend.codex_sdk import CodexSdkBackend


def git(*args: str, cwd: Path) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p.stdout


@pytest.fixture()
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ZLOOP_DATA", str(data))
    conn = zdb.connect(data, create=True)
    yield zdb.ControlStore(data, conn, project_id="fix-test")
    conn.close()


from tests.test_materialize import _reported_packet, canon, worktrees


# ---------------------------------------------------------------- Tests


def test_codex_sdk_backend_async_pool_and_poll():
    """Test that CodexSdkBackend supports non-blocking poll() and thread pool execution."""
    backend = CodexSdkBackend.__new__(CodexSdkBackend)
    from concurrent.futures import ThreadPoolExecutor
    backend._executor = ThreadPoolExecutor(max_workers=4)
    backend._futures = {}
    backend._launches = {}
    backend._model = None
    backend._max_retries = 0

    # Create a mock handle that takes a short delay to run
    mock_handle = MagicMock()
    mock_handle.run.side_effect = lambda: (time.sleep(0.3), "mock-result")[1]

    mock_thread = MagicMock()
    mock_thread.turn.return_value = mock_handle
    backend._client = MagicMock()
    backend._client.thread_start.return_value = mock_thread

    spec = WorkerSpec(launch_id="test-async-1", workspace=Path("."), prompt="hello")
    launch = backend.start(spec)

    # First poll triggers async dispatch, return should be False while sleeping
    done_initially = backend.poll(launch)
    assert done_initially is False

    # Wait for completion
    status = backend.wait(launch, timeout=2.0)
    assert status == "terminal"
    assert backend.poll(launch) is True
    backend.close()


def test_codex_sdk_rate_limit_backoff_retry():
    """Test that 429 / RateLimit errors trigger backoff retry and succeed on subsequent attempt."""
    backend = CodexSdkBackend.__new__(CodexSdkBackend)
    backend._executor = None
    backend._futures = {}
    backend._launches = {}
    backend._max_retries = 2
    backend._model = None

    attempts = 0
    def mock_run():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Error 429: Too Many Requests / Rate limit exceeded")
        return "success-after-retry"

    mock_handle = MagicMock()
    mock_handle.run.side_effect = mock_run
    mock_thread = MagicMock()
    mock_thread.turn.return_value = mock_handle
    backend._client = MagicMock()
    backend._client.thread_start.return_value = mock_thread

    spec = WorkerSpec(launch_id="test-429-retry", workspace=Path("."), prompt="retry me")
    launch = backend.start(spec)

    res = backend.wait(launch)
    assert res == "terminal"
    assert attempts == 2
    assert launch.result == "success-after-retry"


def test_materialize_atomic_rollback_on_failure(store, canon, worktrees):
    """Test that acceptance failure with rollback_on_failure=True cleanly restores Staging HEAD."""
    worker, staging = worktrees
    run_id, sid, _p = _reported_packet(store, canon, acceptance=['python -c "exit(1)"'])

    initial_sha = zmat.staging_commit_sha(staging)
    # Worker produces a bad file
    (worker / "src" / "bad.py").write_text("broken = True\n", encoding="utf-8")

    res = zmat.materialize_packet(
        store, run_id, sid, "P01",
        git_root=canon, staging_ws=staging, workspace=worker,
        write_scope=["src/**"],
        acceptance=['python -c "exit(1)"'],
        rollback_on_failure=True
    )

    assert res["ok"] is False
    assert res["reason"] == "acceptance_failed"
    assert res.get("rolled_back") is True

    # Critical assertions:
    # 1. Staging HEAD must be strictly rolled back to the initial clean SHA
    assert zmat.staging_commit_sha(staging) == initial_sha
    # 2. Defective file must be completely wiped from staging tree
    assert not (staging / "src" / "bad.py").exists()


def test_materialize_prunes_empty_parents_on_delete(store, canon, worktrees):
    """Test that deleted files cause empty parent directories to be cleaned up."""
    worker, staging = worktrees
    # Create a nested file in staging
    nested_dir = staging / "src" / "sub" / "deep"
    nested_dir.mkdir(parents=True, exist_ok=True)
    (nested_dir / "temp.txt").write_text("clean me", encoding="utf-8")
    git("add", "src/sub/deep/temp.txt", cwd=staging)
    git("-c", "user.name=t", "-c", "user.email=t@e",
        "commit", "-m", "add nested", cwd=staging)

    delta = [{"record_type": "1", "xy": " D", "path": "src/sub/deep/temp.txt"}]
    zmat._apply_delta(delta, worker, staging)

    # The file should be removed AND the empty directories pruned
    assert not (staging / "src" / "sub" / "deep" / "temp.txt").exists()
    assert not (staging / "src" / "sub" / "deep").exists()


def test_create_worktree_parameter_validation(tmp_path):
    """Test create_worktree handles invalid base directory gracefully."""
    res = zw.create_worktree(tmp_path / "non_existent", tmp_path / "dest")
    assert res["ok"] is False
    assert "git_root does not exist" in res["reason"]


def test_create_worktree_index_lock_contention_heals(tmp_path, monkeypatch):
    """Test that git worktree add with index.lock contention heals via backoff retry."""
    root = tmp_path / "fake_repo"
    root.mkdir()
    dest = tmp_path / "dest_ws"

    attempts = 0
    def fake_run(args, cwd):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            return subprocess.CompletedProcess(
                args=args, returncode=128,
                stdout=b"", stderr=b"fatal: Unable to create '.git/index.lock': File exists."
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(zw, "_run", fake_run)
    res = zw.create_worktree(root, dest, max_retries=4)
    assert res["ok"] is True
    assert attempts == 3  # failed twice on index.lock, third attempt succeeded
