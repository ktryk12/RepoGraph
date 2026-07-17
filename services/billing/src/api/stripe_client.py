"""
Stripe API Client

Handles communication with Stripe API for subscription management.
"""

import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


class StripeError(Exception):
    """Stripe API error"""
    pass


class StripeClient:
    """Async Stripe API client"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("STRIPE_SECRET_KEY", "")
        self.base_url = "https://api.stripe.com/v1"
        self.api_version = "2024-04-10"

        if not self.api_key:
            logger.warning("Stripe API key not configured")

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to Stripe API"""
        if not self.api_key:
            raise StripeError("Stripe API key not configured")

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Stripe-Version": self.api_version,
            "Content-Type": "application/x-www-form-urlencoded"
        }

        body = None
        if data:
            body = urlencode(data).encode()

        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                    timeout=30.0
                )

                if response.status_code >= 400:
                    error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                    error_msg = error_data.get("error", {}).get("message", f"HTTP {response.status_code}")
                    raise StripeError(f"Stripe API error: {error_msg}")

                return response.json()

            except httpx.RequestError as e:
                raise StripeError(f"Network error: {e}")

    async def create_customer(
        self,
        email: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Create a new customer"""
        data = {}
        if email:
            data["email"] = email
        if metadata:
            for key, value in metadata.items():
                data[f"metadata[{key}]"] = value

        return await self._request("POST", "/customers", data)

    async def get_customer(self, customer_id: str) -> Dict[str, Any]:
        """Get customer by ID"""
        return await self._request("GET", f"/customers/{customer_id}")

    async def create_checkout_session(
        self,
        price_id: str,
        customer_id: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        mode: str = "subscription"
    ) -> Dict[str, Any]:
        """Create a checkout session"""
        data = {
            "mode": mode,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
        }

        if customer_id:
            data["customer"] = customer_id

        if success_url:
            data["success_url"] = success_url

        if cancel_url:
            data["cancel_url"] = cancel_url

        if metadata:
            for key, value in metadata.items():
                data[f"metadata[{key}]"] = value

        return await self._request("POST", "/checkout/sessions", data)

    async def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Get subscription by ID"""
        return await self._request("GET", f"/subscriptions/{subscription_id}")

    async def cancel_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Cancel a subscription"""
        return await self._request("DELETE", f"/subscriptions/{subscription_id}")

    async def update_subscription(
        self,
        subscription_id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Update subscription"""
        data = {}
        for key, value in kwargs.items():
            if value is not None:
                data[key] = str(value)

        return await self._request("POST", f"/subscriptions/{subscription_id}", data)

    async def list_prices(self, limit: int = 10) -> Dict[str, Any]:
        """List available prices"""
        data = {"limit": str(limit)}
        return await self._request("GET", "/prices", data)

    async def create_billing_portal_session(
        self,
        customer_id: str,
        return_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a billing portal session"""
        data = {"customer": customer_id}
        if return_url:
            data["return_url"] = return_url

        return await self._request("POST", "/billing_portal/sessions", data)

    async def retrieve_event(self, event_id: str) -> Dict[str, Any]:
        """Retrieve a Stripe event"""
        return await self._request("GET", f"/events/{event_id}")

    async def list_events(
        self,
        limit: int = 10,
        type_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """List Stripe events"""
        data = {"limit": str(limit)}
        if type_filter:
            data["type"] = type_filter

        return await self._request("GET", "/events", data)


# Global client instance
_stripe_client: Optional[StripeClient] = None


def get_stripe_client() -> StripeClient:
    """Get the global Stripe client instance"""
    global _stripe_client
    if _stripe_client is None:
        _stripe_client = StripeClient()
    return _stripe_client


def initialize_stripe_client(api_key: Optional[str] = None) -> StripeClient:
    """Initialize the global Stripe client"""
    global _stripe_client
    _stripe_client = StripeClient(api_key)
    return _stripe_client