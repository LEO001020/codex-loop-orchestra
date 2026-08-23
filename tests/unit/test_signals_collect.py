# ============================================================================
# test_signals_collect.py — Unit tests for harness/signals_collect.py (H-05):
# the deterministic L0-output -> L1 signals assembler.
# Cases: full assembly of all fields from real-shaped L0 artifacts (normal),
#        empty events + no diff (boundary), missing diffvalidator output
#        degrades safely — conservative defaults, *_missing marker, exit 0
#        (failure injection).
# ============================================================================
import json

from tests.conftest import PY, HARNESS


def collect(loop, tmp_path, pid="p1", **inputs):
    pkt = tmp_path / "packet.json"
    pkt.write_text(json.dumps(
        {"packet_id": pid, "goal": "g", "authorized_paths": ["src/"],
         "acceptance": ["pytest -q"],
         "constraints": ["diff <= 400 lines", "min_test_count>=5"]}))
    cmd = [PY, HARNESS / "signals_collect.py", "--packet", pkt]
    for flag, val in inputs.items():
        cmd += ["--%s" % flag.replace("_", "-"), val]
    p = loop.run(cmd)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_full_assembly_from_l0_outputs(loop, tmp_path):
    dv = tmp_path / "dv.out"
    dv.write_text("PATH_BOUNDARY: unauthorized paths touched: hooks/x.sh\n"
                  "FAIL [PATH_BOUNDARY] packet=p1 files=2 +30/-4 hunks=3 tests=5->6\n")
    rp = tmp_path / "oracle.json.replay.json"
    rp.write_text(json.dumps({"packet_id": "p1", "test_count": 6,
                              "commands": [{"cmd": "pytest -q", "rc": 1},
                                           {"cmd": "pytest -q", "rc": 0}],
                              "commands_passed": False}))
    ev = tmp_path / "events.ndjson"
    ev.write_text(json.dumps({"ts": 1, "packet_id": "p1", "event": "duty_review",
                              "detail": {"why": "2_consecutive_same_class"}}) + "\n")
    led = tmp_path / "ledger.json"
    led.write_text(json.dumps({"packets": {"p1": {"state": "FAILED",
                                                  "attempts": 2}}}))
    diff = tmp_path / "c.diff"
    diff.write_text("diff --git a/hooks/x.sh b/hooks/x.sh\n+++ b/hooks/x.sh\n")
    sig = collect(loop, tmp_path, diffvalidator_out=dv, replay=rp,
                  events=ev, ledger=led, diff=diff)
    assert sig["packet_id"] == "p1"
    assert sig["exit_codes"] == [1, 0] and sig["exit_code_sequence"] == [1, 0]
    assert sig["retry_count"] == 2
    assert sig["diff_lines"] == 34 and sig["diff_line_count"] == 34
    assert sig["deleted_lines"] == 4
    assert sig["diff_budget"] == 400 and sig["min_test_count"] == 5
    assert sig["path_boundary_attempts"] == 1
    assert sig["path_violation_attempted"] is True
    assert sig["test_count_before"] == 5 and sig["test_count_after"] == 6
    assert sig["test_count_delta"] == 1
    assert sig["paths_touched"] == ["hooks/x.sh"]
    assert sig["high_risk_path_hit"] is True          # hooks/ is high-risk
    assert sig["consecutive_same_type_failures"] is True
    assert sig["loop_fingerprint"] == 2               # 'pytest -q' twice
    assert sig["observation_length"] == max(sig["observation_lengths"])


def test_boundary_empty_events_and_no_diff(loop, tmp_path):
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps({"test_count": 5,
                              "commands": [{"cmd": "true", "rc": 0}]}))
    dv = tmp_path / "dv.out"
    dv.write_text("PASS packet=p1 files=1 +3/-0 hunks=1 tests=5->5\n")
    ev = tmp_path / "events.ndjson"
    ev.write_text("")                                  # empty event stream
    sig = collect(loop, tmp_path, diffvalidator_out=dv, replay=rp, events=ev)
    assert sig["consecutive_same_type_failures"] is False
    assert sig["retry_class_no_match"] is False
    assert sig["paths_touched"] == []                  # no diff file given
    assert sig["high_risk_path_hit"] is False
    assert sig["retry_count"] == 0                     # no ledger -> default 0
    assert sig["test_count_delta"] == 0


def test_missing_diffvalidator_output_degrades_safely(loop, tmp_path):
    sig = collect(loop, tmp_path,
                  diffvalidator_out=str(tmp_path / "does_not_exist.out"))
    assert sig["diffvalidator_missing"] is True        # visible marker
    assert sig["replay_missing"] is True
    # conservative defaults: no fabricated green or boundary claims
    assert sig["diff_lines"] == 0
    assert sig["path_boundary_attempts"] == 0
    assert sig["exit_codes"] == []
    assert sig["test_count_before"] is None and sig["test_count_after"] is None
    assert sig["loop_fingerprint"] == 0
