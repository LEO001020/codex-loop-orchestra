#!/usr/bin/env python3
# ============================================================================
# dispatch.py — Deterministic dispatch (spec §3.3 transition 3, incl CSV batch)
# Purpose : Take DISPATCHABLE packets, allocate a worktree each (physical
#           isolation), and dispatch: (a) single-spawn via `codex exec` in the
#           packet worktree, or (b) homogeneous batch via a spawn_agents_on_csv
#           instruction pack (CSV + instruction template + output_schema) that
#           Sol's session invokes as a tool. No LLM calls inside this script.
# Input   : data/packets/<pid>.json (4-field), dag.json wave index, config,
#           agents/<role>.toml (single source of truth for model pinning).
# Output  : 'dispatched' events appended to events.ndjson; per-packet worktree;
#           single mode: `codex exec` runs with -m <model> -c
#           model_reasoning_effort=<effort> read from the role TOML (P0-1:
#           exec top-level processes do NOT load agents/*.toml, so without
#           the explicit override every packet would run on the root Sol
#           model); --json stdout is landed to data/reports/<pid>/events.jsonl
#           (turn.completed.usage = per-packet token consumption, no hook
#           dependency); spawn wall-clock ts recorded to data/spawn_times.json
#           (consumed by statemachine.py watchdog, transition 5 producer);
#           re-dispatch (attempts>0): one previous-attempt handle line is
#           appended to the spawn prompt — PATH + retry class (+ duty officer
#           fix_hint when a gated fixable ruling exists), never file content
#           (axiom 5: path + summary; bytes never enter Sol).
#           CSV mode: data/dispatch/batch_<wave>.csv + instruction + schema +
#           agent_type (in-session spawn_agents_on_csv loads the agent TOML;
#           [agents] default_subagent_model/_reasoning_effort is the fallback).
#           Exit 0 = all dispatched, 1 = error, 2 = nothing dispatchable.
# Lines   : ~175 (excluding this header)
# ============================================================================
import argparse, csv, json, os, re, shutil, subprocess, sys, time, tomllib, urllib.request, uuid
from loop_config import config_value, policy_int
from lifecycle_supervisor import locked
from pathlib import Path

ROOT = os.environ.get("LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
HARN = os.path.join(ROOT, "harness")
SOL_BUDGET_EXEMPT_STATES = {"planning", "adjudication", "release_finalize"}
ADJUDICATION_STATES = {"SOL_ADJUDICATE", "DEAD_LETTER", "MERGE_CONFLICT",
                       "WAVE_DONE", "WAVE_DONE_READY", "SOL_WAKE"}
TERMINAL_STATES = {"MERGED", "DONE"}
PACKET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
RELEASE_REVIEW_ROLE = "reviewer"
RELEASE_REVIEW_SANDBOX = "read-only"
RELEASE_REVIEW_DIR = os.path.join(DATA, "release_review")
THROTTLE_STATE = os.path.join(DATA, "refill", "spawn_throttle_state.json")
THROTTLE_LOCK = Path(DATA) / "refill" / ".spawn_throttle.lock"


class BirthThrottleError(RuntimeError):
    pass

def validate_packet_id(pid):
    if not isinstance(pid, str) or not PACKET_ID_RE.fullmatch(pid):
        sys.stderr.write("invalid packet id %r (allowed: 1-96 ASCII letters/digits/._-)\n" % pid)
        raise SystemExit(1)
    return pid

def agent_toml_path(role):
    """Locate agents/<role>.toml: LOOP_ROOT first (test override), then
    $CODEX_HOME, then the package dir this script really lives in."""
    pkg = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    candidates = [os.path.join(ROOT, "agents", role + ".toml"),
                  os.path.join(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")),
                               "agents", role + ".toml"),
                  os.path.join(pkg, "agents", role + ".toml")]
    for c in candidates:
        if os.path.exists(c):
            return c
    sys.stderr.write("agent TOML not found for role %s (searched: %s)\n"
                     % (role, ", ".join(candidates)))
    sys.exit(1)

def agent_overrides(role):
    """P0-1 root-cause fix: read the role TOML (single source of truth — never
    hand-copy model names) and return ([-m model, -c effort], sandbox, model,
    effort). `codex exec` top-level processes do NOT load agents/*.toml
    (research §5), so without these overrides every dispatched packet runs on
    the root Sol model. Fail-visible: missing model/effort fields exit 1 —
    silently falling back to Sol is exactly the defect being fixed."""
    path = agent_toml_path(role)
    try:
        with open(path, "rb") as f:
            t = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        sys.stderr.write("agent TOML %s unreadable: %s\n" % (path, e)); sys.exit(1)
    model = t.get("model")
    effort = t.get("model_reasoning_effort")
    if not model or not effort:
        sys.stderr.write("agent TOML %s missing model/model_reasoning_effort — "
                         "refusing to dispatch on the root model (P0-1 fail-visible)\n" % path)
        sys.exit(1)
    ov = ["-m", model, "-c", "model_reasoning_effort=%s" % effort]
    ov.extend(context_overrides(t, path))
    return ov, t.get("sandbox_mode", "workspace-write"), model, effort


def context_overrides(agent_toml, path):
    """Carry role-local context sizing into top-level ``codex exec``.

    Native spawned agents load the role TOML directly; headless exec workers do
    not, so both context keys must ride on the CLI just like model/effort.
    """
    overrides = []
    for key in ("model_context_window", "model_auto_compact_token_limit"):
        value = agent_toml.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            sys.stderr.write("agent TOML %s has invalid %s=%r\n" % (path, key, value))
            sys.exit(1)
        overrides.extend(["-c", "%s=%d" % (key, value)])
    return overrides


def ipybox_enabled_for(role, packet=None):
    """Resolve ipybox from the v2 policy without a model-name shortcut.

    Desktop is the control/observation plane and never hosts ipybox.  On the
    WSL/headless plane only ordinary workers enable it by default.  K3 roles
    remain disabled unless the packet explicitly needs code execution and the
    policy permits that exception; the duty officer is always zero-tool.
    """
    if os.environ.get("LOOP_EXECUTION_PLANE") == "desktop_native" or os.name == "nt":
        return False
    policy_path = os.path.join(ROOT, "config", "orchestration_policy_v2.toml")
    try:
        with open(policy_path, "rb") as handle:
            policy = tomllib.load(handle).get("ipybox", {})
    except (OSError, tomllib.TOMLDecodeError):
        policy = {}
    if role == "worker":
        return bool(policy.get("wsl_headless_worker_enabled", True))
    if role in ("verifier", "reviewer", "plan_expander"):
        if bool(policy.get("k3_planning_verifying_enabled", False)):
            return True
        return (bool((packet or {}).get("needs_code_execution"))
                and bool(policy.get("k3_code_execution_exception", True)))
    return False

def append_event(pid, event, detail=None):
    with locked(Path(DATA) / "lifecycle" / ".events.lock"):
        with open(os.path.join(DATA, "events.ndjson"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "packet_id": pid, "event": event,
                                "detail": detail or {}}, separators=(",", ":")) + "\n")


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def resolve_codex_binary():
    """Resolve the real executable for the native supervisor boundary."""
    explicit = os.environ.get("CODEX_HEADLESS_BIN")
    if explicit:
        path = Path(explicit).resolve()
        if path.is_file():
            return str(path)
        raise OSError("CODEX_HEADLESS_BIN is not a file: %s" % path)
    if os.name == "nt":
        vendor = (Path.home() / "AppData" / "Roaming" / "npm" / "node_modules"
                  / "@openai" / "codex" / "node_modules" / "@openai"
                  / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc"
                  / "bin" / "codex.exe")
        if vendor.is_file():
            return str(vendor)
        candidate = shutil.which("codex.exe")
    else:
        candidate = shutil.which("codex")
    if not candidate:
        raise OSError("headless codex executable not found")
    return candidate


