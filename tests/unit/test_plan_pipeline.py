import importlib.util
import json
from pathlib import Path

import pytest


PKG = Path(__file__).resolve().parents[2]
PATH = PKG / "harness" / "orchestration" / "plan_pipeline.py"
SPEC = importlib.util.spec_from_file_location("plan_pipeline", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def decision(path):
    doc = {
        "decision_skeleton_id": "d1", "control_packet_id": "c1",
        "objective": "ship", "definition_of_done": ["tests pass"],
        "constraints": [{"id": "x", "text": "read only"}],
        "decisions": [{"id": "d", "text": "use K3", "authority": "sol"}],
        "allowed_side_effects": [], "risk_class": "low",
        "evidence_roots": ["reports/a.md"], "unresolved_choices": [],
    }
    path.write_text(json.dumps(doc), encoding="utf-8")


def control(path):
    path.write_text(json.dumps({"control_packet_id": "c1", "revision": 1}),
                    encoding="utf-8")


def test_dry_run_is_headless_k3_without_ipybox(tmp_path, capsys):
    d, c, out = tmp_path / "decision.json", tmp_path / "control.json", tmp_path / "out.json"
    decision(d); control(c)
    assert MOD.main(["--decision-skeleton", str(d), "--control-packet", str(c),
                     "--output", str(out)]) == 0
    command = json.loads(capsys.readouterr().out)["command"]
    settings = MOD.load_plan_settings()
    assert command[command.index("-m") + 1] == settings["k3_model"]
    assert ("model_reasoning_effort=%s" % settings["k3_reasoning"]) in command
    assert "mcp_servers.ipybox.enabled=false" in command
    assert "--output-schema" in command
    assert "root transcript" in command[-1]


def test_invalid_decision_fails_before_spawn(tmp_path):
    d, c = tmp_path / "decision.json", tmp_path / "control.json"
    d.write_text("{}", encoding="utf-8"); control(c)
    with pytest.raises(ValueError, match="required property"):
        MOD.main(["--decision-skeleton", str(d), "--control-packet", str(c),
                  "--output", str(tmp_path / "out.json")])


def test_control_packet_requires_identity(tmp_path):
    d, c = tmp_path / "decision.json", tmp_path / "control.json"
    decision(d); c.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="control_packet_id"):
        MOD.main(["--decision-skeleton", str(d), "--control-packet", str(c),
                  "--output", str(tmp_path / "out.json")])


def test_verbose_k3_result_is_projected_and_missing_edges_are_derived(tmp_path):
    d, c = tmp_path / "decision.json", tmp_path / "control.json"
    decision(d); control(c)
    decision_doc = MOD.validate(d, MOD.DECISION_SCHEMA)
    control_doc = MOD.load_json(c)
    raw = {
        "control_packet_id": "c1", "decision_skeleton_id": "d1",
        "revision": 1, "objective": "extra explanation",
        "packets": [{
            "packet_id": "p1", "goal": "verify", "authorized_paths": ["reports/a.md"],
            "acceptance": ["read succeeds"], "constraints": [{"id": "x"}],
        }],
        "dag": {"nodes": ["p1"], "edges": []}, "needs_decision": [],
    }
    normalized = MOD.normalize_plan(raw, decision_doc, control_doc)
    assert set(normalized) == MOD.TOP_LEVEL_KEYS
    packet = normalized["packets"][0]
    assert set(packet) == MOD.PACKET_KEYS
    assert packet["decision_refs"] == ["d"]
    assert packet["allowed_side_effects"] == []
    assert packet["risk_tags"] == ["low"]
    assert packet["dependencies"] == []
    assert packet["artifacts"] == []
