#!/usr/bin/env python3
"""Bridge the proven rollout parser into the v2 token ledger/report.

One filesystem scan feeds both reports.  The v2 ledger remains append-only and
idempotent, while the legacy-compatible report is derived from the exact same
records for shadow comparison.  No prompt or transcript content is persisted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("LOOP_ROOT", HERE.parent)).resolve()
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "harness"))

import model_token_share as legacy  # noqa: E402
from lifecycle_supervisor import locked  # noqa: E402
from l2_consumer import load_policy  # noqa: E402
from model_token_share_v2 import LedgerRecord, MeterV2  # noqa: E402


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _legacy_report(records: list[dict[str, Any]], now: float,
                   sessions_dirs: list[Path], f2_start: float) -> dict[str, Any]:
    def window(seconds: float | None) -> dict[str, Any]:
        rows = records if seconds is None else [
            row for row in records if row.get("ts") is not None
            and float(row["ts"]) >= now - seconds]
        return legacy.shares(rows)

    return {
        "schema": "codex-loop-token-share/v1-bridge",
        "generated_at": now,
        "sessions_dirs": [str(path) for path in sessions_dirs],
        "f2_start": f2_start,
        "records": len(records),
        "thresholds": {"warning": legacy.WARN, "block": legacy.BLOCK,
                       "primary_metric": "share_effective"},
        "windows": {
            "cumulative_since_f2": window(None),
            "rolling_5h": window(5 * 3600),
            "rolling_24h": window(24 * 3600),
            "rolling_7d": window(7 * 24 * 3600),
        },
        "bridge": {"single_scan": True,
                   "v2_report": "data/usage/model_token_share_v2.json"},
    }


def _append_v2(meter: MeterV2, records: list[dict[str, Any]]) -> tuple[int, int]:
    added = duplicate = 0
    pending: list[LedgerRecord] = []
    existing = meter.ledger._existing_keys()
    for row in records:
        usage = row.get("usage") or {}
        raw_total = int(usage.get("total_tokens", 0) or 0)
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        # Codex last_token_usage.total_tokens is the billed total; reasoning is
        # already represented there.  Projecting the remainder into output
        # keeps v1/v2 effective denominators byte-for-byte comparable.
        output_tokens = max(0, raw_total - input_tokens)
        identity = json.dumps({
            "session": row.get("session"), "agent": row.get("agent_id"),
            "model": row.get("model"), "ts": row.get("ts"),
            "source": row.get("source"), "usage": usage,
        }, sort_keys=True, separators=(",", ":"))
        step_id = "rollout:" + hashlib.sha256(
            identity.encode("utf-8")).hexdigest()[:24]
        root_session = str(row.get("parent_session_id") or
                           row.get("session") or "unknown")
        record = LedgerRecord(
            ts=float(row.get("ts") or 0.0),
            task_id=root_session,
            root_session_id=root_session,
            agent_id=str(row.get("agent_id") or row.get("session") or "unknown"),
            role=str(row.get("bucket") or "unknown"),
            model=str(row.get("model") or "unknown"),
            step_id=step_id,
            input_tokens=input_tokens,
            cached_input_tokens=int(usage.get("cached_input_tokens", 0) or 0),
            output_tokens=output_tokens,
            reasoning_output_tokens=0,
            token_class=("maintenance" if row.get("bucket") == "maintenance"
                         else "production"),
        )
        key = record.idem_key()
        if key in existing:
            duplicate += 1
        else:
            existing.add(key)
            pending.append(record)
            added += 1
    if pending:
        meter.ledger.path.parent.mkdir(parents=True, exist_ok=True)
        with meter.ledger.path.open("a", encoding="utf-8") as handle:
            for record in pending:
                handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return added, duplicate


def refresh(root: Path, sessions_dir: Path | list[Path] | tuple[Path, ...], *, force: bool = False,
            now: float | None = None, f2_start: float = 0.0) -> dict[str, Any]:
    now = time.time() if now is None else now
    usage_dir = root / "data" / "usage"
    state_path = usage_dir / "meter_bridge_state.json"
    policy = load_policy(root / "config" / "orchestration_policy_v2.toml")
    debounce = float(policy.get("tokens", {}).get("refresh_debounce_s", 60))
    lock_path = usage_dir / ".meter_bridge.lock"
    with locked(lock_path):
        try:
            prior = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prior = {}
        if (not force and now - float(prior.get("generated_at", 0)) < debounce):
            return {"status": "DEBOUNCED", **prior}
        sessions_dirs = ([sessions_dir] if isinstance(sessions_dir, Path)
                         else list(sessions_dir))
        sessions_dirs = [Path(path).resolve() for path in sessions_dirs]
        missing = [path for path in sessions_dirs if not path.is_dir()]
        if missing:
            raise FileNotFoundError("sessions directory not found: %s" % missing[0])
        roles, waves = legacy.load_role_maps(str(root / "data" / "events.ndjson"))
        effective_start = f2_start if f2_start > 0 else now - 7 * 24 * 3600
        records: list[dict[str, Any]] = []
        for source in sessions_dirs:
            records.extend(row for row in legacy.collect(
                str(source), roles, waves, since_mtime=effective_start)
                if row.get("ts") is None or float(row["ts"]) >= effective_start)
        meter = MeterV2(root, policy, clock=lambda: now)
        added, duplicate = _append_v2(meter, records)
        v2 = meter.refresh(force=True) or meter.read_fresh_report()
        v1 = _legacy_report(records, now, sessions_dirs, effective_start)
        atomic_json(usage_dir / "model_token_share.json", v1)
        old_5h = v1["windows"]["rolling_5h"].get("share_effective")
        new_5h = (v2.get("windows", {}).get("rolling_5h", {})
                  .get("sol_share_effective"))
        diff = None if old_5h is None or new_5h is None else round(new_5h - old_5h, 8)
        comparison = {
            "schema": "codex-loop-meter-shadow/v2",
            "generated_at": now, "single_scan_records": len(records),
            "legacy_sol_share_5h": old_5h, "v2_sol_share_5h": new_5h,
            "absolute_diff": None if diff is None else abs(diff),
            # v1 intentionally rounds shares to four decimals while v2 keeps
            # six.  One v1 rounding unit is equivalence, not metering drift.
            "status": ("NO_DATA" if diff is None else
                       "PASS" if abs(diff) <= 0.0001 else "DIFF"),
        }
        atomic_json(usage_dir / "meter_shadow_comparison.json", comparison)
        state = {"schema": "codex-loop-meter-bridge/v2",
                 "generated_at": now, "status": "OK", "records": len(records),
                 "v2_rows_added": added, "v2_rows_duplicate": duplicate,
                 "comparison": comparison["status"],
                 "effective_start": effective_start,
                 "session_sources": [str(path) for path in sessions_dirs]}
        atomic_json(state_path, state)
        return state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="single-scan rollout -> v1/v2 meter bridge")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--sessions-dir", type=Path, action="append", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--now", type=float)
    ap.add_argument("--f2-start", type=float, default=0.0)
    args = ap.parse_args(argv)
    try:
        session_sources = args.sessions_dir or [Path(
            os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"]
        result = refresh(args.root.resolve(), session_sources,
                         force=args.force, now=args.now, f2_start=args.f2_start)
    except Exception as exc:
        print("model_token_share_bridge: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
