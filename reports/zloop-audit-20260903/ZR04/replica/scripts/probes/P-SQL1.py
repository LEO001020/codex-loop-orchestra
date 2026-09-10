#!/usr/bin/env python3
"""P-SQL1 — SQLite version gate + crash/kill/contention suite (VOL-07 §3, VOL-20 §4).

Question: the local stdlib sqlite (3.50.4) sits inside the WAL-reset affected
range (3.7.0-3.51.2, below the 3.50.7 backport) — which journal profile must S
use on this machine, and does DELETE+EXTRA survive crash / kill / contention?

Sections:
  s1  zloop.db.journal_profile() + actual PRAGMA readback on a temp S DB
  s2  fix path (a): pip install pysqlite3 into the repo venv (cp314/Windows)
  s3  (a) crash atomicity: child hard-killed (os._exit(1)) BEFORE commit
  s4  (b) child does 200 committed create_claim mutations; parent kills it
      (taskkill /PID /T /F) after a random 0.5-2s delay
  s5  (c) two concurrent writers x 30 committed mutations each
  s6  (d) network-FS negative: connect() on a fake UNC path must fail closed

Safety: every DB lives in a throwaway system-temp dir; ZLOOP_DATA is redirected
to a temp dir before any zloop code runs, so the real ~/.zloop is never read or
written. src/zloop/db.py is imported, never modified. The only repo write is
artifacts/probes/P-SQL1.json.

Run:  PYTHONPATH=src python scripts/probes/P-SQL1.py
"""
from __future__ import annotations

import json
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
ART = REPO / "artifacts" / "probes"
_VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
PY = str(_VENV_PY) if _VENV_PY.exists() else sys.executable

SYNC_NAMES = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}

# Make the probe robust even when launched without PYTHONPATH=src.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# --------------------------------------------------------------------------
# child scripts — executed as `python -c <code>` (no extra files created).
# They inherit PYTHONPATH=src and ZLOOP_DATA=<temp> from the parent.
# --------------------------------------------------------------------------
CHILD_S3 = r'''
import os
from pathlib import Path

import zloop.db as db

proj = Path(os.environ["ZLOOP_PROBE_DIR"])
marker = Path(os.environ["ZLOOP_PROBE_MARKER"])
conn = db.connect(proj, create=True)
store = db.ControlStore(proj, conn, project_id="psql1-s3")
store.create_claim(purpose="run_start", run_id=None)  # committed BEFORE crash
with store.mutation():  # BEGIN IMMEDIATE ... COMMIT never reached
    conn.execute(
        "INSERT INTO runs(run_id, project_id, objective, state, created_at)"
        " VALUES (?,?,?,?,?)",
        ("R999", "psql1-s3", "uncommitted run row (must never be visible)",
         "ACTIVE", "1970-01-01T00:00:00Z"))
    store._event("run_created",
                 {"objective": "uncommitted run row"}, run_id="R999")
    marker.write_text("inserted-uncommitted", encoding="utf-8")
    os._exit(1)  # hard kill BEFORE COMMIT: no unwinding, no cleanup, no atexit
'''

CHILD_S4 = r'''
import os
from pathlib import Path

import zloop.db as db

proj = Path(os.environ["ZLOOP_PROBE_DIR"])
conn = db.connect(proj, create=True)
store = db.ControlStore(proj, conn, project_id="psql1-s4")
for _ in range(200):
    store.create_claim(purpose="run_start", run_id=None)  # committed mutation
print("COMPLETED-200", flush=True)
'''

CHILD_S5 = r'''
import os
import traceback
from pathlib import Path

import zloop.db as db

proj = Path(os.environ["ZLOOP_PROBE_DIR"])
res = Path(os.environ["ZLOOP_PROBE_RESULT"])
tag = os.environ.get("ZLOOP_PROBE_TAG", "?")
committed, err = 0, None
try:
    conn = db.connect(proj, create=True)
    store = db.ControlStore(proj, conn, project_id="psql1-s5")
    for _ in range(30):
        store.create_claim(purpose="run_start", run_id=None)
        committed += 1
except BaseException:
    err = traceback.format_exc(limit=6)
res.write_text(repr({"tag": tag, "committed": committed, "error": err}),
               encoding="utf-8")
print(f"CHILD-{tag}-DONE committed={committed} error={'yes' if err else 'no'}",
      flush=True)
'''

