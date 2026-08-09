#!/usr/bin/env python3
# ============================================================================
# dispatch.py — Deterministic dispatch (spec §3.3 transition 3, incl CSV batch)
# Purpose : Take DISPATCHABLE packets, allocate a worktree each (physical
#           isolation), and dispatch: (a) single-spawn via `codex exec` in the
#           packet worktree, or (b) homogeneous batch via a spawn_agents_on_csv
#           instruction pack (CSV + instruction template + output_schema) that
#           Sol's session invokes as a tool. No LLM calls inside this script.
# Input   : data/packets/<pid>.json (4-field), dag.json wave index, config.
# Output  : 'dispatched' events appended to events.ndjson; per-packet worktree;
#           CSV mode: data/dispatch/batch_<wave>.csv + instruction + schema.
#           Exit 0 = all dispatched, 1 = error, 2 = nothing dispatchable.
# Lines   : ~110 (excluding this header)
# ============================================================================
import argparse, csv, json, os, subprocess, sys, time

ROOT = os.environ.get("LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
HARN = os.path.join(ROOT, "harness")

def append_event(pid, event, detail=None):
    with open(os.path.join(DATA, "events.ndjson"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "packet_id": pid, "event": event,
                            "detail": detail or {}}, separators=(",", ":")) + "\n")

def load_packet(pid):
    p = json.load(open(os.path.join(DATA, "packets", pid + ".json"), encoding="utf-8"))
    for k in ("packet_id", "goal", "authorized_paths", "acceptance"):
        if k not in p:
            sys.stderr.write("packet %s missing required field %s\n" % (pid, k)); sys.exit(1)
    return p

def wave_packets(wave_idx):
    dag = json.load(open(os.path.join(DATA, "packets", "dag.json"), encoding="utf-8"))
    waves = dag.get("waves", [])
    if wave_idx >= len(waves):
        sys.stderr.write("no wave %d in dag.json\n" % wave_idx); sys.exit(1)
    return waves[wave_idx]

def dispatchable(pids):
    led_path = os.path.join(DATA, "progress_ledger.json")
    led = json.load(open(led_path, encoding="utf-8")) if os.path.exists(led_path) else {"packets": {}}
    return [p for p in pids
            if led["packets"].get(p, {}).get("state") == "DISPATCHABLE"]

def spawn_prompt(pkt, worktree):
    """Four-field spawn prompt (spec §4.3) — goal/authorized_paths/acceptance/constraints."""
    return ("You are an Executor. Work ONLY inside %s.\n"
            "goal: %s\nauthorized_paths: %s\nacceptance: %s\nconstraints: %s\n"
            "On completion write data/reports/%s/report.json with fields "
            '{"packet_id","status":"done|failed","summary"(<=500 tokens),"diff_stat"} '
            "and return 1 line conclusion + artifact path."
            % (worktree, pkt["goal"], json.dumps(pkt["authorized_paths"]),
               json.dumps(pkt["acceptance"]), json.dumps(pkt.get("constraints", [])),
               pkt["packet_id"]))

def allocate_worktree(pid):
    out = subprocess.run(["bash", os.path.join(HARN, "worktree_pool.sh"), "allocate", pid],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.stderr.write("worktree allocate failed for %s: %s\n" % (pid, out.stderr)); sys.exit(1)
    return out.stdout.strip().splitlines()[-1]

def dispatch_single(pids, dry_run):
    for pid in pids:
        pkt = load_packet(pid)
        wt = allocate_worktree(pid) if not dry_run else "<worktree>"
        os.makedirs(os.path.join(DATA, "reports", pid), exist_ok=True)
        cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
               "-o", os.path.join(DATA, "reports", pid, "last_message.txt"), spawn_prompt(pkt, wt)]
        if dry_run:
            print("DRY-RUN %s: %s" % (pid, " ".join(cmd[:6]) + " ..."))
        else:
            # Fire the worker in its own worktree; wait-all happens natively at
            # the caller (blocking produces no Sol rounds — axiom 2, no polling).
            subprocess.Popen(cmd, cwd=wt, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        append_event(pid, "dispatched", {"mode": "single", "worktree": wt, "dry_run": dry_run})

def dispatch_csv(pids, wave_idx, dry_run):
    """Homogeneous batch: emit spawn_agents_on_csv inputs (tool is invoked from
    the Codex session, not the CLI). One row per packet, {column} placeholders."""
    ddir = os.path.join(DATA, "dispatch"); os.makedirs(ddir, exist_ok=True)
    csv_path = os.path.join(ddir, "batch_w%d.csv" % wave_idx)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["packet_id", "goal", "authorized_paths", "acceptance", "constraints", "worktree"])
        for pid in pids:
            pkt = load_packet(pid)
            wt = allocate_worktree(pid) if not dry_run else "<worktree>"
            os.makedirs(os.path.join(DATA, "reports", pid), exist_ok=True)
            w.writerow([pid, pkt["goal"], json.dumps(pkt["authorized_paths"]),
                        json.dumps(pkt["acceptance"]), json.dumps(pkt.get("constraints", [])), wt])
            append_event(pid, "dispatched", {"mode": "csv", "worktree": wt, "dry_run": dry_run})
    instruction = ("Work ONLY inside {worktree}. goal: {goal}. authorized_paths: {authorized_paths}. "
                   "acceptance: {acceptance}. constraints: {constraints}. Write "
                   "data/reports/{packet_id}/report.json when done; return 1-line conclusion + path.")
    call = {"tool": "spawn_agents_on_csv",
            "csv_path": csv_path, "instruction": instruction, "id_column": "packet_id",
            "output_schema": {"status": "string", "summary": "string", "report_path": "string"},
            "output_csv_path": os.path.join(ddir, "results_w%d.csv" % wave_idx),
            "max_concurrency": 4, "max_runtime_seconds": 1800}
    json.dump(call, open(os.path.join(ddir, "batch_w%d.call.json" % wave_idx), "w",
                         encoding="utf-8"), indent=1)
    print("CSV batch pack written: %s (+ .call.json). Invoke spawn_agents_on_csv "
          "with these arguments from the orchestrating session." % csv_path)

def main():
    ap = argparse.ArgumentParser(description="LOOP-F2 dispatcher (single + CSV batch)")
    ap.add_argument("--wave", type=int, default=0, help="wave index in dag.json")
    ap.add_argument("--mode", choices=["single", "csv"], default="single")
    ap.add_argument("--packet", action="append", help="dispatch specific packet id(s)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="skip DISPATCHABLE state check")
    args = ap.parse_args()
    pids = args.packet or wave_packets(args.wave)
    if not args.force:
        pids = dispatchable(pids)
    if not pids:
        print("nothing dispatchable"); return 2
    if args.mode == "csv":
        dispatch_csv(pids, args.wave, args.dry_run)
    else:
        dispatch_single(pids, args.dry_run)
    return 0

if __name__ == "__main__":
    sys.exit(main())
