"""zloop.metrics.tokens — S0/S1 Token reduction and cost savings metrics.

Provides pure functions to calculate token reduction ratios, cost savings,
and structured token evaluation reports.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict


def calculate_token_reduction(s0_tokens: int, s1_tokens: int) -> float:
    """Calculate the token reduction ratio from S0 to S1.

    Returns:
        (s0 - s1) / s0 if s0 > 0 else 0.0.

    Raises:
        ValueError: If s0_tokens or s1_tokens is negative.
    """
    if s0_tokens < 0 or s1_tokens < 0:
        raise ValueError("Token counts cannot be negative")
    if s0_tokens == 0:
        return 0.0
    return (s0_tokens - s1_tokens) / s0_tokens


def calculate_cost_savings(
    s0_tokens: int, s1_tokens: int, price_per_1k: float = 0.002
) -> float:
    """Calculate the estimated cost savings in USD.

    Returns:
        max(0.0, (s0 - s1) / 1000.0 * price_per_1k).

    Raises:
        ValueError: If s0_tokens or s1_tokens is negative.
    """
    if s0_tokens < 0 or s1_tokens < 0:
        raise ValueError("Token counts cannot be negative")
    return max(0.0, (s0_tokens - s1_tokens) / 1000.0 * price_per_1k)


@dataclass(frozen=True)
class TokenReport:
    """Evaluation report containing token counts, reduction ratio, and cost savings."""

    s0_tokens: int
    s1_tokens: int
    reduction_ratio: float
    estimated_savings_usd: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert the report to a dictionary."""
        return asdict(self)


def generate_token_report(
    s0_tokens: int, s1_tokens: int, price_per_1k: float = 0.002
) -> TokenReport:
    """Generate a TokenReport from S0 and S1 token counts and pricing.

    Raises:
        ValueError: If s0_tokens or s1_tokens is negative.
    """
    reduction_ratio = calculate_token_reduction(s0_tokens, s1_tokens)
    estimated_savings = calculate_cost_savings(
        s0_tokens, s1_tokens, price_per_1k=price_per_1k
    )
    return TokenReport(
        s0_tokens=s0_tokens,
        s1_tokens=s1_tokens,
        reduction_ratio=reduction_ratio,
        estimated_savings_usd=estimated_savings,
    )
