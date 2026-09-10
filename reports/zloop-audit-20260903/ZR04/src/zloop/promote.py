"""zloop.promote — controlled canonical writes + dangling reconciliation
(VOL-11 §2/§3).

I30: promotion is a compare-and-set — the canonical HEAD must equal the
expected head AND the worktree dirty digest must be unchanged, or the
promotion is refused (DIRTY_OR_DRIFT / HEAD_DRIFT) with the repo untouched.

I39: never a bare ``git update-ref`` on the checked-out canonical branch
(that splits ref/index/worktree) — the promotion is a checked-out-safe
``git merge --ff-only`` on the checked-out branch, and the staged commit
must first be proven a descendant of the current HEAD.

VOL-11 §4: Git is the physical oracle for dangling promotion intents. When a
crash lands between the intent and the S COMMIT, ``reconcile_dangling``
inspects the physical canonical state against each INTENDED intent and
classifies it — RECOVERED (already applied; never double-apply), INTENDED
(untouched and safe to retry) or BLOCKED (third-party change / cannot
determine; fail-visible).
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from . import ids

# sha256(b"") — the digest of a pristine-clean `git status --porcelain=v2 -z`.
# VOL-08 §3 stores the empty STRING for a clean canonical base, so empty
# expectations are normalized to this constant before comparison.
CLEAN_DIGEST = hashlib.sha256(b"").hexdigest()


def _git(args: list[str], cwd: Path,
         timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          timeout=timeout)


def _err(proc: subprocess.CompletedProcess, limit: int = 300) -> str:
    return proc.stderr.decode("utf-8", errors="replace").strip()[:limit]


# ---------------------------------------------------------------- dirty CAS


def dirty_state(git_root: Path) -> dict:
    """sha256 over the sorted ``git status --porcelain=v2 -z`` output.

    VOL-08 §3 semantics: an empty status IS clean. The NUL-separated records
    are sorted before hashing so the digest is order-independent; a pristine
    worktree therefore always yields ``{"digest": CLEAN_DIGEST, "clean": True}``
    while ANY working-tree or index change (modified/staged/deleted/untracked)
    yields a different digest with ``clean: False``.
    """
    proc = _git(["status", "--porcelain=v2", "-z"], Path(git_root))
    if proc.returncode != 0:
        raise RuntimeError(
            f"git status failed in {git_root}: {_err(proc)}")
    tokens = [t for t in proc.stdout.split(b"\x00") if t]
    payload = b"\x00".join(sorted(tokens))
    return {"digest": hashlib.sha256(payload).hexdigest(),
            "clean": payload == b""}


def _head(git_root: Path) -> str:
    proc = _git(["rev-parse", "HEAD"], git_root)
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse failed in {git_root}: {_err(proc)}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------- promotion


def promote(store, run_id: str, stage_id: str, *, git_root: Path,
            staged_commit: str, expected_head: str,
            expected_dirty_digest: str) -> dict:
    """CAS + ff-only promotion of a staged commit onto the canonical branch.

    Order (all pre-checks happen before a single repo write, so every failure
    leaves the repo untouched):
      1. an INTENDED promotion intent for exactly this staged commit must
         exist (VOL-11 §2 intent-first ordering — an unfenced promotion is
         refused), and the stage row must exist;
      2. canonical worktree clean and dirty digest unchanged (I30);
      3. canonical HEAD == expected_head (I30);
      4. staged_commit must be a descendant of HEAD;
      5. ``git merge --ff-only <staged_commit>`` (I39 — checked-out-safe);
      6. one S mutation: intent(s) APPLIED, stage PROMOTED, events.
    """
    git_root = Path(git_root)
    intents = [dict(r) for r in store.conn.execute(
        "SELECT * FROM promotion_intents WHERE run_id=? AND stage_id=?"
        " AND state='INTENDED' AND staged_head=?",
        (run_id, stage_id, staged_commit))]
    if not intents:
        return {"ok": False, "reason": "NO_INTENT",
                "detail": "no INTENDED promotion intent matches this staged"
                          " commit (VOL-11 §2 intent-first ordering)"}
    stage = store.conn.execute(
        "SELECT * FROM stages WHERE run_id=? AND stage_id=?",
        (run_id, stage_id)).fetchone()
    if stage is None:
        return {"ok": False, "reason": "UNKNOWN_STAGE",
                "detail": f"unknown stage {run_id}/{stage_id}"}

    # (2) dirty CAS (I30). '' is the VOL-08 §3 clean spelling.
    expected_digest = expected_dirty_digest or CLEAN_DIGEST
    try:
        dirty = dirty_state(git_root)
    except RuntimeError as e:
        return {"ok": False, "reason": "GIT_ERROR", "detail": str(e)[:300]}
    if not dirty["clean"] or dirty["digest"] != expected_digest:
        return {"ok": False, "reason": "DIRTY_OR_DRIFT",
                "detail": {"clean": dirty["clean"],
                           "digest": dirty["digest"],
                           "expected": expected_digest}}

    # (3) HEAD CAS (I30)
    try:
        head = _head(git_root)
    except RuntimeError as e:
        return {"ok": False, "reason": "GIT_ERROR", "detail": str(e)[:300]}
    if head != expected_head:
        return {"ok": False, "reason": "HEAD_DRIFT",
                "detail": {"head": head, "expected": expected_head}}

    # (4) staged must be a descendant of the current HEAD (VOL-11 §2)
    anc = _git(["merge-base", "--is-ancestor", head, staged_commit], git_root)
    if anc.returncode != 0:
        return {"ok": False, "reason": "NOT_DESCENDANT",
                "detail": {"head": head, "staged": staged_commit}}

    # (5) checked-out-safe promotion — never update-ref on the checked-out
    # branch (I39)
    merge = _git(["merge", "--ff-only", staged_commit], git_root)
    if merge.returncode != 0:
        return {"ok": False, "reason": "MERGE_FAILED",
                "detail": _err(merge)}
    new_head = _head(git_root)
    if new_head != staged_commit:  # --ff-only must land exactly on staged
        return {"ok": False, "reason": "MERGE_UNEXPECTED",
                "detail": {"new_head": new_head, "staged": staged_commit}}

    # (6) one S mutation: intent APPLIED + stage PROMOTED + events
    with store.mutation():
        store.conn.execute(
            "UPDATE promotion_intents SET state='APPLIED', resolved_at=?"
            " WHERE run_id=? AND stage_id=? AND staged_head=?"
            " AND state='INTENDED'",
            (ids.now_iso(), run_id, stage_id, staged_commit))
        store.conn.execute(
            "UPDATE stages SET state='PROMOTED', updated_at=?"
            " WHERE run_id=? AND stage_id=?"
            " AND state IN ('STAGED','PROMOTING')",
            (ids.now_iso(), run_id, stage_id))
        store._event("promotion_applied",
                     {"staged_head": staged_commit, "new_head": new_head,
                      "expected_head": expected_head},
                     run_id=run_id, stage_id=stage_id)
        store._event("stage_promoted", {"head": new_head},
                     run_id=run_id, stage_id=stage_id)
    return {"ok": True, "new_head": new_head,
            "intents_applied": len(intents)}


# ------------------------------------------------------------ reconciliation


def _trailers_match(git_root: Path, commit: str, run_id: str,
                     stage_id: str) -> bool:
    """True when the commit carries this run/stage's ZLoop provenance
    trailers (materialization/final-staging commits always do)."""
    proc = _git(["log", "-1", "--format=%B", commit], git_root)
    if proc.returncode != 0:
        return False
    body = proc.stdout.decode("utf-8", errors="replace")
    return (f"ZLoop-Run: {run_id}" in body) and (f"ZLoop-Stage: {stage_id}" in body)


def reconcile_dangling(store, run_id: str, git_root: Path) -> list[dict]:
    """Classify every dangling promotion intent (VOL-11 §4 table).

    For each ``promotion_intents(state=INTENDED)`` of the run, inspect the
    physical oracle (Git) in the canonical worktree:

    - HEAD == staged_head and the staged commit carries this run/stage's
      ZLoop trailers -> the physical effect already happened: mark the intent
      RECOVERED (no double apply);
    - HEAD == expected_canonical_head and the worktree is clean -> nothing
      happened yet: leave INTENDED (safe to retry);
    - anything else (third-party ref move, dirty worktree, unreadable Git
      state) -> BLOCKED (fail-visible, needs a human/replan).
    """
    git_root = Path(git_root)
    out: list[dict] = []
    rows = [dict(r) for r in store.conn.execute(
        "SELECT * FROM promotion_intents WHERE run_id=? AND state='INTENDED'"
        " ORDER BY created_at, intent_id", (run_id,))]
    for row in rows:
        stage_id = row["stage_id"]
        entry = {"intent_id": row["intent_id"], "stage_id": stage_id}
        try:
            head = _head(git_root)
        except RuntimeError as e:
            entry.update({"classification": "BLOCKED",
                         "detail": f"git unreadable: {str(e)[:200]}"})
            _mark_blocked(store, run_id, stage_id,
                              row["intent_id"], entry["detail"])
            out.append(entry)
            continue

        if head == row["staged_head"]:
            if _trailers_match(git_root, row["staged_head"], run_id, stage_id):
                entry.update({"classification": "RECOVERED",
                              "detail": "canonical already at staged_head with"
                                        " matching ZLoop trailers"})
                with store.mutation():
                    store.conn.execute(
                        "UPDATE promotion_intents SET state='RECOVERED',"
                        " resolved_at=? WHERE intent_id=?",
                        (ids.now_iso(), row["intent_id"]))
                    store._event("promotion_recovered",
                                 {"intent_id": row["intent_id"],
                                  "staged_head": row["staged_head"],
                                  "re_applied": False},
                                 run_id=run_id, stage_id=stage_id)
            else:
                entry.update({"classification": "BLOCKED",
                              "detail": "canonical at staged_head but ZLoop"
                                        " trailers do not match"})
                _mark_blocked(store, run_id, stage_id,
                              row["intent_id"], entry["detail"])
        elif head == row["expected_canonical_head"]:
            try:
                dirty = dirty_state(git_root)
            except RuntimeError as e:
                entry.update({"classification": "BLOCKED",
                             "detail": str(e)[:200]})
                _mark_blocked(store, run_id, stage_id,
                              row["intent_id"], entry["detail"])
                out.append(entry)
                continue
            if dirty["clean"]:
                entry.update({"classification": "INTENDED",
                              "detail": "canonical still at the expected old"
                                        " head, clean — safe to retry"})
            else:
                entry.update({"classification": "BLOCKED",
                              "detail": "canonical at the expected old head but"
                                        " worktree dirty (third-party change)"})
                _mark_blocked(store, run_id, stage_id,
                              row["intent_id"], entry["detail"])
        else:
            entry.update({"classification": "BLOCKED",
                          "detail": "canonical HEAD matches neither staged"
                                    " nor expected (third-party change)"})
            _mark_blocked(store, run_id, stage_id,
                              row["intent_id"], entry["detail"])
        out.append(entry)
    return out


def _mark_blocked(store, run_id: str, stage_id: str, intent_id: str,
                  detail: str) -> None:
    with store.mutation():
        store.conn.execute(
            "UPDATE promotion_intents SET state='BLOCKED', resolved_at=?"
            " WHERE intent_id=?", (ids.now_iso(), intent_id))
        store._event("promotion_blocked",
                     {"intent_id": intent_id, "detail": detail},
                     run_id=run_id, stage_id=stage_id)
