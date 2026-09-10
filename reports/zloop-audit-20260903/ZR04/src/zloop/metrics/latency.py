"""Latency distribution metrics and sampling for supervisor and workers.

Provides percentile computation using linear interpolation, latency summarization,
and LatencySampler to track latency distributions for supervisor and worker tasks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any


def compute_percentiles(
    samples: list[float],
    percentiles: list[float] = [50.0, 90.0, 95.0, 99.0],
) -> dict[float, float]:
    """Calculate given percentiles using linear interpolation.

    Args:
        samples: List of numeric latency samples.
        percentiles: List of percentile values (0.0 to 100.0). Defaults to [50.0, 90.0, 95.0, 99.0].

    Returns:
        Mapping from percentile value to interpolated latency value.
        If samples is empty, returns 0.0 for each requested percentile.
    """
    if percentiles is None:
        percentiles = [50.0, 90.0, 95.0, 99.0]

    if not samples:
        return {p: 0.0 for p in percentiles}

    sorted_samples = sorted(float(x) for x in samples)
    n = len(sorted_samples)

    results: dict[float, float] = {}
    for p in percentiles:
        if p <= 0.0:
            results[p] = sorted_samples[0]
            continue
        if p >= 100.0:
            results[p] = sorted_samples[-1]
            continue

        # Linear interpolation between data points:
        # rank r between 0 and n - 1
        rank = (p / 100.0) * (n - 1)
        low_idx = int(math.floor(rank))
        high_idx = int(math.ceil(rank))
        weight = rank - low_idx

        if low_idx == high_idx:
            results[p] = sorted_samples[low_idx]
        else:
            interpolated = sorted_samples[low_idx] * (1.0 - weight) + sorted_samples[high_idx] * weight
            results[p] = interpolated

    return results


@dataclass
class LatencySummary:
    """Summary of latency distribution metrics.

    Attributes:
        count: Total number of samples recorded.
        min_ms: Minimum latency in milliseconds (0.0 if empty).
        max_ms: Maximum latency in milliseconds (0.0 if empty).
        mean_ms: Arithmetic mean latency in milliseconds (0.0 if empty).
        percentiles: Calculated percentiles mapped to their values in milliseconds.
    """

    count: int
    min_ms: float
    max_ms: float
    mean_ms: float
    percentiles: dict[float, float]

    def to_dict(self) -> dict[str, Any]:
        """Convert the summary to a dictionary representation."""
        return asdict(self)


class LatencySampler:
    """Encapsulates latency measurements for supervisor and worker executions."""

    def __init__(self, percentiles: list[float] | None = None) -> None:
        """Initialize sampler with default or custom percentiles.

        Args:
            percentiles: Default percentiles used for summaries, or None for standard defaults.
        """
        self._default_percentiles = percentiles if percentiles is not None else [50.0, 90.0, 95.0, 99.0]
        self._supervisor_samples: list[float] = []
        self._worker_samples: dict[str, list[float]] = {}

    def record_supervisor_latency(self, ms: float) -> None:
        """Record a supervisor latency measurement in milliseconds.

        Args:
            ms: Latency sample in milliseconds.
        """
        self._supervisor_samples.append(float(ms))

    def record_worker_latency(self, worker_id: str, ms: float) -> None:
        """Record a worker latency measurement in milliseconds.

        Args:
            worker_id: Unique identifier for the worker.
            ms: Latency sample in milliseconds.
        """
        if worker_id not in self._worker_samples:
            self._worker_samples[worker_id] = []
        self._worker_samples[worker_id].append(float(ms))

    def _summarize_samples(self, samples: list[float]) -> LatencySummary:
        """Summarize a list of latency samples into a LatencySummary."""
        count = len(samples)
        if count == 0:
            return LatencySummary(
                count=0,
                min_ms=0.0,
                max_ms=0.0,
                mean_ms=0.0,
                percentiles=compute_percentiles([], percentiles=self._default_percentiles),
            )

        min_ms = min(samples)
        max_ms = max(samples)
        mean_ms = sum(samples) / count
        pcts = compute_percentiles(samples, percentiles=self._default_percentiles)

        return LatencySummary(
            count=count,
            min_ms=min_ms,
            max_ms=max_ms,
            mean_ms=mean_ms,
            percentiles=pcts,
        )

    def summarize_supervisor(self) -> LatencySummary:
        """Generate a LatencySummary for supervisor latency recordings."""
        return self._summarize_samples(self._supervisor_samples)

    def summarize_worker(self, worker_id: str) -> LatencySummary:
        """Generate a LatencySummary for a specific worker's recordings.

        Args:
            worker_id: Unique identifier for the worker.

        Returns:
            LatencySummary for the given worker (empty summary if unknown worker).
        """
        samples = self._worker_samples.get(worker_id, [])
        return self._summarize_samples(samples)

    def summarize_all_workers(self) -> LatencySummary:
        """Generate an aggregated LatencySummary across all recorded workers."""
        all_samples: list[float] = []
        for worker_list in self._worker_samples.values():
            all_samples.extend(worker_list)
        return self._summarize_samples(all_samples)
