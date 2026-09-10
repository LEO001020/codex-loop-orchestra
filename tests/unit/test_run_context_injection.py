"""Run-context injection: once at task boundaries, fail-open, bounded.

Verifies the PRESENT/PROVENANCE wiring: worker spawn prompts (v1 single,
v2, headless wave) and the SessionStart/SubagentStart context hook embed
the derived Current Run View / Root pointer — and that a broken ledger
never breaks the spawn path (no ceremony, no per-turn injection).
"""
from __future__ import annotations

import json
import sys

import pytest

import dispatch as dispatch_mod


def write_ledger(loop, packets, cursor=4321):
    loop.set_ledger({"packets": packets, "event_cursor": cursor,
                     "loop_state": "planning"})


def active_entry(task, paths, role="worker"):
    return {"state": "RUNNING", "attempts": 0, "role": role,
            "task_name": task, "authorized_paths": paths,
            "cwd": "E:\\tmp\\wt",
            "history": [{"ts": 1.0, "to": "RUNNING", "via": "dispatched"}]}


def test_v1_spawn_prompt_embeds_view_and_own_packet(loop, monkeypatch):
    monkeypatch.setattr(dispatch_mod, "ROOT", str(loop.root))
    write_ledger(loop, {"peer-a": active_entry("event persistence",
                                               ["harness/events/**"])})
    pkt = {"packet_id": "w1-p01", "goal": "implement thing",
           "authorized_paths": ["src/x.py"], "acceptance": ["true"],
           "constraints": []}
    prompt = dispatch_mod.spawn_prompt(pkt, "E:\\tmp\\wt-w1p01")
    assert "goal: implement thing" in prompt
    assert "authorized_paths" in prompt
    # the derived view of OTHER concurrent work is present
    assert "peer-a" in prompt and "event persistence" in prompt
    assert "state_cursor: 4321" in prompt
    assert "EVIDENCE ENTRY" in prompt


def test_v1_spawn_prompt_fail_open_on_corrupt_ledger(loop, monkeypatch):
    monkeypatch.setattr(dispatch_mod, "ROOT", str(loop.root))
    (loop.data / "progress_ledger.json").write_text("{bad json",
                                                    encoding="utf-8")
    pkt = {"packet_id": "w1-p02", "goal": "g", "authorized_paths": ["a/"],
           "acceptance": ["true"], "constraints": []}
    prompt = dispatch_mod.spawn_prompt(pkt, "wt")
    assert "goal: g" in prompt  # the 4-field contract is intact


def test_prompt_boundedness_many_active(loop, monkeypatch):
    monkeypatch.setattr(dispatch_mod, "ROOT", str(loop.root))
    packets = {("peer%02d" % i): active_entry("t%d" % i, ["a/%d" % i])
               for i in range(40)}
    write_ledger(loop, packets)
    pkt = {"packet_id": "w1-p03", "goal": "g", "authorized_paths": ["a/"],
           "acceptance": ["true"], "constraints": []}
    prompt = dispatch_mod.spawn_prompt(pkt, "wt")
    lines = prompt.splitlines()
    assert len(lines) < 140  # bounded: 4-field header + capped view block
    assert "+28 more" in prompt


def test_hook_block_session_start_root_pointer(loop):
    root = loop.root
    (root / "config" / "global_working_agreement.md").write_text(
        "agree", encoding="utf-8")
    (root / "AGENTS.md").write_text("agents", encoding="utf-8")
    write_ledger(loop, {"p1": active_entry("t", ["a/"])})
    sys.path.insert(0, str(root / "harness"))
    import run_view
    out = run_view.hook_block(str(root), "SessionStart")
    assert out.startswith("ROOT RUN CONTEXT")
    assert "canonical state: data/progress_ledger.json" in out
    assert "run_view.py" in out and "run_evidence.py" in out
    assert "superseded" in out
    out = run_view.hook_block(str(root), "SubagentStart")
    assert "ACTIVE WORK" in out and "p1" in out
    assert "state_cursor: 4321" in out


def test_hook_context_end_to_end_injection(loop, capsys):
    root = loop.root
    (root / "config" / "global_working_agreement.md").write_text(
        "agree", encoding="utf-8")
    (root / "AGENTS.md").write_text("agents", encoding="utf-8")
    write_ledger(loop, {"p1": active_entry("t", ["a/"])})
    import global_loop_mode
    assert global_loop_mode.emit_context(root, "SessionStart") == 0
    captured = capsys.readouterr().out
    doc = json.loads(captured)
    ctx = doc["hookSpecificOutput"]["additionalContext"]
    assert "ROOT RUN CONTEXT" in ctx
    assert global_loop_mode.emit_context(root, "SubagentStart") == 0
    doc = json.loads(capsys.readouterr().out)
    ctx = doc["hookSpecificOutput"]["additionalContext"]
    assert "ACTIVE WORK" in ctx and "p1" in ctx
    assert "agree" in ctx  # static agreement is still injected first


def test_headless_wave_prompt_embeds_view(loop):
    write_ledger(loop, {"peer-b": active_entry("reviewer lifecycle",
                                               ["hooks/**"],
                                               role="reviewer")})
    from orchestration_common import LoopPaths, OrchestrationPolicy
    from headless_wave import build_commands
    paths = LoopPaths.resolve(loop.root)
    policy = OrchestrationPolicy.load(paths)
    manifest = loop.root / "manifest.json"
    manifest.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    task = {"task_id": "t1", "task_name": "hw", "role": "worker",
            "sandbox": "read-only", "cwd": str(loop.root),
            "prompt": "do the bounded thing"}
    _pid, _run_id, _sup, worker, _rdir = build_commands(
        task, manifest, paths, policy, timeout=60.0)
    prompt = worker[-1]
    assert "do the bounded thing" in prompt
    assert "peer-b" in prompt and "reviewer lifecycle" in prompt
    assert "state_cursor: 4321" in prompt
