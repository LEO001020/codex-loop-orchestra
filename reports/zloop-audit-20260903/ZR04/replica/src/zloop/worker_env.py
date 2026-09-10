"""zloop.worker_env — allowlist-constructed subprocess environment (VOL-17 §3, P1-8).

The root environment on this machine measurably contains sensitive names
(``ALIBABA_TOKEN_PLAN_API_KEY``, ``ZAI_OAUTH_CLIENT_ID``,
``ZAI_BUSINESS_BASE_URL`` — 2026-09-02 audit). An enumerative denylist can
never cover that, so every environment zloop hands to a subprocess is built
by ALLOWLIST only:

- ``WORKER_ENV_ALLOWLIST`` is the exact VOL-17 §3 set (PATH/SYSTEMROOT/... —
  the bare minimum for a working toolchain).
- ``build_worker_env`` copies only allowlisted keys from ``os.environ`` and
  layers the packet's explicitly declared minimal variables (``extra``) on
  top. Extra keys whose NAME looks like a secret (``zloop.redact.key_is_secret``,
  e.g. ``MY_API_TOKEN`` / ``AUTHORIZATION``) are rejected with ``ValueError``
  — a packet may never smuggle credentials into a worker env.

Worker envs must never contain: ``ZCODE_PLUGIN_DATA``, C2C credentials,
exchange secrets, or any ``~/.zloop`` path value (VOL-17 §3).
"""
from __future__ import annotations

import os

from .redact import key_is_secret

# VOL-17 §3 exact set (uppercase: Windows normalizes env keys to upper case,
# and POSIX conventions keep these names uppercase anyway).
WORKER_ENV_ALLOWLIST = {
    "PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP",
    "LANG", "LC_CTYPE", "PYTHONIOENCODING", "PYTHONUTF8",
    "HOME", "USERPROFILE",
}


def build_worker_env(extra: dict | None = None) -> dict:
    """Build a worker subprocess env: allowlisted os.environ keys + ``extra``.

    ``extra`` holds packet-declared minimal variables (VOL-17 §3 "+ packet
    显式声明的最小变量"); its keys must not be secret-shaped
    (``zloop.redact.key_is_secret``) — such keys raise ``ValueError`` rather
    than ever reaching a worker process. Nothing else from ``os.environ``
    is copied: not secrets, not ``ZCODE_*``, nothing.
    """
    env = {k: os.environ[k] for k in WORKER_ENV_ALLOWLIST if k in os.environ}
    for key, value in (extra or {}).items():
        if key_is_secret(str(key)):
            raise ValueError(
                f"worker env extra key {key!r} looks like a secret "
                f"(zloop.redact.key_is_secret); secrets never enter a "
                f"worker environment (VOL-17 §3)")
        env[str(key)] = value
    return env
