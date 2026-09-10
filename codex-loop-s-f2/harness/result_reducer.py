#!/usr/bin/env python3
"""result_reducer.py — zero-model ShortResult/verdict reduction pipeline.

Implements §5.4 of ``phase2_architecture_design.md`` (S6 inflow fix): a
deterministic reducer that collects the ShortResults and L2 verdicts of a
wave (or anomaly set), deduplicates them by idempotency key, aggregates the
verdicts, merges compatible results, and renders **one bounded
AdjudicationPacket** (≤2,000 new tokens) — the *only* thing Sol's finale round
ever consumes. "Orient via compressed summaries, never raw trajectories."

Verdict algebra (strictest-wins, identical to ``verdict_aggregate.py`` so the
two components can never disagree)::

    pass < redo < escalate_l2_5 < escalate_l3

Power semantics preserved (S5): an aggregate ``pass`` only exempts packets
from Sol per-packet review and forwards them to the serial merge queue; it
never releases anything — mechanical acceptance and the human L4 gate still
follow.

L2.5 ranking: when multiple candidates exist for one packet (multi-candidate
mode, ≤3), candidates are ranked least-strict-verdict-first then
highest-mean-score, the winner is emitted as a ``best_candidate`` event
(t36 L2_RANK → L2_VERIFY) and the rest are discarded from the packet's merge
path.

Integration:

* ``l2_consumer`` completions feed :meth:`ResultReducer.add_completion`;
* ``lifecycle_supervisor_v2`` feeds validated child ShortResults through
  :meth:`ResultReducer.add_short_result`;
* ``verdict_check.py`` semantics are honored: a negative verdict without
  findings is inconsistent and escalates instead of blocking silently;
* output integrates with the state machine through a single consolidated
  verdict + an AdjudicationPacket file for ``SOL_ADJUDICATE``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field as _dc_field
from pathlib import Path
from typing import Any, Final, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from short_result_validator import (  # noqa: E402
    CHARS_PER_TOKEN,
    ShortResultValidator,
)

__all__ = [
    "VERDICT_ORDER",
    "CandidateRanking",
    "ConsolidatedVerdict",
    "AdjudicationPacket",
    "ResultReducer",
]

LOG = logging.getLogger("result_reducer")

# Strictness-ascending — MUST stay identical to verdict_aggregate.py ORDER.
VERDICT_ORDER: Final[tuple[str, ...]] = (
    "pass", "redo", "escalate_l2_5", "escalate_l3")
_RANK: Final[dict[str, int]] = {v: i for i, v in enumerate(VERDICT_ORDER)}

_DEFAULT_ADJUDICATION_MAX_TOKENS: Final[int] = 2000


def _atomic_write_json(path: Path, obj: Any) -> None:
    """Atomic JSON write (temp + ``os.replace``), Windows/WSL safe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, sort_keys=True, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class CandidateRanking:
    """Ranked view of one candidate in L2.5 multi-candidate mode."""

    candidate_id: str
    verdict: str
    mean_score: float
    diff_path: str | None = None


@dataclass(frozen=True)
class ConsolidatedVerdict:
    """Single consolidated verdict handed to the state machine.

    Attributes:
        verdict: strictest verdict across all deduplicated inputs.
        event: the state-machine event name to emit
            (``verdict_pass`` … ``verdict_escalate_l3``).
        n_inputs: number of deduplicated contributing results.
        n_duplicates: inputs dropped by idempotency-key dedup.
        best_candidate: L2.5 winner when candidates were ranked, else ``None``.
        inconsistencies: verdict_check-style closure violations found (a
            negative verdict citing zero findings, etc.). Non-empty
            inconsistencies force escalation — never a silent pass.
    """

    verdict: str
    event: str
    n_inputs: int
    n_duplicates: int
    best_candidate: CandidateRanking | None
    inconsistencies: tuple[str, ...] = _dc_field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form."""
        return {
            "verdict": self.verdict, "event": self.event,
            "n_inputs": self.n_inputs, "n_duplicates": self.n_duplicates,
            "best_candidate": (self.best_candidate.__dict__
                               if self.best_candidate else None),
            "inconsistencies": list(self.inconsistencies),
        }


@dataclass(frozen=True)
class AdjudicationPacket:
    """Bounded Sol input: statuses + finding ids + artifact *paths* + question.

    Never carries content — artifact paths only (design invariant 5).
    """

    packet_ids: tuple[str, ...]
    statuses: dict[str, str]
    verdict: str
    finding_ids: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    decision_question: str
    token_estimate: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form (what gets written to disk for Sol)."""
        return {
            "schema": "codex-loop-adjudication-packet/v2",
            "packet_ids": list(self.packet_ids),
            "statuses": dict(self.statuses),
            "verdict": self.verdict,
            "finding_ids": list(self.finding_ids),
            "artifact_paths": list(self.artifact_paths),
            "decision_question": self.decision_question,
            "token_estimate": self.token_estimate,
        }


