"""zloop.cli — root-side command surface (VOL-22 §9 root loop, VOL-05 §4 bind-token).

Exit codes (contract):
  0 ok · 2 usage · 3 S_DEGRADED (S degraded, fail-closed I4) ·
  4 bind-token wait timeout (P2-13 foreground constraint) ·
  5 blocked (precondition not met).

Parallel modules (zloop.history / zloop.checkpoint / zloop.install /
zloop.hook) are imported lazily inside their command handlers; when one is
absent the command prints "module not available (parallel integration
pending)" and exits 0 — never a crash, and never a fake success claim about
a plane that is not there. The same tolerance covers the still-parallel
zloop.supervisor (wave start) and zloop.research.broker (research run).

Milestone-4/6 surface (VOL-08 / VOL-09): stage begin/status/close, wave
propose/start/cancel. M8 (VOL-11 §2): stage promote. run start / stage begin /
wave start are FOREGROUND commands (keep <600s; long waves belong to the
future await path).
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import __version__, c2c, db, ids, paths, stage, wave, workspace

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_S_DEGRADED = 3
EXIT_BIND_TIMEOUT = 4
EXIT_BLOCKED = 5

MODULE_UNAVAILABLE = "module not available (parallel integration pending)"
RESEARCH_MODULE_PENDING = "module pending (parallel integration)"
CODEX_AUTH_BROKEN = ("codex backend requires `codex login` "
                     "(auth currently broken on this machine)")
FOREGROUND_NOTE = "run this in FOREGROUND so the PostToolUse hook can claim the token"
LEGACY_CODEX_REQUIREMENTS = Path("C:/ProgramData/OpenAI/Codex/requirements.toml")
CLAIM_TTL_S = 120  # I32: one-time bind token, TTL 120s


class CliError(Exception):
    """Command failed with a specific exit code (message goes to stderr)."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# ---- small helpers --------------------------------------------------------


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _lazy_import(module_name: str):
    """Import a parallel module tolerantly; None when absent (integration pending)."""
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


def _git_root(cwd: Optional[Path] = None) -> Path:
    cwd = Path(cwd or Path.cwd())
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           cwd=str(cwd), capture_output=True, text=True, timeout=15)
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip()).resolve()
    except Exception:
        pass
    return cwd.resolve()


def _require_project() -> tuple[str, Path]:
    """Resolve the project for the cwd git root. Never guesses by cwd for
    binding purposes — the project must already be registered (I32)."""
    root = _git_root()
    pid = paths.find_project_by_git_root(str(root))
    if pid is None:
        raise CliError(EXIT_BLOCKED,
                       f"no zloop project registered for {root} — "
                       f"run 'zloop project attach' or 'zloop run start' first")
    return pid, paths.project_dir(pid)


def _open_store(pdir: Path, project_id: str) -> tuple["db.ControlStore", Any]:
    conn = db.connect(pdir, create=True)
    return db.ControlStore(pdir, conn, project_id=project_id), conn


def _seconds_to_expiry(expires_at: Optional[str]) -> Optional[int]:
    if not expires_at:
        return None
    dt = ids.parse_iso(expires_at)
    if dt is None:
        return None
    return int((dt - datetime.now(timezone.utc)).total_seconds())


# ---- run / stage resolution + git evidence (M4/M6 surface) -----------------


def _sorted_runs(store: "db.ControlStore") -> list[dict]:
    # created_at has second resolution — run_id is the deterministic tiebreak
    return sorted(store.runs(), key=lambda r: (r["created_at"], r["run_id"]))


def _require_active_run(store: "db.ControlStore") -> dict:
    """The latest ACTIVE run of the project (stage/wave commands operate on
    exactly one live run; no guessing across closed history)."""
    active = [r for r in _sorted_runs(store) if r["state"] == "ACTIVE"]
    if not active:
        raise CliError(EXIT_BLOCKED,
                       "no ACTIVE run in this project — run 'zloop run start' first")
    return active[-1]


def _current_run(store: "db.ControlStore") -> dict:
    """Latest ACTIVE run, else the latest run at all — stage status/close on a
    closed run's still-open stage is the verify-run remediation path."""
    runs = _sorted_runs(store)
    active = [r for r in runs if r["state"] == "ACTIVE"]
    if active:
        return active[-1]
    if not runs:
        raise CliError(EXIT_BLOCKED,
                       "no runs in this project — run 'zloop run start' first")
    return runs[-1]


def _open_stage(store: "db.ControlStore", run_id: str) -> dict:
    """The latest stage of the run that can still take waves (VOL-08 §6)."""
    rows = [dict(r) for r in store.conn.execute(
        "SELECT * FROM stages WHERE run_id=? ORDER BY created_at, stage_id",
        (run_id,))]
    for row in reversed(rows):
        if row["state"] in _STAGE_OPEN_STATES:
            return row
    raise CliError(EXIT_BLOCKED,
                   f"no open stage in run {run_id} — run 'zloop stage begin' first")


def _project_git_root(pid: str) -> Path:
    rec = paths.load_registry()["projects"].get(pid, {})
    root = str(rec.get("git_root") or "").strip()
    return Path(root) if root else _git_root()


