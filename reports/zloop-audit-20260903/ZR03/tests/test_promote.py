"""Promotion tests (VOL-11 §2-§4): CAS + ff-only checked-out-safe canonical
writes (I30/I39) and dangling-intent reconciliation. Real git repos via
subprocess; ControlStore on a tmp ZLOOP_DATA."""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import db as zdb            # noqa: E402
from zloop import ids as zids          # noqa: E402
from zloop import promote as zprom     # noqa: E402
from zloop import stage as zstage      # noqa: E402
from zloop import workspace as zw      # noqa: E402


def git(*args: str, cwd: Path) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd),
                       capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p.stdout


@pytest.fixture()
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ZLOOP_DATA", str(data))
    conn = zdb.connect(data, create=True)
    yield zdb.ControlStore(data, conn, project_id="prom-test")
    conn.close()


@pytest.fixture()
def canon(tmp_path: Path) -> Path:
    root = tmp_path / "canon"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "T", cwd=root)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "base", cwd=root)
    return root


def head(canon: Path) -> str:
    return git("rev-parse", "HEAD", cwd=canon).strip()


def commit(cwd: Path, message: str, *files: str) -> str:
    for i, content in enumerate(files):
        (cwd / f"f{i}.txt").write_text(content, encoding="utf-8")
    git("add", "-A", cwd=cwd)
    git("commit", "-q", "-m", message, cwd=cwd)
    return head(cwd)


def staged_commit(canon: Path, tmp_path: Path, run_id: str, stage_id: str,
                  content: str = "staged\n") -> str:
    """Build a descendant staged commit in a private worktree, carrying the
    ZLoop provenance trailers materialization/final-staging produce."""
    wt = tmp_path / "staging-ws"
    if wt.exists():
        git("worktree", "remove", "--force", str(wt), cwd=canon)
    r = zw.create_worktree(canon, wt)
    assert r["ok"], r
    (wt / "staged.txt").write_text(content, encoding="utf-8")
    git("add", "-A", cwd=wt)
    git("commit", "-q", "-m",
        f"final staging {run_id}/{stage_id}\n\nZLoop-Run: {run_id}\n"
        f"ZLoop-Stage: {stage_id}\nZLoop-Stage-Revision: 1", cwd=wt)
    return head(wt)


class Prom:
    """run + stage (STAGED) + INTENDED promotion intent, ready to promote."""

    def __init__(self, store, canon, staged: str):
        self.store, self.canon = store, canon
        self.run_id = store.create_run("objective")
        self.stage_id = zstage.create_stage(
            store, self.run_id, "finish the work", "NORMAL",
            expected_head=head(canon), dirty_digest="",
            stage_base_ref="refs/zloop/R/base", stage_base_tree="tree0"
        )["stage_id"]
        zstage.transition_stage(store, self.run_id, self.stage_id, "EXECUTING")
        zstage.transition_stage(store, self.run_id, self.stage_id, "STAGED")
        self.intent_id = "int-" + uuid.uuid4().hex[:12]
        self.staged = staged
        self.expected_head = head(canon)
        with store.mutation():
            store.conn.execute(
                "INSERT INTO promotion_intents(intent_id, run_id, stage_id,"
                " stage_revision, expected_canonical_head,"
                " expected_dirty_digest, staged_head, final_audit_ref,"
                " state, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (self.intent_id, self.run_id, self.stage_id, 1,
                 self.expected_head, "", staged, None, "INTENDED",
                 zids.now_iso()))

    def intent(self) -> dict:
        return dict(self.store.conn.execute(
            "SELECT * FROM promotion_intents WHERE intent_id=?",
            (self.intent_id,)).fetchone())

    def stage(self) -> dict:
        return zstage.get_stage(self.store, self.run_id, self.stage_id)


@pytest.fixture()
def prom(store, canon, tmp_path):
    """A ready-to-promote world: staged descendant + INTENDED intent."""
    staged = staged_commit(canon, tmp_path, "R001", "S01")
    return Prom(store, canon, staged)


def _kinds(store):
    return [r["kind"] for r in store.conn.execute(
        "SELECT kind FROM events ORDER BY seq")]


# ---- dirty_state (VOL-08 §3 semantics) ---------------------------------------

def test_dirty_state_clean_and_dirty(canon: Path):
    d = zprom.dirty_state(canon)
    assert d == {"digest": zprom.CLEAN_DIGEST, "clean": True}
    (canon / "untracked.txt").write_text("x", encoding="utf-8")
    d2 = zprom.dirty_state(canon)
    assert d2["clean"] is False and d2["digest"] != d["digest"]
    assert zprom.dirty_state(canon)["digest"] == d2["digest"]   # deterministic
    # a tracked-content change gives yet another digest
    (canon / "README.md").write_text("changed\n", encoding="utf-8")
    assert zprom.dirty_state(canon)["digest"] != d2["digest"]


