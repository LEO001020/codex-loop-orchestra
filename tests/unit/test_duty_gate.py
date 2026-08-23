# ============================================================================
# test_duty_gate.py — Unit tests for harness/duty_gate.py (S6 whitelist fence)
# Cases: valid ruling enforced (rc 0), valid ruling record-only in F2
#        (rc 3, default), off-whitelist key smuggling (rc 1), illegal class,
#        free text instead of JSON, missing/pointer-less evidence, low or
#        illegal confidence, theta boundary (== theta passes), stdin mode.
# ============================================================================
import json
import subprocess

import pytest

from tests.conftest import PY, HARNESS

VALID = {"class": "retryable",
         "evidence": ["report.md:41", "line 12 of pytest output"],
         "confidence": 0.9,
         "progress_ledger_delta": {}}


def gate(doc, *args, stdin=False, tmp_path=None):
    payload = json.dumps(doc) if isinstance(doc, dict) else doc
    cmd = [PY, str(HARNESS / "duty_gate.py"), *args]
    if stdin:
        p = subprocess.run(cmd, input=payload, capture_output=True, text=True,
                           timeout=30)
    else:
        f = tmp_path / "ruling.json"
        f.write_text(payload)
        p = subprocess.run(cmd + ["--ruling", str(f)], capture_output=True,
                           text=True, timeout=30)
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    return p.returncode, out


def test_valid_ruling_enforced(tmp_path):
    rc, out = gate(VALID, "--enforce", "true", tmp_path=tmp_path)
    assert rc == 0 and out["gate"] == "VALID" and out["class"] == "retryable"


def test_f2_default_is_record_only(tmp_path):
    rc, out = gate(VALID, tmp_path=tmp_path)   # default --enforce false
    assert rc == 3
    assert out["gate"] == "RECORDED_NOT_ENFORCED"


def test_extra_key_is_smuggled_authority(tmp_path):
    doc = dict(VALID, release_authorization=True)
    rc, out = gate(doc, "--enforce", "true", tmp_path=tmp_path)
    assert rc == 1 and out["gate"] == "DEAD_LETTER"
    assert any("OFF_WHITELIST_KEYS" in r for r in out["reasons"])


def test_free_text_ruling_rejected(tmp_path):
    rc, out = gate("The packet looks retryable to me, please retry it.",
                   "--enforce", "true", tmp_path=tmp_path)
    assert rc == 1
    assert any("FREE_TEXT_REJECTED" in r for r in out["reasons"])


@pytest.mark.parametrize("mutation,expected_reason", [
    ({"class": "just_ship_it"}, "ILLEGAL_CLASS"),
    ({"evidence": []}, "EVIDENCE_MISSING"),
    ({"evidence": ["it failed somehow"]}, "EVIDENCE_NO_LINE_NUMBERS"),
    ({"confidence": 0.5}, "CONFIDENCE_BELOW_THETA"),
    ({"confidence": "high"}, "CONFIDENCE_ILLEGAL"),
    ({"confidence": 1.5}, "CONFIDENCE_ILLEGAL"),
], ids=["class", "no-evidence", "no-line-ptr", "low-conf", "str-conf", "over-1"])
def test_off_whitelist_rulings_dead_letter(tmp_path, mutation, expected_reason):
    rc, out = gate(dict(VALID, **mutation), "--enforce", "true", tmp_path=tmp_path)
    assert rc == 1
    assert any(expected_reason in r for r in out["reasons"]), out["reasons"]


def test_confidence_exactly_theta_passes(tmp_path):
    rc, _ = gate(dict(VALID, confidence=0.7), "--enforce", "true",
                 "--theta", "0.7", tmp_path=tmp_path)
    assert rc == 0                              # boundary: >= theta is valid


def test_stdin_mode(tmp_path):
    rc, out = gate(VALID, "--enforce", "true", stdin=True)
    assert rc == 0 and out["gate"] == "VALID"
