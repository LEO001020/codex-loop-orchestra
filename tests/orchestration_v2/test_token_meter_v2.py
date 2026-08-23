"""test_token_meter_v2.py — turn-scoped, windowed, hysteresis-driven meter.

Covers: per-agent attribution by dispatch record (not session-wide substring
sweeps), the 5-hour sliding window, hysteresis, real-time share computation,
and Sol/V4/K3/legacy model attribution.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.orchestration_v2.conftest import make_root

from l2_consumer import load_policy
from model_token_share_v2 import (
    MAINTENANCE_MARKERS,
    HysteresisController,
    LedgerRecord,
    MeterV2,
    TokenLedger,
    classify_turn,
    load_policy_models,
)

NOW = 1_000_000.0


@pytest.fixture
def env(tmp_path):
    root = make_root(tmp_path)
    policy = dict(load_policy(root / "config" / "orchestration_policy_v2.toml"))
    policy["models"] = dict(policy["models"])
    # Keep the generic family tests independent of the active runtime profile;
    # shared physical model attribution is covered explicitly below.
    policy["models"]["v4_model"] = "test/v4"
    policy["models"]["k3_model"] = "test/k3"
    # small denominator so unit-scale token counts produce OK windows
    policy["tokens"] = {**policy.get("tokens", {}), "minimum_denominator": 10}
    clock = [NOW]
    meter = MeterV2(root, policy, clock=lambda: clock[0])
    return meter, policy, clock, root


def _record(meter: MeterV2, model: str, tokens: int, *, run_id=None,
            agent="a1", root_sid="root-1", step="s1", text=None, retry=None):
    return meter.record_turn(
        task_id="t1", root_session_id=root_sid, agent_id=agent,
        run_id=run_id, model=model, step_id=step,
        usage={"input_tokens": tokens, "output_tokens": 0},
        user_turn_text=text, retry_reason=retry)


def _models(policy) -> dict[str, str]:
    m = policy["models"]
    return {"sol": m["sol_model"], "v4": m["v4_model"], "k3": m["k3_model"],
            "legacy": m["legacy_aliases"][0]}


# ---------------------------------------------------------------------------
# per-agent attribution (P0-4.4: dispatch record, not substring sweep)
# ---------------------------------------------------------------------------
def test_role_attribution_by_run_role_map(env):
    meter, policy, _, root = env
    models = _models(policy)
    usage = root / "data" / "usage"
    usage.mkdir(parents=True, exist_ok=True)
    (usage / "run_role_map.json").write_text(json.dumps({
        "run-77": {"role": "verifier", "model": models["k3"],
                   "packet_id": "p1"}}))
    assert meter._role_for("run-77", models["k3"]) == "verifier", \
        "the dispatch record wins"
    assert meter._role_for(None, models["k3"]) == "k3", \
        "model-family fallback only for pre-F2 history"
    assert meter._role_for("unknown-run", "never/seen") == "unknown"


def test_classification_is_turn_scoped_never_inherited(env):
    """A maintenance marker classifies only ITS OWN turn; the next turn of
    the same session/agent stays production (kills the session-wide sweep)."""
    meter, policy, _, _ = env
    models = _models(policy)
    _record(meter, models["sol"], 100, step="s1",
            text="smoke: reply exactly OK")
    _record(meter, models["sol"], 100, step="s2",
            text="please do actual work")
    rows = meter.ledger.read()
    classes = {r["step_id"]: r["class"] for r in rows}
    assert classes == {"s1": "maintenance", "s2": "production"}


def test_classify_turn_markers():
    for marker in MAINTENANCE_MARKERS:
        assert classify_turn("prefix %s suffix" % marker) == "maintenance"
    assert classify_turn("normal production request") == "production"
    assert classify_turn(None) == "production"


def test_retry_turns_billed_to_retry_class(env):
    meter, policy, _, _ = env
    _record(meter, _models(policy)["v4"], 500, retry="timeout")
    row = meter.ledger.read()[0]
    assert row["class"] == "retry"
    report = meter.compute_windows()["rolling_5h"]
    assert report.production_effective == 0, \
        "retry storms never pollute the production denominator"


# ---------------------------------------------------------------------------
# 5-hour sliding window
# ---------------------------------------------------------------------------
def test_5h_window_slides(env):
    meter, policy, clock, _ = env
    sol = _models(policy)["sol"]
    clock[0] = NOW - 20_000            # older than 5h (18000s)
    _record(meter, sol, 1_000, step="old")
    clock[0] = NOW - 1_000             # inside 5h
    _record(meter, sol, 500, step="new")
    clock[0] = NOW
    windows = meter.compute_windows()
    assert windows["rolling_5h"].production_effective == 500
    assert windows["rolling_24h"].production_effective == 1_500
    assert windows["cumulative"].production_effective == 1_500


def test_all_five_windows_present(env):
    meter, _, _, _ = env
    windows = meter.compute_windows()
    assert set(windows) == {"rolling_1h", "rolling_5h", "rolling_24h",
                            "rolling_7d", "cumulative"}


def test_denominator_floor_never_actuates(tmp_path):
    root = make_root(tmp_path)
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    policy["models"]["v4_model"] = "test/v4"
    policy["models"]["k3_model"] = "test/k3"
    meter = MeterV2(root, policy, clock=lambda: NOW)  # real 2M floor
    _record(meter, policy["models"]["sol_model"], 100)
    report = meter.compute_windows()["rolling_5h"]
    assert report.status == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# real-time share computation + model attribution
# ---------------------------------------------------------------------------
def test_share_calculation_per_family(env):
    meter, policy, _, _ = env
    models = _models(policy)
    _record(meter, models["sol"], 200, step="s1")
    _record(meter, models["v4"], 600, step="s2")
    _record(meter, models["k3"], 200, step="s3")
    report = meter.compute_windows()["rolling_5h"]
    assert report.status == "OK"
    assert report.shares["sol"] == pytest.approx(0.2)
    if models["v4"] == models["k3"]:
        # Temporary all-K3 routing deliberately collapses the *actual model*
        # families.  Do not manufacture a V4 share from execution-role labels;
        # role-level accounting remains available in the v1/bridge meter.
        assert "v4" not in report.shares
        assert report.shares["k3"] == pytest.approx(0.8)
    else:
        assert report.shares["v4"] == pytest.approx(0.6)
    assert report.shares["k3"] == pytest.approx(0.2)


def test_shared_physical_model_is_attributed_by_role(tmp_path):
    root = make_root(tmp_path)
    policy = dict(load_policy(root / "config" / "orchestration_policy_v2.toml"))
    policy["models"] = dict(policy["models"])
    shared = "provider-c/shared-model"
    policy["models"]["v4_model"] = shared
    policy["models"]["k3_model"] = shared
    policy["tokens"] = {**policy.get("tokens", {}), "minimum_denominator": 10}
    meter = MeterV2(root, policy, clock=lambda: NOW)
    assert meter.family_of(shared) == "shared"
    meter.ledger.append(LedgerRecord(
        ts=NOW, task_id="t", root_session_id="r", agent_id="w",
        role="worker", model=shared, step_id="worker", input_tokens=600))
    meter.ledger.append(LedgerRecord(
        ts=NOW, task_id="t", root_session_id="r", agent_id="v",
        role="verifier", model=shared, step_id="verifier", input_tokens=400))
    meter.ledger.append(LedgerRecord(
        ts=NOW, task_id="t", root_session_id="r", agent_id="old",
        role="unknown", model=shared, step_id="history", input_tokens=100))
    report = meter.compute_windows()["rolling_5h"]
    assert report.shares["v4"] == pytest.approx(600 / 1100)
    assert report.shares["k3"] == pytest.approx(400 / 1100)
    assert report.shares["shared"] == pytest.approx(100 / 1100)


def test_legacy_alias_quarantined_never_counted_as_k3(env):
    """gpt-5.6-terra-style aliases land in a quarantined 'legacy' bucket —
    K3's band is never polluted by Sol-family tokens (§2.4 AC4)."""
    meter, policy, _, _ = env
    models = _models(policy)
    _record(meter, models["legacy"], 500, step="s1")
    _record(meter, models["k3"], 500, step="s2")
    report = meter.compute_windows()["rolling_5h"]
    assert report.shares["legacy"] == pytest.approx(0.5)
    assert report.shares["k3"] == pytest.approx(0.5)
    assert meter.family_of(models["legacy"]) == "legacy"


