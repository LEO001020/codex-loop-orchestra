#!/usr/bin/env python3
# ============================================================================
# retry.py — Table-driven retry / circuit breaker / dead-letter (spec t9,10,14,15)
# Purpose : Classify a packet failure against config/retry_classes.yaml regex
#           table. On-table + budget left -> full-jitter delay + retry_dispatch
#           event (FAILED->RUNNING). Off-table -> duty_review event then DLQ
#           path — NEVER silent. 2 consecutive same-class failures -> duty
#           officer partition. Session circuit breaker halts re-dispatch.
# Input   : --packet <pid> --error-file <path> (or --error "text"); reads
#           data/progress_ledger.json (attempts) + data/.breaker.json.
#           Run-level retry budget and circuit-breaker threshold come from
#           retry_classes.yaml (run_level_retry_budget / session_circuit_
#           breaker); DEFAULTS below are fallbacks for a missing table only.
# Output  : event appended to events.ndjson; DLQ file in data/dead_letters/.
#           Exit 0 = retry scheduled, 4 = duty_review, 5 = dead-letter,
#           6 = circuit open. Prints decision JSON. Deterministic, no LLM.
# Lines   : ~80 (excluding this header)
# ============================================================================
import argparse, json, os, random, re, sys, time

ROOT = os.environ.get("LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
DEFAULTS = {"base": 0.5, "cap": 30.0, "max_attempts": 3,
            "run_budget": 10, "breaker_fails": 5, "breaker_window": 60, "breaker_cooldown": 45}

def append_event(pid, event, detail):
    with open(os.path.join(DATA, "events.ndjson"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "packet_id": pid, "event": event,
                            "detail": detail}, separators=(",", ":")) + "\n")

def load_classes(path):
    """retry_classes.yaml: list of {name, pattern, retryable, max_attempts?, base?, cap?}.
    Returns (classes, run_budget, breaker_fails): the run-level retry budget
    (run_level_retry_budget) and session breaker threshold (session_circuit_
    breaker) are read from the SAME yaml so table and code cannot drift;
    DEFAULTS apply only when the table/keys are missing (fail-visible)."""
    run_budget, breaker_fails = DEFAULTS["run_budget"], DEFAULTS["breaker_fails"]
    if not os.path.exists(path):
        return [], run_budget, breaker_fails  # missing table => everything off-table => fail-visible DLQ path
    try:
        import yaml
        doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
        if isinstance(doc, dict):
            run_budget = int(doc.get("run_level_retry_budget", run_budget))
            breaker_fails = int(doc.get("session_circuit_breaker", breaker_fails))
        classes = doc.get("classes", doc if isinstance(doc, list) else [])
        return classes, run_budget, breaker_fails
    except Exception as e:
        sys.stderr.write("retry_classes.yaml unreadable (%s): treating all as off-table\n" % e)
        return [], run_budget, breaker_fails

def full_jitter(base, cap, attempt):
    return random.uniform(0, min(cap, base * (2 ** attempt)))  # AWS full jitter

def breaker(record_failure, breaker_fails=None):
    """Session circuit breaker: open after N failures inside the window
    (N = retry_classes.yaml session_circuit_breaker, DEFAULTS fallback)."""
    if breaker_fails is None:
        breaker_fails = DEFAULTS["breaker_fails"]
    bp = os.path.join(DATA, ".breaker.json")
    st = json.load(open(bp)) if os.path.exists(bp) else {"fails": [], "open_until": 0}
    now = time.time()
    if record_failure:
        st["fails"] = [t for t in st["fails"] if now - t < DEFAULTS["breaker_window"]] + [now]
        if len(st["fails"]) >= breaker_fails:
            st["open_until"] = now + DEFAULTS["breaker_cooldown"]
    json.dump(st, open(bp, "w"))
    return now < st["open_until"]

def dead_letter(pid, reason, detail, cls):
    dl = {"packet_id": pid, "reason": reason, "class": cls, "detail": detail, "ts": time.time()}
    json.dump(dl, open(os.path.join(DATA, "dead_letters", "%s.json" % pid), "w"), indent=1)
    append_event(pid, "budget_exhausted", {"reason": reason, "class": cls})

