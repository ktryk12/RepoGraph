"""Migration shim — kode er i services/lora-orchestrator/src/main.py"""
import sys as _sys
import os as _os

_src = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "services", "lora-orchestrator", "src",
)
if _src not in _sys.path:
    _sys.path.insert(0, _src)

from main import *  # noqa: F401, F403
from main import main, _build_redis_client, _parse_gap  # noqa: F401

if __name__ == "__main__":
    raise SystemExit(main())
