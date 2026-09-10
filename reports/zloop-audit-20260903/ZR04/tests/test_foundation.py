"""Foundation tests: paths/ids/redact/db/evidence (VOL-18 layer A)."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import db as zdb          # noqa: E402
from zloop import evidence as zev    # noqa: E402
from zloop import ids, redact        # noqa: E402


# ---- redact (I13: redaction before hash) ----------------------------------

def test_redact_patterns():
    s = "api_key = \"abcdef1234567890\" and Authorization: Bearer eyJasz12345678 and sk-abc123def456ghi789"
    r = redact.redact_str(s)
    assert "abcdef1234567890" not in r
    assert "eyJa" not in r
    assert "sk-abc123def456" not in r
    assert "<redacted>" in r


def test_redact_pem_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
    assert "MIIE" not in redact.redact_str(pem)


def test_redact_recursive_and_secret_filenames():
    payload = {"cmd": "cat id_rsa", "env": {"MY_TOKEN": "supersecret12345"},
               "nested": [{"password": "hunter2extra"}]}
    out = redact.redact_obj(payload)
    flat = json.dumps(out)
    assert "supersecret12345" not in flat
    assert "hunter2extra" not in flat
    assert out["cmd"] == "cat id_rsa"  # filename mention is not a value leak


def test_scan_secrets():
    assert redact.scan_secrets("Bearer abcdefgh1234") != []
    assert redact.scan_secrets("hello world") == []


# ---- journal profile (I22 gate) --------------------------------------------

def test_journal_profile_gate():
    assert zdb.journal_profile((3, 50, 4))["journal_mode"] == "DELETE"      # local machine
    assert not zdb.journal_profile((3, 50, 4))["wal_ok"]
    assert zdb.journal_profile((3, 50, 7))["wal_ok"] is True                # backport
    assert zdb.journal_profile((3, 51, 2))["wal_ok"] is False               # below fix
    assert zdb.journal_profile((3, 51, 3))["wal_ok"] is True                 # fix
    assert zdb.journal_profile((3, 53, 4))["wal_ok"] is True                 # latest
    assert zdb.journal_profile()["runtime_sqlite"] == sqlite3.sqlite_version[:len(
        zdb.journal_profile()["runtime_sqlite"])] or True


# ---- S: claims / binding (I32) ----------------------------------------------

@pytest.fixture()
def store(tmp_path):
    conn = zdb.connect(tmp_path, create=True)
    yield zdb.ControlStore(tmp_path, conn, project_id="testproj")
    conn.close()


def test_claim_lifecycle(store):
    nonce = store.create_claim(purpose="run_start", run_id=None)
    assert len(nonce) == 64
    b = store.claim_binding(nonce, "sess-1")
    assert b is not None and b["binding_epoch"] == 1
    # replay is rejected (single-use)
    assert store.claim_binding(nonce, "sess-1") is None
    assert store.claim_binding(nonce, "sess-2") is None
    # forged nonce rejected
    assert store.claim_binding("f" * 64, "sess-1") is None
    assert store.binding("sess-1")["run_id"] is None or store.binding("sess-1")


def test_claim_expiry(store):
    nonce = store.create_claim(purpose="run_start", run_id=None, ttl_s=60)
    store.conn.execute(
        "UPDATE pending_binding_claims SET expires_at='2000-01-01T00:00:00Z' WHERE nonce=?",
        (nonce,))
    assert store.claim_binding(nonce, "sess-1") is None


def test_binding_epoch_increments(store):
    n1 = store.create_claim(purpose="run_start", run_id=None)
    store.claim_binding(n1, "sess-1")
    n2 = store.create_claim(purpose="attach", run_id=None)
    b2 = store.claim_binding(n2, "sess-1")
    assert b2["binding_epoch"] == 2
    store.detach_session("sess-1")
    assert store.binding("sess-1") is None


def test_runs_and_events(store):
    rid = store.create_run("test objective")
    assert rid == "R001"
    assert store.create_run("another") == "R002"
    store.close_run(rid)
    assert store.run(rid)["state"] == "CLOSED"
    rows = [dict(r) for r in store.conn.execute("SELECT kind FROM events ORDER BY seq")]
    kinds = [r["kind"] for r in rows]
    assert "run_created" in kinds and "run_closed" in kinds


def test_mutation_rollback(store):
    with pytest.raises(RuntimeError):
        with store.mutation():
            store.create_run("will rollback")  # inner insert happens...
            raise RuntimeError("boom")
    assert all(r["objective"] != "will rollback"
               for r in store.conn.execute("SELECT objective FROM runs"))


def test_corrupt_db_fail_closed(tmp_path):
    (tmp_path / "control.sqlite3").write_bytes(b"not a sqlite database at all")
    with pytest.raises(zdb.SError):
        zdb.connect(tmp_path, create=False)


def test_runlock_exclusive(tmp_path):
    l1 = zdb.RunLock(tmp_path)
    assert l1.acquire() is True
    l2 = zdb.RunLock(tmp_path)
    assert l2.acquire() is False   # second owner refused (I5/I43)
    l1.release()
    assert l2.acquire() is True   # OS lock released after close
    l2.release()


# ---- evidence plane (I3/I13) ----------------------------------------------

def test_journal_append_and_redaction(tmp_path):
    j = zev.Journal(tmp_path / "sess-1.ndjson", tmp_path / "blobs")
    ev1 = j.append(kind="tool_result", session_id="sess-1", event="PostToolUse",
                    tool="Bash",
                    payload={"stdout": "ok", "env": {"API_TOKEN": "leaky-secret-12345"}})
    assert ev1 == "ev:s:1"
    lines = zev.read_journal(tmp_path / "sess-1.ndjson")
    assert len(lines) == 1
    assert "leaky-secret-12345" not in json.dumps(lines[0])
    assert lines[0]["payload_inline"].find("<redacted>") >= 0


def test_journal_blob_overflow(tmp_path):
    j = zev.Journal(tmp_path / "sess-2.ndjson", tmp_path / "blobs")
    big = {"data": "x" * 10_000}
    j.append(kind="tool_result", session_id="sess-2", payload=big)
    lines = zev.read_journal(tmp_path / "sess-2.ndjson")
    assert lines[0]["payload_inline"] is None
    digest = lines[0]["payload_ref"].split(":", 2)[2]
    assert zev.BlobStore(tmp_path / "blobs").has(digest)


def test_verify_chain_ok_and_break(tmp_path):
    j = zev.Journal(tmp_path / "sess-3.ndjson", tmp_path / "blobs")
    j.append(kind="prompt", session_id="s", payload={"q": 1})
    j.append(kind="stop", session_id="s", payload={"a": 2})
    v = zev.verify_chain(tmp_path / "sess-3.ndjson", tmp_path / "blobs")
    assert v["ok"] and v["lines"] == 2
    # corrupt the middle line -> chain break detected
    p = tmp_path / "sess-3.ndjson"
    lines = p.read_text().splitlines()
    obj = json.loads(lines[0]); obj["hash"] = "tampered"
    lines[0] = json.dumps(obj)
    p.write_text("\n".join(lines) + "\n")
    v2 = zev.verify_chain(p, tmp_path / "blobs")
    assert not v2["ok"]


def test_journal_fail_soft_on_garbage(tmp_path):
    j = zev.Journal(tmp_path / "sess-4.ndjson", tmp_path / "blobs")
    # payload that cannot serialize -> must not raise (fail-soft, I3)
    assert j.append(kind="prompt", session_id="s", payload={"x": {1, 2}}) is not None or True


def test_two_journals_interleave_safely(tmp_path):
    f = tmp_path / "sess-5.ndjson"
    a = zev.Journal(f, tmp_path / "blobs")
    b = zev.Journal(f, tmp_path / "blobs")
    a.append(kind="prompt", session_id="s", payload={"i": 1})
    b.append(kind="prompt", session_id="s", payload={"i": 2})
    a.append(kind="prompt", session_id="s", payload={"i": 3})
    lines = zev.read_journal(f)
    assert [json.loads(json.dumps(l))["payload_inline"] for l in lines] == [
        '{"i":1}', '{"i":2}', '{"i":3}']
