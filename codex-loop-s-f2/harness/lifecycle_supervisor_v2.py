#!/usr/bin/env python3
"""lifecycle_supervisor_v2.py — schema-validated child result finalization.

Fixes P0-7 (``phase1b_codebase_analysis.md``): v1's success condition was
``rc == 0 and report.exists()`` — no schema validation, no summary bound, and
duty/reviewer verdicts machine-parsed from reply bodies. v2 makes the
short-return contract mechanical at every publish point
(``phase2_architecture_design.md`` §2.7):

* **success requires** ``rc == 0 AND report exists AND
  validate_short_result(report) == OK`` — oversize/extra-field/missing-field/
  stale-revision reports fail closed as ``exec_failed`` with reason
  ``short_result_invalid`` and the report is *quarantined* to
  ``data/reports/<pid>/report.rejected.json`` (evidence preserved, never
  propagated);
* **fixed-size receipt** — the orchestrating session receives only
  ``{packet_id, status, report_path}``; content never rides the tool result;
* **bounded CSV summaries** — the CSV publish path runs the same validator,
  and any ``summary`` beyond ``max_chars`` is rejected (not truncated-then-
  forwarded): unbounded strings can no longer re-inflate root context;
* **single verdict channel** — duty/reviewer/verifier verdicts are read from
  ``report.json`` **only**; ``last_message.txt`` is forensic and never
  machine-parsed (the contradictory reply-body path is gone);
* **revision guard** — dispatch stamps children with the ControlPacket
  revision; publishing against a superseded revision fails as
  ``stale_revision`` (children racing a replan cannot publish);
* integrations — :mod:`short_result_validator` does the validation and
  :mod:`result_reducer` receives every accepted result, so Sol's finale sees
  one bounded AdjudicationPacket instead of raw returns.

This module deliberately implements the *result-boundary* half of the
supervisor: the process-boundary half (setsid/Job-object spawn, heartbeat,
timeout/cancel) is unchanged from v1 and continues to work as shipped; v1's
publish path calls :func:`finalize_child` instead of its inline
``rc==0 && exists`` check.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from short_result_validator import (  # noqa: E402
    CHARS_PER_TOKEN,
    ShortResultValidator,
    ValidationResult,
)

__all__ = [
    "FinalizeOutcome",
    "Receipt",
    "LifecycleSupervisorV2",
    "finalize_child",
    "parse_verdict_from_report",
    "bound_csv_summary",
]

LOG = logging.getLogger("lifecycle_supervisor_v2")

_RECEIPT_FIELDS: Final[tuple[str, ...]] = ("packet_id", "status", "report_path")
_LEGAL_DUTY_RULINGS: Final[frozenset[str]] = frozenset(
    {"duty_retryable", "duty_fixable", "duty_terminal"})


@dataclass(frozen=True)
class Receipt:
    """Fixed-size receipt returned to the orchestrating session.

    Deliberately tiny (< 200 tokens by construction): content never rides
    the tool result — long content belongs in files named by
    ``artifact_paths`` (§2.7 AC1).
    """

    packet_id: str
    status: str
    report_path: str

    def to_json(self) -> str:
        """Serialize; asserts the fixed-size property."""
        body = json.dumps({"packet_id": self.packet_id, "status": self.status,
                           "report_path": self.report_path}, sort_keys=True)
        if len(body) > 200 * CHARS_PER_TOKEN:
            raise ValueError("receipt exceeded its fixed-size bound")
        return body


@dataclass(frozen=True)
class FinalizeOutcome:
    """Outcome of finalizing one child execution."""

    success: bool
    event: str                       # subagent_stop | exec_failed
    why: str | None
    receipt: Receipt
    validation: ValidationResult | None
    quarantine_path: str | None = None


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, sort_keys=True, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def parse_verdict_from_report(report_path: Path | str,
                              kind: str = "verifier") -> str | None:
    """Read a machine verdict from ``report.json`` — the ONLY verdict channel.

    Args:
        report_path: the child's report file.
        kind: ``"verifier"`` (L2 enum in ``verdict``), ``"duty"`` (ruling in
            ``ruling``), or ``"reviewer"`` (release-review ``verdict``).

    Returns:
        the verdict string, or ``None`` when absent/illegal. Callers MUST
        treat ``None`` as fail-visible (the packet fails; a ruling present
        only in a reply body is ignored by design — §2.7 AC3).
    """
    try:
        doc = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    if kind == "duty":
        ruling = doc.get("ruling")
        return ruling if ruling in _LEGAL_DUTY_RULINGS else None
    verdict = doc.get("verdict")
    return verdict if isinstance(verdict, str) and verdict else None


def bound_csv_summary(summary: object, max_chars: int = 2000) -> tuple[bool, str]:
    """Validate one CSV call-pack ``summary`` against the bounded contract.

    Returns ``(ok, reason)``. Rejection (not truncation): a producer that
    overflows must learn the contract, and truncated content must never be
    silently forwarded as if complete.
    """
    if not isinstance(summary, str):
        return False, "summary must be a string"
    if len(summary) > max_chars:
        return False, ("summary %d chars > %d (~%d tokens): long content "
                       "belongs in artifact files, not the CSV summary"
                       % (len(summary), max_chars, max_chars // CHARS_PER_TOKEN))
    return True, "ok"


class LifecycleSupervisorV2:
    """Result-boundary supervisor: validate, quarantine, receipt, reduce.

    Args:
        root: LOOP root directory.
        max_tokens: ShortResult conclusion bound (policy
            ``[context].child_short_result_max_tokens``; default 500).
        max_findings: findings cap (default 8).
        reducer: optional :class:`result_reducer.ResultReducer` — accepted
            results are fed to it so the finale AdjudicationPacket is built
            incrementally.
    """

    def __init__(self, root: Path | str, max_tokens: int = 500,
                 max_findings: int = 8, reducer: Any | None = None,
                 clock: Any = time.time) -> None:
        self.root = Path(root).resolve()
        self._validator = ShortResultValidator(max_tokens=max_tokens,
                                               max_findings=max_findings)
        self._reducer = reducer
        self._clock = clock

    # -- events -----------------------------------------------------------------

    def _emit_event(self, packet_id: str, event: str,
                    detail: Mapping[str, Any]) -> None:
        events = self.root / "data" / "events.ndjson"
        events.parent.mkdir(parents=True, exist_ok=True)
        with open(events, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": self._clock(), "packet_id": packet_id, "event": event,
                "source": "lifecycle_supervisor_v2", "detail": dict(detail)},
                sort_keys=True) + "\n")

    # -- the fixed publish path ---------------------------------------------------

    def finalize_child(self, *, packet_id: str, rc: int, report: Path,
                       expected_revision: int | None = None,
                       publish_to: Path | None = None) -> FinalizeOutcome:
        """Finalize one child run: the v2 success condition.

        ``success = rc == 0 AND report.exists() AND schema-valid short result``
        (revision-checked when *expected_revision* is given). Anything else
        fails closed:

        * nonzero rc → ``exec_failed/nonzero_exit``;
        * missing report → ``exec_failed/missing_report``;
        * invalid/oversize/stale report → ``exec_failed/short_result_invalid``
          (or ``stale_revision``) with the report **quarantined** to
          ``report.rejected.json`` — evidence preserved, never propagated.

        Returns a :class:`FinalizeOutcome` whose ``receipt`` is the ONLY
        object the orchestrating session may receive.
        """
        report = Path(report)
        if rc != 0:
            detail = {"why": "nonzero_exit", "exit_code": rc}
            self._emit_event(packet_id, "exec_failed", detail)
            return FinalizeOutcome(
                False, "exec_failed", "nonzero_exit",
                Receipt(packet_id, "failed", str(report)), None)

        if not report.exists():
            detail = {"why": "missing_report", "exit_code": rc}
            self._emit_event(packet_id, "exec_failed", detail)
            return FinalizeOutcome(
                False, "exec_failed", "missing_report",
                Receipt(packet_id, "failed", str(report)), None)

        try:
            doc = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result = ValidationResult(False, "UNREADABLE",
                                      "cannot parse report: %s" % exc, None,
                                      ("UNREADABLE:%s" % exc,))
            doc = None
        else:
            result = self._validator.validate(
                doc, expected_revision=expected_revision)

        if not result.ok:
            quarantine = self._quarantine(packet_id, report)
            why = ("stale_revision" if result.code == "STALE_REVISION"
                   else "short_result_invalid")
            self._emit_event(packet_id, "exec_failed", {
                "why": why, "validation": result.to_dict(),
                "quarantined": quarantine})
            LOG.warning("packet %s failed closed: %s (%s)", packet_id, why,
                        result.code)
            return FinalizeOutcome(
                False, "exec_failed", why,
                Receipt(packet_id, "failed", str(report)), result, quarantine)

        # success: publish, receipt, reduce
        published = report
        if publish_to is not None:
            publish_to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report, publish_to)
            published = publish_to
        self._emit_event(packet_id, "subagent_stop", {
            "source": "lifecycle_supervisor_v2", "exit_code": 0,
            "report_path": str(published),
            "revision": (doc or {}).get("control_packet_revision")})
        if self._reducer is not None and doc is not None:
            try:
                self._reducer.add_short_result(doc, source="supervisor")
            except Exception:  # reducer trouble must not fail a green child
                LOG.exception("reducer ingestion failed for %s", packet_id)
        return FinalizeOutcome(
            True, "subagent_stop", None,
            Receipt(packet_id, str((doc or {}).get("status", "completed")),
                    str(published)),
            result)

    def _quarantine(self, packet_id: str, report: Path) -> str | None:
        """Move an invalid report aside — evidence preserved, never merged."""
        dest = (self.root / "data" / "reports" / packet_id
                / "report.rejected.json")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report, dest)
            return str(dest)
        except OSError as exc:
            LOG.error("quarantine failed for %s: %s", packet_id, exc)
            return None

    # -- CSV batch path -------------------------------------------------------------

    def finalize_csv_row(self, *, packet_id: str, row: Mapping[str, Any],
                         report: Path,
                         expected_revision: int | None = None,
                         max_summary_chars: int = 2000) -> FinalizeOutcome:
        """CSV publish path: same validator + bounded summary (P0-7.2).

        The row's ``summary`` must satisfy :func:`bound_csv_summary`; the
        tool-result return is the fixed-size receipt, never the summary.
        """
        ok, reason = bound_csv_summary(row.get("summary", ""),
                                       max_chars=max_summary_chars)
        if not ok:
            self._emit_event(packet_id, "exec_failed", {
                "why": "short_result_invalid", "csv_summary": reason})
            return FinalizeOutcome(
                False, "exec_failed", "short_result_invalid",
                Receipt(packet_id, "failed", str(report)),
                ValidationResult(False, "OVERSIZE", reason, "summary",
                                 ("OVERSIZE:%s" % reason,)))
        return self.finalize_child(packet_id=packet_id, rc=0, report=report,
                                   expected_revision=expected_revision)

    # -- verdict routing (single channel) ---------------------------------------------

    def route_duty_ruling(self, packet_id: str,
                          report: Path) -> str | None:
        """Duty ruling from ``report.json["ruling"]`` only (§2.7 AC3).

        A ruling present only in a reply body is ignored and the packet fails
        visible (``duty_ruling_missing`` event → the retry/DLQ path decides).
        """
        ruling = parse_verdict_from_report(report, kind="duty")
        if ruling is None:
            self._emit_event(packet_id, "exec_failed", {
                "why": "duty_ruling_missing",
                "note": "ruling must be report.json['ruling'] — reply bodies "
                        "are forensic only and never machine-parsed"})
            return None
        self._emit_event(packet_id, ruling, {"source": "report.json"})
        return ruling


def finalize_child(root: Path | str, *, packet_id: str, rc: int,
                   report: Path | str,
                   expected_revision: int | None = None) -> FinalizeOutcome:
    """Module-level convenience wrapper around
    :meth:`LifecycleSupervisorV2.finalize_child`."""
    return LifecycleSupervisorV2(root).finalize_child(
        packet_id=packet_id, rc=rc, report=Path(report),
        expected_revision=expected_revision)


def _main(argv: Sequence[str]) -> int:
    """CLI: ``lifecycle_supervisor_v2.py --packet p --rc 0 --report r.json``.

    Prints the fixed-size receipt on stdout (the only thing a caller may
    forward). Exit 0 = success, 1 = failed closed.
    """
    import argparse

    ap = argparse.ArgumentParser(
        description="schema-validated child result finalization")
    ap.add_argument("--root", default=os.environ.get("LOOP_ROOT", "."))
    ap.add_argument("--packet", required=True)
    ap.add_argument("--rc", type=int, required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--revision", type=int, default=None)
    args = ap.parse_args(argv)

    outcome = finalize_child(args.root, packet_id=args.packet, rc=args.rc,
                             report=args.report,
                             expected_revision=args.revision)
    print(outcome.receipt.to_json())
    if not outcome.success and outcome.validation is not None:
        for err in outcome.validation.errors:
            print(err, file=sys.stderr)  # fail-visible
    return 0 if outcome.success else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