def update_throttle_state(**fields):
    os.makedirs(os.path.dirname(THROTTLE_STATE), exist_ok=True)
    with locked(THROTTLE_LOCK):
        state = read_json(THROTTLE_STATE, {"schema": "codex-loop-spawn-throttle/v1"})
        state.update(fields)
        state["updated_at"] = time.time()
        tmp = THROTTLE_STATE + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, THROTTLE_STATE)
        return state


def observed_birth_counts():
    # Keep the physical birth gate on exactly the same lifecycle truth as the
    # canonical refill controller.  The old duplicate parser counted stale
    # native ``running`` rows forever and ignored headless PID generations,
    # so dispatch could refuse at 60 while refill correctly observed 23.
    try:
        sys.path.insert(0, str(Path(ROOT) / "harness"))
        from orchestration_common import LoopPaths
        from refill_controller_v2 import RefillControllerV2
        counts = RefillControllerV2(LoopPaths.resolve(Path(ROOT))).read_roster_counts()
        initializing = sum(counts[pool]["initializing"] for pool in ("v4", "k3"))
        running = sum(counts[pool]["running"] for pool in ("v4", "k3"))
        return initializing, running
    except Exception as exc:
        # Capacity is a safety gate: if the canonical truth source is
        # unavailable, fail closed instead of falling back to stale text rows.
        raise BirthThrottleError(
            "canonical lifecycle counts unavailable: %s: %s" %
            (type(exc).__name__, exc)) from exc


def health_gate(pid):
    timeout = max(0.1, policy_int("spawn_throttle", "health_timeout_ms", 2000,
                                  Path(ROOT)) / 1000.0)
    backoff = max(1, policy_int("spawn_throttle", "failure_backoff_seconds", 30,
                                Path(ROOT)))
    url = os.environ.get("LOOP_OPENCODEX_HEALTH_URL", "http://127.0.0.1:10100/healthz")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = json.loads(response.read(65536).decode("utf-8"))
        if response.status != 200 or body.get("status") != "ok":
            raise RuntimeError("unhealthy response status=%s body_status=%s" %
                               (response.status, body.get("status")))
    except Exception as exc:
        now = time.time()
        detail = {"status": "failed", "checked_at": now,
                  "error": "%s: %s" % (type(exc).__name__, exc)}
        update_throttle_state(last_health=detail, last_error=detail["error"],
                              blocked_until=now + backoff)
        append_event(pid, "spawn_health_gate_failed",
                     {**detail, "backoff_seconds": backoff})
        raise BirthThrottleError("OpenCodex health gate failed; births blocked for %ss" % backoff)
    detail = {"status": "ok", "checked_at": time.time(),
              "pid": body.get("pid"), "uptime": body.get("uptime")}
    update_throttle_state(last_health=detail, last_error=None,
                          blocked_until=0, births_since_health=0)
    append_event(pid, "spawn_health_gate_passed", detail)


