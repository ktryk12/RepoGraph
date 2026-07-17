from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, TypeAlias
import json
import logging
logger = logging.getLogger(__name__)
import os
import time

from aesa.core.timeout_budget import TimeoutBudget
from aesa.bootstrap.model_runtime_wiring import ExpertServingServiceRuntime, build_expert_serving_service_runtime
from aesa.infrastructure.expert_serving_router import ModelNotAvailableError, resolve_model_url
from aesa.infrastructure.model_runner_http import (
    LlamaCppRunnerGateway,
    ModelRunnerHttpError,
    ModelRunnerTimeoutError,
)

try:
    from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
    from fastapi.responses import JSONResponse
except Exception:  # pragma: no cover - optional dependency
    FastAPI = None  # type: ignore

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: Any = None) -> None:
            super().__init__(str(detail))
            self.status_code = status_code
            self.detail = detail

    class FastAPIRequest:
        headers: Mapping[str, Any]
        url: Any

    class JSONResponse:
        def __init__(self, status_code: int, content: Any) -> None:
            self.status_code = status_code
            self.content = content


def create_app(*, runtime: ExpertServingServiceRuntime | None = None, env: Mapping[str, str] | None = None) -> Any:
    if FastAPI is None:
        raise RuntimeError("FastAPI is required for expert_serving_service. Install: pip install fastapi uvicorn")

    source_env = env if env is not None else os.environ
    _verify_models_paths(source_env)
    service_runtime = runtime or build_expert_serving_service_runtime(env=source_env)

    app = FastAPI(title="AESA Expert Serving Service", version="0.1.0")
    app.state.runtime = service_runtime
    app.state.api_key = _expert_serving_api_key(source_env)
    app.state.source_env = source_env
    app.state.runner_gateway = LlamaCppRunnerGateway(
        base_url=_model_runner_base_url(source_env),
        model_ref=_model_runner_ref(source_env),
        runner_ref=_model_runner_name(source_env),
        timeout_seconds=_model_runner_timeout_seconds(source_env),
    )

    @app.middleware("http")
    async def require_api_key(http_request: FastAPIRequest, call_next):  # type: ignore[no-untyped-def]
        required_key = str(getattr(app.state, "api_key", "") or "").strip()
        if not required_key:
            return await call_next(http_request)
        path = str(getattr(http_request.url, "path", "") or "")
        if path in {"/health", "/docs", "/openapi.json", "/redoc"}:
            return await call_next(http_request)
        provided = str(http_request.headers.get("x-api-key") or "").strip()
        if provided != required_key:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "reason": "invalid_api_key"},
            )
        return await call_next(http_request)

    @app.get("/health")
    def health() -> Dict[str, Any]:
        model_runtime = app.state.runtime.model_runtime
        list_models = getattr(model_runtime, "list_models", None)
        models = list_models() if callable(list_models) else []
        runner_health = app.state.runner_gateway.health()
        return {
            "ok": True,
            "service": "expert-serving",
            "models": models if isinstance(models, list) else [],
            "runner": runner_health,
        }

    @app.post("/v1/generate")
    def generate(request: Dict[str, Any], http_request: FastAPIRequest) -> Dict[str, Any]:
        prompt = str(request.get("prompt") or "").strip()
        decision_id = str(request.get("decision_id") or "").strip()
        context_id = str(request.get("context_id") or "").strip() or "dev"
        purpose = str(request.get("purpose") or "").strip() or "default"
        constraints = request.get("constraints")
        constraints_obj = dict(constraints) if isinstance(constraints, dict) else {}
        seed = _optional_int(request.get("seed"))
        max_tokens = _optional_int(request.get("max_tokens"))
        temperature = _optional_float(request.get("temperature"))
        requested_profile = _request_model_profile(request)
        logger.info(
            "debug_expert_serving_request decision_id=%s context_id=%s purpose=%s model_profile=%s prompt_len=%d constraints=%s seed=%s max_tokens=%s temperature=%s",
            decision_id,
            context_id,
            purpose,
            requested_profile or "general",
            len(prompt),
            _clip_text(json.dumps(constraints_obj, ensure_ascii=True, sort_keys=True), max_chars=400),
            str(seed) if seed is not None else "",
            str(max_tokens) if max_tokens is not None else "",
            str(temperature) if temperature is not None else "",
        )
        logger.info(
            "debug_prompt_sent decision_id=%s context_id=%s prompt_text=%s",
            decision_id,
            context_id,
            _clip_text(prompt, max_chars=1400),
        )
        if not prompt:
            raise HTTPException(status_code=400, detail={"error": "prompt_required"})
        if not decision_id:
            raise HTTPException(status_code=400, detail={"error": "decision_id_required"})

        trace = _trace_context_from_headers(http_request)
        started_at = time.perf_counter()
        try:
            gateway = _gateway_for_profile(
                requested_profile=requested_profile,
                default_gateway=app.state.runner_gateway,
                source_env=app.state.source_env,
            )
            logger.info(
                "debug_expert_serving_gateway_call decision_id=%s context_id=%s purpose=%s model_profile=%s seed=%s",
                decision_id,
                context_id,
                purpose,
                requested_profile or "general",
                str(seed) if seed is not None else "",
            )
            response = gateway.generate(
                decision_id=decision_id,
                context_id=context_id,
                purpose=purpose,
                prompt=prompt,
                constraints=constraints_obj,
                seed=seed,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text_value = str(response.get("text") or "")
            logger.info(
                "debug_expert_serving_response decision_id=%s context_id=%s text_len=%d text=%s model_ref=%s runner_ref=%s trace=%s",
                decision_id,
                context_id,
                len(text_value.strip()),
                _clip_text(text_value.strip(), max_chars=1000),
                str(response.get("model_ref") or ""),
                str(response.get("runner_ref") or ""),
                _clip_text(json.dumps(dict(response.get("trace") or {}), ensure_ascii=True, sort_keys=True), max_chars=500),
            )
            _log_service_event(
                event_type="expert_serving.generate",
                decision_id=decision_id,
                context_id=context_id,
                purpose=purpose,
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                duration_ms=_elapsed_ms(started_at),
                status="ok",
                model_ref=response.get("model_ref"),
                runner_ref=response.get("runner_ref"),
                tokens_used=response.get("tokens_used"),
                model_profile=requested_profile or "general",
            )
            return response
        except ModelNotAvailableError as exc:
            _log_service_event(
                event_type="expert_serving.generate",
                decision_id=decision_id,
                context_id=context_id,
                purpose=purpose,
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                duration_ms=_elapsed_ms(started_at),
                status="model_profile_unavailable",
                error=str(exc),
                model_profile=requested_profile or "general",
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "model_profile_unavailable",
                    "message": str(exc),
                    "model_profile": requested_profile or "general",
                },
            )
        except ModelRunnerTimeoutError as exc:
            _log_service_event(
                event_type="expert_serving.generate",
                decision_id=decision_id,
                context_id=context_id,
                purpose=purpose,
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                duration_ms=_elapsed_ms(started_at),
                status="runner_timeout",
                error=str(exc),
                timeout_type="inner_http",
                timeout_seconds=_model_runner_timeout_seconds(source_env),
                model_profile=requested_profile or "general",
            )
            raise HTTPException(
                status_code=504,
                detail={
                    "error": "runner_timeout",
                    "message": str(exc),
                    "decision_id": decision_id,
                    "context_id": context_id,
                    "timeout_type": "inner_http",
                    "timeout_seconds": _model_runner_timeout_seconds(source_env),
                    "model_profile": requested_profile or "general",
                },
            )
        except ModelRunnerHttpError as exc:
            logger.warning(
                "debug_expert_serving_runner_error decision_id=%s context_id=%s model_profile=%s error=%s",
                decision_id,
                context_id,
                requested_profile or "general",
                _clip_text(str(exc), max_chars=500),
            )
            _log_service_event(
                event_type="expert_serving.generate",
                decision_id=decision_id,
                context_id=context_id,
                purpose=purpose,
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                duration_ms=_elapsed_ms(started_at),
                status="runner_failed",
                error=str(exc),
                model_profile=requested_profile or "general",
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "runner_failed",
                    "message": str(exc),
                    "decision_id": decision_id,
                    "context_id": context_id,
                    "model_profile": requested_profile or "general",
                },
            )
        except Exception as exc:
            _log_service_event(
                event_type="expert_serving.generate",
                decision_id=decision_id,
                context_id=context_id,
                purpose=purpose,
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                duration_ms=_elapsed_ms(started_at),
                status="error",
                error=str(exc),
                model_profile=requested_profile or "general",
            )
            raise HTTPException(status_code=500, detail={"error": "generate_failed", "message": str(exc)})

    @app.post("/v1/models/{model_id}/predict")
    def predict(model_id: str, request: Dict[str, Any], http_request: FastAPIRequest) -> Dict[str, Any]:
        features = request.get("features")
        if not isinstance(features, dict):
            raise HTTPException(status_code=400, detail={"error": "features_required"})

        trace = _trace_context_from_headers(http_request)
        started_at = time.perf_counter()
        try:
            prediction = app.state.runtime.model_runtime.predict(
                model_id=str(model_id),
                features=dict(features),
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
            )
            if not isinstance(prediction, dict):
                raise ValueError("prediction payload must be an object")
            _log_service_event(
                event_type="expert_serving.predict",
                model_id=str(model_id),
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                duration_ms=_elapsed_ms(started_at),
                status="ok",
            )
            return {"model_id": str(model_id), "prediction": prediction}
        except HTTPException:
            raise
        except ValueError as exc:
            _log_service_event(
                event_type="expert_serving.predict",
                model_id=str(model_id),
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                duration_ms=_elapsed_ms(started_at),
                status="invalid_request",
                error=str(exc),
            )
            raise HTTPException(status_code=400, detail={"error": "invalid_request", "msg": str(exc)})
        except Exception as exc:
            _log_service_event(
                event_type="expert_serving.predict",
                model_id=str(model_id),
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                duration_ms=_elapsed_ms(started_at),
                status="error",
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail={"error": "prediction_failed", "msg": str(exc)})

    return app


