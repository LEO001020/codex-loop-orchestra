"""conftest.py — shared fixtures for the codex-loop-s-f2 v2 test suite.

Every test runs against an isolated LOOP root under ``tmp_path`` with the
real config files copied in (single-source-of-truth discipline: tests read
model pins and thresholds from config, never hardcode them).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

IMPL = Path(__file__).resolve().parent.parent
PKG = IMPL
HARNESS = PKG / "harness"
CONFIG = PKG / "config"
TESTS = PKG / "tests"
MOCK = TESTS / "mock_codex"
PY = sys.executable


def _resolve_bash() -> str:
    """Git-Bash-style bash for Windows subprocesses.

    On Windows a bare 'bash' argv can resolve to WSL bash.exe, which cannot
    see drive-letter paths like E:\\... and fails every script invocation
    with exit 127.  Probe the standard Git for Windows locations first;
    non-Windows and explicit overrides pass through unchanged.
    """
    override = os.environ.get("LOOP_TEST_GIT_BASH")
    if override:
        return override
    if os.name != "nt":
        return "bash"
    for candidate in (r"C:\Program Files\Git\bin\bash.exe",
                      r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.exists(candidate):
            return candidate
    return shutil.which("bash") or "bash"


BASH = _resolve_bash()
for _sub in ("harness", "metering", "hooks"):
    _p = str(IMPL / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

CONFIG_FILES = (
    "orchestration_policy.toml",
    "orchestration_policy_v2.toml",
    "refill_policy.toml",
    "loop_config_v2.toml",
    "statemachine_v2_transitions.json",
    "roles_v2.yaml",
    "triggers_v2.yaml",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate every test from ambient LOOP/codex environment state."""
    for var in ("LOOP_ROOT", "LOOP_ORCH_POLICY", "LOOP_REFILL_POLICY",
                "LOOP_GOVERNOR_OVERRIDE", "LOOP_EXECUTION_PLANE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CODEX_HOME", "/nonexistent-codex-home-for-tests")
    yield


def make_root(tmp_path: Path) -> Path:
    """Create an isolated LOOP root with real config files copied in."""
    root = tmp_path / "loop"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    for name in CONFIG_FILES:
        src = IMPL / "config" / name
        if src.exists():
            shutil.copy2(src, root / "config" / name)
    return root


@pytest.fixture
def loop_root(tmp_path: Path) -> Path:
    return make_root(tmp_path)


def set_routing_mode(root: Path, mode: str,
                     filename: str = "orchestration_policy_v2.toml") -> None:
    """Flip the [routing].mode line in a copied policy file (one-key switch).

    Defaults to the v2 policy file: the v2-era router reads
    orchestration_policy_v2.toml, so editing the legacy v1 file has no
    routing effect (pre-v2 tests silently edited a file nobody reads)."""
    path = root / "config" / filename
    text = path.read_text(encoding="utf-8")
    new = re.sub(r'^mode = "[a-z_]+"', 'mode = "%s"' % mode,
                 text, count=1, flags=re.M)
    assert new != text or ('mode = "%s"' % mode) in text
    path.write_text(new, encoding="utf-8")


