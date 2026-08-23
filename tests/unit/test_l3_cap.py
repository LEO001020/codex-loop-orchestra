# ============================================================================
# test_l3_cap.py — Unit tests for the L3 per-packet call cap (P-04) and the
# passthrough config-key wiring (P-05) in harness/trigger_eval.py.
# Cases: cap=2 from escalation_ladder.yaml -> 3rd direct_l3 routing for the
#        same packet is FORCED to direct_l4 (human); counters are per-packet
#        and persisted to l3_counters.json; a custom ladder cap is honored;
#        F2 cold-start upgrades count against the cap too; passthrough
#        default comes from config/config.toml [escalation]
#        passthrough_enabled with the CLI flag taking precedence.
# ============================================================================
import json

from tests.conftest import PY, CONFIG

BASE = {"packet_id": "p1", "exit_codes": [0], "retry_count": 0,
        "run_level_budget": 6, "diff_lines": 10, "diff_budget": 400,
        "test_count_before": 5, "test_count_after": 6,
        "paths_touched": ["src/mod/a.py"], "command_history": ["pytest -q"],
        "observation_lengths": [100]}

# path_boundary_attempt is an intrinsic direct_l3 rule (not an F2 upgrade)
L3_SIGNAL = dict(BASE, path_boundary_attempts=1)


def evaluate(loop, tmp_path, signals, passthrough=None, ladder=None, tag=""):
    sig = tmp_path / ("signals%s.json" % tag)
    sig.write_text(json.dumps(signals))
    cmd = [PY, loop.harness("trigger_eval.py"), "--signals", sig,
           "--triggers", CONFIG / "triggers.yaml",
           "--log", loop.data / "escalation_log.jsonl"]
    if passthrough is not None:
        cmd += ["--passthrough-enabled", passthrough]
    if ladder is not None:
        cmd += ["--ladder", ladder]
    p = loop.run(cmd)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def counters(loop):
    f = loop.data / "l3_counters.json"
    return json.loads(f.read_text()) if f.exists() else {}


# ---- P-04: per-packet cap from the shipped ladder yaml (cap=2 -> L4) -------

def test_third_l3_routing_is_forced_to_l4(loop, tmp_path):
    ladder = CONFIG / "escalation_ladder.yaml"      # shipped cap: 2 -> L4
    out1 = evaluate(loop, tmp_path, L3_SIGNAL, "true", ladder, tag="1")
    out2 = evaluate(loop, tmp_path, L3_SIGNAL, "true", ladder, tag="2")
    out3 = evaluate(loop, tmp_path, L3_SIGNAL, "true", ladder, tag="3")
    assert out1["action"] == "direct_l3"
    assert out2["action"] == "direct_l3"
    assert out3["action"] == "direct_l4"            # over cap -> human gate
    assert any(r.startswith("L3_CAP_EXCEEDED") for r in out3["rules_hit"])
    # never a downgrade: direct_l4 is strictly more escalated than direct_l3
    assert out3["raw_action"] == "direct_l3"


def test_counter_state_is_persisted_per_packet(loop, tmp_path):
    ladder = CONFIG / "escalation_ladder.yaml"
    evaluate(loop, tmp_path, L3_SIGNAL, "true", ladder, tag="a")
    evaluate(loop, tmp_path, dict(L3_SIGNAL, packet_id="p2"), "true", ladder,
             tag="b")
    evaluate(loop, tmp_path, L3_SIGNAL, "true", ladder, tag="c")
    c = counters(loop)
    assert c["p1"] == 2 and c["p2"] == 1            # per-packet, on disk
    # p2 is untouched by p1's consumption of its own cap
    out = evaluate(loop, tmp_path, dict(L3_SIGNAL, packet_id="p2"), "true",
                   ladder, tag="d")
    assert out["action"] == "direct_l3"


def test_custom_ladder_cap_is_read_from_yaml(loop, tmp_path):
    ladder = tmp_path / "ladder.yaml"
    ladder.write_text(json.dumps(                    # yaml superset of json
        {"levels": {"L3": {"per_packet_call_cap": 1,
                           "on_cap_exceeded": "L4"}}}))
    out1 = evaluate(loop, tmp_path, L3_SIGNAL, "true", ladder, tag="1")
    out2 = evaluate(loop, tmp_path, L3_SIGNAL, "true", ladder, tag="2")
    assert out1["action"] == "direct_l3"
    assert out2["action"] == "direct_l4"            # cap=1 honored


def test_cold_start_upgrades_count_against_the_cap(loop, tmp_path):
    # F2 cold start: benign packets are upgraded to direct_l3; the ladder cap
    # still bounds Sol guidance per packet — the overflow goes to the human.
    ladder = CONFIG / "escalation_ladder.yaml"
    for i in range(2):
        out = evaluate(loop, tmp_path, BASE, "false", ladder, tag=str(i))
        assert out["action"] == "direct_l3" and out["raw_action"] == "pass"
    out = evaluate(loop, tmp_path, BASE, "false", ladder, tag="last")
    assert out["action"] == "direct_l4"


# ---- P-05: passthrough default read from config.toml (CLI wins) ------------

def test_passthrough_default_read_from_config_true(loop, tmp_path):
    loop.write_config(passthrough=True)
    out = evaluate(loop, tmp_path, BASE)             # no CLI flag given
    assert out["action"] == "pass"                   # config key took effect
    assert out["raw_action"] == "pass"


def test_passthrough_defaults_false_without_config(loop, tmp_path):
    out = evaluate(loop, tmp_path, BASE)             # no config.toml, no flag
    assert out["action"] == "direct_l3"              # F2 cold-start default
    assert out["raw_action"] == "pass"


def test_cli_flag_overrides_config_key(loop, tmp_path):
    loop.write_config(passthrough=False)
    out = evaluate(loop, tmp_path, BASE, passthrough="true")
    assert out["action"] == "pass"                   # CLI wins over config
