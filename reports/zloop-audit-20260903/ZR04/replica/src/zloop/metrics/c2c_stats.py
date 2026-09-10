"""zloop.metrics.c2c_stats — C2C audit pass rates and verification metrics.

Provides C2CRecord dataclass, C2CStatsSummary dataclass, and calculate_c2c_stats
pure computation function for plan and result audit evaluations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class C2CRecord:
    """Individual C2C audit result record."""

    c2c_id: str
    role: str  # "plan" or "result"
    verdict: str  # e.g. "APPROVED", "REJECTED", "PASS", "FAIL"
    latency_s: float = 0.0


@dataclass(frozen=True)
class C2CStatsSummary:
    """Aggregated statistics across C2C audit evaluations."""

    total: int
    plan_total: int
    plan_approved: int
    plan_pass_rate: float
    result_total: int
    result_approved: int
    result_pass_rate: float
    overall_pass_rate: float

    def to_dict(self) -> dict[str, Any]:
        """Convert the summary to a dictionary representation."""
        return asdict(self)


_APPROVED_VERDICTS = frozenset({"approved", "pass"})


def calculate_c2c_stats(records: Sequence[C2CRecord]) -> C2CStatsSummary:
    """Calculate pass rates and totals across C2C audit records.

    Case-insensitive matching: "APPROVED" and "PASS" count as passing/approved.
    If category totals are 0, rates are returned as 0.0.
    """
    total = len(records)
    plan_total = 0
    plan_approved = 0
    result_total = 0
    result_approved = 0

    for r in records:
        role_lower = r.role.strip().lower()
        verdict_lower = r.verdict.strip().lower()
        is_pass = verdict_lower in _APPROVED_VERDICTS

        if role_lower == "plan":
            plan_total += 1
            if is_pass:
                plan_approved += 1
        elif role_lower == "result":
            result_total += 1
            if is_pass:
                result_approved += 1

    plan_rate = (plan_approved / plan_total) if plan_total > 0 else 0.0
    result_rate = (result_approved / result_total) if result_total > 0 else 0.0
    total_approved = plan_approved + result_approved
    overall_rate = (total_approved / total) if total > 0 else 0.0

    return C2CStatsSummary(
        total=total,
        plan_total=plan_total,
        plan_approved=plan_approved,
        plan_pass_rate=plan_rate,
        result_total=result_total,
        result_approved=result_approved,
        result_pass_rate=result_rate,
        overall_pass_rate=overall_rate,
    )
