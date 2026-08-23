from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tests.orchestration_v2.conftest import make_root, set_routing_mode
from plan_consumer import PlanConsumer


def _inputs(root: Path, request_id: str = "req-1", packet_id: str = "planning-1"):
    source = root / "data" / "plans" / "source"
    source.mkdir(parents=True, exist_ok=True)
    decision = source / "decision.json"
    control = source / "control.json"
    decision.write_text(json.dumps({
        "decision_skeleton_id": "d-1", "control_packet_id": "c-1",
        "objective": "bounded plan", "definition_of_done": ["done"],
        "constraints": [], "decisions": [], "allowed_side_effects": [],
        "risk_class": "low", "evidence_roots": [], "unresolved_choices": [],
    }), encoding="utf-8")
    control.write_text(json.dumps({"control_packet_id": "c-1", "revision": 1}),
                       encoding="utf-8")
    inbox = root / "data" / "plans" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    request = inbox / (request_id + ".json")
    request.write_text(json.dumps({
        "request_id": request_id, "packet_id": packet_id,
        "decision_skeleton": str(decision.relative_to(root)),
        "control_packet": str(control.relative_to(root)),
        "timeout_seconds": 30,
    }), encoding="utf-8")
    return request


def _events(root: Path):
    path = root / "data" / "events.ndjson"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_cold_start_is_zero_birth(tmp_path):
    root = make_root(tmp_path)
    _inputs(root)
    calls = []
    stats = PlanConsumer(root, runner=lambda *a, **k: calls.append(a)).drain()
    assert stats.mode == "cold_start" and stats.discovered == 0
    assert calls == [] and _events(root) == []


def test_shadow_records_would_expand_without_claim_or_birth(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "shadow")
    _inputs(root)
    calls = []
    stats = PlanConsumer(root, runner=lambda *a, **k: calls.append(a)).drain()
    assert stats.observed == 1 and calls == []
    assert (root / "data/plans/shadow/req-1.json").exists()
    assert not list((root / "data/plans/claims").glob("*.json"))


def test_layered_claims_exactly_once_and_emits_valid_edges(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    _inputs(root)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        output = Path(command[command.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"ok": True}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    consumer = PlanConsumer(root, runner=runner, clock=lambda: 100.0)
    first = consumer.drain()
    second = consumer.drain()
    assert first.claimed == 1 and first.completed == 1
    assert second.already_terminal == 1 and len(calls) == 1
    command, kwargs = calls[0]
    assert command[-1] == "--execute"
    assert kwargs["env"]["LOOP_ROOT"] == str(root)
    assert [item["event"] for item in _events(root)] == [
        "skeleton_ready", "expansion_valid"]


def test_layered_failure_emits_expansion_invalid_once(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    _inputs(root)
    runner = lambda *a, **k: SimpleNamespace(returncode=124)
    consumer = PlanConsumer(root, runner=runner, clock=lambda: 100.0)
    stats = consumer.drain()
    assert stats.failed == 1
    assert [item["event"] for item in _events(root)] == [
        "skeleton_ready", "expansion_invalid"]
    assert json.loads((root / "data/plans/completions/req-1.json").read_text())["returncode"] == 124


def test_escape_and_identity_mismatch_fail_closed_without_birth(tmp_path):
    root = make_root(tmp_path)
    set_routing_mode(root, "layered")
    request = _inputs(root)
    doc = json.loads(request.read_text())
    doc["decision_skeleton"] = str(tmp_path.parent / "outside.json")
    request.write_text(json.dumps(doc), encoding="utf-8")
    calls = []
    stats = PlanConsumer(root, runner=lambda *a, **k: calls.append(a)).drain()
    assert stats.failed == 1 and stats.errors and calls == []
    assert json.loads((root / "data/plans/completions/req-1.json").read_text())["status"] == "rejected"
