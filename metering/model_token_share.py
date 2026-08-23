#!/usr/bin/env python3
# ============================================================================
# model_token_share.py — Sol token-share aggregator (P1-2, pure offline)
# Purpose : turn the 20%–25% Sol-share target from a documentation goal into
#           a measured, budget-checked number. Reads Codex's OWN rollout
#           JSONL persistence (~/.codex/sessions/**/rollout-*.jsonl), parses
#           token_count events (last_token_usage / total_token_usage),
#           attributes tokens to buckets (sol / worker / verifier / reviewer
#           / duty_officer / maintenance) by session, model, and role, and
#           computes three share metrics per window:
#             share_total     = sol_tokens / total_tokens
#             share_effective = (sol - sol_cached_in) / (total - cached_in)
#                               <- PRIMARY control metric (cache reads are
#                                  cheap; ruling 2 cost-equivalent direction)
#             share_output    = sol_output / total_output
#           Windows: per task (= per session), per wave, rolling 5h (primary),
#           rolling 24h, rolling 7d, cumulative since F2 start. Budget feedback:
#           share_effective > 20% -> WARNING line; > 25% -> BLOCK line
#           (recommend refusing new non-planning/adjudication Sol work).
#           Reviewer is metered in its OWN bucket (never hides in sol);
#           install/acceptance sessions (smoke prompts) go to maintenance
#           and are EXCLUDED from the production share denominator.
# Input   : --sessions-dir (default $CODEX_HOME/sessions), optional --events
#           data/events.ndjson (SubagentStart role attribution + packet->wave
#           map), --f2-start epoch, --now epoch (test override).
# Output  : JSON to --output (default data/usage/model_token_share.json);
#           WARNING/BLOCK lines on stdout. Exit 0 = OK/WARNING, 1 = BLOCK
#           in any time window (fail-visible), 2 = usage error.
# Lines   : ~150 (excluding this header)
# Token ownership: zero token — pure offline disk pipeline.
# ============================================================================
import argparse, glob, json, os, re, sys, time, tomllib

ROOT = os.environ.get("LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def configured_model_buckets(root):
    """Load active model attribution from public configuration, fail-soft."""
    buckets = {}
    try:
        with open(os.path.join(root, "config", "model_profiles.toml"), "rb") as handle:
            profiles = tomllib.load(handle)
        for profile in (profiles.get("profiles") or {}).values():
            if profile.get("execution_model"):
                buckets[str(profile["execution_model"])] = "worker"
            if profile.get("review_model"):
                buckets[str(profile["review_model"])] = "verifier"
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    try:
        with open(os.path.join(root, "config", "orchestration_policy_v2.toml"), "rb") as handle:
            models = tomllib.load(handle).get("models") or {}
        if models.get("v4_model"):
            buckets[str(models["v4_model"])] = "worker"
        if models.get("k3_model"):
            buckets[str(models["k3_model"])] = "verifier"
        for alias in models.get("execution_aliases", []):
            buckets[str(alias)] = "worker"
        for alias in models.get("review_aliases", []):
            buckets[str(alias)] = "verifier"
        if models.get("sol_model"):
            buckets[str(models["sol_model"])] = "sol"
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return buckets


MODEL_BUCKET = configured_model_buckets(ROOT)
SOL_MODELS = {model for model, bucket in MODEL_BUCKET.items() if bucket == "sol"}
SOL_BUCKET, WARN, BLOCK = "sol", 0.20, 0.25
MAINTENANCE_MARKERS = (
    "smoke: reply exactly OK",
    "loop-install",
    "Stability probe:",
    "K3_OK",
    "LOOP_MAINTENANCE",
)
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens",
              "reasoning_output_tokens", "total_tokens")


def walk(obj, key):
    """Yield every dict value under `key` anywhere in a nested structure."""
    if isinstance(obj, dict):
        if key in obj:
            yield obj[key]
        for v in obj.values():
            yield from walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v, key)


