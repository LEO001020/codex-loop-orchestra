# ============================================================================
# test_model_token_share.py — Unit tests for metering/model_token_share.py
# Cases: normal share (sol well under 20% -> OK, correct bucket totals and
#        share metrics, reviewer + maintenance bucketed separately); soft
#        breach (share_effective > 20% -> WARNING printed, rc 0); hard breach
#        (share_effective > 25% -> BLOCK printed, rc 1 fail-visible); plus
#        boundary: missing sessions dir -> rc 2; integration: P1-5
#        usage_reconcile --rollout-dir embeds the token_share block and turns
#        a budget breach into a reconciliation discrepancy.
# ============================================================================
import json
import tomllib

from tests.conftest import PKG, PY

METER = PKG / "metering" / "model_token_share.py"
NOW = 1_800_000_000.0


def rollout(dir_, name, model, totals, ts=NOW - 3600, prompt="work packet"):
    """Write one minimal rollout JSONL: turn_context (model) + token_count."""
    d = dir_ / "2026" / "08" / "10"
    d.mkdir(parents=True, exist_ok=True)
    inp, cached, out = totals
    lines = [
        json.dumps({"timestamp": ts, "type": "turn_context",
                    "payload": {"model": model, "cwd": "/x"}}),
        json.dumps({"timestamp": ts, "type": "event_msg",
                    "payload": {"type": "user_message", "message": prompt}}),
        json.dumps({"timestamp": ts + 60, "type": "event_msg",
                    "payload": {"type": "token_count", "info": {
                        "last_token_usage": {
                            "input_tokens": inp, "cached_input_tokens": cached,
                            "output_tokens": out, "reasoning_output_tokens": 0,
                            "total_tokens": inp + out},
                        "total_token_usage": {
                            "input_tokens": inp, "cached_input_tokens": cached,
                            "output_tokens": out, "reasoning_output_tokens": 0,
                            "total_tokens": inp + out}}}}),
    ]
    (d / ("rollout-%s.jsonl" % name)).write_text("\n".join(lines) + "\n")


def attributed_rollout(dir_, agent_id, role, model, totals, events, ts=NOW - 3600):
    d = dir_ / "2026" / "08" / "10"
    d.mkdir(parents=True, exist_ok=True)
    inp, cached, out = totals
    lines = [
        json.dumps({"timestamp": ts, "type": "session_meta", "payload": {
            "session_id": "root-1", "id": agent_id,
            "parent_thread_id": "root-1", "thread_source": "subagent",
            "agent_role": role, "cwd": "/x"}}),
        json.dumps({"timestamp": ts, "type": "turn_context",
                    "payload": {"model": model, "effort": "high", "cwd": "/x"}}),
        json.dumps({"timestamp": ts + 1, "type": "event_msg", "payload": {
            "type": "token_count", "info": {"last_token_usage": {
                "input_tokens": inp, "cached_input_tokens": cached,
                "output_tokens": out, "reasoning_output_tokens": 0,
                "total_tokens": inp + out}}}}),
    ]
    (d / ("rollout-prefix-%s.jsonl" % agent_id)).write_text("\n".join(lines) + "\n")
    with events.open("a") as handle:
        handle.write(json.dumps({"event": "SubagentStartRecovered",
                                  "agent_id": agent_id, "session_id": "root-1",
                                  "agent_role": role}) + "\n")


def run_meter(loop, sessions, out):
    return loop.run([PY, METER, "--sessions-dir", sessions, "--output", out,
                     "--events", loop.data / "events.ndjson",
                     "--now", NOW])


def test_replayed_cumulative_snapshot_is_counted_once(loop, tmp_path):
    sessions = tmp_path / "sessions" / "2026" / "08" / "10"
    sessions.mkdir(parents=True)
    meta = {"timestamp": NOW - 100, "type": "session_meta", "payload": {
        "session_id": "same-root", "id": "same-root", "cwd": "/x"}}
    turn = {"timestamp": NOW - 90, "type": "turn_context", "payload": {
        "model": "gpt-5.6", "cwd": "/x"}}
    usage = {"input_tokens": 1000, "cached_input_tokens": 800,
             "output_tokens": 100, "reasoning_output_tokens": 0,
             "total_tokens": 1100}
    token = {"timestamp": NOW - 80, "type": "event_msg", "payload": {
        "type": "token_count", "info": {"last_token_usage": usage,
                                             "total_token_usage": usage}}}
    for suffix in ("original", "resume-copy"):
        (sessions / ("rollout-%s.jsonl" % suffix)).write_text(
            "\n".join(json.dumps(row) for row in (meta, turn, token)) + "\n")
    out = tmp_path / "share.json"
    p = run_meter(loop, tmp_path / "sessions", out)
    assert p.returncode == 1  # all production usage is Sol, but only once
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["records"] == 1
    assert report["windows"]["cumulative_since_f2"]["sol_kpi"]["total_tokens"] == 1100


# ---- normal: sol share comfortably inside budget ------------------------------

