from __future__ import annotations

import json

from provider_health import (backoff_active, classify_failure, health_path,
                             record_failure, record_success)


def test_upstream_504_is_transport_failure():
    result = classify_failure(1, "unexpected status 504 Gateway Timeout: Provider error 504")
    assert result == {"kind": "upstream_5xx", "transport": True,
                      "http_status": 504, "backoff_seconds": 300}


def test_schema_and_permission_failures_do_not_mark_provider_unhealthy(tmp_path):
    model = "provider-b/k3-reviewer"
    record_success(tmp_path, model, run_id="good")
    record_failure(tmp_path, model, run_id="bad", rc=1,
                   stderr="JSON schema validation failed: permission denied")
    doc = json.loads((tmp_path / "data/provider_health/k3.json").read_text())
    assert doc["status"] == "healthy" and doc["backoff_until"] == 0
    assert doc["last_task_outcome"] == "local_failure"


def test_timeout_without_events_sets_backoff_and_success_clears_it(tmp_path):
    model = "provider-b/k3-reviewer"
    record_failure(tmp_path, model, run_id="slow", rc=124, stderr="",
                   events="", timed_out=True)
    blocked, doc = backoff_active(tmp_path, model)
    assert blocked and doc["last_error_kind"] == "provider_stall_no_first_response"
    record_success(tmp_path, model, run_id="recovered")
    blocked, doc = backoff_active(tmp_path, model)
    assert not blocked and doc["status"] == "healthy"


def test_timeout_after_first_event_is_local_outcome(tmp_path):
    model = "provider-b/k3-reviewer"
    record_success(tmp_path, model, run_id="good")
    record_failure(tmp_path, model, run_id="long", rc=124, stderr="",
                   events='{"type":"thread.started"}', timed_out=True)
    blocked, doc = backoff_active(tmp_path, model)
    assert not blocked and doc["status"] == "healthy"
    assert doc["last_task_outcome"] == "local_failure"


def test_non_k3_model_never_creates_provider_state(tmp_path):
    assert record_failure(tmp_path, "provider-a/v4-executor", run_id="v4",
                          rc=1, stderr="504") is None
    assert not (tmp_path / "data/provider_health").exists()


def test_policy_review_model_without_k3_name_has_isolated_health(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    model = "provider-c/shared-model"
    (config / "orchestration_policy_v2.toml").write_text(
        '[models]\nk3_model = "%s"\n' % model, encoding="utf-8")
    record_failure(tmp_path, model, run_id="sonnet-down", rc=1,
                   stderr="unexpected status 504 Gateway Timeout")
    path = health_path(tmp_path, model)
    assert path.name.startswith("route-") and path.name.endswith(".json")
    assert path.name != "k3.json"
    blocked, doc = backoff_active(tmp_path, model)
    assert blocked and doc["logical_pool"] == "k3"
    assert doc["provider"] == "provider-c"
    # An old K3 backoff file cannot contaminate a temporary physical route.
    (path.parent / "k3.json").write_text(
        json.dumps({"backoff_until": 10**12}), encoding="utf-8")
    record_success(tmp_path, model, run_id="sonnet-up")
    assert backoff_active(tmp_path, model)[0] is False