# ---- promote: CAS + ff-only (I30/I39) ----------------------------------------

def test_promote_ff_only_success(store, prom):
    canon = prom.canon
    res = zprom.promote(store, prom.run_id, prom.stage_id, git_root=canon,
                        staged_commit=prom.staged,
                        expected_head=prom.expected_head,
                        expected_dirty_digest="")
    assert res["ok"] is True, res
    assert res["new_head"] == prom.staged
    # I28: ref, index and worktree are one — the staged content is checked
    # out on the canonical branch, which stays clean
    assert head(canon) == prom.staged
    assert (canon / "staged.txt").read_text(encoding="utf-8") == "staged\n"
    assert git("status", "--porcelain", cwd=canon) == ""
    # S: intent APPLIED, stage PROMOTED, audited
    assert prom.intent()["state"] == "APPLIED"
    assert prom.intent()["resolved_at"] is not None
    assert prom.stage()["state"] == "PROMOTED"
    kinds = _kinds(store)
    assert "promotion_applied" in kinds and "stage_promoted" in kinds


def test_promote_dirty_canonical_blocked(store, prom):
    canon = prom.canon
    (canon / "third-party.txt").write_text("dirty\n", encoding="utf-8")
    res = zprom.promote(store, prom.run_id, prom.stage_id, git_root=canon,
                        staged_commit=prom.staged,
                        expected_head=prom.expected_head,
                        expected_dirty_digest="")
    assert res["ok"] is False and res["reason"] == "DIRTY_OR_DRIFT"
    assert res["detail"]["clean"] is False
    # repo untouched, nothing transitioned
    assert head(canon) == prom.expected_head
    assert (canon / "staged.txt").exists() is False
    assert prom.intent()["state"] == "INTENDED"
    assert prom.stage()["state"] == "STAGED"


def test_promote_dirty_digest_mismatch_blocked(store, prom):
    canon = prom.canon
    wrong = zprom.CLEAN_DIGEST.replace("e", "a")  # clean repo, wrong digest
    res = zprom.promote(store, prom.run_id, prom.stage_id, git_root=canon,
                        staged_commit=prom.staged,
                        expected_head=prom.expected_head,
                        expected_dirty_digest=wrong)
    assert res["ok"] is False and res["reason"] == "DIRTY_OR_DRIFT"
    assert head(canon) == prom.expected_head
    assert prom.intent()["state"] == "INTENDED"


def test_promote_head_drift_blocked(store, prom):
    canon = prom.canon
    # a third party commits onto the canonical branch after the intent
    moved = commit(canon, "third party", "moved\n")
    res = zprom.promote(store, prom.run_id, prom.stage_id, git_root=canon,
                        staged_commit=prom.staged,
                        expected_head=prom.expected_head,
                        expected_dirty_digest="")
    assert res["ok"] is False and res["reason"] == "HEAD_DRIFT"
    assert res["detail"] == {"head": moved, "expected": prom.expected_head}
    assert head(canon) == moved                     # untouched
    assert prom.intent()["state"] == "INTENDED"
    assert prom.stage()["state"] == "STAGED"


def test_promote_not_descendant_blocked(store, prom):
    canon = prom.canon
    # staged is a child of the OLD head; canonical meanwhile moved on, so the
    # staged commit is no longer a descendant — refusing keeps linear history
    moved = commit(canon, "third party", "moved\n")
    res = zprom.promote(store, prom.run_id, prom.stage_id, git_root=canon,
                        staged_commit=prom.staged,
                        expected_head=moved,
                        expected_dirty_digest="")
    assert res["ok"] is False and res["reason"] == "NOT_DESCENDANT"
    assert head(canon) == moved                     # untouched
    assert prom.intent()["state"] == "INTENDED"
    # the ancestor itself (no movement) also is not a valid promotion target
    base_ancestor = git("rev-parse", "HEAD^", cwd=canon).strip()
    store.conn.execute(
        "UPDATE promotion_intents SET staged_head=? WHERE intent_id=?",
        (base_ancestor, prom.intent_id))
    res2 = zprom.promote(store, prom.run_id, prom.stage_id, git_root=canon,
                         staged_commit=base_ancestor,
                         expected_head=moved,
                         expected_dirty_digest="")
    assert res2["ok"] is False and res2["reason"] == "NOT_DESCENDANT"


