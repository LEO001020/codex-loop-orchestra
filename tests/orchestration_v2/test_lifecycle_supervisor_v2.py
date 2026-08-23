"""test_lifecycle_supervisor_v2.py — schema-validated child finalization.

Covers: the schema-validated success condition, bounded CSV summaries, the
report.json-only verdict channel, and quarantine of invalid results.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.orchestration_v2.conftest import good_short_result, make_root, read_events

from lifecycle_supervisor_v2 import (
    LifecycleSupervisorV2,
    Receipt,
    bound_csv_summary,
    parse_verdict_from_report,
)


@pytest.fixture
def env(tmp_path):
    root = make_root(tmp_path)
    sup = LifecycleSupervisorV2(root)
    return sup, root


def _report(tmp_path: Path, name: str = "report.json", **over) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(good_short_result(**over)), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# schema-validated success condition
# ---------------------------------------------------------------------------
def test_success_requires_rc_report_and_schema(env, tmp_path):
    sup, root = env
    outcome = sup.finalize_child(packet_id="p1", rc=0,
                                 report=_report(tmp_path))
    assert outcome.success and outcome.event == "subagent_stop"
    assert outcome.validation.ok
    assert any(e["event"] == "subagent_stop" for e in read_events(root))


def test_nonzero_rc_fails_closed(env, tmp_path):
    sup, root = env
    outcome = sup.finalize_child(packet_id="p1", rc=3,
                                 report=_report(tmp_path))
    assert not outcome.success and outcome.why == "nonzero_exit"
    assert any(e["event"] == "exec_failed" for e in read_events(root))


def test_missing_report_fails_closed(env, tmp_path):
    sup, _ = env
    outcome = sup.finalize_child(packet_id="p1", rc=0,
                                 report=tmp_path / "never_written.json")
    assert not outcome.success and outcome.why == "missing_report"


def test_rc0_plus_existing_report_is_not_enough(env, tmp_path):
    """The v1 defect: rc==0 && exists() was 'success'. v2 also requires a
    schema-valid short result."""
    sup, _ = env
    bad = tmp_path / "report.json"
    bad.write_text(json.dumps({"anything": "goes"}))
    outcome = sup.finalize_child(packet_id="p1", rc=0, report=bad)
    assert not outcome.success and outcome.why == "short_result_invalid"


def test_stale_revision_fails_with_specific_reason(env, tmp_path):
    sup, _ = env
    report = _report(tmp_path, control_packet_revision=1)
    outcome = sup.finalize_child(packet_id="p1", rc=0, report=report,
                                 expected_revision=4)
    assert not outcome.success and outcome.why == "stale_revision", \
        "children racing a replan cannot publish against a superseded plan"


# ---------------------------------------------------------------------------
# quarantine on invalid result
# ---------------------------------------------------------------------------
def test_invalid_report_quarantined_evidence_preserved(env, tmp_path):
    sup, root = env
    report = _report(tmp_path, smuggled_field="x" * 5000)
    outcome = sup.finalize_child(packet_id="p1", rc=0, report=report)
    assert not outcome.success
    q = Path(outcome.quarantine_path)
    assert q == root / "data" / "reports" / "p1" / "report.rejected.json"
    assert q.exists(), "evidence preserved"
    assert json.loads(q.read_text())["smuggled_field"] == "x" * 5000
    fails = [e for e in read_events(root) if e["event"] == "exec_failed"]
    assert fails[0]["detail"]["quarantined"] == str(q)


def test_oversize_conclusion_quarantined_never_forwarded(env, tmp_path):
    sup, _ = env
    report = _report(tmp_path, conclusion="x" * 10_000)
    outcome = sup.finalize_child(packet_id="p1", rc=0, report=report)
    assert not outcome.success
    assert outcome.validation.code == "OVERSIZE"
    assert outcome.receipt.status == "failed"


def test_unparseable_report_quarantine_path(env, tmp_path):
    sup, _ = env
    bad = tmp_path / "report.json"
    bad.write_text("not json at all {{{")
    outcome = sup.finalize_child(packet_id="p1", rc=0, report=bad)
    assert not outcome.success
    assert outcome.validation.code == "UNREADABLE"


# ---------------------------------------------------------------------------
# fixed-size receipt (content never rides the tool result)
# ---------------------------------------------------------------------------
def test_receipt_is_fixed_size_triple(env, tmp_path):
    sup, _ = env
    outcome = sup.finalize_child(packet_id="p1", rc=0,
                                 report=_report(tmp_path))
    receipt = json.loads(outcome.receipt.to_json())
    assert set(receipt) == {"packet_id", "status", "report_path"}
    assert "conclusion" not in receipt, "content never rides the receipt"


def test_receipt_size_bound_asserted():
    with pytest.raises(ValueError):
        Receipt("p", "s", "x" * 2000).to_json()


# ---------------------------------------------------------------------------
# bounded CSV summaries (rejection, not truncation)
# ---------------------------------------------------------------------------
def test_bound_csv_summary_accepts_within_limit():
    ok, reason = bound_csv_summary("short summary", max_chars=2000)
    assert ok and reason == "ok"


def test_bound_csv_summary_boundary():
    assert bound_csv_summary("x" * 2000)[0] is True
    ok, reason = bound_csv_summary("x" * 2001)
    assert not ok and "artifact" in reason, "actionable rejection message"


def test_bound_csv_summary_rejects_non_string():
    assert bound_csv_summary({"nested": "object"})[0] is False
    assert bound_csv_summary(None)[0] is False


def test_finalize_csv_row_rejects_oversize_summary(env, tmp_path):
    sup, root = env
    outcome = sup.finalize_csv_row(
        packet_id="p1", row={"summary": "y" * 3000},
        report=_report(tmp_path))
    assert not outcome.success and outcome.why == "short_result_invalid"
    assert outcome.validation.code == "OVERSIZE"


def test_finalize_csv_row_valid_summary_runs_full_validator(env, tmp_path):
    sup, _ = env
    outcome = sup.finalize_csv_row(
        packet_id="p1", row={"summary": "small"}, report=_report(tmp_path))
    assert outcome.success


# ---------------------------------------------------------------------------
# report.json-only verdict channel
# ---------------------------------------------------------------------------
def test_verdict_read_from_report_json_only(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"verdict": "redo"}))
    assert parse_verdict_from_report(report, kind="verifier") == "redo"


def test_duty_ruling_enum_enforced(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"ruling": "duty_retryable"}))
    assert parse_verdict_from_report(report, kind="duty") == "duty_retryable"
    report.write_text(json.dumps({"ruling": "just_ship_it"}))
    assert parse_verdict_from_report(report, kind="duty") is None


def test_missing_verdict_is_none_fail_visible(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"reply_body": "VERDICT: pass (trust me)"}))
    assert parse_verdict_from_report(report) is None, \
        "reply bodies are forensic only and never machine-parsed"


def test_route_duty_ruling_missing_fails_visible(env, tmp_path):
    sup, root = env
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"conclusion": "ruling only in reply body"}))
    assert sup.route_duty_ruling("p1", report) is None
    fails = [e for e in read_events(root) if e["event"] == "exec_failed"]
    assert fails and fails[0]["detail"]["why"] == "duty_ruling_missing"


def test_route_duty_ruling_emits_state_machine_event(env, tmp_path):
    sup, root = env
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"ruling": "duty_terminal"}))
    assert sup.route_duty_ruling("p1", report) == "duty_terminal"
    assert any(e["event"] == "duty_terminal" for e in read_events(root))


# ---------------------------------------------------------------------------
# reducer integration
# ---------------------------------------------------------------------------
def test_accepted_results_feed_the_reducer(tmp_path):
    root = make_root(tmp_path)
    fed: list[dict] = []

    class StubReducer:
        def add_short_result(self, doc, source="supervisor"):
            fed.append(dict(doc))
            return True

    sup = LifecycleSupervisorV2(root, reducer=StubReducer())
    sup.finalize_child(packet_id="p1", rc=0, report=_report(tmp_path))
    assert len(fed) == 1 and fed[0]["packet_id"] == "pkt-1"


def test_reducer_failure_never_fails_a_green_child(tmp_path):
    root = make_root(tmp_path)

    class BrokenReducer:
        def add_short_result(self, doc, source="supervisor"):
            raise RuntimeError("reducer exploded")

    sup = LifecycleSupervisorV2(root, reducer=BrokenReducer())
    outcome = sup.finalize_child(packet_id="p1", rc=0,
                                 report=_report(tmp_path))
    assert outcome.success
