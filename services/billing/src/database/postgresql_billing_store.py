"""
PostgreSQL store for billing service

Handles subscriptions, billing events, and customer data persistence.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import asyncpg
from asyncpg import Pool

logger = logging.getLogger(__name__)


class PostgreSQLBillingStore:
    """PostgreSQL storage for billing data"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[Pool] = None

    async def initialize(self):
        """Initialize the connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=1,
                max_size=10,
                command_timeout=60
            )
            logger.info("PostgreSQL billing store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize billing store: {e}")
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

    async def upsert_subscription(self, customer_id: str, **kwargs) -> None:
        """Create or update subscription record"""
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            # Check if subscription exists
            existing = await conn.fetchval(
                "SELECT customer_id FROM subscriptions WHERE customer_id = $1",
                customer_id
            )

            if existing:
                # Update existing subscription
                set_clauses = []
                values = []
                param_count = 1

                for key, value in kwargs.items():
                    param_count += 1
                    set_clauses.append(f"{key} = ${param_count}")
                    values.append(value)

                param_count += 1
                set_clauses.append(f"updated_at = ${param_count}")
                values.append(now)

                param_count += 1
                values.append(customer_id)  # for WHERE clause

                query = f"""
                    UPDATE subscriptions
                    SET {', '.join(set_clauses)}
                    WHERE customer_id = ${param_count}
                """
                await conn.execute(query, *values)

            else:
                # Insert new subscription
                kwargs.setdefault("tier", "free")
                kwargs.setdefault("status", "inactive")

                columns = ["customer_id"] + list(kwargs.keys()) + ["created_at", "updated_at"]
                placeholders = [f"${i+1}" for i in range(len(columns))]
                values = [customer_id] + list(kwargs.values()) + [now, now]

                query = f"""
                    INSERT INTO subscriptions ({', '.join(columns)})
                    VALUES ({', '.join(placeholders)})
                """
                await conn.execute(query, *values)

    async def get_subscription(self, customer_id: str) -> Optional[Dict]:
        """Get subscription by customer ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM subscriptions WHERE customer_id = $1",
                customer_id
            )
            return dict(row) if row else None

    async def get_subscription_by_stripe_id(self, stripe_sub_id: str) -> Optional[Dict]:
        """Get subscription by Stripe subscription ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM subscriptions WHERE stripe_sub_id = $1",
                stripe_sub_id
            )
            return dict(row) if row else None

    async def log_billing_event(
        self,
        event_type: str,
        customer_id: Optional[str],
        stripe_event_id: Optional[str],
        payload: Dict[str, Any]
    ) -> None:
        """Log a billing event"""
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO billing_events (event_type, stripe_event_id, customer_id, payload, recorded_at)
                VALUES ($1, $2, $3, $4, $5)
            """, event_type, stripe_event_id, customer_id, json.dumps(payload), now)

    async def get_billing_events(
        self,
        customer_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get billing events with optional filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        query_parts = ["SELECT * FROM billing_events WHERE 1=1"]
        params = []
        param_count = 0

        if customer_id:
            param_count += 1
            query_parts.append(f"AND customer_id = ${param_count}")
            params.append(customer_id)

        if event_type:
            param_count += 1
            query_parts.append(f"AND event_type = ${param_count}")
            params.append(event_type)

        query_parts.append("ORDER BY recorded_at DESC")
        param_count += 1
        query_parts.append(f"LIMIT ${param_count}")
        params.append(limit)

        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def get_active_subscriptions(self) -> List[Dict]:
        """Get all active subscriptions"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM subscriptions WHERE status = 'active' ORDER BY created_at DESC"
            )
            return [dict(row) for row in rows]

    async def get_subscription_stats(self) -> Dict[str, Any]:
        """Get subscription statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            # Total subscriptions by tier
            tier_stats = await conn.fetch("""
                SELECT tier, COUNT(*) as count
                FROM subscriptions
                GROUP BY tier
                ORDER BY tier
            """)

            # Active subscriptions by tier
            active_stats = await conn.fetch("""
                SELECT tier, COUNT(*) as count
                FROM subscriptions
                WHERE status = 'active'
                GROUP BY tier
                ORDER BY tier
            """)

            # Total revenue (this would need actual pricing data)
            total_active = await conn.fetchval(
                "SELECT COUNT(*) FROM subscriptions WHERE status = 'active'"
            )

            return {
                "total_by_tier": {row["tier"]: row["count"] for row in tier_stats},
                "active_by_tier": {row["tier"]: row["count"] for row in active_stats},
                "total_active": total_active or 0
            }


# Global store instance
billing_store: Optional[PostgreSQLBillingStore] = None


async def get_billing_store() -> PostgreSQLBillingStore:
    """Get the global billing store instance"""
    global billing_store
    if billing_store is None:
        raise RuntimeError("Billing store not initialized")
    return billing_store


async def initialize_billing_store(database_url: str):
    """Initialize the global billing store"""
    global billing_store
    billing_store = PostgreSQLBillingStore(database_url)
    await billing_store.initialize()


async def close_billing_store():
    """Close the global billing store"""
    global billing_store
    if billing_store:
        await billing_store.close()
        billing_store = None