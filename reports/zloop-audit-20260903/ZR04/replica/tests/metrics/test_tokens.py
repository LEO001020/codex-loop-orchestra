"""Tests for zloop.metrics.tokens."""
from __future__ import annotations

import pytest

from zloop.metrics.tokens import (
    TokenReport,
    calculate_cost_savings,
    calculate_token_reduction,
    generate_token_report,
)


def test_calculate_token_reduction():
    assert calculate_token_reduction(1000, 500) == 0.5
    assert calculate_token_reduction(1000, 1000) == 0.0
    assert calculate_token_reduction(1000, 0) == 1.0
    assert calculate_token_reduction(0, 100) == 0.0

    with pytest.raises(ValueError, match="Token counts cannot be negative"):
        calculate_token_reduction(-1, 100)

    with pytest.raises(ValueError, match="Token counts cannot be negative"):
        calculate_token_reduction(100, -1)


def test_calculate_cost_savings():
    # 1000 - 500 = 500 tokens -> 0.5k * 0.002 = 0.001
    assert calculate_cost_savings(1000, 500) == pytest.approx(0.001)
    assert calculate_cost_savings(500, 1000) == 0.0  # s0 < s1 clamped to 0.0
    assert calculate_cost_savings(0, 0) == 0.0

    with pytest.raises(ValueError, match="Token counts cannot be negative"):
        calculate_cost_savings(-10, 10)


def test_generate_token_report():
    report = generate_token_report(26856, 12520, price_per_1k=0.002)
    assert isinstance(report, TokenReport)
    assert report.s0_tokens == 26856
    assert report.s1_tokens == 12520
    assert report.reduction_ratio == pytest.approx((26856 - 12520) / 26856)
    assert report.estimated_savings_usd == pytest.approx((26856 - 12520) / 1000.0 * 0.002)

    d = report.to_dict()
    assert d["s0_tokens"] == 26856
    assert d["s1_tokens"] == 12520
    assert "reduction_ratio" in d
    assert "estimated_savings_usd" in d
