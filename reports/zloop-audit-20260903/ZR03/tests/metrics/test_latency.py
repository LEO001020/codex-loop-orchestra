"""Tests for zloop.metrics.latency."""
from __future__ import annotations

import pytest

from zloop.metrics.latency import (
    LatencySampler,
    LatencySummary,
    compute_percentiles,
)


def test_compute_percentiles_empty():
    res = compute_percentiles([], [50.0, 95.0])
    assert res == {50.0: 0.0, 95.0: 0.0}


def test_compute_percentiles_values():
    samples = [10.0, 20.0, 30.0, 40.0, 50.0]
    p = compute_percentiles(samples, [0.0, 50.0, 100.0])
    assert p[0.0] == 10.0
    assert p[50.0] == 30.0
    assert p[100.0] == 50.0


def test_latency_sampler_supervisor_and_worker():
    sampler = LatencySampler(percentiles=[50.0, 99.0])
    sampler.record_supervisor_latency(15.0)
    sampler.record_supervisor_latency(25.0)

    sup_summary = sampler.summarize_supervisor()
    assert isinstance(sup_summary, LatencySummary)
    assert sup_summary.count == 2
    assert sup_summary.min_ms == 15.0
    assert sup_summary.max_ms == 25.0
    assert sup_summary.mean_ms == 20.0
    assert sup_summary.percentiles[50.0] == 20.0

    sampler.record_worker_latency("w1", 100.0)
    sampler.record_worker_latency("w1", 200.0)
    sampler.record_worker_latency("w2", 300.0)

    w1_summary = sampler.summarize_worker("w1")
    assert w1_summary.count == 2
    assert w1_summary.mean_ms == 150.0

    w_unknown = sampler.summarize_worker("unknown")
    assert w_unknown.count == 0
    assert w_unknown.min_ms == 0.0

    all_w = sampler.summarize_all_workers()
    assert all_w.count == 3
    assert all_w.min_ms == 100.0
    assert all_w.max_ms == 300.0
    assert all_w.mean_ms == 200.0
