from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from babyai.memory.visual_memory import VisualMemory


@dataclass(frozen=True)
class ConsistencyProfile:
    dominant_colors: tuple[str, ...] = ()
    style_tags: tuple[str, ...] = ()
    character_descriptions: tuple[str, ...] = ()
    composition_notes: tuple[str, ...] = ()
    negative_prompt_additions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "dominant_colors": list(self.dominant_colors),
            "style_tags": list(self.style_tags),
            "character_descriptions": list(self.character_descriptions),
            "composition_notes": list(self.composition_notes),
            "negative_prompt_additions": list(self.negative_prompt_additions),
        }


class ConsistencyAgent:
    def __init__(
        self,
        visual_memory: VisualMemory,
        image_service_url: str,
        *,
        project_id: str | None = None,
        timeout_seconds: float = 600.0,
        request_fn: Callable[[str, str, Mapping[str, Any], float], Mapping[str, Any]] | None = None,
    ) -> None:
        self.visual_memory = visual_memory
        self.image_service_url = str(image_service_url or "").rstrip("/")
        if not self.image_service_url:
            raise ValueError("image_service_url must be non-empty")
        self.project_id = str(project_id or visual_memory.project_id).strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")
        self.timeout_seconds = max(1.0, float(timeout_seconds or 600.0))
        self._request_fn = request_fn

    def analyze_sequence(self, sequence_id: str) -> ConsistencyProfile:
        entries = self.visual_memory.get_sequence(sequence_id)
        if not entries:
            return ConsistencyProfile()

        colors: list[str] = []
        styles: list[str] = []
        characters: list[str] = []
        compositions: list[str] = []
        negatives: list[str] = []

        question = (
            "Describe dominant colors, style tags, recurring characters, composition notes, "
            "and negative prompt hints for visual consistency."
        )
        for entry in entries:
            analysis = self._analyze_image(entry.file_path, question)
            colors.extend(_to_text_list(analysis.get("dominant_colors")))
            styles.extend(_to_text_list(analysis.get("style_tags")))
            characters.extend(_to_text_list(analysis.get("character_descriptions")))
            compositions.extend(_to_text_list(analysis.get("composition_notes")))
            negatives.extend(_to_text_list(analysis.get("negative_prompt_additions")))

            # Fallback extraction from summary text when structured fields are absent.
            summary = str(analysis.get("summary") or "").strip()
            if summary and not styles:
                styles.extend(_extract_keywords(summary, ["noir", "cinematic", "comic", "illustration", "photo"]))

        return ConsistencyProfile(
            dominant_colors=tuple(_dedupe(colors)),
            style_tags=tuple(_dedupe(styles)),
            character_descriptions=tuple(_dedupe(characters)),
            composition_notes=tuple(_dedupe(compositions)),
            negative_prompt_additions=tuple(_dedupe(negatives)),
        )

    def enrich_prompt(self, base_prompt: str, consistency_profile: ConsistencyProfile) -> str:
        prompt = str(base_prompt or "").strip()
        if not prompt:
            raise ValueError("base_prompt must be non-empty")

        parts = [prompt]
        if consistency_profile.style_tags:
            parts.append(f"style tags: {', '.join(consistency_profile.style_tags)}")
        if consistency_profile.dominant_colors:
            parts.append(f"dominant colors: {', '.join(consistency_profile.dominant_colors)}")
        if consistency_profile.character_descriptions:
            parts.append("keep character consistency: " + "; ".join(consistency_profile.character_descriptions))
        if consistency_profile.composition_notes:
            parts.append("composition: " + "; ".join(consistency_profile.composition_notes))
        if consistency_profile.negative_prompt_additions:
            parts.append("avoid: " + ", ".join(consistency_profile.negative_prompt_additions))
        return ", ".join(parts)

    def _analyze_image(self, image_ref: str, question: str) -> Mapping[str, Any]:
        payload = {
            "image_ref": str(image_ref),
            "question": str(question),
            "project_id": self.project_id,
        }
        return self._request_json("POST", "/v1/images/analyze", payload)

    def _request_json(self, method: str, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._request_fn is not None:
            result = self._request_fn(str(method).upper(), str(path), dict(payload), float(self.timeout_seconds))
            if not isinstance(result, Mapping):
                raise RuntimeError("image service response must be an object")
            return dict(result)

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


def _to_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = str(value).strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def _extract_keywords(text: str, candidates: list[str]) -> list[str]:
    lowered = str(text).lower()
    out: list[str] = []
    for candidate in candidates:
        token = str(candidate).strip().lower()
        if token and token in lowered:
            out.append(token)
    return out
