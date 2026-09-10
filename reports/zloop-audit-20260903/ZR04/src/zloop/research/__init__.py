"""zloop.research — M4 Research Broker: single Kimi K1 lane (decision D-10)."""
from .broker import run_research
from .kimi_server import KimiError, KimiServerLane

__all__ = ["run_research", "KimiServerLane", "KimiError"]
