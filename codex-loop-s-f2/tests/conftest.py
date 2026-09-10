"""conftest.py — shared fixtures for the codex-loop-s-f2 v2 test suite.

Every test runs against an isolated LOOP root under ``tmp_path`` with the
real config files copied in (single-source-of-truth discipline: tests read
model pins and thresholds from config, never hardcode them).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

IMPL = Path(__file__).resolve().parent.parent
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
                     filename: str = "orchestration_policy.toml") -> None:
    """Flip the [routing].mode line in a copied policy file (one-key switch)."""
    path = root / "config" / filename
    text = path.read_text(encoding="utf-8")
    new = re.sub(r'^mode = "[a-z_]+"', 'mode = "%s"' % mode,
                 text, count=1, flags=re.M)
    assert new != text or ('mode = "%s"' % mode) in text
    path.write_text(new, encoding="utf-8")


def green_guards(root: Path, now: float | None = None) -> None:
    """Satisfy all six layered-mode gate guards on disk."""
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
