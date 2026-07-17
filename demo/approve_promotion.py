#!/usr/bin/env python
"""
Approve Promotion — CLI tool til human approval af agent promotions.

Kør:
  python demo/approve_promotion.py

Viser alle agenter der afventer godkendelse til real execution.
Kræver eksplicit ja/nej per agent.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QUEUE_KEY = "challenge:promotion_queue"
AUDIT_LOG_PATH = "logs/challenge_audit.jsonl"


def _get_redis():
    try:
        import redis as _redis  # type: ignore
        r = _redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=2)
        r.ping()
        return r
    except Exception:
        return None


def _load_queue(redis_client) -> list[dict]:
    if redis_client is not None:
        try:
            raw = redis_client.get(QUEUE_KEY)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return []


def _save_queue(redis_client, queue: list[dict]) -> None:
    if redis_client is not None:
        try:
            redis_client.set(QUEUE_KEY, json.dumps(queue))
        except Exception:
            pass


def _audit(agent_id: str, action: str) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "agent": agent_id,
        "level": "PROMOTION_DECISION",
        "msg": f"Human decision: {action}",
    }
    try:
        import pathlib
        p = pathlib.Path(AUDIT_LOG_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> None:
    print()
    print("+======================================================+")
    print("|       BabyAI Promotion Approval Tool                  |")
    print("|  Real execution kræver eksplicit human godkendelse    |")
    print("+======================================================+")
    print()

    r = _get_redis()
    queue = _load_queue(r)
    pending = [e for e in queue if e.get("status") == "pending_human_approval"]

    if not pending:
        print("  Ingen afventende promotion requests.")
        print()
        return

    print(f"  {len(pending)} afventende promotion request(s):\n")

    for entry in pending:
        agent_id = entry.get("agent_id", "unknown")
        perf = entry.get("performance_summary", {})
        criteria = entry.get("criteria_met", {})
        requested = entry.get("requested_at", "")

        print(f"  Agent: {agent_id}")
        print(f"  Anmodet: {requested}")
        print(f"  Kapital: {perf.get('capital_eur', 0):.4f} EUR")
        print(f"  Win rate: {perf.get('win_rate', 0):.2%}  |  Sharpe: {perf.get('sharpe', 0):.2f}")
        print(f"  Runder: {perf.get('rounds', 0)}")
        print(f"  Kriterier:")
        for criterion, met in criteria.items():
            mark = "✓" if met else "✗"
            print(f"    {mark} {criterion}")
        print()
        print("  ADVARSEL: Godkendelse tillader RIGTIGE penge-transaktioner")
        print("  (når real execution er implementeret med broker API)")
        print()

        while True:
            answer = input(f"  Godkend {agent_id} til real execution? [ja/nej]: ").strip().lower()
            if answer in ("ja", "nej", "j", "n", "yes", "no"):
                break
            print("  Svar venligst 'ja' eller 'nej'")

        approved = answer in ("ja", "j", "yes")
        for item in queue:
            if item.get("agent_id") == agent_id and item.get("status") == "pending_human_approval":
                if approved:
                    item["status"] = "approved"
                    item["approved_at"] = datetime.now(timezone.utc).isoformat()
                    print(f"  ✓ {agent_id} GODKENDT til real execution")
                else:
                    item["status"] = "rejected"
                    item["rejected_at"] = datetime.now(timezone.utc).isoformat()
                    print(f"  ✗ {agent_id} AFVIST — fortsætter paper-trading")
                _audit(agent_id, "approved" if approved else "rejected")
                break
        print()

    _save_queue(r, queue)
    print("  Beslutninger gemt.")
    print()


if __name__ == "__main__":
    main()
