"""zloop.db — S control DB (VOL-07): version gate, PRAGMAs, schema, transactions.

I22: production WAL only when the runtime SQLite contains the 2026-03
     WAL-reset fix (>=3.51.3, or backports 3.50.7 / 3.44.6).
     Local baseline 3.50.4 is affected -> DELETE + EXTRA enforced by code.
I4:  S commit failure stops lifecycle mutation — callers map SError to
     fail-closed behaviour (CLI exit 3), never guess.
I32: binding is claimed via one-time nonce, never cwd/latest-run guessing.
I43: single controller = OS run lock + controller_epoch (TTL is hygiene only).
D-20: takeover_controller demands a mechanical (pid, pid_start) liveness
      proof of the recorded owner BEFORE any takeover CAS — pid absent or
      start mismatch = dead (PID reuse), undecidable = refuse (I43).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from . import ids, paths

try:
    import msvcrt
    _WIN = True
except ImportError:  # POSIX fallback for portability of tests
    import fcntl
    _WIN = False

WAL_FIX_MIN = (3, 51, 3)
WAL_BACKPORTS = {(3, 50, 7), (3, 44, 6)}
SCHEMA_VERSION = 2


class SError(Exception):
    """S is degraded: fail-closed semantics (CLI maps this to exit code 3)."""


def runtime_sqlite_version() -> tuple[int, int, int]:
    return tuple(int(p) for p in sqlite3.sqlite_version.split(".")[:3])  # type: ignore[return-value]


def journal_profile(version: Optional[tuple[int, int, int]] = None) -> dict:
    v = version or runtime_sqlite_version()
    if v >= WAL_FIX_MIN or v in WAL_BACKPORTS:
        return {"journal_mode": "WAL", "synchronous": "FULL", "wal_ok": True,
                "runtime_sqlite": ".".join(map(str, v)),
                "reason": "runtime sqlite contains the WAL-reset fix"}
    return {"journal_mode": "DELETE", "synchronous": "EXTRA", "wal_ok": False,
            "runtime_sqlite": ".".join(map(str, v)),
            "reason": f"sqlite {v} is inside the WAL-reset affected range "
                      f"(3.7.0-3.51.2, below the 3.50.7 backport); "
                      f"DELETE+EXTRA enforced by gate (I22)"}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
  project_id TEXT PRIMARY KEY, git_root TEXT NOT NULL, git_common_dir TEXT NOT NULL,
  display_name TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS pending_binding_claims (
  nonce TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  run_id TEXT, stage_id TEXT,
  purpose TEXT NOT NULL CHECK(purpose IN ('run_start','attach')),
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  claimed_at TEXT, claimed_by_session TEXT);

CREATE TABLE IF NOT EXISTS session_bindings (
  zcode_session_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  run_id TEXT, stage_id TEXT,
  binding_epoch INTEGER NOT NULL DEFAULT 1,
  resume_after_clear INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(project_id),
  objective TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('ACTIVE','CLOSED','CANCELLED')),
  created_at TEXT NOT NULL, closed_at TEXT,
  controller_nonce TEXT, controller_pid INTEGER, controller_pid_start TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS controller_epochs (
  epoch INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id),
  started_at TEXT NOT NULL, ended_at TEXT, host TEXT NOT NULL, pid INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS stages (
  run_id TEXT NOT NULL REFERENCES runs(run_id), stage_id TEXT NOT NULL,
  stage_revision INTEGER NOT NULL DEFAULT 1,
  objective_slice TEXT NOT NULL,
  risk_requested TEXT NOT NULL, risk_floor TEXT NOT NULL, risk_effective TEXT NOT NULL,
  expected_canonical_head TEXT NOT NULL, canonical_dirty_digest TEXT NOT NULL,
  stage_base_ref TEXT NOT NULL, stage_base_tree TEXT NOT NULL,
  current_snapshot TEXT,
  state TEXT NOT NULL CHECK(state IN ('PLANNING','EXECUTING','STAGED','PROMOTING','PROMOTED','CLOSED','BLOCKED','CANCELLED')),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, stage_id));

CREATE TABLE IF NOT EXISTS packets (
  run_id TEXT NOT NULL, stage_id TEXT NOT NULL, stage_revision INTEGER NOT NULL,
  packet_id TEXT NOT NULL, packet_revision INTEGER NOT NULL DEFAULT 1,
  goal TEXT NOT NULL, write_scope_json TEXT NOT NULL,
  acceptance_json TEXT NOT NULL, constraints_json TEXT, deps_json TEXT,
  resource_scope_json TEXT, evidence_refs_json TEXT,
  risk_class TEXT NOT NULL, network_policy TEXT NOT NULL DEFAULT 'none',
  max_turns INTEGER,
  state TEXT NOT NULL CHECK(state IN ('PENDING','RUNNING','REPORTED','ACCEPTED','MATERIALIZED','FAILED','BLOCKED','CANCELLED','SUPERSEDED')),
  active_launch_id TEXT,
  PRIMARY KEY (run_id, stage_id, packet_id));

CREATE TABLE IF NOT EXISTS attempts (
  run_id TEXT NOT NULL, stage_id TEXT NOT NULL, packet_id TEXT NOT NULL,
  packet_revision INTEGER NOT NULL, attempt INTEGER NOT NULL,
  created_at TEXT NOT NULL, note TEXT,
  PRIMARY KEY (run_id, stage_id, packet_id, packet_revision, attempt));

CREATE TABLE IF NOT EXISTS launches (
  launch_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL, stage_id TEXT NOT NULL, stage_revision INTEGER NOT NULL,
  packet_id TEXT NOT NULL, packet_revision INTEGER NOT NULL, attempt INTEGER NOT NULL,
  workspace_id TEXT NOT NULL, backend TEXT NOT NULL,
  backend_handle TEXT, pid INTEGER, pid_start_time TEXT,
  intent_state TEXT NOT NULL CHECK(intent_state IN ('INTENDED','BOUND','RUNNING','TERMINAL','AMBIGUOUS','QUARANTINED')),
  terminal_state TEXT, terminal_at TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS resource_leases (
  lease_id TEXT PRIMARY KEY, resource_key TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('EXCLUSIVE','SHARED')),
  holder TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT);

CREATE TABLE IF NOT EXISTS promotion_intents (
  intent_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL, stage_id TEXT NOT NULL, stage_revision INTEGER NOT NULL,
  expected_canonical_head TEXT NOT NULL, expected_dirty_digest TEXT NOT NULL,
  staged_head TEXT NOT NULL, final_audit_ref TEXT,
  state TEXT NOT NULL CHECK(state IN ('INTENDED','APPLIED','RECOVERED','ROLLED_BACK','BLOCKED')),
  created_at TEXT NOT NULL, resolved_at TEXT);

CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  run_id TEXT, stage_id TEXT, kind TEXT NOT NULL, detail_json TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS idx_packets_stage ON packets(run_id, stage_id, state);
CREATE INDEX IF NOT EXISTS idx_launches_packet ON launches(run_id, stage_id, packet_id, packet_revision, attempt);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
"""


