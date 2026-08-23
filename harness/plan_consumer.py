#!/usr/bin/env python3
"""Exactly-once DecisionSkeleton -> K3 plan-expansion inbox consumer.

The root planning turn writes a bounded request under ``data/plans/inbox``;
this zero-model consumer passes only the two validated file handles to the
existing lifecycle-supervised ``plan_pipeline.py``.  It never inherits or
reconstructs the root transcript.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("LOOP_ROOT", HERE.parent)).resolve()
sys.path.insert(0, str(HERE))

from orchestration_common import (  # noqa: E402
    LoopPaths, OrchestrationPolicy, append_ndjson, atomic_write_json,
)

SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")


@dataclass
class PlanDrainStats:
    mode: str
    discovered: int = 0
    observed: int = 0
    claimed: int = 0
    completed: int = 0
    failed: int = 0
    already_terminal: int = 0
    already_claimed: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["errors"] = list(self.errors or [])
        return value


class PlanConsumer:
    def __init__(self, root: Path | str = ROOT, *,
                 runner: Callable[..., subprocess.CompletedProcess] | None = None,
                 clock: Callable[[], float] = time.time):
        self.root = Path(root).resolve()
        self.paths = LoopPaths.resolve(self.root)
        self.policy = OrchestrationPolicy.load(self.paths)
        self.mode = self.policy.routing_mode()
        self.runner = runner or subprocess.run
        self.clock = clock
        self.base = self.root / "data" / "plans"
        self.inbox = self.base / "inbox"
        self.claims = self.base / "claims"
        self.completions = self.base / "completions"
        self.reaped = self.base / "reaped"
        self.results = self.base / "results"
        self.shadow = self.base / "shadow"
        for path in (self.inbox, self.claims, self.completions,
                     self.reaped, self.results, self.shadow):
            path.mkdir(parents=True, exist_ok=True)

    def _safe_id(self, value: Any, field: str) -> str:
        text = str(value or "")
        if not SAFE_ID.fullmatch(text):
            raise ValueError("%s is not a safe id: %r" % (field, value))
        return text

    def _inside_root_file(self, value: Any, field: str) -> Path:
        raw = Path(str(value or ""))
        path = (self.root / raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("%s escapes LOOP root: %s" % (field, path)) from exc
        if not path.is_file():
            raise ValueError("%s is not a file: %s" % (field, path))
        return path

    def _load_request(self, path: Path) -> tuple[dict[str, Any], Path, Path]:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("request unreadable: %s" % exc) from exc
        if not isinstance(doc, dict):
            raise ValueError("request top level must be an object")
        request_id = self._safe_id(doc.get("request_id"), "request_id")
        if request_id != path.stem:
            raise ValueError("request_id must equal inbox filename stem")
        self._safe_id(doc.get("packet_id"), "packet_id")
        decision = self._inside_root_file(doc.get("decision_skeleton"),
                                          "decision_skeleton")
        control = self._inside_root_file(doc.get("control_packet"),
                                         "control_packet")
        try:
            decision_doc = json.loads(decision.read_text(encoding="utf-8"))
            control_doc = json.loads(control.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("plan input unreadable: %s" % exc) from exc
        control_id = str(control_doc.get("control_packet_id") or "")
        if not control_id or decision_doc.get("control_packet_id") != control_id:
            raise ValueError("decision/control identity mismatch")
        timeout = float(doc.get("timeout_seconds", 600))
        if timeout < 10 or timeout > 1800:
            raise ValueError("timeout_seconds must be in [10, 1800]")
        doc["timeout_seconds"] = timeout
        return doc, decision, control

    def _claim(self, request_id: str, request_path: Path) -> Path | None:
        claim = self.claims / (request_id + ".json")
        if claim.exists() and not (self.completions / claim.name).exists():
            try:
                if self.clock() - claim.stat().st_mtime > 1900:
                    os.replace(claim, self.reaped / (
                        "%s.%d.json" % (request_id, int(self.clock()))))
            except OSError:
                pass
        payload = json.dumps({
            "request_id": request_id, "request_path": str(request_path),
            "claimer_pid": os.getpid(), "claimed_at": self.clock(),
        }, ensure_ascii=False).encode("utf-8")
        try:
            fd = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return None
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return claim

    def _event(self, packet_id: str, event: str,
               detail: dict[str, Any]) -> None:
        append_ndjson(self.paths.events, {
            "ts": self.clock(), "packet_id": packet_id,
            "event": event, "detail": detail,
        }, lock_path=self.paths.events_lock)

    def _finish(self, request_id: str, value: dict[str, Any]) -> None:
        atomic_write_json(self.completions / (request_id + ".json"), value)
        try:
            (self.claims / (request_id + ".json")).unlink()
        except FileNotFoundError:
            pass

    def drain(self) -> PlanDrainStats:
        stats = PlanDrainStats(mode=self.mode, errors=[])
        if self.mode == "cold_start":
            return stats
        for request_path in sorted(self.inbox.glob("*.json")):
            stats.discovered += 1
            request_id = request_path.stem
            completion = self.completions / (request_id + ".json")
            if completion.exists():
                stats.already_terminal += 1
                continue
            if self.mode == "shadow":
                shadow = self.shadow / (request_id + ".json")
                if not shadow.exists():
                    try:
                        doc, decision, control = self._load_request(request_path)
                        atomic_write_json(shadow, {
                            "schema": "codex-loop-plan-shadow/v1",
                            "request_id": request_id,
                            "packet_id": doc["packet_id"],
                            "decision_skeleton": str(decision),
                            "control_packet": str(control),
                            "would_expand": True, "ts": self.clock(),
                        })
                        stats.observed += 1
                    except Exception as exc:
                        stats.errors.append("%s: %s" % (request_id, exc))
                continue
            try:
                doc, decision, control = self._load_request(request_path)
            except Exception as exc:
                stats.failed += 1
                stats.errors.append("%s: %s" % (request_id, exc))
                atomic_write_json(completion, {
                    "request_id": request_id, "status": "rejected",
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "ts": self.clock(),
                })
                continue
            claim = self._claim(request_id, request_path)
            if claim is None:
                stats.already_claimed += 1
                continue
            stats.claimed += 1
            packet_id = str(doc["packet_id"])
            output_dir = self.results / request_id
            output = output_dir / "plan.json"
            events = output_dir / "events.jsonl"
            stderr = output_dir / "stderr.log"
            self._event(packet_id, "skeleton_ready", {
                "request_id": request_id,
                "decision_skeleton": str(decision),
                "control_packet": str(control),
            })
            command = [
                sys.executable,
                str(self.root / "harness" / "orchestration" / "plan_pipeline.py"),
                "--decision-skeleton", str(decision),
                "--control-packet", str(control),
                "--output", str(output),
                "--events", str(events), "--stderr", str(stderr),
                "--cwd", str(self.root),
                "--timeout", str(doc["timeout_seconds"]), "--execute",
            ]
            env = dict(os.environ)
            env["LOOP_ROOT"] = str(self.root)
            try:
                result = self.runner(command, cwd=str(self.root), env=env,
                                     timeout=doc["timeout_seconds"] + 30,
                                     check=False)
                rc = int(result.returncode)
            except Exception as exc:
                rc = 125
                stats.errors.append("%s: runner %s: %s" %
                                    (request_id, type(exc).__name__, exc))
            if rc == 0 and output.is_file():
                self._event(packet_id, "expansion_valid", {
                    "request_id": request_id, "plan": str(output)})
                self._finish(request_id, {
                    "request_id": request_id, "packet_id": packet_id,
                    "status": "completed", "plan": str(output),
                    "returncode": 0, "ts": self.clock(),
                })
                stats.completed += 1
            else:
                self._event(packet_id, "expansion_invalid", {
                    "request_id": request_id, "returncode": rc,
                    "stderr": str(stderr)})
                self._finish(request_id, {
                    "request_id": request_id, "packet_id": packet_id,
                    "status": "failed", "returncode": rc,
                    "stderr": str(stderr), "ts": self.clock(),
                })
                stats.failed += 1
        return stats


def run_once(root: Path | str = ROOT) -> tuple[int, dict[str, Any]]:
    stats = PlanConsumer(root).drain()
    value = stats.to_dict()
    return (1 if stats.errors else 0), value


if __name__ == "__main__":
    rc, value = run_once(ROOT)
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(rc)
