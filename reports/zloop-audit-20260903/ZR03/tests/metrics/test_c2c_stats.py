"""Tests for zloop.metrics.c2c_stats."""
from __future__ import annotations

from zloop.metrics.c2c_stats import (
    C2CRecord,
    C2CStatsSummary,
    calculate_c2c_stats,
)


def test_calculate_c2c_stats_empty():
    stats = calculate_c2c_stats([])
    assert isinstance(stats, C2CStatsSummary)
    assert stats.total == 0
    assert stats.overall_pass_rate == 0.0
    assert stats.plan_pass_rate == 0.0
    assert stats.result_pass_rate == 0.0


def test_calculate_c2c_stats_mixed():
    records = [
        C2CRecord(c2c_id="C01", role="plan", verdict="APPROVED", latency_s=1.2),
        C2CRecord(c2c_id="C02", role="plan", verdict="REJECTED", latency_s=0.8),
        C2CRecord(c2c_id="C03", role="result", verdict="PASS", latency_s=2.5),
        C2CRecord(c2c_id="C04", role="result", verdict="FAIL", latency_s=1.1),
        C2CRecord(c2c_id="C05", role="result", verdict="pass", latency_s=0.9),
    ]

    stats = calculate_c2c_stats(records)
    assert stats.total == 5
    assert stats.plan_total == 2
    assert stats.plan_approved == 1
    assert stats.plan_pass_rate == 0.5

    assert stats.result_total == 3
    assert stats.result_approved == 2
    assert stats.result_pass_rate == 2.0 / 3.0

    assert stats.overall_pass_rate == 3.0 / 5.0

    d = stats.to_dict()
    assert d["total"] == 5
    assert d["plan_approved"] == 1
