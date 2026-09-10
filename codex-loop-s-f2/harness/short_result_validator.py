#!/usr/bin/env python3
"""short_result_validator.py — strict, fail-closed ShortResult validation.

Fixes P0-7 (``phase1b_codebase_analysis.md``): the <=500-token / <=8-findings
child-return contract previously lived only in prose and an unwired schema.
This module is the mechanical twin, enforced at **both** publish points
(``lifecycle_supervisor_v2.finalize_child`` and the CSV reconcile path) and by
``l2_consumer`` before any K3 verifier result is accepted.

Contract (``phase2_architecture_design.md`` §2.7):

* unit unification — the contract is **500 tokens**, enforced as
  ``max_chars = 2000`` (4 chars/token engineering constant);
* ``additionalProperties: false`` — any extra field is a rejection;
* every required field must be present with the right type;
* ``control_packet_revision`` must equal the expected revision when one is
  supplied (stale-revision guard: children racing a replan cannot publish
  against a superseded plan);
* findings capped at 8; verdict (when present, verifier results) must come
  from the closed L2 enum.

Validation is implemented without third-party dependencies (no ``jsonschema``
install requirement on either the Windows or the WSL plane); the embedded
declarative schema mirrors ``short_result.schema.json`` v2 exactly.

Every public function returns a structured :class:`ValidationResult` — the
caller decides quarantine/retry policy; this module never raises on invalid
*documents* (it raises only on programmer error such as unreadable schema
configuration).
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field as _dc_field
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MAX_FINDINGS",
    "L2_VERDICTS",
    "STATUS_ENUM",
    "ValidationResult",
    "ShortResultValidator",
    "estimate_tokens",
    "validate_short_result",
    "validate_short_result_file",
]

LOG = logging.getLogger("short_result_validator")

# 4 chars/token engineering constant — stated in the schema $comment
# (phase2 design §2.7.1: "2000 chars ≈ 500 tokens").
CHARS_PER_TOKEN: Final[int] = 4
DEFAULT_MAX_TOKENS: Final[int] = 500
DEFAULT_MAX_FINDINGS: Final[int] = 8

STATUS_ENUM: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "blocked", "cancelled", "stale_revision"}
)
# Closed L2 verifier verdict enum (escalation_ladder power semantics: L2 can
# only pass-forward, block, or escalate — never release).
L2_VERDICTS: Final[frozenset[str]] = frozenset(
    {"pass", "redo", "escalate_l2_5", "escalate_l3"}
)

# Declarative schema: field -> (types, required).  additionalProperties=false
# is enforced by the EXTRA_FIELD check against this exact key set.
_REQUIRED_FIELDS: Final[dict[str, tuple[type, ...]]] = {
    "packet_id": (str,),
    "control_packet_id": (str,),
    "control_packet_revision": (int,),
    "status": (str,),
    "conclusion": (str,),
    "artifact_paths": (list,),
    "finding_ids": (list,),
    "needs_decision": (dict, type(None)),
}
_OPTIONAL_FIELDS: Final[dict[str, tuple[type, ...]]] = {
    # Verifier-role results additionally carry the L2 verdict and idem key.
    "verdict": (str,),
    "idem_key": (str,),
    "score": (int, float),
}
_NEEDS_DECISION_FIELDS: Final[dict[str, tuple[type, ...]]] = {
    "question": (str,),
    "decision_refs": (list,),
    "evidence_refs": (list,),
}


def estimate_tokens(text: str) -> int:
    """Estimate the token count of *text* with the 4-chars/token constant.

    Deliberately conservative and deterministic: governance limits must be
    reproducible on replay, so no tokenizer dependency is allowed here.
    """
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


@dataclass(frozen=True)
class ValidationResult:
    """Structured pass/fail outcome of a ShortResult validation.

    Attributes:
        ok: ``True`` iff the document satisfies the full contract.
        code: machine-readable rejection code (``"OK"`` when ``ok``). One of
            ``OK | NOT_OBJECT | MISSING_FIELD | EXTRA_FIELD | TYPE_ERROR |
            EMPTY_FIELD | OVERSIZE | TOO_MANY_FINDINGS | BAD_STATUS |
            BAD_VERDICT | STALE_REVISION | BAD_NEEDS_DECISION | UNREADABLE``.
        reason: human-readable explanation (actionable: tells the worker what
            the contract is, per Fowler guides+sensors discipline).
        field: offending field name when applicable.
        errors: every individual violation found (not just the first) so a
            quarantined report is fully diagnosable from one record.
    """

    ok: bool
    code: str
    reason: str
    field: str | None = None
    errors: tuple[str, ...] = _dc_field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for events/quarantine records."""
        return {
            "ok": self.ok,
            "code": self.code,
            "reason": self.reason,
            "field": self.field,
            "errors": list(self.errors),
        }


