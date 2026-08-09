# ============================================================================
# conftest.py — Shared fixtures for the Codex-LOOP-Build-F2 test suite
# Purpose : Hermetic per-test LOOP_ROOT (temp data plane + symlinked harness),
#           mock_codex scenario isolation, scratch git repos, and a `Loop`
#           helper object wrapping the harness CLIs. Every test gets a fresh
#           tmp dir (auto-cleaned by pytest) and a fresh mock scenario state.
# Input   : none (pytest plugin conventions)
# Output  : fixtures `loop`, `repo_loop`; helper class Loop.
# ============================================================================
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]          # codex-loop-s-f2/
HARNESS = PKG / "harness"
CONFIG = PKG / "config"
TESTS = PKG / "tests"
MOCK = TESTS / "mock_codex"
PY = sys.executable


class Loop:
    """Hermetic harness driver: fresh LOOP_ROOT, event/report/packet helpers."""

    def __init__(self, root: Path, state_dir: Path):
        self.root = root
        self.data = root / "data"
        self.state_dir = state_dir            # mock_codex scenario state (isolated)
        self.repo = None                      # set by attach_repo()
        self.wt_dir = None

    # ---- environment ------------------------------------------------------
    def env(self, **extra):
        e = os.environ.copy()
        e["LOOP_ROOT"] = str(self.root)
        e["MOCK_CODEX_STATE"] = str(self.state_dir)
        # sandbox has no global git identity; rebase/commit need one
        e.update(GIT_AUTHOR_NAME="loop-test", GIT_AUTHOR_EMAIL="loop@test.local",
                 GIT_COMMITTER_NAME="loop-test", GIT_COMMITTER_EMAIL="loop@test.local")
        if self.repo:
            e["LOOP_REPO"] = str(self.repo)
            e["LOOP_WT_DIR"] = str(self.wt_dir)
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def run(self, cmd, check=False, timeout=120, **extra_env):
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                           env=self.env(**extra_env), timeout=timeout)
        if check and p.returncode != 0:
            raise AssertionError("cmd %s failed rc=%d\nstdout:%s\nstderr:%s"
                                 % (cmd, p.returncode, p.stdout, p.stderr))
        return p

    # ---- harness invocations ------------------------------------------------
    def harness(self, name):
        return HARNESS / name

    def sm(self, *args, **extra_env):
        return self.run([PY, self.harness("statemachine.py"), *args], **extra_env)

    def step(self):
        """Run one state-machine step; return (rc, {pid: state})."""
        p = self.sm("step")
        states = {}
        for line in p.stdout.strip().splitlines():
            try:
                states = json.loads(line)
            except ValueError:
                continue
        return p.returncode, states

    # ---- data plane helpers ---------------------------------------------------
    def append_event(self, pid, event, detail=None):
        with open(self.data / "events.ndjson", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "packet_id": pid,
                                "event": event, "detail": detail or {}}) + "\n")

    def events(self):
        out = []
        for line in (self.data / "events.ndjson").read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def write_packet(self, pid, goal="mock goal + completion definition",
                     paths=None, acceptance=None, constraints=None):
        pkt = {"packet_id": pid, "goal": goal,
               "authorized_paths": paths or ["src/%s/" % pid],
               "acceptance": acceptance or ["true"],
               "constraints": constraints or []}
        (self.data / "packets" / ("%s.json" % pid)).write_text(json.dumps(pkt, indent=1))
        return pkt

    def write_dag(self, edges=None, waves=None):
        (self.data / "packets" / "dag.json").write_text(
            json.dumps({"edges": edges or [], "waves": waves or []}))

    def write_report(self, pid, status="done", **extra):
        d = self.data / "reports" / pid
        d.mkdir(parents=True, exist_ok=True)
        rpt = {"packet_id": pid, "status": status, "summary": "mock", "diff_stat": "mock"}
        rpt.update(extra)
        (d / "report.json").write_text(json.dumps(rpt))

    def ledger(self):
        return json.loads((self.data / "progress_ledger.json").read_text())

    def set_ledger(self, led):
        (self.data / "progress_ledger.json").write_text(json.dumps(led, indent=1))

    def state(self, pid):
        return self.ledger()["packets"].get(pid, {"state": "NONE"})["state"]

    def history(self, pid):
        return self.ledger()["packets"].get(pid, {}).get("history", [])

    def write_config(self, duty_enforce=None, passthrough=None):
        cfg = self.root / "config"
        cfg.mkdir(exist_ok=True)
        lines = []
        if passthrough is not None:
            lines += ["[escalation]", "passthrough_enabled = %s" % str(passthrough).lower()]
        if duty_enforce is not None:
            lines += ["[duty_officer]", "enforce = %s" % str(duty_enforce).lower()]
        (cfg / "config.toml").write_text("\n".join(lines) + "\n")

    def sol_wakes(self):
        d = self.data / "sol_wake"
        return sorted(d.glob("*.md")) if d.exists() else []

    def escalations(self, level=None):
        p = self.data / "escalation_log.jsonl"
        if not p.exists():
            return []
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        return [r for r in rows if level is None or r.get("level") == level]

    # ---- mock codex layer -----------------------------------------------------
    def set_scenario(self, scen):
        self.run(["bash", MOCK / "scenario_control.sh", "set", scen], check=True)

    def reset_scenario(self):
        self.run(["bash", MOCK / "scenario_control.sh", "reset"], check=True)

    def mock_spawn(self, pid, worktree=None, scenario=None):
        cmd = ["bash", MOCK / "mock_spawn.sh", pid]
        if worktree:
            cmd.append(worktree)
        extra = {"MOCK_SCENARIO": scenario} if scenario else {}
        return self.run(cmd, **extra)

    # ---- git repo / worktree pool -----------------------------------------------
    def attach_repo(self, repo_dir, wt_dir):
        self.repo = Path(repo_dir)
        self.wt_dir = Path(wt_dir)
        subprocess.run(["bash", str(MOCK / "setup_test_repo.sh"), str(repo_dir)],
                       check=True, capture_output=True, text=True)

    def pool(self, *args, **extra_env):
        return self.run(["bash", self.harness("worktree_pool.sh"), *args], **extra_env)

    def allocate(self, pid):
        p = self.pool("allocate", pid)
        assert p.returncode == 0, "allocate %s failed: %s" % (pid, p.stderr)
        return p.stdout.strip().splitlines()[-1]

    def worktree_diff(self, pid):
        """Unified diff of packet branch vs main base (for diffvalidator)."""
        wt = str(self.wt_dir / pid)
        p = subprocess.run(["git", "-C", wt, "diff", "main...HEAD"],
                           capture_output=True, text=True)
        return p.stdout


@pytest.fixture
def loop(tmp_path):
    root = tmp_path / "loop"
    (root / "data").mkdir(parents=True)
    for d in ("packets", "reports", "dead_letters"):
        (root / "data" / d).mkdir()
    for f in ("events.ndjson", "escalation_log.jsonl", "lessons.jsonl"):
        (root / "data" / f).touch()
    (root / "data" / "progress_ledger.json").write_text('{"packets": {}, "waves": []}')
    os.symlink(HARNESS, root / "harness")
    (root / "config").mkdir()
    for cf in ("retry_classes.yaml", "triggers.yaml"):
        os.symlink(CONFIG / cf, root / "config" / cf)
    state = tmp_path / "mock_state"
    state.mkdir()
    (state / "scenario").write_text("normal\n")
    lp = Loop(root, state)
    yield lp
    # tmp_path is removed by pytest; nothing global to reset (state is per-test)


@pytest.fixture
def repo_loop(loop, tmp_path):
    """Loop plus a scratch git repo + worktree pool dir wired via env."""
    loop.attach_repo(tmp_path / "repo", tmp_path / "worktrees")
    return loop
