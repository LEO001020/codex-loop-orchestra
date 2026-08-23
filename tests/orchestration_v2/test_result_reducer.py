"""test_result_reducer.py — zero-model result reduction pipeline.

Covers: idem-key dedup, verdict aggregation (pass/redo/escalate ladder),
L2.5 candidate ranking, and the AdjudicationPacket size limit.
"""
from __future__ import annotations

import json

import pytest

from tests.orchestration_v2.conftest import good_short_result, make_root, read_events

from result_reducer import VERDICT_ORDER, ResultReducer


@pytest.fixture
def reducer(tmp_path):
    root = make_root(tmp_path)
    return ResultReducer(root)


# ---------------------------------------------------------------------------
# idem-key dedup
# ---------------------------------------------------------------------------
def test_duplicate_short_results_dropped(reducer):
    doc = good_short_result()
    assert reducer.add_short_result(doc) is True
    assert reducer.add_short_result(doc) is False, "same semantic identity"
    verdict = reducer.consolidate()
    assert verdict.n_inputs == 1 and verdict.n_duplicates == 1


def test_explicit_idem_key_wins(reducer):
    doc = good_short_result()
    assert reducer.add_short_result(doc, idem_key="k1") is True
    assert reducer.add_short_result(doc, idem_key="k2") is True
    assert reducer.consolidate().n_inputs == 2


def test_duplicate_completions_dropped(reducer):
    comp = {"idem_key": "k3:l2req:p1:AAAA", "verdict": "pass", "valid": True}
    assert reducer.add_completion(comp) is True
    assert reducer.add_completion(comp) is False


def test_completion_without_key_ignored(reducer):
    assert reducer.add_completion({"verdict": "pass"}) is False


def test_invalid_short_result_rejected_defensively(reducer):
    """A reducer must never trust its producers."""
    assert reducer.add_short_result(good_short_result(extra_field=1)) is False
    assert reducer.consolidate().n_inputs == 0


# ---------------------------------------------------------------------------
# verdict aggregation (strictest wins)
# ---------------------------------------------------------------------------
def test_verdict_order_matches_verdict_aggregate():
    assert VERDICT_ORDER == ("pass", "redo", "escalate_l2_5", "escalate_l3")


def test_all_pass_aggregates_pass(reducer):
    for i in range(3):
        reducer.add_short_result(good_short_result(packet_id="p%d" % i),
                                 idem_key="k%d" % i)
    verdict = reducer.consolidate()
    assert verdict.verdict == "pass" and verdict.event == "verdict_pass"


def test_single_redo_wins_over_passes(reducer):
    reducer.add_short_result(good_short_result(packet_id="p1"), idem_key="k1")
    reducer.add_short_result(
        good_short_result(packet_id="p2", status="failed"), idem_key="k2")
    assert reducer.consolidate().verdict == "redo"


def test_escalate_l3_wins_over_everything(reducer):
    reducer.add_short_result(good_short_result(packet_id="p1"), idem_key="k1")
    reducer.add_completion({"idem_key": "kc", "verdict": "escalate_l3",
                            "valid": True})
    reducer.add_short_result(
        good_short_result(packet_id="p2", status="failed"), idem_key="k2")
    verdict = reducer.consolidate()
    assert verdict.verdict == "escalate_l3"
    assert verdict.event == "verdict_escalate_l3"


def test_needs_decision_forces_escalation(reducer):
    reducer.add_short_result(good_short_result(needs_decision={
        "question": "which schema?", "decision_refs": [],
        "evidence_refs": []}), idem_key="k1")
    assert reducer.consolidate().verdict == "escalate_l3"


def test_cancelled_escalates_to_human_visibility(reducer):
    reducer.add_short_result(good_short_result(status="cancelled"),
                             idem_key="k1")
    assert reducer.consolidate().verdict == "escalate_l3"


def test_negative_verdict_without_findings_is_inconsistent(reducer):
    """verdict_check semantics: a negative verdict citing zero findings can
    never be silently trusted — it escalates."""
    reducer.add_short_result(
        good_short_result(verdict="redo", finding_ids=[]), idem_key="k1")
    verdict = reducer.consolidate()
    assert verdict.verdict == "escalate_l3"
    assert any("CLOSURE_VIOLATION" in i for i in verdict.inconsistencies)


def test_invalid_completion_escalates(reducer):
    reducer.add_completion({"idem_key": "kc", "verdict": None, "valid": False})
    verdict = reducer.consolidate()
    assert verdict.verdict == "escalate_l3"
    assert any("INVALID_COMPLETION" in i for i in verdict.inconsistencies)


def test_empty_reduction_can_never_pass(reducer):
    verdict = reducer.consolidate()
    assert verdict.verdict == "escalate_l3"
    assert any("NO_INPUTS" in i for i in verdict.inconsistencies)


def test_aggregate_pass_never_releases(reducer):
    """Power semantics: pass exempts from Sol review; MERGED/DONE still
    require the mechanical merge queue + human L4 gate."""
    reducer.add_short_result(good_short_result(), idem_key="k1")
    verdict = reducer.consolidate()
    assert verdict.event == "verdict_pass"  # t31 -> ACCEPTED, not MERGED