def connect(project_dir: Path, *, create: bool = False) -> sqlite3.Connection:
    """Open S with the version-gated journal profile (I22). Raises SError."""
    db_path = Path(project_dir) / "control.sqlite3"
    if not db_path.exists() and not create:
        raise SError(f"control DB missing: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    prof = journal_profile()
    try:
        conn.execute(f"PRAGMA journal_mode={prof['journal_mode']}")
        conn.execute(f"PRAGMA synchronous={prof['synchronous']}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        row = conn.execute("PRAGMA quick_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            conn.close()
            raise SError(f"S quick_check failed: {row!r}")
        _migrate(conn)
    except sqlite3.DatabaseError as e:
        conn.close()
        raise SError(f"S degraded (fail-closed): {e}") from e
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)  # all statements are IF NOT EXISTS
    # v2 (D-8): controller-token columns on older databases
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    for col, ddl in (("controller_nonce", "TEXT"), ("controller_pid", "INTEGER"),
                     ("controller_pid_start", "TEXT"),
                     ("cancel_requested", "INTEGER NOT NULL DEFAULT 0")):
        if col not in cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {ddl}")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        cur = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()
        if cur[0] < SCHEMA_VERSION:
            conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?,?)",
                         (SCHEMA_VERSION, ids.now_iso()))
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class RunLock:
    """OS-level exclusive run lock (I43). Failure means another controller owns it."""

    def __init__(self, project_dir: Path):
        self.path = Path(project_dir) / "run.lock"
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")
        try:
            if _WIN:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self._fh.close()
            self._fh = None
            return False

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if _WIN:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        if not self.acquire():
            raise SError(f"another controller holds {self.path}")
        return self

    def __exit__(self, *exc):
        self.release()


