"""Concurrency analysis and metrics for worker intervals."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class WorkerInterval:
    """Represents the active time interval of a worker.

    Attributes:
        worker_id: Unique identifier of the worker.
        start_time: Interval start timestamp.
        end_time: Interval end timestamp (must be >= start_time).
    """

    worker_id: str
    start_time: float
    end_time: float

    def __post_init__(self) -> None:
        if self.start_time > self.end_time:
            raise ValueError(
                f"start_time ({self.start_time}) cannot be greater than end_time ({self.end_time})"
            )


@dataclass(frozen=True)
class ConcurrencyReport:
    """Summary report of concurrency metrics across worker intervals.

    Attributes:
        peak_concurrency: Maximum number of concurrently active workers.
        average_concurrency: Time-weighted average number of concurrent workers over the makespan.
        overlap_ratio: Fraction of total makespan during which at least target_threshold workers are active.
        makespan_seconds: Total duration from earliest start to latest end time across all workers.
    """

    peak_concurrency: int
    average_concurrency: float
    overlap_ratio: float
    makespan_seconds: float

    def to_dict(self) -> dict:
        """Convert the report dataclass to a dictionary."""
        return asdict(self)


def compute_instantaneous_concurrency(
    intervals: list[WorkerInterval],
) -> list[tuple[float, int]]:
    """Compute timeline of instantaneous active worker counts sorted by timestamp.

    Boundary conventions:
    At timestamp t where one or more intervals start or end:
    Starts increment the active count (+1) and ends decrement the active count (-1).
    All transitions at timestamp t are aggregated into a single entry reflecting
    the instantaneous active count at/immediately following time t.

    Returns:
        List of (timestamp, active_count) tuples sorted chronologically.
    """
    if not intervals:
        return []

    delta_by_time: dict[float, int] = {}
    for interval in intervals:
        # If start_time == end_time (instantaneous interval), start +1 and end -1 at the same timestamp
        delta_by_time[interval.start_time] = delta_by_time.get(interval.start_time, 0) + 1
        delta_by_time[interval.end_time] = delta_by_time.get(interval.end_time, 0) - 1

    sorted_times = sorted(delta_by_time.keys())
    timeline: list[tuple[float, int]] = []
    current_count = 0

    for t in sorted_times:
        current_count += delta_by_time[t]
        timeline.append((t, current_count))

    return timeline


def compute_overlap_ratio(
    intervals: list[WorkerInterval], target_threshold: int = 8
) -> float:
    """Calculate the fraction of total makespan with at least target_threshold active workers.

    Makespan is defined as max(end_time) - min(start_time).
    Returns 0.0 if makespan <= 0 or if intervals is empty.

    Args:
        intervals: List of worker intervals.
        target_threshold: Minimum active worker count threshold.

    Returns:
        Fraction in range [0.0, 1.0].
    """
    if not intervals:
        return 0.0

    min_start = min(interval.start_time for interval in intervals)
    max_end = max(interval.end_time for interval in intervals)
    makespan = max_end - min_start

    if makespan <= 0:
        return 0.0

    timeline = compute_instantaneous_concurrency(intervals)
    qualifying_duration = 0.0

    for i in range(len(timeline) - 1):
        t_curr, active_count = timeline[i]
        t_next, _ = timeline[i + 1]
        if active_count >= target_threshold:
            qualifying_duration += t_next - t_curr

    return qualifying_duration / makespan


def analyze_concurrency(
    intervals: list[WorkerInterval], target_threshold: int = 8
) -> ConcurrencyReport:
    """Analyze worker intervals and generate a ConcurrencyReport.

    Computes peak concurrency, average concurrency (time-weighted over makespan),
    overlap ratio at target_threshold, and total makespan in seconds.

    Args:
        intervals: List of worker intervals.
        target_threshold: Minimum active worker count threshold for overlap ratio.

    Returns:
        ConcurrencyReport instance.
    """
    if not intervals:
        return ConcurrencyReport(
            peak_concurrency=0,
            average_concurrency=0.0,
            overlap_ratio=0.0,
            makespan_seconds=0.0,
        )

    min_start = min(interval.start_time for interval in intervals)
    max_end = max(interval.end_time for interval in intervals)
    makespan = max_end - min_start

    if makespan <= 0:
        return ConcurrencyReport(
            peak_concurrency=0,
            average_concurrency=0.0,
            overlap_ratio=0.0,
            makespan_seconds=0.0,
        )

    timeline = compute_instantaneous_concurrency(intervals)
    peak = max(count for _, count in timeline) if timeline else 0

    # Calculate time-weighted average concurrency over makespan
    # Total area under concurrency curve divided by makespan
    total_worker_time = sum(
        (timeline[i + 1][0] - timeline[i][0]) * timeline[i][1]
        for i in range(len(timeline) - 1)
    )
    avg = total_worker_time / makespan

    overlap = compute_overlap_ratio(intervals, target_threshold=target_threshold)

    return ConcurrencyReport(
        peak_concurrency=peak,
        average_concurrency=avg,
        overlap_ratio=overlap,
        makespan_seconds=makespan,
    )
