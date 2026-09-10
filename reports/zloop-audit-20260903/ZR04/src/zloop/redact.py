"""zloop.redact — recursive secret redaction (VOL-06 §1.2, VOL-17 §2).

Hard invariant I13: redaction happens BEFORE hash/serialize/journal.
Every string passed through the evidence plane must go through redact_obj /
redact_str first.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

REDACTED = "<redacted>"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # PEM / ssh private key blocks
    ("pem", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.S)),
    # Bearer tokens
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}")),
    # key/token/secret/password assignment: name = value  or  name: value
    ("kv", re.compile(
        r"(?i)\b([A-Za-z0-9_.\-]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|"
        r"CREDENTIAL|AUTH[_-]?HEADER|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)[A-Za-z0-9_.\-]*)"
        r"(\s*[:=]\s*)(\"[^\"]{4,}\"|'[^']{4,}'|[^\s,;}\]]{6,})")),
    # sk- style provider keys
    ("provider_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}")),
]

# Filenames whose whole content is treated as secret if they appear as a
# standalone token in tool input/output (best-effort, name-level only).
SECRET_FILE_RE = re.compile(
    r"(?i)(^|[\\/])(\.env(\.|$)|\.env|id_rsa|id_ed25519|.*\.pem|.*\.keystore|"
    r"credentials\.json|auth\.json|wallet\.json)(\s|$|\"|')")

# Dict keys whose NAME marks the value as secret ({"API_TOKEN": "x"}).
# Segment match (split on _-. and lowercase) — "MY_TOKEN" hits, "author" does not.
# D-21: bare "key" is NOT secret (it over-redacted primary_key / cache_key /
# public_key_id / key_path / keyboard_key / ssh_key); only credential-SHAPED
# names are: an explicit secret segment, or "api/access/private" IMMEDIATELY
# followed by "key" (adjacent segment pair), or the fused compound form.
_SECRET_KEY_SEGMENTS = {
    "token", "secret", "password", "passwd", "credential", "credentials",
    "apikey", "auth", "authorization",
}

# Adjacent segment pairs that make a name credential-shaped (D-21):
# MY_API_KEY / ACCESS_KEY / PRIVATE_KEY hit; PRIMARY_KEY / SSH_KEY do not.
_SECRET_KEY_ADJACENCIES = {
    ("api", "key"), ("access", "key"), ("private", "key"),
}

# Fused single-segment compound the split cannot separate ("ACCESSKEY").
_COMPOUND_KEY_RE = re.compile(r"^(api|access|private)[_-]?key$")


def key_is_secret(name: str) -> bool:
    n = str(name).lower().strip()
    if not n:
        return False
    if _COMPOUND_KEY_RE.match(n):
        return True
    segs = [seg for seg in re.split(r"[\s_\-.]+", n) if seg]
    if any(seg in _SECRET_KEY_SEGMENTS for seg in segs):
        return True
    return any((segs[i], segs[i + 1]) in _SECRET_KEY_ADJACENCIES
               for i in range(len(segs) - 1))


def redact_str(s: str) -> str:
    if not isinstance(s, str):
        return s
    for kind, pat in _PATTERNS:
        if kind == "kv":
            s = pat.sub(lambda m: m.group(1) + m.group(2) + REDACTED, s)
        else:
            s = pat.sub(REDACTED, s)
    return s


def _walk(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_str(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            if SECRET_FILE_RE.search(ks) or key_is_secret(ks):
                out[ks] = REDACTED
            else:
                out[ks] = _walk(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_walk(x) for x in obj]
    return obj


def redact_obj(obj: Any) -> Any:
    """Recursively redact a JSON-like payload (returns a plain structure)."""
    return _walk(obj)


def scan_secrets(text: str) -> list[str]:
    """Return kinds of secret patterns present (for artifact-export re-scan)."""
    hits: list[str] = []
    for kind, pat in _PATTERNS:
        if kind != "kv" and pat.search(text):
            hits.append(kind)
        elif kind == "kv":
            m = pat.search(text)
            if m and m.group(3).strip("\"'") != REDACTED:
                hits.append(kind)
    return hits


def iter_secret_kinds() -> Iterable[str]:
    return [k for k, _ in _PATTERNS]