def before_spawn(pid):
    interval = max(0, policy_int("spawn_throttle", "spawn_interval_ms", 1000,
                                 Path(ROOT))) / 1000.0
    max_initializing = max(1, policy_int("spawn_throttle", "max_initializing", 8,
                                         Path(ROOT)))
    backoff = max(1, policy_int("spawn_throttle", "failure_backoff_seconds", 30,
                                Path(ROOT)))
    target_total = max(1, policy_int("concurrency", "target_total", 80, Path(ROOT)))
    deadline = time.monotonic() + backoff
    while True:
        state = read_json(THROTTLE_STATE, {})
        blocked_until = float(state.get("blocked_until", 0) or 0)
        if blocked_until > time.time():
            raise BirthThrottleError("spawn backoff active until %.3f" % blocked_until)
        initializing, running = observed_birth_counts()
        if initializing + running >= target_total:
            raise BirthThrottleError("effective+initializing concurrency reached target %d" % target_total)
        if initializing < max_initializing:
            break
        if time.monotonic() >= deadline:
            now = time.time()
            error = "initializing cap %d did not drain within %ss" % (max_initializing, backoff)
            update_throttle_state(last_error=error, blocked_until=now + backoff)
            append_event(pid, "spawn_initializing_timeout",
                         {"max_initializing": max_initializing, "backoff_seconds": backoff})
            raise BirthThrottleError(error)
        time.sleep(0.2)
    last_spawn = float(state.get("last_spawn_at", 0) or 0)
    remaining = interval - (time.time() - last_spawn)
    if remaining > 0:
        time.sleep(remaining)


def record_spawn_and_gate(pid, role, model):
    gate_every = max(1, policy_int("spawn_throttle", "health_gate_every", 8,
                                   Path(ROOT)))
    state = read_json(THROTTLE_STATE, {})
    count = int(state.get("births_since_health", 0) or 0) + 1
    update_throttle_state(last_spawn_at=time.time(), births_since_health=count,
                          last_role=role, last_model=model)
    if count >= gate_every:
        health_gate(pid)

def load_packet(pid):
    validate_packet_id(pid)
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

def current_loop_state():
    path = os.path.join(DATA, "progress_ledger.json")
    led = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"packets": {}}
    explicit = led.get("loop_state")
    if isinstance(explicit, str) and explicit:
        return explicit
    states = [p.get("state") for p in led.get("packets", {}).values()]
    if not states:
        return "planning"
    if any(state in ADJUDICATION_STATES for state in states):
        return "adjudication"
    if all(state in TERMINAL_STATES for state in states):
        return "release_finalize"
    return "execution"

def sol_budget_block(role):
    """Return a persisted >25% BLOCK decision for new Sol-model work.
    V4/K3 dispatch stays available so it can reduce the Sol share. Missing
    reports preserve cold-start behavior; malformed present reports fail
    closed for Sol work."""
    if role != "sol" or current_loop_state() in SOL_BUDGET_EXEMPT_STATES:
        return None
    path = os.path.join(DATA, "usage", "model_token_share.json")
    if not os.path.exists(path):
        return None
    try:
        report = json.load(open(path, encoding="utf-8"))
        windows = report.get("windows", {})
        blocked = [name for name, value in windows.items()
                   if isinstance(value, dict) and value.get("status") == "BLOCK"]
    except (OSError, ValueError, AttributeError):
        return {"reason": "malformed Sol token-share report", "report": path}
    if not blocked:
        return None
    return {"reason": "Sol token-share hard cap", "report": path,
            "blocked_windows": blocked}

def previous_attempt_line(pid):
    """H-03 hedge: on re-dispatch (attempts>0) hand the worker a HANDLE to its
    previous failure — report path + retry class (+ gated duty fix_hint, H-01)
    — never the content itself (axiom 5: path + summary, free-side disk read
    costs zero Sol tokens). First dispatch returns '' (byte-identical prompt)."""
    led_path = os.path.join(DATA, "progress_ledger.json")
    try:
        led = json.load(open(led_path, encoding="utf-8")) if os.path.exists(led_path) else {"packets": {}}
        pk = led.get("packets", {}).get(pid, {})
    except (OSError, ValueError):
        pk = {}  # unreadable ledger: behave like first dispatch, never crash
    attempts = int(pk.get("attempts", 0) or 0)
    if attempts <= 0:
        return ""
    retry_class = pk.get("last_fail_class") or "unclassified"
    previous_path = "data/reports/%s/previous/attempt-%d.json" % (pid, max(0, attempts - 1))
    line = ("previous_attempt: %s (failed: %s, attempts: %d)"
            % (previous_path, retry_class, attempts))
    hint_path = os.path.join(DATA, "duty_rulings", pid + ".json")
    try:  # H-01 wiring: gate-validated fixable ruling's fix_hint joins the handle line
        if os.path.exists(hint_path):
            ruling = json.load(open(hint_path, encoding="utf-8"))
            hint = ruling.get("fix_hint", "")
            if ruling.get("class") == "fixable" and isinstance(hint, str) and 0 < len(hint) <= 200:
                line += "; fix_hint: %s" % hint
    except (OSError, ValueError):
        pass  # unreadable ruling: drop the hint, keep the handle — never crash
    return line + "\n"

