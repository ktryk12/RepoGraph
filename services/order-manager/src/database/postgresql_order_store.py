"""
PostgreSQL store for order management service

Handles orders, positions, and trading data persistence.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import uuid4

import asyncpg
from asyncpg import Pool

logger = logging.getLogger(__name__)


class PostgreSQLOrderStore:
    """PostgreSQL storage for order management data"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[Pool] = None

    async def initialize(self):
        """Initialize the connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=20,
                command_timeout=60
            )
            logger.info("PostgreSQL order store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize order store: {e}")
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

    async def upsert_order(self, order: Dict[str, Any]) -> None:
        """Create or update order record"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO orders (
                    order_id, signal_id, strategy_id, symbol, side, quantity,
                    entry_price, stop_loss_price, take_profit_price, sl_order_id, tp_order_id,
                    state, filled_qty, avg_price, pnl, created_at, updated_at, closed_at, meta
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19
                )
                ON CONFLICT (order_id) DO UPDATE SET
                    state = EXCLUDED.state,
                    entry_price = EXCLUDED.entry_price,
                    stop_loss_price = EXCLUDED.stop_loss_price,
                    take_profit_price = EXCLUDED.take_profit_price,
                    sl_order_id = EXCLUDED.sl_order_id,
                    tp_order_id = EXCLUDED.tp_order_id,
                    filled_qty = EXCLUDED.filled_qty,
                    avg_price = EXCLUDED.avg_price,
                    pnl = EXCLUDED.pnl,
                    updated_at = EXCLUDED.updated_at,
                    closed_at = EXCLUDED.closed_at,
                    meta = EXCLUDED.meta
            """,
                order["order_id"], order.get("signal_id"), order.get("strategy_id"),
                order["symbol"], order["side"], order["quantity"],
                order.get("entry_price"), order.get("stop_loss_price"), order.get("take_profit_price"),
                order.get("sl_order_id"), order.get("tp_order_id"),
                order["state"], order.get("filled_qty", 0), order.get("avg_price", 0),
                order.get("pnl", 0), order["created_at"], order["updated_at"],
                order.get("closed_at"), order.get("meta", "{}")
            )

    async def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order by ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM orders WHERE order_id = $1", order_id
            )
            return dict(row) if row else None

    async def get_order_by_sl_or_tp(self, exchange_order_id: str) -> Optional[Dict[str, Any]]:
        """Get order by stop-loss or take-profit order ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM orders
                WHERE sl_order_id = $1 OR tp_order_id = $1
            """, exchange_order_id)
            return dict(row) if row else None

    async def get_orders_by_symbol(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get orders for a specific symbol"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM orders
                WHERE symbol = $1
                ORDER BY created_at DESC
                LIMIT $2
            """, symbol, limit)
            return [dict(row) for row in rows]

    async def get_orders_by_state(self, state: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get orders by state"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM orders
                WHERE state = $1
                ORDER BY created_at DESC
                LIMIT $2
            """, state, limit)
            return [dict(row) for row in rows]

    async def open_position(self, order: Dict[str, Any]) -> str:
        """Open a new position"""
        position_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO positions (
                    position_id, order_id, symbol, quantity, entry_price,
                    current_price, unrealized_pnl, opened_at, is_open
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
                position_id, order["order_id"], order["symbol"],
                order["filled_qty"], order["avg_price"], order["avg_price"],
                0.0, now, True
            )

        return position_id

    async def close_position(self, order_id: str, exit_price: float, pnl: float) -> None:
        """Close positions for an order"""
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute("""
                UPDATE positions
                SET is_open = FALSE, closed_at = $1, current_price = $2, unrealized_pnl = $3
                WHERE order_id = $4 AND is_open = TRUE
            """, now, exit_price, pnl, order_id)

    async def get_position(self, position_id: str) -> Optional[Dict[str, Any]]:
        """Get position by ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM positions WHERE position_id = $1", position_id
            )
            return dict(row) if row else None

    async def get_positions_by_order(self, order_id: str) -> List[Dict[str, Any]]:
        """Get positions for an order"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM positions WHERE order_id = $1 ORDER BY opened_at",
                order_id
            )
            return [dict(row) for row in rows]

    async def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all open positions, optionally filtered by symbol"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            if symbol:
                rows = await conn.fetch("""
                    SELECT * FROM positions
                    WHERE is_open = TRUE AND symbol = $1
                    ORDER BY opened_at DESC
                """, symbol)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM positions
                    WHERE is_open = TRUE
                    ORDER BY opened_at DESC
                """)
            return [dict(row) for row in rows]

    async def update_position_price(self, position_id: str, current_price: float) -> None:
        """Update position current price and unrealized PnL"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            # Get position to calculate PnL
            position = await conn.fetchrow(
                "SELECT * FROM positions WHERE position_id = $1", position_id
            )
            if not position:
                return

            entry_price = float(position["entry_price"])
            quantity = float(position["quantity"])
            unrealized_pnl = (current_price - entry_price) * quantity

            await conn.execute("""
                UPDATE positions
                SET current_price = $1, unrealized_pnl = $2
                WHERE position_id = $3
            """, current_price, unrealized_pnl, position_id)

    async def get_order_stats(self) -> Dict[str, Any]:
        """Get order statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            # Orders by state
            state_stats = await conn.fetch("""
                SELECT state, COUNT(*) as count
                FROM orders
                GROUP BY state
                ORDER BY state
            """)

            # Orders by symbol (top 10)
            symbol_stats = await conn.fetch("""
                SELECT symbol, COUNT(*) as count
                FROM orders
                GROUP BY symbol
                ORDER BY count DESC
                LIMIT 10
            """)

            # PnL statistics
            pnl_stats = await conn.fetchrow("""
                SELECT
                    SUM(pnl) as total_pnl,
                    AVG(pnl) as avg_pnl,
                    COUNT(*) FILTER (WHERE pnl > 0) as winning_orders,
                    COUNT(*) FILTER (WHERE pnl < 0) as losing_orders,
                    COUNT(*) FILTER (WHERE state = 'CLOSED') as total_closed
                FROM orders
                WHERE state = 'CLOSED'
            """)

            # Open positions count
            open_positions = await conn.fetchval(
                "SELECT COUNT(*) FROM positions WHERE is_open = TRUE"
            )

            return {
                "orders_by_state": {row["state"]: row["count"] for row in state_stats},
                "top_symbols": {row["symbol"]: row["count"] for row in symbol_stats},
                "pnl_statistics": dict(pnl_stats) if pnl_stats else {},
                "open_positions_count": open_positions or 0
            }

    async def get_recent_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent orders"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM orders
                ORDER BY created_at DESC
                LIMIT $1
            """, limit)
            return [dict(row) for row in rows]

    async def get_performance_metrics(self, days: int = 30) -> Dict[str, Any]:
        """Get performance metrics for the last N days"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        from datetime import timedelta
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            metrics = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_orders,
                    COUNT(*) FILTER (WHERE state = 'FILLED') as filled_orders,
                    COUNT(*) FILTER (WHERE state = 'FAILED') as failed_orders,
                    SUM(pnl) FILTER (WHERE state = 'CLOSED') as total_pnl,
                    AVG(pnl) FILTER (WHERE state = 'CLOSED' AND pnl != 0) as avg_pnl,
                    COUNT(*) FILTER (WHERE state = 'CLOSED' AND pnl > 0) as winning_trades,
                    COUNT(*) FILTER (WHERE state = 'CLOSED' AND pnl < 0) as losing_trades
                FROM orders
                WHERE created_at >= $1
            """, cutoff_date)

            return dict(metrics) if metrics else {}


# Global store instance
order_store: Optional[PostgreSQLOrderStore] = None


async def get_order_store() -> PostgreSQLOrderStore:
    """Get the global order store instance"""
    global order_store
    if order_store is None:
        raise RuntimeError("Order store not initialized")
    return order_store


async def initialize_order_store(database_url: str):
    """Initialize the global order store"""
    global order_store
    order_store = PostgreSQLOrderStore(database_url)
    await order_store.initialize()


async def close_order_store():
    """Close the global order store"""
    global order_store
    if order_store:
        await order_store.close()
        order_store = None