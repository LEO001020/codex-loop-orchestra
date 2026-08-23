#!/usr/bin/env python3
# ============================================================================
# e0_annotate.py — E0 instrumentation: sessions JSONL per-turn annotation
# Purpose : read Codex session rollout JSONL files ($CODEX_HOME/sessions/,
#           default ~/.codex/sessions/) and annotate every turn with:
#             - trigger_event_type (planning/adjudication/waiting/polling/
#               tallying/retry_decision/state_recap/other)
#             - new_input = total_input - cached ; cached ; output ; reasoning
#             - mechanical_or_semantic tag
#             - T1-T10 task-type bucket
#             - meter_bucket (sol / reviewer / free_side)  [patch caliber 2]
#             - sol_phase pin: planning vs adjudication vs final_review
#               [patch caliber 1 — Sol final review granularity pin]
#             - cost_equivalent (Sol-billed only; free side = 0 by §2 Ruling 2)
#           Cost-equivalent formula (spec §2 Ruling 2):
#             cache_read*0.1 + output*5 + reasoning*5 + cache_write*1.25
#           cache_write is estimated as new_input (uncached input is written
#           to cache at the 1.25x weighted estimate).
# Input   : --sessions-dir (JSONL rollout files, recursive; tolerates the
#           native Codex event_msg/token_count schema and a flat fallback
#           schema; malformed lines are counted and skipped, never fatal)
# Output  : --output annotated JSONL (one line per turn) +
#           --summary JSON (per-task totals, per-bucket totals, trigger
#           histogram, tokens-per-task north star, free-side call counts as
#           an observation metric — NOT a cost item)
# Exit    : 0 ok / 1 no session files found / 2 usage error
# Lines   : 217 (header included)
# Token ownership: zero token — pure offline disk pipeline.
# ============================================================================
import argparse, json, os, re, sys
from pathlib import Path

# --- classification tables ---------------------------------------------------
BUCKETS = {  # T1-T10 task-type buckets (spec E0 row)
    "T1": "planning", "T2": "dispatch", "T3": "execution", "T4": "acceptance",
    "T5": "retry", "T6": "duty_review", "T7": "merge", "T8": "wave_finale",
    "T9": "release_gate", "T10": "adjudication",
}
# (regex, trigger_event_type, bucket) — first hit wins; order = specificity.
RULES = [
    (r"release gate|final review|reviewpacket|reviewer verdict|human-?triggered merge", "planning", "T9"),
    (r"wave finale|wave_summary|wave.?done|finale synthesized", "tallying", "T8"),
    (r"dead.?letter|sol_adjudicate|adjudicat|arbitrat|merge conflict|conflict pointer|l3 escalat", "adjudication", "T10"),
    (r"duty officer|duty_review|ruling json", "retry_decision", "T6"),
    (r"\bretry|re-?dispatch|backoff|circuit break|budget exhaust", "retry_decision", "T5"),
    (r"serial merge|rebase|merge queue|worktree pool", "tallying", "T7"),
    (r"acceptance|diffvalidator|min_test_count|oracle|diff subset", "tallying", "T4"),
    (r"spawn|dispatch|spawn_agents_on_csv|batch csv", "polling", "T2"),
    (r"wait(ing)? for|poll|still running|check(ing)? status|any update", "waiting", "T3"),
    (r"tally|count(ing)? report|collect(ing)? report|aggregate result", "tallying", "T8"),
    (r"state recap|where (are we|were we)|progress so far|summariz(e|ing) (the )?state", "state_recap", "T3"),
    (r"\bplan\b|decompos|packet manifest|dag\.json|work packet", "planning", "T1"),
]
MECHANICAL_TRIGGERS = {"waiting", "polling", "tallying", "retry_decision", "state_recap"}
HIGH_TIER = re.compile(r"gpt-5\.6(-sol)?$")      # Sol / Reviewer tier
FINAL_REVIEW = re.compile(r"release gate|final review|verdict|approve.*merge|releasable", re.I)
ADJUDICATION = re.compile(r"dead.?letter|adjudicat|merge conflict|escalat|arbitrat", re.I)


def classify(text):
    """Return (trigger_event_type, bucket) for one turn's visible text."""
    low = (text or "").lower()
    for pat, trig, bucket in RULES:
        if re.search(pat, low):
            return trig, bucket
    return "other", "T3"  # default: execution work


def cost_equivalent(cached, output, reasoning, new_input):
    """Sol-cost-equivalent units: cache_read 0.1x, output/reasoning 5x,
    cache-write (estimated = new_input) 1.25x weighted."""
    return round(cached * 0.1 + output * 5 + reasoning * 5 + new_input * 1.25, 3)


