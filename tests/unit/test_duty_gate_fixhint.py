# ============================================================================
# test_duty_gate_fixhint.py — Unit tests for the H-01 fix in
# harness/duty_gate.py: fix_hint joins ALLOWED_KEYS as a CONSTRAINED string —
# legal ONLY when class=fixable AND len<=200, otherwise the whole ruling is
# still rejected (fail-visible, reject-not-release direction preserved).
# Cases: fixable + fix_hint passes and is persisted for t12 re-dispatch
#        (normal), non-fixable carrying fix_hint rejected (failure),
#        fix_hint over 200 chars rejected (boundary).
# ============================================================================
import json

from tests.conftest import PY, HARNESS

FIXABLE = {"class": "fixable",
           "evidence": ["report.md:41", "pytest.log:12"],
           "confidence": 0.9,
           "progress_ledger_delta": {"packet_id": "p9", "attempts_seen": 2,
                                     "distinct_failure_signatures": 1,
                                     "liveness": "looping"},
           "fix_hint": "pin the tz to UTC in conftest before re-running"}


def gate_stdin(loop, doc, *args):
    import subprocess
    p = subprocess.run([PY, str(HARNESS / "duty_gate.py"), *args],
                       input=json.dumps(doc), capture_output=True, text=True,
                       timeout=30, env=loop.env())
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    return p.returncode, out


def test_fixable_with_fix_hint_passes_and_persists(loop):
    rc, out = gate_stdin(loop, FIXABLE, "--enforce", "true")
    assert rc == 0 and out["gate"] == "VALID" and out["class"] == "fixable"
    # t12 wiring: validated ruling persisted for dispatch.py's handle line
    ruling_file = loop.data / "duty_rulings" / "p9.json"
    assert ruling_file.exists()
    saved = json.loads(ruling_file.read_text())
    assert saved["fix_hint"] == FIXABLE["fix_hint"]
    assert saved["class"] == "fixable"


def test_non_fixable_carrying_fix_hint_is_rejected(loop):
    doc = dict(FIXABLE, **{"class": "retryable"})
    rc, out = gate_stdin(loop, doc, "--enforce", "true")
    assert rc == 1 and out["gate"] == "DEAD_LETTER"
    assert any(r.startswith("FIX_HINT_CLASS_ILLEGAL") for r in out["reasons"])
    # nothing persisted on rejection
    assert not (loop.data / "duty_rulings" / "p9.json").exists()


def test_fix_hint_over_200_chars_is_rejected(loop):
    doc = dict(FIXABLE, fix_hint="x" * 201)
    rc, out = gate_stdin(loop, doc, "--enforce", "true")
    assert rc == 1 and out["gate"] == "DEAD_LETTER"
    assert any(r.startswith("FIX_HINT_TOO_LONG") for r in out["reasons"])
    # exactly 200 chars is the legal boundary
    rc, out = gate_stdin(loop, dict(FIXABLE, fix_hint="x" * 200),
                         "--enforce", "true")
    assert rc == 0 and out["gate"] == "VALID"
