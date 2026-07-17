"""
PostgreSQL store for broker-gateway risk management

Handles risk state persistence, risk decision audit trail, and risk analytics.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta, date
from typing import Dict, List, Optional, Any, Tuple
from uuid import uuid4

import asyncpg
from asyncpg import Pool

logger = logging.getLogger(__name__)


class PostgreSQLRiskStore:
    """PostgreSQL storage for trading risk management"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[Pool] = None

    async def initialize(self):
        """Initialize the connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=15,
                command_timeout=60
            )
            logger.info("PostgreSQL risk store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize risk store: {e}")
            raise

    async def close(self):
        """Close the connection pool"""
        if self.pool:
            await self.pool.close()

    @asynccontextmanager
    async def transaction(self):
        """Context manager for database transactions"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    # === RISK STATE PERSISTENCE ===

    async def save_risk_state(
        self,
        date_key: str,
        daily_pnl: float,
        position_notionals: Dict[str, float],
        open_position_count: int
    ) -> None:
        """Save daily risk state"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO risk_state (
                    date_key, daily_pnl, position_notionals, open_position_count, updated_at
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (date_key) DO UPDATE SET
                    daily_pnl = EXCLUDED.daily_pnl,
                    position_notionals = EXCLUDED.position_notionals,
                    open_position_count = EXCLUDED.open_position_count,
                    updated_at = EXCLUDED.updated_at
            """,
                date_key, daily_pnl, json.dumps(position_notionals),
                open_position_count, datetime.now(timezone.utc).isoformat()
            )

    async def load_risk_state(self, date_key: str) -> Optional[Dict[str, Any]]:
        """Load risk state for a specific date"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM risk_state WHERE date_key = $1", date_key
            )

            if not row:
                return None

            result = dict(row)
            # Parse JSON position_notionals back to dict
            if result.get("position_notionals"):
                try:
                    result["position_notionals"] = json.loads(result["position_notionals"])
                except json.JSONDecodeError:
                    result["position_notionals"] = {}

            return result

    async def get_current_risk_state(self) -> Dict[str, Any]:
        """Get risk state for today"""
        today = date.today().isoformat()
        state = await self.load_risk_state(today)

        if not state:
            return {
                "date_key": today,
                "daily_pnl": 0.0,
                "position_notionals": {},
                "open_position_count": 0,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

        return state

    # === RISK DECISION AUDIT LOG ===

    async def log_risk_decision(
        self,
        order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float,
        decision: str,
        reason: str,
        risk_checks: Dict[str, Any],
        risk_limits: Dict[str, Any]
    ) -> None:
        """Log a risk decision for audit purposes"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO risk_decisions (
                    id, order_id, symbol, side, order_type, quantity, price,
                    decision, reason, risk_checks, risk_limits, timestamp
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
                str(uuid4()), order_id, symbol, side, order_type, quantity, price,
                decision, reason, json.dumps(risk_checks), json.dumps(risk_limits),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_risk_decisions(
        self,
        hours: int = 24,
        decision_filter: Optional[str] = None,
        symbol_filter: Optional[str] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Get recent risk decisions with optional filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        query_parts = ["SELECT * FROM risk_decisions WHERE timestamp >= $1"]
        params = [cutoff_time]
        param_count = 1

        if decision_filter:
            param_count += 1
            query_parts.append(f"AND decision = ${param_count}")
            params.append(decision_filter)

        if symbol_filter:
            param_count += 1
            query_parts.append(f"AND symbol = ${param_count}")
            params.append(symbol_filter)

        query_parts.append("ORDER BY timestamp DESC")
        param_count += 1
        query_parts.append(f"LIMIT ${param_count}")
        params.append(limit)

        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                # Parse JSON fields
                for json_field in ["risk_checks", "risk_limits"]:
                    if result.get(json_field):
                        try:
                            result[json_field] = json.loads(result[json_field])
                        except json.JSONDecodeError:
                            result[json_field] = {}
                results.append(result)
            return results

    # === POSITION FILLS TRACKING ===

    async def record_position_fill(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        notional: float,
        pnl: float = 0.0,
        commission: float = 0.0,
        exchange_order_id: Optional[str] = None
    ) -> None:
        """Record a position fill for risk tracking"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO position_fills (
                    id, order_id, exchange_order_id, symbol, side, quantity,
                    price, notional, pnl, commission, filled_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
                str(uuid4()), order_id, exchange_order_id, symbol, side,
                quantity, price, notional, pnl, commission,
                datetime.now(timezone.utc).isoformat()
            )

    async def get_position_fills(
        self,
        symbol: Optional[str] = None,
        days: int = 7,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Get position fills with optional symbol filter"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        if symbol:
            query = """
                SELECT * FROM position_fills
                WHERE filled_at >= $1 AND symbol = $2
                ORDER BY filled_at DESC
                LIMIT $3
            """
            params = [cutoff_time, symbol, limit]
        else:
            query = """
                SELECT * FROM position_fills
                WHERE filled_at >= $1
                ORDER BY filled_at DESC
                LIMIT $2
            """
            params = [cutoff_time, limit]

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    # === RISK ANALYTICS ===

    async def get_risk_statistics(self, days: int = 7) -> Dict[str, Any]:
        """Get risk management statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            # Risk decisions summary
            decision_stats = await conn.fetch("""
                SELECT decision, COUNT(*) as count
                FROM risk_decisions
                WHERE timestamp >= $1
                GROUP BY decision
                ORDER BY count DESC
            """, cutoff_time)

            # Risk rejection reasons
            rejection_reasons = await conn.fetch("""
                SELECT reason, COUNT(*) as count
                FROM risk_decisions
                WHERE timestamp >= $1 AND decision = 'REJECTED'
                GROUP BY reason
                ORDER BY count DESC
                LIMIT 10
            """, cutoff_time)

            # Daily PnL trend
            pnl_trend = await conn.fetch("""
                SELECT date_key, daily_pnl
                FROM risk_state
                WHERE date_key >= $1
                ORDER BY date_key
            """, (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat())

            # Position distribution
            current_state = await self.get_current_risk_state()
            position_notionals = current_state.get("position_notionals", {})

            # Trading volume by symbol
            volume_stats = await conn.fetch("""
                SELECT symbol, COUNT(*) as trades, SUM(notional) as total_notional
                FROM position_fills
                WHERE filled_at >= $1
                GROUP BY symbol
                ORDER BY total_notional DESC
                LIMIT 20
            """, cutoff_time)

            return {
                "time_window_days": days,
                "cutoff_time": cutoff_time,
                "risk_decisions": {row["decision"]: row["count"] for row in decision_stats},
                "top_rejection_reasons": [{"reason": row["reason"], "count": row["count"]} for row in rejection_reasons],
                "pnl_trend": [{"date": row["date_key"], "pnl": float(row["daily_pnl"])} for row in pnl_trend],
                "current_positions": len(position_notionals),
                "current_position_notionals": position_notionals,
                "current_daily_pnl": current_state.get("daily_pnl", 0.0),
                "volume_by_symbol": [{"symbol": row["symbol"], "trades": row["trades"], "notional": float(row["total_notional"])} for row in volume_stats]
            }

    async def get_risk_limits_breach_history(self, days: int = 30) -> List[Dict[str, Any]]:
        """Get history of risk limit breaches"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT timestamp, symbol, reason, risk_checks, risk_limits
                FROM risk_decisions
                WHERE timestamp >= $1 AND decision = 'REJECTED'
                ORDER BY timestamp DESC
                LIMIT 500
            """, cutoff_time)

            results = []
            for row in rows:
                result = dict(row)
                # Parse JSON fields
                for json_field in ["risk_checks", "risk_limits"]:
                    if result.get(json_field):
                        try:
                            result[json_field] = json.loads(result[json_field])
                        except json.JSONDecodeError:
                            result[json_field] = {}
                results.append(result)
            return results

    async def cleanup_old_data(self, days: int = 90) -> Dict[str, int]:
        """Clean up old risk data beyond retention period"""
        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cutoff_date = (date.today() - timedelta(days=days)).isoformat()

        async with self.transaction() as conn:
            # Clean old risk decisions
            decisions_result = await conn.execute(
                "DELETE FROM risk_decisions WHERE timestamp < $1", cutoff_time
            )
            decisions_deleted = int(decisions_result.split()[-1]) if decisions_result.split()[-1].isdigit() else 0

            # Clean old position fills
            fills_result = await conn.execute(
                "DELETE FROM position_fills WHERE filled_at < $1", cutoff_time
            )
            fills_deleted = int(fills_result.split()[-1]) if fills_result.split()[-1].isdigit() else 0

            # Clean old risk state (keep more history for risk state)
            state_result = await conn.execute(
                "DELETE FROM risk_state WHERE date_key < $1", cutoff_date
            )
            states_deleted = int(state_result.split()[-1]) if state_result.split()[-1].isdigit() else 0

            return {
                "risk_decisions_deleted": decisions_deleted,
                "position_fills_deleted": fills_deleted,
                "risk_states_deleted": states_deleted
            }

    async def get_health_status(self) -> Dict[str, Any]:
        """Get database health status"""
        if not self.pool:
            return {"status": "disconnected", "pool": None}

        try:
            async with self.pool.acquire() as conn:
                # Test basic connectivity
                result = await conn.fetchval("SELECT 1")

                # Get table counts
                risk_decisions_count = await conn.fetchval("SELECT COUNT(*) FROM risk_decisions")
                position_fills_count = await conn.fetchval("SELECT COUNT(*) FROM position_fills")
                risk_states_count = await conn.fetchval("SELECT COUNT(*) FROM risk_state")

                # Get current risk state
                current_state = await self.get_current_risk_state()

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "risk_decisions_logged": risk_decisions_count,
                    "position_fills_recorded": position_fills_count,
                    "risk_states_stored": risk_states_count,
                    "current_daily_pnl": current_state.get("daily_pnl", 0.0),
                    "current_open_positions": current_state.get("open_position_count", 0),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global store instance
risk_store: Optional[PostgreSQLRiskStore] = None


async def get_risk_store() -> PostgreSQLRiskStore:
    """Get the global risk store instance"""
    global risk_store
    if risk_store is None:
        raise RuntimeError("Risk store not initialized")
    return risk_store


async def initialize_risk_store(database_url: str):
    """Initialize the global risk store"""
    global risk_store
    risk_store = PostgreSQLRiskStore(database_url)
    await risk_store.initialize()


async def close_risk_store():
    """Close the global risk store"""
    global risk_store
    if risk_store:
        await risk_store.close()
        risk_store = None