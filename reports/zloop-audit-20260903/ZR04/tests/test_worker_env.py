"""Worker env allowlist construction tests (VOL-17 §3, P1-8).

The root environment of this machine measurably contains secret-shaped
names (ALIBABA_TOKEN_PLAN_API_KEY / ZAI_OAUTH_CLIENT_ID — 2026-09-02 audit);
these tests pin that an enumerative allowlist keeps them out and that
packet-declared extras can never smuggle a secret in.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop.worker_env import WORKER_ENV_ALLOWLIST, build_worker_env  # noqa: E402

# Secret-shaped names observed in this machine's root env (VOL-17 §3).
MACHINE_SECRET_ENVS = ("ALIBABA_TOKEN_PLAN_API_KEY", "ZAI_OAUTH_CLIENT_ID")


def test_allowlist_is_exact_vol17_set():
    assert WORKER_ENV_ALLOWLIST == {
        "PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP",
        "LANG", "LC_CTYPE", "PYTHONIOENCODING", "PYTHONUTF8",
        "HOME", "USERPROFILE",
    }


def test_built_env_contains_only_allowlisted_keys():
    env = build_worker_env()
    assert set(env) <= WORKER_ENV_ALLOWLIST
    assert env.get("PATH") == os.environ.get("PATH")  # allowed keys ARE copied


def test_machine_secret_envs_absent():
    # These exist in this machine's environment; they must never be copied.
    env = build_worker_env()
    for name in MACHINE_SECRET_ENVS:
        assert name not in env, f"{name} leaked into worker env"


def test_any_non_allowlisted_os_environ_key_is_dropped(monkeypatch):
    # Even when such a key demonstrably exists in os.environ (portable,
    # machine-independent proof of the allowlist mechanism).
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "sk-test")
    monkeypatch.setenv("ZAI_OAUTH_CLIENT_ID", "client-1")
    monkeypatch.setenv("SOME_PASSWORD", "hunter2")
    monkeypatch.setenv("ZCODE_PLUGIN_DATA", r"C:\somewhere")
    monkeypatch.setenv("MY_CUSTOM_VAR", "value")
    env = build_worker_env()
    assert set(env) <= WORKER_ENV_ALLOWLIST
    assert "ALIBABA_TOKEN_PLAN_API_KEY" not in env
    assert "ZAI_OAUTH_CLIENT_ID" not in env
    assert "SOME_PASSWORD" not in env
    assert "ZCODE_PLUGIN_DATA" not in env
    assert "MY_CUSTOM_VAR" not in env


def test_extra_non_secret_vars_are_added():
    env = build_worker_env({"ZLOOP_LAUNCH_ID": "L0001"})
    assert env["ZLOOP_LAUNCH_ID"] == "L0001"


def test_extra_overrides_allowlisted_value():
    env = build_worker_env({"TMP": "/custom/tmp"})
    assert env["TMP"] == "/custom/tmp"


def test_extra_none_equals_plain_allowlist_copy():
    assert build_worker_env() == build_worker_env(None)


def test_secret_named_extra_keys_are_rejected():
    for bad in ("MY_API_TOKEN", "AUTHORIZATION", "client_secret",
                "PASSWORD", "ALIBABA_TOKEN_PLAN_API_KEY", "db.credentials"):
        with pytest.raises(ValueError, match="secret"):
            build_worker_env({bad: "x"})


def test_secret_extra_rejection_happens_before_any_copy():
    # Rejection must not partially apply other extras either: fail-closed.
    # (Note: key_is_secret matches "MY_API_TOKEN" but, by its segment split,
    # not "MY_API_KEY" — see zloop.redact; the contract here is key_is_secret.)
    with pytest.raises(ValueError):
        build_worker_env({"SAFE_VAR": "ok", "MY_API_TOKEN": "sk-leak"})