def _expert_serving_api_key(env: Mapping[str, str]) -> str:
    return str(env.get("EXPERT_SERVING_API_KEY", "") or "").strip()


def _model_runner_base_url(env: Mapping[str, str]) -> str:
    # MODEL_MANAGER_BASE_URL takes precedence; MODEL_RUNNER_BASE_URL kept for backwards compat.
    return (
        str(env.get("MODEL_MANAGER_BASE_URL", "") or "").strip()
        or str(env.get("MODEL_RUNNER_BASE_URL", "") or "").strip()
        or "http://model-manager:8112"
    )


def _model_runner_ref(env: Mapping[str, str]) -> str:
    return str(env.get("MODEL_RUNNER_MODEL_REF", "") or "").strip() or "mamba-gpt-7b-Q2_K.gguf"


def _model_runner_name(env: Mapping[str, str]) -> str:
    return str(env.get("MODEL_RUNNER_NAME", "") or "").strip() or "llama.cpp"


def _model_runner_timeout_seconds(env: Mapping[str, str]) -> float:
    return float(TimeoutBudget.from_env(env).inner_seconds)


def _trace_context_from_headers(http_request: FastAPIRequest) -> Dict[str, str]:
    headers = getattr(http_request, "headers", {}) if http_request is not None else {}
    return {
        "run_id": str(headers.get("x-run-id") or "").strip(),
        "case_id": str(headers.get("x-case-id") or "").strip(),
        "trace_id": str(headers.get("x-trace-id") or "").strip(),
    }


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - float(started_at)) * 1000.0, 3)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _request_model_profile(request: Mapping[str, Any]) -> str | None:
    direct = str(request.get("model_profile") or "").strip().lower()
    if direct:
        return direct
    effective_policy = request.get("effective_policy")
    if isinstance(effective_policy, Mapping):
        nested = str(effective_policy.get("model_profile") or "").strip().lower()
        if nested:
            return nested
    return None


