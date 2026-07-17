"""
services/billing/main.py — Billing service with PostgreSQL and FastAPI

Migrated from SQLite to PostgreSQL with modern FastAPI architecture.

Endpoints:
  POST /api/v1/webhook/stripe       — Stripe webhook handler
  GET  /api/v1/subscriptions/{cid}  — get subscription status
  POST /api/v1/subscriptions/{cid}/cancel — cancel subscription
  GET  /api/v1/health               — health check
  GET  /api/v1/tiers                — available subscription tiers
  GET  /api/v1/admin/stats          — subscription statistics
  GET  /api/v1/admin/events         — billing events

Tiers:
  tier1 "Watcher"   — 299 kr/md — signals only
  tier2 "Autopilot" — 999 kr/md — BYOK auto-execute, 3 strategies
  tier3 "Pro"       — 4999 kr/md — all strategies, custom risk

Env vars:
  DATABASE_URL             : PostgreSQL connection string
  STRIPE_SECRET_KEY        : Stripe API key
  STRIPE_WEBHOOK_SECRET    : webhook signing secret
  BILLING_PORT             : HTTP port (default: 8134)
  STRIPE_PRICE_TIER1       : Stripe price ID for tier1
  STRIPE_PRICE_TIER2       : Stripe price ID for tier2
  STRIPE_PRICE_TIER3       : Stripe price ID for tier3
"""

import sys
from pathlib import Path

# Add src to Python path for imports
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from src.billing_service import app

if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.getenv("BILLING_PORT", "8134"))
    uvicorn.run(app, host="0.0.0.0", port=port)
