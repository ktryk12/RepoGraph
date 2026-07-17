from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from babyai.memory.visual_memory import VisualMemory
from babyai.tools.consistency_agent import ConsistencyAgent, ConsistencyProfile
from babyai.tools.content_policy import ContentPolicy
from babyai.tools.registry import ToolDefinition, ToolRegistry


@dataclass(frozen=True)
class VisualResult:
    visual_id: str
    file_path: str
    style_profile: str
    prompt: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AnalysisResult:
    summary: str
    details: dict[str, Any]


class VisualTool:
    def __init__(
        self,
        image_service_url: str,
        content_policy: ContentPolicy,
        visual_memory: VisualMemory,
        *,
        project_id: str | None = None,
        project_policy: Mapping[str, Any] | None = None,
        timeout_seconds: float = 600.0,
        request_fn: Callable[[str, str, Mapping[str, Any], float], Mapping[str, Any]] | None = None,
        consistency_agent: ConsistencyAgent | None = None,
    ) -> None:
        self.image_service_url = str(image_service_url or "").rstrip("/")
        if not self.image_service_url:
            raise ValueError("image_service_url must be non-empty")
        self.content_policy = content_policy
        self.visual_memory = visual_memory
        self.project_id = str(project_id or visual_memory.project_id).strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")
        self.project_policy = dict(project_policy or {})
        self.timeout_seconds = max(1.0, float(timeout_seconds or 600.0))
        self._request_fn = request_fn
        self.consistency_agent = consistency_agent

    def tool_definition(self, style_profile: str) -> ToolDefinition:
        decision = self.content_policy.check(
            {
                "style_profile": style_profile,
                "prompt": "policy_probe",
                "project_policy": self.project_policy,
            }
        )
        return ToolDefinition(
            id=f"visual_tool.{decision.profile}",
            name="Visual Tool",
            type="visual",
            capability="visual_generation",
            risk_rating=str(decision.risk_rating),
            required_permissions=list(decision.required_permissions),
            cost_model={"unit": "image_or_video"},
            audit_hooks=["tool_call"],
        )

    def register_in_registry(self, registry: ToolRegistry, style_profile: str) -> str:
        if not isinstance(registry, ToolRegistry):
            raise ValueError("registry must be ToolRegistry")
        return registry.register(self.tool_definition(style_profile))

    def generate_image(
        self,
        prompt: str,
        style_profile: str,
        sequence_id: str | None = None,
        reference_image: str | None = None,
    ) -> VisualResult:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise ValueError("prompt must be non-empty")

        effective_prompt = clean_prompt
        consistency_profile = ConsistencyProfile()
        if sequence_id and self.consistency_agent is not None:
            consistency_profile = self.consistency_agent.analyze_sequence(sequence_id)
            effective_prompt = self.consistency_agent.enrich_prompt(clean_prompt, consistency_profile)
            if not reference_image:
                existing = self.visual_memory.get_sequence(sequence_id)
                if existing:
                    reference_image = existing[-1].file_path

        policy_decision = self.content_policy.check(
            {
                "style_profile": style_profile,
                "prompt": effective_prompt,
                "project_id": self.project_id,
                "project_policy": self.project_policy,
            }
        )

        payload = {
            "project_id": self.project_id,
            "prompt": effective_prompt,
            "style_profile": policy_decision.profile,
            "sequence_id": sequence_id,
            "reference_image": reference_image,
            "consistency_profile": consistency_profile.to_dict(),
        }
        response = self._request_json("POST", "/v1/images/generate", payload)
        return self._to_visual_result(
            response=response,
            fallback_prompt=effective_prompt,
            fallback_style=policy_decision.profile,
            sequence_id=sequence_id,
            tags={"domain": "visual", "required_permissions": list(policy_decision.required_permissions)},
        )

    def generate_video(
        self,
        prompt: str,
        style_profile: str,
        reference_frames: list[str] | None = None,
    ) -> VisualResult:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise ValueError("prompt must be non-empty")
        policy_decision = self.content_policy.check(
            {
                "style_profile": style_profile,
                "prompt": clean_prompt,
                "project_id": self.project_id,
                "project_policy": self.project_policy,
            }
        )
        payload = {
            "project_id": self.project_id,
            "prompt": clean_prompt,
            "style_profile": policy_decision.profile,
            "reference_frames": list(reference_frames or []),
            "task_type": "video_generate",
        }
        response = self._request_json("POST", "/v1/images/generate", payload)
        return self._to_visual_result(
            response=response,
            fallback_prompt=clean_prompt,
            fallback_style=policy_decision.profile,
            sequence_id=None,
            tags={"domain": "visual", "modality": "video"},
        )

    def analyze_image(self, image_ref: str, question: str) -> AnalysisResult:
        clean_ref = str(image_ref or "").strip()
        clean_question = str(question or "").strip()
        if not clean_ref:
            raise ValueError("image_ref must be non-empty")
        if not clean_question:
            raise ValueError("question must be non-empty")
        self.content_policy.check(
            {
                "style_profile": "safe",
                "prompt": clean_question,
                "project_id": self.project_id,
                "project_policy": self.project_policy,
            }
        )
        payload = {
            "project_id": self.project_id,
            "image_ref": clean_ref,
            "question": clean_question,
        }
        response = self._request_json("POST", "/v1/images/analyze", payload)
        summary = str(response.get("summary") or response.get("analysis") or "").strip()
        details = dict(response)
        return AnalysisResult(summary=summary, details=details)

    def edit_image(
        self,
        image_ref: str,
        instruction: str,
        style_profile: str,
        sequence_id: str | None = None,
    ) -> VisualResult:
        clean_ref = str(image_ref or "").strip()
        clean_instruction = str(instruction or "").strip()
        if not clean_ref:
            raise ValueError("image_ref must be non-empty")
        if not clean_instruction:
            raise ValueError("instruction must be non-empty")

        policy_decision = self.content_policy.check(
            {
                "style_profile": style_profile,
                "prompt": clean_instruction,
                "project_id": self.project_id,
                "project_policy": self.project_policy,
            }
        )
        payload = {
            "project_id": self.project_id,
            "image_ref": clean_ref,
            "instruction": clean_instruction,
            "style_profile": policy_decision.profile,
            "sequence_id": sequence_id,
        }
        response = self._request_json("POST", "/v1/images/edit", payload)
        return self._to_visual_result(
            response=response,
            fallback_prompt=clean_instruction,
            fallback_style=policy_decision.profile,
            sequence_id=sequence_id,
            tags={"domain": "visual", "mode": "edit"},
        )

    def _to_visual_result(
        self,
        *,
        response: Mapping[str, Any],
        fallback_prompt: str,
        fallback_style: str,
        sequence_id: str | None,
        tags: Mapping[str, Any],
    ) -> VisualResult:
        visual_id = str(response.get("visual_id") or "").strip()
        file_path = str(response.get("file_path") or "").strip()
        style_profile = str(response.get("style_profile") or fallback_style).strip() or fallback_style
        prompt = str(response.get("prompt") or fallback_prompt).strip() or fallback_prompt
        metadata = dict(response.get("metadata") or {})

        if not file_path:
            raise RuntimeError("image_service response missing file_path")
        if not visual_id:
            visual_id = self.visual_memory.save(
                {
                    "file_path": file_path,
                    "prompt": prompt,
                    "style_profile": style_profile,
                    "sequence_id": sequence_id,
                    "consistency_metadata": metadata.get("consistency_profile") or {},
                    "hardware_used": metadata.get("hardware_used"),
                    "duration_seconds": metadata.get("duration_seconds"),
                },
                tags=tags,
                sequence_id=sequence_id,
            )
        return VisualResult(
            visual_id=visual_id,
            file_path=file_path,
            style_profile=style_profile,
            prompt=prompt,
            metadata=metadata,
        )

    def _request_json(self, method: str, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._request_fn is not None:
            out = self._request_fn(str(method).upper(), str(path), dict(payload), float(self.timeout_seconds))
            if not isinstance(out, Mapping):
                raise RuntimeError("image service response must be an object")
            return dict(out)

        body = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        req = Request(
            url=f"{self.image_service_url}{str(path)}",
            data=body,
            method=str(method).upper(),
            headers={"content-type": "application/json", "accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                decoded = json.loads(raw) if raw.strip() else {}
                if not isinstance(decoded, Mapping):
                    raise RuntimeError("image service response must be an object")
                return dict(decoded)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"image_service_http_error status={int(exc.code)} body={detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"image_service_unreachable: {exc}") from exc
