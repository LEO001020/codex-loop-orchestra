"""Regression tests for the manifest-driven v2 installer.

The tests use an isolated target and CODEX_HOME.  They never install into the
production Windows or WSL trees.  A POSIX bash is required because the shipped
installer is intentionally a WSL/POSIX entry point.
"""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

from tests.orchestration_v2.conftest import IMPL


BASH = shutil.which("bash")
# On Windows ``bash.exe`` is normally the WSL compatibility launcher.  Passing
# native ``E:\\...``/``C:\\...`` paths to it is not a POSIX-shell contract and
# can silently strip backslashes.  The same tests are run inside the dedicated
# WSL venv in cross-plane CI/smoke; Windows pytest therefore skips this shell
# suite instead of producing a false installer failure.
pytestmark = pytest.mark.skipif(
    BASH is None or os.name == "nt",
    reason="installer regression suite requires a native POSIX/WSL Python run",
)


def _run(script: Path, *args: str, env: dict[str, str] | None = None):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    test_id = merged.get("PYTEST_CURRENT_TEST", "isolated-install").split(" ", 1)[0]
    state_name = hashlib.sha256((test_id + str(os.getpid())).encode("utf-8")).hexdigest()[:16]
    state_dir = (Path(merged["CODEX_HOME"]).parent / "loop-state"
                 if merged.get("CODEX_HOME") else
                 Path(tempfile.gettempdir()) / "codex-loop-orchestra-tests" / state_name)
    merged.setdefault(
        "CODEX_LOOP_STATE_DIR",
        str(state_dir),
    )
    return subprocess.run(
        [BASH, str(script), *map(str, args)],
        cwd=IMPL,
        env=merged,
        text=True,
        capture_output=True,
        timeout=90,
    )


def _copy_managed_source(dst: Path) -> None:
    manifest = IMPL / "config" / "managed_files_v2.txt"
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        rel = raw.strip()
        if not rel or rel.startswith("#"):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(IMPL / rel, target)


def _fake_prerequisites(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True)
    scripts = {
        "node": "#!/usr/bin/env sh\necho v22.23.2\n",
        "codex": "#!/usr/bin/env sh\necho codex-cli 0.0.0\n",
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)


