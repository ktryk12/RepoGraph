"""
services/exercise_runner/main.py — Exercise Runner entrypoint.

Usage (Docker / direct):
  EXERCISE_SCENARIO=content_workflow EXERCISE_MODE=dry_run python main.py

Usage (via CLI):
  python -m babyai.cli exercise content_workflow --mode dry_run
  python -m babyai.cli exercise content_workflow --mode sandbox
  python -m babyai.cli exercise --list

Exit codes:
  0 = PASS
  1 = FAIL
  2 = PARTIAL
  3 = CONFIG_ERROR (bad mode, missing confirm, unknown scenario)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("exercise-runner")

# Add repo root to path so shared/ and scenarios/ resolve
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BROKERS   = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "kafka:9092"))
MODE      = os.getenv("EXERCISE_MODE", "dry_run").lower()
SCENARIO  = os.getenv("EXERCISE_SCENARIO", "")
LOG_DIR   = Path(os.getenv("BABYAI_LOG_DIR", "logs")) / "exercise"

_VALID_MODES = {"dry_run", "sandbox", "live"}


def main() -> int:
    from scenario_registry import get_scenario, list_scenarios
    from runner import ExerciseRunner

    # --list
    if SCENARIO in ("", "--list", "list"):
        print("Available scenarios:")
        for name in list_scenarios():
            print(f"  {name}")
        return 0

    # Validate mode
    if MODE not in _VALID_MODES:
        _log.error("Invalid EXERCISE_MODE=%r  valid: %s", MODE, _VALID_MODES)
        return 3

    # Look up scenario
    scenario = get_scenario(SCENARIO)
    if scenario is None:
        _log.error("Unknown scenario: %r  available: %s", SCENARIO, list_scenarios())
        return 3

    _log.info("exercise_starting scenario=%s mode=%s brokers=%s", SCENARIO, MODE, BROKERS)

    runner = ExerciseRunner(mode=MODE, brokers=BROKERS)
    result = runner.run(scenario)

    # Write trace to log file
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts      = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    outfile = LOG_DIR / f"{ts}_{result.exercise_id}_{SCENARIO}.json"
    outfile.write_text(
        json.dumps(result.to_log_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Console summary
    _banner(result.verdict)
    print(f"  Scenario : {result.scenario}")
    print(f"  Mode     : {result.mode}")
    print(f"  Exercise : {result.exercise_id}")
    print(f"  Duration : {result.duration_ms:.0f} ms")
    print(f"  Events   : {result.event_count}")
    print(f"  Seen     : {result.trace.topics_seen()}")
    if result.first_missing:
        print(f"  Missing  : {result.first_missing}  (first gap)")
    if result.last_seen:
        print(f"  Last seen: {result.last_seen}")
    if result.contract_failures:
        print(f"  Contracts: {len(result.contract_failures)} failure(s)")
        for f in result.contract_failures[:3]:
            print(f"    {f.topic} missing key '{f.missing_key}'")
    print(f"  Trace    : {outfile}")
    print()

    return result.exit_code


def _banner(verdict: str) -> None:
    width = 60
    icons = {"PASS": "✓", "PARTIAL": "~", "FAIL": "✗", "CONFIG_ERROR": "!"}
    icon  = icons.get(verdict, "?")
    print()
    print("=" * width)
    print(f"  {icon}  {verdict}")
    print("=" * width)


if __name__ == "__main__":
    sys.exit(main())
