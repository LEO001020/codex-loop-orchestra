"""Hook subsystem tests (VOL-05): capture, self-read guard, bind-token claim
(I32), recovery injection, and D-16 strict project scoping (capture/claim
proceed only when the event cwd lies inside a registered project's git_root).
All invocations go through the real process (`python -m zloop.hook`) with
ZLOOP_DATA pointed at a tmp root."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import db as zdb            # noqa: E402
from zloop import evidence as zev      # noqa: E402
from zloop import paths                # noqa: E402

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")


def run_hook(line: str) -> subprocess.CompletedProcess:
    """Feed one stdin line to `python -m zloop.hook` (inherits ZLOOP_DATA)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "zloop.hook"],
        input=line, text=True, capture_output=True,
        env=env, timeout=60,
    )


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZLOOP_DATA", str(tmp_path))
    return tmp_path


def make_project(root: Path, project_id: str = "proj1") -> str:
    paths.register_project(project_id, str(root / f"repo-{project_id}"),
                           str(root / f"repo-{project_id}" / ".git"), "Repo")
    paths.ensure_project_layout(project_id)
    return project_id


def project_cwd(root: Path, project_id: str = "proj1", *sub: str) -> str:
    """A cwd at (or inside) the registered git_root of <project_id>."""
    p = root / f"repo-{project_id}"
    for s in sub:
        p = p / s
    return str(p)


def open_store(project_id: str) -> zdb.ControlStore:
    pdir = paths.project_dir(project_id)
    conn = zdb.connect(pdir, create=True)
    return zdb.ControlStore(pdir, conn, project_id=project_id)


# ---- capture: all seven events (VOL-05 §2) ---------------------------------

SEVEN_EVENTS = [
    ("SessionStart", {"hook_event_name": "SessionStart", "session_id": "sess-A",
                      "source": "startup"}),
    ("UserPromptSubmit", {"hook_event_name": "UserPromptSubmit",
                          "session_id": "sess-A", "prompt": "fix the parser bug"}),
    ("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": "sess-A",
                    "tool_name": "Bash", "tool_input": {"command": "ls -la"}}),
    ("PermissionRequest", {"hook_event_name": "PermissionRequest",
                           "session_id": "sess-A", "tool_name": "Bash"}),
    ("PostToolUse", {"hook_event_name": "PostToolUse", "session_id": "sess-A",
                     "tool_name": "Bash", "tool_input": {"command": "ls -la"},
                     "tool_response": {"stdout": "file1 file2"}}),
    ("PostToolUseFailure", {"hook_event_name": "PostToolUseFailure",
                            "session_id": "sess-A", "tool_name": "Bash",
                            "error": "exit 1", "is_interrupt": False}),
    ("Stop", {"hook_event_name": "Stop", "session_id": "sess-A",
              "last_assistant_message": "all done, parser fixed"}),
]

EXPECTED_KINDS = ["session_start", "prompt", "tool_call", "permission_request",
                  "tool_result", "tool_failure", "stop"]


def test_seven_events_journaled_with_correct_kind(data_root):
    pid = make_project(data_root)  # single registered project, session unbound
    cwd = project_cwd(data_root)   # D-16: cwd inside the registered git_root
    for name, ev in SEVEN_EVENTS:
        r = run_hook(json.dumps({**ev, "cwd": cwd}))
        assert r.returncode == 0
        assert r.stdout == ""  # no binding -> recovery stays silent too
    jf = paths.history_session_file(pid, "sess-A")
    lines = zev.read_journal(jf)
    assert [l["kind"] for l in lines] == EXPECTED_KINDS
    assert [l["event"] for l in lines] == [name for name, _ in SEVEN_EVENTS]
    # payload spot checks (redacted-safe content)
    assert json.loads(lines[0]["payload_inline"]) == {"source": "startup"}
    assert json.loads(lines[1]["payload_inline"]) == {"prompt": "fix the parser bug"}
    assert json.loads(lines[4]["payload_inline"])["tool_name"] == "Bash"
    assert json.loads(lines[6]["payload_inline"]) == {"last_assistant_message": "all done, parser fixed"}


