"""
Billing API Router

FastAPI endpoints for subscription management and Stripe integration.
"""

import hashlib
import hmac
import json
import logging
import os
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, HTTPException, Request, status, Depends
from pydantic import BaseModel

from ..database.postgresql_billing_store import get_billing_store, PostgreSQLBillingStore
from ..domain.models import (
    Subscription, BillingEvent, SubscriptionStats, TierInfo,
    TierType, SubscriptionStatus, TIER_CONFIG, calculate_monthly_revenue
)
from .stripe_client import StripeClient, get_stripe_client

logger = logging.getLogger(__name__)

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response"""
    ok: bool
    stripe_configured: bool
    database_connected: bool


class SubscriptionResponse(BaseModel):
    """Subscription response"""
    customer_id: str
    tier: TierType
    status: SubscriptionStatus
    stripe_customer_id: Optional[str] = None
    stripe_sub_id: Optional[str] = None
    current_period_end: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CancelSubscriptionRequest(BaseModel):
    """Request to cancel subscription"""
    reason: Optional[str] = None


class WebhookResponse(BaseModel):
    """Webhook response"""
    received: bool
    processed: bool = True


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    try:
        store = await get_billing_store()
        database_connected = store.pool is not None
    except Exception:
        database_connected = False

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")

    return HealthResponse(
        ok=True,
        stripe_configured=bool(stripe_key),
        database_connected=database_connected
    )


@router.get("/tiers", response_model=Dict[str, TierInfo])
async def get_tiers():
    """Get available subscription tiers"""
    # Update price IDs from environment
    updated_config = TIER_CONFIG.copy()
    updated_config[TierType.TIER1].price_id = os.getenv("STRIPE_PRICE_TIER1", "")
    updated_config[TierType.TIER2].price_id = os.getenv("STRIPE_PRICE_TIER2", "")
    updated_config[TierType.TIER3].price_id = os.getenv("STRIPE_PRICE_TIER3", "")

    return {tier.value: info for tier, info in updated_config.items()}


@router.get("/subscriptions/{customer_id}", response_model=SubscriptionResponse)
async def get_subscription(customer_id: str):
    """Get subscription status for customer"""
    store = await get_billing_store()
    subscription_data = await store.get_subscription(customer_id)

    if subscription_data is None:
        # Return default free subscription
        return SubscriptionResponse(
            customer_id=customer_id,
            tier=TierType.FREE,
            status=SubscriptionStatus.INACTIVE
        )

    return SubscriptionResponse(**subscription_data)


@router.post("/subscriptions/{customer_id}/cancel")
async def cancel_subscription(
    customer_id: str,
    request: CancelSubscriptionRequest,
    stripe_client: StripeClient = Depends(get_stripe_client)
):
    """Cancel customer subscription"""
    try:
        store = await get_billing_store()
        subscription_data = await store.get_subscription(customer_id)

        if subscription_data and subscription_data.get("stripe_sub_id"):
            # Cancel in Stripe if we have a Stripe subscription ID
            try:
                await stripe_client.cancel_subscription(subscription_data["stripe_sub_id"])
            except Exception as e:
                logger.warning(f"Failed to cancel Stripe subscription: {e}")

        # Update local subscription status
        await store.upsert_subscription(
            customer_id,
            status=SubscriptionStatus.CANCELLED.value,
            tier=TierType.FREE.value
        )

        # Log the cancellation event
        await store.log_billing_event(
            event_type="subscription.cancelled",
            customer_id=customer_id,
            stripe_event_id=None,
            payload={
                "customer_id": customer_id,
                "reason": request.reason,
                "cancelled_by": "api"
            }
        )

        return {"ok": True, "customer_id": customer_id}

    except Exception as e:
        logger.error(f"Error cancelling subscription for {customer_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/webhook/stripe", response_model=WebhookResponse)
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    try:
        # Get raw body and signature
        body = await request.body()
        signature = request.headers.get("stripe-signature", "")

        # Verify webhook signature
        if not _verify_webhook_signature(body, signature):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid signature"
            )

        # Parse event
        event_data = json.loads(body)
        event_type = event_data.get("type", "")
        event_id = event_data.get("id", "")

        # Extract customer ID from metadata if available
        customer_id = None
        data_object = event_data.get("data", {}).get("object", {})
        metadata = data_object.get("metadata", {})
        if "customer_id" in metadata:
            customer_id = metadata["customer_id"]
        elif "customer" in data_object:
            customer_id = data_object["customer"]

        # Log the event
        store = await get_billing_store()
        await store.log_billing_event(
            event_type=event_type,
            customer_id=customer_id,
            stripe_event_id=event_id,
            payload=event_data
        )

        # Handle specific event types
        processed = False
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(event_data)
            processed = True
        elif event_type == "customer.subscription.updated":
            await _handle_subscription_updated(event_data)
            processed = True
        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(event_data)
            processed = True

        return WebhookResponse(received=True, processed=processed)

    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON"
        )
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/admin/stats", response_model=SubscriptionStats)
async def get_subscription_stats():
    """Get subscription statistics (admin endpoint)"""
    try:
        store = await get_billing_store()
        stats_data = await store.get_subscription_stats()

        stats = SubscriptionStats(**stats_data)
        stats.total_revenue_estimate = calculate_monthly_revenue(stats)

        return stats
    except Exception as e:
        logger.error(f"Error getting subscription stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/admin/events", response_model=List[Dict[str, Any]])
async def get_billing_events(
    customer_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100
):
    """Get billing events (admin endpoint)"""
    try:
        store = await get_billing_store()
        events = await store.get_billing_events(
            customer_id=customer_id,
            event_type=event_type,
            limit=min(limit, 1000)  # Cap at 1000
        )
        return events
    except Exception as e:
        logger.error(f"Error getting billing events: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# Private helper functions

async def _handle_checkout_completed(event: Dict[str, Any]):
    """Handle successful checkout completion"""
    obj = event.get("data", {}).get("object", {})
    stripe_customer_id = obj.get("customer", "")
    metadata = obj.get("metadata", {})
    customer_id = metadata.get("customer_id", stripe_customer_id)
    tier = metadata.get("tier", "tier1")
    subscription_id = obj.get("subscription", "")

    store = await get_billing_store()
    await store.upsert_subscription(
        customer_id,
        stripe_customer_id=stripe_customer_id,
        stripe_sub_id=subscription_id,
        tier=tier,
        status=SubscriptionStatus.ACTIVE.value
    )

    logger.info(f"Checkout completed: customer={customer_id}, tier={tier}")


async def _handle_subscription_updated(event: Dict[str, Any]):
    """Handle subscription update from Stripe"""
    obj = event.get("data", {}).get("object", {})
    subscription_id = obj.get("id", "")
    status = obj.get("status", "")
    period_end = obj.get("current_period_end", 0)

    # Convert timestamp to ISO string
    period_end_iso = None
    if period_end:
        from datetime import datetime, timezone
        period_end_iso = datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat()

    store = await get_billing_store()
    subscription_data = await store.get_subscription_by_stripe_id(subscription_id)

    if subscription_data:
        mapped_status = SubscriptionStatus.ACTIVE if status == "active" else SubscriptionStatus.INACTIVE
        await store.upsert_subscription(
            subscription_data["customer_id"],
            status=mapped_status.value,
            current_period_end=period_end_iso
        )

        logger.info(f"Subscription updated: sub={subscription_id}, status={status}")


async def _handle_subscription_deleted(event: Dict[str, Any]):
    """Handle subscription cancellation from Stripe"""
    obj = event.get("data", {}).get("object", {})
    subscription_id = obj.get("id", "")

    store = await get_billing_store()
    subscription_data = await store.get_subscription_by_stripe_id(subscription_id)

    if subscription_data:
        await store.upsert_subscription(
            subscription_data["customer_id"],
            status=SubscriptionStatus.CANCELLED.value,
            tier=TierType.FREE.value
        )

        logger.info(f"Subscription cancelled: sub={subscription_id}")


def _verify_webhook_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """Verify Stripe webhook signature"""
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        # Development mode: skip verification
        return True

    try:
        # Parse signature header
        parts = {}
        for part in signature_header.split(","):
            if "=" in part:
                key, value = part.split("=", 1)
                if key == "t":
                    parts["timestamp"] = value
                elif key == "v1":
                    parts.setdefault("signatures", []).append(value)

        if "timestamp" not in parts or "signatures" not in parts:
            return False

        # Create signed payload
        signed_payload = f"{parts['timestamp']}.".encode() + payload_bytes

        # Verify signatures
        expected_signature = hmac.new(
            webhook_secret.encode(),
            signed_payload,
            hashlib.sha256
        ).hexdigest()

        return any(
            hmac.compare_digest(expected_signature, sig)
            for sig in parts["signatures"]
        )

    except Exception as e:
        logger.warning(f"Webhook signature verification failed: {e}")
        return False