def test_promote_requires_intent_and_stage(store, prom, tmp_path):
    canon = prom.canon
    # no INTENDED intent for this staged commit: unfenced promotion refused
    store.conn.execute(
        "UPDATE promotion_intents SET state='APPLIED' WHERE intent_id=?",
        (prom.intent_id,))
    res = zprom.promote(store, prom.run_id, prom.stage_id, git_root=canon,
                        staged_commit=prom.staged,
                        expected_head=prom.expected_head,
                        expected_dirty_digest="")
    assert res["ok"] is False and res["reason"] == "NO_INTENT"
    assert head(canon) == prom.expected_head       # repo untouched
    # unknown stage row: refused before any repo write
    staged = staged_commit(canon, tmp_path, prom.run_id, "S02")
    store.conn.execute(
        "INSERT INTO promotion_intents(intent_id, run_id, stage_id,"
        " stage_revision, expected_canonical_head, expected_dirty_digest,"
        " staged_head, state, created_at)"
        " VALUES ('int-x', ?, 'S99', 1, ?, '', ?, 'INTENDED', ?)",
        (prom.run_id, prom.expected_head, staged, zids.now_iso()))
    res2 = zprom.promote(store, prom.run_id, "S99", git_root=canon,
                         staged_commit=staged,
                         expected_head=prom.expected_head,
                         expected_dirty_digest="")
    assert res2["ok"] is False and res2["reason"] == "UNKNOWN_STAGE"
    assert head(canon) == prom.expected_head


# ---- reconcile_dangling (VOL-11 §4 table) ------------------------------------

def test_reconcile_recovered_when_ref_already_at_staged(store, prom):
    canon = prom.canon
    # crash window: the physical ff-only promotion already happened, S did not
    git("merge", "--ff-only", prom.staged, cwd=canon)
    before = head(canon)
    res = zprom.reconcile_dangling(store, prom.run_id, canon)
    assert len(res) == 1
    assert res[0]["classification"] == "RECOVERED"
    assert res[0]["intent_id"] == prom.intent_id
    assert prom.intent()["state"] == "RECOVERED"
    # no double apply: the canonical is byte-identical afterwards
    assert head(canon) == before
    assert git("status", "--porcelain", cwd=canon) == ""
    ev = [r for r in store.conn.execute(
        "SELECT detail_json FROM events WHERE kind='promotion_recovered'")]
    assert len(ev) == 1 and '"re_applied": false' in ev[0]["detail_json"]
    assert prom.stage()["state"] == "STAGED"  # reconcile never touches stages


def test_reconcile_retryable_when_ref_still_at_expected_head(store, prom):
    canon = prom.canon
    res = zprom.reconcile_dangling(store, prom.run_id, canon)
    assert res == [{"intent_id": prom.intent_id, "stage_id": prom.stage_id,
                    "classification": "INTENDED",
                    "detail": "canonical still at the expected old head,"
                              " clean — safe to retry"}]
    assert prom.intent()["state"] == "INTENDED"    # left retryable
    assert head(canon) == prom.expected_head
    # and the retry does succeed
    ok = zprom.promote(store, prom.run_id, prom.stage_id, git_root=canon,
                       staged_commit=prom.staged,
                       expected_head=prom.expected_head,
                       expected_dirty_digest="")
    assert ok["ok"] is True and ok["new_head"] == prom.staged


def test_reconcile_blocked_on_third_party_change(store, prom):
    canon = prom.canon
    commit(canon, "third party", "moved\n")
    res = zprom.reconcile_dangling(store, prom.run_id, canon)
    assert res[0]["classification"] == "BLOCKED"
    assert prom.intent()["state"] == "BLOCKED"
    assert "promotion_blocked" in _kinds(store)


def test_reconcile_blocked_on_dirty_worktree(store, prom):
    canon = prom.canon
    (canon / "scratch.txt").write_text("someone was here\n", encoding="utf-8")
    res = zprom.reconcile_dangling(store, prom.run_id, canon)
    assert res[0]["classification"] == "BLOCKED"
    assert prom.intent()["state"] == "BLOCKED"


def test_reconcile_blocked_when_trailers_do_not_match(store, prom):
    canon = prom.canon
    # HEAD sits at a commit that is NOT ours (no ZLoop trailers) — even
    # though it happens to equal the intent's staged_head, we must not
    # blindly call it RECOVERED
    stranger = commit(canon, "stranger commit", "x\n")
    store.conn.execute(
        "UPDATE promotion_intents SET staged_head=? WHERE intent_id=?",
        (stranger, prom.intent_id))
    res = zprom.reconcile_dangling(store, prom.run_id, canon)
    assert res[0]["classification"] == "BLOCKED"
    assert prom.intent()["state"] == "BLOCKED"


def test_reconcile_no_dangling_intents(store, prom):
    store.conn.execute(
        "UPDATE promotion_intents SET state='APPLIED' WHERE intent_id=?",
        (prom.intent_id,))
    assert zprom.reconcile_dangling(store, prom.run_id, prom.canon) == []