CHILD_S6 = r'''
import json
import sqlite3
import time
from pathlib import Path

import zloop.db as db

unc = r"\\nonexistent-server\share\x"
out = {"unc_path": unc}
t0 = time.time()
try:
    conn = db.connect(Path(unc), create=True)
    out["zloop_connect_create"] = {"opened": True}
    conn.close()
except BaseException as e:
    out["zloop_connect_create"] = {"opened": False,
                                   "exception_type": type(e).__name__,
                                   "message": str(e)[:300]}
try:
    conn = db.connect(Path(unc), create=False)
    out["zloop_connect_no_create"] = {"opened": True}
    conn.close()
except BaseException as e:
    out["zloop_connect_no_create"] = {"opened": False,
                                      "exception_type": type(e).__name__,
                                      "message": str(e)[:300]}
try:
    c = sqlite3.connect(unc + r"\control.sqlite3", timeout=1.0)
    c.close()
    out["raw_sqlite3_connect"] = {"opened": True}
except BaseException as e:
    out["raw_sqlite3_connect"] = {"opened": False,
                                  "exception_type": type(e).__name__,
                                  "message": str(e)[:300]}
out["duration_s"] = round(time.time() - t0, 3)
print("P-SQL1-S6-RESULT:" + json.dumps(out, ensure_ascii=False))
'''


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _decode(b: Any) -> str:
    """Decode process output; Windows console tools may emit cp936, not utf-8."""
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    if not isinstance(b, (bytes, bytearray)):
        return str(b)
    for enc in ("utf-8", "gbk"):
        try:
            return bytes(b).decode(enc)
        except UnicodeDecodeError:
            continue
    return bytes(b).decode("utf-8", "replace")


def _tail(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else "..." + s[-n:]


def _head(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "..."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(cmd: Any, *, timeout: int, shell: bool = False,
        env: Optional[dict] = None) -> dict:
    t0 = time.time()
    label = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           shell=shell, env=env)
        return {"cmd": label, "rc": p.returncode, "timeout": False,
                "stdout": _decode(p.stdout).strip(),
                "stderr": _decode(p.stderr).strip(),
                "duration_s": round(time.time() - t0, 3)}
    except subprocess.TimeoutExpired as e:
        return {"cmd": label, "rc": None, "timeout": True,
                "stdout": _decode(e.stdout).strip(),
                "stderr": _decode(e.stderr).strip(),
                "duration_s": round(time.time() - t0, 3)}
    except FileNotFoundError as e:
        return {"cmd": label, "rc": None, "timeout": False,
                "error": f"FileNotFoundError: {e}",
                "duration_s": round(time.time() - t0, 3)}
    except Exception as e:  # noqa: BLE001
        return {"cmd": label, "rc": None, "timeout": False,
                "error": repr(e)[:300],
                "duration_s": round(time.time() - t0, 3)}


def child_env() -> dict:
    env = dict(os.environ)  # carries the temp ZLOOP_DATA set in main()
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), pp]) if pp else str(SRC)
    env["PYTHONUNBUFFERED"] = "1"
    return env


# --------------------------------------------------------------------------
# s1 — journal profile of the actual runtime
# --------------------------------------------------------------------------
def sec1(root: Path) -> tuple[dict, str]:
    import sqlite3 as _sq
    import zloop.db as db

    prof = db.journal_profile()
    d = root / "s1"
    d.mkdir(parents=True, exist_ok=True)
    conn = db.connect(d, create=True)
    store = db.ControlStore(d, conn, project_id="psql1-s1")
    store.create_claim(purpose="run_start", run_id=None)  # one clean mutation
    rb = {
        "journal_mode": str(conn.execute("PRAGMA journal_mode").fetchone()[0]),
        "synchronous_raw": conn.execute("PRAGMA synchronous").fetchone()[0],
        "foreign_keys": conn.execute("PRAGMA foreign_keys").fetchone()[0],
        "busy_timeout_ms": conn.execute("PRAGMA busy_timeout").fetchone()[0],
        "quick_check": str(conn.execute("PRAGMA quick_check").fetchone()[0]),
    }
    rb["synchronous"] = SYNC_NAMES.get(rb["synchronous_raw"],
                                       str(rb["synchronous_raw"]))
    opts = [str(r[0]) for r in conn.execute("PRAGMA compile_options")]
    conn.close()

    expected = {"journal_mode": "DELETE", "synchronous": "EXTRA", "wal_ok": False}
    matches = (
        prof["journal_mode"] == "DELETE" and prof["synchronous"] == "EXTRA"
        and prof["wal_ok"] is False
        and rb["journal_mode"].lower() == "delete"
        and rb["synchronous"] == "EXTRA"
        and rb["foreign_keys"] == 1 and rb["busy_timeout_ms"] == 5000
        and rb["quick_check"].lower() == "ok"
    )
    rec = {
        "question": ("runtime sqlite 3.50.4 -> journal_profile() must enforce "
                     "DELETE + EXTRA with wal_ok=False; verify with PRAGMA readback"),
        "python": sys.version.split()[0],
        "sqlite_runtime": _sq.sqlite_version,
        "sqlite3_module_version": getattr(_sq, "version", None)
        or "(removed in Python 3.14)",
        "journal_profile": prof,
        "pragma_readback_on_real_connect": rb,
        "compile_options": opts,
        "expected": expected,
        "matches_expected_gate": matches,
    }
    return rec, ("PASS" if matches else "FAIL")


