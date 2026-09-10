"""zloop.research.broker — M4 Research Broker (VOL-15 §1/§4, VOL-04 §10).

Kimi Research Lane with adaptive HTTP port discovery and CLI fallback mechanism.
The broker NEVER raises on per-question lane failures: every question is captured
into its own record. The lane runs in an independent temp cwd — never the project dir
or any canonical writable root (VOL-15 §7 / I42).

D-18 three-axis semantics per record: ``provider_health`` (was the provider
reachable/authenticated/quota-healthy?) is independent of
``retrieval_outcome`` (did we obtain evidence?) which is independent of
``trust``/``verification`` (how much do we believe it?). Quota exhaustion is
QUOTA_EXHAUSTED + NO_EVIDENCE — NOT "evidence, unverified": obtaining
nothing because the provider has no quota is a different epistemic state
from obtaining evidence whose provenance is pending. claim/raw_ref/
verification/trust are only ever set for EVIDENCE_FOUND records.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Optional

from .. import ids, redact
from ..evidence import BlobStore
from .kimi_cli import KimiCliLane
from .kimi_server import (
    HEALTH_ERROR,
    HEALTH_OK,
    HEALTH_SERVER_UNAVAILABLE,
    RETRIEVAL_EVIDENCE_FOUND,
    RETRIEVAL_NO_EVIDENCE,
    KimiServerLane,
    messages_blob_bytes,
)
from .port_discovery import discover_kimi_port

LANE = "kimi"
TRUST = "external_untrusted"
CLAIM_MAX = 300
_RESEARCH_ID_RE = re.compile(r"RS\d{3,}")

# VOL-04 §10 evidence-record fields + the D-18 axis fields (single source of
# truth for the shape). verification/claim/raw_ref/trust are present as keys
# on every record but are None unless retrieval_outcome == EVIDENCE_FOUND.
VOL04_FIELDS = (
    "ref", "research_id", "question_id", "lane", "query", "claim", "url",
    "title", "source_class", "observed_at", "published_at", "retrieved_at",
    "raw_ref", "content_hash", "verification", "trust",
    "provider_health", "retrieval_outcome",
)


def _allocate_research_id(project_dir: Path) -> str:
    """Next free RS### under project_dir/research/ (VOL-04 §1 via ids)."""
    root = Path(project_dir) / "research"
    best = 0
    if root.is_dir():
        for entry in root.iterdir():
            m = _RESEARCH_ID_RE.fullmatch(entry.name)
            if entry.is_dir() and m:
                best = max(best, int(m.group(0)[2:]))
    return ids.fmt_research(best + 1)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


def _token_prefix(lane: Any) -> Optional[str]:
    """sha256 prefix of the lane token — only form allowed in artifacts."""
    try:
        fp = getattr(lane, "token_fingerprint", None)
        return fp() if callable(fp) else None
    except Exception:
        return None


