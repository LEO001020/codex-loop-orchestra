# ============================================================================
# test_sanitize.py — Unit tests for harness/sanitize.py (review independence)
# Cases: forbidden keys stripped at top level (normal), nested dicts + lists
#        stripped (recursion), case-insensitive key match (boundary), clean
#        payload untouched (normal), stdin/stdout mode, --in/--out mode.
# ============================================================================
import json
import subprocess

import pytest

from conftest import PY, HARNESS

FORBIDDEN = ["generation_narrative", "generation_process", "author_self_assessment",
             "self_assessment", "prior_verdict", "builder_claims", "self_report",
             "completion_summary", "confidence_claim", "tests_pass_claim"]


def sanitize_stdin(doc):
    p = subprocess.run([PY, str(HARNESS / "sanitize.py")], input=json.dumps(doc),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_all_forbidden_keys_stripped_top_level():
    doc = {"diff": "ok", "packet_id": "p1"}
    doc.update({k: "I did great, trust me" for k in FORBIDDEN})
    out = sanitize_stdin(doc)
    assert out == {"diff": "ok", "packet_id": "p1"}


def test_nested_and_list_payloads_scrubbed():
    doc = {"candidates": [
        {"id": "c1", "self_report": "all tests pass", "diff": "d1",
         "meta": {"prior_verdict": "APPROVED", "lines": 3}},
        {"id": "c2", "diff": "d2"},
    ]}
    out = sanitize_stdin(doc)
    assert out == {"candidates": [{"id": "c1", "diff": "d1", "meta": {"lines": 3}},
                                  {"id": "c2", "diff": "d2"}]}


@pytest.mark.parametrize("key", ["Self_Report", "TESTS_PASS_CLAIM", "Prior_Verdict"])
def test_case_insensitive_key_match(key):
    out = sanitize_stdin({key: "sneaky", "keep": 1})
    assert out == {"keep": 1}


def test_clean_payload_passes_through_unchanged():
    doc = {"packet_id": "p1", "diff": "+x", "test_output": "3 passed",
           "files": ["a.py"], "counts": {"added": 1, "removed": 0}}
    assert sanitize_stdin(doc) == doc


def test_file_mode_in_out(tmp_path):
    src = tmp_path / "in.json"
    dst = tmp_path / "out.json"
    src.write_text(json.dumps({"builder_claims": "done!", "diff": "+1"}))
    p = subprocess.run([PY, str(HARNESS / "sanitize.py"), "--in", str(src),
                        "--out", str(dst)], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert json.loads(dst.read_text()) == {"diff": "+1"}