def _fail(code: str, reason: str, field_name: str | None,
          errors: Sequence[str]) -> ValidationResult:
    return ValidationResult(False, code, reason, field_name, tuple(errors))


class ShortResultValidator:
    """Reusable validator bound to explicit limits.

    Instances are immutable and thread-safe (no mutable state after
    construction); a single instance can be shared across the consumer,
    supervisor, and reducer.

    Args:
        max_tokens: maximum ``conclusion`` size in tokens (default 500).
        max_findings: maximum ``finding_ids`` entries (default 8).
        require_verdict: when ``True`` (verifier results consumed by
            ``l2_consumer``) the ``verdict`` field is required and must be in
            :data:`L2_VERDICTS`.
    """

    def __init__(self, max_tokens: int = DEFAULT_MAX_TOKENS,
                 max_findings: int = DEFAULT_MAX_FINDINGS,
                 require_verdict: bool = False) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if max_findings < 0:
            raise ValueError("max_findings must be >= 0")
        self._max_tokens = max_tokens
        self._max_chars = max_tokens * CHARS_PER_TOKEN
        self._max_findings = max_findings
        self._require_verdict = require_verdict

    @property
    def max_tokens(self) -> int:
        """Configured token ceiling for the ``conclusion`` field."""
        return self._max_tokens

    def validate(self, doc: object,
                 expected_revision: int | None = None) -> ValidationResult:
        """Validate *doc* against the full ShortResult contract.

        Args:
            doc: parsed JSON document (any object; non-dicts are rejected).
            expected_revision: the current ControlPacket revision stamped at
                dispatch. When given, ``control_packet_revision`` must match
                exactly (stale-revision negative path, design §2.7.4).

        Returns:
            :class:`ValidationResult` — never raises for invalid documents.
        """
        errors: list[str] = []
        if not isinstance(doc, Mapping):
            return _fail("NOT_OBJECT",
                         "short result must be a single JSON object", None,
                         ["document is %s, not object" % type(doc).__name__])

        allowed = set(_REQUIRED_FIELDS) | set(_OPTIONAL_FIELDS)
        extras = sorted(set(doc) - allowed)
        for name in extras:
            errors.append("EXTRA_FIELD:%s" % name)

        for name, types in _REQUIRED_FIELDS.items():
            if name not in doc:
                errors.append("MISSING_FIELD:%s" % name)
            elif not isinstance(doc[name], types):
                errors.append("TYPE_ERROR:%s expected %s got %s" % (
                    name, "/".join(t.__name__ for t in types),
                    type(doc[name]).__name__))
        for name, types in _OPTIONAL_FIELDS.items():
            if name in doc and not isinstance(doc[name], types):
                errors.append("TYPE_ERROR:%s expected %s got %s" % (
                    name, "/".join(t.__name__ for t in types),
                    type(doc[name]).__name__))

        if self._require_verdict and "verdict" not in doc:
            errors.append("MISSING_FIELD:verdict (verifier result requires "
                          "the L2 verdict enum)")

        # Semantic checks only when the basic shape holds for that field.
        status = doc.get("status")
        if isinstance(status, str) and status not in STATUS_ENUM:
            errors.append("BAD_STATUS:%r not in %s" % (status, sorted(STATUS_ENUM)))

        verdict = doc.get("verdict")
        if isinstance(verdict, str) and verdict not in L2_VERDICTS:
            errors.append("BAD_VERDICT:%r not in %s" % (verdict, sorted(L2_VERDICTS)))

        conclusion = doc.get("conclusion")
        if isinstance(conclusion, str):
            if not conclusion.strip():
                errors.append("EMPTY_FIELD:conclusion")
            elif len(conclusion) > self._max_chars:
                errors.append(
                    "OVERSIZE:conclusion %d chars > %d (~%d tokens > %d): long"
                    " content belongs in artifact files referenced by"
                    " artifact_paths, never in the short result" % (
                        len(conclusion), self._max_chars,
                        estimate_tokens(conclusion), self._max_tokens))

        for name in ("packet_id", "control_packet_id"):
            value = doc.get(name)
            if isinstance(value, str) and not value.strip():
                errors.append("EMPTY_FIELD:%s" % name)

        findings = doc.get("finding_ids")
        if isinstance(findings, list):
            if len(findings) > self._max_findings:
                errors.append("TOO_MANY_FINDINGS:%d > %d" % (
                    len(findings), self._max_findings))
            for i, item in enumerate(findings):
                if not isinstance(item, str):
                    errors.append("TYPE_ERROR:finding_ids[%d] must be string" % i)

        artifacts = doc.get("artifact_paths")
        if isinstance(artifacts, list):
            for i, item in enumerate(artifacts):
                if not isinstance(item, str):
                    errors.append("TYPE_ERROR:artifact_paths[%d] must be string" % i)

        needs = doc.get("needs_decision")
        if isinstance(needs, Mapping):
            nd_extras = sorted(set(needs) - set(_NEEDS_DECISION_FIELDS))
            for name in nd_extras:
                errors.append("BAD_NEEDS_DECISION:extra field %s" % name)
            for name, types in _NEEDS_DECISION_FIELDS.items():
                if name not in needs:
                    errors.append("BAD_NEEDS_DECISION:missing %s" % name)
                elif not isinstance(needs[name], types):
                    errors.append("BAD_NEEDS_DECISION:%s type" % name)

        revision = doc.get("control_packet_revision")
        if isinstance(revision, bool):  # bool is an int subclass — reject
            errors.append("TYPE_ERROR:control_packet_revision must be integer")
        elif isinstance(revision, int):
            if revision < 1:
                errors.append("TYPE_ERROR:control_packet_revision must be >= 1")
            elif expected_revision is not None and revision != expected_revision:
                errors.append(
                    "STALE_REVISION:published revision %d != current %d — the"
                    " plan was superseded; re-read the ControlPacket and"
                    " republish" % (revision, expected_revision))

        if not errors:
            return ValidationResult(True, "OK", "short result valid", None, ())

        primary = errors[0]
        code = primary.split(":", 1)[0]
        field_name = None
        if ":" in primary:
            tail = primary.split(":", 1)[1]
            field_name = tail.split(" ", 1)[0].split("[", 1)[0] or None
        result = _fail(code, primary, field_name, errors)
        LOG.info("short result rejected: %s (%d violations)", code, len(errors))
        return result


