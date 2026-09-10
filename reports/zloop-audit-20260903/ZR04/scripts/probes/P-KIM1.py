#!/usr/bin/env python3
"""P-KIM1 -- Kimi dual-lane probe (VOL-20 section 5 P-KIM1; VOL-15 section 3 lanes).

K1 server lane : `kimi web --no-open` -> poll /api/v1/healthz (no auth, <=40s)
                 -> bearer token parsed from the server's startup stdout
                 (hashed, NEVER stored/printed) -> GET /openapi.json +
                 /asyncapi.json (sha256 + path/channel counts) -> POST
                 /api/v1/sessions with metadata.cwd = temp dir -> model set
                 via POST /api/v1/sessions/{id}/profile -> one trivial prompt
                 via POST .../prompts -> poll session for terminal state
                 (last_turn_reason in {completed, cancelled, failed}, <=120s)
                 -> GET .../messages (final assistant message?) -> abort
                 endpoints -> taskkill the server tree, verify port free.
K2 CLI lane    : `kimi -p "Reply with exactly: OK" --output-format
                 stream-json` in a temp dir (<=180s); judge terminal completion
                 per I27 on the stdout JSONL.

Output: artifacts/probes/P-KIM1.json
Safety : bearer tokens / credentials are never printed or stored -- only
         sha256 prefixes. No interactive logins are attempted. Every network
         call is timeboxed. Temp files live under the system temp dir only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib  # py>=3.11
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

REPO = Path(__file__).resolve().parents[2]
ART = REPO / "artifacts" / "probes" / "P-KIM1.json"
KIMI_HOME = Path.home() / ".kimi-code"
KIMI_EXE = KIMI_HOME / "bin" / "kimi.exe"
DEFAULT_PORT = 58627
HEALTHZ_PATH = "/api/v1/healthz"
PROMPT_TEXT = "Reply with exactly: OK"
TERMINAL_REASONS = {"completed", "cancelled", "failed"}

KNOWN_SECRETS: set[str] = set()  # raw values; used only to scrub, never written
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback only


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def sha12(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:12]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def scrub(text: str) -> str:
    """Remove any known secret values / bearer-like strings from text."""
    for sec in KNOWN_SECRETS:
        if sec and sec in text:
            text = text.replace(sec, "<REDACTED_TOKEN>")
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9_\-\.=]{16,}", "<REDACTED_BEARER>", text)
    text = re.sub(r"(?i)([?&#]token=)[A-Za-z0-9_\-\.]+", r"\1<REDACTED>", text)
    return text


def sh(cmd: list[str], timeout: int = 60, cwd: str | None = None) -> dict:
    """Run a command with direct argv (kimi is a real .exe), timeboxed."""
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd)
        return {
            "cmd": cmd,
            "rc": p.returncode,
            "elapsed_s": round(time.time() - t0, 2),
            "out": scrub(p.stdout.decode("utf-8", "replace").strip())[:4000],
            "err": scrub(p.stderr.decode("utf-8", "replace").strip())[:1500],
        }
    except Exception as e:  # noqa: BLE001
        return {"cmd": cmd, "rc": None, "error": scrub(repr(e))[:300]}


def http(method: str, base: str, path: str, body: dict | None = None,
         token: str | None = None, timeout: int = 15) -> dict:
    """Timeboxed HTTP call. Returns status / parsed json / raw body bytes."""
    req = urllib.request.Request(base + path, method=method,
                                  data=json.dumps(body).encode() if body is not None else None)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    t0 = time.time()
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read()
            return {"status": r.status, "elapsed_s": round(time.time() - t0, 2), "raw": raw,
                    "json": _try_json(raw)}
    except urllib.error.HTTPError as e:
        raw = e.read()
        return {"status": e.code, "elapsed_s": round(time.time() - t0, 2), "raw": raw,
                "json": _try_json(raw)}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "elapsed_s": round(time.time() - t0, 2), "raw": b"",
                "json": None, "error": scrub(repr(e))[:250]}


def _try_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def _decode_local(b: bytes) -> str:
    """Decode console tool output using the OEM codepage, then UTF-8."""
    for enc in ("oem", "utf-8"):
        try:
            return b.decode(enc, "replace")
        except LookupError:
            continue
    return b.decode("utf-8", "replace")


def port_listening(port: int) -> bool:
    """True if some process LISTENs on 127.0.0.1:<port> (netstat)."""
    try:
        p = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001
        return False
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        if "LISTENING" in line.upper() and f"127.0.0.1:{port}" in line:
            return True
    return False


# --------------------------------------------------------------------------- #
# step 1+2: version, credentials, config (redacted)
# --------------------------------------------------------------------------- #
def probe_version() -> dict:
    v = sh([str(KIMI_EXE), "--version"], timeout=30)
    m = re.search(r"(\d+\.\d+\.\d+)", v.get("out", "") or "")
    return {"command_result": v, "version": m.group(1) if m else None}


def probe_credentials() -> dict:
    cred_dir = KIMI_HOME / "credentials"
    names: list[str] = []
    exists = cred_dir.is_dir()
    if exists:
        try:
            names = sorted(x.name for x in cred_dir.iterdir())
        except OSError:
            names = []
    out = {"dir": "~/.kimi-code/credentials", "exists": exists,
           "non_empty": bool(names), "names": names,
           "contents_read": False,  # names only, by design
           "note": "credential file contents intentionally NOT read"}
    return out


SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|credential|"
                           r"client[_-]?secret|oauth.*key|^key$)", re.I)


def _redact_value(v) -> object:
    if isinstance(v, str):
        return {"redacted": True, "sha256_12": sha12(v), "length": len(v)}
    return {"redacted": True, "sha256_12": sha12(json.dumps(v, default=str))}


def _redact_tree(node):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if SECRET_KEY_RE.search(str(k)) and not isinstance(v, (dict, list)):
                out[k] = _redact_value(v)
            elif isinstance(v, (dict, list)):
                out[k] = _redact_tree(v)
            else:
                out[k] = v
        return out
    if isinstance(node, list):
        return [_redact_tree(x) for x in node]
    return node


def probe_config() -> dict:
    cfg_path = KIMI_HOME / "config.toml"
    if not cfg_path.exists():
        return {"exists": False, "path": "~/.kimi-code/config.toml"}
    raw = cfg_path.read_bytes()
    if tomllib is None:
        return {"exists": True, "error": "tomllib unavailable"}
    try:
        cfg = tomllib.loads(raw.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"exists": True, "error": scrub(repr(e))[:200]}

    default_model = cfg.get("default_model")
    providers_raw = (cfg.get("providers") or {})
    providers = []
    for name, pv in providers_raw.items():
        if not isinstance(pv, dict):
            continue
        entry = {"name": name, "keys": sorted(pv.keys())}
        if "type" in pv:
            entry["type"] = pv["type"]
        if "base_url" in pv:
            entry["base_url"] = pv["base_url"]  # not a secret
        if "api_key" in pv:
            entry["api_key"] = _redact_value(pv["api_key"])
        oauth = pv.get("oauth")
        if isinstance(oauth, dict):
            entry["oauth"] = {"keys": sorted(oauth.keys()),
                              "storage": oauth.get("storage")}
            if "key" in oauth:
                entry["oauth"]["key"] = _redact_value(oauth["key"])
        providers.append(entry)

    # resolve which provider the default model routes to
    resolved_provider = None
    models = cfg.get("models") or {}
    if isinstance(default_model, str) and isinstance(models, dict):
        m = models.get(default_model)
        if isinstance(m, dict):
            resolved_provider = m.get("provider")

    auth_mode = None
    for p in providers:
        if p["name"] == resolved_provider:
            if "oauth" in p and p["name"].startswith("managed:"):
                auth_mode = f"managed provider '{p['name']}' (type={p.get('type')}), oauth storage={p['oauth'].get('storage')}, api_key present={bool(p.get('api_key'))}"
            else:
                auth_mode = f"plain provider '{p['name']}' (type={p.get('type')}), api_key present={bool(p.get('api_key'))}"
    return {"exists": True, "path": "~/.kimi-code/config.toml",
            "default_model": default_model,
            "default_permission_mode": cfg.get("default_permission_mode"),
            "resolved_provider_for_default_model": resolved_provider,
            "auth_mode": auth_mode,
            "providers": _redact_tree(providers),
            "note": "secret-looking values redacted to sha256_12; base_url/type kept (non-secret)"}


# --------------------------------------------------------------------------- #
# step 3: K1 server lane
# --------------------------------------------------------------------------- #
def run_k1(tmp_root: Path) -> dict:
    k1: dict = {"lane": "K1 kimi web server", "status": "BLOCKED", "reason": None, "steps": {}}

    # --- kimi web --help -------------------------------------------------- #
    help_r = sh([str(KIMI_EXE), "web", "--help"], timeout=30)
    help_txt = (help_r.get("out", "") + "\n" + (help_r.get("err") or ""))
    flags = sorted(set(re.findall(r"--[A-Za-z][A-Za-z0-9-]*", help_txt)))
    k1["steps"]["web_help"] = {
        "rc": help_r.get("rc"),
        "flags": flags,
        "flag_presence": {f: (f in flags) for f in
                          ["--port", "--host", "--token", "--print-token", "--open",
                           "--no-open", "--dangerous-bypass-auth", "--allowed-host",
                           "--insecure-no-tls", "--log-level", "--debug-endpoints"]},
        "help_text_scrubbed": scrub(help_txt)[:2500],
    }

    # --- pre-flight: is the default port already taken? -------------------- #
    pre_occupied = port_listening(DEFAULT_PORT)
    k1["steps"]["pre_flight"] = {"default_port": DEFAULT_PORT, "already_listening": pre_occupied}

    # --- start server (stdout/stderr -> temp files) ------------------------ #
    srv_dir = tmp_root / "k1-server"
    srv_dir.mkdir()
    out_path, err_path = srv_dir / "stdout.txt", srv_dir / "stderr.txt"
    out_f = open(out_path, "wb")
    err_f = open(err_path, "wb")
    proc = subprocess.Popen([str(KIMI_EXE), "web", "--no-open"],
                            stdout=out_f, stderr=err_f, stdin=subprocess.DEVNULL)
    k1["server_pid"] = proc.pid
    k1["cleanup"] = {}
    port, token, token_src = None, None, None

    try:
        # --- poll healthz (no auth) up to 40s; parse port+token from stdout - #
        # NOTE: the HTTP listener can answer /healthz BEFORE the startup banner
        # (with the token) is flushed to the redirected stdout file, so after
        # healthz turns OK we keep waiting up to BANNER_GRACE_S for the banner.
        BANNER_GRACE_S = 15.0
        t0 = time.time()
        port, token, token_src = None, None, None
        healthz, healthz_at = None, None
        deadline = t0 + 40.0
        while time.time() < deadline and proc.poll() is None:
            so = out_path.read_text(encoding="utf-8", errors="replace")
            se = err_path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"http://127\.0\.0\.1:(\d+)/#token=([A-Za-z0-9._\-]+)",
                          so + "\n" + se)
            if m and port is None:
                port, token, token_src = int(m.group(1)), m.group(2), "stdout_local_url"
            if token is None and port is None:
                m2 = re.search(r"^\s*Token:\s*([A-Za-z0-9._\-]{16,})\s*$",
                               so + "\n" + se, re.M)
                if m2:
                    port, token, token_src = DEFAULT_PORT, m2.group(1), "stdout_token_line"
            if healthz_at is None:
                h = http("GET", f"http://127.0.0.1:{port if port else DEFAULT_PORT}",
                         HEALTHZ_PATH, token=None, timeout=4)
                if h["status"] == 200:
                    healthz, healthz_at = h, time.time()
            if token is not None and healthz_at is not None:
                break
            if healthz_at is not None and time.time() - healthz_at > BANNER_GRACE_S:
                break  # server up, but banner never flushed
            time.sleep(0.5)
        secs = round((healthz_at or time.time()) - t0, 2)

        k1["steps"]["server_start"] = {
            "elapsed_s_to_healthz": round(healthz_at - t0, 2) if healthz_at else None,
            "port": port,
            "port_drifted_from_default": (port is not None and port != DEFAULT_PORT),
            "healthz_no_auth": {
                "status": healthz["status"] if healthz else None,
                "body": (healthz["json"] if healthz and healthz.get("json") is not None
                         else (healthz.get("raw", b"")[:200].decode("utf-8", "replace")
                               if healthz else None)),
            },
        }
        if healthz_at is None:
            k1["reason"] = (f"kimi web did not answer /api/v1/healthz within 40s "
                            f"(last status={healthz['status'] if healthz else None}; "
                            f"server rc={proc.poll()})")
            k1["steps"]["server_start"]["stderr_tail"] = scrub(
                err_path.read_text(encoding="utf-8", errors="replace"))[-1500:]
            return k1

        # --- token -------------------------------------------------------- #
        token_note = None
        if not token:
            # fallback: kimi 0.28.1 persists the server token at
            # ~/.kimi-code/server.token (shared across instances); use it only
            # if the startup banner never flushed.
            tok_file = KIMI_HOME / "server.token"
            if tok_file.exists():
                fval = tok_file.read_text(encoding="utf-8", errors="replace").strip()
                if fval:
                    token, token_src = fval, "persistent_server_token_file"
                    token_note = ("startup banner not flushed to stdout within "
                                  f"{BANNER_GRACE_S:.0f}s after healthz OK; recovered "
                                  "the bearer token from ~/.kimi-code/server.token")
        if not token:
            k1["reason"] = ("healthz OK but bearer token not recoverable headlessly: "
                            "parsed startup stdout+stderr for 'http://127.0.0.1:<port>/#token=...' "
                            "and a 'Token:' line for 15s after healthz OK; no persistent "
                            "server.token file either. REST auth (openapi) untested.")
            return k1
        if port is None:
            port = DEFAULT_PORT  # healthz already confirmed on this port
        KNOWN_SECRETS.add(token)
        tok_rec = {"found": True, "source": token_src, "length": len(token),
                   "sha256_12": sha12(token), "stored": False}
        if token_note:
            tok_rec["note"] = token_note
        # cross-check: persistent token file (VOL-15 expects memory/OS-secret only)
        tok_file = KIMI_HOME / "server.token"
        if tok_file.exists():
            fval = tok_file.read_text(encoding="utf-8", errors="replace").strip()
            if fval:
                KNOWN_SECRETS.add(fval)
                tok_rec["persistent_file"] = {
                    "path": "~/.kimi-code/server.token", "size": tok_file.stat().st_size,
                    "matches_recovered_token": fval == token,
                    "sha256_12": sha12(fval)}
        k1["token"] = tok_rec

        base = f"http://127.0.0.1:{port}"
        hdr = lambda r: {"status": r.get("status"), "elapsed_s": r.get("elapsed_s"),
                         "code": (r.get("json") or {}).get("code")
                         if isinstance(r.get("json"), dict) else None,
                         "error": r.get("error")}

        # --- openapi / asyncapi -------------------------------------------- #
        oa = http("GET", base, "/openapi.json", token=token, timeout=20)
        spec = oa["json"] if isinstance(oa["json"], dict) else None
        paths = spec.get("paths", {}) if spec else {}
        k1["steps"]["openapi"] = {
            **hdr(oa), "sha256": sha256_bytes(oa["raw"]) if oa.get("raw") else None,
            "body_len": len(oa.get("raw") or b""),
            "top_level_path_count": len(paths),
            "session_lifecycle_paths": {p: sorted(m.upper() for m in ops)
                                        for p, ops in paths.items()
                                        if "/api/v1/sessions" in p or p in
                                        ("/api/v1/healthz", "/api/v1/meta", "/api/v1/models")},
            "auth_enforced_on_openapi": None}
        noauth = http("GET", base, "/openapi.json", token=None, timeout=10)
        k1["steps"]["openapi"]["auth_enforced_on_openapi"] = (noauth["status"] == 401)
        aa = http("GET", base, "/asyncapi.json", token=token, timeout=20)
        aspec = aa["json"] if isinstance(aa["json"], dict) else None
        ws_msgs, ev_types = None, None
        if aspec:
            ws_msgs = sorted(((aspec.get("components") or {}).get("messages") or {}).keys())
            se_msg = ((aspec.get("components") or {}).get("messages") or {}).get("session_event")
            if se_msg:
                ev_types = sorted(set(re.findall(
                    r'"const"\s*:\s*"([a-z_]+\.[a-z_.]+)"', json.dumps(se_msg))))
        k1["steps"]["asyncapi"] = {
            **hdr(aa), "sha256": sha256_bytes(aa["raw"]) if aa.get("raw") else None,
            "body_len": len(aa.get("raw") or b""),
            "channel_count": len((aspec or {}).get("channels", {})) if aspec else None,
            "ws_message_names": ws_msgs,
            "session_event_types": ev_types}

        # --- pick a model --------------------------------------------------- #
        mo = http("GET", base, "/api/v1/models", token=token, timeout=15)
        models = ((mo["json"] or {}).get("data") or {}).get("items", []) \
            if isinstance(mo["json"], dict) else []
        model_ids = [m.get("model") for m in models if isinstance(m, dict) and m.get("model")]
        cfg_default = probe_config().get("default_model")
        model = cfg_default if cfg_default in model_ids else (model_ids[0] if model_ids else None)
        k1["steps"]["model_selection"] = {
            **hdr(mo), "model_count": len(model_ids),
            "config_default_model": cfg_default,
            "default_model_listed": cfg_default in model_ids,
            "chosen_model": model}

        # --- session create (metadata.cwd = dedicated temp dir) --------------- #
        cwd_dir = tmp_root / "k1-cwd"
        cwd_dir.mkdir()
        cr = http("POST", base, "/api/v1/sessions",
                  {"title": "P-KIM1 probe", "metadata": {"cwd": str(cwd_dir)}},
                  token=token, timeout=60)
        crd = (cr["json"] or {}).get("data") or {} if isinstance(cr["json"], dict) else {}
        sid = crd.get("id")
        k1["steps"]["session_create"] = {
            **hdr(cr), "session_id": sid, "workspace_id": crd.get("workspace_id"),
            "metadata_echo": crd.get("metadata"),
            "agent_config_echo": crd.get("agent_config"),
            "elapsed_s": cr["elapsed_s"]}
        if not sid:
            k1["reason"] = f"session create failed: status={cr['status']} body={scrub(json.dumps(cr['json']))[:300]}"
            return k1

        # 0.28.1 finding: agent_config.model at create time is silently dropped;
        # the turn then fails with model.not_configured. Set it via /profile.
        pf = http("POST", base, f"/api/v1/sessions/{sid}/profile",
                  {"agent_config": {"model": model}}, token=token, timeout=30)
        pfd = (pf["json"] or {}).get("data") or {} if isinstance(pf["json"], dict) else {}
        k1["steps"]["profile_set_model"] = {
            **hdr(pf), "agent_config_echo": pfd.get("agent_config"),
            "why": "create-time agent_config.model observed dropped in 0.28.1"}

        # --- one trivial prompt ---------------------------------------------- #
        pr = http("POST", base, f"/api/v1/sessions/{sid}/prompts",
                  {"content": [{"type": "text", "text": PROMPT_TEXT}]},
                  token=token, timeout=30)
        prd = (pr["json"] or {}).get("data") or {} if isinstance(pr["json"], dict) else {}
        prompt_id = prd.get("prompt_id")
        k1["steps"]["prompt_submit"] = {
            **hdr(pr), "prompt_id": prompt_id, "status": prd.get("status"),
            "submitted_text": PROMPT_TEXT,
            "endpoint_declared_in_openapi":
                "/api/v1/sessions/{session_id}/prompts" in paths}

        # --- poll for terminal state (<=120s) --------------------------------- #
        t0 = time.time()
        term, last_snap = None, {}
        while time.time() - t0 < 120:
            time.sleep(2)
            gs = http("GET", base, f"/api/v1/sessions/{sid}", token=token, timeout=15)
            d = (gs["json"] or {}).get("data") or {} if isinstance(gs["json"], dict) else {}
            last_snap = d
            if not d.get("busy") and d.get("last_turn_reason") in TERMINAL_REASONS:
                term = d.get("last_turn_reason")
                break
            if gs["status"] is None and gs.get("error"):
                term = "poll_error"
                last_snap = {"error": gs["error"]}
                break
        k1["steps"]["terminal_poll"] = {
            "elapsed_s": round(time.time() - t0, 2), "last_turn_reason": term,
            "final_busy": last_snap.get("busy"),
            "final_pending_interaction": last_snap.get("pending_interaction"),
            "timeout_s": 120}

        tr = http("GET", base, f"/api/v1/sessions/{sid}/transcript", token=token, timeout=15)
        trd = (tr["json"] or {}).get("data") or {} if isinstance(tr["json"], dict) else {}
        turns = [i for i in trd.get("items", []) if isinstance(i, dict) and i.get("kind") == "turn"]
        k1["steps"]["transcript"] = {
            **hdr(tr), "turn_states": [{"turnId": t.get("turnId"), "state": t.get("state")}
                                      for t in turns]}

        # --- messages: final assistant message? -------------------------------- #
        ms = http("GET", base, f"/api/v1/sessions/{sid}/messages", token=token, timeout=15)
        items = ((ms["json"] or {}).get("data") or {}).get("items", []) \
            if isinstance(ms["json"], dict) else []
        roles = [m.get("role") for m in items if isinstance(m, dict)]
        assistant_texts = []
        for m in items:
            if isinstance(m, dict) and m.get("role") == "assistant":
                for b in (m.get("content") or []):
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                        assistant_texts.append(b["text"])
        k1["steps"]["messages"] = {
            **hdr(ms), "count": len(items), "roles": roles,
            "assistant_message_present": bool(assistant_texts),
            "assistant_text_first_200": scrub(assistant_texts[0])[:200] if assistant_texts else None,
            "injected_system_reminder_user_message": any(
                isinstance(m, dict) and m.get("role") == "user" and
                "<system-reminder>" in json.dumps(m.get("content") or "") for m in items)}

        # --- server-side event log (WS event stream evidence) ------------------- #
        # NOTE: observed on 0.28.1 that the per-session events file is NOT
        # visible at its final path while the server runs; it materializes at
        # server shutdown (flush). Try briefly live; the post-kill read below in
        # the finally-block is the reliable capture.
        ev_path = KIMI_HOME / "server" / "events" / f"session_{sid}.jsonl"
        ev = {"path": None, "waited_s": None}
        t_ev = time.time()
        while time.time() - t_ev < 3 and not ev_path.exists():
            time.sleep(0.5)
        ev["waited_s"] = round(time.time() - t_ev, 2)
        if ev_path.exists():
            ev.update(_read_event_log(ev_path))
            ev["captured"] = "live"
        else:
            ev["note"] = "events file not visible while server runs (flushes at shutdown); captured post-kill if possible"
        k1["steps"]["server_event_log"] = ev

        # --- abort endpoints (cleanup) ------------------------------------------- #
        ab1 = http("POST", base, f"/api/v1/sessions/{sid}/prompts/{prompt_id}:abort", {},
                   token=token, timeout=15) if prompt_id else {"status": None, "skipped": "no prompt_id"}
        ab2 = http("POST", base, f"/api/v1/sessions/{sid}:abort", {}, token=token, timeout=15)
        k1["steps"]["abort"] = {
            "prompt_level": {"path": f"/api/v1/sessions/{sid}/prompts/<prompt_id>:abort",
                             **hdr(ab1), "data": scrub(json.dumps((ab1.get("json") or {}).get("data")))[:200]
                             if isinstance(ab1.get("json"), dict) else None},
            "session_level": {"path": f"/api/v1/sessions/{sid}:abort",
                              **hdr(ab2), "data": scrub(json.dumps((ab2.get("json") or {}).get("data")))[:200]
                              if isinstance(ab2.get("json"), dict) else None},
            "session_abort_declared_in_openapi": any(":abort" in p for p in paths)}

        # --- K1 verdict ----------------------------------------------------------- #
        k1["turn_completed"] = (term == "completed")
        k1["final_assistant_message_present"] = bool(assistant_texts)
        if term in TERMINAL_REASONS:
            k1["status"] = "PASS" if (term == "completed" and assistant_texts) else "DEGRADED"
            k1["reason"] = None if term == "completed" else f"turn ended with last_turn_reason={term}"
        else:
            k1["status"] = "DEGRADED"
            k1["reason"] = f"no terminal state within 120s (last: busy={last_snap.get('busy')})"
        return k1
    finally:
        # --- cleanup: kill server tree, verify port free ---------------------------- #
        if proc.poll() is None:
            tk = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                capture_output=True, timeout=30)
            k1["cleanup"]["taskkill"] = {
                "rc": tk.returncode,
                "out": _decode_local(tk.stdout)[:300]}
        else:
            k1["cleanup"]["taskkill"] = {"rc": None, "note": "server already exited",
                                         "exit_code": proc.returncode}
        try:
            out_f.close()
            err_f.close()
        except Exception:  # noqa: BLE001
            pass
        for _ in range(10):
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        k1["cleanup"]["server_process_exited"] = (proc.poll() is not None)
        time.sleep(1.5)
        port_used = port_listening(port or DEFAULT_PORT)
        k1["cleanup"]["port_free_after_kill"] = (not port_used)
        k1["cleanup"]["port_checked"] = port or DEFAULT_PORT
        # the per-session events file materializes at server shutdown (observed
        # up to ~2.5 min after exit); bounded wait, best effort
        sid = (k1.get("steps", {}).get("session_create") or {}).get("session_id")
        ev_step = k1.get("steps", {}).get("server_event_log")
        if sid and not (ev_step or {}).get("event_types_in_order"):
            ev_path = KIMI_HOME / "server" / "events" / f"session_{sid}.jsonl"
            t_ev = time.time()
            while time.time() - t_ev < 60 and not ev_path.exists():
                time.sleep(1.0)
            if ev_path.exists():
                try:
                    ev = _read_event_log(ev_path)
                    ev["captured"] = "post_shutdown"
                    ev["waited_s"] = round(time.time() - t_ev, 2)
                    k1["steps"]["server_event_log"] = ev
                except OSError as e:  # noqa: BLE001
                    k1["steps"]["server_event_log"] = {
                        "note": f"events file unreadable post-shutdown: {e!r}"[:150]}
            else:
                k1["steps"]["server_event_log"] = {
                    "note": "events file did not materialize within 60s of "
                            "shutdown (observed to flush up to ~2.5 min after "
                            "server exit); WS event vocabulary captured from "
                            "asyncapi.json session_event instead"}


def _read_event_log(ev_path: Path) -> dict:
    """Parse a kimi server per-session event log (WS event stream mirror)."""
    out: dict = {"path": str(ev_path.relative_to(Path.home()))}
    types, turn_ended = [], None
    for line in ev_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            env = json.loads(line).get("envelope", {})
        except Exception:  # noqa: BLE001
            continue
        if env.get("type"):
            types.append(env["type"])
        if env.get("type") == "turn.ended":
            turn_ended = {"reason": (env.get("payload") or {}).get("reason"),
                          "durationMs": (env.get("payload") or {}).get("durationMs"),
                          "error_code": ((env.get("payload") or {}).get("error") or {}).get("code")}
    out.update({"event_types_in_order": types, "turn_ended": turn_ended,
                "event_count": len(types)})
    return out


# --------------------------------------------------------------------------- #
# step 4: K2 CLI lane
# --------------------------------------------------------------------------- #
def run_k2(tmp_root: Path) -> dict:
    k2: dict = {"lane": "K2 kimi CLI prompt mode", "status": "BLOCKED", "reason": None}
    cwd_dir = tmp_root / "k2-cwd"
    cwd_dir.mkdir()
    t0 = time.time()
    try:
        p = subprocess.run([str(KIMI_EXE), "-p", PROMPT_TEXT, "--output-format", "stream-json"],
                           cwd=str(cwd_dir), capture_output=True, timeout=180)
        rc, timed_out, raw_out, raw_err = p.returncode, False, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, timed_out, raw_out, raw_err = None, True, (e.stdout or b""), (e.stderr or b"")
    elapsed = round(time.time() - t0, 2)

    stdout = raw_out.decode("utf-8", "replace")
    stderr = raw_err.decode("utf-8", "replace")
    lines = [l for l in stdout.splitlines() if l.strip()]
    parsed: list = []
    for l in lines:
        try:
            parsed.append(json.loads(l))
        except Exception:  # noqa: BLE001
            parsed.append(None)

    def kind(o):
        if not isinstance(o, dict):
            return "non-json"
        if o.get("role") == "meta":
            return "meta:" + str(o.get("type"))
        return str(o.get("role"))

    def is_terminal_assistant(o):
        return (isinstance(o, dict) and o.get("role") == "assistant"
                and not (o.get("tool_calls") or o.get("toolCalls")))

    last = parsed[-1] if parsed else None
    last_non_meta = next((o for o in reversed(parsed)
                          if not (isinstance(o, dict) and o.get("role") == "meta")), None)
    assistant_msgs = [o for o in parsed if is_terminal_assistant(o)]
    asst_text = None
    for o in assistant_msgs:
        c = o.get("content")
        if isinstance(c, str) and c.strip():
            asst_text = c
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    asst_text = b["text"]

    k2.update({
        "cwd": str(cwd_dir), "timeout_s": 180, "timed_out": timed_out,
        "rc": rc, "elapsed_s": elapsed,
        "stdout_line_count": len(lines),
        "all_lines_json": all(o is not None for o in parsed),
        "line_kinds": [kind(o) for o in parsed],
        "last_line": {"kind": kind(last),
                      "is_assistant_without_tool_calls": is_terminal_assistant(last),
                      "raw_first_200": scrub(lines[-1])[:200] if lines else None},
        # I27 strict judge: JSONL 末行 = assistant message without tool_calls
        "terminal_completion_per_I27_strict_last_line": is_terminal_assistant(last),
        # adjusted judge observed necessary on 0.28.1 (trailing meta lines)
        "terminal_completion_last_non_meta_line": is_terminal_assistant(last_non_meta),
        "assistant_message_count": len(assistant_msgs),
        "assistant_text_first_200": scrub(asst_text)[:200] if asst_text else None,
        "stderr_scrubbed": scrub(stderr)[:1000] or None,
        "stderr_has_errors": bool(stderr.strip()),
    })
    if timed_out:
        k2["reason"] = "kimi -p timed out after 180s"
    elif rc == 0 and is_terminal_assistant(last_non_meta):
        k2["status"] = "PASS"
    elif rc == 0:
        k2["status"] = "DEGRADED"
        k2["reason"] = "rc=0 but no terminal assistant message found in stdout JSONL"
    else:
        k2["status"] = "DEGRADED"
        k2["reason"] = f"rc={rc}"
    return k2


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    if not KIMI_EXE.exists():
        ART.parent.mkdir(parents=True, exist_ok=True)
        ART.write_text(json.dumps({"probe_id": "P-KIM1", "status": "BLOCKED",
                                   "reason": f"kimi exe not found at {KIMI_EXE}"}, indent=2),
                       encoding="utf-8")
        print("BLOCKED: kimi exe not found")
        return 1

    tmp_root = Path(tempfile.mkdtemp(prefix="pkim1-"))
    started = datetime.now(timezone.utc)
    print("[P-KIM1] temp root:", tmp_root)

    version = probe_version()
    creds = probe_credentials()
    config = probe_config()
    print(f"[P-KIM1] kimi version: {version['version']}  credentials: {creds['names']}")

    try:
        k1 = run_k1(tmp_root)
        print(f"[P-KIM1] K1: {k1['status']}"
              + (f" ({k1['reason']})" if k1.get("reason") else ""))
    except Exception as e:  # noqa: BLE001
        import traceback
        k1 = {"lane": "K1 kimi web server", "status": "BLOCKED",
              "reason": f"exception: {scrub(repr(e))[:300]}",
              "traceback": scrub(traceback.format_exc())[-1500:]}
        print(f"[P-KIM1] K1: BLOCKED (exception: {e!r})")
    try:
        k2 = run_k2(tmp_root)
        print(f"[P-KIM1] K2: {k2['status']}"
              + (f" ({k2['reason']})" if k2.get("reason") else ""))
    except Exception as e:  # noqa: BLE001
        import traceback
        k2 = {"lane": "K2 kimi CLI prompt mode", "status": "BLOCKED",
              "reason": f"exception: {scrub(repr(e))[:300]}",
              "traceback": scrub(traceback.format_exc())[-1500:]}
        print(f"[P-KIM1] K2: BLOCKED (exception: {e!r})")

    notes = [
        "K2: on 0.28.1 the LAST stream-json line is a meta record "
        "(role=meta, type=session.resume_hint), NOT the assistant message; "
        "the terminal assistant message is the last NON-meta line. The strict "
        "I27 'last line = assistant without tool_calls' judge reports a false "
        "INCOMPLETE_OUTPUT unless trailing meta lines are skipped.",
        "K1: POST /api/v1/sessions silently drops agent_config.model in 0.28.1 "
        "(response echoes model:\"\"; a turn then fails with "
        "model.not_configured). Working path: create session, then POST "
        "/api/v1/sessions/{id}/profile {agent_config:{model:...}} before the "
        "first prompt (profile response also echoes model:\"\" even when applied).",
        "K1 auth: bearer token is printed on startup stdout (Local URL "
        "http://127.0.0.1:<port>/#token=... plus a 'Token:' line); recoverable "
        "headlessly. /api/v1/healthz needs NO auth; /openapi.json returns 401 "
        "without the bearer token.",
        "K1 token persistence: token is stored at ~/.kimi-code/server.token and "
        "reused across server instances (observed identical token for "
        "concurrent instances; file hash matched the printed token). VOL-15 "
        "section 3 assumes memory/OS-secret storage only.",
        "K1 port drift: if 58627 is already listening, `kimi web` silently "
        "binds the next port (observed 58628). Clients must parse the startup "
        "banner; the spec port cannot be assumed.",
        "K1 transport: HTTP 200 responses can carry a non-zero body code "
        "(error envelope); success must be judged on body.code==0, not on the "
        "HTTP status alone.",
        "K1 events: the WS event stream is mirrored to "
        "~/.kimi-code/server/events/session_<id>.jsonl (turn.started/ended, "
        "turn.step.*, prompt.completed, context.spliced, "
        "event.session.work_changed) -- a recovery/audit source without a WS "
        "client, BUT the file only becomes visible at its final path well "
        "after server shutdown (observed up to ~2.5 min); the WS event "
        "vocabulary is also enumerated in /asyncapi.json (session_event "
        "message, one channel kimiCodeWebSocket, subscribe/unsubscribe/abort "
        "client messages).",
        "K1 session create takes ~5-10s (spins up MCP servers declared in "
        "~/.kimi-code/mcp.json; one remote MCP server failed with 'fetch "
        "failed'). Budget accordingly.",
        "K1 abort: prompt-level POST /api/v1/sessions/{id}/prompts/{prompt_id}:abort "
        "and session-level POST /api/v1/sessions/{id}:abort exist in the binary; "
        "the {id}:abort route is NOT declared in openapi.json (only :archive is) "
        "-- live spec wins per VOL-15 section 3 hash discipline.",
        "K1 messages endpoint returns items newest-first (assistant before user); "
        "a <system-reminder> user message is auto-injected (auto permission mode).",
        "K1: GET /api/v1/sessions/{id}/transcript returned an empty items list "
        "for the completed session; the reliable terminal judgement was GET "
        "/api/v1/sessions/{id} (busy=false + last_turn_reason) plus GET "
        ".../messages for the final assistant message.",
        "K1: the listener answers /healthz BEFORE the startup banner (with the "
        "token) is flushed to the redirected stdout file; clients must keep "
        "reading stdout for a grace period after healthz turns OK (~1-2s "
        "observed).",
    ]

    statuses = [k1.get("status"), k2.get("status")]
    if all(s == "PASS" for s in statuses):
        overall = "PASS"
    elif all(s in (None,) or s in ("BLOCKED",) for s in statuses):
        overall = "BLOCKED"
    else:
        overall = "DEGRADED"

    artifact = {
        "probe_id": "P-KIM1",
        "question": "Kimi dual lane: K1 `kimi web` server contract (healthz, token, "
                    "openapi/asyncapi hash, session create with metadata.cwd, terminal "
                    "judgement, messages recovery, abort); K2 `-p --output-format "
                    "stream-json` terminal completion per I27 (0.28.1).",
        "executed_at": started.isoformat(),
        "status": overall,
        "environment": {
            "os": sys.platform,
            "python": sys.version.split()[0],
            "repo_root": str(REPO),
            "kimi_exe": str(KIMI_EXE),
            "temp_root": str(tmp_root),
        },
        "kimi_version": version,
        "credentials": creds,
        "config_summary": config,
        "k1": k1,
        "k2": k2,
        "notes_scope": f"observations on this machine, kimi {version.get('version')} "
                        "(validated during P-KIM1 recon and the recorded run)",
        "notes": notes,
        "contract_updates": [
            "VOL-15 s3 (K2): terminal completion judge must skip trailing meta "
            "lines; strict last-line rule is OBSERVED_DIFFERENT on 0.28.1.",
            "VOL-15 s3 (K1): model must be set via the session profile endpoint; "
            "create-time agent_config.model is dropped (0.28.1).",
            "VOL-15 s3 (K1): token persists at ~/.kimi-code/server.token and is "
            "shared across server instances -- not memory-only.",
            "VOL-15 s3 (K1): port may drift from 58627 when occupied; parse the "
            "startup banner.",
            "VOL-20 s2: HTTP 200 + body.code!=0 is an error envelope; judge on "
            "body.code==0.",
        ],
        "evidence_refs": ["artifact:k1", "artifact:k2", "artifact:notes"],
        "fallback_triggered": "K2 CLI lane is the fallback for K1; both were exercised. "
                               "K1 server lane requires profile-based model set (see notes).",
        "version": {"kimi_cli": version.get("version"),
                    "artifact_schema": "1"},
    }

    ART.parent.mkdir(parents=True, exist_ok=True)
    ART.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    print("[P-KIM1] overall:", overall)
    print("[P-KIM1] artifact:", ART)

    # best-effort temp cleanup (server stdout file holds the token)
    try:
        shutil.rmtree(tmp_root, ignore_errors=True)
        print("[P-KIM1] temp root removed")
    except Exception:  # noqa: BLE001
        pass
    return 0 if overall != "BLOCKED" else 2


if __name__ == "__main__":
    sys.exit(main())