def test_redaction_never_leaks_into_journal(data_root):
    pid = make_project(data_root)
    ev = {"hook_event_name": "PreToolUse", "session_id": "sess-R",
          "tool_name": "Bash", "cwd": project_cwd(data_root),
          "tool_input": {"command": "echo hi", "API_TOKEN": "leaky-abc-123456"}}
    r = run_hook(json.dumps(ev))
    assert r.returncode == 0
    jf = paths.history_session_file(pid, "sess-R")
    raw = jf.read_text(encoding="utf-8")
    assert "leaky-abc-123456" not in raw
    lines = zev.read_journal(jf)
    assert lines[0]["kind"] == "tool_call"
    assert "<redacted>" in lines[0]["payload_inline"]


# ---- D-16 strict project scoping ----------------------------------------------

def test_unrelated_cwd_not_journaled(data_root):
    """A registered project must NOT absorb traffic from an unrelated
    workspace (the old single-registered-project fallback, removed)."""
    pid = make_project(data_root)
    elsewhere = data_root / "elsewhere"
    elsewhere.mkdir()
    for name, ev in SEVEN_EVENTS:
        r = run_hook(json.dumps({**ev, "cwd": str(elsewhere)}))
        assert r.returncode == 0
        assert r.stdout == ""
    assert not paths.history_session_file(pid, "sess-A").exists()
    assert not list((data_root / "projects").rglob("*.ndjson"))


def test_missing_cwd_field_not_journaled(data_root):
    """No cwd in the event -> no cwd match -> nothing journaled (fail-safe)."""
    pid = make_project(data_root)
    for name, ev in SEVEN_EVENTS:
        r = run_hook(json.dumps(ev))
        assert r.returncode == 0 and r.stdout == ""
    assert not list((data_root / "projects").rglob("*.ndjson"))


def test_no_registered_project_skips_capture_silently(data_root):
    elsewhere = data_root / "elsewhere"
    elsewhere.mkdir()
    r = run_hook(json.dumps({"hook_event_name": "UserPromptSubmit",
                             "session_id": "sess-X", "prompt": "hi",
                             "cwd": str(elsewhere)}))
    assert r.returncode == 0
    assert r.stdout == ""
    assert not paths.registry_path().exists()


def test_unbound_session_journals_only_project_containing_cwd(data_root):
    """Multiple registered projects: an unbound session is journaled ONLY by
    the project whose git_root contains the cwd (subdirectory counts)."""
    make_project(data_root, "p1")
    make_project(data_root, "p2")
    r = run_hook(json.dumps({"hook_event_name": "UserPromptSubmit",
                             "session_id": "sess-Y", "prompt": "hi",
                             "cwd": project_cwd(data_root, "p1", "sub")}))
    assert r.returncode == 0
    assert r.stdout == ""
    jf1 = paths.history_session_file("p1", "sess-Y")
    lines = zev.read_journal(jf1)
    assert [l["kind"] for l in lines] == ["prompt"]
    assert lines[0]["run_id"] is None  # unbound capture carries no run
    assert not paths.history_session_file("p2", "sess-Y").exists()


def test_bound_session_capture_follows_cwd_scope(data_root):
    """A session bound to the project AND cwd inside it -> journaled with the
    run id; bound but cwd elsewhere -> nothing more is journaled."""
    pid = make_project(data_root)
    store = open_store(pid)
    rid = store.create_run("obj")
    store.attach(rid, "sess-BW")
    store.conn.close()

    r = run_hook(json.dumps({"hook_event_name": "UserPromptSubmit",
                             "session_id": "sess-BW", "prompt": "p",
                             "cwd": project_cwd(data_root)}))
    assert r.returncode == 0 and r.stdout == ""
    lines = zev.read_journal(paths.history_session_file(pid, "sess-BW"))
    assert [l["kind"] for l in lines] == ["prompt"]
    assert lines[0]["run_id"] == rid

    r2 = run_hook(json.dumps({"hook_event_name": "UserPromptSubmit",
                              "session_id": "sess-BW", "prompt": "p",
                              "cwd": str(data_root / "elsewhere")}))
    assert r2.returncode == 0 and r2.stdout == ""
    assert len(zev.read_journal(paths.history_session_file(pid, "sess-BW"))) == 1


