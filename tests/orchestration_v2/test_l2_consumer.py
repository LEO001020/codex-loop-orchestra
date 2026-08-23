"""test_l2_consumer.py — exactly-once send_l2 → K3 consumer mechanics.

Covers: atomic claim creation, idempotency-key uniqueness, claim heartbeat,
stale-claim reaping, completion markers, crash-after-claim recovery,
crash-after-model-before-publish recovery, restart recovery, and the
one-send_l2 → exactly-one-execution guarantee.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.orchestration_v2.conftest import good_short_result, make_root, read_events

from l2_consumer import L2Consumer, L2Record, load_policy, make_idem_key


@pytest.fixture
def env(tmp_path):
    """(consumer, dispatched-keys list, mutable clock) in an isolated root."""
    root = make_root(tmp_path)
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    dispatched: list[str] = []
    clock = [1_000_000.0]

    def recorder(rec: L2Record) -> bool:
        dispatched.append(rec.idem_key)
        return True

    consumer = L2Consumer(root, policy=policy, dispatcher=recorder,
                          clock=lambda: clock[0])
    return consumer, dispatched, clock, root


# ---------------------------------------------------------------------------
# idempotency keys
# ---------------------------------------------------------------------------
def test_idem_key_stable_and_unique():
    k1 = make_idem_key("pkt-1", "run-1", 1)
    assert k1 == make_idem_key("pkt-1", "run-1", 1), "same semantics, same key"
    assert k1 != make_idem_key("pkt-1", "run-1", 2), "new attempt = new key"
    assert k1 != make_idem_key("pkt-1", "run-2", 1), "new run = new key"
    assert re.match(r"^k3:l2req:pkt-1:[a-z2-7]{16}$", k1)
    # the consumer's key MUST agree with the producer-side key from
    # orchestration_common.idem_key (trigger_eval/agent_router) so one
    # semantic request maps to ONE claim regardless of producer.
    from orchestration_common import idem_key as common_idem_key
    assert k1 == common_idem_key("l2req", "pkt-1", "run-1", "1")


def test_idem_key_rejects_unsafe_packet_id():
    with pytest.raises(ValueError):
        make_idem_key("../evil", "run-1", 1)


def test_enqueue_is_idempotent(env):
    consumer, _, _, _ = env
    assert consumer.enqueue("p1", "r1", 1) is not None
    assert consumer.enqueue("p1", "r1", 1) is None, \
        "duplicate semantic request produces zero new records"
    assert len(list(consumer.iter_pending())) == 1


# ---------------------------------------------------------------------------
# atomic claim
# ---------------------------------------------------------------------------
def test_atomic_claim_creation(env):
    consumer, _, _, _ = env
    rec = consumer.enqueue("p1", "r1", 1)
    path = consumer.try_claim(rec)
    assert path is not None and path.exists()
    claim = json.loads(path.read_text(encoding="utf-8"))
    assert claim["idem_key"] == rec.idem_key
    assert claim["heartbeat_ts"] == claim["claimed_ts"]
    # second claim attempt loses the O_CREAT|O_EXCL race deterministically
    assert consumer.try_claim(rec) is None


def test_claim_heartbeat(env):
    consumer, _, clock, _ = env
    rec = consumer.enqueue("p1", "r1", 1)
    path = consumer.try_claim(rec)
    before = json.loads(path.read_text())["heartbeat_ts"]
    clock[0] += 42
    assert consumer.heartbeat(rec.idem_key) is True
    after = json.loads(path.read_text())["heartbeat_ts"]
    assert after == before + 42
    assert consumer.heartbeat("k3:l2req:ghost:AAAAAAAAAAAAAAAA") is False


# ---------------------------------------------------------------------------
# exactly-once
# ---------------------------------------------------------------------------
def test_exactly_once_one_send_one_execution(env):
    """One send_l2 → exactly one K3 execution, no matter how often we drain."""
    consumer, dispatched, _, _ = env
    for _ in range(3):  # duplicate producer calls collapse to one record
        consumer.enqueue("p1", "r1", 1)
    for _ in range(4):  # concurrent/repeated drains collapse to one claim
        consumer.drain()
    assert dispatched == [make_idem_key("p1", "r1", 1)]


def test_drain_emits_t30_event_and_k3_demand(env):
    consumer, _, _, root = env
    consumer.enqueue("p1", "r1", 1)
    stats = consumer.drain()
    assert stats.claimed == 1 and stats.dispatched == 1
    events = read_events(root)
    assert any(e["event"] == "l2_requested" and e["packet_id"] == "p1"
               for e in events), "claim must emit the t30 producer event"
    demand = (root / "data" / "refill" / "k3_demand.ndjsonl").read_text()
    assert '"pool": "k3"' in demand or '"pool":"k3"' in demand.replace(" ", "")


def test_provider_backoff_preserves_pending_without_claim_or_demand(env):
    consumer, dispatched, clock, root = env
    rec = consumer.enqueue("p1", "r1", 1)
    from provider_health import health_path
    health = health_path(root, consumer.verifier_model)
    health.parent.mkdir(parents=True)
    health.write_text(json.dumps({
        "status": "unhealthy", "backoff_until": clock[0] + 60,
        "model": consumer.verifier_model, "ts": clock[0],
    }), encoding="utf-8")

    stats = consumer.drain()
    assert stats.provider_backoff == 1 and stats.scanned == 1
    assert dispatched == []
    assert not consumer._claim_path(rec.idem_key).exists()
    assert not (root / "data/refill/k3_demand.ndjsonl").exists()

    clock[0] += 61
    stats = consumer.drain()
    assert stats.dispatched == 1 and dispatched == [rec.idem_key]


def test_run_canary_green():
    result = L2Consumer.run_canary()
    assert result.ok, result.detail
    assert result.dispatches == 4  # 3 first-run + exactly 1 reclaim


# ---------------------------------------------------------------------------
# completion marker
# ---------------------------------------------------------------------------
def _verifier_report(tmp: Path, **over) -> Path:
    doc = good_short_result(verdict="pass", **over)
    path = tmp / "report.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_completion_marker_valid_report(env, tmp_path):
    consumer, dispatched, _, root = env
    rec = consumer.enqueue("pkt-1", "r1", 1)
    consumer.drain()
    report = _verifier_report(tmp_path)
    result = consumer.complete(rec.idem_key, report, expected_revision=2)
    assert result.ok
    comp = consumer._completion_path(rec.idem_key)
    assert comp.exists()
    marker = json.loads(comp.read_text())
    assert marker["valid"] is True and marker["verdict"] == "pass"
    assert not consumer._claim_path(rec.idem_key).exists(), \
        "claim is removed once the completion is published"
    assert any(e["event"] == "verdict_pass" for e in read_events(root))
    # completed keys can never be re-dispatched
    n = len(dispatched)
    consumer.drain()
    assert len(dispatched) == n


def test_completion_is_idempotent(env, tmp_path):
    consumer, _, _, _ = env
    rec = consumer.enqueue("pkt-1", "r1", 1)
    consumer.drain()
    report = _verifier_report(tmp_path)
    assert consumer.complete(rec.idem_key, report).ok
    second = consumer.complete(rec.idem_key, report)
    assert second.reason == "completion already published"


def test_completion_invalid_report_fails_closed(env, tmp_path):
    consumer, _, _, root = env
    rec = consumer.enqueue("pkt-1", "r1", 1)
    consumer.drain()
    doc = good_short_result(verdict="pass", smuggled_field="x")
    report = tmp_path / "bad.json"
    report.write_text(json.dumps(doc), encoding="utf-8")
    result = consumer.complete(rec.idem_key, report)
    assert not result.ok
    assert any(e["event"] == "exec_failed"
               and e["detail"]["why"] == "short_result_invalid"
               for e in read_events(root)), "invalid completion emits t35"


# ---------------------------------------------------------------------------
# stale-claim reaper
# ---------------------------------------------------------------------------
def test_reaper_recovers_stale_claim(env):
    consumer, dispatched, clock, _ = env
    rec = consumer.enqueue("p1", "r1", 1)
    assert consumer.try_claim(rec) is not None
    clock[0] += consumer.claim_stale_after_s + 1
    assert consumer.reap_stale_claims() == 1
    reaped = list(consumer.paths.reaped.glob("*.json"))
    assert len(reaped) == 1, "reaped claim preserved for forensics"
    consumer.drain()
    assert dispatched == [rec.idem_key], "record became claimable again"


def test_reaper_leaves_fresh_claims_alone(env):
    consumer, _, clock, _ = env
    rec = consumer.enqueue("p1", "r1", 1)
    consumer.try_claim(rec)
    clock[0] += consumer.claim_stale_after_s / 2
    assert consumer.reap_stale_claims() == 0


def test_reclaim_budget_exhaustion_escalates_l3(env):
    """Past claim_max_reclaims the packet escalates direct_l3 fail-visible
    with a poison completion so it can never be re-dispatched."""
    consumer, _, clock, root = env
    rec = consumer.enqueue("p1", "r1", 1)
    for _ in range(consumer.claim_max_reclaims + 1):
        assert consumer.try_claim(rec) is not None
        clock[0] += consumer.claim_stale_after_s + 1
        assert consumer.reap_stale_claims() == 1
    assert consumer._completion_path(rec.idem_key).exists()
    marker = json.loads(consumer._completion_path(rec.idem_key).read_text())
    assert marker["status"] == "escalated_l3"
    assert any(e["event"] == "verdict_escalate_l3" for e in read_events(root))
    wakes = list((root / "data" / "sol_wake").glob("l2_reclaim_exhausted_*"))
    assert wakes, "reclaim exhaustion must be fail-visible via SOL wake"


# ---------------------------------------------------------------------------
# crash matrix
# ---------------------------------------------------------------------------
def test_crash_after_claim_is_reaped_and_requeued(env):
    """Crash after claim, before dispatch: the frozen-heartbeat claim is
    reaped after the stale bound and the record re-dispatches exactly once."""
    consumer, dispatched, clock, _ = env
    rec = consumer.enqueue("p1", "r1", 1)
    consumer.try_claim(rec)          # <- process dies right here
    consumer.drain()
    assert dispatched == [], "orphan claim blocks dispatch while fresh"
    clock[0] += consumer.claim_stale_after_s + 1
    reaped = consumer.reap_stale_claims()
    assert reaped == 1
    consumer.drain()
    assert dispatched == [rec.idem_key], "exactly one recovery dispatch"
    consumer.drain()
    assert dispatched == [rec.idem_key], "no duplicates after recovery"


def test_crash_after_model_before_publish_no_duplicate(env, tmp_path):
    """Crash after the model ran but before the completion published: the
    claim goes stale, is reaped, and re-verifies exactly once; once the
    completion lands there are zero further dispatches."""
    consumer, dispatched, clock, _ = env
    rec = consumer.enqueue("p1", "r1", 1)
    consumer.drain()                 # dispatch happened...
    assert dispatched == [rec.idem_key]
    #                                ...but the process crashed before complete()
    consumer.drain()
    assert len(dispatched) == 1, "fresh claim prevents any duplicate"
    clock[0] += consumer.claim_stale_after_s + 1
    assert consumer.reap_stale_claims() == 1
    consumer.drain()
    assert len(dispatched) == 2, "exactly one re-verification, never more"
    # now the re-verification publishes; the key is permanently settled
    consumer.complete(rec.idem_key, _verifier_report(tmp_path),
                      expected_revision=2)
    clock[0] += consumer.claim_stale_after_s + 1
    consumer.reap_stale_claims()
    consumer.drain()
    assert len(dispatched) == 2, "completed key can never re-dispatch"


def test_restart_reaps_all_incomplete_claims(env, tmp_path):
    """Consumer restart: recover_after_restart reaps every incomplete stale
    claim (completed ones stay) and immediately drains."""
    consumer, dispatched, clock, _ = env
    recs = [consumer.enqueue("p%d" % i, "r%d" % i, 1) for i in range(3)]
    consumer.drain()                            # 3 dispatches, 3 claims
    assert len(dispatched) == 3
    # one of the three published before the crash:
    consumer.complete(recs[0].idem_key,
                      _verifier_report(tmp_path, packet_id="p0"),
                      expected_revision=2)
    clock[0] += consumer.claim_stale_after_s + 1
    reaped = consumer.recover_after_restart()
    assert reaped == 2, "only the two incomplete claims are reaped"
    assert sorted(dispatched[3:]) == sorted([recs[1].idem_key,
                                             recs[2].idem_key])


def test_consumer_down_ages_out_fail_visible(env):
    """Unclaimed records older than l2_max_age_s escalate direct_l3 with a
    SOL wake naming the outage — never a silent drop."""
    consumer, _, clock, root = env
    rec = consumer.enqueue("p1", "r1", 1)
    clock[0] += consumer.l2_max_age_s + 1
    escalated = consumer.check_stale_pending()
    assert escalated == 1
    assert any(e["event"] == "l2_consumer_stale" for e in read_events(root))
    marker = json.loads(consumer._completion_path(rec.idem_key).read_text())
    assert marker["status"] == "stale_escalated"
    assert list((root / "data" / "sol_wake").glob("l2_consumer_stale_*"))


# ---------------------------------------------------------------------------
# policy / model-pin discipline
# ---------------------------------------------------------------------------
def test_verifier_model_from_config_never_hardcoded(env):
    consumer, _, _, root = env
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    assert consumer.verifier_model == policy["models"]["k3_model"]


def test_missing_model_pin_fails_closed(tmp_path):
    root = make_root(tmp_path)
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    policy = dict(policy)
    policy["models"] = {**policy["models"], "k3_model": ""}
    with pytest.raises(RuntimeError, match="k3_model"):
        L2Consumer(root, policy=policy)


def test_policy_unreadable_fails_closed(tmp_path):
    with pytest.raises(RuntimeError, match="failing closed"):
        load_policy(tmp_path / "does_not_exist.toml")


def test_malformed_pending_line_skipped(env):
    consumer, dispatched, _, _ = env
    consumer.enqueue("p1", "r1", 1)
    with consumer.paths.pending.open("a", encoding="utf-8") as fh:
        fh.write("{not json}\n")
        fh.write(json.dumps({"idem_key": "bogus-key-shape"}) + "\n")
    consumer.drain()
    assert len(dispatched) == 1, "malformed lines never crash the drain"
