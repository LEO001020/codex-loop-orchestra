import importlib.util
import json
from pathlib import Path


PKG = Path(__file__).resolve().parents[2]
SCRIPT = PKG / "hooks" / "reconcile_subagent_metering.py"
SPEC = importlib.util.spec_from_file_location("reconcile_subagent_metering", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


ROOT_ID = "019fe81a-a1c3-7ef1-92ca-41909075e0e4"
CHILD_ID = "019fe81a-e3e2-7503-b114-b37602773438"
CWD = str(Path(__file__).resolve().parent.parent.parent)


def session_meta(thread_source, id_value, session_id, **extra):
    payload = {
        "id": id_value,
        "session_id": session_id,
        "thread_source": thread_source,
        "cwd": CWD,
        "timestamp": "2026-08-10T03:58:07.000Z",
    }
    payload.update(extra)
    return {"type": "session_meta", "payload": payload}


def turn_context(model="provider-a/v4-executor"):
    return {"type": "turn_context", "payload": {"model": model, "effort": "high"}}


def rollout_file(sessions, id_value, records, ts="2026-08-10T03-58-24"):
    path = sessions / ("rollout-%s-%s.jsonl" % (ts, id_value))
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return path


def run(sessions, output, root_id=ROOT_ID, cwd=CWD):
    args = MOD.argparse.Namespace(
        sessions=sessions,
        output=output,
        root_session_id=root_id,
        expected_cwd=cwd,
    )
    return MOD.recover(args)


def test_recovers_when_agent_id_absent_using_session_meta_id(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout_file(sessions, ROOT_ID, [session_meta("user", ROOT_ID, ROOT_ID)])
    rollout_file(sessions, CHILD_ID, [
        session_meta("subagent", CHILD_ID, ROOT_ID, agent_role="worker"),
        turn_context(),
    ])

    recovered = run(sessions, tmp_path / "out.ndjson")

    assert set(recovered) == {"worker"}
    record = recovered["worker"]
    assert record["agent_id"] == CHILD_ID
    assert record["agent_id_source"] == "session_meta.id"
    assert record["session_id"] == ROOT_ID
    assert record["parent_thread_id"] == ROOT_ID
    assert record["identity_path_match"] is True
    assert record["hook_observed"] is False
    assert record["rollout_path"].endswith(CHILD_ID + ".jsonl")


def test_agent_id_field_must_agree_with_filename_and_id(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout_file(sessions, ROOT_ID, [session_meta("user", ROOT_ID, ROOT_ID)])
    rollout_file(sessions, CHILD_ID, [
        session_meta("subagent", CHILD_ID, ROOT_ID, agent_role="worker", agent_id=CHILD_ID),
        turn_context(),
    ])

    recovered = run(sessions, tmp_path / "out.ndjson")

    assert set(recovered) == {"worker"}
    assert recovered["worker"]["agent_id"] == CHILD_ID
    assert recovered["worker"]["agent_id_source"] == "session_meta.agent_id"


def test_agent_id_field_mismatch_is_rejected(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout_file(sessions, ROOT_ID, [session_meta("user", ROOT_ID, ROOT_ID)])
    rollout_file(sessions, CHILD_ID, [
        session_meta(
            "subagent", CHILD_ID, ROOT_ID,
            agent_id="019fe999-0000-0000-0000-000000000000",
        ),
        turn_context(),
    ])

    assert run(sessions, tmp_path / "out.ndjson") == {}


def test_filename_id_mismatch_with_session_meta_id_is_rejected(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout_file(sessions, ROOT_ID, [session_meta("user", ROOT_ID, ROOT_ID)])
    other_id = "019fe81a-e49c-7f83-b6a4-d59e41388e65"
    rollout_file(sessions, other_id, [
        session_meta("subagent", CHILD_ID, ROOT_ID),
        turn_context(),
    ])

    assert run(sessions, tmp_path / "out.ndjson") == {}


def test_multi_meta_with_identity_conflict_is_rejected(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout_file(sessions, ROOT_ID, [session_meta("user", ROOT_ID, ROOT_ID)])
    rollout_file(sessions, CHILD_ID, [
        session_meta("subagent", CHILD_ID, ROOT_ID, agent_role="worker"),
        session_meta(
            "subagent", CHILD_ID,
            "019fe999-0000-0000-0000-000000000000",
            agent_role="worker",
        ),
        turn_context(),
    ])

    assert run(sessions, tmp_path / "out.ndjson") == {}


def test_self_meta_critical_conflict_fails_closed_in_both_orders(tmp_path):
    variants = [
        {"cwd": "E:\\other"},
        {"agent_id": "019fe999-0000-0000-0000-000000000000"},
        {"agent_role": "reviewer"},
    ]
    for idx, extra in enumerate(variants):
        for order in (0, 1):
            sessions = tmp_path / ("sessions_%d_%d" % (idx, order))
            sessions.mkdir()
            rollout_file(sessions, ROOT_ID, [session_meta("user", ROOT_ID, ROOT_ID)])
            base = session_meta("subagent", CHILD_ID, ROOT_ID, agent_role="worker")
            variant = session_meta("subagent", CHILD_ID, ROOT_ID, agent_role="worker")
            variant["payload"].update(extra)
            records = [base, variant] if order == 0 else [variant, base]
            records.append(turn_context())
            rollout_file(sessions, CHILD_ID, records)
            assert run(
                sessions, tmp_path / ("out_%d_%d.ndjson" % (idx, order))
            ) == {}


def test_root_requires_session_id_equal_filename_and_root(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    root_payload = session_meta("user", ROOT_ID, ROOT_ID)
    root_payload["payload"]["session_id"] = "019fe999-0000-0000-0000-000000000000"
    rollout_file(sessions, ROOT_ID, [root_payload])
    rollout_file(sessions, CHILD_ID, [
        session_meta("subagent", CHILD_ID, ROOT_ID, agent_role="worker"),
        turn_context(),
    ])

    assert run(sessions, tmp_path / "out.ndjson") == {}


def test_root_requires_id_equal_filename_and_root(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    root_payload = session_meta("user", ROOT_ID, ROOT_ID)
    root_payload["payload"]["id"] = "019fe999-0000-0000-0000-000000000000"
    rollout_file(sessions, ROOT_ID, [root_payload])
    rollout_file(sessions, CHILD_ID, [
        session_meta("subagent", CHILD_ID, ROOT_ID, agent_role="worker"),
        turn_context(),
    ])

    assert run(sessions, tmp_path / "out.ndjson") == {}


def test_multi_meta_without_identity_conflict_is_accepted(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout_file(sessions, ROOT_ID, [session_meta("user", ROOT_ID, ROOT_ID)])
    rollout_file(sessions, CHILD_ID, [
        session_meta("subagent", CHILD_ID, ROOT_ID, agent_role="worker"),
        session_meta("user", ROOT_ID, ROOT_ID, cwd="E:\\other"),
        turn_context(),
    ])

    assert set(run(sessions, tmp_path / "out.ndjson")) == {"worker"}


def test_nonmatching_role_is_not_recovered(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout_file(sessions, ROOT_ID, [session_meta("user", ROOT_ID, ROOT_ID)])
    rollout_file(sessions, CHILD_ID, [
        session_meta("subagent", CHILD_ID, ROOT_ID, agent_role="scout"),
        turn_context(),
    ])

    assert run(sessions, tmp_path / "out.ndjson") == {}


def test_unknown_role_recovers_from_developer_marker(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout_file(sessions, ROOT_ID, [session_meta("user", ROOT_ID, ROOT_ID)])
    rollout_file(sessions, CHILD_ID, [
        session_meta("subagent", CHILD_ID, ROOT_ID, agent_role="unknown"),
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": MOD.ROLE_MARKERS["worker"]}],
            },
        },
        turn_context(),
    ])

    assert set(run(sessions, tmp_path / "out.ndjson")) == {"worker"}


def test_append_is_idempotent(tmp_path):
    output = tmp_path / "out.ndjson"
    recovered = {
        role: {
            "event": "SubagentStartRecovered",
            "agent_id": "agent-%s" % role,
            "session_id": ROOT_ID,
            "agent_role": role,
        }
        for role in MOD.ROLE_MARKERS
    }

    assert MOD.append_idempotently(output, recovered) == 4
    assert MOD.append_idempotently(output, recovered) == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 4


def test_malformed_rollout_filename_is_ignored(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout_file(sessions, ROOT_ID, [session_meta("user", ROOT_ID, ROOT_ID)])
    bogus = sessions / "rollout-2026-08-10T03-58-24-not-a-uuid.jsonl"
    bogus.write_text(
        json.dumps(session_meta("subagent", CHILD_ID, ROOT_ID)) + "\n" +
        json.dumps(turn_context()) + "\n",
        encoding="utf-8",
    )

    assert run(sessions, tmp_path / "out.ndjson") == {}
