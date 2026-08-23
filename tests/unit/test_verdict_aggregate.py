# ============================================================================
# test_verdict_aggregate.py — Unit tests for harness/verdict_aggregate.py
# Cases: strictest-wins aggregation (normal), all-pass ranking by mean score,
#        unjudged candidate escalates (boundary), emit-order is a permutation,
#        pass never phrased as a release (S5), usage errors (rc 2).
# ============================================================================
import json
import subprocess

from tests.conftest import PY, HARNESS


def run_agg(*args):
    return subprocess.run([PY, str(HARNESS / "verdict_aggregate.py"), *args],
                          capture_output=True, text=True, timeout=30)


def aggregate(tmp_path, candidates):
    f = tmp_path / "verdicts.json"
    f.write_text(json.dumps({"candidates": candidates}))
    p = run_agg("--verdicts", str(f))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def cand(cid, *verdicts, scores=None):
    scores = scores or [0.8] * len(verdicts)
    return {"candidate_id": cid, "diff_path": "diffs/%s.diff" % cid,
            "verdicts": [{"verifier": "v%d" % i, "verdict": v, "score": s}
                         for i, (v, s) in enumerate(zip(verdicts, scores))]}


def test_strictest_verdict_wins_across_candidates(tmp_path):
    out = aggregate(tmp_path, [cand("c1", "pass", "pass"),
                               cand("c2", "pass", "escalate_l3"),
                               cand("c3", "redo")])
    assert out["aggregate_verdict"] == "escalate_l3"
    # best candidate is still the least-strict one
    assert out["best_candidate"]["candidate_id"] == "c1"


def test_all_pass_ranks_by_mean_score(tmp_path):
    out = aggregate(tmp_path, [cand("lo", "pass", "pass", scores=[0.5, 0.6]),
                               cand("hi", "pass", "pass", scores=[0.9, 1.0])])
    assert out["aggregate_verdict"] == "pass"
    assert out["best_candidate"]["candidate_id"] == "hi"
    assert [r["candidate_id"] for r in out["ranking"]] == ["hi", "lo"]
    assert out["ranking"][0]["mean_score"] == 0.95


def test_unjudged_candidate_cannot_pass(tmp_path):
    out = aggregate(tmp_path, [cand("judged", "pass"),
                               {"candidate_id": "ghost", "verdicts": []}])
    assert out["aggregate_verdict"] == "escalate_l3"
    ghost = [r for r in out["ranking"] if r["candidate_id"] == "ghost"][0]
    assert ghost["verdict"] == "escalate_l3" and ghost["mean_score"] == 0


def test_pass_is_never_a_release(tmp_path):
    out = aggregate(tmp_path, [cand("c1", "pass")])
    assert out["aggregate_verdict"] == "pass"
    assert "never a release" in out["note"]    # S5 hardcoded semantics


def test_emit_order_is_a_permutation():
    p = run_agg("--emit-order", "3")
    assert p.returncode == 0
    order = json.loads(p.stdout)["presentation_order"]
    assert sorted(order) == [0, 1, 2]


def test_missing_args_and_bad_file_are_usage_errors(tmp_path):
    assert run_agg().returncode == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert run_agg("--verdicts", str(bad)).returncode == 2
