from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(items):
    if os.name != "nt":
        return
    marker = pytest.mark.skip(reason="golden shell scenarios run on POSIX/WSL")
    for item in items:
        if "tests/golden/" in item.nodeid.replace("\\", "/"):
            item.add_marker(marker)
