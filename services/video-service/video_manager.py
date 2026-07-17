"""
Video Manager Module

Consolidated from services/claude-video/
Provides video generation with Claude script generation and external renderers.
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional, Any
from datetime import datetime
from uuid import uuid4
from pathlib import Path

logger = logging.getLogger(__name__)


class VideoManager:
    """
    Video generation and rendering service

    Consolidated functionality from claude-video service:
    - Video script generation via Claude
    - Integration with external video renderers (RunwayML, Synthesia, HeyGen)
    - Video job lifecycle management
    - Artifact storage and management
    """

    def __init__(self, store, event_bus=None):
        self.store = store
        self.event_bus = event_bus

        # Configuration from environment
        self.video_renderer = os.getenv("VIDEO_RENDERER", "stub")
        self.renderer_api_key = os.getenv("VIDEO_RENDERER_API_KEY", "")
        self.artifact_dir = Path(os.getenv("BABYAI_ARTIFACT_STORE", "artifacts"))
        self.claude_model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

        # Ensure artifact directory exists
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

        # Supported renderers
        self.supported_renderers = {
            "stub": self._render_video_stub,
            "runway": self._render_video_runway,
            "synthesia": self._render_video_synthesia,
            "heygen": self._render_video_heygen
        }

    async def initialize(self) -> None:
        """Initialize video manager"""
        try:
            logger.info(f"Video manager initialized with renderer: {self.video_renderer}")

            # Validate renderer
            if self.video_renderer not in self.supported_renderers:
                logger.warning(f"Unknown video renderer: {self.video_renderer}, falling back to stub")
                self.video_renderer = "stub"

            # Check API key for non-stub renderers
            if self.video_renderer != "stub" and not self.renderer_api_key:
                logger.warning(f"No API key provided for {self.video_renderer}, using stub mode")
                self.video_renderer = "stub"

        except Exception as e:
            logger.error(f"Failed to initialize video manager: {e}")
            raise

    async def create_video_job(self, request_data: Dict, renderer: Optional[str] = None,
                              metadata: Optional[Dict] = None) -> str:
        """Create a new video generation job"""
        try:
            job_id = f"video_job_{uuid4().hex[:12]}"
            used_renderer = renderer or self.video_renderer

            # Create job record
            await self.store.create_video_job(
                job_id=job_id,
                request_data=request_data,
                renderer=used_renderer,
                metadata=metadata
            )

            # Publish job started event
            if self.event_bus:
                self.event_bus.publish_video_job_started(job_id, {
                    "renderer": used_renderer,
                    "request_data": request_data
                })

            # Process job asynchronously
            asyncio.create_task(self._process_video_job(job_id))

            logger.info(f"Video job created: {job_id}")
            return job_id

        except Exception as e:
            logger.error(f"Failed to create video job: {e}")
            raise

    async def _process_video_job(self, job_id: str) -> None:
        """Process video generation job"""
        try:
            # Get job data
            job = await self.store.get_video_job(job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")

            # Update status to processing
            await self.store.update_video_job(job_id, status="processing")

            start_time = datetime.utcnow()

            # Step 1: Generate script with Claude
            script = await self._generate_video_script(job["request_data"])
            await self.store.update_video_job(job_id, script_generated=script)

            # Step 2: Render video
            renderer_func = self.supported_renderers.get(job["video_renderer"], self._render_video_stub)
            artifact_path = await renderer_func(job_id, script, job["request_data"])

            # Calculate duration
            duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Update job completion
            await self.store.update_video_job(
                job_id,
                status="completed",
                artifact_path=str(artifact_path)
            )

            # Publish completion event
            if self.event_bus:
                self.event_bus.publish_video_job_completed(job_id, {
                    "duration_ms": duration_ms,
                    "artifact_path": str(artifact_path),
                    "script": script
                })

            logger.info(f"Video job completed: {job_id}")

        except Exception as e:
            logger.error(f"Video job failed: {job_id} - {e}")

            # Update job as failed
            await self.store.update_video_job(job_id, status="failed")

            # Publish failure event
            if self.event_bus:
                self.event_bus.publish_video_job_failed(job_id, {
                    "error": str(e)
                })

    async def _generate_video_script(self, request_data: Dict) -> str:
        """Generate video script using Claude"""
        try:
            # Extract prompt from request data
            prompt = request_data.get("prompt", "Create a video script")
            context = request_data.get("context", "")

            # Enhance prompt for video script generation
            enhanced_prompt = f"""
Generate a detailed video script for the following request:

Request: {prompt}
Context: {context}

Please provide:
1. Scene descriptions
2. Visual elements
3. Narration/dialogue
4. Duration estimates
5. Transitions

Format the script clearly with scene numbers and timing.
"""

            # TODO: Integrate with actual Claude API
            # For now, return a mock script
            mock_script = f"""
