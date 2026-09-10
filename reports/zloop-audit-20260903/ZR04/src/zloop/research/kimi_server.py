"""zloop.research.kimi_server — K1 `KimiServerLane` (VOL-15 §3, VOL-02 §5).

The single Kimi lane of the M4 Research Broker (decision D-10; K2 CLI
fallback intentionally NOT implemented because K1/K2 share one account
quota, so a fallback adds no resilience).

Contract pinned against the live server (2026-09-02, kimi 0.28.1, P-KIM1
plus this session's own probes — treat as ground truth):

- ``GET /api/v1/healthz`` is unauthenticated; everything else (including
  ``GET /openapi.json``) requires ``Authorization: Bearer <token>``.
- The token lives in ``~/.kimi-code/server.token`` (persisted and shared
  across instances — VOL-15's "memory only" assumption is wrong). It is
  never logged, echoed, or embedded in exceptions; artifacts may carry
  only a sha256 prefix (``token_fingerprint``).
- Every ``/api/*`` response uses the envelope ``{code, msg, data,
  request_id}``; HTTP 200 can still carry ``code != 0``.
- ``POST /api/v1/sessions`` with ``{"metadata": {"cwd": ...}, "title": ...}``
  returns the session id in ``data.id``. ``agent_config.model`` is silently
  dropped at create — the model must be set per session via
  ``POST /api/v1/sessions/{id}/profile`` (live probe: without it every
  turn fails with ``model.not_configured``).
- ``POST /api/v1/sessions/{id}/prompts`` requires ``content`` to be an
  ARRAY of typed parts: ``{"content": [{"type": "text", "text": ...}]}``
  (verified against the live openapi.json schema; a plain-string body is
  schema-invalid). The shape actually used is reported in the ask() result.
- ``last_turn_reason`` lives on ``GET /api/v1/sessions/{id}``
  (``data.last_turn_reason``); the ``/status`` endpoint does NOT carry it.
- ``GET /api/v1/sessions/{id}/messages`` returns ``data.items`` (+
  ``has_more``). Trailing records can be meta (e.g. session.resume_hint)
  and must be skipped when picking the terminal assistant message.
- ``POST /api/v1/sessions/{id}:abort`` is best-effort cleanup (not listed
  in openapi.json but P-KIM1-verified).
- ``POST /api/v1/sessions/{id}/prompts`` accepts ``disabled_tools`` —
  the research session is a pure SEARCHER (D-19): Read/Write/Edit/Bash/
  Grep/Glob are disabled at submission; only WebSearch/FetchURL remain.
  A web page reached by the searcher must not face any execution tool.

D-18 three-axis semantics: ``ask`` never reports provider failures as
"evidence we could not verify" — classified failures (quota / auth /
server unavailable / error) are RETURNED as structured records with
``provider_health`` set and ``answer=None``; only unexpected non-Kimi
exceptions propagate (the broker maps those to ERROR records).

Stdlib only (urllib.request). Every call has a connect+read timeout.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .. import redact

DEFAULT_BASE_URL = "http://127.0.0.1:58627"
DEFAULT_MODEL = "kimi-code/kimi-for-coding"  # machine config.toml default_model
TERMINAL_REASONS = ("completed", "cancelled", "failed")

# ---- D-18 three-axis semantics --------------------------------------------
# provider_health describes the LANE/PROVIDER, not the evidence:
# "obtained evidence, provenance pending" (NO evidence-fields yet) and
# "obtained NOTHING because the provider has no quota" are different states.
HEALTH_OK = "OK"
HEALTH_QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
HEALTH_AUTH_FAILED = "AUTH_FAILED"
HEALTH_SERVER_UNAVAILABLE = "SERVER_UNAVAILABLE"
HEALTH_ERROR = "ERROR"
PROVIDER_HEALTHS = (HEALTH_OK, HEALTH_QUOTA_EXHAUSTED, HEALTH_AUTH_FAILED,
                    HEALTH_SERVER_UNAVAILABLE, HEALTH_ERROR)

RETRIEVAL_EVIDENCE_FOUND = "EVIDENCE_FOUND"
RETRIEVAL_NO_EVIDENCE = "NO_EVIDENCE"

# ---- D-19 searcher-only session -------------------------------------------
# The research session is a SEARCHER, not a second execution agent: disable
# the coding-agent toolset at prompt submission; WebSearch + FetchURL stay.
SEARCHER_DISABLED_TOOLS = ("Read", "Write", "Edit", "Bash", "Grep", "Glob")

# live-observed quota failure texts (D-15: 403 provider.api_error / usage
# limit); matched case-insensitively against HTTP bodies, envelope msgs
# and the transcript of failed turns.
_QUOTA_MARKERS = ("provider.api_error", "usage limit", "quota")
_AUTH_MARKERS = ("unauthorized", "invalid token", "authentication")

_CALL_TIMEOUT_S = 30.0   # per-call connect+read cap
_HEALTH_TIMEOUT_S = 3.0
_POLL_INTERVAL_S = 1.0


class KimiError(RuntimeError):
    """Lane-level failure. Never carries the bearer token."""


class KimiProviderError(KimiError):
    """Lane failure with a classified provider_health value (D-18).

    Raised by ``_api``/``token``; ``ask`` converts it into a structured
    no-answer result instead of raising, so the broker can record the
    provider axis. Never carries the bearer token.
    """

    def __init__(self, message: str, provider_health: str):
        super().__init__(message)
        self.provider_health = provider_health


def _classify_http_status(code: int, detail: str) -> str:
    """Map an HTTP status (+ body text) to a provider_health value."""
    d = (detail or "").lower()
    if any(m in d for m in _QUOTA_MARKERS):
        return HEALTH_QUOTA_EXHAUSTED
    if 500 <= code < 600:
        return HEALTH_SERVER_UNAVAILABLE
    if code in (401, 403):
        return HEALTH_AUTH_FAILED
    return HEALTH_ERROR


def _quota_marker_in(messages: Any) -> bool:
    """D-18: does a FAILED turn's transcript mention quota/usage limits?

    Only non-user records are scanned (the question text may legitimately
    contain the words).
    """
    if not isinstance(messages, list):
        return False
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") == "user":
            continue
        try:
            blob = json.dumps(msg, ensure_ascii=False).lower()
        except Exception:
            continue
        if any(m in blob for m in _QUOTA_MARKERS):
            return True
    return False


def messages_blob_bytes(messages: Any) -> bytes:
    """Canonical serialization of (redacted) messages.

    Used both by the lane (raw_messages_ref digest) and the broker (blob
    CAS key) so both hash the exact same bytes.
    """
    return json.dumps(messages, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _is_meta_record(msg: Any) -> bool:
    if not isinstance(msg, dict):
        return True
    t = msg.get("type")
    if isinstance(t, str):
        tl = t.lower()
        if tl == "meta" or tl.startswith("meta:") or tl.startswith("meta."):
            return True
    return False


def extract_answer(messages: Any) -> str:
    """Terminal answer = LAST assistant message that is not meta/tool-only.

    P-KIM1: trailing records may be meta (session.resume_hint); skip
    role != assistant entries, type-meta entries, and assistant messages
    whose content has no text part (pure tool_use / thinking records).
    Returns "" when no terminal assistant message exists (failed turns).
    """
    if not isinstance(messages, list):
        return ""
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        if _is_meta_record(msg):
            continue
        content = msg.get("content")
        texts: list[str] = []
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            for part in content:
                if (isinstance(part, dict) and part.get("type") == "text"
                        and isinstance(part.get("text"), str)):
                    texts.append(part["text"])
        joined = "\n".join(t for t in texts if t).strip()
        if joined:
            return joined
    return ""


class KimiServerLane:
    """K1 lane over the local `kimi web` REST server (loopback only)."""

    def __init__(self, base_url: Optional[str] = None,
                 token_path: Optional[Path] = None,
                 model: str = DEFAULT_MODEL):
        self.base_url = (base_url or os.environ.get("ZLOOP_KIMI_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.token_path = (Path(token_path) if token_path is not None
                           else Path.home() / ".kimi-code" / "server.token")
        self.model = model
        self.owned = False        # True only if WE spawned the server
        self._token: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._spawn_files: list = []  # stdout/stderr temp files of our child

    # ---- token handling (I13/I32: secret never reaches artifacts) --------

    def token(self) -> str:
        if self._token is None:
            try:
                value = self.token_path.read_text(encoding="utf-8").strip()
            except OSError as e:
                raise KimiProviderError(
                    f"kimi server token file not readable: {self.token_path} "
                    "(run `kimi web` once, or point token_path at "
                    "~/.kimi-code/server.token)",
                    HEALTH_AUTH_FAILED) from None
            if not value:
                raise KimiProviderError(
                    f"kimi server token file is empty: {self.token_path}",
                    HEALTH_AUTH_FAILED)
            self._token = value
        return self._token

    def token_fingerprint(self) -> Optional[str]:
        """sha256 prefix of the token — the only form allowed in artifacts."""
        try:
            return hashlib.sha256(self.token().encode("utf-8")).hexdigest()[:8]
        except KimiError:
            return None

    # ---- HTTP (stdlib urllib; every call has a timeout) -------------------

    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 *, auth: bool = True, timeout_s: float = _CALL_TIMEOUT_S
                 ) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base_url + path, method=method)
        if auth:
            req.add_header("Authorization", "Bearer " + self.token())
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, data=data, timeout=timeout_s) as r:
            return r.status, r.read()

    def _api(self, method: str, path: str, body: Optional[dict] = None,
             timeout_s: float = _CALL_TIMEOUT_S) -> Any:
        """Call an enveloped /api/* endpoint; unwrap {code,msg,data}.

        Failures raise ``KimiProviderError`` with the D-18 provider_health
        classification (quota / auth / server-unavailable / error).
        """
        try:
            status, raw = self._request(method, path, body,
                                        auth=True, timeout_s=timeout_s)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(200).decode("utf-8", "replace")
            except Exception:
                pass
            raise KimiProviderError(
                f"{method} {path} -> HTTP {e.code} {detail[:120]}",
                _classify_http_status(e.code, detail)) from None
        except urllib.error.URLError as e:
            raise KimiProviderError(
                f"{method} {path} -> connection failed: {e.reason}",
                HEALTH_SERVER_UNAVAILABLE) from None
        try:
            env = json.loads(raw.decode("utf-8"))
        except Exception:
            raise KimiProviderError(
                f"{method} {path} -> non-JSON response "
                f"({len(raw)} bytes, HTTP {status})",
                HEALTH_ERROR) from None
        code = env.get("code") if isinstance(env, dict) else None
        if status != 200 or (code not in (0, None)):
            msg = env.get("msg") if isinstance(env, dict) else ""
            if status != 200:
                health = _classify_http_status(status, str(msg or ""))
            else:
                m = str(msg or "").lower()
                if any(q in m for q in _QUOTA_MARKERS):
                    health = HEALTH_QUOTA_EXHAUSTED
                elif any(a in m for a in _AUTH_MARKERS):
                    health = HEALTH_AUTH_FAILED
                else:
                    health = HEALTH_ERROR
            raise KimiProviderError(
                f"{method} {path} -> HTTP {status} code={code} msg={msg}",
                health)
        return env.get("data") if isinstance(env, dict) else None

    def _healthz_ok(self, timeout_s: float = _HEALTH_TIMEOUT_S) -> bool:
        try:
            status, _ = self._request("GET", "/api/v1/healthz", auth=False,
                                      timeout_s=timeout_s)
            return status == 200
        except Exception:
            return False

    # ---- server lifecycle --------------------------------------------------

    def _locate_kimi_exe(self) -> Optional[str]:
        exe = shutil.which("kimi") or shutil.which("kimi.exe")
        if exe:
            return exe
        home = Path.home() / ".kimi-code" / "bin"
        for cand in ("kimi.exe", "kimi"):
            p = home / cand
            if p.exists():
                return str(p)
        return None

    def ensure_server(self, timeout_s: float = 40.0) -> bool:
        """Make sure `kimi web` is up. Spawns it if healthz is refused.

        Tracks ownership: if WE started the process, ``owned`` is True and
        ``shutdown()`` will kill the tree. An already-running server is
        left alone. Returns True when healthy, False on spawn timeout.
        """
        if self._healthz_ok():
            return True
        exe = self._locate_kimi_exe()
        if exe is None:
            raise KimiError("kimi executable not found "
                            "(PATH or ~/.kimi-code/bin/kimi.exe)")
        # stdout/stderr to temp files (never the console; never logged)
        out_f = tempfile.TemporaryFile()
        err_f = tempfile.TemporaryFile()
        creationflags = 0
        if os.name == "nt":
            creationflags = (subprocess.CREATE_NEW_PROCESS_GROUP
                             | subprocess.CREATE_NO_WINDOW)
        self._proc = subprocess.Popen(
            [exe, "web"], stdout=out_f, stderr=err_f,
            stdin=subprocess.DEVNULL, close_fds=True,
            creationflags=creationflags)
        self._spawn_files = [out_f, err_f]
        self.owned = True
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise KimiError(
                    f"`kimi web` exited early with code "
                    f"{self._proc.returncode}")
            if self._healthz_ok():
                return True
            time.sleep(0.5)
        # v1 note (P-KIM1): on port conflict kimi drifts to 58628+; we do
        # not discover the drifted port — override via base_url/env instead.
        return False

    def shutdown(self) -> None:
        """Kill the server tree iff WE started it. Never touches others."""
        proc, self._proc = self._proc, None
        owned, self.owned = self.owned, False
        for f in self._spawn_files:
            try:
                f.close()
            except Exception:
                pass
        self._spawn_files = []
        if proc is not None and owned and proc.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=15)
            else:
                proc.terminate()

    # ---- contract pinning --------------------------------------------------

    def openapi_digest(self) -> Optional[str]:
        """sha256 of the live /openapi.json (contract pinning). None on failure.

        The endpoint requires auth (401 without Bearer — verified live).
        """
        try:
            status, raw = self._request("GET", "/openapi.json", auth=True,
                                        timeout_s=15.0)
        except Exception:
            return None
        if status != 200 or not raw:
            return None
        return hashlib.sha256(raw).hexdigest()

    # ---- the actual research call -------------------------------------------

    def ask(self, question: str, *, cwd: Optional[Path] = None,
            timeout_s: int = 180) -> dict:
        """One question -> one terminal answer (VOL-15 §3 K1 flow).

        Returns {"answer", "provider_health", "last_turn_reason",
        "session_id", "raw_messages", "raw_messages_ref", "error",
        "prompt_endpoint", "prompt_body_shape", "disabled_tools"}.

        D-18: provider-side failures are NOT raised — they come back as
        structured records with provider_health in the five-value enum
        and answer=None (quota exhaustion, auth failures, dead server,
        turn failures). Only unexpected non-Kimi exceptions propagate;
        the broker maps those to ERROR records.

        D-19: the prompt is submitted with the coding-agent tools disabled
        (SEARCHER_DISABLED_TOOLS) — the session is a searcher, never a
        second execution agent.

        The answer is the raw terminal assistant text (the broker redacts
        before evidence); raw_messages are already redacted and
        raw_messages_ref is the sha256 of their canonical JSON (the blob
        CAS key).
        """
        deadline = time.monotonic() + max(5, int(timeout_s))

        def remaining() -> float:
            return max(1.0, deadline - time.monotonic())

        def _fail(health: str, error: str,
                  session_id: Optional[str] = None,
                  messages: Any = None,
                  reason: Optional[str] = None) -> dict:
            red = redact.redact_obj(messages if messages is not None else [])
            return {
                "answer": None,
                "provider_health": health,
                "last_turn_reason": reason,
                "session_id": session_id,
                "raw_messages": red,
                "raw_messages_ref": hashlib.sha256(
                    messages_blob_bytes(red)).hexdigest(),
                "prompt_endpoint": None,
                "prompt_body_shape": "content:array_of_typed_parts",
                "disabled_tools": list(SEARCHER_DISABLED_TOOLS),
                "error": redact.redact_str(str(error))[:300],
            }

        # availability gate: spawn/health failures mean no server to talk to
        try:
            if not self.ensure_server(timeout_s=min(40.0, remaining())):
                return _fail(
                    HEALTH_SERVER_UNAVAILABLE,
                    f"`kimi web` did not become healthy on {self.base_url} "
                    "within the startup budget")
        except KimiError as e:   # exe missing / early exit -> no server
            return _fail(HEALTH_SERVER_UNAVAILABLE, str(e))

        own_cwd = cwd is None
        if own_cwd:
            cwd = Path(tempfile.mkdtemp(prefix="zloop-research-"))
        cwd = Path(cwd)
        session_id: Optional[str] = None
        try:
            # create: metadata.cwd only — agent_config.model is silently
            # dropped here (P-KIM1) and MUST go through the profile endpoint
            data = self._api("POST", "/api/v1/sessions",
                             {"metadata": {"cwd": str(cwd)},
                              "title": "zloop-research"},
                             timeout_s=min(_CALL_TIMEOUT_S, remaining()))
            session_id = ((data or {}).get("id")
                         or (data or {}).get("session_id"))
            if not session_id:
                raise KimiError("session create returned no id")

            # set the model per-session via the profile endpoint; without
            # this every turn fails with model.not_configured (live-probed
            # 2026-09-02). Best-effort: a deployment with a server-side
            # default may not need it.
            try:
                self._api("POST", f"/api/v1/sessions/{session_id}/profile",
                          {"agent_config": {"model": self.model}},
                          timeout_s=min(_CALL_TIMEOUT_S, remaining()))
            except KimiError:
                pass

            # prompt: content is an ARRAY of typed parts (live openapi.json);
            # disabled_tools shrinks the session to a searcher (D-19) —
            # the endpoint accepts the field (P-KIM1 verified)
            prompt_path = f"/api/v1/sessions/{session_id}/prompts"
            self._api("POST", prompt_path,
                      {"content": [{"type": "text", "text": str(question)}],
                       "disabled_tools": list(SEARCHER_DISABLED_TOOLS)},
                      timeout_s=min(_CALL_TIMEOUT_S, remaining()))

            # poll the session object: last_turn_reason lives there,
            # NOT on the /status endpoint (live-probed)
            reason: Optional[str] = None
            while time.monotonic() < deadline:
                time.sleep(_POLL_INTERVAL_S)
                sess = self._api("GET", f"/api/v1/sessions/{session_id}",
                                 timeout_s=min(_CALL_TIMEOUT_S, remaining()))
                reason = (sess or {}).get("last_turn_reason")
                if reason in TERMINAL_REASONS:
                    break
            reason = reason if reason in TERMINAL_REASONS else "timeout"

            messages: list = []
            try:
                mdata = self._api(
                    "GET",
                    f"/api/v1/sessions/{session_id}/messages?page_size=200",
                    timeout_s=min(_CALL_TIMEOUT_S, remaining()))
                items = (mdata.get("items") if isinstance(mdata, dict)
                         else mdata) if mdata is not None else None
                if isinstance(items, list):
                    messages = items
            except KimiError:
                messages = []   # turn already terminal; recovery is best-effort

            # D-18 classification of the terminal turn
            answer = extract_answer(messages)
            if reason == "completed" and answer:
                health, error = HEALTH_OK, None
            elif reason == "failed" and _quota_marker_in(messages):
                health = HEALTH_QUOTA_EXHAUSTED
                answer = None
                error = ("provider quota/usage limit exhausted "
                         "(last_turn_reason=failed)")
            elif reason == "failed":
                health, error = (HEALTH_ERROR,
                                "turn failed (last_turn_reason=failed)")
            else:   # cancelled / timeout / completed-without-answer: the
                health = HEALTH_OK   # provider itself is not at fault
                error = f"no answer (last_turn_reason={reason})"

            redacted = redact.redact_obj(messages)
            blob = messages_blob_bytes(redacted)
            return {
                "answer": answer,
                "provider_health": health,
                "last_turn_reason": reason,
                "session_id": session_id,
                "raw_messages": redacted,
                "raw_messages_ref": hashlib.sha256(blob).hexdigest(),
                "prompt_endpoint": "POST " + prompt_path,
                "prompt_body_shape": "content:array_of_typed_parts",
                "disabled_tools": list(SEARCHER_DISABLED_TOOLS),
                "error": redact.redact_str(error)[:300] if error else None,
            }
        except KimiProviderError as e:   # classified: quota/auth/server/error
            return _fail(e.provider_health, str(e), session_id=session_id)
        except KimiError as e:           # unclassified lane failure
            return _fail(HEALTH_ERROR, str(e), session_id=session_id)
        finally:
            if session_id:
                try:  # best-effort cleanup (P-KIM1-verified endpoint)
                    self._api("POST", f"/api/v1/sessions/{session_id}:abort",
                              timeout_s=10.0)
                except KimiError:
                    pass
            if own_cwd:
                shutil.rmtree(cwd, ignore_errors=True)
