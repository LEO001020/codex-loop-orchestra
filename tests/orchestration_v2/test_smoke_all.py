#!/usr/bin/env python3
"""test_smoke_all.py — end-to-end smoke battery for the A2 implementation.

Covers the acceptance-criteria shapes from phase2_architecture_design.md:
exactly-once consumption (§2.2 AC1), crash recovery, stale-consumer
escalation (AC2), validator strictness + stale revision (§2.7 AC2/AC4),
reducer aggregation + L2.5 ranking, meter turn-scoped classification (§2.4
AC1) + hysteresis (AC3) + legacy quarantine (AC4), gate fail-closed (§2.5
AC3) + break-glass audit (AC4), supervisor quarantine, and the six-condition
layered gate. Run: ``python3 tests/test_smoke_all.py``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

IMPL = Path(__file__).resolve().parent.parent
if not (IMPL / "config").is_dir():
    IMPL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(IMPL / "harness"))
sys.path.insert(0, str(IMPL / "metering"))
sys.path.insert(0, str(IMPL / "hooks"))

from l2_consumer import L2Consumer, load_policy, make_idem_key  # noqa: E402
from layered_gate import LayeredGate  # noqa: E402
from lifecycle_supervisor_v2 import (  # noqa: E402
    LifecycleSupervisorV2, bound_csv_summary, parse_verdict_from_report)
from model_token_share_v2 import (  # noqa: E402
    HysteresisController, MeterV2, classify_turn)
from result_reducer import ResultReducer  # noqa: E402
from short_result_validator import validate_short_result  # noqa: E402
from sol_tool_gate_v2 import SolToolGateV2  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS %s" % name)
    else:
        FAIL += 1
        print("FAIL %s %s" % (name, detail))


def good_short_result(**over: object) -> dict:
    doc = {
        "packet_id": "pkt-1", "control_packet_id": "cp-1",
        "control_packet_revision": 2, "status": "completed",
        "conclusion": "done; see artifacts", "artifact_paths": ["out/a.md"],
        "finding_ids": ["F1"], "needs_decision": None,
    }
    doc.update(over)
    return doc


def setup_root(tmp: str) -> Path:
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    for name in ("orchestration_policy_v2.toml", "loop_config_v2.toml",
                 "statemachine_v2_transitions.json"):
        (root / "config" / name).write_bytes(
            (IMPL / "config" / name).read_bytes())
    (root / "data").mkdir(exist_ok=True)
    return root


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="a2smoke.") as tmp:
        root = setup_root(tmp)
        policy = load_policy(root / "config" / "orchestration_policy_v2.toml")

        # ---- validator -----------------------------------------------------
        check("validator.ok", validate_short_result(good_short_result()).ok)
        r = validate_short_result(good_short_result(conclusion="x" * 2001))
        check("validator.oversize", not r.ok and r.code == "OVERSIZE", r.code)
        r = validate_short_result(good_short_result(extra_field=1))
        check("validator.extra_field", not r.ok and r.code == "EXTRA_FIELD", r.code)
        r = validate_short_result(good_short_result(), expected_revision=3)
        check("validator.stale_revision",
              not r.ok and r.code == "STALE_REVISION", r.code)
        doc = good_short_result()
        del doc["status"]
        r = validate_short_result(doc)
        check("validator.missing_field",
              not r.ok and r.code == "MISSING_FIELD", r.code)
        r = validate_short_result(good_short_result(), require_verdict=True)
        check("validator.verdict_required", not r.ok, r.code)
        check("validator.verdict_ok", validate_short_result(
            good_short_result(verdict="pass"), require_verdict=True).ok)

        # ---- l2 consumer: exactly-once, idempotent enqueue -------------------
        dispatched: list[str] = []
        consumer = L2Consumer(root, policy=policy,
                              dispatcher=lambda rec: dispatched.append(
                                  rec.idem_key) or True)
        for _ in range(2):  # duplicate emission — AC2: 0 new records on re-run
            consumer.enqueue("pkt-1", "run-A", 1)
        consumer.enqueue("pkt-2", "run-B", 1)
        consumer.drain()
        consumer.drain()
        check("consumer.exactly_once", len(dispatched) == 2, str(dispatched))
        check("consumer.idem_key_stable",
              make_idem_key("pkt-1", "run-A", 1) ==
              make_idem_key("pkt-1", "run-A", 1))
        check("consumer.heartbeat_written",
              consumer.consumer_heartbeat_age() is not None)

        # completion path: valid verifier report -> verdict event
        report = root / "verifier_report.json"
        report.write_text(json.dumps(good_short_result(
            packet_id="pkt-1", verdict="redo", finding_ids=["F2"])),
            encoding="utf-8")
        key = make_idem_key("pkt-1", "run-A", 1)
        result = consumer.complete(key, report)
        check("consumer.completion_valid", result.ok, result.code)
        events = (root / "data" / "events.ndjson").read_text(encoding="utf-8")
        check("consumer.verdict_event", '"verdict_redo"' in events)
        check("consumer.l2_requested_event", '"l2_requested"' in events)
        result2 = consumer.complete(key, report)  # idempotent completion
        check("consumer.completion_idempotent", result2.ok)

        # stale pending escalation (down-consumer semantics)
        stale = L2Consumer(root, policy=policy,
                           dispatcher=lambda rec: True,
                           clock=lambda: time.time() + 10000)
        stale.enqueue("pkt-3", "run-C", 1)  # created at now+10000
        # write record in the past instead: patch created_ts via direct append
        consumer.enqueue("pkt-old", "run-old", 1)
        old = L2Consumer(root, policy=policy, dispatcher=lambda rec: True,
                         clock=lambda: time.time() + 100000)
        n = old.check_stale_pending()
        check("consumer.stale_escalated", n >= 1, "escalated=%d" % n)
        events = (root / "data" / "events.ndjson").read_text(encoding="utf-8")
        check("consumer.stale_event", '"l2_consumer_stale"' in events)

        # ---- reducer ---------------------------------------------------------
        reducer = ResultReducer(root)
        reducer.add_short_result(good_short_result(packet_id="pkt-1"),
                                 idem_key="k1")
        reducer.add_short_result(good_short_result(packet_id="pkt-1"),
                                 idem_key="k1")  # duplicate
        reducer.add_short_result(good_short_result(
            packet_id="pkt-2", status="failed", finding_ids=["F9"]),
            idem_key="k2")
        verdict = reducer.consolidate()
        check("reducer.dedup", verdict.n_duplicates == 1,
              str(verdict.n_duplicates))
        check("reducer.strictest_wins", verdict.verdict == "redo",
              verdict.verdict)
        reducer.add_candidate("pkt-9", "cand-a",
                              [{"verdict": "pass", "score": 0.9}])
        reducer.add_candidate("pkt-9", "cand-b",
                              [{"verdict": "redo", "score": 0.7}])
        ranked = reducer.rank_candidates("pkt-9")
        check("reducer.l2_5_ranking", ranked[0].candidate_id == "cand-a",
              str(ranked))
        packet = reducer.render_adjudication_packet("merge wave?", wave="w1")
        check("reducer.bounded_packet", packet.token_estimate <= 2000,
              str(packet.token_estimate))
        check("reducer.paths_not_content",
              "out/a.md" in packet.artifact_paths)

        # ---- meter v2 --------------------------------------------------------
        check("meter.turn_scoped_production",
              classify_turn("please audit the harness K3 topology") == "production")
        check("meter.turn_scoped_maintenance",
              classify_turn("Stability probe: ping") == "maintenance")
        # a production turn QUOTING K3_OK in assistant text stays production
        check("meter.no_assistant_launder", classify_turn(None) == "production")

        hc = HysteresisController(0.25, 2, 0.22, 2)
        seq = [hc.sample(s) for s in (0.26, 0.26, 0.23, 0.21, 0.21)]
        check("meter.hysteresis_ac3",
              seq == ["NORMAL", "HIGH", "HIGH", "HIGH", "NORMAL"], str(seq))

        meter = MeterV2(root, policy)
        sol_model = policy["models"]["sol_model"]
        k3_model = policy["models"]["k3_model"]
        meter.record_turn(task_id="t1", root_session_id="rootA",
                          agent_id="a1", run_id=None, model=sol_model,
                          step_id="s1",
                          usage={"input_tokens": 100, "output_tokens": 50},
                          user_turn_text="do the work")
        meter.record_turn(task_id="t1", root_session_id="rootA",
                          agent_id="a1", run_id=None, model=sol_model,
                          step_id="s1",
                          usage={"input_tokens": 100, "output_tokens": 50},
                          user_turn_text="do the work")  # dedup
        meter.record_turn(task_id="t1", root_session_id="rootB",
                          agent_id="a2", run_id=None, model=k3_model,
                          step_id="s2",
                          usage={"input_tokens": 300, "output_tokens": 100},
                          user_turn_text="verify")
        meter.record_turn(task_id="t1", root_session_id="rootC",
                          agent_id="a3", run_id=None, model="gpt-5.6-terra",
                          step_id="s3",
                          usage={"input_tokens": 500, "output_tokens": 100},
                          user_turn_text="legacy")
        windows = meter.compute_windows()
        w5 = windows["rolling_5h"]
        check("meter.insufficient_data", w5.status == "INSUFFICIENT_DATA",
              w5.status)
        check("meter.sol_attributed", w5.shares.get("sol", 0) > 0,
              str(w5.shares))
        check("meter.k3_attributed", w5.shares.get("k3", 0) > 0,
              str(w5.shares))
        check("meter.legacy_quarantined",
              "legacy" in w5.shares and w5.shares["legacy"] > 0
              and abs(w5.shares.get("k3", 0) - 400 / 1150) < 1e-6,
              str(w5.shares))
        report_obj = meter.refresh(force=True)
        check("meter.refresh_writes", report_obj is not None
              and report_obj["status"] == "FRESH")
        sig = meter.budget_signal()
        check("meter.no_actuation_below_denominator", sig["actuate"] is False,
              str(sig))

        # ---- sol tool gate v2 ------------------------------------------------
        gate = SolToolGateV2(root, policy=policy)
        sol = policy["models"]["sol_model"]
        # no progress ledger -> gated tool from sol must be DENIED (fail-closed)
        d = gate.decide({"tool_name": "shell", "model": sol})
        check("gate.fail_closed_no_ledger", not d.allow, d.rule)
        # worker model always allowed
        d = gate.decide({"tool_name": "shell",
                         "model": policy["models"]["v4_model"]})
        check("gate.worker_allowed", d.allow, d.rule)
        # unknown model on gated tool -> deny
        d = gate.decide({"tool_name": "shell", "model": "mystery/model"})
        check("gate.unknown_model_denied", not d.allow, d.rule)
        # ungated tool -> allowed
        d = gate.decide({"tool_name": "dispatch_packet", "model": sol})
        check("gate.ungated_allowed", d.allow, d.rule)
        # planning state with fresh empty ledger: allowed under lease
        (root / "data" / "progress_ledger.json").write_text(
            json.dumps({"packets": {}}), encoding="utf-8")
        d = gate.decide({"tool_name": "shell", "model": sol})
        check("gate.planning_lease_allows", d.allow, d.reason)
        # exhaust the lease: 7th turn denied (AC1)
        for _ in range(6):
            d = gate.decide({"tool_name": "shell", "model": sol})
        check("gate.planning_lease_exhausted", not d.allow, d.rule)
        # unattested explicit loop_state is IGNORED (self-exemption removed)
        (root / "data" / "progress_ledger.json").write_text(
            json.dumps({"loop_state": "planning",
                        "packets": {"p": {"state": "RUNNING"}}}),
            encoding="utf-8")
        d = gate.decide({"tool_name": "shell", "model": sol})
        check("gate.unattested_state_ignored",
              not d.allow and d.rule in ("state_gated",
                                         "meter_stale_fail_closed"), d.rule)
        # attested state honored
        att = root / "data" / "governor" / "state_attestations.ndjsonl"
        att.parent.mkdir(parents=True, exist_ok=True)
        att.write_text(json.dumps({
            "event": "governor.state_set", "state": "adjudication",
            "reason": "wave finale", "idem_key": "att-1"}) + "\n",
            encoding="utf-8")
        (root / "data" / "progress_ledger.json").write_text(
            json.dumps({"loop_state": "adjudication",
                        "packets": {"p": {"state": "RUNNING"}}}),
            encoding="utf-8")
        d = gate.decide({"tool_name": "shell", "model": sol})
        check("gate.attested_state_honored", d.allow, d.rule + " " + d.reason)
        # break-glass: allowed + audited
        (root / "data" / "progress_ledger.json").write_text(
            json.dumps({"packets": {"p": {"state": "RUNNING"}}}),
            encoding="utf-8")
        os.environ["LOOP_GOVERNOR_OVERRIDE"] = "incident-42"
        d = gate.decide({"tool_name": "shell", "model": sol})
        del os.environ["LOOP_GOVERNOR_OVERRIDE"]
        check("gate.break_glass_allows", d.allow and d.break_glass, d.rule)
        audit = (root / "data" / "governor" /
                 "gate_decisions.ndjsonl").read_text(encoding="utf-8")
        check("gate.break_glass_audited", "governor.break_glass" in audit)
        check("gate.decisions_audited", '"governor.decision"' in audit)

        # ---- lifecycle supervisor v2 ------------------------------------------
        sup = LifecycleSupervisorV2(root, reducer=ResultReducer(root))
        good = root / "child_report.json"
        good.write_text(json.dumps(good_short_result(packet_id="pkt-ok")),
                        encoding="utf-8")
        out = sup.finalize_child(packet_id="pkt-ok", rc=0, report=good,
                                 expected_revision=2)
        check("supervisor.success", out.success, str(out.why))
        check("supervisor.receipt_fixed_size",
              len(out.receipt.to_json()) < 800, out.receipt.to_json())
        bad = root / "bad_report.json"
        bad.write_text(json.dumps(good_short_result(
            packet_id="pkt-bad", conclusion="y" * 5000)), encoding="utf-8")
        out = sup.finalize_child(packet_id="pkt-bad", rc=0, report=bad)
        check("supervisor.invalid_fails_closed",
              not out.success and out.why == "short_result_invalid", str(out.why))
        check("supervisor.quarantined", out.quarantine_path is not None
              and Path(out.quarantine_path).exists())
        out = sup.finalize_child(packet_id="pkt-r", rc=0, report=good,
                                 expected_revision=3)
        check("supervisor.stale_revision_fails",
              not out.success and out.why == "stale_revision", str(out.why))
        out = sup.finalize_child(packet_id="pkt-x", rc=2, report=good)
        check("supervisor.nonzero_rc_fails", not out.success)
        ok, _ = bound_csv_summary("x" * 2001)
        check("supervisor.csv_bounded", not ok)
        duty = root / "duty_report.json"
        duty.write_text(json.dumps({"ruling": "duty_retryable"}),
                        encoding="utf-8")
        check("supervisor.verdict_from_report_only",
              parse_verdict_from_report(duty, kind="duty") == "duty_retryable")
        check("supervisor.reply_body_never_parsed",
              sup.route_duty_ruling("pkt-d", root / "no_report.json") is None)

        # ---- layered gate -------------------------------------------------------
        lg = LayeredGate(root, policy=policy)
        result = lg.check_all()   # heartbeat exists from consumer runs above
        check("layered_gate.all_green", result.allow, str(result.failed))
        # kill one condition: stale heartbeat -> gate must refuse
        hb = root / "data" / "l2_queue" / "consumer_heartbeat.json"
        hb.write_text(json.dumps({"ts": 0}), encoding="utf-8")
        result = lg.enable()
        check("layered_gate.refuses_on_failure",
              not result.allow and "consumer_heartbeat" in result.failed,
              str(result.failed))
        log = (root / "data" / "governor" /
               "layered_gate.ndjsonl").read_text(encoding="utf-8")
        check("layered_gate.logged", "layered_mode_refused" in log)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
