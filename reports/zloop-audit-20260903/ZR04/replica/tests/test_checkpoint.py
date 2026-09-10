"""H1.semantic checkpoint tests (VOL-04 §7.2): roundtrip, validation, cap,
dedupe and machine-field stripping."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import checkpoint as zcp    # noqa: E402

_SIX = ["established_facts", "decisions", "rejected_hypotheses",
        "unresolved_questions", "next_frontier", "risk_notes"]


def capsule(**over):
    cp = {
        "objective_slice": "stabilize the tokenizer",
        "established_facts": [
            {"claim": "parser fails on nested quotes",
             "evidence_refs": ["ev:s:12"]},
        ],
        "decisions": [
            {"decision": "rewrite tokenizer", "why": "safer",
             "evidence_refs": ["ev:git:0123456789abcdef0123456789abcdef01234567"]},
        ],
        "rejected_hypotheses": [
            {"hypothesis": "encoding issue", "why_rejected": "no evidence",
             "evidence_refs": ["ev:blob:sha256:" + "a" * 64]},
        ],
        "unresolved_questions": ["does CRLF matter?"],
        "next_frontier": ["add fuzz corpus"],
        "risk_notes": ["low risk"],
    }
    cp.update(over)
    return cp


def test_valid_capsule_roundtrip(tmp_path):
    proj = tmp_path / "proj"
    assert zcp.checkpoint_current(proj) is None          # missing tolerated
    assert zcp.checkpoint_show(proj, "cp_0001") is None

    cp_id = zcp.checkpoint_write(proj, capsule())
    assert cp_id == "cp_0001"
    assert (proj / "checkpoints" / "cp_0001.json").exists()
    ptr = json.loads((proj / "checkpoints" / "current.json").read_text(
        encoding="utf-8"))
    assert ptr["id"] == "cp_0001" and "ts" in ptr

    cur = zcp.checkpoint_current(proj)
    assert cur is not None
    assert cur["objective_slice"] == "stabilize the tokenizer"
    assert cur["established_facts"][0]["claim"] == "parser fails on nested quotes"
    assert cur["unresolved_questions"] == ["does CRLF matter?"]
    assert zcp.checkpoint_show(proj, "cp_0001") == cur
    assert zcp.checkpoint_show(proj, "cp_9999") is None
    assert zcp.checkpoint_show(proj, "../current") is None   # no traversal

    # semantic_state_hash = sha256 over canonical JSON of the six lists
    six = {k: cur[k] for k in _SIX}
    expect = hashlib.sha256(json.dumps(six, sort_keys=True,
                                       separators=(",", ":")).encode(
                                           "utf-8")).hexdigest()
    assert cur["semantic_state_hash"] == "sha256:" + expect


def test_invalid_evidence_refs_moved_to_unverified_notes(tmp_path):
    proj = tmp_path / "proj"
    cp = capsule(
        established_facts=[
            {"claim": "good", "evidence_refs": ["ev:s:1"]},
            {"claim": "bad ref", "evidence_refs": ["http://example.com"]},
            {"claim": "empty refs", "evidence_refs": []},
            {"claim": "no refs key"},
            "not even a dict",
        ],
        decisions=[
            {"decision": "short git", "why": "x",
             "evidence_refs": ["ev:git:abc"]},
            {"decision": "upper hex", "why": "x",
             "evidence_refs": ["ev:blob:sha256:" + "A" * 64]},
        ],
    )
    cp_id = zcp.checkpoint_write(proj, cp)
    assert cp_id == "cp_0001"
    cur = zcp.checkpoint_current(proj)

    assert [f["claim"] for f in cur["established_facts"]] == ["good"]
    assert cur["decisions"] == []
    assert len(cur["unverified_notes"]) == 6
    assert "not even a dict" in cur["unverified_notes"]
    assert any(isinstance(u, dict) and u.get("claim") == "bad ref"
               for u in cur["unverified_notes"])
    # the caller's capsule dict was not mutated
    assert len(cp["established_facts"]) == 5


def test_oversize_capsule_rejected(tmp_path):
    proj = tmp_path / "proj"
    big = capsule()
    big["established_facts"] = [{"claim": "x" * 20_000,
                                "evidence_refs": ["ev:s:1"]}]
    assert zcp.checkpoint_write(proj, big) is None
    assert not (proj / "checkpoints" / "cp_0001.json").exists()
    assert zcp.checkpoint_current(proj) is None

    # near-cap capsules still fit, and the file on disk respects the cap
    ok = capsule()
    ok["established_facts"] = [{"claim": "y" * 15_000,
                                "evidence_refs": ["ev:s:1"]}]
    assert zcp.checkpoint_write(proj, ok) == "cp_0001"
    assert (proj / "checkpoints" / "cp_0001.json").stat().st_size <= zcp.CAP


def test_dedupe_same_semantic_state(tmp_path):
    proj = tmp_path / "proj"
    id1 = zcp.checkpoint_write(proj, capsule())
    id2 = zcp.checkpoint_write(proj, capsule())
    assert id1 == id2 == "cp_0001"
    assert sorted(p.name for p in (proj / "checkpoints").glob("cp_*.json")) == [
        "cp_0001.json"]                      # only one file written

    changed = capsule()
    changed["next_frontier"] = ["different frontier"]
    assert zcp.checkpoint_write(proj, changed) == "cp_0002"
    assert zcp.checkpoint_current(proj)["next_frontier"] == [
        "different frontier"]

    # dedupe only compares against the current checkpoint
    id4 = zcp.checkpoint_write(proj, capsule())
    assert id4 == "cp_0003"


def test_machine_fields_stripped(tmp_path):
    proj = tmp_path / "proj"
    cp = capsule(run_id="R001", stage_state="EXECUTING",
                 canonical_head="git:sha123", packet_states={"P01": "RUNNING"},
                 active_launch_ids=["Ldeadbeef1234"])
    assert zcp.checkpoint_write(proj, cp) == "cp_0001"
    cur = zcp.checkpoint_current(proj)
    for k in ("run_id", "stage_state", "canonical_head", "packet_states",
              "active_launch_ids"):
        assert k not in cur                       # never a semantic field
    assert set(cur["stripped_machine_fields"]) == {
        "run_id", "stage_state", "canonical_head", "packet_states",
        "active_launch_ids"}
    # machine values never reach disk at all (I14: they belong to H1.machine)
    raw = (proj / "checkpoints" / "cp_0001.json").read_text(encoding="utf-8")
    assert "R001" not in raw and "EXECUTING" not in raw
    assert "git:sha123" not in raw and "Ldeadbeef1234" not in raw