def meter_bucket_of(role, model):
    """Patch caliber 2: reviewer thread gets its OWN bucket — high tier on the
    free side has non-zero quota and must never hide inside sol or free_side."""
    role = (role or "").lower()
    if role in ("sol", "main", "primary", ""):        # main thread rollouts
        return "sol"
    if role == "reviewer" or (HIGH_TIER.search(model or "") and role not in ("sol", "main")):
        return "reviewer"
    return "free_side"  # executor/worker/scout/verifier/duty_officer/kernel


def iter_turns(path, skipped):
    """Yield per-turn dicts from one rollout JSONL file. Supports the native
    Codex schema (session_meta / turn_context / event_msg token_count) and a
    flat {input_tokens,...} fallback. Malformed lines -> skipped counter."""
    meta = {"model": "", "effort": None, "role": "",
            "session_id": path.stem, "agent_id": None,
            "thread_source": "", "parent_thread_id": None}
    buf, prev_totals = [], None
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        skipped["files"] += 1
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                skipped["lines"] += 1
                continue
            if not isinstance(rec, dict):
                skipped["lines"] += 1
                continue
            p = rec.get("payload", rec)
            t = rec.get("type", "")
            if t == "session_meta" or "session_id" in p and "model" in p:
                meta["session_id"] = p.get("session_id", meta["session_id"])
                meta["agent_id"] = p.get("id", p.get("agent_id", meta["agent_id"]))
                meta["thread_source"] = p.get("thread_source", meta["thread_source"])
                meta["parent_thread_id"] = p.get("parent_thread_id", meta["parent_thread_id"])
                meta["model"] = p.get("model", meta["model"])
                meta["role"] = p.get("agent_role", p.get("agent_type", meta["role"]))
            elif t == "turn_context":
                meta["model"] = p.get("model", meta["model"])
                meta["effort"] = p.get("effort", meta["effort"])
            elif t == "response_item" or t == "event_msg" and p.get("type") in ("agent_message", "user_message", "agent_reasoning"):
                txt = p.get("text") or p.get("message") or ""
                if isinstance(p.get("content"), list):
                    txt += " ".join(c.get("text", "") for c in p["content"] if isinstance(c, dict))
                buf.append(str(txt)[:2000])
            usage = None
            if t == "event_msg" and p.get("type") == "token_count":
                info = p.get("info") or {}
                usage = info.get("last_token_usage")
                if usage is None and info.get("total_token_usage"):
                    tot = info["total_token_usage"]  # derive per-turn delta
                    usage = {k: tot.get(k, 0) - (prev_totals or {}).get(k, 0) for k in tot}
                    prev_totals = tot
            elif "input_tokens" in rec:  # flat fallback schema
                usage = rec
                buf.append(str(rec.get("text", ""))[:2000])
            if usage:
                total_in = int(usage.get("input_tokens", 0) or 0)
                cached = int(usage.get("cached_input_tokens", usage.get("cache_read_input_tokens", 0)) or 0)
                yield {"session_id": meta["session_id"], "agent_id": meta["agent_id"],
                       "parent_thread_id": meta["parent_thread_id"],
                       "thread_source": meta["thread_source"],
                       "model": meta["model"], "effort": meta["effort"],
                       "agent_role": meta["role"], "text": " ".join(buf),
                       "total_input": total_in, "cached": min(cached, total_in),
                       "output": int(usage.get("output_tokens", 0) or 0),
                       "reasoning": int(usage.get("reasoning_output_tokens", usage.get("reasoning_tokens", 0)) or 0)}
                buf = []