def main():
    ap = argparse.ArgumentParser(description="LOOP-F2 table-driven retry")
    ap.add_argument("--packet", required=True)
    ap.add_argument("--error", default=None)
    ap.add_argument("--error-file", default=None)
    ap.add_argument("--classes", default=os.path.join(ROOT, "config", "retry_classes.yaml"))
    args = ap.parse_args()
    err = args.error or (open(args.error_file, encoding="utf-8", errors="replace").read()
                         if args.error_file else "")
    led_p = os.path.join(DATA, "progress_ledger.json")
    led = json.load(open(led_p)) if os.path.exists(led_p) else {"packets": {}}
    pk = led["packets"].setdefault(args.packet, {"state": "FAILED", "history": [], "attempts": 0})
    total_retries = sum(p.get("attempts", 0) for p in led["packets"].values())

    classes, run_budget, breaker_fails = load_classes(args.classes)
    matched = next((c for c in classes
                    if re.search(c.get("pattern", "$^"), err, re.I | re.M)), None)
    decision = {"packet_id": args.packet, "class": matched["name"] if matched else None}

    if breaker(record_failure=True, breaker_fails=breaker_fails):  # circuit open: halt re-dispatch
        decision["action"] = "circuit_open"
        append_event(args.packet, "duty_review", {"why": "circuit_breaker_open"})
        print(json.dumps(decision)); return 6
    if matched is None:                                     # off-table -> DUTY_REVIEW, never silent
        decision["action"] = "duty_review_offtable"
        append_event(args.packet, "duty_review", {"why": "regex_no_match", "err_head": err[:300]})
        print(json.dumps(decision)); return 4
    # 2 consecutive same-class failures -> duty officer partition (transition 10)
    last = pk.get("last_fail_class")
    pk["last_fail_class"] = matched["name"]
    if last == matched["name"]:
        decision["action"] = "duty_review_repeat"
        append_event(args.packet, "duty_review", {"why": "2_consecutive_same_class",
                                                  "class": matched["name"]})
        json.dump(led, open(led_p, "w"), indent=1); print(json.dumps(decision)); return 4
    # Schema compatibility (found by Phase7 tests): shipped retry_classes.yaml
    # uses {action: retry|dead_letter|duty_review, max_retries, backoff_base,
    # backoff_cap}; the original Group A schema used {retryable, max_attempts,
    # base, cap}. Accept both — first key present wins.
    cls_action = matched.get("action")
    if not matched.get("retryable", cls_action == "retry"):
        if cls_action == "duty_review":            # fixable/semantic class -> duty partition
            decision["action"] = "duty_review_class"
            append_event(args.packet, "duty_review", {"why": "class_action_duty_review",
                                                      "class": matched["name"]})
            json.dump(led, open(led_p, "w"), indent=1); print(json.dumps(decision)); return 4
        decision["action"] = "dead_letter_permanent"
        dead_letter(args.packet, "permanent_class", err[:500], matched["name"])
        json.dump(led, open(led_p, "w"), indent=1); print(json.dumps(decision)); return 5
    max_att = int(matched.get("max_attempts", matched.get("max_retries", DEFAULTS["max_attempts"])))
    if pk.get("attempts", 0) >= max_att or total_retries >= run_budget:
        decision["action"] = "dead_letter_budget"          # per-packet or run-level budget spent
        dead_letter(args.packet, "budget_exhausted", err[:500], matched["name"])
        json.dump(led, open(led_p, "w"), indent=1); print(json.dumps(decision)); return 5
    delay = full_jitter(float(matched.get("base", matched.get("backoff_base", DEFAULTS["base"]))),
                        float(matched.get("cap", matched.get("backoff_cap", DEFAULTS["cap"]))),
                        pk.get("attempts", 0))
    decision.update(action="retry", delay_s=round(delay, 2), attempt=pk.get("attempts", 0) + 1)
    append_event(args.packet, "retry_dispatch", decision)   # FAILED -> RUNNING (transition 9)
    json.dump(led, open(led_p, "w"), indent=1)
    print(json.dumps(decision)); return 0

if __name__ == "__main__":
    sys.exit(main())
