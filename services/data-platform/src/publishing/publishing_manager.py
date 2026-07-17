"""
Publishing Manager Module - Consolidated from services/publisher/
"""

import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


class PublishingManager:
    """Content publishing service for multiple platforms"""

    def __init__(self, store, event_bus=None):
        self.store = store
        self.event_bus = event_bus

        # Supported platforms
        self.platforms = {
            "twitter": self._publish_twitter,
            "youtube": self._publish_youtube,
            "linkedin": self._publish_linkedin,
            "tiktok": self._publish_tiktok,
            "newsletter": self._publish_newsletter,
            "stub": self._publish_stub
        }

    async def initialize(self) -> None:
        """Initialize publishing manager"""
        logger.info("Publishing manager initialized")

    async def publish_content(self, content_data: Dict, platforms: List[str],
                             metadata: Optional[Dict] = None) -> List[str]:
        """Publish content to multiple platforms"""
        try:
            operation_ids = []

            for platform in platforms:
                if platform not in self.platforms:
                    logger.warning(f"Unknown platform: {platform}")
                    continue

                operation_id = f"publish_{uuid4().hex[:12]}"
                content_id = content_data.get("id", f"content_{uuid4().hex[:8]}")

                # Create publishing operation
                await self.store.create_publishing_operation(
                    operation_id=operation_id,
                    content_id=content_id,
                    platform=platform,
                    content_data=content_data,
                    metadata=metadata
                )

                # Publish started event
                if self.event_bus:
                    self.event_bus.publish_content_publish_started(operation_id, {
                        "platform": platform,
                        "content_id": content_id
                    })

                # Process publishing asynchronously
                asyncio.create_task(self._process_publishing_operation(operation_id))
                operation_ids.append(operation_id)

            return operation_ids

        except Exception as e:
            logger.error(f"Failed to publish content: {e}")
            raise

    async def _process_publishing_operation(self, operation_id: str) -> None:
        """Process publishing operation"""
        try:
            # Get operation data
            operation = await self.store.get_publishing_operation(operation_id)
            if not operation:
                raise ValueError(f"Operation not found: {operation_id}")

            platform = operation["platform"]
            content_data = operation["content_data"]

            # Update status to processing
            await self.store.update_publishing_operation(operation_id, publish_status="processing")

            # Publish to platform
            publish_func = self.platforms.get(platform, self._publish_stub)
            result = await publish_func(content_data, operation_id)

            # Update operation completion
            await self.store.update_publishing_operation(
                operation_id,
                publish_status="published",
                platform_ref=result["platform_ref"],
                platform_response=result.get("response", {}),
                published_at=datetime.utcnow()
            )

            # Publish success event
            if self.event_bus:
                self.event_bus.publish_content_published(operation_id, {
                    "platform": platform,
                    "platform_ref": result["platform_ref"]
                })

            logger.info(f"Content published: {operation_id} ({platform})")

        except Exception as e:
            logger.error(f"Publishing operation failed: {operation_id} - {e}")

            # Update operation as failed
            await self.store.update_publishing_operation(operation_id, publish_status="failed")

            # Publish failure event
            if self.event_bus:
                self.event_bus.publish_content_publish_failed(operation_id, {
                    "error": str(e)
                })

    async def _publish_twitter(self, content_data: Dict, operation_id: str) -> Dict:
        """Publish to Twitter"""
        await asyncio.sleep(0.5)  # Simulate API call
        return {"platform_ref": f"twitter_post_{uuid4().hex[:8]}", "response": {"status": "success"}}

    async def _publish_youtube(self, content_data: Dict, operation_id: str) -> Dict:
        """Publish to YouTube"""
        await asyncio.sleep(1.0)  # Simulate API call
        return {"platform_ref": f"youtube_video_{uuid4().hex[:8]}", "response": {"status": "success"}}

    async def _publish_linkedin(self, content_data: Dict, operation_id: str) -> Dict:
        """Publish to LinkedIn"""
        await asyncio.sleep(0.7)  # Simulate API call
        return {"platform_ref": f"linkedin_post_{uuid4().hex[:8]}", "response": {"status": "success"}}

    async def _publish_tiktok(self, content_data: Dict, operation_id: str) -> Dict:
        """Publish to TikTok"""
        await asyncio.sleep(1.2)  # Simulate API call
        return {"platform_ref": f"tiktok_video_{uuid4().hex[:8]}", "response": {"status": "success"}}

    async def _publish_newsletter(self, content_data: Dict, operation_id: str) -> Dict:
        """Publish to Newsletter"""
        await asyncio.sleep(0.3)  # Simulate API call
        return {"platform_ref": f"newsletter_{uuid4().hex[:8]}", "response": {"status": "success"}}

    async def _publish_stub(self, content_data: Dict, operation_id: str) -> Dict:
        """Stub publisher for testing"""
        await asyncio.sleep(0.1)
        return {"platform_ref": f"stub_{uuid4().hex[:8]}", "response": {"status": "stub_success"}}

    async def get_publishing_operation(self, operation_id: str) -> Optional[Dict]:
        """Get publishing operation by ID"""
        return await self.store.get_publishing_operation(operation_id)

    async def list_publishing_operations(self, platform: Optional[str] = None,
                                        status: Optional[str] = None) -> List[Dict]:
        """List publishing operations"""
        return await self.store.list_publishing_operations(platform, status)

    async def get_platform_status(self, platform: str) -> Dict:
        """Get platform status"""
        return {
            "platform": platform,
            "status": "available" if platform in self.platforms else "unsupported",
            "last_check": datetime.utcnow().isoformat()
        }

    def is_healthy(self) -> bool:
        return self.store is not None

    async def shutdown(self) -> None:
        logger.info("Publishing manager shutdown complete")