def test_normal_share_is_ok_with_correct_buckets(loop, tmp_path):
    sess = tmp_path / "sessions"
    rollout(sess, "sol-1", "gpt-5.6", (1000, 800, 100))       # sol: eff 300
    rollout(sess, "w-1", "gpt-5.6-terra", (4000, 0, 800))  # worker
    rollout(sess, "w-2", "gpt-5.6-terra", (3000, 0, 700))  # worker
    rollout(sess, "v-1", "provider-b/independent-reviewer", (2000, 0, 300))         # verifier
    rollout(sess, "smoke-1", "gpt-5.6", (9000, 0, 900),       # maintenance
            prompt="smoke: reply exactly OK (worker)")
    out = tmp_path / "share.json"
    p = run_meter(loop, sess, out)
    assert p.returncode == 0, p.stdout + p.stderr
    rpt = json.loads(out.read_text(encoding="utf-8"))
    cum = rpt["windows"]["cumulative_since_f2"]
    assert cum["status"] == "OK"
    assert "WARNING" not in p.stdout and "BLOCK" not in p.stdout
    # buckets: sol / worker / verifier / maintenance all separated
    assert cum["buckets"]["sol"]["total_tokens"] == 1100
    assert cum["buckets"]["worker"]["total_tokens"] == 8500
    assert cum["buckets"]["verifier"]["total_tokens"] == 2300
    assert cum["buckets"]["maintenance"]["total_tokens"] == 9900
    # maintenance excluded from the production denominator
    assert cum["share_total"] == round(1100 / (1100 + 8500 + 2300), 4)
    # share_effective discounts sol's cached input (ruling 2 direction)
    assert cum["share_effective"] == round(300 / (11900 - 800), 4)
    assert cum["share_output"] == round(100 / (100 + 800 + 700 + 300), 4)
    assert rpt["thresholds"]["primary_metric"] == "share_effective"
    # per-task window has one entry per session
    assert len(rpt["per_task"]) == 5
    assert rpt["windows"]["rolling_5h"] == cum
    assert "rolling_5h" in rpt["largest_records_by_window"]
    assert "rolling_5h" in rpt["context_pressure"]


def test_maintenance_is_turn_scoped_not_rollout_scoped(loop, tmp_path):
    sess = tmp_path / "sessions"
    d = sess / "2026" / "08" / "10"
    d.mkdir(parents=True)
    records = []
    for offset, prompt, totals in (
            (0, "smoke: reply exactly OK", (900, 0, 100)),
            (60, "production work packet", (1800, 0, 200))):
        inp, cached, out_tokens = totals
        ts = NOW - 3600 + offset
        records.extend([
            {"timestamp": ts, "type": "turn_context",
             "payload": {"model": "gpt-5.6-terra", "cwd": "/x"}},
            {"timestamp": ts, "type": "event_msg",
             "payload": {"type": "user_message", "message": prompt}},
            {"timestamp": ts + 1, "type": "event_msg", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": inp, "cached_input_tokens": cached,
                    "output_tokens": out_tokens, "reasoning_output_tokens": 0,
                    "total_tokens": inp + out_tokens}}}},
        ])
    (d / "rollout-two-turns.jsonl").write_text(
        "\n".join(json.dumps(row) for row in records) + "\n")
    out = tmp_path / "share.json"
    p = run_meter(loop, sess, out)
    assert p.returncode == 0, p.stdout + p.stderr
    cum = json.loads(out.read_text(encoding="utf-8"))["windows"]["cumulative_since_f2"]
    assert cum["buckets"]["maintenance"]["total_tokens"] == 1000
    assert cum["buckets"]["worker"]["total_tokens"] == 2000


# ---- soft breach: > 20% -> WARNING, rc 0 --------------------------------------

def test_share_above_20_percent_emits_warning(loop, tmp_path):
    sess = tmp_path / "sessions"
    rollout(sess, "sol-1", "gpt-5.6", (2000, 0, 300))         # sol eff 2300
    rollout(sess, "w-1", "gpt-5.6-terra", (7000, 0, 700))  # total eff 10000
    out = tmp_path / "share.json"
    p = run_meter(loop, sess, out)
    assert p.returncode == 0                    # warning is not a block
    assert "WARNING" in p.stdout
    assert "exceeds 20% budget" in p.stdout
    rpt = json.loads(out.read_text(encoding="utf-8"))
    assert rpt["windows"]["cumulative_since_f2"]["status"] == "WARNING"
    assert rpt["windows"]["cumulative_since_f2"]["share_effective"] == 0.23


def test_reviewer_is_separate_but_included_in_sol_kpi(loop, tmp_path):
    sess = tmp_path / "sessions"
    attributed_rollout(sess, "reviewer-a1", "reviewer", "gpt-5.6",
                       (1000, 0, 100), loop.data / "events.ndjson")
    rollout(sess, "w-1", "gpt-5.6-terra", (3000, 0, 300))
    out = tmp_path / "share.json"
    p = run_meter(loop, sess, out)
    assert p.returncode == 0, p.stdout + p.stderr
    cum = json.loads(out.read_text(encoding="utf-8"))["windows"]["cumulative_since_f2"]
    assert cum["buckets"]["reviewer"]["total_tokens"] == 1100
    assert cum["buckets"].get("sol", {}).get("total_tokens", 0) == 0
    assert cum["sol_kpi"]["total_tokens"] == 1100
    assert cum["share_total"] == round(1100 / 4400, 4)
    assert cum["sol_kpi_components"] == ["sol", "reviewer"]