def packet_attempt(pid):
    try:
        led = json.load(open(os.path.join(DATA, "progress_ledger.json"), encoding="utf-8"))
        return int(led.get("packets", {}).get(pid, {}).get("attempts", 0) or 0)
    except (OSError, ValueError, TypeError):
        return 0

def new_run_id(pid, attempt):
    return "%s-a%d-%s" % (pid, attempt, uuid.uuid4().hex)

def task_name(pkt):
    explicit = pkt.get("task_name")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()[:160]
    prefix = "执行数据包 %s — " % pkt["packet_id"]
    return (prefix + str(pkt["goal"]))[:160]

def spawn_prompt(pkt, worktree):
    """Four-field spawn prompt (spec §4.3) — goal/authorized_paths/acceptance/constraints.
    Re-dispatch appends ONE previous-attempt handle line (path + class, H-03)."""
    return ("任务名：%s\n"
            "You are an Executor. Work ONLY inside %s.\n"
            "goal: %s\nauthorized_paths: %s\nacceptance: %s\nconstraints: %s\n"
            "%s"
            "On completion write data/reports/%s/report.json with fields "
            '{"packet_id","status":"done|failed","summary"(<=500 tokens),"diff_stat"} '
            "and return 1 line conclusion + artifact path."
            % (task_name(pkt), worktree,
               pkt["goal"], json.dumps(pkt["authorized_paths"]),
               json.dumps(pkt["acceptance"]), json.dumps(pkt.get("constraints", [])),
               previous_attempt_line(pkt["packet_id"]), pkt["packet_id"]))

def record_spawn_time(pid, mode, run_id, attempt):
    """H-02 hedge: persist the spawn wall-clock so statemachine.py's watchdog
    can emit transition-5 'timeout' for overdue RUNNING packets (zero token)."""
    path = os.path.join(DATA, "spawn_times.json")
    try:
        times = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    except (OSError, ValueError):
        times = {}  # corrupt state: rebuild — watchdog degrades, dispatch never blocks
    times[pid] = {"ts": time.time(), "mode": mode, "run_id": run_id,
                  "attempt": attempt}
    tmp = path + ".tmp"
    json.dump(times, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, path)

def prepare_report_slot(pid, attempt, local_report=None):
    """Archive the prior generation before a new physical dispatch."""
    current = os.path.join(DATA, "reports", pid, "report.json")
    if os.path.exists(current):
        history = os.path.join(DATA, "reports", pid, "previous")
        os.makedirs(history, exist_ok=True)
        name = ("attempt-%d.json" % max(0, attempt - 1) if attempt > 0
                else "preexisting-%d.json" % int(time.time() * 1000))
        os.replace(current, os.path.join(history, name))
    if local_report and os.path.exists(local_report):
        os.remove(local_report)

