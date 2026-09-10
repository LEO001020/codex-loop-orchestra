"""Tests for port discovery probe, Kimi CLI lane, and fallback broker integration."""
import json
import subprocess
import pytest
from unittest.mock import patch, MagicMock

from zloop.research.port_discovery import probe_port, discover_kimi_port
from zloop.research.kimi_cli import KimiCliLane
from zloop.research.kimi_server import KimiServerLane, HEALTH_OK, HEALTH_QUOTA_EXHAUSTED, HEALTH_SERVER_UNAVAILABLE
from zloop.research.broker import run_research, _run_question


# ---- Test Port Discovery --------------------------------------------------

def test_probe_port_success():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        assert probe_port(58627) is True


def test_probe_port_failure():
    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        assert probe_port(58627) is False


def test_discover_kimi_port_found():
    def mock_probe(port, host="127.0.0.1", timeout_s=1.0):
        return port == 58629

    with patch("zloop.research.port_discovery.probe_port", side_effect=mock_probe):
        url = discover_kimi_port(range(58627, 58635))
        assert url == "http://127.0.0.1:58629"


def test_discover_kimi_port_none():
    with patch("zloop.research.port_discovery.probe_port", return_value=False):
        url = discover_kimi_port(range(58627, 58635))
        assert url is None


# ---- Test KimiCliLane ------------------------------------------------------

def test_kimi_cli_lane_no_exe():
    lane = KimiCliLane(exe_path="/invalid/path/to/kimi_non_existent")
    with patch.object(lane, "ensure_server", return_value=False):
        res = lane.ask("What is Python?")
        assert res["provider_health"] == HEALTH_SERVER_UNAVAILABLE
        assert "not found" in res["error"]


def test_kimi_cli_lane_success():
    lane = KimiCliLane(exe_path="kimi")
    mock_stdout = (
        json.dumps({"role": "user", "content": "What is Python?"}) + "\n"
        + json.dumps({"role": "assistant", "content": "Python is a programming language."}) + "\n"
    )
    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = mock_stdout
    mock_completed.stderr = ""

    with patch.object(lane, "ensure_server", return_value=True), \
         patch("subprocess.run", return_value=mock_completed):
        res = lane.ask("What is Python?")
        assert res["provider_health"] == HEALTH_OK
        assert res["answer"] == "Python is a programming language."
        assert res["last_turn_reason"] == "completed"


def test_kimi_cli_lane_quota_exhausted():
    lane = KimiCliLane(exe_path="kimi")
    mock_completed = MagicMock()
    mock_completed.returncode = 1
    mock_completed.stdout = ""
    mock_completed.stderr = "Error: usage limit exceeded (provider.api_error)"

    with patch.object(lane, "ensure_server", return_value=True), \
         patch("subprocess.run", return_value=mock_completed):
        res = lane.ask("What is Python?")
        assert res["provider_health"] == HEALTH_QUOTA_EXHAUSTED
        assert res["answer"] is None


# ---- Test Broker Integration & Fallback -------------------------------------

def test_broker_fallback_to_cli_when_server_unavailable(tmp_path):
    spec = {"questions": [{"id": "Q1", "query": "Test query"}]}

    # Server lane ask returning HEALTH_SERVER_UNAVAILABLE
    server_lane = KimiServerLane()
    server_lane.ask = MagicMock(return_value={
        "answer": None,
        "provider_health": HEALTH_SERVER_UNAVAILABLE,
        "last_turn_reason": "failed",
        "error": "Server connection failed"
    })

    # CLI lane returning success
    cli_mock_res = {
        "answer": "CLI answer result",
        "provider_health": HEALTH_OK,
        "last_turn_reason": "completed",
        "raw_messages": [],
        "error": None
    }

    with patch("zloop.research.broker.KimiCliLane") as mock_cli_class:
        mock_cli_instance = MagicMock()
        mock_cli_instance.ensure_server.return_value = True
        mock_cli_instance.ask.return_value = cli_mock_res
        mock_cli_class.return_value = mock_cli_instance

        out = run_research(tmp_path, spec, lane=server_lane)
        assert len(out["results"]) == 1
        res = out["results"][0]
        assert res["answer"] == "CLI answer result"
        assert res["provider_health"] == HEALTH_OK
