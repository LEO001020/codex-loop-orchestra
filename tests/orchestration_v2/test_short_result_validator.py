"""test_short_result_validator.py — strict ShortResult contract enforcement.

Covers: max-length rejection, extra-field rejection, missing-field rejection,
stale-revision rejection, acceptance of valid documents, and exact boundary
conditions (conclusion at 2000 chars, findings at 8).
"""
from __future__ import annotations

import pytest

from tests.orchestration_v2.conftest import good_short_result

from short_result_validator import (
    CHARS_PER_TOKEN,
    DEFAULT_MAX_FINDINGS,
    DEFAULT_MAX_TOKENS,
    L2_VERDICTS,
    ShortResultValidator,
    estimate_tokens,
    validate_short_result,
)

MAX_CHARS = DEFAULT_MAX_TOKENS * CHARS_PER_TOKEN  # 2000


# ---------------------------------------------------------------------------
# acceptance
# ---------------------------------------------------------------------------
def test_valid_short_result_accepted():
    result = validate_short_result(good_short_result())
    assert result.ok and result.code == "OK" and result.errors == ()


def test_valid_with_optional_fields_accepted():
    doc = good_short_result(verdict="pass", idem_key="k3:l2req:x:AAAA", score=0.9)
    assert validate_short_result(doc).ok


def test_valid_needs_decision_accepted():
    doc = good_short_result(needs_decision={
        "question": "merge order?", "decision_refs": [], "evidence_refs": []})
    assert validate_short_result(doc).ok


# ---------------------------------------------------------------------------
# oversize rejection + boundaries
# ---------------------------------------------------------------------------
def test_conclusion_exactly_at_limit_accepted():
    doc = good_short_result(conclusion="x" * MAX_CHARS)
    assert validate_short_result(doc).ok, "exactly 2000 chars is legal"


def test_conclusion_one_over_limit_rejected():
    doc = good_short_result(conclusion="x" * (MAX_CHARS + 1))
    result = validate_short_result(doc)
    assert not result.ok and result.code == "OVERSIZE"
    assert result.field == "conclusion"
    assert "artifact" in result.reason, "rejection message is actionable"


def test_findings_exactly_at_cap_accepted():
    doc = good_short_result(finding_ids=["F%d" % i
                                         for i in range(DEFAULT_MAX_FINDINGS)])
    assert validate_short_result(doc).ok, "exactly 8 findings is legal"


def test_findings_one_over_cap_rejected():
    doc = good_short_result(finding_ids=["F%d" % i
                                         for i in range(DEFAULT_MAX_FINDINGS + 1)])
    result = validate_short_result(doc)
    assert not result.ok and result.code == "TOO_MANY_FINDINGS"


def test_custom_token_limit_enforced():
    v = ShortResultValidator(max_tokens=10)  # 40 chars
    assert v.validate(good_short_result(conclusion="x" * 40)).ok
    assert not v.validate(good_short_result(conclusion="x" * 41)).ok


def test_estimate_tokens_ceiling_division():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


# ---------------------------------------------------------------------------
# extra fields (additionalProperties: false)
# ---------------------------------------------------------------------------
def test_extra_field_rejected():
    result = validate_short_result(good_short_result(smuggled="payload"))
    assert not result.ok and result.code == "EXTRA_FIELD"
    assert "EXTRA_FIELD:smuggled" in result.errors


def test_multiple_extra_fields_all_reported():
    result = validate_short_result(good_short_result(a=1, b=2))
    assert not result.ok
    assert "EXTRA_FIELD:a" in result.errors and "EXTRA_FIELD:b" in result.errors


def test_needs_decision_extra_field_rejected():
    doc = good_short_result(needs_decision={
        "question": "q", "decision_refs": [], "evidence_refs": [],
        "raw_transcript": "..."})
    result = validate_short_result(doc)
    assert not result.ok and result.code == "BAD_NEEDS_DECISION"


# ---------------------------------------------------------------------------
# missing required fields
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("field", ["packet_id", "control_packet_id",
                                   "control_packet_revision", "status",
                                   "conclusion", "artifact_paths",
                                   "finding_ids", "needs_decision"])
def test_each_missing_required_field_rejected(field):
    doc = good_short_result()
    del doc[field]
    result = validate_short_result(doc)
    assert not result.ok and result.code == "MISSING_FIELD"
    assert any(field in e for e in result.errors)


def test_missing_verdict_rejected_when_required():
    result = validate_short_result(good_short_result(), require_verdict=True)
    assert not result.ok and "verdict" in result.reason
    assert validate_short_result(good_short_result(verdict="redo"),
                                 require_verdict=True).ok


# ---------------------------------------------------------------------------
# revision guard
# ---------------------------------------------------------------------------
def test_wrong_revision_rejected():
    result = validate_short_result(good_short_result(control_packet_revision=1),
                                   expected_revision=3)
    assert not result.ok and result.code == "STALE_REVISION"
    assert "superseded" in result.reason, "message tells the child what to do"


def test_matching_revision_accepted():
    assert validate_short_result(good_short_result(control_packet_revision=3),
                                 expected_revision=3).ok


def test_no_expected_revision_skips_check():
    assert validate_short_result(good_short_result(control_packet_revision=7)).ok


def test_bool_revision_rejected():
    result = validate_short_result(good_short_result(control_packet_revision=True))
    assert not result.ok and result.code == "TYPE_ERROR"


def test_revision_below_one_rejected():
    result = validate_short_result(good_short_result(control_packet_revision=0))
    assert not result.ok and result.code == "TYPE_ERROR"


# ---------------------------------------------------------------------------
# type / enum / shape negatives
# ---------------------------------------------------------------------------
def test_non_object_rejected():
    for doc in (None, [], "text", 42):
        result = validate_short_result(doc)
        assert not result.ok and result.code == "NOT_OBJECT"


def test_bad_status_rejected():
    result = validate_short_result(good_short_result(status="finished"))
    assert not result.ok and result.code == "BAD_STATUS"


def test_bad_verdict_rejected():
    result = validate_short_result(good_short_result(verdict="release"))
    assert not result.ok and result.code == "BAD_VERDICT"
    assert "release" not in L2_VERDICTS, \
        "L2 can never release (power semantics)"


def test_empty_conclusion_rejected():
    result = validate_short_result(good_short_result(conclusion="   "))
    assert not result.ok and result.code == "EMPTY_FIELD"


def test_non_string_finding_rejected():
    result = validate_short_result(good_short_result(finding_ids=["F1", 2]))
    assert not result.ok and any("finding_ids[1]" in e for e in result.errors)


def test_all_violations_reported_not_just_first():
    doc = good_short_result(conclusion="x" * (MAX_CHARS + 1), status="nope",
                            extra=1)
    result = validate_short_result(doc)
    codes = {e.split(":", 1)[0] for e in result.errors}
    assert {"OVERSIZE", "BAD_STATUS", "EXTRA_FIELD"} <= codes


def test_validator_never_raises_on_garbage():
    validate_short_result({"packet_id": object.__class__})  # must not raise