def test_unknown_event_name_is_ignored(data_root):
    make_project(data_root)
    r = run_hook(json.dumps({"hook_event_name": "Whatever",
                             "session_id": "sess-U",
                             "cwd": project_cwd(data_root)}))
    assert r.returncode == 0
    assert r.stdout == ""
    assert not list((data_root / "projects").rglob("*.ndjson"))


# ---- self-read guard (recursion, VOL-06 §1.2) -------------------------------

def test_self_read_guard_skips_journaling(data_root):
    pid = make_project(data_root)
    for cmd in ("zloop history search foo", "zloop evidence show ev:s:1",
                "zloop checkpoint current"):
        r = run_hook(json.dumps({
            "hook_event_name": "PostToolUse", "session_id": "sess-G",
            "tool_name": "Bash", "cwd": project_cwd(data_root),
            "tool_input": {"command": cmd},
            "tool_response": {"stdout": "(no matches)"}}))
        assert r.returncode == 0
        assert r.stdout == ""
    jf = paths.history_session_file(pid, "sess-G")
    assert not jf.exists() or zev.read_journal(jf) == []


# ---- bind-token claim (I32, VOL-05 §4) ---------------------------------------

def _bind_event(session_id: str, nonce: str, tool_name: str = "Bash",
               cwd: str = "") -> dict:
    return {"hook_event_name": "PostToolUse", "session_id": session_id,
            "tool_name": tool_name, "cwd": cwd,
            "tool_input": {"command": "zloop run start \"objective\""},
            "tool_response": {"stdout": f"ZLOOP_BIND_TOKEN={nonce}\nrun started"}}


def test_bind_claim_happy_path(data_root):
    pid = make_project(data_root)
    store = open_store(pid)
    rid = store.create_run("test objective")
    nonce = store.create_claim(purpose="run_start", run_id=rid)
    store.conn.close()

    r = run_hook(json.dumps(_bind_event("sess-B", nonce,
                                        cwd=project_cwd(data_root))))
    assert r.returncode == 0
    assert len(r.stdout.strip().splitlines()) == 1  # exactly one JSON line
    assert len(r.stdout.strip()) <= 120
    out = json.loads(r.stdout.strip())
    assert out == {"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": f"[zloop] bound: run {rid}"}}

    store2 = open_store(pid)
    b = store2.binding("sess-B")
    assert b is not None and b["run_id"] == rid and b["project_id"] == pid
    store2.conn.close()


def test_bind_claim_requires_cwd_inside_project(data_root):
    """D-16: the claim fires ONLY when the hook cwd is inside the project —
    the same nonce presented from an unrelated workspace does NOT bind."""
    pid = make_project(data_root)
    store = open_store(pid)
    rid = store.create_run("obj")
    nonce = store.create_claim(purpose="run_start", run_id=rid)
    store.conn.close()

    r = run_hook(json.dumps(_bind_event("sess-BC", nonce,
                                        cwd=str(data_root / "elsewhere"))))
    assert r.returncode == 0 and r.stdout == ""
    store2 = open_store(pid)
    assert store2.binding("sess-BC") is None
    row = store2.conn.execute(
        "SELECT claimed_at FROM pending_binding_claims WHERE nonce=?",
        (nonce,)).fetchone()
    assert row["claimed_at"] is None  # nonce untouched — still claimable
    store2.conn.close()

    # the same nonce still binds when the hook fires inside the project
    r2 = run_hook(json.dumps(_bind_event("sess-BC", nonce,
                                         cwd=project_cwd(data_root))))
    assert r2.returncode == 0 and "bound: run" in r2.stdout
    store3 = open_store(pid)
    b = store3.binding("sess-BC")
    assert b is not None and b["run_id"] == rid and b["project_id"] == pid
    store3.conn.close()