def green_guards(root: Path, now: float | None = None) -> None:
    """Satisfy ALL layered-mode gate guards on disk.

    The gate has grown from six to thirteen conditions (default_adapter,
    meter_v2_fresh, plan_pipeline, provider_health, lifecycle_roster,
    rollback_rehearsal, dual_plane_hash joined the original six).  Fixtures
    that enable layered mode must seed every condition, otherwise the gate
    correctly refuses and the fixture cannot exercise layered behavior.
    """
    now = time.time() if now is None else now
    q = root / "data" / "l2_queue"
    q.mkdir(parents=True, exist_ok=True)
    (q / "consumer_heartbeat.json").write_text(
        json.dumps({"ts": now, "pid": 0}), encoding="utf-8")
    (q / "exactly_once_canary.json").write_text(
        json.dumps({"status": "PASS", "ts": now}), encoding="utf-8")
    v = root / "data" / "validators"
    v.mkdir(parents=True, exist_ok=True)
    (v / "short_result_validator.json").write_text(
        json.dumps({"enabled": True}), encoding="utf-8")
    ledger = root / "data" / "progress_ledger.json"
    led = {"packets": {}}
    if ledger.exists():
        try:
            led = json.loads(ledger.read_text(encoding="utf-8"))
        except ValueError:
            led = {"packets": {}}
    led["schema"] = "codex-loop-statemachine/v2"
    led.setdefault("packets", {})
    ledger.write_text(json.dumps(led), encoding="utf-8")

    gov = root / "data" / "governor"
    gov.mkdir(parents=True, exist_ok=True)
    policy_path = root / "config" / "orchestration_policy_v2.toml"
    try:
        import hashlib
        import tomllib
        policy = tomllib.loads(policy_path.read_text(encoding="utf-8"))
        policy_sha = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        k3_model = str(policy.get("models", {}).get("k3_model", ""))
    except (OSError, ValueError):
        policy, policy_sha, k3_model = {}, "", ""
    # default_adapter / plan_pipeline: attestations (fixtures have no full
    # harness tree, so the gate accepts its attestation markers).
    (gov / "default_adapter.json").write_text(
        json.dumps({"status": "PASS", "ts": now}), encoding="utf-8")
    (gov / "plan_pipeline.json").write_text(
        json.dumps({"status": "PASS", "ts": now, "model": k3_model}),
        encoding="utf-8")
    # meter_v2_fresh: a fresh report with a denominator above the minimum.
    minimum = int(policy.get("tokens", {}).get("minimum_denominator", 2_000_000))
    usage = root / "data" / "usage"
    usage.mkdir(parents=True, exist_ok=True)
    (usage / "model_token_share_v2.json").write_text(json.dumps({
        "generated_at": now, "primary_window": "rolling_5h",
        "windows": {"rolling_5h": {"status": "OK",
                                   "production_effective_tokens": minimum + 1}}}),
        encoding="utf-8")
    # provider_health: a healthy, fresh K3 route document at the policy path.
    try:
        sys.path.insert(0, str(root / "harness")) if (root / "harness").is_dir() else None
        from provider_health import health_path as _health_path
        hpath = _health_path(root, k3_model) if k3_model else None
    except Exception:
        hpath = None
    if hpath is None:
        hpath = root / "data" / "provider_health" / "k3.json"
    hpath.parent.mkdir(parents=True, exist_ok=True)
    hpath.write_text(json.dumps({"status": "healthy", "ts": now,
                                 "model": k3_model, "backoff_until": 0}),
                     encoding="utf-8")
    # lifecycle_roster: a parseable exec roster with a jobs map.
    lc = root / "data" / "lifecycle"
    lc.mkdir(parents=True, exist_ok=True)
    (lc / "exec_roster.json").write_text(json.dumps({"jobs": {}}),
                                         encoding="utf-8")
    # rollback_rehearsal + dual_plane_hash: fresh PASS markers bound to the
    # exact current policy bytes.
    (gov / "rollback_rehearsal.json").write_text(json.dumps({
        "status": "PASS", "ts": now, "restored_sha256": policy_sha}),
        encoding="utf-8")
    (gov / "dual_plane_hash.json").write_text(json.dumps({
        "status": "PASS", "ts": now, "policy_sha256": policy_sha,
        "windows_manifest_sha256": "test-manifest",
        "wsl_manifest_sha256": "test-manifest"}), encoding="utf-8")



def enable_layered(root: Path) -> bool:
    """Production cold_start -> layered flip for fixtures.

    The layered router requires a CURRENT layered_authorization bound to the
    exact policy bytes; editing the policy line alone correctly downgrades
    to cold_start.  Seed every gate condition (green_guards) first, then run
    the real LayeredGate.enable() (runs all 13 conditions, sets the policy
    mode and writes the authorization).  Returns True when authorized.
    """
    sys.path.insert(0, str(IMPL / "harness"))
    from layered_gate import LayeredGate
    gate = LayeredGate(root)
    return bool(gate.enable().allow)

def emit_event(root: Path, packet_id: str, event: str,
               detail: dict | None = None, attempt: int | None = None) -> None:
    """Append one state-machine event line to data/events.ndjson."""
    events = root / "data" / "events.ndjson"
    events.parent.mkdir(parents=True, exist_ok=True)
    obj = {"ts": time.time(), "packet_id": packet_id, "event": event,
           "detail": detail or {}}
    if attempt is not None:
        obj["attempt"] = attempt
    with events.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")