def allocate_worktree(pid):
    out = subprocess.run(["bash", os.path.join(HARN, "worktree_pool.sh"), "allocate", pid],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.stderr.write("worktree allocate failed for %s: %s\n" % (pid, out.stderr)); sys.exit(1)
    return out.stdout.strip().splitlines()[-1]

def supervisor_command(pid, pkt, wt, rdir, local_report, run_id, attempt,
                       parent_session_id, manifest_id, worker_cmd, role="worker",
                       model=None, completion=None):
    if not model:
        raise ValueError("lifecycle supervisor requires an explicit model pin")
    timeout = float(config_value("agents", "job_max_runtime_seconds", 1800))
    grace = float(config_value("lifecycle", "stop_grace_seconds", 10))
    command = [sys.executable, os.path.join(HARN, "lifecycle_supervisor.py"),
            "--data-dir", DATA, "--packet", pid, "--run-id", run_id,
            "--attempt", str(attempt),
            *(["--parent-session-id", parent_session_id] if parent_session_id else []),
            *(["--manifest-id", manifest_id] if manifest_id else []),
            "--task-name", task_name(pkt), "--role", role, "--model", model,
            "--cwd", wt, "--stdout", os.path.join(rdir, "events.jsonl"),
            "--stderr", os.path.join(rdir, "stderr.log"),
            "--report", local_report,
            "--publish-report", os.path.join(rdir, "report.json"),
            "--timeout", str(timeout), "--grace", str(grace)]
    if completion:
        command += ["--l2-idem-key", str(completion["idem_key"])]
        if completion.get("revision") is not None:
            command += ["--l2-revision", str(completion["revision"])]
    return [*command, "--", *worker_cmd]

def launch_supervisor(pid, pkt, wt, rdir, local_report, run_id, attempt,
                       parent_session_id, manifest_id, worker_cmd, role="worker",
                       model=None, completion=None):
    command = supervisor_command(pid, pkt, wt, rdir, local_report, run_id,
                                 attempt, parent_session_id, manifest_id,
                                 worker_cmd, role,
                                 model, completion)
    log = open(os.path.join(rdir, "supervisor.log"), "ab")
    kwargs = {"cwd": ROOT, "stdin": subprocess.DEVNULL, "stdout": log,
              "stderr": subprocess.STDOUT, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                                   getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(command, **kwargs)
    finally:
        log.close()
    return proc.pid

def dispatch_single(pids, dry_run, role="worker", wave_idx=0,
                     pinned=None, mode=None, prompt_builder=None,
                     capture_report=False, completion=None,
                     detail_extra=None, run_id_overrides=None,
                     parent_session_id_overrides=None,
                     readonly_cwd_overrides=None):
    # P0-1: model/effort/sandbox come from the role TOML — the exec top-level
    # process never loads agents/*.toml, so the pin MUST ride on the CLI.
    if pinned is not None:
        overrides, sandbox, model, effort = pinned  # hard route: caller pins
    else:
        overrides, sandbox, model, effort = agent_overrides(role)
    dispatch_mode = mode or "single"
    last_run_id = None
    for pid in pids:
        pkt = load_packet(pid)
        attempt = packet_attempt(pid)
        run_id = ((run_id_overrides or {}).get(pid)
                  if isinstance(run_id_overrides, dict) else None)
        run_id = str(run_id or new_run_id(pid, attempt))
        parent_session_id = ((parent_session_id_overrides or {}).get(pid)
                             if isinstance(parent_session_id_overrides, dict)
                             else None) or (os.environ.get("LOOP_PARENT_SESSION_ID") or
                             os.environ.get("CODEX_THREAD_ID") or
                             os.environ.get("CODEX_SESSION_ID"))
        manifest_id = str(pkt.get("manifest_id") or "") or None
        if not dry_run:
            try:
                before_spawn(pid)
            except BirthThrottleError as exc:
                # Refuse before allocating a worktree or creating report
                # slots.  A throttled packet remains DISPATCHABLE and the
                # refusal is still durable and machine-readable.
                try:
                    append_event(pid, "spawn_throttled", {
                        "phase": "pre_spawn", "error": str(exc),
                        "role": role, "model": model, "run_id": run_id,
                        "attempt": attempt,
                    })
                except Exception as event_exc:
                    sys.stderr.write(
                        "spawn throttle event degraded for %s: %s\n" %
                        (pid, event_exc))
                raise
        # K3 verifier output captured via ``codex exec -o`` is a read-only,
        # detached job: it neither needs nor may mutate a Git worktree.  This
        # also lets the WSL deployment root consume a mounted source repo
        # without pretending ``$LOOP_ROOT/repo`` exists.  Ordinary workers
        # still require the physical worktree pool.
        readonly_cwd = ((readonly_cwd_overrides or {}).get(pid)
                        if isinstance(readonly_cwd_overrides, dict) else None)
        detached_readonly = bool(readonly_cwd) or (capture_report and role in (
            "verifier", "reviewer", "plan_expander"))
        detached_root = os.path.realpath(readonly_cwd or ROOT)
        wt = (detached_root if detached_readonly and not dry_run else
              "<read-only-root>" if detached_readonly else
              allocate_worktree(pid) if not dry_run else "<worktree>")
        rdir = os.path.join(DATA, "reports", pid)
        os.makedirs(rdir, exist_ok=True)
        local_report = (os.path.join(DATA, "lifecycle_reports", pid,
                                     "report.json")
                        if detached_readonly else
                        os.path.join(wt, "data", "reports", pid, "report.json")
                        if not dry_run else
                        os.path.join("<worktree>", "data", "reports", pid,
                                     "report.json"))
        if not dry_run:
            os.makedirs(os.path.dirname(local_report), exist_ok=True)
            prepare_report_slot(pid, attempt, local_report)
        prompt = (spawn_prompt(pkt, wt) if prompt_builder is None
                  else prompt_builder(pkt, wt, run_id))
        ipybox = ipybox_enabled_for(role, pkt)
        output_path = local_report if capture_report else os.path.join(rdir, "last_message.txt")
        cmd = [resolve_codex_binary(), "exec", "--skip-git-repo-check", "--sandbox", sandbox,
               *overrides, "-c", "mcp_servers.ipybox.enabled=%s" %
               ("true" if ipybox else "false")]
        if os.name == "nt":
            cmd += ["-c", "mcp_servers.node_repl.enabled=false"]
        cmd += ["--json", "-o", output_path, prompt]
        detail = {"mode": dispatch_mode, "worktree": wt, "dry_run": dry_run,
                  "role": role, "model": model, "reasoning_effort": effort,
                   "wave": wave_idx, "attempt": attempt, "run_id": run_id,
                   "parent_session_id": parent_session_id,
                   "manifest_id": manifest_id,
                  "ipybox_enabled": ipybox,
                  "local_report": local_report}
        if detail_extra:
            detail.update(detail_extra)
        if dry_run:
            print("DRY-RUN %s: %s" % (pid, json.dumps(cmd)))
            append_event(pid, "dispatch_dry_run", detail)
        else:
            # Fire the worker in its own worktree; wait-all happens natively at
            # the caller (blocking produces no Sol rounds — axiom 2, no polling).
            # --json stdout lands in events.jsonl: turn.completed.usage gives
            # this packet's token consumption without any hook dependency.
            record_spawn_time(pid, dispatch_mode, run_id, attempt)
            # Persist transition 3 before the supervisor can emit a terminal
            # event. This removes the fast-failure race (exec_failed before
            # dispatched would otherwise be an off-table NONE transition).
            append_event(pid, "dispatched", detail)
            try:
                supervisor_pid = launch_supervisor(pid, pkt, wt, rdir, local_report,
                                                   run_id, attempt, parent_session_id,
                                                   manifest_id, cmd,
                                                   role, model, completion)
            except OSError as exc:
                append_event(pid, "exec_failed", {"why": "supervisor_launch_failed",
                                                   "phase": "pre_spawn",
                                                   "error": str(exc), "run_id": run_id,
                                                   "attempt": attempt})
                raise
            # Popen is the physical birth boundary.  Failures after this point
            # must remain visible but may not bubble up as spawn failures: the
            # supervisor already owns a live child and its budget reservation.
            try:
                append_event(pid, "exec_supervisor_started", {
                    "supervisor_pid": supervisor_pid,
                    "run_id": run_id, "attempt": attempt})
                record_spawn_and_gate(pid, role, model)
            except Exception as exc:
                sys.stderr.write("post-spawn accounting degraded for %s: %s\n"
                                 % (pid, exc))
                try:
                    append_event(pid, "post_spawn_accounting_degraded", {
                        "error": str(exc), "supervisor_pid": supervisor_pid,
                        "run_id": run_id, "attempt": attempt})
                except Exception:
                    pass
            last_run_id = run_id
    return last_run_id

def dispatch_csv(pids, wave_idx, dry_run, role="worker"):
    """Homogeneous batch: emit spawn_agents_on_csv inputs (tool is invoked from
    the Codex session, not the CLI). One row per packet, {column} placeholders.
    P0-1: the call pack pins agent_type=<role> so the in-session spawn loads
    agents/<role>.toml; agent_overrides() is still consulted so a missing/
    unpinned TOML fails HERE (fail-visible), and the pinned model is recorded
    in every dispatched event. [agents] default_subagent_model/_reasoning_effort
    in config.toml is the documented fallback layer."""
    _, _, model, effort = agent_overrides(role)  # fail-visible TOML validation
    ddir = os.path.join(DATA, "dispatch"); os.makedirs(ddir, exist_ok=True)
    csv_path = os.path.join(ddir, "batch_w%d.csv" % wave_idx)
    results_path = os.path.join(ddir, "results_w%d.csv" % wave_idx)
    stamp_path = os.path.join(ddir, "reconcile_w%d.json" % wave_idx)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["packet_id", "task_name", "goal", "authorized_paths", "acceptance",
                    "constraints", "worktree", "local_report", "attempt", "run_id"])
        for pid in pids:
            pkt = load_packet(pid)
            attempt = packet_attempt(pid)
            run_id = new_run_id(pid, attempt)
            wt = allocate_worktree(pid) if not dry_run else "<worktree>"
            os.makedirs(os.path.join(DATA, "reports", pid), exist_ok=True)
            local_report = os.path.join(wt, "data", "reports", pid, "report.json")
            w.writerow([pid, task_name(pkt), pkt["goal"], json.dumps(pkt["authorized_paths"]),
                        json.dumps(pkt["acceptance"]), json.dumps(pkt.get("constraints", [])),
                        wt, local_report, attempt, run_id])
            detail = {"mode": "csv", "worktree": wt, "dry_run": dry_run,
                                             "role": role, "model": model,
                                             "reasoning_effort": effort, "wave": wave_idx,
                                             "attempt": attempt, "run_id": run_id,
                                             "local_report": local_report,
                                             "reconcile_required": not dry_run,
                                             "results_csv": results_path,
                                             "reconcile_stamp": stamp_path}
            if dry_run:
                append_event(pid, "dispatch_dry_run", detail)
            else:
                os.makedirs(os.path.dirname(local_report), exist_ok=True)
                prepare_report_slot(pid, attempt, local_report)
                record_spawn_time(pid, "csv", run_id, attempt)
                append_event(pid, "dispatched", detail)
    instruction = ("任务名：{task_name}\nWork ONLY inside {worktree}. goal: {goal}. authorized_paths: {authorized_paths}. "
                   "acceptance: {acceptance}. constraints: {constraints}. Write "
                   "{local_report} when done; return 1-line conclusion + path.")
    reconcile_argv = [sys.executable, os.path.join(ROOT, "harness", "csv_reconcile.py"),
                      "--batch-csv", csv_path, "--results-csv", results_path,
                      "--data-dir", DATA, "--stamp", stamp_path]
    state_argv = [sys.executable, os.path.join(ROOT, "harness", "statemachine.py"),
                  "reconcile"]
    call = {"tool": "spawn_agents_on_csv", "agent_type": role,
            "csv_path": csv_path, "instruction": instruction, "id_column": "packet_id",
            "output_schema": {"status": "string", "summary": "string", "report_path": "string"},
            "output_csv_path": results_path,
            # Per-parent/dialogue target comes from the same scheduling
            # authority as the global capacity. Model profiles never alter it.
            "max_concurrency": int(policy_int(
                "concurrency", "dialogue_target", 20, Path(ROOT))),
            "max_runtime_seconds": int(config_value("agents", "job_max_runtime_seconds", 1800)),
            "required_postprocess": {
                "enabled": not dry_run,
                "when": "after spawn_agents_on_csv returns and output_csv_path exists",
                "argv": reconcile_argv,
                "then_argv": state_argv,
                "stamp": stamp_path,
                "failure_policy": "nonzero is visible and must stop wave advancement"}}
    json.dump(call, open(os.path.join(ddir, "batch_w%d.call.json" % wave_idx), "w",
                         encoding="utf-8"), indent=1)
    print("CSV batch pack written: %s (+ .call.json). Invoke spawn_agents_on_csv "
          "with these arguments from the orchestrating session." % csv_path)
    print("REQUIRED after tool completion: %s" % subprocess.list2cmdline(reconcile_argv))
    print("THEN: %s" % subprocess.list2cmdline(state_argv))

# ----------------------------------------------------------------------------
# Release-review hard route (K3): explicit entry, fixed pins, fail-closed,
# idempotent per wave, result always returns to SOL_ADJUDICATE.
# ----------------------------------------------------------------------------

def release_review_pins():
    """Resolve one fail-closed release-review pin snapshot.

    The active model profile atomically maintains the policy and role TOML.
    Reading model *and* effort from that policy keeps release review valid when
    operators switch profiles, while still rejecting any role-TOML drift.
    """
    path = agent_toml_path(RELEASE_REVIEW_ROLE)
    try:
        with open(path, "rb") as f:
            t = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        sys.stderr.write("release-review pin check failed: agent TOML %s "
                         "unreadable: %s\n" % (path, e))
        sys.exit(1)
    model = t.get("model")
    effort = t.get("model_reasoning_effort")
    sandbox = t.get("sandbox_mode")
    try:
        with open(os.path.join(ROOT, "config", "orchestration_policy_v2.toml"),
                  "rb") as handle:
            policy_models = tomllib.load(handle)["models"]
            expected_model = policy_models["k3_model"]
            expected_effort = policy_models["k3_reasoning"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        sys.stderr.write("release-review policy pin unreadable: %s\n" % exc)
        sys.exit(1)
    if (model != expected_model or effort != expected_effort or
            sandbox != RELEASE_REVIEW_SANDBOX):
        sys.stderr.write("release-review pin mismatch (fail-closed): expected "
                         "%s/%s/%s, got %s/%s/%s\n" %
                         (expected_model, expected_effort,
                          RELEASE_REVIEW_SANDBOX, model, effort, sandbox))
        sys.exit(1)
    overrides = (["-m", expected_model,
                  "-c", "model_reasoning_effort=%s" % expected_effort]
                 + context_overrides(t, path))
    return overrides, str(expected_model), str(expected_effort)


def release_review_model():
    with open(agent_toml_path(RELEASE_REVIEW_ROLE), "rb") as handle:
        return str(tomllib.load(handle)["model"])

def release_review_record_path(wave_idx):
    return os.path.join(RELEASE_REVIEW_DIR, "w%d.json" % wave_idx)

def load_release_review_record(wave_idx):
    """Return any live/terminal launch ownership record for this wave."""
    path = release_review_record_path(wave_idx)
    try:
        if os.path.exists(path):
            rec = json.load(open(path, encoding="utf-8"))
            if isinstance(rec, dict) and rec.get("status") in (
                    "launching", "dispatched"):
                return rec
    except (OSError, ValueError):
        pass
    return None

def claim_release_review_launch(wave_idx, pid, run_id, model, effort):
    """Atomically own a wave before birth; exactly one caller may launch."""
    os.makedirs(RELEASE_REVIEW_DIR, exist_ok=True)
    path = release_review_record_path(wave_idx)
    with locked(Path(path + ".lock")):
        rec = {}
        if os.path.exists(path):
            try:
                rec = json.load(open(path, encoding="utf-8"))
            except (OSError, ValueError):
                rec = {}
        if isinstance(rec, dict) and rec.get("status") in (
                "launching", "dispatched"):
            return False, rec
        rec = {"schema": "codex-loop-release-review-record/v1", "wave": wave_idx,
               "packet_id": pid, "run_id": run_id, "role": RELEASE_REVIEW_ROLE,
               "model": model, "effort": effort,
               "mode": "release_review", "status": "launching", "ts": time.time()}
        tmp = path + ".tmp"
        json.dump(rec, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, path)
        return True, rec

def set_release_review_status(wave_idx, run_id, status, **detail):
    """Generation-scoped launch status update; stale launchers cannot clobber."""
    path = release_review_record_path(wave_idx)
    with locked(Path(path + ".lock")):
        try:
            rec = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(rec, dict) or rec.get("run_id") != run_id:
            return False
        rec.update(status=status, updated_at=time.time(), **detail)
        tmp = path + ".tmp"
        json.dump(rec, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, path)
        return True

def release_review_packet(wave_idx, pids):
    """One review unit per wave: a synthetic packet whose lifecycle stays on
    the review route and is flagged for routing back to SOL_ADJUDICATE."""
    pid = "rr-wave%d" % wave_idx
    pkt = {"packet_id": pid,
           "goal": ("Release-gate review of wave %d (packets: %s): "
                    "falsification review of the wave diff; never release."
                    % (wave_idx, ", ".join(pids))),
           "authorized_paths": ["data/reports/"],
           "acceptance": ["verdict_check.py --verdict <report> "
                          "--dispatch-record data/release_review/w%d.json"
                          % wave_idx],
           "constraints": ["read-only review", "no release action",
                           "verdict JSON + provenance echo in report"]}
    validate_packet_id(pid)
    os.makedirs(os.path.join(DATA, "packets"), exist_ok=True)
    json.dump(pkt, open(os.path.join(DATA, "packets", pid + ".json"), "w",
                        encoding="utf-8"), indent=1)
    return pid, pkt

def release_review_ledger_seed(pid, wave_idx):
    """Place the review unit at DISPATCHABLE (so the controlled 'dispatched'
    event is on-table, t3) and flag it for the SOL_ADJUDICATE return route."""
    led_path = os.path.join(DATA, "progress_ledger.json")
    led = json.load(open(led_path, encoding="utf-8")) \
        if os.path.exists(led_path) else {"packets": {}}
    led.setdefault("packets", {})[pid] = {"state": "DISPATCHABLE",
                                          "history": [], "attempts": 0,
                                          "release_review": True,
                                          "release_review_wave": wave_idx}
    tmp = led_path + ".tmp"
    json.dump(led, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, led_path)

def release_review_prompt(pkt, worktree, run_id, wave_idx, model, effort):
    """Reviewer prompt: verdict JSON plus a provenance echo that must match
    the controlled dispatch record (checked by verdict_check.py)."""
    return ("Task: %s\n"
            "You are the release-gate Reviewer (hard-pinned %s/%s). Work ONLY "
            "inside %s.\n"
            "goal: %s\nauthorized_paths: %s\nacceptance: %s\nconstraints: %s\n"
            "Write your machine-parseable verdict to data/reports/%s/report.json "
            'as {"packet_id","verdict","findings","provenance":'
            '{"run_id","model","effort","wave"}} with provenance.run_id=%s, '
            "provenance.model=%s, provenance.effort=%s, provenance.wave=%d. "
            "Provenance must echo the controlled dispatch record exactly; "
            "mismatches fail closed. You cannot release anything: your result "
            "returns to SOL_ADJUDICATE. Return 1 line conclusion + report path.\n"
            % (task_name(pkt), model, effort,
               worktree, pkt["goal"], json.dumps(pkt["authorized_paths"]),
               json.dumps(pkt["acceptance"]), json.dumps(pkt.get("constraints", [])),
               pkt["packet_id"], run_id, model, effort, wave_idx))

def dispatch_release_review(wave_idx, dry_run):
    """Explicit release-review entry: one hard-pinned reviewer per wave.
    Pin errors fail closed before dispatch; repeated calls for the same wave
    are idempotent no-ops. The reviewer result (pass or fail) always returns
    to SOL_ADJUDICATE -- this route can never release directly."""
    release_overrides, model, effort = release_review_pins()
    existing = load_release_review_record(wave_idx)
    if existing:
        print("release review already dispatched for wave %d (run_id %s); "
              "idempotent skip" % (wave_idx, existing.get("run_id")))
        return 0
    pids = wave_packets(wave_idx)
    pid, pkt = release_review_packet(wave_idx, pids)
    claimed_run_id = new_run_id(pid, packet_attempt(pid))
    if not dry_run:
        claimed, record = claim_release_review_launch(
            wave_idx, pid, claimed_run_id, model, effort)
        if not claimed:
            print("release review already owned for wave %d (run_id %s); "
                  "idempotent skip" % (wave_idx, record.get("run_id")))
            return 0
    if not dry_run:
        release_review_ledger_seed(pid, wave_idx)
    try:
        run_id = dispatch_single([pid], dry_run, role=RELEASE_REVIEW_ROLE,
                                 wave_idx=wave_idx,
                                 pinned=(release_overrides, RELEASE_REVIEW_SANDBOX,
                                         model, effort),
                                 mode="release_review",
                                 prompt_builder=lambda p_, wt_, rid_:
                                     release_review_prompt(
                                         p_, wt_, rid_, wave_idx, model, effort),
                                 run_id_overrides=({pid: claimed_run_id}
                                                   if not dry_run else None))
    except BaseException as exc:
        if not dry_run:
            set_release_review_status(wave_idx, claimed_run_id, "launch_failed",
                                      error="%s: %s" % (type(exc).__name__, exc))
        raise
    if not dry_run:
        set_release_review_status(wave_idx, claimed_run_id, "dispatched")
    return 0

def main():
    ap = argparse.ArgumentParser(description="LOOP-F2 dispatcher (single + CSV batch)")
    ap.add_argument("--wave", type=int, default=0, help="wave index in dag.json")
    ap.add_argument("--mode", choices=["single", "csv"], default="single")
    ap.add_argument("--packet", action="append", help="dispatch specific packet id(s)")
    ap.add_argument("--role", default="worker",
                    choices=["worker", "reviewer", "verifier", "duty_officer"],
                    help="agent role TOML to pin model/effort/sandbox from")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="skip DISPATCHABLE state check")
    ap.add_argument("--release-review", action="store_true",
                    help="explicit release-review entry: one hard-pinned K3 "
                         "reviewer per wave, idempotent, never releases")
    args = ap.parse_args()
    if args.release_review:
        try:
            return dispatch_release_review(args.wave, args.dry_run)
        except BirthThrottleError as exc:
            print("dispatch throttled: %s" % exc, file=sys.stderr)
            return 3
    pids = args.packet or wave_packets(args.wave)
    if not args.force:
        pids = dispatchable(pids)
    if not pids:
        print("nothing dispatchable"); return 2
    _, _, selected_model, _ = agent_overrides(args.role)
    budget_block = sol_budget_block(args.role)
    if budget_block:
        for pid in pids:
            append_event(pid, "sol_budget_blocked", {**budget_block,
                         "role": args.role, "model": selected_model,
                         "force_requested": args.force})
        print("dispatch blocked: %s (%s)" %
              (budget_block["reason"], budget_block["report"]), file=sys.stderr)
        return 3
    try:
        if args.mode == "csv":
            dispatch_csv(pids, args.wave, args.dry_run, args.role)
        else:
            dispatch_single(pids, args.dry_run, args.role, args.wave)
    except BirthThrottleError as exc:
        print("dispatch throttled: %s" % exc, file=sys.stderr)
        return 3
    return 0

if __name__ == "__main__":
    sys.exit(main())
