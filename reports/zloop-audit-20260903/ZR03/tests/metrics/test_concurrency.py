"""Unit tests for concurrency metrics."""

import pytest
from zloop.metrics.concurrency import (
    ConcurrencyReport,
    WorkerInterval,
    analyze_concurrency,
    compute_instantaneous_concurrency,
    compute_overlap_ratio,
)


def test_worker_interval_valid() -> None:
    w = WorkerInterval(worker_id="w1", start_time=10.0, end_time=20.0)
    assert w.worker_id == "w1"
    assert w.start_time == 10.0
    assert w.end_time == 20.0


def test_worker_interval_zero_duration() -> None:
    w = WorkerInterval(worker_id="w2", start_time=5.0, end_time=5.0)
    assert w.start_time == 5.0
    assert w.end_time == 5.0


def test_worker_interval_invalid() -> None:
    with pytest.raises(ValueError, match="cannot be greater than end_time"):
        WorkerInterval(worker_id="w3", start_time=25.0, end_time=20.0)


def test_compute_instantaneous_concurrency_empty() -> None:
    assert compute_instantaneous_concurrency([]) == []


def test_compute_instantaneous_concurrency_single() -> None:
    intervals = [WorkerInterval(worker_id="w1", start_time=0.0, end_time=10.0)]
    timeline = compute_instantaneous_concurrency(intervals)
    assert timeline == [(0.0, 1), (10.0, 0)]


def test_compute_instantaneous_concurrency_overlapping() -> None:
    intervals = [
        WorkerInterval(worker_id="w1", start_time=0.0, end_time=10.0),
        WorkerInterval(worker_id="w2", start_time=5.0, end_time=15.0),
    ]
    timeline = compute_instantaneous_concurrency(intervals)
    assert timeline == [(0.0, 1), (5.0, 2), (10.0, 1), (15.0, 0)]


def test_compute_instantaneous_concurrency_concurrent_boundaries() -> None:
    # w1 ends at 10.0, w2 starts at 10.0
    intervals = [
        WorkerInterval(worker_id="w1", start_time=0.0, end_time=10.0),
        WorkerInterval(worker_id="w2", start_time=10.0, end_time=20.0),
    ]
    timeline = compute_instantaneous_concurrency(intervals)
    assert timeline == [(0.0, 1), (10.0, 1), (20.0, 0)]


def test_compute_instantaneous_concurrency_zero_length() -> None:
    intervals = [
        WorkerInterval(worker_id="w1", start_time=5.0, end_time=5.0),
    ]
    timeline = compute_instantaneous_concurrency(intervals)
    assert timeline == [(5.0, 0)]


def test_compute_overlap_ratio_empty_and_zero_makespan() -> None:
    assert compute_overlap_ratio([], target_threshold=8) == 0.0
    assert compute_overlap_ratio([WorkerInterval("w1", 5.0, 5.0)], target_threshold=1) == 0.0


def test_compute_overlap_ratio_below_threshold() -> None:
    intervals = [
        WorkerInterval(worker_id=f"w{i}", start_time=0.0, end_time=10.0)
        for i in range(5)
    ]
    # target_threshold is 8, active is 5 -> overlap is 0.0
    assert compute_overlap_ratio(intervals, target_threshold=8) == 0.0


def test_compute_overlap_ratio_partial_and_full() -> None:
    # 8 workers active from 0 to 10.
    intervals_full = [
        WorkerInterval(worker_id=f"w{i}", start_time=0.0, end_time=10.0)
        for i in range(8)
    ]
    assert compute_overlap_ratio(intervals_full, target_threshold=8) == 1.0

    # 8 workers active from 2 to 8, makespan is 0 to 10 (makespan = 10, overlap duration = 6)
    intervals_partial = [
        WorkerInterval(worker_id="slow", start_time=0.0, end_time=10.0),
    ] + [
        WorkerInterval(worker_id=f"w{i}", start_time=2.0, end_time=8.0)
        for i in range(7)
    ]
    # active counts: [0, 2): 1, [2, 8): 8, [8, 10): 1.
    # threshold 8: 6.0 seconds out of 10.0 => 0.6
    assert pytest.approx(compute_overlap_ratio(intervals_partial, target_threshold=8)) == 0.6


def test_concurrency_report_to_dict() -> None:
    rep = ConcurrencyReport(
        peak_concurrency=8,
        average_concurrency=4.5,
        overlap_ratio=0.5,
        makespan_seconds=20.0,
    )
    d = rep.to_dict()
    assert d == {
        "peak_concurrency": 8,
        "average_concurrency": 4.5,
        "overlap_ratio": 0.5,
        "makespan_seconds": 20.0,
    }


def test_analyze_concurrency_empty() -> None:
    rep = analyze_concurrency([])
    assert rep.peak_concurrency == 0
    assert rep.average_concurrency == 0.0
    assert rep.overlap_ratio == 0.0
    assert rep.makespan_seconds == 0.0


def test_analyze_concurrency_zero_makespan() -> None:
    rep = analyze_concurrency([WorkerInterval("w1", 5.0, 5.0)])
    assert rep.peak_concurrency == 0
    assert rep.average_concurrency == 0.0
    assert rep.overlap_ratio == 0.0
    assert rep.makespan_seconds == 0.0


def test_analyze_concurrency_staggered() -> None:
    # 2 workers: w1 [0, 10], w2 [5, 15]
    # timeline: [0, 5): 1 worker (5s * 1 = 5)
    #           [5, 10): 2 workers (5s * 2 = 10)
    #           [10, 15): 1 worker (5s * 1 = 5)
    # total worker time = 20, makespan = 15 => avg = 20/15 = 4/3 ~ 1.333333
    # peak = 2
    # overlap_ratio (threshold=2): 5s / 15s = 1/3 ~ 0.333333
    # overlap_ratio (threshold=8, default): 0.0
    intervals = [
        WorkerInterval(worker_id="w1", start_time=0.0, end_time=10.0),
        WorkerInterval(worker_id="w2", start_time=5.0, end_time=15.0),
    ]
    rep = analyze_concurrency(intervals, target_threshold=2)
    assert rep.peak_concurrency == 2
    assert pytest.approx(rep.average_concurrency) == 20.0 / 15.0
    assert pytest.approx(rep.overlap_ratio) == 5.0 / 15.0
    assert pytest.approx(rep.makespan_seconds) == 15.0

    rep_default = analyze_concurrency(intervals)
    assert rep_default.overlap_ratio == 0.0