# ---- process identity probes (D-20) ------------------------------------------
#
# Death proof for a D-8 takeover: the recorded owner (pid, pid_start) is only
# mechanically dead when the OS says so. Probes run through powershell
# (Windows is the production target); ANY probe failure is ambiguous, never
# "dead" — callers fail closed (I43).

_PS_TIMEOUT_S = 15.0


def _ps_probe(command: str, timeout_s: float = _PS_TIMEOUT_S) -> Optional[str]:
    """Run one powershell probe; return stripped stdout, or None on ANY
    failure (nonzero exit, timeout, missing executable). Never raises.

    Locale-proof: decode with errors="replace" so a localized (non-UTF-8)
    powershell message can never crash a capture reader thread — probe
    output we match on is ASCII ('o' timestamps, ZLOOP_PRESENT/ABSENT).
    """
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout_s)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def process_start_time(pid: int) -> Optional[str]:
    """D-20: the REAL creation time of pid as ISO 8601 ('o' round-trip
    format), or None on ANY failure (process absent, access denied, probe
    failure). This is a mechanical process identity, never a wall-clock
    guess: recording it at claim time is what makes a later takeover's
    death proof sound."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    return _ps_probe(f"(Get-Process -Id {pid}).StartTime.ToString('o')")


def owner_alive(pid: Optional[int], pid_start: Optional[str]) -> Optional[bool]:
    """D-20: mechanical liveness proof for a recorded controller owner.

    True  — pid exists AND its real start time equals pid_start (alive);
    False — pid absent (dead), or start mismatch (the PID was reused after
            the recorded owner died: old owner is dead);
    None  — ambiguous (no recorded start, or the probe cannot decide);
            callers MUST fail closed (I43)."""
    if pid is None or pid_start is None:
        return None  # no recorded identity to prove against: ambiguous
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    present = _ps_probe(
        f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) "
        f"{{ 'ZLOOP_PRESENT' }} else {{ 'ZLOOP_ABSENT' }}")
    if present is None:
        return None  # probe failed: existence undecidable
    if present != "ZLOOP_PRESENT":
        return False  # process absent: mechanically dead
    start = process_start_time(pid)
    if start is None:
        return None  # present but start unreadable: comparison impossible
    return start == pid_start  # equal -> alive; mismatch -> PID reuse


class ControlStore:
    """High-level S operations. All mutations go through self.mutation()."""

    def __init__(self, project_dir: Path, conn: sqlite3.Connection,
                 project_id: Optional[str] = None):
        self.project_dir = Path(project_dir)
        self.conn = conn
        self.project_id = project_id or self.project_dir.name
        self.profile = journal_profile()
        # Satisfy the projects FK even when the store was opened directly on a
        # project dir (registry lookup is best-effort here, never authoritative).
        reg = paths.load_registry()["projects"].get(self.project_id, {})
        self.conn.execute(
            "INSERT OR IGNORE INTO projects"
            "(project_id, git_root, git_common_dir, display_name, created_at)"
            " VALUES (?,?,?,?,?)",
            (self.project_id, reg.get("git_root", ""), reg.get("git_common_dir", ""),
             reg.get("display_name", ""), ids.now_iso()))

    # ---- transactions ----------------------------------------------------

    @contextmanager
    def mutation(self):
        if self.conn.in_transaction:
            # join an outer transaction (nested call) — atomicity preserved
            yield self.conn
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def _event(self, kind: str, detail: dict, run_id: Optional[str] = None,
               stage_id: Optional[str] = None) -> None:
        self.conn.execute(
            "INSERT INTO events(ts, run_id, stage_id, kind, detail_json) VALUES (?,?,?,?,?)",
            (ids.now_iso(), run_id, stage_id, kind,
             json.dumps(detail, ensure_ascii=False)))

    # ---- binding claims (I32) ---------------------------------------------

    def create_claim(self, *, purpose: str, run_id: Optional[str],
                     stage_id: Optional[str] = None, ttl_s: int = 120) -> str:
        nonce = ids.new_nonce()
        with self.mutation():
            self.conn.execute(
                "INSERT INTO pending_binding_claims"
                "(nonce, project_id, run_id, stage_id, purpose, created_at, expires_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (nonce, self.project_id, run_id, stage_id, purpose,
                 ids.now_iso(), ids.iso_plus(ttl_s)))
            self._event("binding_claim_created",
                       {"purpose": purpose, "run_id": run_id}, run_id=run_id)
        return nonce

    def claim_binding(self, nonce: str, session_id: str) -> Optional[dict]:
        """Atomically claim a one-time token for this ZCode session.

        Returns the new binding dict, or None when nonce is unknown/expired/
        already claimed (safe result = NOT BOUND)."""
        now = ids.now_iso()
        with self.mutation():
            cur = self.conn.execute(
                "UPDATE pending_binding_claims SET claimed_at=?, claimed_by_session=?"
                " WHERE nonce=? AND claimed_at IS NULL AND expires_at>?",
                (now, session_id, nonce, now))
            if cur.rowcount != 1:
                return None
            row = self.conn.execute(
                "SELECT project_id, run_id, stage_id FROM pending_binding_claims"
                " WHERE nonce=?", (nonce,)).fetchone()
            if row is None:
                return None
            project_id, run_id, stage_id = row["project_id"], row["run_id"], row["stage_id"]
            old = self.conn.execute(
                "SELECT binding_epoch, resume_after_clear FROM session_bindings"
                " WHERE zcode_session_id=?", (session_id,)).fetchone()
            epoch = (old["binding_epoch"] + 1) if old else 1
            rac = old["resume_after_clear"] if old else 0
            self.conn.execute(
                "INSERT INTO session_bindings"
                "(zcode_session_id, project_id, run_id, stage_id, binding_epoch,"
                " resume_after_clear, updated_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(zcode_session_id) DO UPDATE SET"
                " project_id=excluded.project_id, run_id=excluded.run_id,"
                " stage_id=excluded.stage_id, binding_epoch=excluded.binding_epoch,"
                " updated_at=excluded.updated_at",
                (session_id, project_id, run_id, stage_id, epoch, rac, now))
            self._event("binding_claimed",
                       {"session_id": session_id, "run_id": run_id, "epoch": epoch},
                       run_id=run_id)
        return {"session_id": session_id, "project_id": project_id,
                "run_id": run_id, "stage_id": stage_id, "binding_epoch": epoch}

    def binding(self, session_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM session_bindings WHERE zcode_session_id=?",
            (session_id,)).fetchone()
        return dict(row) if row else None

    def pending_claims(self) -> list[dict]:
        now = ids.now_iso()
        return [dict(r) for r in self.conn.execute(
            "SELECT nonce, purpose, run_id, stage_id, created_at, expires_at,"
            " claimed_at, claimed_by_session FROM pending_binding_claims"
            " WHERE claimed_at IS NULL AND expires_at>?", (now,))]

    def set_resume_after_clear(self, session_id: str, value: bool) -> None:
        with self.mutation():
            self.conn.execute(
                "UPDATE session_bindings SET resume_after_clear=?, updated_at=?"
                " WHERE zcode_session_id=?",
                (int(value), ids.now_iso(), session_id))

    def detach_session(self, session_id: str) -> None:
        with self.mutation():
            self.conn.execute(
                "DELETE FROM session_bindings WHERE zcode_session_id=?", (session_id,))
            self._event("binding_detached", {"session_id": session_id})

    # ---- runs --------------------------------------------------------------

    def _next_run_no(self) -> int:
        nums = [ids.parse_int_suffix("R", r["run_id"]) or 0
                for r in self.conn.execute("SELECT run_id FROM runs")]
        return (max(nums) + 1) if nums else 1

    def create_run(self, objective: str) -> str:
        run_id = ids.fmt_run(self._next_run_no())
        with self.mutation():
            self.conn.execute(
                "INSERT INTO runs(run_id, project_id, objective, state, created_at)"
                " VALUES (?,?,?,?,?)",
                (run_id, self.project_id, objective, "ACTIVE", ids.now_iso()))
            self._event("run_created", {"objective": objective}, run_id=run_id)
        return run_id

    def run(self, run_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def runs(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM runs ORDER BY created_at")]

    def close_run(self, run_id: str) -> None:
        with self.mutation():
            self.conn.execute(
                "UPDATE runs SET state='CLOSED', closed_at=? WHERE run_id=? AND state='ACTIVE'",
                (ids.now_iso(), run_id))
            self._event("run_closed", {}, run_id=run_id)

    def attach(self, run_id: str, session_id: str, resume_after_clear: bool = False) -> None:
        run = self.run(run_id)
        if run is None:
            raise SError(f"unknown run {run_id}")
        with self.mutation():
            self.conn.execute(
                "INSERT INTO session_bindings"
                "(zcode_session_id, project_id, run_id, stage_id, binding_epoch,"
                " resume_after_clear, updated_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(zcode_session_id) DO UPDATE SET"
                " run_id=excluded.run_id, resume_after_clear=excluded.resume_after_clear,"
                " updated_at=excluded.updated_at",
                (session_id, self.project_id, run_id, None, 1,
                 int(resume_after_clear), ids.now_iso()))
            self._event("binding_attached", {"session_id": session_id}, run_id=run_id)

    # ---- controller ownership (D-8: S-internal CAS token, no long-held OS lock) ----

    def claim_controller(self, run_id: str, *, nonce: Optional[str] = None,
                         pid: Optional[int] = None, pid_start: Optional[str] = None,
                         expected_old: Optional[str] = None) -> bool:
        """CAS-claim run ownership. expected_old=None claims only when free;
        a caller that has mechanically proven the old owner dead passes
        expected_old=<old nonce> to take over. Never guesses (I5/I43)."""
        nonce = nonce or ids.new_nonce()
        with self.mutation():
            cur = self.conn.execute(
                "UPDATE runs SET controller_nonce=?, controller_pid=?,"
                " controller_pid_start=? WHERE run_id=? AND"
                " ((? IS NULL AND controller_nonce IS NULL) OR controller_nonce=?)",
                (nonce, pid, pid_start, run_id, expected_old, expected_old))
            if cur.rowcount != 1:
                return False
            self._event("controller_claimed",
                       {"nonce": nonce[:8], "pid": pid, "takeover": expected_old is not None},
                       run_id=run_id)
        return True

    def takeover_controller(self, run_id: str, *, nonce: Optional[str] = None,
                            pid: Optional[int] = None,
                            pid_start: Optional[str] = None,
                            expected_old: str) -> dict:
        """D-20 death-proof takeover of a run's controller token.

        Reads the current controller row; a free run (no old nonce) is
        claimed directly (fresh-claim-only semantics, unchanged). Otherwise
        the recorded owner must be mechanically proven dead via
        ``owner_alive(old_pid, old_pid_start)`` BEFORE any CAS:
        alive -> refuse ({"ok": False, "reason": "owner_alive"}); ambiguous
        -> refuse ({"ok": False, "reason": "ambiguous_fail_closed"}, I43);
        dead -> CAS claim against ``expected_old`` (guards the probe/claim
        race). Never guesses (I5/I43).

        Returns {"ok": True, "nonce", "takeover"} or
        {"ok": False, "reason": "unknown_run" | "owner_alive" |
         "ambiguous_fail_closed" | "cas_failed"}.
        """
        nonce = nonce or ids.new_nonce()
        row = self.controller(run_id)
        if row is None:
            return {"ok": False, "reason": "unknown_run"}
        old = row.get("controller_nonce")
        if old is None:
            if self.claim_controller(run_id, nonce=nonce, pid=pid,
                                     pid_start=pid_start):
                return {"ok": True, "nonce": nonce, "takeover": False}
            return {"ok": False, "reason": "cas_failed"}
        alive = owner_alive(row.get("controller_pid"),
                            row.get("controller_pid_start"))
        if alive is True:
            return {"ok": False, "reason": "owner_alive"}
        if alive is None:
            return {"ok": False, "reason": "ambiguous_fail_closed"}
        # alive is False: recorded owner mechanically proven dead -> CAS
        # against the nonce we inspected (a concurrent claim breaks it).
        if self.claim_controller(run_id, nonce=nonce, pid=pid,
                                 pid_start=pid_start, expected_old=expected_old):
            return {"ok": True, "nonce": nonce, "takeover": True}
        return {"ok": False, "reason": "cas_failed"}

    def release_controller(self, run_id: str, nonce: str) -> bool:
        with self.mutation():
            cur = self.conn.execute(
                "UPDATE runs SET controller_nonce=NULL, controller_pid=NULL,"
                " controller_pid_start=NULL WHERE run_id=? AND controller_nonce=?",
                (run_id, nonce))
            if cur.rowcount == 1:
                self._event("controller_released", {"nonce": nonce[:8]}, run_id=run_id)
        return cur.rowcount == 1

    def controller(self, run_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT controller_nonce, controller_pid, controller_pid_start,"
            " cancel_requested FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def request_cancel(self, run_id: str) -> bool:
        """External cancel = command input, NOT a lifecycle transition (D-8).
        Needs no controller token: the owner observes it on its next tick."""
        with self.mutation():
            cur = self.conn.execute(
                "UPDATE runs SET cancel_requested=1 WHERE run_id=? AND state='ACTIVE'",
                (run_id,))
            if cur.rowcount == 1:
                self._event("cancel_requested", {}, run_id=run_id)
        return cur.rowcount == 1

    def clear_cancel(self, run_id: str) -> None:
        with self.mutation():
            self.conn.execute(
                "UPDATE runs SET cancel_requested=0 WHERE run_id=?", (run_id,))

    def next_controller_epoch(self, run_id: str, host: str, pid: int) -> int:
        with self.mutation():
            self.conn.execute(
                "UPDATE controller_epochs SET ended_at=? WHERE run_id=? AND ended_at IS NULL",
                (ids.now_iso(), run_id))
            cur = self.conn.execute(
                "INSERT INTO controller_epochs(run_id, started_at, host, pid)"
                " VALUES (?,?,?,?)", (run_id, ids.now_iso(), host, pid))
            self._event("controller_epoch", {"host": host, "pid": pid}, run_id=run_id)
            return cur.lastrowid


# ---- project registration ---------------------------------------------------

def register_project_from_git(git_root: Optional[Path] = None,
                              display_name: str = "") -> dict:
    """Register (or resolve) the project for a git root. Never guesses by cwd
    for binding purposes — this is explicit project attach only."""
    root = Path(git_root or Path.cwd()).resolve()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(root),
                             capture_output=True, text=True, timeout=15)
        if top.returncode == 0:
            root = Path(top.stdout.strip()).resolve()
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                                cwd=str(root), capture_output=True, text=True, timeout=15)
        common_dir = (Path(common.stdout.strip()).resolve()
                      if common.returncode == 0 else root / ".git")
    except Exception:
        common_dir = root / ".git"
    existing = paths.find_project_by_git_root(str(root))
    if existing:
        rec = paths.load_registry()["projects"][existing]
        return {"project_id": existing, "created": False, **rec}
    project_id = ids.new_project_id()
    paths.register_project(project_id, str(root), str(common_dir), display_name)
    paths.ensure_project_layout(project_id)
    return {"project_id": project_id, "created": True,
            "git_root": str(root), "git_common_dir": str(common_dir),
            "display_name": display_name or root.name}