class ResultReducer:
    """Deduplicating, verdict-aggregating, budget-bounded result reducer.

    Thread-safe: all mutating methods hold an internal lock, so supervisor
    threads and the consumer can feed one reducer instance concurrently.

    Args:
        root: LOOP root (events + adjudication outputs live under ``data/``).
        max_adjudication_tokens: hard budget of the rendered
            AdjudicationPacket (default 2,000 — policy
            ``[context].adjudication_packet_max_new_tokens``).
        validator: optional shared :class:`ShortResultValidator` used to
            re-check documents defensively before reduction.
    """

    def __init__(self, root: Path | str,
                 max_adjudication_tokens: int = _DEFAULT_ADJUDICATION_MAX_TOKENS,
                 validator: ShortResultValidator | None = None,
                 clock: Any = time.time) -> None:
        self.root = Path(root).resolve()
        self.max_tokens = int(max_adjudication_tokens)
        self._validator = validator or ShortResultValidator()
        self._clock = clock
        self._lock = threading.Lock()
        # idem_key -> record; insertion order preserved for determinism
        self._results: dict[str, dict[str, Any]] = {}
        self._duplicates = 0
        self._candidates: dict[str, list[dict[str, Any]]] = {}

    # -- collection ----------------------------------------------------------

    def add_short_result(self, doc: Mapping[str, Any],
                         idem_key: str | None = None,
                         source: str = "supervisor") -> bool:
        """Add one validated ShortResult; dedup by idempotency key.

        Args:
            doc: the ShortResult document (already validated at its publish
                point; re-validated here defensively — a reducer must never
                trust its producers).
            idem_key: explicit idempotency key; falls back to the document's
                ``idem_key`` field, else a semantic composite of
                ``packet_id|control_packet_revision|status``.
            source: provenance label for the audit trail.

        Returns:
            ``True`` when the result was added, ``False`` when it was a
            duplicate or failed defensive validation.
        """
        key = idem_key or str(doc.get("idem_key") or "") or (
            "sr:%s:%s:%s" % (doc.get("packet_id"),
                             doc.get("control_packet_revision"),
                             doc.get("status")))
        check = self._validator.validate(dict(doc))
        if not check.ok:
            LOG.warning("reducer rejected %s from %s: %s", key, source,
                        check.code)
            return False
        with self._lock:
            if key in self._results:
                self._duplicates += 1
                LOG.debug("duplicate result %s dropped (dedup)", key)
                return False
            self._results[key] = {"doc": dict(doc), "source": source,
                                  "idem_key": key, "ts": self._clock()}
        return True

    def add_completion(self, completion: Mapping[str, Any]) -> bool:
        """Ingest one ``l2_consumer`` completion marker.

        Invalid completions contribute an ``escalate_l3``-weight synthetic
        record (a verification that could not be validated can never pass).
        """
        key = str(completion.get("idem_key") or "")
        if not key:
            LOG.warning("completion without idem_key ignored")
            return False
        verdict = completion.get("verdict")
        with self._lock:
            if key in self._results:
                self._duplicates += 1
                return False
            self._results[key] = {
                "doc": None, "source": "l2_consumer", "idem_key": key,
                "verdict": verdict if verdict in _RANK else "escalate_l3",
                "valid": bool(completion.get("valid")),
                "report_path": completion.get("report_path"),
                "ts": self._clock()}
        return True

    def add_candidate(self, packet_id: str, candidate_id: str,
                      verdicts: Sequence[Mapping[str, Any]],
                      diff_path: str | None = None) -> None:
        """Register an L2.5 candidate (≤3 per packet) with its L2 verdicts."""
        with self._lock:
            bucket = self._candidates.setdefault(packet_id, [])
            if len(bucket) >= 3:
                raise ValueError("L2.5 accepts at most 3 candidates per packet")
            bucket.append({"candidate_id": candidate_id,
                           "verdicts": [dict(v) for v in verdicts],
                           "diff_path": diff_path})

    # -- reduction -----------------------------------------------------------

    @staticmethod
    def _strictest(verdicts: Iterable[str]) -> str:
        worst = "pass"
        for v in verdicts:
            if v in _RANK and _RANK[v] > _RANK[worst]:
                worst = v
        return worst

    def rank_candidates(self, packet_id: str) -> list[CandidateRanking]:
        """L2.5 ranking: least-strict verdict first, then highest mean score.

        A candidate nobody judged can never pass — it ranks as
        ``escalate_l3`` with score 0 (verdict_aggregate.py semantics).
        """
        with self._lock:
            cands = list(self._candidates.get(packet_id, ()))
        ranking: list[CandidateRanking] = []
        for cand in cands:
            vs = [v for v in cand["verdicts"] if v.get("verdict") in _RANK]
            if not vs:
                strict, score = "escalate_l3", 0.0
            else:
                strict = self._strictest(v["verdict"] for v in vs)
                score = sum(float(v.get("score", 0.0)) for v in vs) / len(vs)
            ranking.append(CandidateRanking(
                candidate_id=str(cand["candidate_id"]), verdict=strict,
                mean_score=round(score, 4), diff_path=cand.get("diff_path")))
        ranking.sort(key=lambda r: (_RANK[r.verdict], -r.mean_score,
                                    r.candidate_id))
        return ranking

    def consolidate(self) -> ConsolidatedVerdict:
        """Produce the single consolidated verdict for the state machine.

        Rules:
          * dedup already happened at insertion; strictest verdict wins across
            all inputs;
          * ShortResults with ``status`` in {failed, blocked} weigh ``redo``;
            ``stale_revision`` weighs ``redo``; ``cancelled`` weighs
            ``escalate_l3`` (someone stopped it — a human must know);
          * verdict-closure inconsistencies (negative verdict, zero findings)
            force at least ``escalate_l3``: never silently trust a malformed
            negative;
          * when L2.5 candidates exist, the per-packet winner re-enters
            verification (t36) and is reported as ``best_candidate``.
        """
        with self._lock:
            records = list(self._results.values())
            duplicates = self._duplicates
            candidate_packets = list(self._candidates)

        verdicts: list[str] = []
        inconsistencies: list[str] = []
        for rec in records:
            if rec.get("doc") is None:  # l2 completion
                verdicts.append(str(rec.get("verdict", "escalate_l3")))
                if not rec.get("valid", False):
                    inconsistencies.append(
                        "INVALID_COMPLETION:%s" % rec["idem_key"])
                continue
            doc = rec["doc"]
            status = doc.get("status")
            explicit = doc.get("verdict")
            if explicit in _RANK:
                verdicts.append(str(explicit))
                if explicit != "pass" and not doc.get("finding_ids"):
                    inconsistencies.append(
                        "CLOSURE_VIOLATION:%s negative verdict %s cites zero "
                        "findings" % (rec["idem_key"], explicit))
            elif status == "completed":
                verdicts.append("pass")
            elif status in ("failed", "blocked", "stale_revision"):
                verdicts.append("redo")
            elif status == "cancelled":
                verdicts.append("escalate_l3")
            if doc.get("needs_decision"):
                verdicts.append("escalate_l3")

        worst = self._strictest(verdicts) if verdicts else "escalate_l3"
        if not records:
            inconsistencies.append("NO_INPUTS:reducing an empty set can "
                                   "never pass")
        if inconsistencies and _RANK[worst] < _RANK["escalate_l3"]:
            worst = "escalate_l3"

        best: CandidateRanking | None = None
        if candidate_packets:
            ranked = self.rank_candidates(candidate_packets[0])
            best = ranked[0] if ranked else None
            if best is not None:
                self._emit_event(candidate_packets[0], "best_candidate", {
                    "candidate_id": best.candidate_id,
                    "verdict": best.verdict,
                    "mean_score": best.mean_score})

        return ConsolidatedVerdict(
            verdict=worst, event="verdict_%s" % worst,
            n_inputs=len(records), n_duplicates=duplicates,
            best_candidate=best, inconsistencies=tuple(inconsistencies))

    # -- adjudication packet ---------------------------------------------------

    def render_adjudication_packet(
            self, decision_question: str,
            wave: str | None = None) -> AdjudicationPacket:
        """Render the bounded AdjudicationPacket for Sol's finale round.

        Contents: per-packet statuses, deduplicated finding ids, artifact
        *paths* (never content), the consolidated verdict, and the one
        specific decision question. The rendered JSON is guaranteed to fit
        ``max_adjudication_tokens`` — lower-value fields (artifact paths,
        then finding ids) are truncated with an explicit ``…(+N more)``
        sentinel rather than exceeding the budget.
        """
        consolidated = self.consolidate()
        with self._lock:
            records = list(self._results.values())

        statuses: dict[str, str] = {}
        finding_ids: list[str] = []
        artifact_paths: list[str] = []
        for rec in records:
            doc = rec.get("doc")
            if not doc:
                continue
            pid = str(doc.get("packet_id", "?"))
            statuses[pid] = str(doc.get("status", "?"))
            for f in doc.get("finding_ids", []):
                if f not in finding_ids:
                    finding_ids.append(f)
            for a in doc.get("artifact_paths", []):
                if a not in artifact_paths:
                    artifact_paths.append(a)

        def build(fids: list[str], arts: list[str]) -> AdjudicationPacket:
            packet = AdjudicationPacket(
                packet_ids=tuple(sorted(statuses)),
                statuses=statuses, verdict=consolidated.verdict,
                finding_ids=tuple(fids), artifact_paths=tuple(arts),
                decision_question=decision_question,
                token_estimate=0)
            body = json.dumps(packet.to_dict(), sort_keys=True)
            est = (len(body) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
            return AdjudicationPacket(
                packet.packet_ids, packet.statuses, packet.verdict,
                packet.finding_ids, packet.artifact_paths,
                packet.decision_question, est)

        fids, arts = list(finding_ids), list(artifact_paths)
        packet = build(fids, arts)
        while packet.token_estimate > self.max_tokens and (fids or arts):
            if arts:
                arts = arts[:max(0, len(arts) - max(1, len(arts) // 4))]
                if arts:
                    arts[-1] = "…(+%d more, see report files)" % (
                        len(artifact_paths) - len(arts) + 1)
            elif fids:
                fids = fids[:max(0, len(fids) - 1)]
            packet = build(fids, arts)

        out_dir = self.root / "data" / "adjudication"
        name = "adjudication_%s.json" % (wave or ("%d" % int(self._clock())))
        _atomic_write_json(out_dir / name, {
            **packet.to_dict(),
            "consolidated": consolidated.to_dict(),
            "rendered_ts": self._clock()})
        LOG.info("adjudication packet rendered: %s (%d est. tokens)",
                 name, packet.token_estimate)
        return packet

    # -- events ---------------------------------------------------------------

    def _emit_event(self, packet_id: str, event: str,
                    detail: dict[str, Any]) -> None:
        events = self.root / "data" / "events.ndjson"
        events.parent.mkdir(parents=True, exist_ok=True)
        with open(events, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": self._clock(), "packet_id": packet_id, "event": event,
                "source": "result_reducer", "detail": detail},
                sort_keys=True) + "\n")


def _main(argv: Sequence[str]) -> int:
    """CLI: reduce completion markers + reports into one AdjudicationPacket.

    ``result_reducer.py --root . --question "..." [--wave w3]`` scans
    ``data/l2_queue/completions/*.json`` and ``data/reports/*/report.json``.
    """
    import argparse

    ap = argparse.ArgumentParser(description="zero-model result reducer")
    ap.add_argument("--root", default=os.environ.get("LOOP_ROOT", "."))
    ap.add_argument("--question", required=True,
                    help="the specific decision question for Sol")
    ap.add_argument("--wave", default=None)
    ap.add_argument("--max-tokens", type=int,
                    default=_DEFAULT_ADJUDICATION_MAX_TOKENS)
    args = ap.parse_args(argv)

    root = Path(args.root)
    reducer = ResultReducer(root, max_adjudication_tokens=args.max_tokens)
    for comp in sorted((root / "data" / "l2_queue" / "completions").glob("*.json")):
        try:
            reducer.add_completion(json.loads(comp.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            LOG.warning("skipping completion %s: %s", comp, exc)
    for report in sorted((root / "data" / "reports").glob("*/report.json")):
        try:
            reducer.add_short_result(
                json.loads(report.read_text(encoding="utf-8")),
                source=str(report))
        except (OSError, ValueError) as exc:
            LOG.warning("skipping report %s: %s", report, exc)
    packet = reducer.render_adjudication_packet(args.question, wave=args.wave)
    print(json.dumps(packet.to_dict()))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