def annotate(turn, idx):
    trig, bucket = classify(turn["text"])
    mb = meter_bucket_of(turn["agent_role"], turn["model"])
    new_input = max(turn["total_input"] - turn["cached"], 0)
    ann = {"turn_index": idx, "session_id": turn["session_id"],
           "agent_id": turn.get("agent_id"),
           "parent_thread_id": turn.get("parent_thread_id"),
           "thread_source": turn.get("thread_source"),
           "task_id": turn.get("parent_thread_id") or turn["session_id"],
           "meter_bucket": mb, "model": turn["model"], "effort": turn.get("effort"),
           "agent_role": turn["agent_role"] or ("sol" if mb == "sol" else ""),
           "trigger_event_type": trig, "bucket": bucket, "bucket_name": BUCKETS[bucket],
           "new_input": new_input, "cached": turn["cached"], "output": turn["output"],
           "reasoning": turn["reasoning"],
           "mechanical_or_semantic": "mechanical" if trig in MECHANICAL_TRIGGERS else "semantic"}
    if mb == "sol":  # patch caliber 1 — Sol final review granularity pin
        txt = turn["text"]
        ann["sol_phase"] = ("final_review" if FINAL_REVIEW.search(txt) else
                            "adjudication" if ADJUDICATION.search(txt) else "planning")
        if ann["sol_phase"] == "final_review":
            ann["trigger_event_type"], ann["bucket"], ann["bucket_name"] = "adjudication", "T9", BUCKETS["T9"]
        ann["cost_equivalent"] = cost_equivalent(turn["cached"], turn["output"], turn["reasoning"], new_input)
    else:  # §2 Ruling 2: free side = ZERO cost. Reviewer quota tracked apart.
        ann["cost_equivalent"] = 0.0
        if mb == "reviewer":
            ann["reviewer_quota_equivalent"] = cost_equivalent(turn["cached"], turn["output"], turn["reasoning"], new_input)
    return ann


def main():
    ap = argparse.ArgumentParser(description="E0 sessions JSONL per-turn annotator")
    ap.add_argument("--sessions-dir", default=os.path.join(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")), "sessions"))
    ap.add_argument("--output", required=True, help="annotated JSONL path")
    ap.add_argument("--summary", required=True, help="summary JSON path")
    a = ap.parse_args()
    root = Path(os.path.expanduser(a.sessions_dir))
    files = sorted(root.rglob("*.jsonl")) if root.is_dir() else []
    if not files:
        print(f"e0_annotate: no session JSONL files under {root}", file=sys.stderr)
        return 1
    skipped = {"lines": 0, "files": 0}
    tasks, buckets, trig_hist, free_calls = {}, {}, {}, {}
    n = 0
    with open(a.output, "w", encoding="utf-8") as out:
        for f in files:
            for i, turn in enumerate(iter_turns(f, skipped)):
                ann = annotate(turn, i)
                out.write(json.dumps(ann, ensure_ascii=False) + "\n")
                n += 1
                tk = tasks.setdefault(ann["task_id"], {"turns": 0, "new_input": 0, "cached": 0, "output": 0, "reasoning": 0, "cost_equivalent": 0.0, "reviewer_quota_equivalent": 0.0})
                bk = buckets.setdefault(ann["bucket"], {"name": ann["bucket_name"], "turns": 0, "tokens": 0, "cost_equivalent": 0.0})
                for k in ("new_input", "cached", "output", "reasoning"):
                    tk[k] += ann[k]
                tk["turns"] += 1; tk["cost_equivalent"] = round(tk["cost_equivalent"] + ann["cost_equivalent"], 3)
                tk["reviewer_quota_equivalent"] = round(tk["reviewer_quota_equivalent"] + ann.get("reviewer_quota_equivalent", 0.0), 3)
                bk["turns"] += 1; bk["tokens"] += ann["new_input"] + ann["output"] + ann["reasoning"]
                bk["cost_equivalent"] = round(bk["cost_equivalent"] + ann["cost_equivalent"], 3)
                trig_hist[ann["trigger_event_type"]] = trig_hist.get(ann["trigger_event_type"], 0) + 1
                if ann["meter_bucket"] == "free_side":  # observation metric only
                    free_calls[ann["agent_role"] or "unknown"] = free_calls.get(ann["agent_role"] or "unknown", 0) + 1
    billed = round(sum(t["cost_equivalent"] for t in tasks.values()), 3)
    tokens = sum(t["new_input"] + t["output"] + t["reasoning"] for t in tasks.values())
    summary = {"files_scanned": len(files), "turns_annotated": n, "skipped": skipped,
               "per_task": tasks, "per_bucket": {k: buckets[k] for k in sorted(buckets, key=lambda x: int(x[1:]))},
               "trigger_event_histogram": trig_hist,
               "tokens_per_task": round(tokens / len(tasks), 1) if tasks else 0,  # north star
               "sol_billed_cost_equivalent": billed,
               "reviewer_quota_equivalent": round(sum(t["reviewer_quota_equivalent"] for t in tasks.values()), 3),
               "free_side_call_counts": free_calls,  # observation, NOT a cost item
               "note": "cost = cache_read*0.1 + output*5 + reasoning*5 + cache_write(=new_input)*1.25; free side = 0"}
    with open(a.summary, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    print(f"e0_annotate: {n} turns -> {a.output}; tokens/task={summary['tokens_per_task']}; sol cost-eq={billed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
