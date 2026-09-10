#!/usr/bin/env python3
"""Bounded Sol-decision -> K3 plan-expansion pipeline.

The root supplies only two file handles: a DecisionSkeleton and a ControlPacket.
K3 reads those files in a fresh headless session and returns a schema-validated
packet DAG. No root transcript is inherited and ipybox is disabled for this
planning-only call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(os.environ.get("LOOP_ROOT", Path(__file__).resolve().parents[2]))
DECISION_SCHEMA = ROOT / "schemas" / "decision_skeleton.schema.json"
OUTPUT_SCHEMA = ROOT / "schemas" / "plan_expander.schema.json"
TOP_LEVEL_KEYS = {"control_packet_id", "decision_skeleton_id", "packets", "needs_decision"}
PACKET_KEYS = {"packet_id", "goal", "authorized_paths", "allowed_side_effects",
               "dependencies", "acceptance", "artifacts", "risk_tags",
               "decision_refs", "evidence_refs"}


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("%s: %s" % (path, exc)) from exc
    if not isinstance(value, dict):
        raise ValueError("%s: top level must be an object" % path)
    return value


def load_plan_settings(root: Path = ROOT) -> dict:
    path = root / "config" / "orchestration_policy_v2.toml"
    try:
        with path.open("rb") as handle:
            doc = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("plan policy unreadable %s: %s" % (path, exc)) from exc
    models = doc.get("models", {})
    required = ("k3_model", "k3_reasoning", "k3_context_tokens",
                "k3_compaction_tokens")
    missing = [key for key in required if not models.get(key)]
    if missing:
        raise ValueError("plan policy missing model settings: %s" %
                         ", ".join(missing))
    return {key: models[key] for key in required}


def validate(path: Path, schema_path: Path) -> dict:
    value = load_json(path)
    schema = load_json(schema_path)
    errors = sorted(Draft202012Validator(schema).iter_errors(value),
                    key=lambda item: list(item.absolute_path))
    if errors:
        first = errors[0]
        where = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise ValueError("%s: %s: %s" % (path, where, first.message))
    return value


def build_prompt(decision: Path, control: Path) -> str:
    return (
        "You are the configured K3 plan_expander. Read exactly these two JSON files:\n"
        "DecisionSkeleton: %s\nControlPacket: %s\n"
        "Do not read the root transcript or unrelated files. Preserve every user/Sol/"
        "policy decision and constraint. Expand only into independent bounded packets; "
        "put ambiguity in needs_decision. Return exactly one JSON object matching the "
        "provided output schema and nothing else." % (decision, control)
    )


def build_command(codex_bin: str, cwd: Path, decision: Path, control: Path,
                  output: Path, settings: dict | None = None) -> list[str]:
    settings = settings or load_plan_settings(ROOT)
    command = [
        codex_bin, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
        "-C", str(cwd), "-m", str(settings["k3_model"]),
        "-c", "model_reasoning_effort=%s" % settings["k3_reasoning"],
        "-c", "model_context_window=%s" % settings["k3_context_tokens"],
        "-c", "model_auto_compact_token_limit=%s" %
        settings["k3_compaction_tokens"],
        "-c", "mcp_servers.ipybox.enabled=false",
    ]
    if os.name == "nt":
        command += ["-c", "mcp_servers.node_repl.enabled=false"]
    command += ["--output-schema", str(OUTPUT_SCHEMA),
        "--json", "-o", str(output), build_prompt(decision, control),
    ]
    return command


def normalize_plan(raw: dict, decision: dict, control: dict) -> dict:
    """Project a verbose model result onto the strict schema deterministically.

    Missing authority/evidence fields are derived only from the two validated
    inputs and an optional DAG edge list. Core semantic fields are never
    invented: the final schema validator still rejects a missing packet id,
    goal, authorized path list, or acceptance list.
    """
    normalized = {key: raw.get(key) for key in TOP_LEVEL_KEYS}
    edges = ((raw.get("dag") or {}).get("edges") or []) if isinstance(raw.get("dag"), dict) else []
    dependencies: dict[str, list[str]] = {}
    for edge in edges:
        if isinstance(edge, (list, tuple)) and len(edge) == 2:
            dependencies.setdefault(str(edge[1]), []).append(str(edge[0]))
        elif isinstance(edge, dict) and edge.get("from") and edge.get("to"):
            dependencies.setdefault(str(edge["to"]), []).append(str(edge["from"]))
    decision_refs = [str(item["id"]) for item in decision.get("decisions", [])
                     if isinstance(item, dict) and item.get("id")]
    evidence_refs = [str(item) for item in
                     (control.get("evidence_paths") or decision.get("evidence_roots") or [])]
    packets = []
    for item in raw.get("packets") or []:
        if not isinstance(item, dict):
            packets.append(item)
            continue
        packet = {key: item[key] for key in PACKET_KEYS if key in item}
        packet_id = str(item.get("packet_id") or "")
        packet.setdefault("allowed_side_effects", list(decision.get("allowed_side_effects") or []))
        packet.setdefault("dependencies", dependencies.get(packet_id, []))
        packet.setdefault("artifacts", [])
        packet.setdefault("risk_tags", [str(decision.get("risk_class") or "low")])
        packet.setdefault("decision_refs", decision_refs)
        packet.setdefault("evidence_refs", evidence_refs)
        packets.append(packet)
    normalized["packets"] = packets
    normalized["needs_decision"] = raw.get("needs_decision") or []
    return normalized


def materialize_plan(result: dict, root: Path = ROOT) -> dict:
    """Publish validated packets and a topological DAG without overwrites."""
    control_id = str(result["control_packet_id"])
    blocked = {str(pid) for item in result.get("needs_decision") or []
               if isinstance(item, dict)
               for pid in item.get("blocking_packets") or []}
    packet_dir = root / "data" / "packets"
    packet_dir.mkdir(parents=True, exist_ok=True)
    published: list[str] = []
    packets = {str(item["packet_id"]): item for item in result.get("packets") or []}
    for pid, packet in packets.items():
        if pid in blocked:
            continue
        path = packet_dir / (pid + ".json")
        if path.exists():
            existing = load_json(path)
            if existing != packet:
                raise ValueError("refusing to overwrite divergent packet %s" % pid)
        else:
            _atomic_json(path, packet)
        published.append(pid)

    remaining = set(published)
    done: set[str] = set()
    waves: list[list[str]] = []
    while remaining:
        ready = sorted(pid for pid in remaining
                       if set(str(dep) for dep in packets[pid].get(
                           "dependencies", []) if str(dep) in packets) <= done)
        if not ready:
            raise ValueError("plan contains a dependency cycle among %s" %
                             ", ".join(sorted(remaining)))
        waves.append(ready)
        done.update(ready)
        remaining.difference_update(ready)
    plan_dir = root / "data" / "plans" / hashlib.sha256(
        control_id.encode("utf-8")).hexdigest()[:20]
    dag = {"schema": "codex-loop-plan-dag/v2", "control_packet_id": control_id,
           "decision_skeleton_id": result["decision_skeleton_id"],
           "waves": waves, "blocked_packets": sorted(blocked),
           "packet_count": len(published), "ts": time.time()}
    _atomic_json(plan_dir / "dag.json", dag)
    _atomic_json(root / "data" / "plans" / "active.json", {
        "schema": "codex-loop-active-plan/v2", "control_packet_id": control_id,
        "dag": str(plan_dir / "dag.json"), "ts": time.time()})
    if result.get("needs_decision"):
        _atomic_json(root / "data" / "sol_wake" /
                     ("plan_needs_decision_%s.json" % plan_dir.name), {
            "schema": "codex-loop-bounded-adjudication/v2",
            "control_packet_id": control_id,
            "needs_decision": result["needs_decision"],
            "dag": str(plan_dir / "dag.json"), "ts": time.time()})
    return dag


def execute(command: list[str], events: Path, stderr: Path, timeout: float) -> int:
    events.parent.mkdir(parents=True, exist_ok=True)
    stderr.parent.mkdir(parents=True, exist_ok=True)
    with events.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
        proc = subprocess.Popen(command, stdout=out, stderr=err, text=True,
                                start_new_session=(os.name != "nt"))
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait(timeout=5)
            return 124


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def execute_supervised(command: list[str], events: Path, stderr: Path,
                       timeout: float, output: Path, cwd: Path,
                       settings: dict, control_packet_id: str) -> int:
    """Run K3 through the common lifecycle/roster authority."""
    sys.path.insert(0, str(ROOT / "harness"))
    from provider_health import backoff_active
    blocked, health = backoff_active(ROOT, str(settings["k3_model"]))
    if blocked:
        print("plan_pipeline: K3 provider backoff active until %.0f" %
              float(health["backoff_until"]), file=sys.stderr)
        return 75
    supervisor = ROOT / "harness" / "lifecycle_supervisor.py"
    if not supervisor.exists():
        return execute(command, events, stderr, timeout)
    digest = hashlib.sha256(control_packet_id.encode("utf-8")).hexdigest()[:20]
    packet_id = "v2job-plan-" + digest
    run_id = "%s-%s" % (packet_id, uuid.uuid4().hex)
    supervised = [
        sys.executable, str(supervisor), "--data-dir", str(ROOT / "data"),
        "--packet", packet_id, "--run-id", run_id, "--attempt", "0",
        "--task-name", "K3计划扩充 — %s" % control_packet_id,
        "--role", "plan_expander", "--model", str(settings["k3_model"]),
        "--cwd", str(cwd), "--stdout", str(events), "--stderr", str(stderr),
        "--report", str(output), "--timeout", str(timeout), "--grace", "10",
        "--", *command,
    ]
    return subprocess.run(supervised, cwd=str(ROOT)).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="K3 headless plan expansion")
    ap.add_argument("--decision-skeleton", type=Path, required=True)
    ap.add_argument("--control-packet", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--events", type=Path)
    ap.add_argument("--stderr", type=Path)
    ap.add_argument("--cwd", type=Path, default=ROOT)
    ap.add_argument("--codex-bin", default="codex")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--no-materialize", action="store_true")
    args = ap.parse_args(argv)

    decision = args.decision_skeleton.resolve()
    control = args.control_packet.resolve()
    output = args.output.resolve()
    validate(decision, DECISION_SCHEMA)
    control_doc = load_json(control)
    if not isinstance(control_doc.get("control_packet_id"), str) or not control_doc["control_packet_id"]:
        raise ValueError("%s: missing non-empty control_packet_id" % control)
    raw_output = output.with_suffix(output.suffix + ".raw.json")
    settings = load_plan_settings(ROOT)
    command = build_command(args.codex_bin, args.cwd.resolve(), decision, control,
                            raw_output, settings)
    if not args.execute:
        print(json.dumps({"mode": "dry-run", "command": command}, ensure_ascii=False))
        return 0

    events = (args.events or output.with_suffix(output.suffix + ".events.jsonl")).resolve()
    stderr = (args.stderr or output.with_suffix(output.suffix + ".stderr.log")).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    rc = execute_supervised(command, events, stderr, args.timeout, raw_output,
                            args.cwd.resolve(), settings,
                            str(control_doc["control_packet_id"]))
    if rc != 0:
        print("plan_pipeline: K3 failed rc=%d events=%s stderr=%s" %
              (rc, events, stderr), file=sys.stderr)
        return rc
    raw_result = load_json(raw_output)
    result = normalize_plan(raw_result, validate(decision, DECISION_SCHEMA), control_doc)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    result = validate(output, OUTPUT_SCHEMA)
    if result.get("control_packet_id") != control_doc["control_packet_id"]:
        raise ValueError("output control_packet_id does not match input")
    if result.get("decision_skeleton_id") != validate(decision, DECISION_SCHEMA)["decision_skeleton_id"]:
        raise ValueError("output decision_skeleton_id does not match input")
    if not args.no_materialize:
        materialize_plan(result, ROOT)
    print("plan_pipeline: validated %d packet(s) -> %s" %
          (len(result.get("packets") or []), output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