def test_bind_replay_same_nonce_rejected(data_root):
    pid = make_project(data_root)
    store = open_store(pid)
    rid = store.create_run("obj")
    nonce = store.create_claim(purpose="run_start", run_id=rid)
    store.conn.close()

    r1 = run_hook(json.dumps(_bind_event("sess-C", nonce,
                                         cwd=project_cwd(data_root))))
    assert r1.returncode == 0 and "bound: run" in r1.stdout
    r2 = run_hook(json.dumps(_bind_event("sess-C", nonce,  # replay
                                         cwd=project_cwd(data_root))))
    assert r2.returncode == 0 and r2.stdout == ""

    store2 = open_store(pid)
    b = store2.binding("sess-C")
    assert b is not None and b["binding_epoch"] == 1  # unchanged by the replay
    row = store2.conn.execute(
        "SELECT claimed_at, claimed_by_session FROM pending_binding_claims"
        " WHERE nonce=?", (nonce,)).fetchone()
    assert row["claimed_at"] is not None
    assert row["claimed_by_session"] == "sess-C"
    store2.conn.close()


def test_forged_nonce_stays_silent(data_root):
    pid = make_project(data_root)
    r = run_hook(json.dumps(_bind_event("sess-F", "f" * 64,
                                        cwd=project_cwd(data_root))))
    assert r.returncode == 0 and r.stdout == ""
    store = open_store(pid)
    assert store.binding("sess-F") is None
    store.conn.close()


def test_bind_requires_bash_tool(data_root):
    pid = make_project(data_root)
    store = open_store(pid)
    rid = store.create_run("obj")
    nonce = store.create_claim(purpose="run_start", run_id=rid)
    store.conn.close()

    r = run_hook(json.dumps(_bind_event("sess-NB", nonce, tool_name="Read",
                                        cwd=project_cwd(data_root))))
    assert r.returncode == 0 and r.stdout == ""
    store2 = open_store(pid)
    assert store2.binding("sess-NB") is None
    store2.conn.close()


# ---- recovery branch (SessionStart only, VOL-05 §5) --------------------------

def test_recovery_compact_with_binding(data_root):
    pid = make_project(data_root)
    store = open_store(pid)
    rid = store.create_run("recovery objective for the run")
    store.attach(rid, "sess-D")
    store.conn.close()

    # D-16: recovery works off the exact session binding regardless of cwd
    # (here: an unrelated workspace — capture stays silent, recovery injects)
    r = run_hook(json.dumps({"hook_event_name": "SessionStart",
                             "session_id": "sess-D", "source": "compact",
                             "cwd": str(data_root / "elsewhere")}))
    assert r.returncode == 0
    out = json.loads(r.stdout.strip())
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "ACTIVE ZLOOP RUN" in text and rid in text
    assert "(ACTIVE)" in text
    assert "recovery objective for the run" in text
    assert "Current files/runtime/oracles override stale checkpoint prose." in text
    assert len(text) <= 1200

    # startup / resume with an exact binding also inject
    for src in ("startup", "resume"):
        r2 = run_hook(json.dumps({"hook_event_name": "SessionStart",
                                  "session_id": "sess-D", "source": src}))
        assert r2.returncode == 0 and "ACTIVE ZLOOP RUN" in r2.stdout


