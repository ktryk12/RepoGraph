"""
UI Manager Module

Consolidated from services/ui/
Provides user interface and dashboard functionality with session management.
"""

import asyncio
import logging
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


class UIManager:
    """
    User Interface management service

    Consolidated functionality from ui service:
    - UI session management
    - Dashboard data aggregation
    - User interaction handling
    - Real-time updates and WebSocket management
    - Request routing and response handling
    """

    def __init__(self, store, event_bus=None):
        self.store = store
        self.event_bus = event_bus

        # Active sessions
        self.active_sessions: Dict[str, Dict] = {}

        # UI configuration
        self.dashboard_refresh_interval = 30  # seconds
        self.max_session_duration = 3600  # 1 hour
        self.max_concurrent_sessions = 100

    async def initialize(self) -> None:
        """Initialize UI manager"""
        try:
            logger.info("UI manager initialized")

            # Start background tasks
            asyncio.create_task(self._session_cleanup_task())
            asyncio.create_task(self._dashboard_update_task())

        except Exception as e:
            logger.error(f"Failed to initialize UI manager: {e}")
            raise

    async def create_session(self, user_id: str, initial_data: Dict,
                           metadata: Optional[Dict] = None) -> str:
        """Create a new UI session"""
        try:
            session_id = f"ui_session_{uuid4().hex[:12]}"

            # Check session limits
            if len(self.active_sessions) >= self.max_concurrent_sessions:
                await self._cleanup_oldest_session()

            # Prepare session data
            session_data = {
                "user_id": user_id,
                "created_at": datetime.utcnow().isoformat(),
                "last_activity": datetime.utcnow().isoformat(),
                "page_views": 0,
                "actions_performed": 0,
                "current_page": "/",
                "user_agent": initial_data.get("user_agent", ""),
                "initial_data": initial_data
            }

            # Store session in database
            await self.store.create_ui_session(
                session_id=session_id,
                user_id=user_id,
                session_data=session_data,
                metadata=metadata
            )

            # Add to active sessions
            self.active_sessions[session_id] = session_data

            # Publish session created event
            if self.event_bus:
                self.event_bus.publish_ui_session_created(session_id, {
                    "user_id": user_id,
                    "initial_data": initial_data
                })

            logger.info(f"UI session created: {session_id} (user: {user_id})")
            return session_id

        except Exception as e:
            logger.error(f"Failed to create UI session: {e}")
            raise

    async def get_session(self, session_id: str) -> Optional[Dict]:
        """Get UI session by ID"""
        try:
            # Check active sessions first
            if session_id in self.active_sessions:
                return {
                    "session_id": session_id,
                    **self.active_sessions[session_id]
                }

            # Fall back to database
            session = await self.store.get_ui_session(session_id)
            if session:
                # Add back to active sessions
                self.active_sessions[session_id] = session["session_data"]

            return session

        except Exception as e:
            logger.error(f"Failed to get UI session {session_id}: {e}")
            return None

    async def update_session(self, session_id: str, session_data: Dict) -> None:
        """Update UI session data"""
        try:
            # Update last activity
            session_data["last_activity"] = datetime.utcnow().isoformat()

            # Update active sessions
            if session_id in self.active_sessions:
                self.active_sessions[session_id].update(session_data)

            # Update database
            await self.store.update_ui_session(session_id, session_data)

            logger.debug(f"UI session updated: {session_id}")

        except Exception as e:
            logger.error(f"Failed to update UI session {session_id}: {e}")
            raise

    async def handle_request(self, session_id: str, request_data: Dict) -> Dict:
        """Handle UI request"""
        try:
            # Get session
            session = await self.get_session(session_id)
            if not session:
                return {"error": "Session not found", "status": "error"}

            # Extract request details
            action = request_data.get("action", "unknown")
            path = request_data.get("path", "/")
            method = request_data.get("method", "GET")
            params = request_data.get("params", {})

            # Update session activity
            session_updates = {
                "last_activity": datetime.utcnow().isoformat(),
                "actions_performed": session["session_data"]["actions_performed"] + 1
            }

            if path != session["session_data"]["current_page"]:
                session_updates["page_views"] = session["session_data"]["page_views"] + 1
                session_updates["current_page"] = path

            await self.update_session(session_id, session_updates)

            # Route request based on action
            response = await self._route_request(action, path, method, params, session)

            # Publish UI action event
            if self.event_bus:
                self.event_bus.publish_ui_action_performed(session_id, {
                    "action": action,
                    "path": path,
                    "method": method
                })

            logger.debug(f"UI request handled: {session_id} - {action}")

            return response

        except Exception as e:
            logger.error(f"Failed to handle UI request for session {session_id}: {e}")
            return {"error": str(e), "status": "error"}

    async def _route_request(self, action: str, path: str, method: str,
                           params: Dict, session: Dict) -> Dict:
        """Route UI request to appropriate handler"""
        try:
            # Dashboard requests
            if action == "get_dashboard":
                return await self._handle_dashboard_request(params, session)

            # Video management requests
            elif action == "get_video_jobs":
                return await self._handle_video_jobs_request(params, session)

            elif action == "create_video_job":
                return await self._handle_create_video_job_request(params, session)

            # Voice operations requests
            elif action == "get_voice_operations":
                return await self._handle_voice_operations_request(params, session)

            # System status requests
            elif action == "get_system_status":
                return await self._handle_system_status_request(params, session)

            # Default page request
            elif action == "get_page":
                return await self._handle_page_request(path, params, session)

            else:
                return {
                    "status": "error",
                    "error": f"Unknown action: {action}",
                    "available_actions": [
                        "get_dashboard", "get_video_jobs", "create_video_job",
                        "get_voice_operations", "get_system_status", "get_page"
                    ]
                }

        except Exception as e:
            logger.error(f"Request routing failed: {e}")
            return {"error": str(e), "status": "error"}

    async def _handle_dashboard_request(self, params: Dict, session: Dict) -> Dict:
        """Handle dashboard data request"""
        try:
            # Mock dashboard data - would aggregate from various services
            dashboard_data = {
                "status": "success",
                "data": {
                    "overview": {
                        "total_video_jobs": 25,
                        "completed_video_jobs": 20,
                        "active_voice_operations": 3,
                        "active_ui_sessions": len(self.active_sessions)
                    },
                    "recent_activity": [
                        {"type": "video_job", "id": "video_job_123", "status": "completed", "time": "2 min ago"},
                        {"type": "voice_stt", "id": "stt_456", "status": "completed", "time": "5 min ago"},
                        {"type": "ui_session", "id": "ui_session_789", "status": "created", "time": "8 min ago"}
                    ],
                    "system_health": {
                        "video_manager": "healthy",
                        "voice_manager": "healthy",
                        "ui_manager": "healthy",
                        "database": "healthy"
                    },
                    "performance_metrics": {
                        "avg_video_generation_time": "45s",
                        "avg_voice_processing_time": "2.1s",
                        "ui_response_time": "120ms",
                        "system_uptime": "5d 12h"
                    }
                },
                "timestamp": datetime.utcnow().isoformat()
            }

            return dashboard_data

        except Exception as e:
            logger.error(f"Dashboard request failed: {e}")
            return {"error": str(e), "status": "error"}

    async def _handle_video_jobs_request(self, params: Dict, session: Dict) -> Dict:
        """Handle video jobs list request"""
        try:
            status_filter = params.get("status")
            limit = min(params.get("limit", 20), 100)  # Cap at 100

            # Mock video jobs data - would query from media platform
            video_jobs = [
                {
                    "job_id": f"video_job_{i}",
                    "status": "completed" if i % 3 == 0 else "processing",
                    "created_at": f"2024-04-22T{10 + i % 10}:00:00Z",
                    "renderer": "stub",
                    "progress": 100 if i % 3 == 0 else 65
                }
                for i in range(limit)
            ]

            if status_filter:
                video_jobs = [job for job in video_jobs if job["status"] == status_filter]

            return {
                "status": "success",
                "data": {
                    "jobs": video_jobs,
                    "total": len(video_jobs),
                    "filter": status_filter
                }
            }

        except Exception as e:
            logger.error(f"Video jobs request failed: {e}")
            return {"error": str(e), "status": "error"}

    async def _handle_create_video_job_request(self, params: Dict, session: Dict) -> Dict:
        """Handle create video job request"""
        try:
            prompt = params.get("prompt", "")
            if not prompt:
                return {"error": "Prompt is required", "status": "error"}

            # Mock job creation - would interface with video manager
            job_id = f"video_job_{uuid4().hex[:12]}"

            return {
                "status": "success",
                "data": {
                    "job_id": job_id,
                    "status": "created",
                    "prompt": prompt,
                    "created_at": datetime.utcnow().isoformat()
                }
            }

        except Exception as e:
            logger.error(f"Create video job request failed: {e}")
            return {"error": str(e), "status": "error"}

    async def _handle_voice_operations_request(self, params: Dict, session: Dict) -> Dict:
        """Handle voice operations list request"""
        try:
            op_type = params.get("type")  # "stt" or "tts"
            limit = min(params.get("limit", 20), 100)

            # Mock voice operations - would query from voice manager
            operations = [
                {
                    "operation_id": f"{op_type or 'stt'}_{i}",
                    "type": op_type or ("stt" if i % 2 == 0 else "tts"),
                    "status": "completed",
                    "created_at": f"2024-04-22T{10 + i % 10}:00:00Z",
                    "duration_ms": 1500 + i * 100
                }
                for i in range(limit)
            ]

            if op_type:
                operations = [op for op in operations if op["type"] == op_type]

            return {
                "status": "success",
                "data": {
                    "operations": operations,
                    "total": len(operations),
                    "filter": op_type
                }
            }

        except Exception as e:
            logger.error(f"Voice operations request failed: {e}")
            return {"error": str(e), "status": "error"}

    async def _handle_system_status_request(self, params: Dict, session: Dict) -> Dict:
        """Handle system status request"""
        try:
            return {
                "status": "success",
                "data": {
                    "system_status": "healthy",
                    "services": {
                        "media_platform": "running",
                        "video_manager": "healthy",
                        "voice_manager": "healthy",
                        "ui_manager": "healthy"
                    },
                    "database": {
                        "status": "connected",
                        "pool_size": 10,
                        "active_connections": 3
                    },
                    "event_bus": {
                        "status": "connected",
                        "pending_messages": 0
                    },
                    "timestamp": datetime.utcnow().isoformat()
                }
            }

        except Exception as e:
            logger.error(f"System status request failed: {e}")
            return {"error": str(e), "status": "error"}

    async def _handle_page_request(self, path: str, params: Dict, session: Dict) -> Dict:
        """Handle page content request"""
        try:
            # Mock page content based on path
            page_content = {
                "/": {"title": "Media Platform Dashboard", "component": "dashboard"},
                "/video": {"title": "Video Jobs", "component": "video_jobs"},
                "/voice": {"title": "Voice Operations", "component": "voice_operations"},
                "/status": {"title": "System Status", "component": "system_status"}
            }

            content = page_content.get(path, {
                "title": "Page Not Found",
                "component": "error",
                "error": f"Path not found: {path}"
            })

            return {
                "status": "success",
                "data": {
                    "path": path,
                    "content": content,
                    "session_id": session["session_id"]
                }
            }

        except Exception as e:
            logger.error(f"Page request failed: {e}")
            return {"error": str(e), "status": "error"}

    async def _cleanup_oldest_session(self) -> None:
        """Clean up the oldest active session"""
        if not self.active_sessions:
            return

        # Find oldest session
        oldest_session_id = min(
            self.active_sessions.keys(),
            key=lambda sid: self.active_sessions[sid].get("created_at", "")
        )

        # Remove from active sessions
        del self.active_sessions[oldest_session_id]
        logger.info(f"Cleaned up oldest session: {oldest_session_id}")

    async def _session_cleanup_task(self) -> None:
        """Background task to clean up expired sessions"""
        while True:
            try:
                current_time = datetime.utcnow()
                expired_sessions = []

                for session_id, session_data in self.active_sessions.items():
                    last_activity = datetime.fromisoformat(
                        session_data.get("last_activity", "").replace("Z", "+00:00")
                    )
                    if (current_time - last_activity).total_seconds() > self.max_session_duration:
                        expired_sessions.append(session_id)

                # Clean up expired sessions
                for session_id in expired_sessions:
                    del self.active_sessions[session_id]
                    logger.info(f"Expired session cleaned up: {session_id}")

                # Sleep for cleanup interval
                await asyncio.sleep(300)  # 5 minutes

            except Exception as e:
                logger.error(f"Session cleanup task error: {e}")
                await asyncio.sleep(60)

    async def _dashboard_update_task(self) -> None:
        """Background task to push dashboard updates"""
        while True:
            try:
                # This would push real-time updates to connected clients
                # For now, just log the activity
                logger.debug(f"Dashboard update - Active sessions: {len(self.active_sessions)}")

                await asyncio.sleep(self.dashboard_refresh_interval)

            except Exception as e:
                logger.error(f"Dashboard update task error: {e}")
                await asyncio.sleep(30)

    def is_healthy(self) -> bool:
        """Check if UI manager is healthy"""
        return self.store is not None

    async def shutdown(self) -> None:
        """Shutdown UI manager"""
        try:
            # Clear active sessions
            self.active_sessions.clear()
            logger.info("UI manager shutdown complete")

        except Exception as e:
            logger.error(f"Error during UI manager shutdown: {e}")
            raise