def read_events(root: Path) -> list[dict]:
    """Parse all events from data/events.ndjson."""
    path = root / "data" / "events.ndjson"
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def good_short_result(**over) -> dict:
    """A schema-valid ShortResult document (v2 contract)."""
    doc = {
        "packet_id": "pkt-1",
        "control_packet_id": "cp-1",
        "control_packet_revision": 2,
        "status": "completed",
        "conclusion": "done; see artifacts",
        "artifact_paths": ["out/a.md"],
        "finding_ids": ["F1"],
        "needs_decision": None,
    }
    doc.update(over)
    return doc


def write_packet(root: Path, pid: str, **over) -> Path:
    """Write a minimal valid packet file for dispatch tests."""
    pkt = {"packet_id": pid, "goal": "test goal",
           "authorized_paths": ["src/"], "acceptance": ["tests pass"]}
    pkt.update(over)
    pdir = root / "data" / "packets"
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / (pid + ".json")
    path.write_text(json.dumps(pkt), encoding="utf-8")
    return path


def write_meter_report(root: Path, sol_share: float,
                       generated_at: float | None = None,
                       status: str = "OK") -> Path:
    """Write a v1-shape meter report the governor can read."""
    now = time.time() if generated_at is None else generated_at
    report = {"generated_at": now,
              "windows": {"rolling_5h": {"status": status,
                                         "sol_share_effective": sol_share}}}
    usage = root / "data" / "usage"
    usage.mkdir(parents=True, exist_ok=True)
    path = usage / "meter_v2_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Legacy F2 fixture compatibility
