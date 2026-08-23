# ============================================================================
# test_verdict_check.py — Unit tests for harness/verdict_check.py
# Cases: valid APPROVED (normal), valid CHANGES_REQUESTED with findings,
#        blocking finding + APPROVED contradiction (failure), negative verdict
#        with zero findings (failure), finding missing pointer (failure),
#        unknown verdict / severity (failure), malformed file (rc 2),
#        full reviewer.toml enum accepted (APPROVED_WITH_NOTES pass-class,
#        CHANGES_REQUIRED blocking-class — P-03 unification).
# ============================================================================
import json

import pytest

from tests.conftest import PY


def check(loop, tmp_path, doc):
    f = tmp_path / "verdict.json"
    f.write_text(json.dumps(doc) if isinstance(doc, dict) else doc)
    return loop.run([PY, loop.harness("verdict_check.py"), "--verdict", f])


def finding(severity="minor", pointer="src/a.py:10", note="nit"):
    return {"severity": severity, "pointer": pointer, "note": note}


def test_approved_with_no_blockers_passes(loop, tmp_path):
    p = check(loop, tmp_path, {"verdict": "APPROVED",
                               "findings": [finding("minor"), finding("info")]})
    assert p.returncode == 0, p.stderr


def test_changes_requested_with_findings_passes(loop, tmp_path):
    p = check(loop, tmp_path, {"verdict": "CHANGES_REQUESTED",
                               "findings": [finding("major")]})
    assert p.returncode == 0, p.stderr


def test_blocking_finding_contradicts_approved(loop, tmp_path):
    p = check(loop, tmp_path, {"verdict": "APPROVED",
                               "findings": [finding("blocking")]})
    assert p.returncode == 1
    assert "blocking" in (p.stderr + p.stdout).lower()


def test_negative_verdict_with_zero_findings_rejected(loop, tmp_path):
    # A rejection without evidence is unusable for the retry loop.
    p = check(loop, tmp_path, {"verdict": "REJECTED", "findings": []})
    assert p.returncode == 1


def test_finding_without_pointer_rejected(loop, tmp_path):
    p = check(loop, tmp_path, {"verdict": "CHANGES_REQUESTED",
                               "findings": [{"severity": "major", "note": "vague"}]})
    assert p.returncode == 1


@pytest.mark.parametrize("doc", [
    {"verdict": "LGTM", "findings": []},                          # unknown verdict
    {"verdict": "APPROVED", "findings": [finding("catastrophic")]},  # unknown severity
])
def test_unknown_enums_rejected(loop, tmp_path, doc):
    p = check(loop, tmp_path, doc)
    assert p.returncode == 1


def test_malformed_json_is_usage_error(loop, tmp_path):
    p = check(loop, tmp_path, "{not json")
    assert p.returncode == 2


# ---- P-03: reviewer.toml enum {APPROVED, APPROVED_WITH_NOTES,
# CHANGES_REQUIRED, REJECTED} must be legal and closure-consistent ----------

def test_approved_with_notes_with_findings_passes(loop, tmp_path):
    p = check(loop, tmp_path, {"verdict": "APPROVED_WITH_NOTES",
                               "findings": [finding("minor")]})
    assert p.returncode == 0, p.stderr


def test_approved_with_notes_without_findings_rejected(loop, tmp_path):
    # "with notes" and zero findings is inconsistent — the notes must be cited
    p = check(loop, tmp_path, {"verdict": "APPROVED_WITH_NOTES",
                               "findings": []})
    assert p.returncode == 1


def test_approved_with_notes_cannot_coexist_with_blocking(loop, tmp_path):
    # pass-class verdict + blocking finding = closure violation (fail-visible)
    p = check(loop, tmp_path, {"verdict": "APPROVED_WITH_NOTES",
                               "findings": [finding("blocking")]})
    assert p.returncode == 1
    assert "blocking" in (p.stderr + p.stdout).lower()


def test_changes_required_with_findings_passes(loop, tmp_path):
    p = check(loop, tmp_path, {"verdict": "CHANGES_REQUIRED",
                               "findings": [finding("major")]})
    assert p.returncode == 0, p.stderr


def test_changes_required_without_findings_rejected(loop, tmp_path):
    # blocking-class verdict must cite evidence, same rule as REJECTED
    p = check(loop, tmp_path, {"verdict": "CHANGES_REQUIRED", "findings": []})
    assert p.returncode == 1
