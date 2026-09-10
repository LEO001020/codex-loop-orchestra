"""zloop.research.port_discovery — Port discovery probe for Kimi Server (58627-58635).

Probes the loopback port range for an active Kimi Server HTTP instance,
returning the base URL of the first responding instance or None if unreachable.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import Iterable, Optional

DEFAULT_PORT_RANGE = range(58627, 58636)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PROBE_TIMEOUT_S = 1.0


def probe_port(port: int, host: str = DEFAULT_HOST,
               timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> bool:
    """Probe GET /api/v1/healthz on a target host:port."""
    url = f"http://{host}:{port}/api/v1/healthz"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status == 200
    except Exception:
        return False


def discover_kimi_port(port_range: Iterable[int] = DEFAULT_PORT_RANGE,
                        host: str = DEFAULT_HOST,
                        timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> Optional[str]:
    """Scan port range for an active Kimi HTTP server.

    Returns the base_url (e.g. 'http://127.0.0.1:58627') if found, else None.
    """
    for port in port_range:
        if probe_port(port, host=host, timeout_s=timeout_s):
            return f"http://{host}:{port}"
    return None
