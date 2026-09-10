"""C2C host-side packet tests (VOL-16, M5): prepare/record roundtrip,
role/data-class validation, redaction belt, tamper handling, bounded content
and bounded digest."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import c2c as zc2c        # noqa: E402
from zloop import db as zdb          # noqa: E402
from zloop import evidence as zev     # noqa: E402
from zloop import paths as zpaths     # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZLOOP_DATA", str(tmp_path / "zloop-data"))
    project_dir = zpaths.ensure_project_layout("testproj")
    conn = zdb.connect(project_dir, create=True)
    store = zdb.ControlStore(project_dir, conn, project_id="testproj")
    try:
        yield SimpleNamespace(project_dir=project_dir, store=store, conn=conn)
    finally:
        conn.close()


def _prepare(env, **over):
    """prepare_c2c with defaults; per-call kwargs override."""
    run_id = env.store.create_run("audit the c2c auditor")
    kw = dict(project_dir=env.project_dir, store=env.store, run_id=run_id,
              stage_id="S01", role="plan",
              content="objective slice: ship the tokenizer fix")
    kw.update(over)
    return zc2c.prepare_c2c(**kw)


def _kinds(env):
    return [r["kind"] for r in env.conn.execute(
        "SELECT kind FROM events ORDER BY seq")]


def _detail(env, kind, c2c_id):
    for row in env.conn.execute(
            "SELECT detail_json FROM events WHERE kind=? ORDER BY seq", (kind,)):
        d = json.loads(row["detail_json"])
        if d.get("c2c_id") == c2c_id:
            return d
    return None


# ---- prepare / record roundtrip (VOL-16 §1) ---------------------------------

def test_prepare_record_roundtrip(env):
    run_id = env.store.create_run("objective")
    pkt = zc2c.prepare_c2c(env.project_dir, env.store, run_id, "S01", "plan",
                           content="audit the plan for blind spots")
    assert pkt["c2c_id"] == "C2C001"
    assert pkt["role"] == "plan"
    assert pkt["run_id"] == run_id and pkt["stage_id"] == "S01"
    assert pkt["risk_effective"] == "NORMAL"
    assert pkt["data_class"] == "project_internal"
    assert pkt["fresh_thread_required"] is False          # NORMAL: not mandatory
    assert pkt["created_at"]
    # the packet file on disk is exactly the returned dict
    pfile = env.project_dir / "c2c" / "C2C001.json"
    assert pfile.exists()
    assert json.loads(pfile.read_text(encoding="utf-8")) == pkt
    # ids are sequential across the project
    pkt2 = zc2c.prepare_c2c(env.project_dir, env.store, run_id, "S01", "result",
                            content="audit the staged result")
    assert pkt2["c2c_id"] == "C2C002" and pkt2["role"] == "result"

    res = zc2c.record_c2c(
        env.project_dir, env.store, "C2C001",
        "finding 1: no fuzz corpus for nested quotes (severity: medium)\n"
        "finding 2: CRLF edge untested (severity: low)",
        observed_identity={"surface": "chatgpt.web", "ui_model_label": "GPT-X",
                           "search_mode": "web_search",
                           "thread_id_hint": "t_abc123"})
    assert res["ok"] is True
    assert res["audit_coverage"] == "text_packet_only"
    assert res["trust"] == "external_untrusted"
    # observable identity: given fields recorded, missing ones -> "unknown"
    assert res["observed_identity"]["surface"] == "chatgpt.web"
    assert res["observed_identity"]["thread_id_hint"] == "t_abc123"
    assert res["observed_identity"]["timestamp"] == "unknown"
    assert set(res["observed_identity"]) == set(zc2c.IDENTITY_FIELDS)
    # the redacted result record (packet summary + response) is on disk
    rfile = env.project_dir / "c2c" / "C2C001-result.json"
    assert rfile.exists()
    rec = json.loads(rfile.read_text(encoding="utf-8"))
    assert rec == res
    assert rec["response"].startswith("finding 1")
    assert rec["packet_summary"]["c2c_id"] == "C2C001"
    assert rec["observed_identity"]["ui_model_label"] == "GPT-X"
    # the full redacted response lives in the blob CAS
    blob = zev.BlobStore(env.project_dir / "blobs" / "sha256")
    digest = res["response_ref"].split("blob:sha256:")[1]
    assert blob.get(digest) == res["response"].encode("utf-8")
    # both S events written, with the required detail fields
    kinds = _kinds(env)
    assert kinds.count("c2c_prepared") == 2 and "c2c_recorded" in kinds
    prep = _detail(env, "c2c_prepared", "C2C001")
    assert prep["role"] == "plan" and len(prep["packet_sha256"]) == 64
    recd = _detail(env, "c2c_recorded", "C2C001")
    assert recd["response_sha256"] == digest
    assert recd["audit_coverage"] == "text_packet_only"
    assert recd["observed_identity"]["surface"] == "chatgpt.web"


# ---- D-11: fresh threads mandatory only for HIGH/CRITICAL ------------------

def test_fresh_thread_required_only_for_high_critical(env):
    assert _prepare(env)["fresh_thread_required"] is False          # NORMAL
    assert _prepare(env, risk_effective="LOW")["fresh_thread_required"] is False
    for level in ("HIGH", "CRITICAL"):
        pkt = _prepare(env, risk_effective=level)
        assert pkt["fresh_thread_required"] is True
        assert "MANDATORY" in pkt["thread_policy_note"]
    note = _prepare(env, risk_effective="NORMAL")["thread_policy_note"]
    assert "A/B" in note and "C_same" in note and "C_fresh" in note


# ---- role / data class / risk validation (VOL-16 §4) ------------------------

def test_role_and_data_class_validation(env):
    with pytest.raises(ValueError):
        _prepare(env, role="boss")                       # role validated
    with pytest.raises(ValueError):
        _prepare(env, data_class="secret")               # never leaves the machine
    with pytest.raises(ValueError):
        _prepare(env, data_class="wild")
    with pytest.raises(ValueError):
        _prepare(env, risk_effective="EXTREME")
    # nothing was written by the rejected calls
    assert not (env.project_dir / "c2c" / "C2C001.json").exists()
    assert "c2c_prepared" not in _kinds(env)
    # the three legal data classes are accepted
    assert _prepare(env, data_class="public")["data_class"] == "public"
    assert _prepare(env, data_class="sensitive")["data_class"] == "sensitive"


# ---- redaction belt (I13) ----------------------------------------------------

def test_content_and_response_redacted(env):
    secret = "Authorization: Bearer abcdef123456"
    pkt = _prepare(env, content=f"call the api with {secret} and proceed")
    # returned packet is redacted...
    assert "<redacted>" in pkt["content"]
    assert "abcdef123456" not in json.dumps(pkt)
    # ...and so is the stored file
    raw = (env.project_dir / "c2c" / f"{pkt['c2c_id']}.json").read_text(
        encoding="utf-8")
    assert "<redacted>" in raw and "abcdef123456" not in raw

    res = zc2c.record_c2c(env.project_dir, env.store, pkt["c2c_id"],
                          f"leaked header: {secret}")
    assert "<redacted>" in res["response"]
    assert "abcdef123456" not in json.dumps(res)
    # identity values are redacted too (belt)
    res2 = zc2c.record_c2c(
        env.project_dir, env.store, pkt["c2c_id"], "clean response",
        observed_identity={"surface": "Bearer abcdef123456"})
    assert res2["observed_identity"]["surface"] == "<redacted>"


# ---- unknown c2c_id / tampered or missing packet file -----------------------

def test_record_unknown_c2c_id(env):
    assert zc2c.record_c2c(env.project_dir, env.store, "C2C999", "resp") == {
        "ok": False, "reason": "unknown_c2c_id"}
    # malformed ids (incl. traversal shapes) are unknown, not paths
    assert zc2c.record_c2c(env.project_dir, env.store, "../../etc/passwd",
                          "resp")["reason"] == "unknown_c2c_id"
    assert zc2c.record_c2c(env.project_dir, env.store, "C2C1",
                          "resp")["reason"] == "unknown_c2c_id"


def test_record_tampered_or_corrupt_packet_is_graceful(env):
    pkt = _prepare(env, content="plan body")
    cid = pkt["c2c_id"]
    pfile = env.project_dir / "c2c" / f"{cid}.json"

    # (a) content tampered after prepare -> hash mismatch, no exception
    data = json.loads(pfile.read_text(encoding="utf-8"))
    data["content"] = "tampered content"
    pfile.write_text(json.dumps(data, indent=2), encoding="utf-8")
    res = zc2c.record_c2c(env.project_dir, env.store, cid, "resp")
    assert res == {"ok": False, "reason": "packet_integrity_mismatch"}

    # (b) corrupt JSON on disk
    pfile.write_text("{oops", encoding="utf-8")
    assert zc2c.record_c2c(env.project_dir, env.store, cid,
                          "resp")["ok"] is False

    # (c) packet file deleted entirely -> unknown id
    pfile.unlink()
    assert zc2c.record_c2c(env.project_dir, env.store, cid, "resp") == {
        "ok": False, "reason": "unknown_c2c_id"}

    # no failed attempt produced a record file or a c2c_recorded event
    assert not (env.project_dir / "c2c" / f"{cid}-result.json").exists()
    assert "c2c_recorded" not in _kinds(env)


# ---- bounded content (>8000 chars -> blob + content_ref) --------------------

def test_large_content_is_bounded_and_blobbed(env):
    pkt = _prepare(env, content="y" * 9000)
    assert pkt["content_ref"].startswith("blob:sha256:")
    assert pkt["content"].startswith("y" * 8000)      # truncated at the limit
    assert 8000 < len(pkt["content"]) < 8200          # ...plus a short marker
    assert "truncated" in pkt["content"]
    # the full redacted text lives in the blob CAS
    blob = zev.BlobStore(env.project_dir / "blobs" / "sha256")
    digest = pkt["content_ref"].split(":", 2)[2]
    assert blob.get(digest) == ("y" * 9000).encode("utf-8")
    # short content: no blob, no content_ref key
    pkt2 = _prepare(env, content="short and sweet")
    assert "content_ref" not in pkt2


# ---- bounded digest (D-13-era rule, P1-11) ----------------------------------

def test_bounded_digest():
    assert zc2c.bounded_digest("hello") == "hello"
    assert zc2c.bounded_digest("x" * 5000) == "x" * 2048
    assert zc2c.bounded_digest("abcdef", limit=3) == "abc"
    assert zc2c.bounded_digest("abcdef", limit=0) == ""
    # redaction belt before bounding (I13)
    assert zc2c.bounded_digest(
        "Authorization: Bearer abcdef123456") == "Authorization: <redacted>"
    with pytest.raises(ValueError):
        zc2c.bounded_digest(b"bytes")