def test_k3_reviewer_stays_separate_and_is_not_charged_to_sol_kpi(loop, tmp_path):
    sess = tmp_path / "sessions"
    attributed_rollout(sess, "reviewer-k3", "reviewer", "provider-b/independent-reviewer",
                       (1000, 0, 100), loop.data / "events.ndjson")
    rollout(sess, "w-1", "gpt-5.6-terra", (3000, 0, 300))
    out = tmp_path / "share.json"
    p = run_meter(loop, sess, out)
    assert p.returncode == 0, p.stdout + p.stderr
    cum = json.loads(out.read_text(encoding="utf-8"))["windows"]["cumulative_since_f2"]
    assert cum["buckets"]["reviewer"]["total_tokens"] == 1100
    assert cum["sol_kpi"]["total_tokens"] == 0
    assert cum["sol_kpi_components"] == ["sol"]


# ---- hard breach: > 25% -> BLOCK, rc 1 (fail-visible) --------------------------

def test_share_above_25_percent_emits_block_and_rc1(loop, tmp_path):
    sess = tmp_path / "sessions"
    rollout(sess, "sol-1", "gpt-5.6", (3000, 0, 500))         # sol eff 3500
    rollout(sess, "w-1", "gpt-5.6-terra", (6000, 0, 500))  # total eff 10000
    out = tmp_path / "share.json"
    p = run_meter(loop, sess, out)
    assert p.returncode == 1                    # fail-visible hard cap
    assert "BLOCK" in p.stdout
    assert "refuse new non-planning/adjudication Sol work" in p.stdout
    rpt = json.loads(out.read_text(encoding="utf-8"))
    assert rpt["windows"]["cumulative_since_f2"]["status"] == "BLOCK"
    assert rpt["windows"]["cumulative_since_f2"]["share_effective"] == 0.35


# ---- boundary: sessions dir missing -> usage error -----------------------------

def test_missing_sessions_dir_is_usage_error(loop, tmp_path):
    p = run_meter(loop, tmp_path / "nope", tmp_path / "share.json")
    assert p.returncode == 2


# ---- P1-5 integration: weekly reconciliation carries the token share -----------

def test_usage_reconcile_embeds_token_share_and_flags_breach(loop, tmp_path):
    sess = tmp_path / "sessions"
    rollout(sess, "sol-1", "gpt-5.6", (3000, 0, 500))
    rollout(sess, "w-1", "gpt-5.6-terra", (6000, 0, 500))
    annotated = tmp_path / "annotated.jsonl"
    annotated.write_text("")
    out = tmp_path / "reconcile.json"
    p = loop.run([PY, PKG / "metering" / "usage_reconcile.py",
                  "--events", loop.data / "events.ndjson",
                  "--sessions", annotated, "--output", out,
                  "--rollout-dir", sess])
    assert p.returncode == 1, p.stdout + p.stderr   # breach = discrepancy
    rpt = json.loads(out.read_text(encoding="utf-8"))
    assert rpt["token_share"]["windows"]["cumulative_since_f2"]["status"] == "BLOCK"
    kinds = {d["type"] for d in rpt["discrepancies"]}
    assert "sol_share_block" in kinds


def test_reviewer_observed_on_wrong_physical_model_is_routing_anomaly(loop, tmp_path):
    loop.data.mkdir(parents=True, exist_ok=True)
    (loop.data / "events.ndjson").write_text(json.dumps({
        "event": "SubagentStartRecovered", "agent_id": "reviewer-a1",
        "turn_id": "turn-1", "session_id": "root-1",
        "agent_role": "reviewer", "model": "gpt-5.6-terra",
        "effort": "max"}) + "\n", encoding="utf-8")
    annotated = tmp_path / "annotated.jsonl"
    annotated.write_text("", encoding="utf-8")
    out = tmp_path / "reconcile.json"
    p = loop.run([PY, PKG / "metering" / "usage_reconcile.py",
                  "--events", loop.data / "events.ndjson",
                  "--sessions", annotated, "--output", out])
    assert p.returncode == 1, p.stdout + p.stderr
    rpt = json.loads(out.read_text(encoding="utf-8"))
    anomalies = [d for d in rpt["discrepancies"]
                 if d["type"] == "routing_anomaly"
                 and d["agent_role"] == "reviewer"
                 and "model_observed" in d
                 and d["severity"] == "warn"]
    assert len(anomalies) == 1
    assert anomalies[0]["severity"] == "warn"
    assert anomalies[0]["model_observed"] == "gpt-5.6-terra"
    with (PKG / "config" / "orchestration_policy_v2.toml").open("rb") as handle:
        expected = tomllib.load(handle)["models"]["k3_model"]
    assert anomalies[0]["model_expected"] == expected
