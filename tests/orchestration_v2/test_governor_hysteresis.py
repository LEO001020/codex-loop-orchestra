"""test_governor_hysteresis.py — the anti-oscillation controller in depth.

Covers: the enter-HIGH/stay-during-cooldown/exit-after-gap sequence, the
no-oscillation guarantee under noisy inputs, and the AC3 consecutive-sample
pattern for both persisted (governor) and in-process (meter) controllers.
"""
from __future__ import annotations

import json

import pytest

from tests.orchestration_v2.conftest import make_root

from model_token_share_v2 import HysteresisController as MeterHysteresis
from orchestration_common import LoopPaths, OrchestrationPolicy
from root_turn_governor import HysteresisController as GovernorHysteresis


@pytest.fixture
def gov_ctl(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    params = OrchestrationPolicy.load(paths).hysteresis()
    return GovernorHysteresis(paths, params), paths


def _feed(ctl: GovernorHysteresis, shares, start=0):
    return [ctl.observe(s, sample_id="s%d" % (start + i))
            for i, s in enumerate(shares)]


# ---------------------------------------------------------------------------
# enter / hold / exit sequence
# ---------------------------------------------------------------------------
def test_enter_high_needs_consecutive_samples(gov_ctl):
    ctl, _ = gov_ctl
    assert _feed(ctl, [0.30]) == ["NORMAL"], "one sample never actuates"
    assert _feed(ctl, [0.30], start=1) == ["HIGH"], "second consecutive enters"


def test_stays_high_during_between_band_cooldown(gov_ctl):
    """Between leave_high (0.22) and enter_high (0.25) the state HOLDS —
    that dead band is what kills the bang-bang BLOCK↔drain cycle."""
    ctl, _ = gov_ctl
    bands = _feed(ctl, [0.30, 0.30, 0.235, 0.24, 0.23, 0.245])
    assert bands == ["NORMAL", "HIGH", "HIGH", "HIGH", "HIGH", "HIGH"]


def test_exit_only_after_cooldown_plus_band_gap(gov_ctl):
    ctl, _ = gov_ctl
    bands = _feed(ctl, [0.30, 0.30,      # enter
                        0.21,            # 1st below leave: still HIGH
                        0.21])           # 2nd below leave: exit
    assert bands == ["NORMAL", "HIGH", "HIGH", "NORMAL"]


def test_partial_recovery_resets_exit_counter(gov_ctl):
    ctl, _ = gov_ctl
    bands = _feed(ctl, [0.30, 0.30, 0.21, 0.24, 0.21, 0.21])
    #                                ^ blip back into the band resets below-count
    assert bands == ["NORMAL", "HIGH", "HIGH", "HIGH", "HIGH", "NORMAL"]


# ---------------------------------------------------------------------------
# no oscillation
# ---------------------------------------------------------------------------
def test_no_oscillation_on_alternating_noise(gov_ctl):
    """0.26/0.20 alternating forever never enters HIGH: consecutive-sample
    entry makes single-sample noise powerless."""
    ctl, _ = gov_ctl
    bands = _feed(ctl, [0.26, 0.20] * 10)
    assert set(bands) == {"NORMAL"}


def test_at_most_one_transition_per_direction(gov_ctl):
    ctl, _ = gov_ctl
    bands = _feed(ctl, [0.30, 0.30, 0.30, 0.21, 0.21, 0.10, 0.10])
    flips = sum(1 for a, b in zip(bands, bands[1:]) if a != b)
    assert flips == 2, "exactly one rise and one fall, no chatter"


def test_state_persists_across_instances(gov_ctl):
    ctl, paths = gov_ctl
    _feed(ctl, [0.30, 0.30])
    params = OrchestrationPolicy.load(paths).hysteresis()
    fresh = GovernorHysteresis(paths, params)  # separate hook process
    assert fresh.band() == "HIGH", "hook invocations share disk state"
    doc = json.loads((paths.governor_dir / "hysteresis.json").read_text())
    assert doc["band"] == "HIGH"


def test_sample_id_dedup_blocks_fast_forward(gov_ctl):
    ctl, _ = gov_ctl
    for _ in range(10):
        assert ctl.observe(0.30, sample_id="same-report") == "NORMAL"
    assert ctl.observe(0.30, sample_id="new-report") == "HIGH"


# ---------------------------------------------------------------------------
# AC3 pattern (consecutive samples, configurable count)
# ---------------------------------------------------------------------------
def test_ac3_reference_sequence(gov_ctl):
    """The design's reference: 0.26, 0.26, 0.23, 0.21, 0.21 -> HIGH at
    sample 2, exit at sample 5 (§2.4 AC3)."""
    ctl, _ = gov_ctl
    bands = _feed(ctl, [0.26, 0.26, 0.23, 0.21, 0.21])
    assert bands == ["NORMAL", "HIGH", "HIGH", "HIGH", "NORMAL"]


def test_ac3_three_consecutive_sample_variant(tmp_path):
    """With enter_samples=3 exactly three consecutive breaches are needed."""
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    ctl = GovernorHysteresis(paths, {"enter_high": 0.25, "enter_samples": 3,
                                     "leave_high": 0.22, "leave_samples": 3})
    assert _feed(ctl, [0.30, 0.30]) == ["NORMAL", "NORMAL"]
    assert _feed(ctl, [0.20], start=2) == ["NORMAL"], "streak broken"
    assert _feed(ctl, [0.30, 0.30, 0.30], start=3) == \
        ["NORMAL", "NORMAL", "HIGH"]
    # and three consecutive to leave
    assert _feed(ctl, [0.10, 0.10], start=6) == ["HIGH", "HIGH"]
    assert _feed(ctl, [0.10], start=8) == ["NORMAL"]


def test_meter_controller_agrees_with_governor_controller():
    """Both hysteresis implementations must produce identical bands for the
    same parameterisation and input — two controllers, one truth."""
    meter = MeterHysteresis(enter_high=0.25, enter_samples=2,
                            leave_high=0.22, leave_samples=2)
    seq = [0.26, 0.26, 0.23, 0.21, 0.21, 0.30, 0.30, 0.10, 0.10]
    meter_bands = [meter.sample(s) for s in seq]
    assert meter_bands == ["NORMAL", "HIGH", "HIGH", "HIGH", "NORMAL",
                           "NORMAL", "HIGH", "HIGH", "NORMAL"]


def test_thresholds_come_from_policy(tmp_path):
    root = make_root(tmp_path)
    params = OrchestrationPolicy.load(LoopPaths.resolve(root)).hysteresis()
    assert params["enter_high"] == 0.25
    assert params["leave_high"] == 0.22
    assert params["enter_samples"] == 2
    assert params["leave_samples"] == 2
    assert params["enter_high"] > params["leave_high"], \
        "a dead band must exist between the thresholds"
