"""
PromotionManager — håndterer overgang fra paper-trading til real execution.

Real execution er OPT-IN og kræver eksplicit human approval.
Ingen automatisk real execution — altid required_approval=True.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

AUDIT_LOG = Path("logs/challenge_audit.jsonl")

# In-memory fallback for promotion queue (bruges hvis Redis ikke er tilgængelig)
_MEMORY_PROMOTION_QUEUE: list[dict] = []


def _get_redis():
    """Forsøg at forbinde til Redis. Returner None ved fejl."""
    try:
        import redis as _redis  # type: ignore
        r = _redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=1)
        r.ping()
        return r
    except Exception:
        return None


class PromotionResult:
    """Resultat af en promotion eligibility check."""

    def __init__(
        self,
        eligible: bool,
        criteria_met: dict[str, bool],
        recommendation: str,
        required_approval: bool = True,
    ) -> None:
        self.eligible = eligible
        self.criteria_met = criteria_met
        self.recommendation = recommendation
        self.required_approval = required_approval  # altid True

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "criteria_met": self.criteria_met,
            "recommendation": self.recommendation,
            "required_approval": self.required_approval,
        }


class PromotionManager:
    """
    Evaluerer om en agent opfylder kriterier til promotion.

    Promotion til real execution kræver ALTID eksplicit human approval.
    """

    def __init__(self) -> None:
        self._redis = _get_redis()

    def check_promotion_eligibility(
        self,
        agent: Any,
        policy: Any,
    ) -> PromotionResult:
        """
        Tjekker om en agent opfylder alle promotion_criteria.

        Tjekker:
          - win_rate > 0.55
          - sharpe > 1.2
          - min_rounds >= 50
          - ingen L7 violations i de seneste 20 runder
        """
        criteria = policy.promotion_criteria
        perf = agent.performance

        win_rate_ok = float(perf.win_rate) > float(criteria.win_rate_min)
        sharpe_ok = float(perf.sharpe) > float(criteria.sharpe_min)
        rounds_ok = int(perf.rounds) >= int(criteria.min_rounds)
        no_l7 = not getattr(agent, "_recent_l7_violation", False)

        criteria_met = {
            f"win_rate > {criteria.win_rate_min}": win_rate_ok,
            f"sharpe > {criteria.sharpe_min}": sharpe_ok,
            f"min_rounds >= {criteria.min_rounds}": rounds_ok,
            "no_l7_violations_last_20": no_l7,
        }
        eligible = all(criteria_met.values())

        if eligible:
            rec = "Klar til real execution — alle kriterier opfyldt"
        else:
            failed = [k for k, v in criteria_met.items() if not v]
            rec = f"Ikke klar — mangler: {', '.join(failed)}"

        return PromotionResult(
            eligible=eligible,
            criteria_met=criteria_met,
            recommendation=rec,
            required_approval=True,  # ALTID
        )

    def request_promotion_approval(
        self,
        agent_id: str,
        result: PromotionResult,
        performance_summary: dict[str, Any] | None = None,
    ) -> None:
        """
        Sæt promotion i kø til human approval.

        Skriver til Redis "challenge:promotion_queue" og audit log.
        Printer visuelt banner til stdout.
        """
        entry = {
            "agent_id": str(agent_id),
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "performance_summary": performance_summary or {},
            "criteria_met": result.criteria_met,
            "recommendation": result.recommendation,
            "status": "pending_human_approval",
        }

        # Gem i Redis eller memory
        _MEMORY_PROMOTION_QUEUE.append(entry)
        if self._redis is not None:
            try:
                raw = self._redis.get("challenge:promotion_queue") or b"[]"
                queue = json.loads(raw)
                queue.append(entry)
                self._redis.set("challenge:promotion_queue", json.dumps(queue))
            except Exception as exc:
                logger.warning("PromotionManager: Redis write fejlede: %s", exc)

        # Audit log
        self._audit(agent_id, entry, level="PROMOTION_REQUEST")

        # Print banner
        perf = performance_summary or {}
        win = perf.get("win_rate", 0)
        sharpe = perf.get("sharpe", 0)
        cap = perf.get("capital_eur", 0)
        print()
        print("+==========================================+")
        print("|  PROMOTION REQUEST — HUMAN APPROVAL REQ  |")
        print(f"|  Agent: {agent_id:<34}|")
        print(f"|  Win rate: {win:.2f} · Sharpe: {sharpe:.2f}{'':<16}|")
        print(f"|  Kapital: {cap:.3f} EUR{'':<24}|")
        print("|  Klar til REAL execution med 30 EUR max  |")
        print("|  Kør: python demo/approve_promotion.py   |")
        print("+==========================================+")
        print()

    def get_pending_promotions(self) -> list[dict]:
        """Hent alle afventende promotions."""
        if self._redis is not None:
            try:
                raw = self._redis.get("challenge:promotion_queue") or b"[]"
                queue = json.loads(raw)
                return [e for e in queue if e.get("status") == "pending_human_approval"]
            except Exception:
                pass
        return [e for e in _MEMORY_PROMOTION_QUEUE if e.get("status") == "pending_human_approval"]

    def _audit(self, agent_id: str, entry: dict, level: str = "PROMOTION_REQUEST") -> None:
        """Skriv til audit log."""
        try:
            AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
            log_entry = {
                "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "agent": agent_id,
                "level": level,
                "msg": entry.get("recommendation", "promotion_request"),
                "data": entry,
            }
            with AUDIT_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