VIDEO SCRIPT - Generated {datetime.utcnow().isoformat()}

SCENE 1 - OPENING (0:00-0:05)
Visual: Fade in from black
Narration: "{prompt}"
Duration: 5 seconds

SCENE 2 - MAIN CONTENT (0:05-0:25)
Visual: Main content visualization
Context: {context}
Duration: 20 seconds

SCENE 3 - CLOSING (0:25-0:30)
Visual: Fade to logo/end screen
Narration: Conclusion
Duration: 5 seconds

TOTAL DURATION: 30 seconds
"""
            return mock_script

        except Exception as e:
            logger.error(f"Failed to generate video script: {e}")
            return f"Error generating script: {str(e)}"

    async def _render_video_stub(self, job_id: str, script: str, request_data: Dict) -> Path:
        """Stub video renderer - creates text file artifact"""
        try:
            artifact_path = self.artifact_dir / f"{job_id}_script.txt"

            # Write script to file
            with open(artifact_path, "w", encoding="utf-8") as f:
                f.write(f"Video Job: {job_id}\n")
                f.write(f"Generated: {datetime.utcnow().isoformat()}\n")
                f.write(f"Request: {request_data}\n\n")
                f.write("SCRIPT:\n")
                f.write(script)

            # Simulate processing time
            await asyncio.sleep(1)

            logger.info(f"Stub video rendered: {artifact_path}")
            return artifact_path

        except Exception as e:
            logger.error(f"Stub video rendering failed: {e}")
            raise

    async def _render_video_runway(self, job_id: str, script: str, request_data: Dict) -> Path:
        """RunwayML video renderer"""
        try:
            # TODO: Integrate with RunwayML API
            logger.info(f"Would render video via RunwayML for job: {job_id}")

            # For now, fall back to stub
            return await self._render_video_stub(job_id, script, request_data)

        except Exception as e:
            logger.error(f"RunwayML video rendering failed: {e}")
            raise

    async def _render_video_synthesia(self, job_id: str, script: str, request_data: Dict) -> Path:
        """Synthesia video renderer"""
        try:
            # TODO: Integrate with Synthesia API
            logger.info(f"Would render video via Synthesia for job: {job_id}")

            # For now, fall back to stub
            return await self._render_video_stub(job_id, script, request_data)

        except Exception as e:
            logger.error(f"Synthesia video rendering failed: {e}")
            raise

    async def _render_video_heygen(self, job_id: str, script: str, request_data: Dict) -> Path:
        """HeyGen video renderer"""
        try:
            # TODO: Integrate with HeyGen API
            logger.info(f"Would render video via HeyGen for job: {job_id}")

            # For now, fall back to stub
            return await self._render_video_stub(job_id, script, request_data)

        except Exception as e:
            logger.error(f"HeyGen video rendering failed: {e}")
            raise

    async def get_video_job(self, job_id: str) -> Optional[Dict]:
        """Get video job by ID"""
        try:
            return await self.store.get_video_job(job_id)

        except Exception as e:
            logger.error(f"Failed to get video job {job_id}: {e}")
            return None

    async def list_video_jobs(self, status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """List video jobs"""
        try:
            return await self.store.list_video_jobs(status, limit)

        except Exception as e:
            logger.error(f"Failed to list video jobs: {e}")
            return []

    async def update_job_status(self, job_id: str, status: str, **kwargs) -> None:
        """Update video job status"""
        try:
            await self.store.update_video_job(job_id, status=status, **kwargs)

        except Exception as e:
            logger.error(f"Failed to update job status {job_id}: {e}")
            raise

    async def cancel_video_job(self, job_id: str) -> bool:
        """Cancel a video job"""
        try:
            await self.store.update_video_job(job_id, status="cancelled")

            # Publish cancellation event
            if self.event_bus:
                self.event_bus.publish_video_job_cancelled(job_id, {})

            logger.info(f"Video job cancelled: {job_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel video job {job_id}: {e}")
            return False

    async def get_job_performance_metrics(self, job_id: str) -> Dict:
        """Get performance metrics for a video job"""
        try:
            if not self.store:
                return {}

            metrics = await self.store.get_performance_metrics("video_job", job_id)
            return {"metrics": metrics}

        except Exception as e:
            logger.error(f"Failed to get job performance metrics {job_id}: {e}")
            return {}

    def is_healthy(self) -> bool:
        """Check if video manager is healthy"""
        return (
            self.store is not None and
            self.artifact_dir.exists() and
            self.video_renderer in self.supported_renderers
        )

    async def shutdown(self) -> None:
        """Shutdown video manager"""
        try:
            logger.info("Video manager shutdown complete")

        except Exception as e:
            logger.error(f"Error during video manager shutdown: {e}")
            raise