def test_recovery_includes_checkpoint_semantics(data_root):
    pid = make_project(data_root)
    store = open_store(pid)
    rid = store.create_run("obj with checkpoint")
    store.attach(rid, "sess-CK")
    store.conn.close()
    ck = paths.project_dir(pid) / "checkpoints" / "current.json"
    ck.write_text(json.dumps(
        {"semantic_summary": "Stage S01 executing; decided to retry on 429."}),
        encoding="utf-8")

    r = run_hook(json.dumps({"hook_event_name": "SessionStart",
                             "session_id": "sess-CK", "source": "compact"}))
    assert r.returncode == 0
    assert "Stage S01 executing; decided to retry on 429." in r.stdout


def test_recovery_clear_requires_resume_after_clear(data_root):
    pid = make_project(data_root)
    store = open_store(pid)
    rid = store.create_run("obj")
    store.attach(rid, "sess-CL")
    store.conn.close()

    r = run_hook(json.dumps({"hook_event_name": "SessionStart",
                             "session_id": "sess-CL", "source": "clear"}))
    assert r.returncode == 0 and r.stdout == ""

    store2 = open_store(pid)
    store2.set_resume_after_clear("sess-CL", True)
    store2.conn.close()
    r2 = run_hook(json.dumps({"hook_event_name": "SessionStart",
                              "session_id": "sess-CL", "source": "clear"}))
    assert r2.returncode == 0 and "ACTIVE ZLOOP RUN" in r2.stdout


def test_recovery_unbound_session_stays_silent(data_root):
    make_project(data_root)  # single project, but sess-Z has no binding
    for src in ("compact", "startup", "resume", "clear"):
        r = run_hook(json.dumps({"hook_event_name": "SessionStart",
                                 "session_id": "sess-Z", "source": src}))
        assert r.returncode == 0 and r.stdout == ""


# ---- fail-soft I3 --------------------------------------------------------------

def test_malformed_stdin_fail_soft(data_root):
    make_project(data_root)
    r = run_hook("not json")
    assert r.returncode == 0
    assert r.stdout == ""
    assert "Traceback" not in r.stderr
    r2 = run_hook("")
    assert r2.returncode == 0 and r2.stdout == ""
    r3 = run_hook('["not", "a", "dict"]')
    assert r3.returncode == 0 and r3.stdout == ""


def test_hook_module_always_returns_zero(data_root):
    import zloop.hook as zhook
    assert zhook.main() == 0


# ---- resolve_project_for_cwd (D-16 strict scoping, direct unit) ---------------

def test_resolve_project_for_cwd(data_root):
    from zloop import hook as zhook
    make_project(data_root, "p1")
    make_project(data_root, "p2")

    # deep subdirectory of p2's git_root resolves to p2
    hit = zhook.resolve_project_for_cwd(project_cwd(data_root, "p2", "deep"))
    assert hit is not None and hit["project_id"] == "p2"
    assert hit["git_root"] == str(data_root / "repo-p2")

    # the git_root itself counts (ancestor-or-equal)
    hit2 = zhook.resolve_project_for_cwd(project_cwd(data_root, "p1"))
    assert hit2 is not None and hit2["project_id"] == "p1"

    # sibling prefix is NOT an ancestor
    assert zhook.resolve_project_for_cwd(str(data_root / "repo-p2x")) is None
    # outside every registered project
    assert zhook.resolve_project_for_cwd(str(data_root / "elsewhere")) is None
    # missing / non-string cwd fails safe
    assert zhook.resolve_project_for_cwd("") is None
    assert zhook.resolve_project_for_cwd(None) is None
    assert zhook.resolve_project_for_cwd(123) is None


@pytest.mark.skipif(sys.platform != "win32",
                    reason="case-insensitive path matching is Windows behavior")
def test_resolve_project_for_cwd_case_insensitive(data_root):
    from zloop import hook as zhook
    make_project(data_root, "p1")
    upper = str(data_root / "repo-p1").upper()
    hit = zhook.resolve_project_for_cwd(upper)
    assert hit is not None and hit["project_id"] == "p1"