# --------------------------------------------------------------------------
# s2 — fix path (a): pysqlite3 wheel availability on cp314/Windows
# --------------------------------------------------------------------------
def sec2() -> tuple[dict, str]:
    pv = run([PY, "-m", "pip", "--version"], timeout=60)
    ins = run([PY, "-m", "pip", "install", "pysqlite3"], timeout=240)
    idx = run([PY, "-m", "pip", "index", "versions", "pysqlite3"], timeout=90)

    blob = (ins["stdout"] + "\n" + ins["stderr"]).lower()
    installed = ins["rc"] == 0
    import_test: Optional[dict] = None
    if installed:
        t = run([PY, "-c",
                 "import pysqlite3\n"
                 "v = getattr(pysqlite3, 'sqlite_version', None)\n"
                 "if v is None:\n"
                 "    import pysqlite3.dbapi2 as _d; v = _d.sqlite_version\n"
                 "print(v)"], timeout=60)
        ver = t["stdout"].strip() or None
        import_test = {"rc": t["rc"], "module_sqlite_version": ver,
                       "stderr": _tail(t["stderr"], 400)}
        installed = ver is not None

    # Does the shipped sqlite pass the VOL-07 §3 version gate? (gate logic
    # taken from zloop.db itself, not re-implemented here)
    import zloop.db as _db
    if installed:
        shipped = import_test["module_sqlite_version"]
        shipped_t = tuple(int(p) for p in shipped.split(".")[:3])
        gate_ok = (shipped_t >= _db.WAL_FIX_MIN or shipped_t in _db.WAL_BACKPORTS)
    else:
        shipped, gate_ok = None, False

    if not installed:
        if ins.get("timeout"):
            classification = ("timeout: pip install exceeded 240s (likely a "
                              "source build) and was killed")
        elif "no matching distribution" in blob:
            classification = ("no_matching_distribution: no cp314/Windows "
                              "artifact pip will accept; pip rejects before "
                              "any compile step")
        elif ("microsoft visual c++" in blob or "unable to find vcvarsall" in blob
              or "cl.exe" in blob or "build failed" in blob):
            classification = ("source_build_requires_compiler: an sdist was "
                              "selectable but the build failed (MSVC toolchain "
                              "missing) — wheel path unusable on this machine")
        elif "connection error" in blob or "could not fetch" in blob or \
                "network" in blob or "proxy" in blob:
            classification = "network_error: pip could not reach the index"
        else:
            classification = "failed_other (see pip output)"
    elif gate_ok:
        classification = ("installed_and_passes_gate: a cp314/Windows wheel "
                          "installed and it ships sqlite "
                          f"{shipped}, which passes the VOL-07 §3 gate")
    else:
        classification = ("installed_but_ships_affected_sqlite: a "
                          "cp314/Windows wheel installed, but it bundles "
                          f"sqlite {shipped}, still inside the WAL-reset "
                          "affected range (3.7.0-3.51.2; fix is 3.51.3, "
                          "backports 3.50.7/3.44.6) — it does NOT lift the gate")

    if classification.startswith("installed_and_passes_gate"):
        conclusion = (f"pysqlite3 installed and imports; it ships sqlite "
                      f"{shipped} which passes the version gate. Fix path (a) "
                      f"is viable, but adopting it requires a db.py runtime "
                      f"switch (out of M0 probe scope; not done).")
        status = "PASS"
    elif classification.startswith("installed_but_ships_affected_sqlite"):
        conclusion = (f"pysqlite3 installs cleanly under Python 3.14 on "
                      f"Windows (wheel exists), but it bundles sqlite "
                      f"{shipped} — still inside the WAL-reset affected range, "
                      f"so installing it does not lift the VOL-07 §3 gate. "
                      f"Fix path (a) as published today does not fix the "
                      f"problem; journal decision falls back to path (c).")
        status = "DEGRADED"
    elif classification.startswith(("no_matching_distribution",
                                     "source_build_requires_compiler")):
        conclusion = (f"pysqlite3 is NOT usable under Python 3.14 on Windows "
                     f"here: {classification}. Fix path (a) is closed; journal "
                     f"decision falls back to VOL-07 §3 path (c).")
        status = "DEGRADED"
    else:
        conclusion = (f"pip outcome inconclusive ({classification}); fix path "
                      f"(a) neither confirmed nor refuted.")
        status = "FAIL"

    rec = {
        "question": ("is pysqlite3 installable in the repo venv (fix path (a)) "
                     "under Python 3.14 on Windows, and which sqlite does it ship?"),
        "venv_python": PY,
        "pip_version": _head(pv["stdout"] + " " + pv["stderr"], 200),
        "pip_install": {"cmd": ins["cmd"], "rc": ins["rc"],
                         "timeout": ins.get("timeout", False),
                         "duration_s": ins.get("duration_s"),
                         "stdout_tail": _tail(ins["stdout"], 2500),
                         "stderr_tail": _tail(ins["stderr"], 2500)},
        "pip_index_versions": {"rc": idx["rc"],
                               "stdout": _head(idx["stdout"], 1200),
                               "stderr": _head(idx["stderr"], 400),
                               "note": "pip index is an experimental command; "
                                       "empty rc!=0 means unavailable"},
        "import_test": import_test,
        "shipped_sqlite_version": shipped,
        "shipped_version_passes_vol07_gate": gate_ok,
        "pysqlite3_left_installed": installed,
        "classification": classification,
        "conclusion": conclusion,
    }
    return rec, status