def test_cached_input_excluded_from_effective(env):
    meter, policy, _, _ = env
    meter.record_turn(task_id="t", root_session_id="r", agent_id="a",
                      run_id=None, model=_models(policy)["sol"], step_id="s",
                      usage={"input_tokens": 1_000, "cached_input_tokens": 900,
                             "output_tokens": 100})
    report = meter.compute_windows()["rolling_5h"]
    assert report.production_effective == 200, "effective = total - cached"


def test_critical_root_flagged_in_1h_window(env):
    meter, policy, _, _ = env
    sol = _models(policy)["sol"]
    _record(meter, sol, 800, root_sid="runaway", step="s1")
    _record(meter, _models(policy)["v4"], 200, root_sid="calm", step="s2")
    report = meter.compute_windows()["rolling_1h"]
    assert "runaway" in report.critical_roots, "per-root 1h gating signal"


def test_missing_model_pin_fails_closed():
    with pytest.raises(RuntimeError, match="fail closed"):
        load_policy_models({"models": {"sol_model": "x", "v4_model": ""}})


def test_inactive_execution_profiles_stay_in_execution_family(env):
    _meter, policy, _clock, _root = env
    mapping = load_policy_models(policy)
    for alias in policy["models"]["execution_aliases"]:
        assert mapping[alias.lower()] == "v4"