# ---------------------------------------------------------------------------
# L2.5 candidate ranking
# ---------------------------------------------------------------------------
def test_rank_least_strict_then_highest_score(reducer):
    reducer.add_candidate("p1", "cand-a",
                          [{"verdict": "pass", "score": 0.6}])
    reducer.add_candidate("p1", "cand-b",
                          [{"verdict": "pass", "score": 0.9}])
    reducer.add_candidate("p1", "cand-c",
                          [{"verdict": "redo", "score": 1.0}])
    ranking = reducer.rank_candidates("p1")
    assert [r.candidate_id for r in ranking] == ["cand-b", "cand-a", "cand-c"]
    assert ranking[0].mean_score == 0.9


def test_strictest_verdict_within_candidate(reducer):
    reducer.add_candidate("p1", "cand-a", [
        {"verdict": "pass", "score": 1.0},
        {"verdict": "escalate_l2_5", "score": 1.0}])
    ranking = reducer.rank_candidates("p1")
    assert ranking[0].verdict == "escalate_l2_5"


def test_unjudged_candidate_can_never_pass(reducer):
    reducer.add_candidate("p1", "cand-a", [])
    ranking = reducer.rank_candidates("p1")
    assert ranking[0].verdict == "escalate_l3" and ranking[0].mean_score == 0.0


def test_at_most_three_candidates(reducer):
    for i in range(3):
        reducer.add_candidate("p1", "cand-%d" % i,
                              [{"verdict": "pass", "score": 1.0}])
    with pytest.raises(ValueError, match="at most 3"):
        reducer.add_candidate("p1", "cand-3", [])


def test_best_candidate_emits_t36_event(tmp_path):
    root = make_root(tmp_path)
    reducer = ResultReducer(root)
    reducer.add_candidate("p1", "winner", [{"verdict": "pass", "score": 0.8}])
    verdict = reducer.consolidate()
    assert verdict.best_candidate.candidate_id == "winner"
    events = [e for e in read_events(root) if e["event"] == "best_candidate"]
    assert events and events[0]["detail"]["candidate_id"] == "winner", \
        "the ranked winner re-enters verification (t36)"


# ---------------------------------------------------------------------------
# AdjudicationPacket size limit
# ---------------------------------------------------------------------------
def test_adjudication_packet_bounded(tmp_path):
    root = make_root(tmp_path)
    reducer = ResultReducer(root, max_adjudication_tokens=600)
    for i in range(40):
        reducer.add_short_result(good_short_result(
            packet_id="packet-%03d" % i,
            artifact_paths=["reports/very/long/artifact/path-%03d.md" % i],
            finding_ids=["FINDING-%03d" % i]), idem_key="k%d" % i)
    unbounded = ResultReducer(root, max_adjudication_tokens=100_000)
    for i in range(40):
        unbounded.add_short_result(good_short_result(
            packet_id="packet-%03d" % i,
            artifact_paths=["reports/very/long/artifact/path-%03d.md" % i],
            finding_ids=["FINDING-%03d" % i]), idem_key="k%d" % i)
    full = unbounded.render_adjudication_packet("merge order for wave 3?")
    packet = reducer.render_adjudication_packet("merge order for wave 3?")
    assert full.token_estimate > 600, "the unbounded packet overflows"
    assert packet.token_estimate <= 600, \
        "the rendered packet must fit its token budget"
    assert packet.decision_question == "merge order for wave 3?"
    truncated = [a for a in packet.artifact_paths if "more" in a]
    assert truncated or len(packet.artifact_paths) < 40, \
        "truncation is explicit (…(+N more)), never a silent overflow"


def test_adjudication_packet_carries_paths_not_content(tmp_path):
    root = make_root(tmp_path)
    reducer = ResultReducer(root)
    reducer.add_short_result(good_short_result(), idem_key="k1")
    packet = reducer.render_adjudication_packet("release?")
    body = packet.to_dict()
    assert body["artifact_paths"] == ["out/a.md"]
    assert "conclusion" not in json.dumps(body), \
        "content never rides the packet — artifact paths only"


def test_adjudication_packet_written_to_disk(tmp_path):
    root = make_root(tmp_path)
    reducer = ResultReducer(root)
    reducer.add_short_result(good_short_result(), idem_key="k1")
    reducer.render_adjudication_packet("q?", wave="w7")
    saved = json.loads(
        (root / "data" / "adjudication" / "adjudication_w7.json").read_text())
    assert saved["schema"] == "codex-loop-adjudication-packet/v2"
    assert saved["consolidated"]["verdict"] == "pass"


def test_statuses_and_findings_deduplicated(tmp_path):
    root = make_root(tmp_path)
    reducer = ResultReducer(root)
    reducer.add_short_result(good_short_result(finding_ids=["F1", "F2"]),
                             idem_key="k1")
    reducer.add_short_result(good_short_result(packet_id="pkt-2",
                                               finding_ids=["F2", "F3"]),
                             idem_key="k2")
    packet = reducer.render_adjudication_packet("q?")
    assert list(packet.finding_ids) == ["F1", "F2", "F3"]
    assert packet.statuses == {"pkt-1": "completed", "pkt-2": "completed"}
