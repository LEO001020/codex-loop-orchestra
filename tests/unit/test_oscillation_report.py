# ============================================================================
# test_oscillation_report.py — Unit tests for metering/oscillation_report.py:
# the zero-token oscillation metric aggregator (GPT critique metric — does
# each intervention ADD information or merely re-interpret?).
# Cases: all signatures change -> info_gain_ratio 1.0, no oscillation
#        candidate (normal); identical signatures >= 2 + state revisits ->
#        oscillation candidate flagged (normal); missing/empty inputs
#        degrade to an all-zero report, exit 0 (failure injection).
# ============================================================================
import json

from tests.conftest import PY, PKG

METERING = PKG / "metering"


def run_report(loop, tmp_path, esc_rows, events_rows, ledger, e0=None):
    esc = tmp_path / "esc.jsonl"
    esc.write_text("".join(json.dumps(r) + "\n" for r in esc_rows))
    ev = tmp_path / "ev.ndjson"
    ev.write_text("".join(json.dumps(r) + "\n" for r in events_rows))
    led = tmp_path / "led.json"
    led.write_text(json.dumps(ledger))
    cmd = [PY, METERING / "oscillation_report.py", "--escalation-log", esc,
           "--events", ev, "--ledger", led]
    if e0 is not None:
        e0f = tmp_path / "e0.json"
        e0f.write_text(json.dumps(e0))
        cmd += ["--e0-summary", e0f]
    p = loop.run(cmd)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_all_signatures_change_is_pure_information_gain(loop, tmp_path):
    esc = [{"ts": 1, "packet_id": "p1", "rules_hit": ["diff_over_budget"], "action": "direct_l3"},
           {"ts": 3, "packet_id": "p1", "rules_hit": ["test_count_decreased"], "action": "direct_l3"}]
    events = [{"ts": 2, "packet_id": "p1", "event": "retry_dispatch",
               "detail": {"class": "flaky_network"}}]
    ledger = {"packets": {"p1": {"state": "MERGED", "history": [
        {"ts": 1, "to": "RUNNING", "via": "dispatched"},
        {"ts": 2, "to": "FAILED", "via": "acceptance_fail"},
        {"ts": 3, "to": "MERGED", "via": "merged"}]}}}
    rep = run_report(loop, tmp_path, esc, events, ledger)
    p1 = rep["per_packet"]["p1"]
    assert p1["interventions"] == 3
    assert p1["signature_change_rate"] == 1.0       # every step added info
    assert p1["revisit_pairs"] == {}                # no state revisits
    assert p1["oscillation_candidate"] is False
    assert rep["totals"]["info_gain_ratio"] == 1.0


def test_unchanged_signatures_and_revisits_flag_oscillation(loop, tmp_path):
    same = {"packet_id": "p2", "rules_hit": ["exit_persistent_failure"],
            "action": "direct_l3"}
    esc = [dict(same, ts=1), dict(same, ts=2), dict(same, ts=3)]
    revisit = [{"ts": t, "to": s, "via": v} for t, s, v in
               [(1, "RUNNING", "dispatched"), (2, "FAILED", "acceptance_fail"),
                (3, "DISPATCHABLE", "retry_dispatch"),
                (4, "FAILED", "acceptance_fail"),
                (5, "DISPATCHABLE", "retry_dispatch")]]
    ledger = {"packets": {"p2": {"state": "DISPATCHABLE", "history": revisit}}}
    e0 = {"per_bucket": {"T5": {"cost_equivalent": 12.5},
                         "T10": {"cost_equivalent": 40.0}}}
    rep = run_report(loop, tmp_path, esc, [], ledger, e0=e0)
    p2 = rep["per_packet"]["p2"]
    assert p2["signature_change_rate"] == 0.0       # pure re-interpretation
    assert p2["revisit_pairs"]["FAILED|acceptance_fail"] == 2
    assert p2["revisit_pairs"]["DISPATCHABLE|retry_dispatch"] == 2
    assert p2["oscillation_candidate"] is True
    assert rep["revisit_histogram"] == {"2": 2}
    # e0 join attributes the oscillation to Sol cost (T5/T6/T10 buckets)
    assert rep["e0_cost_join"] == {"T5": 12.5, "T6": 0.0, "T10": 40.0}


def test_empty_or_missing_inputs_degrade_safely(loop, tmp_path):
    rep = run_report(loop, tmp_path, [], [], {"packets": {}})
    assert rep["per_packet"] == {}
    assert rep["totals"] == {"interventions": 0, "info_gain": 0,
                             "info_gain_ratio": None}
    assert rep["revisit_histogram"] == {}
    # entirely nonexistent files: still exit 0, still a well-formed report
    p = loop.run([PY, METERING / "oscillation_report.py",
                  "--escalation-log", tmp_path / "no.jsonl",
                  "--events", tmp_path / "no.ndjson",
                  "--ledger", tmp_path / "no.json"])
    assert p.returncode == 0, p.stderr
    rep2 = json.loads(p.stdout)
    assert rep2["per_packet"] == {} and rep2["totals"]["interventions"] == 0