# ---------------------------------------------------------------------------
# ledger idempotency
# ---------------------------------------------------------------------------
def test_ledger_dedups_by_semantic_key(tmp_path):
    ledger = TokenLedger(tmp_path / "ledger.ndjsonl")
    rec = LedgerRecord(ts=NOW, task_id="t", root_session_id="r", agent_id="a",
                       role="worker", model="m", step_id="s1",
                       input_tokens=100)
    assert ledger.append(rec) is True
    assert ledger.append(rec) is False, "duplicate idempotency key dropped"
    assert len(ledger.read()) == 1


def test_ledger_rejects_illegal_class(tmp_path):
    ledger = TokenLedger(tmp_path / "ledger.ndjsonl")
    rec = LedgerRecord(ts=NOW, task_id="t", root_session_id="r", agent_id="a",
                       role="w", model="m", step_id="s",
                       token_class="mystery")
    with pytest.raises(ValueError):
        ledger.append(rec)


# ---------------------------------------------------------------------------
# hysteresis
# ---------------------------------------------------------------------------
def test_hysteresis_ac3_sequence():
    ctl = HysteresisController(enter_high=0.25, enter_samples=2,
                               leave_high=0.22, leave_samples=2)
    states = [ctl.sample(s) for s in (0.26, 0.26, 0.23, 0.21, 0.21)]
    assert states == ["NORMAL", "HIGH", "HIGH", "HIGH", "NORMAL"], \
        "goes HIGH at sample 2 and exits at sample 5, no flapping"


def test_hysteresis_no_single_spike_trigger():
    ctl = HysteresisController(enter_high=0.25, enter_samples=2)
    assert ctl.sample(0.90) == "NORMAL", "one spike never actuates"
    assert ctl.sample(0.10) == "NORMAL"
    assert ctl.sample(0.90) == "NORMAL", "non-consecutive spikes reset"


def test_hysteresis_persists_via_policy_roundtrip(env):
    meter, policy, _, _ = env
    ctl = HysteresisController.from_policy(policy)
    ctl.sample(0.30)
    restored = HysteresisController.from_policy(policy, ctl.to_dict())
    assert restored.sample(0.30) == "HIGH", "persisted counters carry over"


# ---------------------------------------------------------------------------
# event-driven refresh + staleness
# ---------------------------------------------------------------------------
def test_refresh_writes_report_with_manifest(env):
    meter, policy, _, _ = env
    _record(meter, _models(policy)["sol"], 100)
    report = meter.refresh(force=True)
    assert report["schema"] == "codex-loop-token-share/v2"
    assert report["primary_window"] == "rolling_5h"
    assert report["input_manifest"]["rows"] == 1
    assert report["input_manifest"]["sha256"], "frozen manifest for replay"


def test_refresh_debounced(env):
    meter, policy, clock, _ = env
    _record(meter, _models(policy)["sol"], 100)
    assert meter.refresh(force=True) is not None
    clock[0] += 10  # inside the 60s debounce
    assert meter.refresh() is None
    clock[0] += 60
    assert meter.refresh() is not None


def test_report_self_labels_stale(env):
    meter, policy, clock, _ = env
    _record(meter, _models(policy)["sol"], 100)
    meter.refresh(force=True)
    assert meter.read_fresh_report()["status"] == "FRESH"
    clock[0] += meter.stale_after_s + 1
    report = meter.read_fresh_report()
    assert report["status"] == "STALE", "never two truths again (§2.4 AC5)"
    signal = meter.budget_signal()
    assert signal["actuate"] is False, "STALE must be fail-closed by callers"


def test_budget_signal_actuates_only_when_fresh_and_sufficient(env):
    meter, policy, _, _ = env
    _record(meter, _models(policy)["sol"], 100)
    meter.refresh(force=True)
    signal = meter.budget_signal()
    assert signal["status"] == "FRESH"
    assert signal["actuate"] is True
