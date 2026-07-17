"""
Billing domain models

Business entities for subscription and billing management.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field


class TierType(str, Enum):
    """Subscription tier types"""
    FREE = "free"
    TIER1 = "tier1"  # Watcher - 299 kr/md - signals only
    TIER2 = "tier2"  # Autopilot - 999 kr/md - BYOK auto-execute, 3 strategies
    TIER3 = "tier3"  # Pro - 4999 kr/md - all strategies, custom risk


class SubscriptionStatus(str, Enum):
    """Subscription status values"""
    INACTIVE = "inactive"
    ACTIVE = "active"
    CANCELLED = "cancelled"
    PAST_DUE = "past_due"
    UNPAID = "unpaid"


class TierInfo(BaseModel):
    """Information about a subscription tier"""
    name: str
    price_dkk: int
    price_id: str
    features: list[str] = []

    class Config:
        frozen = True


class Subscription(BaseModel):
    """Customer subscription model"""
    customer_id: str
    stripe_customer_id: Optional[str] = None
    stripe_sub_id: Optional[str] = None
    tier: TierType = TierType.FREE
    status: SubscriptionStatus = SubscriptionStatus.INACTIVE
    current_period_end: Optional[str] = None  # ISO timestamp
    created_at: str
    updated_at: str

    @classmethod
    def create_new(cls, customer_id: str, **kwargs) -> "Subscription":
        """Create a new subscription with timestamps"""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            customer_id=customer_id,
            created_at=now,
            updated_at=now,
            **kwargs
        )

    def update_status(self, status: SubscriptionStatus, **kwargs) -> "Subscription":
        """Update subscription status and other fields"""
        now = datetime.now(timezone.utc).isoformat()
        return self.copy(update={
            "status": status,
            "updated_at": now,
            **kwargs
        })

    def is_active(self) -> bool:
        """Check if subscription is currently active"""
        return self.status == SubscriptionStatus.ACTIVE

    def is_premium(self) -> bool:
        """Check if subscription is a premium tier"""
        return self.tier in [TierType.TIER1, TierType.TIER2, TierType.TIER3]


class BillingEvent(BaseModel):
    """Billing event record"""
    id: Optional[int] = None
    event_type: str
    stripe_event_id: Optional[str] = None
    customer_id: Optional[str] = None
    payload: Dict[str, Any]
    recorded_at: str

    @classmethod
    def create_new(
        cls,
        event_type: str,
        payload: Dict[str, Any],
        customer_id: Optional[str] = None,
        stripe_event_id: Optional[str] = None
    ) -> "BillingEvent":
        """Create a new billing event"""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            event_type=event_type,
            stripe_event_id=stripe_event_id,
            customer_id=customer_id,
            payload=payload,
            recorded_at=now
        )


class StripeWebhookEvent(BaseModel):
    """Stripe webhook event structure"""
    id: str
    type: str
    data: Dict[str, Any]
    created: int
    livemode: bool = False


class CheckoutSession(BaseModel):
    """Stripe checkout session data"""
    customer: Optional[str] = None
    subscription: Optional[str] = None
    metadata: Dict[str, str] = Field(default_factory=dict)
    status: str = "open"


class SubscriptionObject(BaseModel):
    """Stripe subscription object"""
    id: str
    customer: str
    status: str
    current_period_end: int
    items: Dict[str, Any] = Field(default_factory=dict)


class SubscriptionStats(BaseModel):
    """Subscription statistics"""
    total_by_tier: Dict[str, int]
    active_by_tier: Dict[str, int]
    total_active: int
    total_revenue_estimate: float = 0.0


# Configuration for subscription tiers
TIER_CONFIG: Dict[TierType, TierInfo] = {
    TierType.FREE: TierInfo(
        name="Free",
        price_dkk=0,
        price_id="",
        features=["Basic access", "Limited features"]
    ),
    TierType.TIER1: TierInfo(
        name="Watcher",
        price_dkk=299,
        price_id="",  # Set from environment
        features=["Signals only", "Basic monitoring"]
    ),
    TierType.TIER2: TierInfo(
        name="Autopilot",
        price_dkk=999,
        price_id="",  # Set from environment
        features=["BYOK auto-execute", "3 strategies", "Advanced monitoring"]
    ),
    TierType.TIER3: TierInfo(
        name="Pro",
        price_dkk=4999,
        price_id="",  # Set from environment
        features=["All strategies", "Custom risk management", "Premium support"]
    )
}


def get_tier_info(tier: TierType) -> TierInfo:
    """Get information about a subscription tier"""
    return TIER_CONFIG[tier]


def calculate_monthly_revenue(stats: SubscriptionStats) -> float:
    """Calculate estimated monthly revenue from subscription stats"""
    revenue = 0.0
    for tier_str, count in stats.active_by_tier.items():
        try:
            tier = TierType(tier_str)
            tier_info = get_tier_info(tier)
            revenue += tier_info.price_dkk * count
        except (ValueError, KeyError):
            continue
    return revenue