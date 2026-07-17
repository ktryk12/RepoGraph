"""
Tool Runtime Module

Consolidated from services/tool-runtime/src/
Provides tool execution infrastructure and runtime management.
"""

import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


class ToolRuntime:
    """Tool execution runtime service"""

    def __init__(self, store, event_bus=None):
        self.store = store
        self.event_bus = event_bus
        self.active_executions: Dict[str, asyncio.Task] = {}
        self.max_concurrent = 10

    async def initialize(self) -> None:
        """Initialize tool runtime"""
        logger.info("Tool runtime initialized")

    async def execute_tool(self, tool_id: str, input_data: Dict,
                         execution_context: Optional[Dict] = None) -> Dict:
        """Execute a tool"""
        try:
            execution_id = f"exec_{uuid4().hex[:12]}"

            # Check concurrent limit
            if len(self.active_executions) >= self.max_concurrent:
                raise Exception("Maximum concurrent executions reached")

            # Create execution record
            await self.store.create_tool_execution(
                execution_id=execution_id,
                tool_id=tool_id,
                execution_context=execution_context or {},
                input_data=input_data
            )

            # Execute tool
            start_time = datetime.utcnow()
            result = await self._execute_tool_internal(tool_id, input_data, execution_context)
            duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Update execution record
            await self.store.update_tool_execution(
                execution_id=execution_id,
                execution_state="completed",
                output_data=result,
                duration_ms=duration_ms
            )

            # Publish execution event
            if self.event_bus:
                self.event_bus.publish_tool_executed(tool_id, {
                    "execution_id": execution_id,
                    "duration_ms": duration_ms,
                    "success": True
                })

            return {
                "execution_id": execution_id,
                "status": "completed",
                "result": result,
                "duration_ms": duration_ms
            }

        except Exception as e:
            logger.error(f"Tool execution failed for {tool_id}: {e}")
            if 'execution_id' in locals():
                await self.store.update_tool_execution(
                    execution_id=execution_id,
                    execution_state="failed",
                    error_data={"error": str(e)}
                )
            raise

    async def _execute_tool_internal(self, tool_id: str, input_data: Dict,
                                   execution_context: Optional[Dict]) -> Dict:
        """Internal tool execution logic"""
        # Simulate tool execution
        await asyncio.sleep(0.1)

        return {
            "tool_id": tool_id,
            "processed": True,
            "output": f"Tool {tool_id} executed successfully",
            "timestamp": datetime.utcnow().isoformat()
        }

    async def get_execution(self, execution_id: str) -> Optional[Dict]:
        """Get execution details"""
        # Would query from store
        return {"execution_id": execution_id, "status": "completed"}

    async def abort_execution(self, execution_id: str) -> bool:
        """Abort execution"""
        if execution_id in self.active_executions:
            self.active_executions[execution_id].cancel()
            return True
        return False

    async def get_performance_metrics(self, tool_id: str) -> Dict:
        """Get tool performance metrics"""
        if not self.store:
            return {}

        metrics = await self.store.get_performance_metrics("tool", tool_id)
        return {"metrics": metrics}

    def is_healthy(self) -> bool:
        return True

    async def shutdown(self) -> None:
        """Shutdown tool runtime"""
        for task in self.active_executions.values():
            task.cancel()
        self.active_executions.clear()
        logger.info("Tool runtime shutdown complete")