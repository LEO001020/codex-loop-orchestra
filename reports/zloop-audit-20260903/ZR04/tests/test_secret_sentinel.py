"""Sentinel regression oracle (D-21, GPT-audit finding): a fake Kimi server
token seeded at <home>/.kimi-code/server.token must never survive anywhere
in the zloop data tree.

The token is pushed through every persistence plane — redact_obj, the
Journal (inline AND blob overflow), prepare_c2c (packet + content blob +
S events), and the research-broker storage path (redacted raw_messages →
BlobStore → manifest.json) — then the ENTIRE tmp tree is scanned bytewise.
No live calls: the "provider credential" is a FAKETOKEN-<uuid> string.
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import c2c, db, evidence, paths, redact                  # noqa: E402
from zloop.research.kimi_server import messages_blob_bytes           # noqa: E402

PROJECT_ID = "sentinel"


def _scan_tree(root: Path, needle: str, exclude: tuple[Path, ...] = ()) -> list[Path]:
    """Every file under root (minus exclude) whose bytes contain needle."""
    hits: list[Path] = []
    for f in sorted(Path(root).rglob("*")):
        if not f.is_file() or f in exclude:
            continue
        try:
            if needle.encode("utf-8") in f.read_bytes():
                hits.append(f)
        except OSError:
            continue
    return hits


def _seed_credential(tmp_path, monkeypatch) -> tuple[str, Path]:
    """Fake <home>/.kimi-code/server.token exactly where the audit found it."""
    token = f"FAKETOKEN-{uuid.uuid4()}"
    home = tmp_path / "home"
    cred = home / ".kimi-code" / "server.token"
    cred.parent.mkdir(parents=True, exist_ok=True)
    cred.write_text(token + "\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("ZLOOP_DATA", str(tmp_path / "data"))
    return token, cred


def test_fake_token_sentinel_never_reaches_data_tree(tmp_path, monkeypatch):
    token, cred = _seed_credential(tmp_path, monkeypatch)

    # sanity: the sentinel really is at the credential location
    assert (Path.home() / ".kimi-code" / "server.token").read_text(
        encoding="utf-8").strip() == token

    proj = paths.ensure_project_layout(PROJECT_ID)
    conn = db.connect(proj, create=True)
    store = db.ControlStore(proj, conn, project_id=PROJECT_ID)
    run_id = store.create_run("sentinel oracle run")

    # (a) direct redact_obj: secret-named keys, kv text, bearer header
    safe = redact.redact_obj({
        "tool_response": {"stdout": f"Authorization: Bearer {token}\n"
                                    f"server.token: {token}"},
        "env": {"KIMI_SERVER_TOKEN": token},
        "nested": [{"server": {"token": token}}],
    })
    assert token not in json.dumps(safe)
    assert redact.REDACTED in json.dumps(safe)

    # (b) evidence journal: inline payload AND blob-overflow payload
    j = evidence.Journal(paths.history_session_file(PROJECT_ID, "sentinel"),
                         paths.blobs_root(PROJECT_ID))
    assert j.append(kind="tool_result", session_id="sentinel",
                    event="PostToolUse", tool="Bash",
                    payload={"stdout": f"export KIMI_SERVER_TOKEN={token}\n"
                                       f"Authorization: Bearer {token}"})
    assert j.append(kind="tool_result", session_id="sentinel",
                    payload={"stdout": "noise-" * 900
                              + f"\nserver.token: {token}"})   # >4KB -> blob
    lines = evidence.read_journal(paths.history_session_file(PROJECT_ID, "sentinel"))
    inline = lines[0]["payload_inline"]
    assert inline is not None and "<redacted>" in inline          # inline redacted
    ref = lines[1]["payload_ref"]
    assert ref and ref.startswith("blob:sha256:")
    blob = evidence.BlobStore(paths.blobs_root(PROJECT_ID)).get(
        ref.split(":", 2)[2])
    assert blob is not None and b"<redacted>" in blob             # blob redacted
    assert token.encode() not in blob

    # (c) c2c packet: content >8000 chars -> truncated packet + content blob
    content = ("auditing the session environment\n"
               f"server.token: {token}\n"
               f"Authorization: Bearer {token}\n"
               + "filler audit line\n" * 600)
    packet = c2c.prepare_c2c(proj, store, run_id, "S1", "plan", content=content)
    assert token not in json.dumps(packet)
    assert token not in (proj / "c2c" / f"{packet['c2c_id']}.json").read_text(
        encoding="utf-8")
    assert packet.get("content_ref", "").startswith("blob:sha256:")

    # (d) research-broker storage path (simulated, no lane): redacted
    # raw_messages -> messages_blob_bytes -> BlobStore -> manifest record
    raw_messages = [
        {"role": "user", "content": f"please authenticate with Bearer {token}"},
        {"role": "assistant", "content": f"using server.token: {token}"},
    ]
    blobs = evidence.BlobStore(paths.blobs_root(PROJECT_ID))
    digest = blobs.put(messages_blob_bytes(redact.redact_obj(raw_messages)))
    research_dir = proj / "research" / "RS001"
    research_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "research_id": "RS001",
        "lane": "kimi",
        "results": [{
            "ref": "web:kimi:Q1",
            "research_id": "RS001",
            "question_id": "Q1",
            "query": redact.redact_str(f"verify server.token={token}"),
            "answer": redact.redact_str(f"authenticated with Bearer {token}"),
            "raw_ref": "blob:sha256:" + digest,
            "content_hash": hashlib.sha256(b"redacted").hexdigest(),
        }],
    }
    (research_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    raw_blob = blobs.get(digest)
    assert raw_blob is not None and token.encode() not in raw_blob
    assert b"<redacted>" in raw_blob

    conn.close()

    # THEN: walk the ENTIRE tmp tree (data root + home) — the fake token
    # must appear in NO file except the seeded credential itself.
    leaks = _scan_tree(tmp_path, token, exclude=(cred,))
    assert leaks == [], f"sentinel token leaked into: {leaks}"

    # oracle has teeth: we really scanned a populated tree, and the seeded
    # credential is the only place the token exists
    scanned = [f for f in tmp_path.rglob("*") if f.is_file()]
    assert len(scanned) >= 8
    assert _scan_tree(tmp_path, token) == [cred]
    assert token in cred.read_text(encoding="utf-8")   # untouched on disk


def test_sentinel_detector_flags_an_unredacted_leak(tmp_path, monkeypatch):
    # Control: the same scan detects a leak when redaction is bypassed,
    # so the oracle above can never pass vacuously.
    token, _ = _seed_credential(tmp_path, monkeypatch)
    proj = paths.ensure_project_layout(PROJECT_ID)
    (proj / "history" / "sessions" / "leak.ndjson").write_text(
        json.dumps({"payload_inline": json.dumps({"stdout": token})}),
        encoding="utf-8")
    assert _scan_tree(tmp_path, token)