def _git_out(repo: Path, *git_args: str) -> str:
    try:
        p = subprocess.run(["git", *git_args], cwd=str(repo), capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CliError(EXIT_BLOCKED,
                       f"git {' '.join(git_args)} failed in {repo}: {e!r}")
    if p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip()[:300]
        raise CliError(EXIT_BLOCKED,
                       f"git {' '.join(git_args)} failed in {repo}: {detail}")
    return p.stdout.strip()


def _git_dirty(repo: Path) -> bool:
    """[I37] dirty-base check, straight from `git status --porcelain=v2 -z`:
    any non-empty machine output means the canonical worktree is dirty."""
    try:
        p = subprocess.run(["git", "status", "--porcelain=v2", "-z"],
                           cwd=str(repo), capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CliError(EXIT_BLOCKED, f"git status failed in {repo}: {e!r}")
    if p.returncode != 0:
        detail = p.stderr.decode("utf-8", "replace").strip()[:300]
        raise CliError(EXIT_BLOCKED, f"git status failed in {repo}: {detail}")
    return bool(p.stdout)


# ---- bind-token flow (VOL-05 §4) ------------------------------------------


def _emit_bind_token(store: "db.ControlStore", conn: Any, *, purpose: str,
                     run_id: Optional[str], project_id: str,
                     extra: Optional[dict] = None) -> str:
    """Create the one-time claim and print the bind-token block.

    Output order is exact: marker line first (truncation-safe, ≤80 chars),
    then one JSON line. Nothing may be printed before the marker."""
    nonce = store.create_claim(purpose=purpose, run_id=run_id, ttl_s=CLAIM_TTL_S)
    row = conn.execute(
        "SELECT expires_at FROM pending_binding_claims WHERE nonce=?",
        (nonce,)).fetchone()
    expires_at = row["expires_at"] if row is not None else ids.iso_plus(CLAIM_TTL_S)
    print(f"ZLOOP_BIND_TOKEN={nonce}", flush=True)
    payload: dict[str, Any] = {
        "run_id": run_id,
        "project_id": project_id,
        "claim_expires_at": expires_at,
        "note": FOREGROUND_NOTE,
    }
    if extra:
        payload.update(extra)
    _print_json(payload)
    return nonce


def _wait_for_claim(conn: Any, nonce: str, wait_s: int, *, run_id: Optional[str],
                    project_id: str) -> int:
    """Poll S until the PostToolUse hook claims the nonce (VOL-05 §4 step 4).

    Success -> {"bound": true, ...} + exit 0. Timeout/expiry -> WARNING on
    stderr + exit 4 (P2-13: a background Bash tool_response never contains
    the marker, so the token cannot be claimed)."""
    deadline = time.monotonic() + max(0, wait_s)
    while True:
        row = conn.execute(
            "SELECT claimed_at, claimed_by_session, expires_at"
            " FROM pending_binding_claims WHERE nonce=?", (nonce,)).fetchone()
        if row is not None and row["claimed_at"]:
            _print_json({"bound": True, "nonce": nonce,
                         "claimed_by_session": row["claimed_by_session"],
                         "run_id": run_id, "project_id": project_id,
                         "claim_expires_at": row["expires_at"]})
            return EXIT_OK
        if row is not None:
            left = _seconds_to_expiry(row["expires_at"])
            if left is not None and left <= 0:
                break  # expired unclaimed — waiting longer is pointless
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    print("WARNING: token not claimed — likely ran in background; re-run in foreground",
          file=sys.stderr)
    return EXIT_BIND_TIMEOUT


# ---- commands -------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"data root: {paths.zloop_data_root()}")
    prof = db.journal_profile()
    print("journal profile: " + json.dumps(prof, ensure_ascii=False))
    projects = paths.load_registry().get("projects", {})
    print(f"registry: {len(projects)} project(s)")
    for pid in sorted(projects):
        rec = projects[pid]
        name = rec.get("display_name") or ""
        try:
            conn = db.connect(paths.project_dir(pid), create=False)
            try:
                row = conn.execute("PRAGMA quick_check").fetchone()
                qc = str(row[0]).lower() if row is not None else "unknown"
            finally:
                conn.close()
            print(f"project {pid} ({name}): quick_check={qc} "
                  f"journal={prof['journal_mode']}")
        except db.SError as e:
            if "control DB missing" in str(e):
                print(f"project {pid} ({name}): no control DB yet (no runs)")
            else:
                print(f"project {pid} ({name}): S_DEGRADED ({e})")
    # hook deployment status (parallel module, lazy)
    zinstall = _lazy_import("zloop.install")
    if zinstall is None:
        print(f"hooks: {MODULE_UNAVAILABLE}")
    else:
        try:
            print("hooks: " + json.dumps(zinstall.hook_status(), ensure_ascii=False))
        except Exception as e:  # diagnostics must not die on the parallel module
            print(f"hooks: ERROR ({e})")
    if LEGACY_CODEX_REQUIREMENTS.exists():
        print("WARN: old LOOP machine-wide Codex hooks present (P-HYG1)")
    return EXIT_OK


def cmd_project(args: argparse.Namespace) -> int:
    if args.cmd == "attach":
        rec = db.register_project_from_git(args.git_root)
        _print_json(rec)
        return EXIT_OK
    projects = paths.load_registry().get("projects", {})
    _print_json([{"project_id": pid, **rec} for pid, rec in sorted(projects.items())])
    return EXIT_OK


def cmd_run_start(args: argparse.Namespace) -> int:
    rec = db.register_project_from_git()  # ensure registered for cwd git root
    pid = rec["project_id"]
    pdir = paths.ensure_project_layout(pid)
    conn = None
    try:
        with db.RunLock(pdir):  # single controller (I43)
            conn = db.connect(pdir, create=True)
            store = db.ControlStore(pdir, conn, project_id=pid)
            run_id = store.create_run(args.objective)
            nonce = _emit_bind_token(store, conn, purpose="run_start",
                                     run_id=run_id, project_id=pid)
        # run lock released; the read-only claim polling continues without it
        if args.wait_claim is not None:
            return _wait_for_claim(conn, nonce, args.wait_claim,
                                   run_id=run_id, project_id=pid)
        return EXIT_OK
    finally:
        if conn is not None:
            conn.close()


def cmd_run_close(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    with db.RunLock(pdir):
        conn = db.connect(pdir, create=True)
        try:
            store = db.ControlStore(pdir, conn, project_id=pid)
            run = store.run(args.run_id)
            if run is None:
                raise CliError(EXIT_BLOCKED,
                               f"unknown run {args.run_id} in project {pid}")
            if run["state"] == "CLOSED":
                _print_json({"run_id": args.run_id, "state": "CLOSED",
                             "note": "already closed"})
                return EXIT_OK
            if run["state"] != "ACTIVE":
                raise CliError(EXIT_BLOCKED,
                               f"run {args.run_id} is {run['state']}; "
                               f"only ACTIVE runs can be closed")
            store.close_run(args.run_id)
            closed = store.run(args.run_id)
            _print_json({"run_id": args.run_id, "state": closed["state"],
                         "closed_at": closed["closed_at"]})
            return EXIT_OK
        finally:
            conn.close()


def _runs_report(pid: str, pdir: Path, run_id: Optional[str]) -> int:
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        if run_id:
            run = store.run(run_id)
            if run is None:
                raise CliError(EXIT_BLOCKED, f"unknown run {run_id} in project {pid}")
            _print_json(run)
        else:
            _print_json(store.runs())
        return EXIT_OK
    finally:
        conn.close()


def cmd_run_status(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    return _runs_report(pid, pdir, args.run_id)


def cmd_run_list(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    return _runs_report(pid, pdir, None)


def cmd_attach(args: argparse.Namespace) -> int:
    rec = db.register_project_from_git()  # like run start: ensure registered
    pid = rec["project_id"]
    pdir = paths.ensure_project_layout(pid)
    conn = None
    try:
        with db.RunLock(pdir):
            conn = db.connect(pdir, create=True)
            store = db.ControlStore(pdir, conn, project_id=pid)
            if store.run(args.run_id) is None:
                raise CliError(EXIT_BLOCKED,
                               f"unknown run {args.run_id} in project {pid}")
            if args.resume_after_clear:
                # I28 intent: recovery after /clear once the token is claimed.
                # The claim row itself cannot carry the flag (fixed S DDL),
                # so it is recorded as a logical audit event.
                with store.mutation():
                    store._event("attach_resume_after_clear_requested",
                                 {"run_id": args.run_id, "resume_after_clear": True},
                                 run_id=args.run_id)
            nonce = _emit_bind_token(
                store, conn, purpose="attach", run_id=args.run_id, project_id=pid,
                extra={"purpose": "attach",
                       "resume_after_clear": bool(args.resume_after_clear)})
        if args.wait_claim is not None:
            return _wait_for_claim(conn, nonce, args.wait_claim,
                                   run_id=args.run_id, project_id=pid)
        return EXIT_OK
    finally:
        if conn is not None:
            conn.close()


def cmd_detach(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    with db.RunLock(pdir):
        conn = db.connect(pdir, create=True)
        try:
            store = db.ControlStore(pdir, conn, project_id=pid)
            if store.binding(args.session) is None:
                raise CliError(EXIT_BLOCKED, f"no binding for session {args.session}")
            store.detach_session(args.session)
            _print_json({"detached": True, "session_id": args.session})
            return EXIT_OK
        finally:
            conn.close()


def cmd_binding_status(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        if args.session:
            b = store.binding(args.session)
            bindings = [b] if b else []
        else:
            bindings = [dict(r) for r in conn.execute(
                "SELECT * FROM session_bindings WHERE project_id=?"
                " ORDER BY updated_at", (pid,))]
        pending = []
        for c in store.pending_claims():
            c = dict(c)
            c["seconds_to_expiry"] = _seconds_to_expiry(c["expires_at"])
            pending.append(c)
        out: dict[str, Any] = {"project_id": pid, "bindings": bindings,
                               "pending_claims": pending}
        if args.session and not bindings:
            out["note"] = f"no binding for session {args.session}"
        _print_json(out)
        return EXIT_OK
    finally:
        conn.close()


def cmd_verify_run(args: argparse.Namespace) -> int:
    """Goal-completion check (VOL-08 §7): exit 0 only when the run exists,
    is CLOSED, and every stage of the run is terminal (CLOSED/CANCELLED —
    PLANNING/EXECUTING/STAGED/PROMOTING/PROMOTED/BLOCKED are open)."""
    pid, pdir = _require_project()
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        rid = args.run_id
        if rid is None:
            runs = store.runs()
            if not runs:
                raise CliError(EXIT_BLOCKED,
                               f"no runs in project {pid} — nothing to verify")
            rid = runs[-1]["run_id"]
        run = store.run(rid)
        if run is None:
            raise CliError(EXIT_BLOCKED, f"unknown run {rid} in project {pid}")
        stages = [dict(r) for r in conn.execute(
            "SELECT * FROM stages WHERE run_id=? ORDER BY created_at, stage_id",
            (rid,))]
        open_stages = [f"{s['stage_id']}:{s['state']}" for s in stages
                       if s["state"] not in _STAGE_TERMINAL_STATES]
        if run["state"] != "CLOSED":
            reason = f"run state is {run['state']}, expected CLOSED"
        elif open_stages:
            reason = (f"open stages remain: {', '.join(open_stages)} "
                      f"(only CLOSED/CANCELLED stages are terminal)")
        else:
            reason = "run is CLOSED and every stage is terminal (CLOSED/CANCELLED)"
        ok = run["state"] == "CLOSED" and not open_stages
        _print_json({"run_id": rid, "state": run["state"],
                     "objective": run["objective"], "closed_at": run["closed_at"],
                     "stages": {"total": len(stages), "open": open_stages},
                     "verify": "ok" if ok else "blocked", "reason": reason})
        if ok:
            return EXIT_OK
        raise CliError(EXIT_BLOCKED, f"verify-run: {reason}")
    finally:
        conn.close()


def cmd_history(args: argparse.Namespace) -> int:
    hist = _lazy_import("zloop.history")
    if hist is None:
        print(MODULE_UNAVAILABLE)
        return EXIT_OK
    pid, pdir = _require_project()
    if args.cmd == "search":
        results = hist.history_search(pdir, args.query, session=args.session,
                                      run_id=args.run, limit=args.limit)
        _print_json(results)
    else:
        _print_json(hist.history_verify(pdir))
    return EXIT_OK


def cmd_checkpoint(args: argparse.Namespace) -> int:
    cp = _lazy_import("zloop.checkpoint")
    if cp is None:
        print(MODULE_UNAVAILABLE)
        return EXIT_OK
    pid, pdir = _require_project()
    if args.cmd == "write":
        try:
            if args.file is not None:
                capsule = json.loads(Path(args.file).read_text(encoding="utf-8"))
            else:
                capsule = json.loads(sys.stdin.read())
        except (OSError, json.JSONDecodeError) as e:
            raise CliError(EXIT_USAGE, f"capsule is not valid JSON: {e}")
        cp_id = cp.checkpoint_write(pdir, capsule)
        if cp_id is None:
            raise CliError(EXIT_BLOCKED, "checkpoint write failed")
        _print_json({"checkpoint_id": cp_id, "project_id": pid})
        return EXIT_OK
    if args.cmd == "current":
        _print_json({"checkpoint": cp.checkpoint_current(pdir)})
        return EXIT_OK
    capsule = cp.checkpoint_show(pdir, args.checkpoint_id)
    if capsule is None:
        raise CliError(EXIT_BLOCKED, f"unknown checkpoint {args.checkpoint_id}")
    _print_json(capsule)
    return EXIT_OK


# ---- stage commands (VOL-08, milestone 4/6) --------------------------------

_STAGE_TERMINAL_STATES = ("CLOSED", "CANCELLED")
_STAGE_OPEN_STATES = ("PLANNING", "EXECUTING", "STAGED", "PROMOTING", "PROMOTED")
# stage close walks the FSM forward to CLOSED; a BLOCKED stage can only be
# cancelled (VOL-08 §4 guard table: BLOCKED -> CANCELLED is its only exit).
_STAGE_CLOSE_PATH: dict[str, tuple[str, ...]] = {
    "PLANNING": ("EXECUTING", "STAGED", "PROMOTING", "PROMOTED", "CLOSED"),
    "EXECUTING": ("STAGED", "PROMOTING", "PROMOTED", "CLOSED"),
    "STAGED": ("PROMOTING", "PROMOTED", "CLOSED"),
    "PROMOTING": ("PROMOTED", "CLOSED"),
    "PROMOTED": ("CLOSED",),
    "BLOCKED": ("CANCELLED",),
}


def cmd_stage(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    if args.cmd == "begin":
        return _stage_begin(args, pid, pdir)
    if args.cmd == "status":
        return _stage_status(args, pid, pdir)
    if args.cmd == "promote":
        return _stage_promote(args, pid, pdir)
    return _stage_close(args, pid, pdir)


def _stage_begin(args: argparse.Namespace, pid: str, pdir: Path) -> int:
    """Lock a provably-clean stage base [I37] and create the next stage of
    the ACTIVE run in PLANNING (VOL-08 §3)."""
    repo = _project_git_root(pid)
    with db.RunLock(pdir):
        conn = db.connect(pdir, create=True)
        try:
            store = db.ControlStore(pdir, conn, project_id=pid)
            run = _require_active_run(store)
            head = _git_out(repo, "rev-parse", "HEAD")
            if _git_dirty(repo):
                raise CliError(
                    EXIT_BLOCKED,
                    "stage begin blocked: BLOCKED_DIRTY_BASE — the canonical "
                    "worktree is dirty (git status --porcelain=v2 -z is "
                    "non-empty, I37 / VOL-08 §3); commit your work first, "
                    "then retry")
            tree = _git_out(repo, "rev-parse", "HEAD^{tree}")
            try:
                st = stage.create_stage(
                    store, run["run_id"], args.objective, args.risk,
                    expected_head=head, dirty_digest="",
                    stage_base_ref=head, stage_base_tree=tree)
            except ValueError as e:
                raise CliError(EXIT_BLOCKED, f"stage begin failed: {e}")
        finally:
            conn.close()
    _print_json(st)
    return EXIT_OK


def _stage_status(args: argparse.Namespace, pid: str, pdir: Path) -> int:
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        run = _current_run(store)
        if args.stage_id:
            st = stage.get_stage(store, run["run_id"], args.stage_id)
            if st is None:
                raise CliError(EXIT_BLOCKED,
                               f"unknown stage {args.stage_id} in run "
                               f"{run['run_id']} (project {pid})")
            _print_json(st)
        else:
            _print_json([dict(r) for r in store.conn.execute(
                "SELECT * FROM stages WHERE run_id=? ORDER BY created_at, stage_id",
                (run["run_id"],))])
        return EXIT_OK
    finally:
        conn.close()


def _stage_close(args: argparse.Namespace, pid: str, pdir: Path) -> int:
    """Move a stage to a terminal state: CLOSED along the legal FSM path
    (each hop audited by stage.transition_stage); BLOCKED -> CANCELLED."""
    with db.RunLock(pdir):
        conn = db.connect(pdir, create=True)
        try:
            store = db.ControlStore(pdir, conn, project_id=pid)
            run = _current_run(store)
            st = stage.get_stage(store, run["run_id"], args.stage_id)
            if st is None:
                raise CliError(EXIT_BLOCKED,
                               f"unknown stage {args.stage_id} in run "
                               f"{run['run_id']} (project {pid})")
            current = st["state"]
            if current in _STAGE_TERMINAL_STATES:
                _print_json({**st,
                             "note": f"stage {args.stage_id} is already terminal "
                                     f"({current})"})
                return EXIT_OK
            hops: list[str] = []
            try:
                for to_state in _STAGE_CLOSE_PATH[current]:
                    st = stage.transition_stage(store, run["run_id"], args.stage_id,
                                               to_state)
                    hops.append(to_state)
            except ValueError as e:
                raise CliError(EXIT_BLOCKED, f"stage close failed: {e}")
            _print_json({**st, "closed_via": hops})
            return EXIT_OK
        finally:
            conn.close()


# ---- stage promote (VOL-11 §2, M8) ------------------------------------------

# The packet states that allow promotion (VOL-09 §7): every packet of the
# stage must have settled, and at least one produced a materialized snapshot.
_PROMOTE_PACKET_TERMINAL = ("MATERIALIZED", "FAILED", "BLOCKED", "CANCELLED")
_C2C_GATE_RISK = ("HIGH", "CRITICAL")


def _c2c_recorded_roles(store, rid: str, sid: str) -> set:
    """Roles of the c2c_recorded events for this run+stage (D-25). The role
    lives in the event detail_json; corrupt details are skipped (fail-closed
    means 'does not count', never 'crashes the gate')."""
    roles = set()
    for r in store.conn.execute(
            "SELECT detail_json FROM events WHERE kind='c2c_recorded'"
            " AND run_id=? AND stage_id=?", (rid, sid)):
        try:
            d = json.loads(r["detail_json"])
        except Exception:
            continue
        role = d.get("role")
        if isinstance(role, str):
            roles.add(role)
    return roles


def _stage_promote(args: argparse.Namespace, pid: str, pdir: Path) -> int:
    """Promote a fully-materialized stage onto the canonical branch (M8,
    VOL-11 §2). Narrow command semantics — no PromotionManager:

    every packet of the stage terminal (>= 1 MATERIALIZED) -> EXECUTING
    moved to STAGED -> HIGH/CRITICAL risk requires a recorded C2C result for
    this run+stage (VOL-16 §6), waived only by the audited ``--skip-c2c`` ->
    the staging worktree's HEAD is the staged commit (snapshot identity,
    D-12; same path convention wave start used) -> the INTENDED promotion
    intent is written first (VOL-11 §2 intent-first ordering) ->
    ``promote.promote`` performs the CAS + ff-only checked-out-safe promotion
    (I30/I39, lazy import — parallel module) and settles the stage as
    PROMOTED in one S transaction.
    """
    promote_mod = _lazy_import("zloop.promote")
    if promote_mod is None:
        print(MODULE_UNAVAILABLE)
        return EXIT_OK
    materialize = _lazy_import("zloop.materialize")
    repo = _project_git_root(pid)
    c2c_waived = False
    with db.RunLock(pdir):
        conn = db.connect(pdir, create=True)
        try:
            store = db.ControlStore(pdir, conn, project_id=pid)
            run = _current_run(store)
            st = stage.get_stage(store, run["run_id"], args.stage_id)
            if st is None:
                raise CliError(EXIT_BLOCKED,
                               f"unknown stage {args.stage_id} in run "
                               f"{run['run_id']} (project {pid})")
            rid, sid = run["run_id"], st["stage_id"]

            # (1) every packet terminal, at least one MATERIALIZED
            packets = [dict(r) for r in store.conn.execute(
                "SELECT packet_id, state FROM packets"
                " WHERE run_id=? AND stage_id=? ORDER BY packet_id",
                (rid, sid))]
            open_pk = [f"{p['packet_id']}:{p['state']}" for p in packets
                       if p["state"] not in _PROMOTE_PACKET_TERMINAL]
            if open_pk:
                raise CliError(
                    EXIT_BLOCKED,
                    f"stage promote blocked: packets_pending — "
                    f"{', '.join(open_pk)}; every packet of the stage must "
                    f"be terminal ({'/'.join(_PROMOTE_PACKET_TERMINAL)})")
            if not any(p["state"] == "MATERIALIZED" for p in packets):
                raise CliError(
                    EXIT_BLOCKED,
                    f"stage promote blocked: nothing_materialized — stage "
                    f"{sid} has no MATERIALIZED packet; run a wave that "
                    f"materializes at least one packet first")

            # (2) EXECUTING -> STAGED (VOL-08 §4; already STAGED proceeds)
            if st["state"] == "EXECUTING":
                try:
                    st = stage.transition_stage(store, rid, sid, "STAGED")
                except ValueError as e:
                    raise CliError(EXIT_BLOCKED, f"stage promote failed: {e}")
            elif st["state"] != "STAGED":
                raise CliError(EXIT_BLOCKED,
                               f"stage {sid} is {st['state']}; promotion "
                               f"requires the stage to be EXECUTING/STAGED "
                               f"(VOL-08 §4)")

            # (3) HIGH/CRITICAL risk gate: automated C2C result audit (D-26)
            if st["risk_effective"] in _C2C_GATE_RISK:
                c2c_runner = _lazy_import("zloop.c2c_runner")
                if c2c_runner is not None:
                    audit_res = c2c_runner.run_automated_c2c_audit(
                        pdir, store, rid, sid, role="result", risk_effective=st["risk_effective"]
                    )
                    if audit_res.get("verdict") == "REJECT" or (not audit_res.get("ok", True) and audit_res.get("verdict") not in ("PREPARED", "PASS", "SKIPPED", "WAIVED")):
                        raise CliError(
                            EXIT_BLOCKED,
                            f"stage promote blocked: c2c_result_rejected — "
                            f"{sid} is {st['risk_effective']} risk and external result C2C audit rejected promotion: "
                            f"{audit_res.get('blockers') or audit_res.get('reason')}"
                        )

            # (4) staged commit = staging worktree HEAD (D-12 snapshot
            # identity; the worktree wave start created at the locked base)
            staging_ws = pdir / "workspaces" / sid / "staging"
            if not staging_ws.is_dir():
                raise CliError(
                    EXIT_BLOCKED,
                    f"stage promote blocked: staging_missing — the stage's "
                    f"staging worktree {staging_ws} does not exist "
                    f"(created by wave start)")
            try:
                if materialize is not None:
                    staged_commit = materialize.staging_commit_sha(staging_ws)
                else:
                    staged_commit = _git_out(staging_ws, "rev-parse", "HEAD")
            except RuntimeError as e:
                raise CliError(EXIT_BLOCKED,
                               f"stage promote blocked: staged commit "
                               f"unreadable in {staging_ws}: {e}")

            # (5) intent-first ordering (VOL-11 §2): the INTENDED promotion
            # intent for exactly this staged commit, written before any repo
            # write (idempotent on retry after a refused promotion)
            with store.mutation():
                have = store.conn.execute(
                    "SELECT COUNT(*) FROM promotion_intents WHERE run_id=?"
                    " AND stage_id=? AND staged_head=? AND state='INTENDED'",
                    (rid, sid, staged_commit)).fetchone()[0]
                if not have:
                    store.conn.execute(
                        "INSERT INTO promotion_intents(intent_id, run_id,"
                        " stage_id, stage_revision, expected_canonical_head,"
                        " expected_dirty_digest, staged_head, final_audit_ref,"
                        " state, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        ("pi-" + ids.new_nonce(8), rid, sid,
                         st["stage_revision"], st["expected_canonical_head"],
                         st["canonical_dirty_digest"], staged_commit, None,
                         "INTENDED", ids.now_iso()))
                    store._event("promotion_intent_created",
                                 {"staged_head": staged_commit,
                                  "expected_canonical_head":
                                      st["expected_canonical_head"]},
                                 run_id=rid, stage_id=sid)

            # (6) the promotion itself: dirty CAS + HEAD CAS + descendant
            # check + ff-only merge (I30/I39, all pre-checked before a single
            # repo write) and one S transaction settling the stage PROMOTED.
            # Failure reasons (DIRTY_OR_DRIFT / HEAD_DRIFT / NOT_DESCENDANT /
            # NO_INTENT / UNKNOWN_STAGE / ...) map to exit 5 with the reason.
            result = promote_mod.promote(
                store, rid, sid, git_root=repo, staged_commit=staged_commit,
                expected_head=st["expected_canonical_head"],
                expected_dirty_digest=st["canonical_dirty_digest"])
            if not result.get("ok"):
                reason = str(result.get("reason") or "PROMOTE_FAILED")
                detail = result.get("detail")
                raise CliError(EXIT_BLOCKED, f"stage promote blocked: {reason}"
                               + (f" ({detail})" if detail else ""))

            # (7) post-verify: the physical oracle really advanced (I21) and
            # the stage row is PROMOTED (promote settled it in one mutation)
            new_head = result.get("new_head")
            actual = _git_out(repo, "rev-parse", "HEAD")
            if actual != new_head:
                raise CliError(
                    EXIT_BLOCKED,
                    f"stage promote verify failed: canonical HEAD is {actual},"
                    f" promoted new_head is {new_head}")
            out: dict[str, Any] = {"promoted": True, "stage": sid,
                                   "run_id": rid, "new_head": new_head,
                                   "staged_commit": staged_commit}
            if c2c_waived:
                out["c2c_waived"] = True
            _print_json(out)
            return EXIT_OK
        finally:
            conn.close()


# ---- wave commands (VOL-09, milestone 4/6) ----------------------------------


def _json_or_none(v: Any) -> Optional[str]:
    return json.dumps(v, ensure_ascii=False) if v is not None else None


def _row_to_proposal(row: dict) -> dict:
    """Rebuild a wave-proposal-shaped packet dict from a packets table row."""

    def _loads(v, default):
        try:
            return json.loads(v) if v else default
        except (TypeError, ValueError):
            return default

    return {
        "packet_id": row["packet_id"],
        "packet_revision": row["packet_revision"],
        "goal": row["goal"],
        "write_scope": _loads(row["write_scope_json"], []),
        "acceptance": _loads(row["acceptance_json"], []),
        "constraints": _loads(row["constraints_json"], []),
        "depends_on": _loads(row["deps_json"], []),
        "risk_class": row["risk_class"],
        "network_policy": row["network_policy"],
        "max_turns": row["max_turns"],
    }


def _normalize_wave_id(raw: str) -> str:
    w = (raw or "").strip()
    if w.isdigit():
        w = "W" + w
    if not (w.startswith("W") and len(w) > 1 and w[1:].isdigit()):
        raise CliError(EXIT_USAGE, f"invalid wave id {raw!r} (expected W<n>, e.g. W1)")
    return w


def _load_packets_file(path: Path) -> list:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise CliError(EXIT_USAGE, f"cannot read packets file {path}: {e}")
    except json.JSONDecodeError as e:
        raise CliError(EXIT_USAGE, f"packets file {path} is not valid JSON: {e}")
    packets = doc.get("packets") if isinstance(doc, dict) else None
    if not isinstance(packets, list):
        raise CliError(EXIT_USAGE, f"packets file {path} must be a JSON object "
                                   f'{{"packets": [...]}} (VOL-04 §8 schema)')
    return packets


# ---- C2C packets (VOL-16 §1, D-25) -----------------------------------------

def _read_text_arg(path: str) -> str:
    """Read the packet/response text from a file, or stdin for '-'."""
    if path == "-":
        return sys.stdin.read()
    p = Path(path)
    if not p.is_file():
        raise CliError(EXIT_USAGE, f"file not found: {path}")
    return p.read_text(encoding="utf-8")


def _c2c_prepare(args: argparse.Namespace, pid: str, pdir: Path) -> int:
    content = _read_text_arg(args.file)
    if not content.strip():
        raise CliError(EXIT_USAGE, "c2c packet content is empty")
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        with db.RunLock(pdir):
            run = _require_active_run(store)
            st = _open_stage(store, run["run_id"])
            packet = c2c.prepare_c2c(pdir, store, run["run_id"], st["stage_id"],
                                     args.role, content=content,
                                     data_class=args.data_class,
                                     risk_effective=st["risk_effective"])
        out = {k: packet[k] for k in ("c2c_id", "role", "stage_id", "run_id",
                                      "risk_effective", "data_class",
                                      "fresh_thread_required",
                                      "thread_policy_note", "created_at")
               if k in packet}
        out["packet_path"] = str(pdir / "c2c" / f"{packet['c2c_id']}.json")
        out["content_chars"] = len(packet.get("content") or "")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return EXIT_OK
    finally:
        conn.close()


def _c2c_record(args: argparse.Namespace, pid: str, pdir: Path) -> int:
    response = _read_text_arg(args.file)
    if not response.strip():
        raise CliError(EXIT_USAGE, "c2c response text is empty")
    ident: dict = {}
    for kv in args.identity:
        if "=" not in kv:
            raise CliError(EXIT_USAGE, f"--identity expects key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        ident[k.strip()] = v.strip()
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        result = c2c.record_c2c(pdir, store, args.c2c, response,
                               observed_identity=ident or None)
        if not result.get("ok"):
            raise CliError(EXIT_BLOCKED,
                           f"c2c record failed: {result.get('reason')}")
        summary = result.get("packet_summary") or {}
        out = {"c2c_id": result["c2c_id"],
               "role": summary.get("role"),
               "audit_coverage": result["audit_coverage"],
               "trust": result["trust"],
               "response_digest": result["response_digest"],
               "observed_identity": result["observed_identity"],
               "recorded_at": result["recorded_at"],
               "result_path": str(pdir / "c2c" / f"{result['c2c_id']}-result.json")}
        # full response lives in the store (blob CAS) — the CLI emits only the
        # bounded digest (VOL-16 §5, P1-11)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return EXIT_OK
    finally:
        conn.close()


def cmd_c2c(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    if args.cmd == "prepare":
        return _c2c_prepare(args, pid, pdir)
    return _c2c_record(args, pid, pdir)


def cmd_wave(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    if args.cmd == "propose":
        return _wave_propose(args, pid, pdir)
    if args.cmd == "start":
        return _wave_start(args, pid, pdir)
    return _wave_cancel(args, pid, pdir)


def _wave_propose(args: argparse.Namespace, pid: str, pdir: Path) -> int:
    """Schema-validate a wave proposal (VOL-04 §8) and insert its packets as
    PENDING rows of the stage's current revision (VOL-09 §1)."""
    packets = _load_packets_file(args.packets)  # usage errors before touching S
    with db.RunLock(pdir):
        conn = db.connect(pdir, create=True)
        try:
            store = db.ControlStore(pdir, conn, project_id=pid)
            run = _require_active_run(store)
            st = _open_stage(store, run["run_id"])
            rid, sid = run["run_id"], st["stage_id"]
            # deps may reference earlier waves of this stage ({} for the first)
            existing: dict[str, dict] = {}
            for r in store.conn.execute(
                    "SELECT * FROM packets WHERE run_id=? AND stage_id=?", (rid, sid)):
                proposal = _row_to_proposal(dict(r))
                existing[proposal["packet_id"]] = proposal
            verdict = wave.validate_wave(packets, existing,
                                         stage_floor=st["risk_effective"])
            if not verdict["ok"]:
                raise CliError(EXIT_BLOCKED,
                               "wave proposal rejected: " + "; ".join(verdict["errors"]))
            with store.mutation():
                n = store.conn.execute(
                    "SELECT COUNT(*) FROM events WHERE kind='wave_proposed'"
                    " AND run_id=? AND stage_id=?", (rid, sid)).fetchone()[0]
                wid = ids.fmt_wave(int(n) + 1)
                for p in packets:
                    _insert_pending_packet(store, rid, sid,
                                           st["stage_revision"], p, wid)
                store._event("wave_proposed",
                             {"wave": wid,
                              "packets": [p["packet_id"] for p in packets]},
                             run_id=rid, stage_id=sid)
            wanted = {p["packet_id"] for p in packets}
            rows = [dict(r) for r in store.conn.execute(
                "SELECT * FROM packets WHERE run_id=? AND stage_id=?"
                " ORDER BY packet_id", (rid, sid))
                if r["packet_id"] in wanted]
            _print_json({"wave": wid, "packets": rows})
            return EXIT_OK
        finally:
            conn.close()


def _find_wave(store: "db.ControlStore", run_id: str, stage_id: str,
               wid: str) -> dict:
    """The wave_proposed event detail for W<n> of a stage (packet id list)."""
    for r in store.conn.execute(
            "SELECT detail_json FROM events WHERE kind='wave_proposed'"
            " AND run_id=? AND stage_id=? ORDER BY seq", (run_id, stage_id)):
        try:
            detail = json.loads(r["detail_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(detail, dict) and detail.get("wave") == wid:
            return detail
    raise CliError(EXIT_BLOCKED,
                   f"unknown wave {wid} for stage {stage_id} — "
                   f"run 'zloop wave propose' first")


def _pending_wave_packets(store: "db.ControlStore", run_id: str, stage_id: str,
                          wid: str, wave_detail: dict) -> list[dict]:
    rows = []
    for pid in wave_detail.get("packets") or []:
        row = store.conn.execute(
            "SELECT * FROM packets WHERE run_id=? AND stage_id=? AND packet_id=?",
            (run_id, stage_id, pid)).fetchone()
        if row is not None:
            rows.append(dict(row))
    rows.sort(key=lambda r: r["packet_id"])
    pending = [r for r in rows if r["state"] == "PENDING"]
    if not pending:
        raise CliError(EXIT_BLOCKED,
                       f"wave {wid} has no PENDING packets in stage {stage_id} "
                       f"(already started or consumed)")
    return pending


def _ensure_staging_worktree(pid: str, pdir: Path, st: dict) -> Path:
    """Staging worktree under <project>/workspaces/<stage>/staging at the
    locked stage base (workspace.create_worktree, VOL-13 worktree_fast)."""
    repo = _project_git_root(pid)
    dest = pdir / "workspaces" / st["stage_id"] / "staging"
    if dest.exists():
        return dest  # earlier wave start of this stage already created it
    dest.parent.mkdir(parents=True, exist_ok=True)
    res = workspace.create_worktree(repo, dest, base_ref=st["stage_base_ref"])
    if not res.get("ok"):
        reason = res.get("reason") or res.get("stderr_summary") or "git worktree add failed"
        raise CliError(EXIT_BLOCKED,
                       f"staging worktree creation failed for stage "
                       f"{st['stage_id']}: {reason}")
    return dest


class MockWorkspaceBackend(wave.MockBackend):
    """v1 mock backend for `wave start --backend mock`.

    Plain ``wave.MockBackend`` cannot complete a real wave: the supervisor
    only mkdirs each launch workspace and materialization later reconstructs
    the worker delta from that workspace (VOL-10 §2), which requires it to
    be a real git worktree — workspace setup is a backend concern (VOL-13,
    same convention as the supervisor's own tests). This backend keeps
    MockBackend's deterministic no-op worker and adds that setup, so the
    full launch -> collect -> materialize pipeline runs honestly."""

    def __init__(self, git_root: Path):
        super().__init__()
        self._git_root = Path(git_root)

    def start(self, spec: dict) -> dict:
        ws = Path(spec["workspace"])
        if ws.is_dir() and not any(ws.iterdir()):
            ws.rmdir()  # let create_worktree own the directory
        res = workspace.create_worktree(self._git_root, ws)
        if not res.get("ok"):
            reason = res.get("reason") or res.get("stderr_summary") \
                or "git worktree add failed"
            raise RuntimeError(f"mock launch workspace failed: {reason}")
        return super().start(spec)


# supervisor.run_wave refuses a wave (writing nothing) with these reasons
_NO_WRITE_WAVE_REASONS = frozenset({"unknown_run", "controller_busy",
                                   "unknown_stage", "invalid_wave",
                                   "KIMI_SERVER_UP", "owner_alive",
                                   "ambiguous_fail_closed", "cas_failed"})


def _insert_pending_packet(store: "db.ControlStore", rid: str, sid: str,
                           stage_revision: int, p: dict, wid: str,
                           *, or_ignore: bool = False) -> None:
    verb = "INSERT OR IGNORE INTO packets" if or_ignore else "INSERT INTO packets"
    store.conn.execute(
        verb + "(run_id, stage_id, stage_revision, packet_id,"
        " packet_revision, goal, write_scope_json, acceptance_json,"
        " constraints_json, deps_json, resource_scope_json,"
        " evidence_refs_json, risk_class, network_policy, max_turns,"
        " state, active_launch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, sid, stage_revision, p["packet_id"], 1,
         p.get("goal") or "",
         json.dumps(p.get("write_scope") or [], ensure_ascii=False),
         json.dumps(p.get("acceptance") or [], ensure_ascii=False),
         _json_or_none(p.get("constraints")),
         json.dumps(p.get("depends_on") or [], ensure_ascii=False),
         _json_or_none(p.get("resource_scope")),
         _json_or_none(p.get("evidence_refs")),
         p.get("risk_class") or "NORMAL",
         p.get("network_policy") or "none",
         p.get("max_turns"),
         "PENDING", None))
    store._event("packet_created",
                 {"packet_id": p["packet_id"], "state": "PENDING",
                  "wave": wid},
                 run_id=rid, stage_id=sid)


def _handoff_proposal_rows(store: "db.ControlStore", rid: str, sid: str,
                           wid: str, pending_rows: list[dict]) -> None:
    """supervisor.run_wave writes the authoritative PENDING rows itself
    (packets/attempts/launch context in one mutation), so the CLI's
    proposal-staged rows are removed for the handoff — audited, and only
    the wave's still-PENDING rows ever leave."""
    with store.mutation():
        for row in pending_rows:
            store.conn.execute(
                "DELETE FROM packets WHERE run_id=? AND stage_id=?"
                " AND packet_id=? AND state='PENDING'",
                (rid, sid, row["packet_id"]))
        store._event("wave_proposal_handoff",
                     {"wave": wid,
                      "packets": [r["packet_id"] for r in pending_rows]},
                     run_id=rid, stage_id=sid)


def _restore_proposal_rows(store: "db.ControlStore", rid: str, sid: str,
                           stage_revision: int, wid: str,
                           proposals: list[dict]) -> None:
    """Best-effort restore of the proposal rows after the supervisor refused
    or died without taking ownership (OR IGNORE: only rows it never wrote
    come back; rows it already owns are left untouched)."""
    try:
        with store.mutation():
            for p in proposals:
                _insert_pending_packet(store, rid, sid, stage_revision, p,
                                       wid, or_ignore=True)
            store._event("wave_proposal_restored", {"wave": wid},
                         run_id=rid, stage_id=sid)
    except Exception:
        pass  # the primary failure is what gets reported, not this cleanup


def _wave_start(args: argparse.Namespace, pid: str, pdir: Path) -> int:
    """FOREGROUND wave start (VOL-09 §1/§7): resolve the wave's PENDING
    packets, force the stage to EXECUTING, create the staging worktree, then
    hand the wave to the supervisor (lazy — parallel module)."""
    wid = _normalize_wave_id(args.wave)
    if args.backend == "codex":
        raise CliError(EXIT_BLOCKED, CODEX_AUTH_BROKEN)
    if args.backend != "mock":
        raise CliError(EXIT_USAGE,
                       f"unknown backend {args.backend!r} (v1: 'mock'; 'codex' "
                       f"is pending auth)")
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        with db.RunLock(pdir):
            run = _require_active_run(store)
            st = _open_stage(store, run["run_id"])
            rid, sid = run["run_id"], st["stage_id"]
            wave_detail = _find_wave(store, rid, sid, wid)
            pending = _pending_wave_packets(store, rid, sid, wid, wave_detail)
            # D-26 automated plan C2C & Web Search gate (VOL-16 §6, user directive):
            # Automated C2C plan audit & web search execution for HIGH/CRITICAL waves.
            # Blocked here, the stage stays PLANNING (nothing dispatched).
            if st["risk_effective"] in _C2C_GATE_RISK:
                c2c_runner = _lazy_import("zloop.c2c_runner")
                if c2c_runner is not None:
                    audit_res = c2c_runner.run_automated_c2c_audit(
                        pdir, store, rid, sid, role="plan", risk_effective=st["risk_effective"]
                    )
                    if audit_res.get("verdict") == "REJECT" or (not audit_res.get("ok", True) and audit_res.get("verdict") not in ("PREPARED", "PASS", "SKIPPED", "WAIVED")):
                        raise CliError(
                            EXIT_BLOCKED,
                            f"wave start blocked: c2c_plan_rejected — "
                            f"{sid} is {st['risk_effective']} risk and external plan C2C audit rejected dispatch: "
                            f"{audit_res.get('blockers') or audit_res.get('reason')}"
                        )
            if st["state"] == "PLANNING":
                try:
                    st = stage.transition_stage(store, rid, sid, "EXECUTING")
                except ValueError as e:
                    raise CliError(EXIT_BLOCKED, f"wave start failed: {e}")
            elif st["state"] != "EXECUTING":
                raise CliError(EXIT_BLOCKED,
                               f"stage {sid} is {st['state']}; a wave requires the "
                               f"stage to be PLANNING/EXECUTING (VOL-09 §1)")
        # RunLock released: never hold the OS lock across the wave itself (D-8)
        staging = _ensure_staging_worktree(pid, pdir, st)
        supervisor = _lazy_import("zloop.supervisor")
        if supervisor is None:
            print(MODULE_UNAVAILABLE)
            return EXIT_OK
        run_wave_fn = getattr(supervisor, "run_wave", None)
        if run_wave_fn is None:
            raise CliError(EXIT_BLOCKED, "zloop.supervisor has no run_wave "
                                          "(parallel integration pending)")
        repo = _project_git_root(pid)
        proposals = [_row_to_proposal(r) for r in pending]
        _handoff_proposal_rows(store, rid, sid, wid, pending)
        try:
            summary = run_wave_fn(store, rid, sid, int(wid[1:]), proposals,
                                  MockWorkspaceBackend(repo),
                                  git_root=repo, staging_ws=staging,
                                  workspaces_root=pdir / "workspaces")
        except db.SError:
            raise
        except Exception as e:
            _restore_proposal_rows(store, rid, sid, st["stage_revision"],
                                   wid, proposals)
            raise CliError(EXIT_BLOCKED,
                           f"wave start failed: {type(e).__name__}: {e}")
        if (isinstance(summary, dict)
                and summary.get("reason") in _NO_WRITE_WAVE_REASONS):
            _restore_proposal_rows(store, rid, sid, st["stage_revision"],
                                   wid, proposals)
            detail = "; ".join(str(x) for x in summary.get("errors") or [])
            raise CliError(EXIT_BLOCKED,
                           "wave start refused by the supervisor: "
                           f"{summary.get('reason')}"
                           + (f" ({detail})" if detail else ""))
        _print_json(summary)
        return EXIT_OK
    finally:
        conn.close()


def _wave_cancel(args: argparse.Namespace, pid: str, pdir: Path) -> int:
    """D-8 / VOL-09 §8: cancel is a command input to the owner, NOT a
    lifecycle transition — one S transaction, no locks, no live process
    required (the request persists until an owner observes it)."""
    wid = _normalize_wave_id(args.wave)
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        run = _require_active_run(store)
        if not store.request_cancel(run["run_id"]):
            raise CliError(EXIT_BLOCKED,
                           f"cancel request could not be recorded for run "
                           f"{run['run_id']}")
        _print_json({
            "cancel_requested": True,
            "run_id": run["run_id"],
            "wave": wid,
            "note": "runs.cancel_requested=1 recorded; the running owner wave "
                    "process executes CANCELLING on its next loop tick (D-8). "
                    "The request persists even when no wave process is live.",
        })
        return EXIT_OK
    finally:
        conn.close()


# ---- research command (VOL-15, milestone 4) ----------------------------------

_ANSWER_TRUNC = 200  # bounded stdout: full answers live in the research dir


def _truncate_answer(s: str) -> str:
    return s if len(s) <= _ANSWER_TRUNC else s[:_ANSWER_TRUNC] + "…[truncated]"


def _bounded_research_manifest(manifest: Any) -> Any:
    """Bound the stdout copy of a research manifest (VOL-15): every long
    string inside the per-question records (answer/claim/query/error) is
    truncated; the full manifest on disk keeps everything."""
    if not isinstance(manifest, dict):
        return manifest
    out = dict(manifest)

    def _bound_record(rec: Any) -> Any:
        if isinstance(rec, str):
            return _truncate_answer(rec)
        if isinstance(rec, dict):
            return {k: (_truncate_answer(v) if isinstance(v, str) else v)
                    for k, v in rec.items()}
        return rec

    results = out.get("results")
    if isinstance(results, list):
        out["results"] = [_bound_record(r) for r in results]
    answers = out.get("answers")  # tolerate the documented answers-shaped form
    if isinstance(answers, str):
        out["answers"] = _truncate_answer(answers)
    elif isinstance(answers, list):
        out["answers"] = [_bound_record(a) for a in answers]
    return out


def cmd_research(args: argparse.Namespace) -> int:
    broker = _lazy_import("zloop.research.broker")
    if broker is None:
        print(RESEARCH_MODULE_PENDING)
        return EXIT_OK
    pid, pdir = _require_project()
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    except OSError as e:
        raise CliError(EXIT_USAGE, f"cannot read research spec {args.spec}: {e}")
    except json.JSONDecodeError as e:
        raise CliError(EXIT_USAGE, f"research spec {args.spec} is not valid JSON: {e}")
    if not isinstance(spec, dict):
        raise CliError(EXIT_USAGE,
                       f"research spec {args.spec} must be a JSON object "
                       f'(VOL-15 §1: {{"questions": [{{"id", "query"}}], ...}})')
    try:
        manifest = broker.run_research(pdir, spec)
    except ValueError as e:
        raise CliError(EXIT_BLOCKED, f"research run failed: {e}")
    if isinstance(manifest, dict):
        bounded = _bounded_research_manifest(manifest)
        rid = manifest.get("research_id")
        if (isinstance(rid, str) and rid and "manifest_error" not in manifest):
            bounded["full_manifest"] = str(pdir / "research" / rid / "manifest.json")
        manifest = bounded
    _print_json(manifest)
    return EXIT_OK


def _hook_selfcheck() -> tuple[bool, str]:
    """Verify `import zloop.hook` in a fresh interpreter (zloop.hook is a
    parallel module — never imported in-process here)."""
    env = dict(os.environ)
    pkg_parent = str(Path(__file__).resolve().parents[1])
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    if pkg_parent not in parts:
        parts.insert(0, pkg_parent)
        env["PYTHONPATH"] = os.pathsep.join(parts)
    try:
        proc = subprocess.run([sys.executable, "-c", "import zloop.hook"],
                              capture_output=True, text=True, env=env, timeout=60)
    except subprocess.TimeoutExpired:
        return False, "selfcheck timed out"
    except OSError as e:
        return False, f"could not run interpreter: {e}"
    if proc.returncode == 0:
        return True, ""
    err = (proc.stderr or "").strip()
    if "ModuleNotFoundError" in err:
        return False, "MODULE_MISSING"
    return False, err or f"exit code {proc.returncode}"


def cmd_install(args: argparse.Namespace) -> int:
    ok, info = _hook_selfcheck()
    if not ok:
        if info == "MODULE_MISSING":
            print(MODULE_UNAVAILABLE)
            return EXIT_OK
        raise CliError(EXIT_BLOCKED, f"zloop.hook import selfcheck failed: {info}")
    zinstall = _lazy_import("zloop.install")
    if zinstall is None:
        print(MODULE_UNAVAILABLE)
        return EXIT_OK
    result = zinstall.install_hooks(sys.executable, ["-m", "zloop.hook"],
                                    timeout_ms=args.timeout_ms,
                                    config_path=args.config_path)
    _print_json(result)
    return EXIT_OK


def cmd_uninstall(args: argparse.Namespace) -> int:
    zinstall = _lazy_import("zloop.install")
    if zinstall is None:
        print(MODULE_UNAVAILABLE)
        return EXIT_OK
    _print_json(zinstall.uninstall_hooks(config_path=args.config_path))
    return EXIT_OK


# ---- parser ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zloop",
        description="ZLoop root-side control surface "
                    "(run start/attach MUST run in foreground to bind)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("doctor", help="environment & S health report")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("project", help="project registry")
    psub = sp.add_subparsers(dest="cmd", required=True)
    sp = psub.add_parser("attach", help="register the project for a git root (default: cwd)")
    sp.add_argument("--git-root", type=Path, default=None, metavar="PATH")
    sp.set_defaults(func=cmd_project)
    sp = psub.add_parser("list", help="list registered projects")
    sp.set_defaults(func=cmd_project)

    sp = sub.add_parser("run", help="runs")
    rsub = sp.add_subparsers(dest="cmd", required=True)
    sp = rsub.add_parser("start", help="create a run and emit a bind token (FOREGROUND)")
    sp.add_argument("objective")
    sp.add_argument("--wait-claim", type=int, default=None, metavar="N",
                    help="poll up to N seconds for the hook to claim the token")
    sp.set_defaults(func=cmd_run_start)
    sp = rsub.add_parser("close", help="close a run")
    sp.add_argument("run_id", metavar="RID")
    sp.set_defaults(func=cmd_run_close)
    sp = rsub.add_parser("status", help="show one run (default: all runs)")
    sp.add_argument("run_id", nargs="?", default=None, metavar="RID")
    sp.set_defaults(func=cmd_run_status)
    sp = rsub.add_parser("list", help="list runs")
    sp.set_defaults(func=cmd_run_list)

    sp = sub.add_parser("attach", help="emit a bind token for an existing run (FOREGROUND)")
    sp.add_argument("run_id", metavar="RID")
    sp.add_argument("--resume-after-clear", action="store_true",
                    help="request recovery injection after /clear (I28) once bound")
    sp.add_argument("--wait-claim", type=int, default=None, metavar="N")
    sp.set_defaults(func=cmd_attach)

    sp = sub.add_parser("detach", help="remove a session binding")
    sp.add_argument("--session", required=True, metavar="ID")
    sp.set_defaults(func=cmd_detach)

    sp = sub.add_parser("binding", help="session bindings & pending claims")
    bsub = sp.add_subparsers(dest="cmd", required=True)
    sp = bsub.add_parser("status", help="bindings + pending unexpired claims")
    sp.add_argument("--session", default=None, metavar="ID")
    sp.set_defaults(func=cmd_binding_status)

    sp = sub.add_parser("history", help="H0 history search/verify")
    hsub = sp.add_subparsers(dest="cmd", required=True)
    sp = hsub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--session", default=None, metavar="ID")
    sp.add_argument("--run", default=None, metavar="RID")
    sp.add_argument("--limit", type=int, default=50, metavar="N")
    sp.set_defaults(func=cmd_history)
    sp = hsub.add_parser("verify")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("checkpoint", help="H1 checkpoints")
    csub = sp.add_subparsers(dest="cmd", required=True)
    sp = csub.add_parser("write", help="write a checkpoint capsule (file or stdin)")
    sp.add_argument("--file", type=Path, default=None, metavar="PATH")
    sp.set_defaults(func=cmd_checkpoint)
    sp = csub.add_parser("current", help="show the current checkpoint")
    sp.set_defaults(func=cmd_checkpoint)
    sp = csub.add_parser("show", help="show one checkpoint")
    sp.add_argument("checkpoint_id", metavar="ID")
    sp.set_defaults(func=cmd_checkpoint)

    sp = sub.add_parser("stage", help="stage pipeline (VOL-08)")
    gsub = sp.add_subparsers(dest="cmd", required=True)
    sp = gsub.add_parser("begin", help="begin the next stage of the ACTIVE run "
                                       "(FOREGROUND; dirty base -> BLOCKED_DIRTY_BASE)")
    sp.add_argument("--objective", required=True, metavar="TEXT")
    sp.add_argument("--risk", default="NORMAL", choices=stage.RISK_LEVELS,
                    metavar="LEVEL",
                    help="requested risk level (default NORMAL; the host floor "
                         "can only raise it, VOL-08 §2)")
    sp.set_defaults(func=cmd_stage)
    sp = gsub.add_parser("status", help="stages of the current run (or one stage)")
    sp.add_argument("stage_id", nargs="?", default=None, metavar="SID")
    sp.set_defaults(func=cmd_stage)
    sp = gsub.add_parser("close", help="move a stage to a terminal state "
                                       "(CLOSED; BLOCKED -> CANCELLED)")
    sp.add_argument("stage_id", metavar="SID")
    sp.set_defaults(func=cmd_stage)
    sp = gsub.add_parser("promote", help="promote a staged stage onto the "
                                         "canonical branch (CAS + ff-only, "
                                         "VOL-11 §2)")
    sp.add_argument("stage_id", metavar="SID")
    sp.set_defaults(func=cmd_stage)

    sp = sub.add_parser("c2c", help="cross-model C2C packets (VOL-16, D-25)")
    c2sub = sp.add_subparsers(dest="cmd", required=True)
    sp = c2sub.add_parser("prepare", help="prepare a bounded, redacted packet "
                                         "for the external auditor (role plan|result)")
    sp.add_argument("--role", required=True, choices=["plan", "result"])
    sp.add_argument("--file", default="-", metavar="PATH",
                    help="packet content file ('-' = stdin, default)")
    sp.add_argument("--data-class", default="project_internal",
                    choices=["public", "project_internal", "sensitive"])
    sp.set_defaults(func=cmd_c2c)
    sp = c2sub.add_parser("record", help="record the external auditor's response "
                                        "for a prepared packet (verifies packet sha)")
    sp.add_argument("--c2c", required=True, metavar="ID")
    sp.add_argument("--file", default="-", metavar="PATH",
                    help="response text file ('-' = stdin, default)")
    sp.add_argument("--identity", action="append", default=[], metavar="KEY=VALUE",
                    help="observable identity (repeatable): surface=... "
                         "ui_model_label=... search_mode=... thread_id_hint=...")
    sp.set_defaults(func=cmd_c2c)

    sp = sub.add_parser("wave", help="wave propose/start/cancel (VOL-09)")
    wsub = sp.add_subparsers(dest="cmd", required=True)
    sp = wsub.add_parser("propose", help="validate packets.json and insert PENDING "
                                         "packets as the next wave")
    sp.add_argument("packets", type=Path, metavar="packets.json")
    sp.set_defaults(func=cmd_wave)
    sp = wsub.add_parser("start", help="run a wave in the FOREGROUND (keep <600s; "
                                       "long waves belong to the future await path)")
    sp.add_argument("wave", metavar="Wn")
    sp.add_argument("--backend", default="mock", metavar="NAME",
                    help="worker backend (v1: mock; codex pending auth)")
    sp.set_defaults(func=cmd_wave)
    sp = wsub.add_parser("cancel", help="request cancellation of the ACTIVE run's "
                                        "wave (owner executes CANCELLING on its "
                                        "next tick, D-8)")
    sp.add_argument("wave", metavar="Wn")
    sp.set_defaults(func=cmd_wave)

    sp = sub.add_parser("research", help="research lane (VOL-15)")
    rsub = sp.add_subparsers(dest="cmd", required=True)
    sp = rsub.add_parser("run", help="run a research spec through the broker "
                                     "(bounded manifest on stdout)")
    sp.add_argument("spec", type=Path, metavar="spec.json")
    sp.set_defaults(func=cmd_research)

    sp = sub.add_parser("install", help="install user-level ZCode hooks")
    sp.add_argument("--timeout-ms", type=int, default=8000, metavar="N")
    sp.add_argument("--config-path", type=Path, default=None, metavar="PATH",
                    help="hook config path (default: user-level ZCode config)")
    sp.set_defaults(func=cmd_install)
    sp = sub.add_parser("uninstall", help="remove ZCode hooks")
    sp.add_argument("--config-path", type=Path, default=None, metavar="PATH")
    sp.set_defaults(func=cmd_uninstall)

    sp = sub.add_parser("verify-run", help="goal-completion check: run CLOSED and "
                                          "every stage terminal")
    sp.add_argument("run_id", nargs="?", default=None, metavar="RID")
    sp.set_defaults(func=cmd_verify_run)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    # Exact-spec messages contain non-ASCII (em-dash); never die on a legacy
    # console codepage while printing them.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)  # usage errors exit 2 (argparse)
    try:
        return args.func(args)
    except CliError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.code
    except db.SError as e:
        print(f"S_DEGRADED: {e}", file=sys.stderr)
        return EXIT_S_DEGRADED
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # python -m zloop.cli
    sys.exit(main())
