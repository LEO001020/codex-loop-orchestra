"""Cold-start v1 must preserve the same physical retry boundary as v2."""

import statemachine


def prepare_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(statemachine, "DATA", str(tmp_path))
    for name in ("dead_letters", "sol_wake", "lifecycle"):
        (tmp_path / name).mkdir()
    (tmp_path / "escalation_log.jsonl").touch()


def test_timed_out_retry_returns_to_dispatchable_and_increments_attempt(
        tmp_path, monkeypatch):
    prepare_data_root(tmp_path, monkeypatch)
    led = {"packets": {"p1": {
        "state": "TIMED_OUT", "history": [], "attempts": 0,
    }}}

    result = statemachine.apply_event(
        led, {"packet_id": "p1", "event": "retry_dispatch"},
        enforce_duty=False)

    assert result == "DISPATCHABLE"
    assert led["packets"]["p1"]["state"] == "DISPATCHABLE"
    assert led["packets"]["p1"]["attempts"] == 1
    assert led["packets"]["p1"]["history"][-1]["t"] == 37


def test_second_timed_out_retry_dead_letters_with_shared_budget(
        tmp_path, monkeypatch):
    prepare_data_root(tmp_path, monkeypatch)
    led = {"packets": {"p1": {
        "state": "TIMED_OUT", "history": [], "attempts": 0,
    }}}

    first = statemachine.apply_event(
        led, {"packet_id": "p1", "event": "retry_dispatch"},
        enforce_duty=False)
    assert first == "DISPATCHABLE"
    led["packets"]["p1"]["state"] = "TIMED_OUT"
    second = statemachine.apply_event(
        led, {"packet_id": "p1", "event": "retry_dispatch"},
        enforce_duty=False)

    assert second == "DEAD_LETTER"
    assert led["packets"]["p1"]["state"] == "DEAD_LETTER"
    dead = __import__("json").loads(
        (tmp_path / "dead_letters" / "p1.json").read_text(encoding="utf-8"))
    assert dead["reason"] == "timeout_retry_exhausted"
    assert dead["detail"] == {"timeout_retries": 2, "cap": 1}
