"""H2 history tests: search / around / verify over seeded session journals."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import evidence as zev    # noqa: E402
from zloop import history as zh      # noqa: E402


@pytest.fixture()
def proj(tmp_path):
    pd = tmp_path / "proj"
    (pd / "history" / "sessions").mkdir(parents=True)
    (pd / "blobs" / "sha256").mkdir(parents=True)
    return pd


def _seed(proj: Path) -> None:
    """Two session journals: sess-alpha (R001/S01, 5 events, one secret
    payload proving redaction upstream) and sess-beta (R002/S02, 3 events)."""
    a = zev.Journal(proj / "history" / "sessions" / "sess-alpha.ndjson",
                    proj / "blobs" / "sha256")
    a.append(kind="session_start", session_id="sess-alpha", run_id="R001",
             stage_id="S01", payload={"note": "alpha session begins"})
    a.append(kind="prompt", session_id="sess-alpha", run_id="R001",
             stage_id="S01", payload={"q": "investigate the flaky database lock"})
    a.append(kind="tool_result", session_id="sess-alpha", run_id="R001",
             stage_id="S01", event="PostToolUse", tool="Bash",
             payload={"stdout": "3 tests passed in 0.02s"})
    a.append(kind="tool_result", session_id="sess-alpha", run_id="R001",
             stage_id="S01", event="PostToolUse", tool="Bash",
             payload={"env": {"API_TOKEN": "leaky-secret-98765"},
                      "note": "token audit done"})
    a.append(kind="stop", session_id="sess-alpha", run_id="R001",
             stage_id="S01", payload={"final": "alpha milestone wrapped"})

    b = zev.Journal(proj / "history" / "sessions" / "sess-beta.ndjson",
                    proj / "blobs" / "sha256")
    b.append(kind="prompt", session_id="sess-beta", run_id="R002",
             stage_id="S02", payload={"q": "plan the database migration"})
    b.append(kind="tool_result", session_id="sess-beta", run_id="R002",
             stage_id="S02", payload={"stdout": "migration dry-run ok"})
    b.append(kind="stop", session_id="sess-beta", run_id="R002",
             stage_id="S02", payload={"final": "beta done"})


# ---- search ----------------------------------------------------------------

def test_search_substring_case_insensitive_and_redaction(proj):
    _seed(proj)
    res = zh.history_search(proj, "database")            # plain substring
    assert len(res) == 2
    assert all(isinstance(e, dict) and "seq" in e for e in res)
    assert len(zh.history_search(proj, "DATABASE")) == 2  # case-insensitive
    assert zh.history_search(proj, "zzz-not-there") == []

    # redaction already applied upstream (I13): the secret never landed
    raw = (proj / "history" / "sessions" / "sess-alpha.ndjson").read_text(
        encoding="utf-8")
    assert "leaky-secret-98765" not in raw and "98765" not in raw
    assert zh.history_search(proj, "leaky-secret-98765") == []
    # the redacted line itself is still searchable
    assert len(zh.history_search(proj, "token audit")) == 1


def test_search_run_and_session_filters(proj):
    _seed(proj)
    res = zh.history_search(proj, "migration", run_id="R002")
    assert len(res) == 2
    assert all(e["run_id"] == "R002" and e["stage_id"] == "S02" for e in res)
    assert zh.history_search(proj, "migration", run_id="R001") == []

    res = zh.history_search(proj, "database", session="sess-alpha")
    assert len(res) == 1
    assert res[0]["session_id"] == "sess-alpha" and res[0]["seq"] == 2
    assert zh.history_search(proj, "database", session="no-such-session") == []

    # both filters combine
    combo = zh.history_search(proj, "database", session="sess-beta",
                              run_id="R002")
    assert [e["seq"] for e in combo] == [1]


def test_search_limit(proj):
    _seed(proj)
    assert len(zh.history_search(proj, "alpha")) == 5   # all sess-alpha lines
    res = zh.history_search(proj, "alpha", limit=2)
    assert [e["seq"] for e in res] == [1, 2]            # bounded, in order
    assert zh.history_search(proj, "alpha", limit=0) == []


def test_search_fail_soft(proj):
    # nothing seeded: empty results, never an exception
    assert zh.history_search(proj, "anything") == []
    with open(proj / "history" / "sessions" / "sess-none.ndjson", "w") as f:
        f.write("this is not json\n")
    assert zh.history_search(proj, "anything") == []
    # torn lines in one journal never break the derived view
    _seed(proj)
    with open(proj / "history" / "sessions" / "sess-alpha.ndjson", "a") as f:
        f.write("also not json\n")
    assert len(zh.history_search(proj, "database")) == 2
    assert isinstance(zh.history_search(proj, None), list)   # hostile query


# ---- around ----------------------------------------------------------------

def test_history_around(proj):
    _seed(proj)
    r = zh.history_around(proj, "ev:s:4", before=1, after=1)
    assert r["event"]["seq"] == 4
    assert "<redacted>" in r["event"]["payload_inline"]
    assert [e["seq"] for e in r["before"]] == [3]
    assert [e["seq"] for e in r["after"]] == [5]

    # bounded by availability at the end of the journal
    r = zh.history_around(proj, "ev:s:5", before=3, after=3)
    assert r["event"]["seq"] == 5
    assert [e["seq"] for e in r["before"]] == [2, 3, 4]
    assert r["after"] == []

    # bounded at the start of the journal (default before/after = 3)
    r = zh.history_around(proj, "ev:s:1")
    assert r["before"] == []
    assert [e["seq"] for e in r["after"]] == [2, 3, 4]

    r = zh.history_around(proj, "ev:s:2", before=0, after=0)
    assert r["before"] == [] and r["after"] == []
    assert r["event"]["seq"] == 2

    # missing / malformed event ids
    assert zh.history_around(proj, "ev:s:999") == {
        "before": [], "event": None, "after": []}
    assert zh.history_around(proj, "not-an-event")["event"] is None
    assert zh.history_around(proj, "ev:s:abc")["event"] is None


# ---- verify ----------------------------------------------------------------

def test_history_verify_ok(proj):
    _seed(proj)
    g = zev.Journal(proj / "history" / "sessions" / "sess-gamma.ndjson",
                    proj / "blobs" / "sha256")
    g.append(kind="tool_result", session_id="sess-gamma",
             payload={"data": "z" * 10_000})          # >4KB -> blob ref
    v = zh.history_verify(proj)
    assert v["ok"] is True and v["errors"] == []
    assert v["sessions"] == 3 and v["lines"] == 9


def test_history_verify_detects_tampering(proj):
    _seed(proj)
    p = proj / "history" / "sessions" / "sess-alpha.ndjson"
    lines = p.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[2])
    obj["hash"] = "0" * 64                            # flip a hash
    lines[2] = json.dumps(obj, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = zh.history_verify(proj)
    assert v["ok"] is False
    assert any("chain break" in e for e in v["errors"])
    assert v["sessions"] == 2 and v["lines"] == 8


def test_history_verify_detects_missing_blob(proj):
    g = zev.Journal(proj / "history" / "sessions" / "sess-gamma.ndjson",
                    proj / "blobs" / "sha256")
    g.append(kind="tool_result", session_id="sess-gamma",
             payload={"data": "z" * 10_000})
    env = zev.read_journal(
        proj / "history" / "sessions" / "sess-gamma.ndjson")[0]
    assert env["payload_inline"] is None
    digest = env["payload_ref"].split(":", 2)[2]
    blob = proj / "blobs" / "sha256" / digest[:2] / digest
    assert blob.exists()
    blob.unlink()
    v = zh.history_verify(proj)
    assert v["ok"] is False
    assert any("missing blob" in e for e in v["errors"])
    assert v["sessions"] == 1 and v["lines"] == 1


def test_history_verify_empty_project(proj):
    v = zh.history_verify(proj)
    assert v == {"sessions": 0, "lines": 0, "errors": [], "ok": True}