def test_release_checksums_cover_exact_managed_boundary():
    managed = {
        line.strip() for line in (IMPL / "config/managed_files_v2.txt").read_text(
            encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    rows = {}
    for line in (IMPL / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        rows[rel] = digest
    assert set(rows) == managed - {"SHA256SUMS"}
    assert "SHA256SUMS" in managed
    for rel, digest in rows.items():
        assert hashlib.sha256((IMPL / rel).read_bytes()).hexdigest() == digest


def test_dry_run_is_zero_write_and_validates_all_sources(tmp_path):
    target = tmp_path / "must-not-be-created"
    proc = _run(
        IMPL / "install_v2.sh", "--target", target,
        "--dry-run", "--skip-user-config",
    )
    assert proc.returncode == 0, proc.stderr
    assert "DRY RUN (zero writes)" in proc.stdout
    assert not target.exists()


def test_missing_managed_source_fails_before_target_write(tmp_path):
    source = tmp_path / "source"
    _copy_managed_source(source)
    (source / "harness" / "agent_router.py").unlink()
    target = tmp_path / "must-not-be-created"
    proc = _run(
        source / "install_v2.sh", "--target", target,
        "--skip-user-config",
    )
    assert proc.returncode != 0
    assert "managed source missing: harness/agent_router.py" in proc.stderr
    assert not target.exists()


def test_existing_file_is_backed_up_and_runtime_closure_imports(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    old = b"old installer bytes\n"
    (target / "install.sh").write_bytes(old)
    proc = _run(
        IMPL / "install_v2.sh", "--target", target,
        "--skip-user-config",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    backups = list(target.glob("backup-v2-*/install.sh"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == old
    assert (target / "install.sh").read_bytes() == (IMPL / "install.sh").read_bytes()
    assert "v2 validation OK" in proc.stdout


def test_repeated_install_never_overwrites_prior_backup(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    first = b"first prior version\n"
    second = b"second prior version\n"
    installed = target / "install.sh"
    installed.write_bytes(first)
    proc1 = _run(
        IMPL / "install_v2.sh", "--target", target,
        "--skip-user-config",
    )
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr
    installed.write_bytes(second)
    proc2 = _run(
        IMPL / "install_v2.sh", "--target", target,
        "--skip-user-config",
    )
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    backups = list(target.glob("backup-v2-*/install.sh"))
    assert len(backups) == 2
    assert {path.read_bytes() for path in backups} == {first, second}


def test_package_copy_failure_rolls_back_every_prior_change(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    originals = {
        "VERSIONS.lock": b"old versions\n",
        "install.sh": b"old install\n",
        "install_v2.sh": b"old install v2\n",
    }
    for rel, content in originals.items():
        (target / rel).write_bytes(content)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_cp = fake_bin / "cp"
    fake_cp.write_text(
        "#!/usr/bin/env bash\n"
        "src=\"${@: -2:1}\"; dst=\"${@: -1}\"\n"
        "if [[ \"$src\" == */install_v2.sh && \"$dst\" == *.tmp.* ]]; then exit 73; fi\n"
        "exec /bin/cp \"$@\"\n",
        encoding="utf-8",
    )
    fake_cp.chmod(0o755)
    proc = _run(
        IMPL / "install_v2.sh", "--target", target,
        "--skip-user-config",
        env={"PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", "")},
    )
    assert proc.returncode != 0
    assert "package copy rolled back after failure" in proc.stderr
    for rel, content in originals.items():
        assert (target / rel).read_bytes() == content
    assert not list(target.rglob("*.tmp.*"))
    assert not list(target.glob(".install-v2-journal-*"))


def test_second_writer_fails_visible_without_touching_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    lock_dir = target / ".install-v2.lock.d"
    lock_dir.mkdir()
    sentinel = target / "sentinel"
    sentinel.write_bytes(b"unchanged")
    proc = _run(
        IMPL / "install_v2.sh", "--target", target,
        "--skip-user-config", "--skip-smoke",
    )
    assert proc.returncode == 75
    assert "another install_v2 writer owns target lock" in proc.stderr
    assert sentinel.read_bytes() == b"unchanged"
    assert list(lock_dir.iterdir()) == []


def test_success_releases_target_writer_lock(tmp_path):
    target = tmp_path / "target"
    proc = _run(
        IMPL / "install_v2.sh", "--target", target,
        "--skip-user-config", "--skip-smoke",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (target / ".install-v2.lock.d").exists()
    assert not (target / ".install-v2.lock").exists()


def test_user_config_and_hooks_merge_preserve_unmanaged_entries(tmp_path):
    repo = tmp_path / "repo"
    home = tmp_path / "codex-home"
    fake_bin = tmp_path / "bin"
    repo.mkdir(parents=True)
    home.mkdir()
    _fake_prerequisites(fake_bin)
    config = home / "config.toml"
    config.write_text('user_key = "keep"\n', encoding="utf-8")
    hooks = home / "hooks.json"
    hooks.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [
                {"hooks": [{"type": "command", "command": "custom.py"}]},
                {"hooks": [{"type": "command", "command": "old/sol_tool_gate.py"}]},
                {"hooks": [{"type": "command", "command": "powershell.exe -File E:/old/reconcile_subagent_metering.ps1"}]},
            ]
        },
        "user_metadata": {"keep": True},
    }), encoding="utf-8")
    env = {
        "CODEX_HOME": str(home),
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
    }
    proc = _run(IMPL / "install.sh", "--repo", repo, "--skip-smoke", env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    with config.open("rb") as handle:
        merged_config = tomllib.load(handle)
    assert merged_config["user_key"] == "keep"
    assert list(home.glob("config.toml.bak.*"))

    merged_hooks = json.loads(hooks.read_text(encoding="utf-8"))
    blob = json.dumps(merged_hooks, ensure_ascii=False)
    assert merged_hooks["user_metadata"] == {"keep": True}
    assert "custom.py" in blob
    requirements = (home / "requirements.toml").read_text(encoding="utf-8")
    assert "--component spawn-gate" in requirements
    assert "--component lifecycle" in requirements
    assert "old/sol_tool_gate.py" not in blob
    assert "E:/old/reconcile_subagent_metering.ps1" not in blob
    reconcile_specs = [
        hook for group in merged_hooks["hooks"].get("Stop", [])
        for hook in group.get("hooks", [])
        if "reconcile_subagent_metering.ps1" in hook.get("command", "")
    ]
    assert len(reconcile_specs) == 0
    assert "backup_dir" in proc.stdout


def test_malformed_hooks_fail_before_agents_or_config_change(tmp_path):
    repo = tmp_path / "repo"
    home = tmp_path / "codex-home"
    fake_bin = tmp_path / "bin"
    repo.mkdir(parents=True)
    (home / "agents").mkdir(parents=True)
    _fake_prerequisites(fake_bin)
    config = home / "config.toml"
    original_config = b'user_key = "keep"\n'
    config.write_bytes(original_config)
    existing_worker = home / "agents" / "worker.toml"
    original_worker = b"user worker bytes\n"
    existing_worker.write_bytes(original_worker)
    hooks = home / "hooks.json"
    hooks.write_text("{malformed", encoding="utf-8")
    env = {
        "CODEX_HOME": str(home),
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
    }
    proc = _run(IMPL / "install.sh", "--repo", repo, "--skip-smoke", env=env)
    assert proc.returncode != 0
    assert existing_worker.read_bytes() == original_worker
    assert config.read_bytes() == original_config
    assert hooks.read_text(encoding="utf-8") == "{malformed"
    assert not list(home.rglob("*.bak.*"))
    assert not list(repo.rglob("*.bak.*"))


def test_portable_manifest_excludes_platform_specific_codex_state():
    entries = {
        line.strip() for line in
        (IMPL / "config" / "managed_files_v2.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    forbidden = {
        ".codex/config.toml",
        ".codex/hooks.json",
        ".codex/hooks/reconcile_subagent_metering.ps1",
    }
    assert entries.isdisjoint(forbidden)
    assert "install.sh" in entries and "install_v2.sh" in entries
    assert "VERSIONS.lock" in entries and "harness/smoke_gate.sh" in entries


def test_config_example_contains_only_codex_native_agent_scalars():
    """LOOP scheduler knobs belong to policy TOMLs, never native [agents]."""
    with (IMPL / "config" / "config.toml.example").open("rb") as handle:
        doc = tomllib.load(handle)
    agents = doc["agents"]
    forbidden = {
        "normal_wave_concurrency",
        "normal_wave_low_water",
        "normal_k3_wave_concurrency",
        "normal_k3_wave_low_water",
        "idle_reclaim_threshold",
    }
    assert forbidden.isdisjoint(agents)
    assert agents["max_concurrent_threads_per_session"] == 50


def test_smoke_gate_bounds_each_real_provider_probe():
    text = (IMPL / "harness" / "smoke_gate.sh").read_text(encoding="utf-8")
    assert 'SMOKE_ROLE_TIMEOUT_SECONDS:-45' in text
    assert text.count('timeout --signal=TERM --kill-after=5s') >= 2


def test_top_level_install_runs_smoke_by_default_and_skip_is_explicit():
    text = (IMPL / "install_v2.sh").read_text(encoding="utf-8")
    assert "SKIP_SMOKE=0" in text
    assert "--skip-smoke) SKIP_SMOKE=1" in text
    assert '[[ $SKIP_SMOKE -eq 1 ]] && args+=(--skip-smoke)' in text


def test_installer_validates_policy_relationship_not_stale_target_literal():
    text = (IMPL / "install_v2.sh").read_text(encoding="utf-8")
    assert "refill.v4_target()" in text and "refill.k3_target()" in text
    assert "targets[1] + targets[2] != targets[0]" in text
    assert "(48, 36, 12)" not in text
    assert "import refill_consumer_v2, parent_manifest_importer" in text