# ---------------------------------------------------------------------------
# The v2 suite replaced the original conftest during orchestration work, but
# unit/golden tests still import these public helpers.  Keep one authoritative
# conftest module and expose both fixture generations from it; separate nested
# conftest files cause pytest import-file-mismatch failures on Windows.
class Loop:
    """Hermetic harness driver retained for the original unit/golden suite."""

    def __init__(self, root: Path, state_dir: Path):
        self.root = root
        self.data = root / "data"
        self.state_dir = state_dir
        self.repo = None
        self.wt_dir = None

    def env(self, **extra):
        value = os.environ.copy()
        value["LOOP_ROOT"] = str(self.root)
        value["MOCK_CODEX_STATE"] = str(self.state_dir)
        value.update(GIT_AUTHOR_NAME="loop-test",
                     GIT_AUTHOR_EMAIL="loop@test.local",
                     GIT_COMMITTER_NAME="loop-test",
                     GIT_COMMITTER_EMAIL="loop@test.local")
        if self.repo:
            value["LOOP_REPO"] = str(self.repo)
            value["LOOP_WT_DIR"] = str(self.wt_dir)
        value.update({key: str(item) for key, item in extra.items()})
        return value

    def run(self, command, check=False, timeout=120, **extra_env):
        argv = [str(item) for item in command]
        if argv and argv[0] == "bash":
            argv[0] = BASH
        result = subprocess.run(
            argv, capture_output=True, text=True, errors="replace",
            env=self.env(**extra_env), timeout=timeout)
        if check and result.returncode != 0:
            raise AssertionError(
                "cmd %s failed rc=%d\nstdout:%s\nstderr:%s" %
                (command, result.returncode, result.stdout, result.stderr))
        return result

    def harness(self, name):
        return HARNESS / name

    def sm(self, *args, **extra_env):
        return self.run([PY, self.harness("statemachine.py"), *args], **extra_env)

    def step(self):
        result = self.sm("step")
        states = {}
        for line in result.stdout.strip().splitlines():
            try:
                states = json.loads(line)
            except ValueError:
                continue
        return result.returncode, states

    def append_event(self, packet_id, event, detail=None):
        with (self.data / "events.ndjson").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "ts": time.time(), "packet_id": packet_id, "event": event,
                "detail": detail or {},
            }) + "\n")

    def events(self):
        return [json.loads(line) for line in
                (self.data / "events.ndjson").read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def write_packet(self, packet_id, goal="mock goal + completion definition",
                     paths=None, acceptance=None, constraints=None):
        packet = {
            "packet_id": packet_id, "goal": goal,
            "authorized_paths": paths or ["src/%s/" % packet_id],
            "acceptance": acceptance or ["true"],
            "constraints": constraints or [],
        }
        (self.data / "packets" / ("%s.json" % packet_id)).write_text(
            json.dumps(packet, indent=1), encoding="utf-8")
        return packet

    def write_dag(self, edges=None, waves=None):
        (self.data / "packets" / "dag.json").write_text(
            json.dumps({"edges": edges or [], "waves": waves or []}),
            encoding="utf-8")

    def write_report(self, packet_id, status="done", **extra):
        directory = self.data / "reports" / packet_id
        directory.mkdir(parents=True, exist_ok=True)
        report = {"packet_id": packet_id, "status": status,
                  "summary": "mock", "diff_stat": "mock"}
        report.update(extra)
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")

    def ledger(self):
        return json.loads((self.data / "progress_ledger.json").read_text(encoding="utf-8"))

    def set_ledger(self, ledger):
        (self.data / "progress_ledger.json").write_text(
            json.dumps(ledger, indent=1), encoding="utf-8")

    def state(self, packet_id):
        return self.ledger()["packets"].get(packet_id, {"state": "NONE"})["state"]

    def history(self, packet_id):
        return self.ledger()["packets"].get(packet_id, {}).get("history", [])

    def write_config(self, duty_enforce=None, passthrough=None):
        config_dir = self.root / "config"
        config_dir.mkdir(exist_ok=True)
        lines = []
        if passthrough is not None:
            lines += ["[escalation]",
                      "passthrough_enabled = %s" % str(passthrough).lower()]
        if duty_enforce is not None:
            lines += ["[duty_officer]",
                      "enforce = %s" % str(duty_enforce).lower()]
        (config_dir / "config.toml").write_text("\n".join(lines) + "\n",
                                                  encoding="utf-8")

    def sol_wakes(self):
        directory = self.data / "sol_wake"
        return sorted(directory.glob("*.md")) if directory.exists() else []

    def escalations(self, level=None):
        path = self.data / "escalation_log.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        return [row for row in rows if level is None or row.get("level") == level]

    def set_scenario(self, scenario):
        self.run(["bash", MOCK / "scenario_control.sh", "set", scenario], check=True)

    def reset_scenario(self):
        self.run(["bash", MOCK / "scenario_control.sh", "reset"], check=True)

    def mock_spawn(self, packet_id, worktree=None, scenario=None):
        command = ["bash", MOCK / "mock_spawn.sh", packet_id]
        if worktree:
            command.append(worktree)
        extra = {"MOCK_SCENARIO": scenario} if scenario else {}
        return self.run(command, **extra)

    def attach_repo(self, repo_dir, worktree_dir):
        self.repo = Path(repo_dir)
        self.wt_dir = Path(worktree_dir)
        subprocess.run([BASH, str(MOCK / "setup_test_repo.sh"), str(repo_dir)],
                       check=True, capture_output=True, text=True,
                       errors="replace")

    def pool(self, *args, **extra_env):
        return self.run(["bash", self.harness("worktree_pool.sh"), *args], **extra_env)

    def allocate(self, packet_id):
        result = self.pool("allocate", packet_id)
        assert result.returncode == 0, "allocate %s failed: %s" % (packet_id, result.stderr)
        return result.stdout.strip().splitlines()[-1]

    def worktree_diff(self, packet_id):
        result = subprocess.run(
            ["git", "-C", str(self.wt_dir / packet_id), "diff", "main...HEAD"],
            capture_output=True, text=True)
        return result.stdout


@pytest.fixture
def loop(tmp_path):
    root = tmp_path / "loop"
    (root / "data").mkdir(parents=True)
    for directory in ("packets", "reports", "dead_letters"):
        (root / "data" / directory).mkdir()
    for filename in ("events.ndjson", "escalation_log.jsonl", "lessons.jsonl"):
        (root / "data" / filename).touch()
    (root / "data" / "progress_ledger.json").write_text(
        '{"packets": {}, "waves": []}', encoding="utf-8")
    # Windows test runners commonly lack SeCreateSymbolicLinkPrivilege.
    # A private tmp_path copy is hermetic and avoids requiring elevation.
    shutil.copytree(HARNESS, root / "harness")
    (root / "config").mkdir()
    for filename in ("retry_classes.yaml", "triggers.yaml",
                     "orchestration_policy_v2.toml", "refill_policy.toml"):
        shutil.copy2(CONFIG / filename, root / "config" / filename)
    shutil.copytree(PKG / "agents", root / "agents")
    state = tmp_path / "mock_state"
    state.mkdir()
    (state / "scenario").write_text("normal\n", encoding="utf-8")
    yield Loop(root, state)


@pytest.fixture
def repo_loop(loop, tmp_path):
    loop.attach_repo(tmp_path / "repo", tmp_path / "worktrees")
    return loop