def _gateway_for_profile(
    *,
    requested_profile: str | None,
    default_gateway: LlamaCppRunnerGateway,
    source_env: Mapping[str, str],
) -> LlamaCppRunnerGateway:
    profile = str(requested_profile or "").strip().lower() or "general"
    base_url = resolve_model_url(profile, env=source_env)
    if profile == "general" and base_url.rstrip("/") == _model_runner_base_url(source_env).rstrip("/"):
        return default_gateway
    return LlamaCppRunnerGateway(
        base_url=base_url,
        model_ref=profile,
        runner_ref="llama.cpp",
        timeout_seconds=_model_runner_timeout_seconds(source_env),
    )


def _verify_models_paths(env: Mapping[str, str]) -> None:
    models_root = Path("/app/models")
    production_root = models_root / "production"
    strict = _models_path_strict_mode(env=env)

    if not models_root.exists():
        logger.error(
            "expert-serving models root missing: %s (dev expects ./models bind mount; prod expects baked models)",
            models_root.as_posix(),
        )
        if strict:
            raise RuntimeError(f"models root missing in production: {models_root.as_posix()}")
        return

    if not production_root.exists():
        logger.error(
            "expert-serving production models path missing: %s (latest.json/registry not discoverable)",
            production_root.as_posix(),
        )
        if strict:
            raise RuntimeError(f"models/production missing in production: {production_root.as_posix()}")
        return

    latest_candidates = sorted(str(path) for path in production_root.glob("*/latest.json"))
    if not latest_candidates:
        logger.warning(
            "expert-serving found no latest.json pointers under %s; SSRN boot may degrade in dev",
            production_root.as_posix(),
        )


def _models_path_strict_mode(*, env: Mapping[str, str]) -> bool:
    raw = str(env.get("EXPERT_SERVING_STRICT_MODELS_PATH", "") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    runtime_env = str(env.get("ENVIRONMENT", "") or "").strip().lower()
    return runtime_env in {"prod", "production"}


def _log_service_event(event_type: str, **payload: Any) -> None:
    row = {"event_type": str(event_type)}
    row.update({str(k): v for k, v in payload.items()})
    logger.info("telemetry=%s", json.dumps(row, ensure_ascii=True, sort_keys=True, default=str))


def _clip_text(value: str, *, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "...<truncated>"
