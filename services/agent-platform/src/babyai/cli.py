"""
babyai/cli.py — BabyAI command-line interface.

Usage:
    python -m babyai.cli approve-gap   <gap_id>
    python -m babyai.cli reject-gap    <gap_id>
    python -m babyai.cli approve-trade <trade_id>
    python -m babyai.cli reject-trade  <trade_id>
    python -m babyai.cli exercise      <scenario> [--mode dry_run|sandbox|live]
    python -m babyai.cli exercise      --list

Gap commands operate on logs/gap_detector.log (JSON lines).
Trade commands operate on logs/pending_trades.log (JSON lines).
All commands update the status field in-place by rewriting the file.
Exercise mode runs end-to-end scenarios via the existing Kafka bus.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LOG_DIR        = Path(os.getenv("BABYAI_LOG_DIR", "logs"))
_GAP_LOG        = _LOG_DIR / "gap_detector.log"
_BOOTSTRAP_LOG  = _LOG_DIR / "agent_bootstrap.log"
_TRADE_LOG      = _LOG_DIR / "pending_trades.log"
_TRADE_EXEC_LOG = _LOG_DIR / "trade_execution.log"

try:
    from tools.kafka_provisioner import TopicSpec
    from babyai.agents.bootstrap.agent_bootstrap_usecase import AgentBootstrapUseCase, AgentSpec
except ImportError:
    TopicSpec = None  # type: ignore[assignment,misc]
    AgentBootstrapUseCase = None  # type: ignore[assignment,misc]
    AgentSpec = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Gap log helpers
# ---------------------------------------------------------------------------

def _read_all_gaps() -> List[Dict[str, Any]]:
    if not _GAP_LOG.exists():
        return []
    lines = []
    for raw in _GAP_LOG.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return lines


def _find_gap(gap_id: str) -> Optional[Dict[str, Any]]:
    for entry in _read_all_gaps():
        if entry.get("gap_id") == gap_id:
            return entry
    return None


def _rewrite_gap_status(gap_id: str, new_status: str) -> bool:
    entries = _read_all_gaps()
    found   = False
    updated = []
    for entry in entries:
        if entry.get("gap_id") == gap_id:
            entry["status"]     = new_status
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            found               = True
        updated.append(entry)

    if not found:
        return False

    _GAP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _GAP_LOG.open("w", encoding="utf-8") as fh:
        for entry in updated:
            fh.write(json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")
    return True


# ---------------------------------------------------------------------------
# Trade log helpers
# ---------------------------------------------------------------------------

def _read_all_trades() -> List[Dict[str, Any]]:
    if not _TRADE_LOG.exists():
        return []
    lines = []
    for raw in _TRADE_LOG.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return lines


def _find_trade(trade_id: str) -> Optional[Dict[str, Any]]:
    for entry in _read_all_trades():
        if entry.get("trade_id") == trade_id:
            return entry
    return None


def _rewrite_trade_status(trade_id: str, new_status: str) -> bool:
    entries = _read_all_trades()
    found   = False
    updated = []
    for entry in entries:
        if entry.get("trade_id") == trade_id:
            entry["status"]     = new_status
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            found               = True
        updated.append(entry)

    if not found:
        return False

    _TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _TRADE_LOG.open("w", encoding="utf-8") as fh:
        for entry in updated:
            fh.write(json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")
    return True


def _append_exec_log(entry: Dict[str, Any]) -> None:
    _TRADE_EXEC_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _TRADE_EXEC_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------------------
# approve-gap
# ---------------------------------------------------------------------------

def approve_gap(gap_id: str) -> Tuple[bool, str]:
    """
    Approve a gap proposal by gap_id.

    Steps:
      1. Find the gap in gap_detector.log
      2. Update status to "approved"
      3. Build AgentSpec and call AgentBootstrapUseCase.execute()
      4. Return (success, message)
    """
    entry = _find_gap(gap_id)
    if entry is None:
        return False, f"Gap not found: {gap_id}"

    current_status = entry.get("status", "pending")
    if current_status == "approved":
        return True, f"Gap {gap_id} is already approved."
    if current_status == "rejected":
        return False, f"Gap {gap_id} was already rejected and cannot be re-approved."

    if not _rewrite_gap_status(gap_id, "approved"):
        return False, f"Failed to update status for gap {gap_id}"

    try:
        if AgentBootstrapUseCase is None or AgentSpec is None or TopicSpec is None:
            return False, "AgentBootstrapUseCase unavailable (missing dependencies)"

        topic       = entry.get("topic", "")
        suggested   = entry.get("suggested_agent", "UnknownAgent")
        agent_class = f"agents.{suggested.lower()}.{suggested}"

        spec = AgentSpec(
            agent_id          = f"{suggested.lower()}-{gap_id[:8]}",
            agent_class       = agent_class,
            topics_to_consume = [TopicSpec(name=topic)] if topic else [],
            topics_to_produce = [],
            consumer_group_id = f"{suggested.lower()}-group",
            policy_ref        = f"gap:{gap_id}",
            approved_by       = "human",
        )

        use_case = AgentBootstrapUseCase(log_path=str(_BOOTSTRAP_LOG))
        result   = use_case.execute(spec)

        if result.success:
            return True, (
                f"Gap {gap_id} approved.\n"
                f"AgentBootstrapUseCase succeeded for {spec.agent_id}.\n"
                f"Topics provisioned: {[t.topic for t in result.topics_created]}"
            )
        else:
            return False, (
                f"Gap {gap_id} approved but bootstrap failed: {result.error}\n"
                f"See {_BOOTSTRAP_LOG} for details."
            )
    except Exception as exc:
        return False, f"Approval failed with unexpected error: {exc}"


# ---------------------------------------------------------------------------
# reject-gap
# ---------------------------------------------------------------------------

def reject_gap(gap_id: str) -> Tuple[bool, str]:
    """Reject a gap proposal. Status set to 'rejected', no action taken."""
    entry = _find_gap(gap_id)
    if entry is None:
        return False, f"Gap not found: {gap_id}"

    current_status = entry.get("status", "pending")
    if current_status == "rejected":
        return True, f"Gap {gap_id} was already rejected."
    if current_status == "approved":
        return False, f"Gap {gap_id} is already approved and cannot be rejected."

    if not _rewrite_gap_status(gap_id, "rejected"):
        return False, f"Failed to update status for gap {gap_id}"

    return True, f"Gap {gap_id} rejected. No action will be taken."


# ---------------------------------------------------------------------------
# approve-trade
# ---------------------------------------------------------------------------

def approve_trade(trade_id: str) -> Tuple[bool, str]:
    """
    Approve a pending trade by trade_id.

    Steps:
      1. Find the trade in logs/pending_trades.log
      2. Update status to "approved"
      3. Instantiate EToroClient with mode from the trade record
      4. Call place_order with requires_confirm=False
      5. Log outcome to logs/trade_execution.log
      6. Return (success, message)
    """
    entry = _find_trade(trade_id)
    if entry is None:
        return False, f"Trade not found: {trade_id}"

    current_status = entry.get("status", "pending")
    if current_status == "approved":
        return True, f"Trade {trade_id} is already approved."
    if current_status == "rejected":
        return False, f"Trade {trade_id} was already rejected and cannot be re-approved."

    if not _rewrite_trade_status(trade_id, "approved"):
        return False, f"Failed to update status for trade {trade_id}"

    try:
        from tools.etoro_client import EToroClient  # noqa: PLC0415

        # Temporarily set mode from the trade record
        mode = entry.get("mode", "demo")
        os.environ.setdefault("ETORO_MODE", mode)

        client = EToroClient()
        # Override mode from trade record (may differ from env default)
        client.mode = mode

        result = client.place_order(
            instrument_id    = entry.get("instrument_id", 0),
            amount           = entry.get("amount", 0.0),
            is_buy           = entry.get("is_buy", True),
            requires_confirm = False,   # human approved via CLI
        )

        success = "error" not in result
        exec_entry = {
            "event":        "trade_execution",
            "trade_id":     trade_id,
            "executed_at":  datetime.now(timezone.utc).isoformat(),
            "mode":         mode,
            "instrument_id": entry.get("instrument_id"),
            "amount":       entry.get("amount"),
            "is_buy":       entry.get("is_buy"),
            "result":       result,
            "success":      success,
        }
        _append_exec_log(exec_entry)

        if success:
            return True, (
                f"Trade {trade_id} approved and executed in {mode} mode.\n"
                f"Result: {result}"
            )
        else:
            return False, (
                f"Trade {trade_id} approved but execution failed: {result.get('error')}\n"
                f"See {_TRADE_EXEC_LOG} for details."
            )
    except Exception as exc:
        return False, f"Trade approval failed with unexpected error: {exc}"


# ---------------------------------------------------------------------------
# reject-trade
# ---------------------------------------------------------------------------

def reject_trade(trade_id: str) -> Tuple[bool, str]:
    """Reject a pending trade. Status set to 'rejected', no order placed."""
    entry = _find_trade(trade_id)
    if entry is None:
        return False, f"Trade not found: {trade_id}"

    current_status = entry.get("status", "pending")
    if current_status == "rejected":
        return True, f"Trade {trade_id} was already rejected."
    if current_status == "approved":
        return False, f"Trade {trade_id} is already approved and cannot be rejected."

    if not _rewrite_trade_status(trade_id, "rejected"):
        return False, f"Failed to update status for trade {trade_id}"

    return True, f"Trade {trade_id} rejected. No order will be placed."


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: List[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        _print_usage()
        return 1

    command = args[0].lower()

    # exercise command: flexible argument parsing
    if command == "exercise":
        return _exercise_command(args[1:])

    if len(args) < 2:
        _print_usage()
        return 1

    item_id = args[1]

    if command == "approve-gap":
        ok, msg = approve_gap(item_id)
    elif command == "reject-gap":
        ok, msg = reject_gap(item_id)
    elif command == "approve-trade":
        ok, msg = approve_trade(item_id)
    elif command == "reject-trade":
        ok, msg = reject_trade(item_id)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 1

    print(msg)
    return 0 if ok else 1


def _print_usage() -> None:
    print("Usage:")
    print("  python -m babyai.cli approve-gap   <gap_id>")
    print("  python -m babyai.cli reject-gap    <gap_id>")
    print("  python -m babyai.cli approve-trade <trade_id>")
    print("  python -m babyai.cli reject-trade  <trade_id>")
    print("  python -m babyai.cli exercise      <scenario> [--mode dry_run|sandbox|live]")
    print("  python -m babyai.cli exercise      --list")


def _exercise_command(args: List[str]) -> int:
    """
    Handle: exercise <scenario> [--mode MODE]
            exercise --list
    """
    # --list
    if not args or args[0] in ("--list", "list"):
        return _exercise_list()

    scenario_name = args[0]

    # Parse --mode flag (positional or --mode=value or --mode value)
    mode = "dry_run"
    for i, arg in enumerate(args[1:], 1):
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1]
            break
        if arg == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            break

    return run_exercise(scenario_name, mode)


def _exercise_list() -> int:
    """Print available scenarios and exit 0."""
    try:
        _root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(_root / "services" / "exercise_runner"))
        sys.path.insert(0, str(_root))
        from scenario_registry import list_scenarios
        print("Available scenarios:")
        for name in list_scenarios():
            print(f"  {name}")
        return 0
    except ImportError as exc:
        print(f"Exercise runner not available: {exc}", file=sys.stderr)
        return 3


def run_exercise(scenario_name: str, mode: str = "dry_run") -> int:
    """
    Run an exercise scenario in-process by invoking the exercise runner main module.

    Exit codes:
      0 = PASS
      1 = FAIL
      2 = PARTIAL
      3 = CONFIG_ERROR / unavailable
    """
    import subprocess

    _root = Path(__file__).resolve().parent.parent
    runner_main = _root / "services" / "exercise_runner" / "main.py"

    if not runner_main.exists():
        print(f"Exercise runner not found: {runner_main}", file=sys.stderr)
        return 3

    env = os.environ.copy()
    env["EXERCISE_SCENARIO"] = scenario_name
    env["EXERCISE_MODE"]     = mode
    env["DRY_RUN"]           = "false" if mode == "live" else "true"
    # Propagate BABYAI_LOG_DIR if set
    env.setdefault("BABYAI_LOG_DIR", str(_LOG_DIR))

    result = subprocess.run(
        [sys.executable, str(runner_main)],
        env=env,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