def validate_short_result(doc: object,
                          expected_revision: int | None = None,
                          max_tokens: int = DEFAULT_MAX_TOKENS,
                          max_findings: int = DEFAULT_MAX_FINDINGS,
                          require_verdict: bool = False) -> ValidationResult:
    """One-shot convenience wrapper around :class:`ShortResultValidator`."""
    return ShortResultValidator(
        max_tokens=max_tokens, max_findings=max_findings,
        require_verdict=require_verdict,
    ).validate(doc, expected_revision=expected_revision)


def validate_short_result_file(path: Path | str,
                               expected_revision: int | None = None,
                               max_tokens: int = DEFAULT_MAX_TOKENS,
                               max_findings: int = DEFAULT_MAX_FINDINGS,
                               require_verdict: bool = False) -> ValidationResult:
    """Read and validate a report file; unreadable/unparseable fails closed."""
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _fail("UNREADABLE",
                     "cannot read/parse %s: %s" % (p, exc), None,
                     ["UNREADABLE:%s" % exc])
    return validate_short_result(doc, expected_revision=expected_revision,
                                 max_tokens=max_tokens,
                                 max_findings=max_findings,
                                 require_verdict=require_verdict)


def _main(argv: Sequence[str]) -> int:
    """CLI: ``short_result_validator.py <report.json> [--revision N]``.

    Exit 0 = valid, 1 = invalid (details on stderr), 2 = usage error.
    """
    import argparse

    ap = argparse.ArgumentParser(description="strict ShortResult validation")
    ap.add_argument("report", help="path to report.json")
    ap.add_argument("--revision", type=int, default=None,
                    help="expected control_packet_revision")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--require-verdict", action="store_true")
    args = ap.parse_args(argv)

    result = validate_short_result_file(
        args.report, expected_revision=args.revision,
        max_tokens=args.max_tokens, require_verdict=args.require_verdict)
    if result.ok:
        print("PASS %s" % args.report)
        return 0
    for err in result.errors:
        print(err, file=sys.stderr)  # fail-visible
    print("FAIL code=%s %s" % (result.code, args.report))
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