def _shutdown_default_lane(lane: Any) -> None:
    """Kill the server tree iff the default lane spawned it (owned)."""
    try:
        shutdown = getattr(lane, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception:
        pass


def _run_question(lane: Any, store: BlobStore, research_id: str,
                  qid: str, query: Any, timeout_s: int) -> dict:
    now = ids.now_iso()
    rec = {
        "ref": f"web:{LANE}:{qid}",
        "research_id": research_id,
        "question_id": qid,
        "lane": LANE,
        "query": redact.redact_str(str(query)) if query else None,
        "claim": None,
        "url": None,            # K1 v1 surface has no URL; lane-synthesized
        "title": None,
        "source_class": "secondary",
        "observed_at": now,
        "published_at": None,   # model output has no publication time
        "retrieved_at": now,
        "raw_ref": None,
        "content_hash": None,
        "verification": None,  # D-18: evidence fields exist only for
        "trust": None,          # EVIDENCE_FOUND records
        "provider_health": HEALTH_ERROR,
        "retrieval_outcome": RETRIEVAL_NO_EVIDENCE,
        "answer": None,
        "last_turn_reason": None,
        "session_id": None,
        "error": None,
    }
    if not query:
        # a spec problem, not a provider fault: the provider was never
        # contacted, so the health axis carries no provider failure
        rec["provider_health"] = HEALTH_OK
        rec["error"] = "missing query"
        return rec

    # I42: the lane NEVER runs in the project dir — independent temp cwd
    temp_cwd = Path(tempfile.mkdtemp(prefix="zloop-research-"))
    try:
        res = lane.ask(str(query), cwd=temp_cwd, timeout_s=timeout_s)

        # Dynamic fallback: if KimiServerLane fails with SERVER_UNAVAILABLE, retry via KimiCliLane
        if isinstance(lane, KimiServerLane) and res.get("provider_health") == HEALTH_SERVER_UNAVAILABLE:
            cli_lane = KimiCliLane()
            if cli_lane.ensure_server():
                res = cli_lane.ask(str(query), cwd=temp_cwd, timeout_s=timeout_s)

        answer = res.get("answer")
        reason = res.get("last_turn_reason")
        health = res.get("provider_health")
        if not health:   # lanes predating D-18: derive, never trust blanks
            health = (HEALTH_OK if (reason == "completed" and answer)
                      else HEALTH_ERROR)
        rec["provider_health"] = health
        rec["last_turn_reason"] = reason
        rec["session_id"] = res.get("session_id")

        found = bool(reason == "completed" and answer
                     and health == HEALTH_OK)
        rec["retrieval_outcome"] = (RETRIEVAL_EVIDENCE_FOUND if found
                                    else RETRIEVAL_NO_EVIDENCE)
        if found:
            raw = res.get("raw_messages")
            if raw is not None:
                safe = redact.redact_obj(raw)
                digest = store.put(messages_blob_bytes(safe))
                rec["raw_ref"] = "blob:sha256:" + digest
            rec["answer"] = redact.redact_str(str(answer))
            rec["claim"] = rec["answer"][:CLAIM_MAX]
            rec["content_hash"] = sha256(
                rec["answer"].encode("utf-8")).hexdigest()
            rec["verification"] = "lane_reported"
            rec["trust"] = TRUST
        else:
            # NO_EVIDENCE: keep the health axis and a readable cause; the
            # evidence-provenance fields stay null
            err = res.get("error") or \
                f"no answer (last_turn_reason={reason})"
            rec["error"] = redact.redact_str(str(err))[:300]
    except Exception as e:  # per-question isolation: never raise (M4)
        rec["provider_health"] = HEALTH_ERROR
        rec["retrieval_outcome"] = RETRIEVAL_NO_EVIDENCE
        rec["error"] = redact.redact_str(f"{type(e).__name__}: {e}")[:300]
    finally:
        shutil.rmtree(temp_cwd, ignore_errors=True)
    return rec


def run_research(project_dir: Path, spec: dict, *, lane: Any = None) -> dict:
    """Run one research spec through the Kimi lane; write a bounded manifest.

    spec = {"research_id": optional "RS###", "questions": [{"id","query"}],
            "timeout_s": optional}. Returns {"research_id", "results"
            [evidence records], "openapi_digest"}. Per-question failures are
    captured in the records; only a malformed research_id raises.
    """
    project_dir = Path(project_dir)
    spec = spec or {}
    questions = list(spec.get("questions") or [])
    timeout_s = int(spec.get("timeout_s") or 180)
    research_id = str(spec.get("research_id") or "").strip() \
        or _allocate_research_id(project_dir)
    if not _RESEARCH_ID_RE.fullmatch(research_id):
        raise ValueError(
            f"invalid research_id {research_id!r} (expected RS###)")
    research_dir = project_dir / "research" / research_id
    research_dir.mkdir(parents=True, exist_ok=True)

    # default lane looked up at call time so tests can monkeypatch the
    # module attribute; an injected lane (parameter) is caller-owned
    default_lane = lane is None
    if lane is None:
        discovered = discover_kimi_port()
        if discovered:
            server_lane = KimiServerLane(base_url=discovered)
            try:
                if server_lane.ensure_server():
                    lane = server_lane
            except Exception:
                lane = None

        if lane is None:
            # Check if default port 58627 can be ensured/spawned
            server_lane = KimiServerLane()
            try:
                if server_lane.ensure_server():
                    lane = server_lane
            except Exception:
                lane = None

        if lane is None:
            # Fall back to CLI lane if HTTP server discovery/spawning fails
            lane = KimiCliLane()

    openapi_digest = None
    try:
        openapi_digest = lane.openapi_digest()
    except Exception:
        openapi_digest = None

    store = BlobStore(project_dir / "blobs" / "sha256")
    results = []
    for i, q in enumerate(questions):
        q = q or {}
        qid = str(q.get("id") or f"Q{i + 1}")
        results.append(_run_question(lane, store, research_id, qid,
                                     q.get("query"), timeout_s))

    manifest = {
        "research_id": research_id,
        "created_at": ids.now_iso(),
        "lane": LANE,
        "openapi_digest": openapi_digest,
        "token_sha256_prefix": _token_prefix(lane),
        "timeout_s": timeout_s,
        "results": results,
    }
    out = {"research_id": research_id, "results": results,
           "openapi_digest": openapi_digest}
    try:
        _write_json(research_dir / "manifest.json", manifest)
    except Exception as e:  # manifest write failure must not lose results
        out["manifest_error"] = str(e)[:200]

    if default_lane:
        _shutdown_default_lane(lane)
    return out