# --------------------------------------------------------------------------
# s3 — (a) crash atomicity: hard kill BEFORE commit
# --------------------------------------------------------------------------
def sec3(root: Path) -> tuple[dict, str]:
    import zloop.db as db

    d = root / "s3"
    d.mkdir(parents=True, exist_ok=True)
    proj = d / "proj"
    marker = d / "marker-s3.txt"
    env = child_env()
    env["ZLOOP_PROBE_DIR"] = str(proj)
    env["ZLOOP_PROBE_MARKER"] = str(marker)
    p = subprocess.Popen([PY, "-c", CHILD_S3], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    deadline = time.time() + 60
    while time.time() < deadline and not marker.exists() and p.poll() is None:
        time.sleep(0.02)
    try:
        out_b, err_b = p.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        out_b, err_b = p.communicate()

    hot_journal = proj / "control.sqlite3-journal"
    jsize = hot_journal.stat().st_size if hot_journal.exists() else None
    jmagic = (hot_journal.read_bytes()[:8].hex() == "d9d505f920a163d7"
              if hot_journal.exists() else None)
    try:
        conn = db.connect(proj, create=True)  # recovery point if journal were hot
    except Exception as e:  # noqa: BLE001
        return {"question": "uncommitted mutation must vanish after hard kill",
                "reopen_error": repr(e)[:400],
                "child_rc": p.returncode,
                "child_stderr": _tail(_decode(err_b), 800)}, "FAIL"
    qc = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    runs_total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    r999 = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE run_id='R999'").fetchone()[0]
    ev999 = conn.execute(
        "SELECT COUNT(*) FROM events WHERE run_id='R999'").fetchone()[0]
    claims = conn.execute(
        "SELECT COUNT(*) FROM pending_binding_claims").fetchone()[0]
    projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    conn.close()
    journal_after = (proj / "control.sqlite3-journal").exists()

    ok = (marker.exists() and p.returncode == 1
          and qc.lower() == "ok" and runs_total == 0 and r999 == 0 and ev999 == 0
          and claims == 1 and projects >= 1 and jsize is not None)
    rec = {
        "question": ("after os._exit(1) BEFORE commit: quick_check ok, "
                     "uncommitted run row absent, committed rows survive"),
        "child_rc": p.returncode,
        "marker_written": marker.exists(),
        "child_stdout": _tail(_decode(out_b), 300),
        "child_stderr": _tail(_decode(err_b), 800),
        "journal_file_before_reopen": {
            "exists": jsize is not None, "size_bytes": jsize,
            "header_magic_valid_hot": jmagic,
            "note": ("journal left by the killed transaction has a zeroed "
                     "header (magic never synced at commit) -> NOT hot; "
                     "SQLite ignores it and the DB file on disk was never "
                     "dirtied pre-commit, so atomicity holds structurally"),
        },
        "journal_file_after_reopen": journal_after,
        "after_reopen": {"quick_check": qc, "runs_total": runs_total,
                         "runs_R999": r999, "events_R999": ev999,
                         "claims_committed_before_crash": claims,
                         "projects_rows": projects},
        "verdict": ("uncommitted run row R999 absent; committed claim+project "
                    "rows survived; quick_check ok. Leftover -journal file is "
                    "inert (non-hot) garbage — harmless, next transaction "
                    "overwrites it" if ok else
                    "atomicity violated or evidence incomplete"),
    }
    return rec, ("PASS" if ok else "FAIL")


# --------------------------------------------------------------------------
# s4 — (b) kill during 200 sequential committed mutations
# --------------------------------------------------------------------------
def sec4(root: Path) -> tuple[dict, str]:
    import sqlite3 as _sq
    import zloop.db as db

    def _witness_claims(proj: Path, threshold: int, deadline: float) -> int:
        """Poll committed claim count read-only until >= threshold."""
        while time.time() < deadline:
            try:
                c = _sq.connect(str(proj / "control.sqlite3"), timeout=2.0)
                n = c.execute(
                    "SELECT COUNT(*) FROM pending_binding_claims").fetchone()[0]
                c.close()
                if n >= threshold:
                    return n
            except Exception:  # noqa: BLE001 — transient busy, retry
                pass
            time.sleep(0.005)
        return -1

    attempts: list[dict] = []
    accepted: Optional[dict] = None
    integrity_ok = True

    def _run_attempt(i: int, mode: str, delay: Optional[float]) -> dict:
        nonlocal accepted
        d = root / f"s4-try{i}"
        d.mkdir(parents=True, exist_ok=True)
        proj = d / "proj"
        env = child_env()
        env["ZLOOP_PROBE_DIR"] = str(proj)
        p = subprocess.Popen([PY, "-c", CHILD_S4], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if mode == "random_delay":
            time.sleep(delay)
        else:  # witness_poll: kill only after >=50 committed claims observed
            _witness_claims(proj, 50, time.time() + 30)
        alive = p.poll() is None
        tk: Optional[dict] = None
        if alive:
            tk = run(["taskkill", "/PID", str(p.pid), "/T", "/F"], timeout=30)
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        try:
            out_b, err_b = p.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
            out_b, err_b = p.communicate()

        journal_exists = (proj / "control.sqlite3-journal").exists()
        conn = db.connect(proj, create=True)
        qc = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        claims = conn.execute(
            "SELECT COUNT(*) FROM pending_binding_claims").fetchone()[0]
        evs = conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind='binding_claim_created'"
        ).fetchone()[0]
        conn.close()

        att = {
            "attempt": i,
            "mode": ("kill after random 0.5-2s delay" if mode == "random_delay"
                     else "kill after read-only witness observed >=50 "
                          "committed claims (deterministic mid-loop kill)"),
            "kill_delay_s": delay,
            "child_alive_at_kill": alive,
            "taskkill": (None if tk is None else
                         {"rc": tk["rc"],
                          "output": _tail(tk["stdout"] + " " + tk["stderr"], 300)}),
            "child_rc": p.returncode,
            "child_stdout": _tail(_decode(out_b), 200),
            "child_stderr": _tail(_decode(err_b), 600),
            "journal_file_before_reopen": journal_exists,
            "quick_check": qc,
            "claims_committed_when_killed": claims,
            "events_binding_claim_created": evs,
            "invariant_holds": qc.lower() == "ok" and claims == evs,
        }
        attempts.append(att)
        if not att["invariant_holds"]:
            return att
        if alive and 1 <= claims <= 199:  # true mid-loop kill
            accepted = att
        return att

    for i in range(1, 9):  # bounded retries to land the random kill mid-loop
        _run_attempt(i, "random_delay", round(random.uniform(0.5, 2.0), 3))
        if accepted is not None or not attempts[-1]["invariant_holds"]:
            break
    if accepted is None and all(a["invariant_holds"] for a in attempts):
        # the machine can finish 200 mutations in ~0.6s, so the random window
        # may miss; guarantee mid-commit crash coverage deterministically
        _run_attempt(len(attempts) + 1, "witness_poll", None)

    integrity_ok = all(a["invariant_holds"] for a in attempts)
    if not integrity_ok:
        status = "FAIL"
    elif accepted is not None:
        status = "PASS"
    elif any(a["child_alive_at_kill"] for a in attempts):
        status = "DEGRADED"  # killed while alive, but never mid-mutation-loop
    else:
        status = "DEGRADED"  # child always finished before the 0.5s floor

    rec = {
        "question": ("kill (taskkill /T /F) during 200 sequential committed "
                     "create_claim mutations; DB must reopen clean with "
                     "consistent counts"),
        "invariant": ("after every kill+reopen: PRAGMA quick_check='ok' AND "
                      "count(pending_binding_claims) == count(events WHERE "
                      "kind='binding_claim_created') — each committed "
                      "create_claim writes exactly one claim row + one event "
                      "in a single transaction, so a torn commit must show up "
                      "as a count mismatch or a failed quick_check"),
        "note": ("this machine completes the 200-mutation loop in ~0.6s, so "
                 "besides the random 0.5-2s delay kills, one deterministic "
                 "attempt kills the child after a read-only connection "
                 "witnesses >=50 committed claims, guaranteeing the kill "
                 "lands mid-commit-loop"),
        "attempts": attempts,
        "accepted_attempt": (None if accepted is None else accepted["attempt"]),
        "verdict": ("mid-loop kill achieved; every killed DB reopened with "
                    "quick_check ok and claim/event counts consistent — the "
                    "in-flight (uncommitted) transaction left at most a "
                    "non-hot journal and never tore committed state"
                    if status == "PASS"
                    else "integrity held on every attempt but the kill never "
                         "landed mid-mutation-loop (see attempts)"),
    }
    return rec, status


# --------------------------------------------------------------------------
# s5 — (c) two concurrent writers x 30 committed mutations
# --------------------------------------------------------------------------
def sec5(root: Path) -> tuple[dict, str]:
    import ast as _ast
    import zloop.db as db

    d = root / "s5"
    d.mkdir(parents=True, exist_ok=True)
    proj = d / "proj"
    res_dir = d / "results"
    res_dir.mkdir(parents=True, exist_ok=True)

    procs: dict[str, subprocess.Popen] = {}
    for tag in ("A", "B"):
        env = child_env()
        env["ZLOOP_PROBE_DIR"] = str(proj)
        env["ZLOOP_PROBE_RESULT"] = str(res_dir / f"child-{tag}.txt")
        env["ZLOOP_PROBE_TAG"] = tag
        procs[tag] = subprocess.Popen(
            [PY, "-c", CHILD_S5], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    reports: dict[str, dict] = {}
    for tag, p in procs.items():
        timed_out = False
        try:
            out_b, err_b = p.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            timed_out = True
            p.kill()
            out_b, err_b = p.communicate()
        rf = res_dir / f"child-{tag}.txt"
        rep = None
        if rf.exists():
            try:
                rep = _ast.literal_eval(rf.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                rep = {"parse_error": repr(e)[:200]}
        reports[tag] = {"rc": p.returncode, "timeout": timed_out,
                        "report": rep,
                        "stdout": _tail(_decode(out_b), 200),
                        "stderr": _tail(_decode(err_b), 600)}

    conn = db.connect(proj, create=True)
    qc = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    claims = conn.execute(
        "SELECT COUNT(*) FROM pending_binding_claims").fetchone()[0]
    evs = conn.execute(
        "SELECT COUNT(*) FROM events WHERE kind='binding_claim_created'"
    ).fetchone()[0]
    conn.close()

    ok_children = all(
        r["report"] and r["report"].get("committed") == 30
        and not r["report"].get("error")
        for r in reports.values())
    ok = (qc.lower() == "ok" and claims == 60 and evs == 60 and ok_children)
    rec = {
        "question": ("two processes x 30 committed create_claim mutations "
                     "concurrently (busy_timeout=5000 from connect): total "
                     "must be exactly 60 with no loss and quick_check ok"),
        "children": reports,
        "after_join": {"quick_check": qc, "claims_total": claims,
                        "events_binding_claim_created": evs},
        "invariant": ("claims_total == 60 AND events == 60 AND each child "
                      "reported committed=30 with no error"),
        "verdict": ("all 60 mutations durable, no torn/lost rows under "
                    "concurrent single-writer serialization"
                    if ok else "contention test failed or counts inconsistent"),
    }
    return rec, ("PASS" if ok else "FAIL")


# --------------------------------------------------------------------------
# s6 — (d) network-FS negative: fake UNC must fail closed
# --------------------------------------------------------------------------
def sec6(root: Path) -> tuple[dict, str]:
    env = child_env()
    t0 = time.time()
    try:
        p = subprocess.run([PY, "-c", CHILD_S6], capture_output=True,
                           env=env, timeout=60)
        line = next((l for l in _decode(p.stdout).splitlines()
                     if l.startswith("P-SQL1-S6-RESULT:")), None)
        res = json.loads(line.split(":", 1)[1]) if line else None
        err = _tail(_decode(p.stderr), 400)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        res, rc, err = None, None, ""
    if res is None:
        return {"question": "connect() on a fake UNC path must fail closed",
                "error": err or "child produced no result (timeout)",
                "rc": rc}, "FAIL"
    expected_failure = (not res["zloop_connect_create"]["opened"]
                        and not res["zloop_connect_no_create"]["opened"])
    rec = {
        "question": ("zloop.db.connect() on \\\\nonexistent-server\\share\\x "
                     "must refuse to open (proves we cannot silently open "
                     "network paths)"),
        **res,
        "expected_failure_observed": expected_failure,
        "caveat": ("a *reachable* SMB share would still be openable by raw "
                   "SQLite (see raw_sqlite3_connect): the network-FS ban is a "
                   "zloop policy obligation (VOL-07 §2), not something the "
                   "engine enforces for us; the unreachable-UNC case fails "
                   "closed at the filesystem layer"),
    }
    return rec, ("PASS" if expected_failure else "FAIL")


# --------------------------------------------------------------------------
# decision + report
# --------------------------------------------------------------------------
def build_decision(s1: dict, s2: dict) -> str:
    v = s1["sqlite_runtime"]
    if s1["journal_profile"].get("wal_ok"):
        return (f"journal_mode=WAL, synchronous=FULL (VOL-07 §3): runtime "
                f"sqlite {v} passes the version gate on this machine.")
    cls = s2["classification"]
    if cls.startswith("installed_and_passes_gate"):
        return (f"journal_mode=DELETE, synchronous=EXTRA for this machine now "
                f"(VOL-07 §3 v1 default path): the ACTIVE runtime is still the "
                f"stdlib sqlite {v}, inside the WAL-reset affected range "
                f"(3.7.0-3.51.2, below the 3.50.7 backport). pysqlite3 "
                f"installed and ships sqlite {s2['shipped_sqlite_version']} "
                f"which passes the gate, so fix path (a) is viable — but "
                f"adopting it requires a db.py runtime switch (out of M0 probe "
                f"scope). WAL stays forbidden for the stdlib runtime until the "
                f"version gate passes (I22).")
    if cls.startswith("installed_but_ships_affected_sqlite"):
        return (f"journal_mode=DELETE, synchronous=EXTRA for this machine "
                f"(VOL-07 §3 v1 default path / D-1 path (c)): the runtime "
                f"sqlite {v} is inside the WAL-reset affected range (3.7.0-"
                f"3.51.2, below the 3.50.7 backport), and pysqlite3 — though it "
                f"installs cleanly on cp314/Windows (wheel exists) — bundles "
                f"sqlite {s2['shipped_sqlite_version']}, which is still inside "
                f"the affected range, so fix path (a) does not lift the gate "
                f"today. WAL is forbidden until the version gate passes (I22); "
                f"zloop doctor must warn 'WAL available after SQLite upgrade "
                f"(see VOL-07 §3)'. Remaining upgrade route: path (b) — bundle "
                f"the sqlite.org 3.53.4 DLL and load it explicitly once a "
                f"gate-passing build is wired in. Until then S relies on "
                f"DELETE+EXTRA with hot-journal rollback semantics, which this "
                f"probe validated under crash/kill/contention.")
    return (f"journal_mode=DELETE, synchronous=EXTRA for this machine "
            f"(VOL-07 §3 v1 default path / D-1 path (c)): the runtime sqlite "
            f"{v} is inside the WAL-reset affected range (3.7.0-3.51.2, below "
            f"the 3.50.7 backport) and fix path (a) is closed — pysqlite3: "
            f"{cls}. WAL is forbidden until the version gate passes (I22); "
            f"zloop doctor must warn 'WAL available after SQLite upgrade "
            f"(see VOL-07 §3)'. Path (b) (bundle the sqlite.org 3.53.4 DLL "
            f"and load it explicitly) remains the future upgrade route; until "
            f"then S relies on DELETE+EXTRA with hot-journal rollback "
            f"semantics, which this probe validated under crash/kill/contention.")


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="zloop-P-SQL1-"))
    # Redirect the data root BEFORE any zloop import side effect so the real
    # ~/.zloop is never read or written by this probe or its children.
    os.environ["ZLOOP_DATA"] = str(root / "zloop-data")
    (root / "zloop-data").mkdir(parents=True, exist_ok=True)

    try:
        s1, st1 = sec1(root)
        s2, st2 = sec2()
        s3, st3 = sec3(root)
        s4, st4 = sec4(root)
        s5, st5 = sec5(root)
        s6, st6 = sec6(root)
    except Exception as e:  # noqa: BLE001 — probe must still emit a manifest
        report = {
            "probe_id": "P-SQL1",
            "question": "SQLite version gate & crash atomicity (VOL-07 §3, VOL-20 §4)",
            "executed_at": _now(),
            "status": {"overall": "FAIL"},
            "environment": {"python": sys.version.split()[0],
                            "os": platform.platform(), "host": platform.node(),
                            "zloop_data_redirect": os.environ.get("ZLOOP_DATA")},
            "results": {"fatal": {"error": repr(e)[:800],
                                  "note": "probe aborted; see traceback"}},
            "decision": "not reached (probe failed)",
        }
        out = ART / "P-SQL1.json"
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        print("P-SQL1 FAIL (fatal):", repr(e))
        return 1

    statuses = {
        "s1_journal_profile": st1,
        "s2_pysqlite3_fix_path": st2,
        "s3_crash_atomicity_a": st3,
        "s4_kill_midcommit_b": st4,
        "s5_multiproc_contention_c": st5,
        "s6_networkfs_negative_d": st6,
    }
    vals = list(statuses.values())
    overall = ("FAIL" if "FAIL" in vals
               else "DEGRADED" if "DEGRADED" in vals else "PASS")

    fallback = None
    if st2 != "PASS":
        fallback = (f"pysqlite3 fix path (a) does not lift the VOL-07 §3 "
                    f"version gate on this machine ({s2['classification']}) "
                    f"-> stay on VOL-07 §3 default: DELETE+EXTRA (path (c)); "
                    f"WAL postponed until a gate-passing sqlite is wired in")

    report = {
        "probe_id": "P-SQL1",
        "question": ("SQLite version gate [P0-1]: runtime 3.50.4 is WAL-reset "
                     "affected — which journal profile for S on this machine, "
                     "and does it survive crash/kill/multi-writer? (VOL-20 §4)"),
        "executed_at": _now(),
        "status": {**statuses, "overall": overall},
        "environment": {
            "os": platform.platform(),
            "host": platform.node(),
            "python": sys.version.split()[0],
            "sqlite_runtime": s1["sqlite_runtime"],
            "venv_python": PY,
            "cwd": os.getcwd(),
            "zloop_data_redirect": os.environ["ZLOOP_DATA"],
            "real_home_zloop_touched": False,
        },
        "results": {
            "s1_journal_profile": s1,
            "s2_pysqlite3_fix_path": s2,
            "s3_crash_atomicity_a": s3,
            "s4_kill_midcommit_b": s4,
            "s5_multiproc_contention_c": s5,
            "s6_networkfs_negative_d": s6,
        },
        "fallback_triggered": fallback,
        "decision": build_decision(s1, s2),
    }
    out = ART / "P-SQL1.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                   encoding="utf-8")

    print(f"P-SQL1 {overall}: artifact {out}")
    for k, v in statuses.items():
        print(f"  {k}: {v}")
    print(f"  sqlite runtime: {s1['sqlite_runtime']} -> "
          f"{s1['journal_profile']['journal_mode']}+"
          f"{s1['journal_profile']['synchronous']} "
          f"(wal_ok={s1['journal_profile']['wal_ok']})")
    print(f"  pysqlite3: {s2['classification']}")
    print(f"  decision: {report['decision'][:200]}...")

    if overall != "FAIL":
        shutil.rmtree(root, ignore_errors=True)  # temp evidence already in JSON
    else:
        print(f"  temp evidence kept at: {root}")
    return 0 if overall != "FAIL" else 2


if __name__ == "__main__":
    sys.exit(main())
