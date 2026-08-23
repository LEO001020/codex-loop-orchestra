"""test_windows_wsl_paths.py — cross-platform path discipline.

Covers: pathlib-only path construction, no hardcoded separators anywhere in
the implementation, Windows-style and WSL-style path inputs, and the
packet-id filename rail that keeps ids inside data/ on every platform.
"""
from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath

import pytest

from tests.orchestration_v2.conftest import V2_SOURCE_FILES, make_root

from l2_consumer import L2QueuePaths, load_policy
from orchestration_common import (
    LoopPaths,
    append_ndjson,
    atomic_write_json,
    file_lock,
    read_json,
)
from statemachine_v2 import PACKET_ID_RE, safe_pid_filename

SOURCE_FILES = V2_SOURCE_FILES


# ---------------------------------------------------------------------------
# static source discipline — no hardcoded separators
# ---------------------------------------------------------------------------
def test_sources_found():
    assert len(SOURCE_FILES) >= 15


def test_no_os_path_join_anywhere():
    """All joins go through pathlib's / operator — os.path.join with mixed
    separators is the classic Windows/WSL divergence source."""
    offenders = [str(p) for p in SOURCE_FILES
                 if "os.path.join" in p.read_text(encoding="utf-8")]
    assert not offenders, offenders


def test_no_backslash_path_literals():
    r"""No 'C:\...' or 'data\\...' style literals in any source file."""
    pattern = re.compile(r"""["'][A-Za-z]:\\|["']data\\\\|["']config\\\\""")
    offenders = []
    for p in SOURCE_FILES:
        if pattern.search(p.read_text(encoding="utf-8")):
            offenders.append(str(p))
    assert not offenders, offenders


def test_no_hardcoded_posix_absolute_loop_paths():
    """No absolute /home/... or /mnt/... LOOP paths baked into code."""
    pattern = re.compile(r"""["'](/home/|/mnt/[a-z]/|C:/Users)""")
    offenders = []
    for p in SOURCE_FILES:
        if pattern.search(p.read_text(encoding="utf-8")):
            offenders.append(str(p))
    assert not offenders, offenders


def test_every_module_uses_pathlib():
    offenders = [str(p) for p in SOURCE_FILES
                 if "from pathlib import" not in p.read_text(encoding="utf-8")]
    assert not offenders, "every module must build paths via pathlib"


# ---------------------------------------------------------------------------
# LoopPaths — derived layout is pure pathlib
# ---------------------------------------------------------------------------
def test_loop_paths_all_derived_via_pathlib(tmp_path):
    paths = LoopPaths.resolve(tmp_path)
    for name in ("data", "config", "events", "ledger", "l2_queue_dir",
                 "l2_pending", "l2_claims_dir", "l2_heartbeat",
                 "governor_dir", "budget_dir", "router_dir", "usage_dir",
                 "refill_dir", "token_ledger", "run_role_map"):
        value = getattr(paths, name)
        assert isinstance(value, Path), name
        assert value.is_relative_to(tmp_path), name


def test_loop_paths_env_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_ROOT", str(tmp_path))
    assert LoopPaths.resolve().root == tmp_path.resolve()
    explicit = tmp_path / "elsewhere"
    assert LoopPaths.resolve(explicit).root == explicit.resolve()


def test_policy_declared_dirs_resolve_under_root(tmp_path):
    """Policy files declare dirs with forward slashes ('data/l2_queue');
    Path() treats those correctly on BOTH Windows and POSIX."""
    root = make_root(tmp_path)
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    qp = L2QueuePaths.from_policy(root, policy)
    assert qp.pending.is_relative_to(root)
    assert qp.claims.is_relative_to(root)
    # the declared value contains no backslashes and no absolute anchor
    declared = str(policy["l2_queue"]["dir"])
    assert "\\" not in declared and not declared.startswith("/")
    # PureWindowsPath accepts the same forward-slash relative form
    assert not PureWindowsPath(declared).is_absolute()


# ---------------------------------------------------------------------------
# WSL-style and Windows-style roots
# ---------------------------------------------------------------------------
def test_wsl_style_root_works(tmp_path):
    """A deep POSIX path (the WSL plane shape) round-trips through every
    IO helper."""
    root = tmp_path / "mnt-c" / "loop" / "codex-loop-s-f2"
    paths = LoopPaths.resolve(root)
    atomic_write_json(paths.ledger, {"packets": {}})
    assert read_json(paths.ledger) == {"packets": {}}
    append_ndjson(paths.events, {"event": "x"}, lock_path=paths.events_lock)
    assert paths.events.read_text().strip()


def test_spaces_and_unicode_in_root(tmp_path):
    root = tmp_path / "Program Files clone" / "löop root"
    paths = LoopPaths.resolve(root)
    atomic_write_json(paths.ledger, {"ok": True})
    assert read_json(paths.ledger) == {"ok": True}
    with file_lock(paths.events_lock):
        pass  # advisory lock works on paths with spaces/unicode


def test_windows_shape_relative_layout():
    """The relative layout is identical when interpreted by Windows path
    semantics — no component ever contains a separator."""
    paths = LoopPaths(Path("."))
    for prop in ("events", "ledger", "l2_pending", "l2_heartbeat"):
        rel = getattr(paths, prop)
        win = PureWindowsPath(*rel.parts)
        assert win.parts == rel.parts, prop


# ---------------------------------------------------------------------------
# packet-id filename rail (ids can never escape data/ on any platform)
# ---------------------------------------------------------------------------
def test_safe_pid_accepts_normal_ids():
    for pid in ("p1", "wave3.packet-7", "A_b-c.d"):
        assert safe_pid_filename(pid) == pid


@pytest.mark.parametrize("pid", [
    "../escape", "a/b", "a\\b", "..", "C:evil", "p:1", "", None,
    "x" * 97, ".hidden-start-not-alnum"])
def test_safe_pid_rejects_traversal_shapes(pid):
    if pid == ".hidden-start-not-alnum":
        assert not PACKET_ID_RE.fullmatch(pid)
        return
    with pytest.raises(SystemExit):
        safe_pid_filename(pid)


def test_idem_key_filenames_are_portable(tmp_path):
    """Claim/completion filenames replace ':' (illegal on Windows/NTFS)."""
    root = make_root(tmp_path)
    from l2_consumer import L2Consumer
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    consumer = L2Consumer(root, policy=policy, dispatcher=lambda r: True)
    rec = consumer.enqueue("p1", "r1", 1)
    path = consumer._claim_path(rec.idem_key)
    assert ":" not in path.name, "':' is illegal in NTFS filenames"
    assert PureWindowsPath(path.name).name == path.name


# ---------------------------------------------------------------------------
# atomic IO helpers behave on this platform
# ---------------------------------------------------------------------------
def test_atomic_write_creates_parents_and_replaces(tmp_path):
    target = tmp_path / "deep" / "nested" / "state.json"
    atomic_write_json(target, {"v": 1})
    atomic_write_json(target, {"v": 2})
    assert json.loads(target.read_text()) == {"v": 2}
    leftovers = [p for p in target.parent.iterdir() if p.suffix == ".tmp"]
    assert not leftovers, "no temp litter after replace"


def test_append_ndjson_locked_lines(tmp_path):
    target = tmp_path / "events.ndjson"
    for i in range(5):
        append_ndjson(target, {"i": i})
    lines = target.read_text().splitlines()
    assert [json.loads(l)["i"] for l in lines] == list(range(5))
