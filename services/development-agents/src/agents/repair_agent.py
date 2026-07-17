"""Migration shim — kode er i services/repair-agent/src/repair_agent.py"""
import sys as _sys
import os as _os

_src = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "services", "repair-agent", "src",
)
if _src not in _sys.path:
    _sys.path.insert(0, _src)

from repair_agent import *  # noqa: F401, F403
from repair_agent import RepairAgent, _load_curriculum_report  # noqa: F401
