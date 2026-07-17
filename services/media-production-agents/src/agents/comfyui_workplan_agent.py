from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
import uuid

from agents.base import Agent
from babyai_shared.bus.protocol import Context, Message, MessageType
_log = logging.getLogger(__name__)

WORKFLOWS_DIR = Path(__file__).parent.parent / "config" / "comfyui_workflows"
DEFAULT_IMAGE_SERVICE_URL = "http://host.docker.internal:8110"


class ComfyUIWorkplanAgent(Agent):
    def __init__(
        self,
        agent_id: str = "comfyui-001",
        client: Any | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            role="comfyui_workplan",
            accepts={MessageType.IMAGE_REQUEST},
        )
        if client is not None:
            self._client = client
        else:
            from aesa.api.image_service import ComfyUIClient
            url = os.environ.get("IMAGE_SERVICE_URL", DEFAULT_IMAGE_SERVICE_URL)
            self._client = ComfyUIClient(base_url=url, timeout_seconds=600.0)
        self._workflows: Dict[str, Any] = self._load_workflows()

    def _load_workflows(self) -> Dict[str, Any]:
        workflows: Dict[str, Any] = {}
        if not WORKFLOWS_DIR.exists():
            return workflows
        for path in WORKFLOWS_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                workflows[path.stem] = data
            except Exception as exc:
                _log.warning("Failed to load workflow %s: %s", path.name, exc)
        return workflows

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type == MessageType.IMAGE_REQUEST:
            return self._handle_image_request(message, context)
        return []

    def _handle_image_request(self, message: Message, context: Context) -> List[Message]:
        payload = message.payload or {}
        intent = str(payload.get("intent") or "generate").strip().lower()
        project_id = str(payload.get("project_id") or context.context_id or "default")

        try:
            if intent == "analyze":
                result = self._client.analyze(
                    image_ref=str(payload.get("image_ref") or ""),
                    question=str(payload.get("question") or "Describe this image"),
                    project_id=project_id,
                )
                content: Dict[str, Any] = {"result": result, "intent": intent}
            elif intent == "edit":
                job = self._client.edit(
                    image_ref=str(payload.get("image_ref") or ""),
                    instruction=str(payload.get("instruction") or payload.get("prompt") or ""),
                    style_profile=str(payload.get("style_profile") or "default"),
                    project_id=project_id,
                    reference_image=payload.get("reference_image"),
                )
                content = {"file_path": job.file_path, "metadata": dict(job.metadata), "intent": intent}
            else:
                job = self._client.generate(
                    prompt=str(payload.get("prompt") or ""),
                    style_profile=str(payload.get("style_profile") or "default"),
                    task_type=str(payload.get("task_type") or "image_generate"),
                    project_id=project_id,
                    reference_image=payload.get("reference_image"),
                    reference_frames=payload.get("reference_frames"),
                    consistency_profile=payload.get("consistency_profile"),
                )
                content = {"file_path": job.file_path, "metadata": dict(job.metadata), "intent": intent}
        except Exception as exc:
            _log.warning("ComfyUIWorkplanAgent: %s failed: %s", intent, exc)
            content = {"error": f"{type(exc).__name__}: {exc}", "intent": intent}

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type=MessageType.IMAGE_COMPLETE,
            payload=content,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]
