import json

from tests.conftest import PY


def seed_duty_review(loop, pid="p1"):
    ledger = loop.ledger()
    ledger["packets"][pid] = {"state": "DUTY_REVIEW", "history": [], "attempts": 1}
    loop.set_ledger(ledger)


def write_ruling(tmp_path, class_="retryable", confidence=0.9):
    path = tmp_path / "ruling.json"
    path.write_text(json.dumps({"class": class_, "evidence": ["report.json:1"],
                                "confidence": confidence,
                                "progress_ledger_delta": {}}))
    return path


def run_route(loop, ruling):
    return loop.run([PY, loop.harness("duty_route.py"), "--packet", "p1",
                     "--ruling", ruling, "--enforce", "true"])


def test_valid_ruling_emits_state_event(loop, tmp_path):
    seed_duty_review(loop)
    p = run_route(loop, write_ruling(tmp_path, "retryable"))
    assert p.returncode == 0, p.stdout + p.stderr
    assert loop.events()[-1]["event"] == "duty_retryable"
    assert loop.events()[-1]["detail"]["gate"]["gate"] == "VALID"


def test_invalid_ruling_fails_visible_as_terminal(loop, tmp_path):
    seed_duty_review(loop)
    p = run_route(loop, write_ruling(tmp_path, "fixable", confidence=0.2))
    assert p.returncode == 1
    assert loop.events()[-1]["event"] == "duty_terminal"


def test_routing_is_idempotent(loop, tmp_path):
    seed_duty_review(loop)
    ruling = write_ruling(tmp_path)
    assert run_route(loop, ruling).returncode == 0
    before = len(loop.events())
    second = run_route(loop, ruling)
    assert second.returncode == 3
    assert len(loop.events()) == before


def test_enforce_defaults_to_config_true(loop, tmp_path):
    """Production semantics: without --enforce the single source of truth is
    duty_officer.enforce in config (never a hardcoded default)."""
    seed_duty_review(loop)
    loop.write_config(duty_enforce=True)
    p = loop.run([PY, loop.harness("duty_route.py"), "--packet", "p1",
                  "--ruling", write_ruling(tmp_path)])
    assert p.returncode == 0
    assert loop.events()[-1]["event"] == "duty_retryable"
    assert loop.events()[-1]["detail"]["enforce"] is True


def test_enforce_defaults_to_config_false_records_only(loop, tmp_path):
    seed_duty_review(loop)
    loop.write_config(duty_enforce=False)
    p = loop.run([PY, loop.harness("duty_route.py"), "--packet", "p1",
                  "--ruling", write_ruling(tmp_path)])
    assert p.returncode == 0
    assert loop.events()[-1]["detail"]["enforce"] is False
    assert loop.events()[-1]["detail"]["gate"]["gate"] == "RECORDED_NOT_ENFORCED"
