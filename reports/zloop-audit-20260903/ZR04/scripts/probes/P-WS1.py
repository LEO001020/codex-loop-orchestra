#!/usr/bin/env python3
"""P-WS1: worktree_fast isolation probe (VOL-13 §2/§4/§5, VOL-10 §2).

Creates a temp git repo + worktree and records: (a) worktree create
latency; (b) git-admin isolation — `git update-ref` run from INSIDE the
worktree (worktrees share the common refs, so it SUCCEEDS: capability is
FALSE for worktree_fast, HIGH/CRITICAL must use clone_strong); (c)
enumerate_delta correctness (untracked / modified / staged rename);
(d) paths_within_scope violation cases. Also runs the clone_strong tier for
the two-tier comparison (refs in a clone do NOT reach the canonical repo).

PASS means the probe produced data — NOT that worktree_fast is safe.
Writes only to artifacts/probes/P-WS1.json; everything else is temp.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from zloop import workspace as zw  # noqa: E402

OUT = REPO / "artifacts" / "probes" / "P-WS1.json"


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd),
                          capture_output=True, text=True)


def git_ok(*args: str, cwd: Path) -> str:
    p = git(*args, cwd=cwd)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout


def main() -> int:
    td = Path(tempfile.mkdtemp(prefix="p-ws1-"))
    findings: dict = {}
    try:
        # ---- temp canonical repo ------------------------------------------
        repo = td / "repo"
        repo.mkdir()
        git_ok("init", "-q", "-b", "main", cwd=repo)
        git_ok("config", "user.email", "probe@zloop.local", cwd=repo)
        git_ok("config", "user.name", "P-WS1", cwd=repo)
        (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        (repo / "old_name.txt").write_text("rename me\n", encoding="utf-8")
        (repo / "src" / "foo").mkdir(parents=True)
        (repo / "src" / "foo" / "a.py").write_text("x = 1\n", encoding="utf-8")
        git_ok("add", "-A", cwd=repo)
        git_ok("commit", "-q", "-m", "init", cwd=repo)

        # ---- (a) worktree create latency + ok -----------------------------
        wt = td / "ws" / "launch-1"
        wt.parent.mkdir(parents=True)
        t0 = time.perf_counter()
        r = zw.create_worktree(repo, wt)
        create_ms = (time.perf_counter() - t0) * 1000.0
        findings["worktree_create"] = {
            "ok": r["ok"], "latency_ms": round(create_ms, 1),
            "stderr_summary": r.get("stderr_summary", "")[:300],
            "detached_head": git_ok("rev-parse", "--abbrev-ref", "HEAD",
                                    cwd=wt).strip() == "HEAD",
            "content_present": (wt / "tracked.txt").is_file(),
        }

        # ---- (b) git-admin isolation: update-ref from INSIDE the worktree --
        p = git("update-ref", "refs/heads/probe-branch", "HEAD", cwd=wt)
        visible = git("rev-parse", "--verify", "refs/heads/probe-branch",
                      cwd=repo)
        succeeded = p.returncode == 0
        findings["git_admin_isolation_worktree_fast"] = {
            "attempt": "git update-ref refs/heads/probe-branch HEAD (cwd=worktree)",
            "succeeded": succeeded,
            "stderr": (p.stderr or "").strip()[:300],
            "shared_ref_visible_from_canonical_repo": visible.returncode == 0,
            "capability_git_admin_isolation": not succeeded,
            "conclusion": (
                "worktrees share the common Git objects/refs administration: "
                "a worker inside a worktree CAN mutate canonical refs/config. "
                "worktree_fast does NOT isolate Git administration "
                "(VOL-13 §2); HIGH/CRITICAL must use clone_strong."
            ),
        }

        # ---- (c) enumerate_delta correctness -------------------------------
        (wt / "untracked_probe.txt").write_text("new\n", encoding="utf-8")
        (wt / "tracked.txt").write_text("modified\n", encoding="utf-8")
        git_ok("mv", "old_name.txt", "new_name.txt", cwd=wt)  # staged rename
        entries = zw.enumerate_delta(wt)
        untracked = [e["path"] for e in entries if e["record_type"] == "?"]
        modified = [e for e in entries if e["record_type"] == "1"
                   and e["path"] == "tracked.txt"]
        renamed = [e for e in entries if e["record_type"] == "2"]
        findings["enumerate_delta"] = {
            "command": "git status --porcelain=v2 -z --untracked-files=all",
            "record_count": len(entries),
            "untracked_detected": {
                "ok": "untracked_probe.txt" in untracked,
                "paths": untracked},
            "modified_detected": {
                "ok": len(modified) == 1 and "M" in modified[0]["xy"],
                "record": modified},
            "rename_detected": {
                "ok": len(renamed) == 1
                and renamed[0].get("path") == "new_name.txt"
                and renamed[0].get("orig_path") == "old_name.txt",
                "records": renamed},
        }

        # ---- (d) paths_within_scope violation cases -------------------------
        scope = ["src/foo/**"]
        cases = [{"path": "../escape.txt"},     # traversal
                 {"path": ".git/config"},       # Git-admin escape
                 {"path": "other/x"},           # plain out-of-scope
                 {"path": "src/foo/a.py"}]      # in-scope control
        ok, violations = zw.paths_within_scope(cases, scope)
        findings["paths_within_scope"] = {
            "write_scope": scope,
            "cases": [c["path"] for c in cases],
            "expected_violations": ["../escape.txt", ".git/config", "other/x"],
            "actual_violations": violations,
            "in_scope_control_accepted": "src/foo/a.py" not in violations,
            "ok": (not ok) and violations ==
                  ["../escape.txt", ".git/config", "other/x"],
        }

        # ---- clone_strong tier for the VOL-13 §5 two-tier comparison -------
        clone = td / "clone-1"
        t1 = time.perf_counter()
        cr = zw.create_clone(repo, clone)
        clone_ms = (time.perf_counter() - t1) * 1000.0
        cp = git("update-ref", "refs/heads/probe-branch-2", "HEAD", cwd=clone)
        leaked = git("rev-parse", "--verify", "refs/heads/probe-branch-2",
                     cwd=repo)
        findings["clone_strong"] = {
            "ok": cr["ok"], "latency_ms": round(clone_ms, 1),
            "note": cr.get("note"),
            "update_ref_inside_clone_succeeded": cp.returncode == 0,
            "ref_reached_canonical_repo": leaked.returncode == 0,
            "capability_git_admin_isolation": leaked.returncode != 0,
        }

        # ---- cleanup (also exercises remove_worktree) -----------------------
        rm = zw.remove_worktree(wt)
        findings["worktree_remove"] = {
            "ok": rm["ok"], "removed": rm["removed"], "pruned": rm["pruned"],
            "stderr_summary": rm.get("stderr_summary", "")[:300],
        }

        capabilities = {
            "worktree_fast": {
                "git_admin_isolation":
                    findings["git_admin_isolation_worktree_fast"]["capability_git_admin_isolation"],
                "verdict": "NORMAL only; requires sandbox proof + host-side "
                           "full verification (VOL-13 §2)",
            },
            "clone_strong": {
                "git_admin_isolation":
                    findings["clone_strong"]["capability_git_admin_isolation"],
                "verdict": "HIGH/CRITICAL default",
            },
        }
    finally:
        shutil.rmtree(td, ignore_errors=True)

    report = {
        "probe_id": "P-WS1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "status_note": "PASS = probe produced data, not that worktree_fast is safe",
        "findings": findings,
        "capabilities": capabilities,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"P-WS1 -> {OUT}")
    print(json.dumps({"status": report["status"], "capabilities": capabilities},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
