"""D-21: redact precision — credential-SHAPED key names only, not bare "key".

Bare segment "key" used to over-redact legitimate evidence fields
(primary_key / cache_key / public_key_id ...). key_is_secret now requires
an explicit secret segment OR an adjacent ("api"|"access"|"private","key")
segment pair OR the fused compound form ^(api|access|private)[_-]?key$.
Value-shape patterns (PEM/bearer/kv/sk-) are unchanged and still catch real
key material regardless of the field name.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import redact                          # noqa: E402
from zloop.redact import key_is_secret            # noqa: E402
from zloop.worker_env import build_worker_env     # noqa: E402

# ---- D-21 case table ---------------------------------------------------------

SECRET_NAMES = [
    "MY_API_KEY", "API_KEY", "ACCESS_KEY", "PRIVATE_KEY",
    "my_api_key", "api-key", "api key", "MY-ACCESS-KEY", "OPENAI_API_KEY",
    "ACCESSKEY", "PRIVATEKEY", "apikey",
    # unchanged explicit-set behavior
    "MY_API_TOKEN", "AUTHORIZATION", "client_secret", "PASSWORD",
    "db.credentials",
]

PLAIN_NAMES = [
    "primary_key", "cache_key", "public_key_id", "key_path", "keyboard_key",
    "ssh_key", "sort_key", "key", "KEY", "key_id", "foreign_keys",
    "author", "monkeys",
]
# NOTE: "token_bucket_name"-style names still redact by design — the explicit
# segment "token" is secret regardless of what follows it (rule (a) of D-21);
# D-21 only removed the bare "key" segment.


@pytest.mark.parametrize("name", SECRET_NAMES)
def test_credential_shaped_names_are_secret(name):
    assert key_is_secret(name) is True, name


@pytest.mark.parametrize("name", PLAIN_NAMES)
def test_plain_key_names_are_not_secret(name):
    assert key_is_secret(name) is False, name


def test_table_via_redact_obj():
    # The same table through the redaction walk itself: secret-named keys
    # lose their value, plain evidence keys keep theirs.
    payload = {name: "value-1" for name in SECRET_NAMES + PLAIN_NAMES}
    out = redact.redact_obj(payload)
    for name in SECRET_NAMES:
        assert out[name] == redact.REDACTED, name
    for name in PLAIN_NAMES:
        assert out[name] == "value-1", name


# ---- regression: value-shape patterns still catch real key material ----------

def test_pem_value_and_token_key_still_redacted():
    # "ssh_key" is NOT a secret name (D-21), but its PEM VALUE is redacted by
    # the pem pattern; "API_TOKEN" is redacted by name.
    pem = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
           "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc"
           "2gtZWQyNTUxOQAAACBleGFtcGxlZXhhbXBsZQAAAAAAAAAAAA==\n"
           "-----END OPENSSH PRIVATE KEY-----")
    out = redact.redact_obj({"API_TOKEN": "x", "ssh_key": pem})
    assert out["API_TOKEN"] == redact.REDACTED
    assert "BEGIN OPENSSH PRIVATE KEY" not in out["ssh_key"]
    assert "b3BlbnNza" not in out["ssh_key"]
    assert redact.REDACTED in out["ssh_key"]


def test_bearer_and_kv_patterns_unchanged():
    s = f"Authorization: Bearer {'a' * 32} and server.token: {'b' * 20}"
    r = redact.redact_str(s)
    assert "a" * 32 not in r and "b" * 20 not in r
    assert redact.REDACTED in r


def test_journal_payload_with_plain_key_names_survives(tmp_path):
    # Evidence-plane integration: legitimate key-shaped fields survive the
    # redaction walk (Journal.append), values under secret-shaped ones do not.
    from zloop import evidence
    j = evidence.Journal(tmp_path / "s.ndjson", tmp_path / "blobs")
    j.append(kind="tool_result", session_id="s", payload={
        "primary_key": "pk-1", "cache_key": "ck-2", "ssh_key": "fingerprint",
        "env": {"MY_API_KEY": "sk-live-abcdef123456"},
    })
    lines = evidence.read_journal(tmp_path / "s.ndjson")
    blob = json.dumps(lines)
    assert "sk-live-abcdef123456" not in blob
    inline = lines[0]["payload_inline"]
    assert '"primary_key":"pk-1"' in inline
    assert '"cache_key":"ck-2"' in inline


# ---- worker-env rejection path (VOL-17 §3) -----------------------------------

def test_worker_env_rejects_adjacent_api_key():
    # The rejection path still sees MY_API_KEY as secret via the adjacency
    # pair ("api","key") — a packet may never smuggle credentials into a
    # worker env, even now that bare "key" is no longer secret.
    with pytest.raises(ValueError, match="secret"):
        build_worker_env({"MY_API_KEY": "v"})


def test_worker_env_rejects_plain_secret_names_but_allows_key_names():
    with pytest.raises(ValueError, match="secret"):
        build_worker_env({"API_KEY": "v", "ACCESS_TOKEN": "v"})
    env = build_worker_env({"KEYBOARD_KEY": "f5", "CACHE_KEY": "abc"})
    assert env["KEYBOARD_KEY"] == "f5" and env["CACHE_KEY"] == "abc"
