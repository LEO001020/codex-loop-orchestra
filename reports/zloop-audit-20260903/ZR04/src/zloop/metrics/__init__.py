"""Metrics package for ZLoop."""
from __future__ import annotations

from zloop.metrics.c2c_stats import (
    C2CRecord,
    C2CStatsSummary,
    calculate_c2c_stats,
)
from zloop.metrics.concurrency import (
    ConcurrencyReport,
    WorkerInterval,
    analyze_concurrency,
    compute_instantaneous_concurrency,
    compute_overlap_ratio,
)
from zloop.metrics.latency import (
    LatencySampler,
    LatencySummary,
    compute_percentiles,
)
from zloop.metrics.tokens import (
    TokenReport,
    calculate_cost_savings,
    calculate_token_reduction,
    generate_token_report,
)

__all__ = [
    # tokens
    "TokenReport",
    "calculate_cost_savings",
    "calculate_token_reduction",
    "generate_token_report",
    # latency
    "LatencySampler",
    "LatencySummary",
    "compute_percentiles",
    # concurrency
    "ConcurrencyReport",
    "WorkerInterval",
    "analyze_concurrency",
    "compute_instantaneous_concurrency",
    "compute_overlap_ratio",
    # c2c_stats
    "C2CRecord",
    "C2CStatsSummary",
    "calculate_c2c_stats",
]