def parse_ts(line_obj):
    ts = line_obj.get("timestamp") or line_obj.get("ts")
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            import datetime
            return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def load_role_maps(events_path):
    """events.ndjson -> (agent/session identity->role, packet->wave)."""
    roles, waves = {}, {}
    if not events_path or not os.path.exists(events_path):
        return roles, waves
    for line in open(events_path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") in ("SubagentStart", "SubagentStartRecovered"):
            identity = e.get("agent_id") or e.get("session_id")
            if identity:
                roles[identity] = (e.get("agent_role") or "").lower()
        if e.get("event") == "dispatched" and "wave" in (e.get("detail") or {}):
            waves[e.get("packet_id")] = e["detail"]["wave"]
    return roles, waves


def collect(sessions_dir, roles, waves, since_mtime=None):
    """Per-session token records: [{session, bucket, wave, ts, usage{}}]."""
    recs = []
    seen_cumulative = set()
    pat = os.path.join(sessions_dir, "**", "rollout-*.jsonl")
    for path in sorted(glob.glob(pat, recursive=True)):
        if since_mtime is not None:
            try:
                if os.path.getmtime(path) < float(since_mtime):
                    continue
            except OSError:
                continue
        filename_sid = re.sub(r"^rollout-", "", os.path.basename(path)).rsplit(".", 1)[0]
        wave = None
        model, effort, sess_recs, meta = None, None, [], {}
        seen_turn_context = False
        turn_maintenance = False
        for line in open(path, encoding="utf-8", errors="replace"):
            if wave is None:
                match = re.search(r"data/reports/([\w.-]+)/report\.json", line)
                if match:
                    wave = waves.get(match.group(1))
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                meta = obj["payload"]
            if obj.get("type") == "turn_context" and isinstance(obj.get("payload"), dict):
                turn = obj["payload"]
                model = turn.get("model") or model
                effort = turn.get("effort") or effort
                seen_turn_context = True
            elif model is None:
                for mv in walk(obj, "model"):
                    if isinstance(mv, str) and mv:
                        model = mv
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            if payload.get("type") == "user_message":
                message = payload.get("message")
                if not isinstance(message, str):
                    message = json.dumps(message, ensure_ascii=False) if message is not None else ""
                turn_maintenance = any(marker in message for marker in MAINTENANCE_MARKERS)
            usage_items = list(walk(obj, "last_token_usage"))
            for info in usage_items:
                if isinstance(info, dict):
                    usage = {k: int(info.get(k, 0) or 0) for k in USAGE_KEYS}
                    if not usage["total_tokens"]:
                        usage["total_tokens"] = (usage["input_tokens"]
                                                 + usage["output_tokens"])
                    # Child rollouts replay parent token_count snapshots before
                    # their first own turn_context. Component-zero token_count
                    # records are bookkeeping/phantom repeats, not new usage.
                    if (meta.get("thread_source") == "subagent"
                            and not seen_turn_context):
                        continue
                    if (usage["total_tokens"] > 0
                            and not any(usage[k] for k in USAGE_KEYS
                                        if k != "total_tokens")):
                        continue
                    cumulative = next((value for value in walk(obj, "total_token_usage")
                                       if isinstance(value, dict)), None)
                    cumulative_key = (tuple(int(cumulative.get(k, 0) or 0)
                                            for k in USAGE_KEYS)
                                      if cumulative else None)
                    sess_recs.append({"ts": parse_ts(obj), "model": model,
                                      "effort": effort, "usage": usage,
                                      "maintenance": turn_maintenance,
                                      "cumulative_key": cumulative_key,
                                      "source": os.path.basename(path)})
            if usage_items:
                # Maintenance is a property of the just-completed turn, not of
                # every historical turn in the rollout file.
                turn_maintenance = False
        agent_id = meta.get("id") or meta.get("agent_id")
        parent_session_id = meta.get("session_id")
        is_subagent = meta.get("thread_source") == "subagent"
        sid = (agent_id if is_subagent else parent_session_id or agent_id) or filename_sid
        role = (roles.get(agent_id) or roles.get(sid)
                or (meta.get("agent_role") or "")).lower()
        for r in sess_recs:
            # Resumed/compacted rollouts can replay token_count snapshots from
            # the same logical session. total_token_usage is monotonic for that
            # session, so an identical cumulative snapshot is the same billed
            # event, not another turn. Keep first occurrence only.
            if r["cumulative_key"] is not None:
                replay_key = (sid, r["model"], r["cumulative_key"])
                if replay_key in seen_cumulative:
                    continue
                seen_cumulative.add(replay_key)
            if r.pop("maintenance", False):
                b = "maintenance"
            elif role in ("worker", "executor", "scout", "duty_officer",
                          "plan_expander", "verifier", "reviewer"):
                b = "duty_officer" if role == "duty_officer" else role
            else:
                b = MODEL_BUCKET.get(r["model"] or "", "unknown")
                if b == SOL_BUCKET and role == "reviewer":
                    b = "reviewer"
            r["session"], r["agent_id"] = sid, agent_id
            r["parent_session_id"] = parent_session_id if is_subagent else None
            r["bucket"], r["wave"] = b, wave
            r.pop("cumulative_key", None)
            recs.append(r)
    return recs


def shares(recs):
    """Aggregate one record set -> bucket totals + the three share metrics.
    maintenance is excluded from the production denominator (still reported)."""
    buckets, models = {}, {}
    for r in recs:
        b = buckets.setdefault(r["bucket"], dict.fromkeys(USAGE_KEYS, 0))
        for k in USAGE_KEYS:
            b[k] += r["usage"][k]
        if r["bucket"] != "maintenance":
            model = r.get("model") or "unknown"
            model_usage = models.setdefault(model, dict.fromkeys(USAGE_KEYS, 0))
            for k in USAGE_KEYS:
                model_usage[k] += r["usage"][k]
    prod = {k: sum(v[k] for b, v in buckets.items() if b != "maintenance")
            for k in USAGE_KEYS}
    sol_records = [r for r in recs
                   if ((r.get("model") or "") in SOL_MODELS
                       and r.get("bucket") != "maintenance")]
    sol = {k: sum(r["usage"][k] for r in sol_records) for k in USAGE_KEYS}
    components = ["sol"]
    if any(r.get("bucket") == "reviewer" for r in sol_records):
        components.append("reviewer")
    def ratio(num, den):
        return round(num / den, 4) if den > 0 else None
    model_shares = {}
    for model, usage in models.items():
        model_shares[model] = {**usage,
            "share_total": ratio(usage["total_tokens"], prod["total_tokens"]),
            "share_effective": ratio(usage["total_tokens"] - usage["cached_input_tokens"],
                                     prod["total_tokens"] - prod["cached_input_tokens"]),
            "share_output": ratio(usage["output_tokens"], prod["output_tokens"])}
    eff = ratio(sol["total_tokens"] - sol["cached_input_tokens"],
                prod["total_tokens"] - prod["cached_input_tokens"])
    out = {"buckets": buckets,
           "models": model_shares,
           "sol_kpi": sol,
           "sol_kpi_components": components,
           "share_total": ratio(sol["total_tokens"], prod["total_tokens"]),
           "share_effective": eff,
           "share_output": ratio(sol["output_tokens"], prod["output_tokens"])}
    out["status"] = ("NO_DATA" if eff is None else
                     "BLOCK" if eff > BLOCK else
                     "WARNING" if eff > WARN else "OK")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sol token-share aggregator (offline)")
    ap.add_argument("--sessions-dir", default=os.path.join(
        os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")), "sessions"))
    ap.add_argument("--events", default=os.path.join(ROOT, "data", "events.ndjson"))
    ap.add_argument("--output", default=os.path.join(ROOT, "data", "usage",
                                                     "model_token_share.json"))
    ap.add_argument("--f2-start", type=float, default=0.0, help="epoch of F2 enable")
    ap.add_argument("--now", type=float, default=None, help="epoch override (tests)")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.sessions_dir):
        print("model_token_share: sessions dir not found: %s" % a.sessions_dir,
              file=sys.stderr)
        return 2
    now = a.now if a.now is not None else time.time()
    roles, waves = load_role_maps(a.events)
    recs = [r for r in collect(a.sessions_dir, roles, waves)
            if r["ts"] is None or r["ts"] >= a.f2_start]
    def window(pred):
        return shares([r for r in recs if pred(r)])
    def largest(rows, limit=20):
        return [
            {"session": r["session"], "agent_id": r.get("agent_id"),
             "bucket": r["bucket"], "model": r.get("model"),
             "ts": r.get("ts"), "source": r.get("source"),
             "usage": r["usage"]}
            for r in sorted(rows, key=lambda row: row["usage"]["total_tokens"],
                            reverse=True)[:limit]
        ]
    def pressure(rows):
        production = [r for r in rows if r["bucket"] != "maintenance"]
        return {
            "max_input_tokens": max((r["usage"]["input_tokens"] for r in production),
                                    default=0),
            "max_total_tokens": max((r["usage"]["total_tokens"] for r in production),
                                    default=0),
            "records_input_over_100k": sum(r["usage"]["input_tokens"] > 100_000
                                           for r in production),
            "records_input_over_200k": sum(r["usage"]["input_tokens"] > 200_000
                                           for r in production),
        }
    rolling_5h_records = [r for r in recs
                          if r["ts"] is not None and r["ts"] >= now - 5 * 3600]
    rolling_24h_records = [r for r in recs
                           if r["ts"] is not None and r["ts"] >= now - 86400]
    rolling_7d_records = [r for r in recs
                          if r["ts"] is not None and r["ts"] >= now - 7 * 86400]
    report = {
        "generated_at": now, "sessions_dir": a.sessions_dir,
        "f2_start": a.f2_start, "records": len(recs),
        "thresholds": {"warning": WARN, "block": BLOCK,
                       "primary_metric": "share_effective"},
        "windows": {
            "cumulative_since_f2": window(lambda r: True),
            "rolling_5h": window(lambda r: r["ts"] is not None and
                                  r["ts"] >= now - 5 * 3600),
            "rolling_24h": window(lambda r: r["ts"] is not None and r["ts"] >= now - 86400),
            "rolling_7d": window(lambda r: r["ts"] is not None and r["ts"] >= now - 7 * 86400),
        },
        "per_task": {s: shares([r for r in recs
                                 if (r.get("parent_session_id") or r["session"]) == s])
                     for s in sorted({r.get("parent_session_id") or r["session"]
                                      for r in recs})},
        "per_wave": {str(w): shares([r for r in recs if r["wave"] == w])
                      for w in sorted({r["wave"] for r in recs
                                       if r["wave"] is not None})},
        "largest_records": largest(recs),
        "largest_records_by_window": {
            "rolling_5h": largest(rolling_5h_records),
            "rolling_24h": largest(rolling_24h_records),
            "rolling_7d": largest(rolling_7d_records),
        },
        "context_pressure": {
            "cumulative_since_f2": pressure(recs),
            "rolling_5h": pressure(rolling_5h_records),
            "rolling_24h": pressure(rolling_24h_records),
            "rolling_7d": pressure(rolling_7d_records),
        },
    }
    os.makedirs(os.path.dirname(a.output) or ".", exist_ok=True)
    json.dump(report, open(a.output, "w", encoding="utf-8"), indent=1)
    blocked = False
    for name, w in report["windows"].items():
        if w["status"] == "WARNING":
            print("WARNING %s: sol share_effective=%s exceeds %.0f%% budget"
                  % (name, w["share_effective"], WARN * 100))
        elif w["status"] == "BLOCK":
            blocked = True
            print("BLOCK %s: sol share_effective=%s exceeds %.0f%% hard cap — "
                  "refuse new non-planning/adjudication Sol work"
                  % (name, w["share_effective"], BLOCK * 100))
    print("model_token_share: %d records -> %s" % (len(recs), a.output))
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
