from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen
import json
import os
import re
import time
from uuid import uuid4
from hashlib import sha256

from babyai_shared.ops.dashboard_service import get_ops_dashboard_service
from babyai_shared.provenance.store import ProvenanceStore
from policy.governance_smoke import GOVERNANCE_HELLO_WORLD_TEMPLATE_ID
from babyai_shared.storage.artifact_store import FileArtifactStore

try:
    from confluent_kafka import Consumer, KafkaError, Producer
except Exception:  # pragma: no cover - optional dependency
    Consumer = None  # type: ignore[assignment]
    KafkaError = None  # type: ignore[assignment]
    Producer = None  # type: ignore[assignment]


RUN_LOG_PATH = Path(os.getenv("RUN_LOG_PATH", "logs/failures.jsonl"))
BENCHMARK_PATH = Path(os.getenv("BENCHMARK_PATH", "analysis/ci_benchmark.json"))
PROVENANCE_PATH = Path(os.getenv("PROVENANCE_PATH", "provenance/provenance.sqlite"))
_DEFAULT_ARTIFACT_DIR = Path("/app/artifacts") if Path("/app/artifacts").exists() else Path(__file__).resolve().parents[1] / "artifacts"
ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", str(_DEFAULT_ARTIFACT_DIR)))
OPS_ARTIFACT_REGISTRY_MANIFEST = Path(
    os.getenv("OPS_ARTIFACT_REGISTRY_MANIFEST", "artifacts/registry/manifest.jsonl")
)
OPS_AUTOLOOP_ROOT = Path(os.getenv("OPS_AUTOLOOP_ROOT", "artifacts/autoloop"))
OPS_COURT_ROOT = Path(os.getenv("OPS_COURT_ROOT", "artifacts/court"))
OPS_REVIEW_QUEUE_ROOT = Path(os.getenv("OPS_REVIEW_QUEUE_ROOT", "artifacts/review_queue"))
OPS_REVIEW_QUEUE_CONFIG = Path(os.getenv("OPS_REVIEW_QUEUE_CONFIG", "policy/review_queue.yaml"))
BENCHMARK_ARTIFACT_DIR = Path(os.getenv("BENCHMARK_ARTIFACT_DIR", "artifacts/benchmark"))
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
UI_INTENT_TOPIC = os.getenv("UI_INTENT_TOPIC", "decision.intent")
UI_QUESTIONS_TOPIC = os.getenv("UI_QUESTIONS_TOPIC", "decision.truthpack.questions")
UI_ANSWERS_TOPIC = os.getenv("UI_ANSWERS_TOPIC", "decision.truthpack.answers")
UI_READY_TOPIC = os.getenv("UI_READY_TOPIC", "decision.truthpack.ready")
UI_CACHE_GROUP_ID = os.getenv("UI_CACHE_GROUP_ID", f"ui-cache-{uuid4().hex[:8]}")
REQUEST_GATE_BASE_URL = os.getenv("REQUEST_GATE_BASE_URL", "http://localhost:8097")
POLICY_BOOTSTRAP_BASE_URL = os.getenv("POLICY_BOOTSTRAP_BASE_URL", "http://policy-bootstrap:8100")


def _default_ui_host() -> str:
    if Path("/.dockerenv").exists():
        return "0.0.0.0"
    return "127.0.0.1"


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        return int(raw)
    except Exception:
        return int(default)


UI_HOST = os.getenv("UI_HOST", _default_ui_host())
UI_PORT = _env_int("UI_PORT", 8080)

DIMENSION_LABELS: Dict[str, str] = {
    "domain_name": "Dom\u00e6nets navn",
    "domain_description": "Form\u00e5l og kontekst",
    "authoritative_sources": "Autoritative kilder",
    "autonomous_actions": "Selvst\u00e6ndige handlinger",
    "approval_required": "Kr\u00e6ver godkendelse",
    "forbidden_outputs": "Forbudte outputs",
    "target_user_context": "Bruger og kontekst",
}


def _normalize_policy_preset(preset: str) -> str:
    normalized = str(preset or "").strip().lower() or "dev"
    if normalized not in {"public", "dev", "restricted"}:
        raise ValueError(f"unsupported policy_preset: {preset}")
    return normalized


def build_intent_payload(
    *,
    user_prompt: str,
    policy_preset: str,
    context_id: str | None = None,
    decision_id: str | None = None,
    template_id: str | None = None,
) -> Dict[str, Any]:
    safe_prompt = str(user_prompt or "").strip()
    if not safe_prompt:
        raise ValueError("user_prompt is required")
    safe_decision_id = str(decision_id or "").strip() or str(uuid4())
    safe_context_id = str(context_id or "").strip() or "dev"
    safe_policy_preset = _normalize_policy_preset(policy_preset)
    return {
        "decision_id": safe_decision_id,
        "context_id": safe_context_id,
        "policy_preset": safe_policy_preset,
        "user_prompt": safe_prompt,
        "template_id": _normalize_template_id(template_id),
    }


def _normalize_template_id(value: str | None) -> str:
    text = str(value or "").strip()
    if text == GOVERNANCE_HELLO_WORLD_TEMPLATE_ID:
        return text
    return "auto"


def governance_default_answers(*, policy_preset: str = "dev") -> Dict[str, str]:
    normalized_policy = str(policy_preset or "").strip().lower() or "dev"
    prompts = (
        ("goal", "What concrete outcome should BabyAI deliver?"),
        ("acceptance", "How should we verify that the outcome is correct?"),
        ("constraints", "Any constraints or non-goals we must respect?"),
    )
    values = {
        "goal": 'Return ONLY valid JSON: {"hello":"world"}.',
        "acceptance": 'Lifecycle reaches terminal status, eval exists, and artifact governance_smoke.v1 payload equals {"hello":"world"}.',
        "constraints": "No repository writes. No external network. Use only internal model runner and artifact writer.",
    }
    out: Dict[str, str] = {}
    for key, prompt in prompts:
        qid = _question_id(policy_preset=normalized_policy, key=key, prompt=prompt)
        out[qid] = values[key]
    return out


def _question_id(*, policy_preset: str, key: str, prompt: str) -> str:
    seed = f"{str(policy_preset).strip().lower() or 'dev'}:{key}:{prompt}"
    return f"q_{sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def format_policy_explanation(payload: Dict[str, Any]) -> Dict[str, Any]:
    policy_preset = str(payload.get("policy_preset") or "").strip() or "dev"
    safety_profile = str(payload.get("safety_profile") or "").strip()
    write_scope = str(payload.get("write_scope") or "").strip()
    explanation_raw = payload.get("policy_explanation")
    if isinstance(explanation_raw, dict):
        if not safety_profile:
            safety_profile = str(explanation_raw.get("safety_profile") or "").strip()
        if not write_scope:
            scope = explanation_raw.get("write_scope")
            if isinstance(scope, dict):
                write_scope = str(scope.get("type") or "").strip()
    if_you_change_raw = payload.get("if_you_change")
    if not isinstance(if_you_change_raw, list) and isinstance(explanation_raw, dict):
        if_you_change_raw = explanation_raw.get("if_you_change")
    if_you_change: List[str] = []
    if isinstance(if_you_change_raw, list):
        for row in if_you_change_raw:
            if not isinstance(row, dict):
                continue
            field = str(row.get("field") or "").strip()
            change = str(row.get("change") or "").strip()
            effect = str(row.get("effect") or "").strip()
            if field and change and effect:
                if_you_change.append(f"{field}: {change}. {effect}")
    summary = (
        "Approval allows execution to continue under the current effective policy. "
        "Approval does not expand permissions beyond the effective policy. "
        "Risk is controlled by write scope and safety profile."
    )
    return {
        "policy_preset": policy_preset,
        "safety_profile": safety_profile or "balanced",
        "write_scope": write_scope or "policy_service",
        "if_you_change": if_you_change,
        "summary": summary,
    }


def build_answers_payload(*, decision_id: str, answers: Dict[str, Any]) -> Dict[str, Any]:
    safe_decision_id = str(decision_id or "").strip()
    if not safe_decision_id:
        raise ValueError("decision_id is required")
    if not isinstance(answers, dict):
        raise ValueError("answers must be an object")
    normalized_answers = {
        str(key): str(value).strip()
        for key, value in sorted(answers.items(), key=lambda item: str(item[0]))
    }
    return {
        "decision_id": safe_decision_id,
        "answers": normalized_answers,
    }


def publish_kafka_payload(
    payload: Dict[str, Any],
    *,
    topic: str,
    bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS,
) -> None:
    if Producer is None:
        raise RuntimeError("confluent-kafka is not installed")
    safe_topic = str(topic).strip()
    if safe_topic not in {UI_INTENT_TOPIC, UI_ANSWERS_TOPIC}:
        raise RuntimeError(f"ui publisher does not allow topic: {safe_topic}")
    producer = Producer(  # type: ignore[misc]
        {
            "bootstrap.servers": str(bootstrap_servers),
            "socket.timeout.ms": 3000,
            "message.timeout.ms": 5000,
        }
    )
    decision_id = str(payload.get("decision_id") or "").strip() or "ui-submit"
    producer.produce(
        topic=safe_topic,
        key=decision_id.encode("utf-8"),
        value=json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )
    remaining = producer.flush(10.0)
    if remaining > 0:
        raise RuntimeError(f"kafka publish timeout: {remaining} undelivered message(s)")


def _first_text(values: Dict[str, Any], key: str) -> str:
    value = values.get(key, "")
    if isinstance(value, list):
        if not value:
            return ""
        first = value[0]
        return str(first).strip() if first is not None else ""
    return str(value).strip() if value is not None else ""


def _decode_submit_payload(content_type: str, raw_body: bytes) -> Dict[str, Any]:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized == "application/json":
        decoded = json.loads(raw_body.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("json payload must be an object")
        return dict(decoded)
    if normalized in {"application/x-www-form-urlencoded", ""}:
        parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
        return {key: values[0] if values else "" for key, values in parsed.items()}
    raise ValueError(f"unsupported content type: {content_type}")


def _request_gate_url(path: str) -> str:
    base = str(REQUEST_GATE_BASE_URL or "").strip().rstrip("/")
    suffix = "/" + str(path or "").lstrip("/")
    if not base:
        raise RuntimeError("REQUEST_GATE_BASE_URL is not configured")
    return f"{base}{suffix}"


def request_gate_json(
    *,
    method: str,
    path: str,
    payload: Dict[str, Any] | None = None,
    timeout_seconds: float = 5.0,
) -> Any:
    body = b""
    headers = {"accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers["content-type"] = "application/json"
    req = Request(
        url=_request_gate_url(path),
        data=body or None,
        method=str(method).upper(),
        headers=headers,
    )
    try:
        with urlopen(req, timeout=float(timeout_seconds)) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"request_gate_http_error status={int(exc.code)} body={raw_body}") from exc
    except URLError as exc:
        raise RuntimeError(f"request_gate_unreachable: {exc}") from exc


def _policy_bootstrap_url(path: str) -> str:
    base = str(POLICY_BOOTSTRAP_BASE_URL or "").strip().rstrip("/")
    suffix = "/" + str(path or "").lstrip("/")
    if not base:
        raise RuntimeError("POLICY_BOOTSTRAP_BASE_URL is not configured")
    return f"{base}{suffix}"


def _decode_upstream_json(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {"message": text}


def policy_bootstrap_request(
    *,
    method: str,
    path: str,
    raw_body: bytes | None = None,
    content_type: str | None = None,
    timeout_seconds: float = 120.0,
) -> tuple[int, Any]:
    body = bytes(raw_body or b"")
    headers = {"accept": "application/json"}
    if str(content_type or "").strip():
        headers["content-type"] = str(content_type).strip()
    req = Request(
        url=_policy_bootstrap_url(path),
        data=body or None,
        method=str(method).upper(),
        headers=headers,
    )
    try:
        with urlopen(req, timeout=float(timeout_seconds)) as response:
            status = int(getattr(response, "status", 200))
            raw = response.read().decode("utf-8", errors="replace")
            return status, _decode_upstream_json(raw)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), _decode_upstream_json(raw)
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("policy_bootstrap_unreachable") from exc


def _extract_proxy_error_code(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("error") or "").strip()
        return str(payload.get("error") or "").strip()
    return ""


def map_policy_proxy_error(*, status: int, payload: Any) -> Dict[str, Any]:
    error_code = _extract_proxy_error_code(payload)
    if int(status) == 503:
        message = "Modellen svarer ikke \u2014 pr\u00f8v igen om et \u00f8jeblik"
    elif int(status) == 404:
        message = "Sessionen er udl\u00f8bet \u2014 start forfra"
    elif int(status) == 409 and error_code == "max_revisions_reached":
        message = "Maksimalt antal revisioner n\u00e5et \u2014 godkend eller afvis"
    elif int(status) == 409:
        message = "Denne policy er allerede godkendt"
    elif int(status) == 422:
        message = "Ugyldigt valg \u2014 pr\u00f8v igen"
    else:
        message = "Anmodningen kunne ikke behandles"
    return {
        "ok": False,
        "error": error_code or f"proxy_http_{int(status)}",
        "message": message,
    }


def policy_proxy_get_upstream_path(path: str) -> str | None:
    segments = [segment for segment in str(path or "").split("/") if segment]
    if len(segments) != 4:
        return None
    if segments[0] != "policy" or segments[1] != "sessions":
        return None
    session_id = str(segments[2]).strip()
    action = str(segments[3]).strip()
    if not session_id or action not in {"draft", "state"}:
        return None
    return f"/sessions/{quote(session_id, safe='')}/{action}"


def policy_proxy_post_upstream_path(path: str) -> str | None:
    if str(path or "").strip() == "/policy/sessions":
        return "/sessions"
    segments = [segment for segment in str(path or "").split("/") if segment]
    if len(segments) != 4:
        return None
    if segments[0] != "policy" or segments[1] != "sessions":
        return None
    session_id = str(segments[2]).strip()
    action = str(segments[3]).strip()
    if not session_id or action not in {"documents", "answers", "approve"}:
        return None
    return f"/sessions/{quote(session_id, safe='')}/{action}"


class UiKafkaStateCache:
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        group_id: str,
        questions_topic: str,
        ready_topic: str,
    ) -> None:
        self._bootstrap_servers = str(bootstrap_servers)
        self._group_id = str(group_id)
        self._questions_topic = str(questions_topic)
        self._ready_topic = str(ready_topic)
        self._questions: dict[str, dict[str, Any]] = {}
        self._ready: dict[str, dict[str, Any]] = {}
        self._lock = Lock()
        self._thread: Thread | None = None
        self._stop = False
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        if Consumer is None:
            return
        self._thread = Thread(target=self._run_loop, daemon=True, name="ui-kafka-state-cache")
        self._thread.start()
        self._started = True

    def get_questions(self, decision_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._questions.get(decision_id, {}))

    def get_ready(self, decision_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._ready.get(decision_id, {}))

    def _run_loop(self) -> None:
        while not self._stop:
            consumer = None
            try:
                consumer = Consumer(  # type: ignore[misc]
                    {
                        "bootstrap.servers": self._bootstrap_servers,
                        "group.id": self._group_id,
                        "auto.offset.reset": "latest",
                        "enable.auto.commit": False,
                    }
                )
                consumer.subscribe([self._questions_topic, self._ready_topic])
                while not self._stop:
                    msg = consumer.poll(0.5)
                    if msg is None:
                        continue
                    if msg.error():
                        if KafkaError is not None and msg.error().code() == KafkaError._PARTITION_EOF:
                            continue
                        continue
                    raw_value = msg.value()
                    if raw_value is None:
                        consumer.commit(message=msg, asynchronous=False)
                        continue
                    try:
                        payload = json.loads(raw_value.decode("utf-8"))
                    except Exception:
                        payload = {}
                    if isinstance(payload, dict):
                        decision_id = str(payload.get("decision_id") or "").strip()
                        if decision_id:
                            with self._lock:
                                topic = str(msg.topic() or "")
                                if topic == self._questions_topic:
                                    self._questions[decision_id] = payload
                                elif topic == self._ready_topic:
                                    self._ready[decision_id] = payload
                    consumer.commit(message=msg, asynchronous=False)
            except Exception:
                time.sleep(1.0)
            finally:
                if consumer is not None:
                    try:
                        consumer.close()
                    except Exception:
                        pass


_STATE_CACHE = UiKafkaStateCache(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    group_id=UI_CACHE_GROUP_ID,
    questions_topic=UI_QUESTIONS_TOPIC,
    ready_topic=UI_READY_TOPIC,
)


def _ensure_state_cache() -> None:
    _STATE_CACHE.start()


def load_runs(path: Path = RUN_LOG_PATH, limit: int = 50) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("event_type") != "final_outcome":
            continue
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("timestamp", "")), reverse=True)
    return rows[:limit]


def load_scoreline(path: Path = BENCHMARK_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_run_detail(decision_id: str, path: Path = RUN_LOG_PATH) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("decision_id") == decision_id:
            found = row
    return found


def load_decision_result(
    decision_id: str,
    *,
    log_path: Path = RUN_LOG_PATH,
    artifact_dir: Path = ARTIFACT_DIR,
) -> Dict[str, Any]:
    safe_decision_id = str(decision_id or "").strip()
    out: Dict[str, Any] = {
        "decision_id": safe_decision_id,
        "generated_output": None,
        "passed": None,
    }
    if not safe_decision_id:
        return out
    if not log_path.exists():
        return out

    selected_row: Dict[str, Any] | None = None
    last_row: Dict[str, Any] | None = None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("decision_id") != safe_decision_id:
            continue
        if not isinstance(row, dict):
            continue
        last_row = row
        if isinstance(row.get("decision_ref"), str) and str(row.get("decision_ref")).strip():
            selected_row = row

    source_row = selected_row or last_row
    if source_row is None:
        return out
    if isinstance(source_row.get("passed"), bool):
        out["passed"] = bool(source_row["passed"])

    decision_ref_raw = source_row.get("decision_ref")
    decision_ref = str(decision_ref_raw).strip() if isinstance(decision_ref_raw, str) else ""
    if not decision_ref:
        return out

    payload_raw = _read_artifact_bytes_from_ref(decision_ref, artifact_dir=artifact_dir)
    if payload_raw is None:
        store = FileArtifactStore(root=artifact_dir)
        payload_raw = store.get(decision_ref)
    if payload_raw is None:
        return out
    payload = _parse_artifact_json_payload(payload_raw)
    if payload is None:
        return out

    generated_output = payload.get("generated_output")
    if isinstance(generated_output, dict):
        out["generated_output"] = generated_output

    artifact_decision_id = payload.get("decision_id")
    # TODO: external decision_id and artifact-internal decision_id can diverge; align contract in a dedicated follow-up.
    if isinstance(artifact_decision_id, str) and artifact_decision_id.strip() and artifact_decision_id.strip() != safe_decision_id:
        pass
    return out


_REDACTED_TOKEN_VALUE_PATTERN = re.compile(r"(:\s*)\[(REDACTED:[^\]\r\n]+)\]")


def _parse_artifact_json_payload(payload_raw: bytes) -> Dict[str, Any] | None:
    text = payload_raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except Exception:
        # Security redaction may inject unquoted [REDACTED:...] tokens into JSON values.
        repaired = _REDACTED_TOKEN_VALUE_PATTERN.sub(lambda m: f'{m.group(1)}"{m.group(2)}"', text)
        try:
            parsed = json.loads(repaired)
        except Exception:
            return None
    return parsed if isinstance(parsed, dict) else None


def _read_artifact_bytes_from_ref(decision_ref: str, artifact_dir: Path) -> bytes | None:
    ref = str(decision_ref or "").strip()
    if not ref:
        return None

    candidates: List[Path] = []
    roots = _artifact_lookup_roots(Path(artifact_dir))
    hash_value = _artifact_hash_from_ref(ref)
    safe_name = _safe_artifact_filename_from_ref(ref)
    for root in roots:
        if hash_value:
            candidates.append(root / f"artifact_sha256_{hash_value}.bin")
        if safe_name:
            candidates.append(root / safe_name)

    seen: set[str] = set()
    fallback_payload: bytes | None = None
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_file():
            try:
                payload = path.read_bytes()
            except Exception:
                continue
            if hash_value and sha256(payload).hexdigest() != hash_value:
                if fallback_payload is None:
                    fallback_payload = payload
                continue
            return payload
    return fallback_payload


def _artifact_lookup_roots(artifact_dir: Path) -> List[Path]:
    roots: List[Path] = []

    raw = Path(artifact_dir)
    if raw.is_absolute():
        roots.append(raw)
    else:
        roots.append(Path("/app") / raw)
        roots.append(Path(__file__).resolve().parents[1] / raw)
        roots.append(raw.resolve())

    fallback = Path("/app/artifacts")
    if fallback not in roots:
        roots.append(fallback)
    return roots


def _artifact_hash_from_ref(ref: str) -> str:
    text = str(ref or "").strip()
    prefix = "artifact:sha256:"
    if text.startswith(prefix):
        value = text[len(prefix):].strip().lower()
        if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
            return value
    return ""


def _safe_artifact_filename_from_ref(ref: str) -> str:
    safe = str(ref or "").strip().replace(":", "_")
    if not safe:
        return ""
    return f"{safe}.bin"


def lineage_for_file(file_path: str, store_path: Path = PROVENANCE_PATH) -> Dict[str, Any]:
    store = ProvenanceStore(store_path)
    return store.explain_change(file_path)


def load_ops_dashboard(
    *,
    artifact_registry_manifest: Path = OPS_ARTIFACT_REGISTRY_MANIFEST,
    autoloop_root: Path = OPS_AUTOLOOP_ROOT,
    court_root: Path = OPS_COURT_ROOT,
    review_queue_root: Path = OPS_REVIEW_QUEUE_ROOT,
    review_queue_config: Path = OPS_REVIEW_QUEUE_CONFIG,
) -> Dict[str, Any]:
    service = get_ops_dashboard_service(
        artifact_registry_manifest=artifact_registry_manifest,
        autoloop_root=autoloop_root,
        court_root=court_root,
        review_queue_root=review_queue_root,
        review_queue_config=review_queue_config,
    )
    return service.snapshot(limit=10)


def load_judge_summary(artifact_dir: Path = BENCHMARK_ARTIFACT_DIR) -> Dict[str, Any]:
    summary_file = artifact_dir / "judge_summary_latest.json"
    if not summary_file.exists():
        return {}
    try:
        return json.loads(summary_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def render_index_html() -> str:
    template_path = Path(__file__).resolve().with_name("command_center_index.html")
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    return """<!doctype html><html lang="en"><head><meta charset="utf-8" /><title>BabyAI Command Center</title></head><body><h1>Latest Runs</h1><h2>New Run</h2><h2>Ops Dashboard</h2><h2>Lineage</h2></body></html>"""


def render_policy_bootstrap_html() -> str:
    html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Policy Bootstrap</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700;800&family=Syne:wght@600;700;800&display=swap" rel="stylesheet" />
    <style>
      :root {
        --bg-primary: #080a12;
        --bg-surface: rgba(18, 22, 35, 0.78);
        --bg-elevated: rgba(20, 24, 38, 0.8);
        --text-primary: #f2f4ff;
        --text-secondary: #a4acd2;
        --accent-blue: #9b6aff;
        --accent-green: #24dcc4;
        --accent-amber: #ffbc5a;
        --accent-red: #ff6f95;
        --border: rgba(194, 202, 255, 0.18);
        --mono-font: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        --body-font: "DM Sans", sans-serif;
        --display-font: "Syne", sans-serif;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background: var(--bg-primary);
        color: var(--text-primary);
        font-family: var(--body-font);
        background:
          radial-gradient(circle at 9% 15%, rgba(155, 106, 255, 0.2), transparent 36%),
          radial-gradient(circle at 85% 80%, rgba(36, 220, 196, 0.16), transparent 40%),
          radial-gradient(circle at 60% 20%, rgba(255, 188, 90, 0.12), transparent 34%),
          var(--bg-primary);
      }
      a { color: #d5b7ff; text-decoration: none; }
      .layout {
        min-height: 100vh;
        display: grid;
        grid-template-columns: 320px 1fr;
        gap: 18px;
        padding: 18px;
      }
      .sidebar {
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 20px;
        background: linear-gradient(180deg, rgba(19, 24, 39, 0.92), rgba(10, 12, 22, 0.98));
        backdrop-filter: blur(12px);
      }
      .main {
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 22px;
        background: rgba(18, 22, 35, 0.7);
        backdrop-filter: blur(12px);
      }
      .title {
        margin: 0 0 6px 0;
        font-size: 24px;
        font-family: var(--display-font);
      }
      .muted { color: var(--text-secondary); }
      .mono { font-family: var(--mono-font); }
      .state-list { margin: 18px 0 20px 0; padding: 0; list-style: none; }
      .state-item {
        padding: 7px 10px;
        border: 1px solid var(--border);
        border-radius: 8px;
        margin-bottom: 8px;
        color: var(--text-secondary);
      }
      .state-item.active {
        color: var(--text-primary);
        border-color: var(--accent-blue);
        background: linear-gradient(145deg, rgba(155, 106, 255, 0.28), rgba(36, 220, 196, 0.16));
      }
      .coverage-list { margin: 0; padding: 0; list-style: none; }
      .coverage-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 5px 0;
        color: var(--text-secondary);
      }
      .coverage-item.complete { color: var(--accent-green); }
      .coverage-item.partial { color: var(--accent-amber); }
      .symbol {
        width: 18px;
        text-align: center;
        font-family: var(--mono-font);
      }
      .banner {
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 10px 12px;
        margin-bottom: 16px;
        background: var(--bg-surface);
        display: none;
      }
      .banner.error { border-color: var(--accent-red); color: #ffd7d5; }
      .banner.success { border-color: var(--accent-green); color: #c4f1ce; }
      .panel {
        border: 1px solid var(--border);
        border-radius: 12px;
        background: var(--bg-surface);
        padding: 16px;
        margin-bottom: 16px;
        opacity: 0;
        transform: translateY(4px);
        transition: opacity 140ms ease, transform 140ms ease;
        display: none;
      }
      .panel.active {
        opacity: 1;
        transform: translateY(0);
        display: block;
      }
      .row { margin-bottom: 10px; }
      .row label {
        display: block;
        margin-bottom: 6px;
        color: var(--text-secondary);
      }
      input, textarea, select {
        width: 100%;
        padding: 10px;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--bg-elevated);
        color: var(--text-primary);
        font-family: var(--body-font);
      }
      textarea { min-height: 110px; resize: vertical; }
      .button-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
      button {
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 9px 14px;
        font-weight: 600;
        cursor: pointer;
        background: var(--bg-elevated);
        color: var(--text-primary);
      }
      button.primary { background: linear-gradient(120deg, var(--accent-blue), rgba(36, 220, 196, 0.9)); color: #fff; }
      button.approve { background: rgba(36, 220, 196, 0.22); color: #8ff8e9; border-color: rgba(36, 220, 196, 0.36); }
      button.revise { border-color: var(--accent-amber); color: #f2d79b; }
      button.reject { border-color: var(--accent-red); color: #ffb3ad; }
      .question {
        border: 1px solid var(--border);
        border-radius: 8px;
        background: rgba(20, 24, 38, 0.65);
        padding: 12px;
        margin-bottom: 10px;
      }
      .warnings {
        border: 1px solid var(--accent-amber);
        border-radius: 8px;
        background: rgba(210, 153, 34, 0.1);
        padding: 10px;
      }
      .warnings ul { margin: 8px 0 0 18px; }
      .dimension-card {
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 10px;
        margin-bottom: 8px;
        background: rgba(20, 24, 38, 0.65);
      }
      .dimension-card .name {
        font-size: 12px;
        letter-spacing: 0.03em;
        color: var(--text-secondary);
      }
      .dimension-card .value {
        margin-top: 7px;
        white-space: pre-wrap;
      }
      .revision-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 8px;
        margin-bottom: 8px;
      }
      .revision-option {
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 8px;
        background: rgba(20, 24, 38, 0.65);
      }
      .confirm-box {
        border: 1px solid var(--accent-green);
        border-radius: 10px;
        background: rgba(63, 185, 80, 0.12);
        padding: 12px;
      }
      @media (max-width: 980px) {
        .layout { grid-template-columns: 1fr; }
        .sidebar { border-right: 1px solid var(--border); }
      }
    </style>
  </head>
  <body>
    <div class="layout">
      <aside class="sidebar">
        <h1 class="title">Policy Bootstrap</h1>
        <div class="muted">State machine</div>
        <ul class="state-list">
          <li class="state-item active" data-stage="start">1. Start</li>
          <li class="state-item" data-stage="discovery">2. Discovery</li>
          <li class="state-item" data-stage="draft">3. Draft</li>
          <li class="state-item" data-stage="confirmation">4. Bekr\u00e6ftelse</li>
        </ul>
        <div class="muted">Dimensionsd\u00e6kning</div>
        <ul id="coverage-list" class="coverage-list"></ul>
      </aside>
      <main class="main">
        <div class="row"><a href="/">Tilbage til oversigt</a></div>
        <div id="banner" class="banner"></div>

        <section id="panel-start" class="panel active">
          <h2>Start policy-definition</h2>
          <form id="start-form">
            <div class="row">
              <label for="domain-name">Dom\u00e6nenavn</label>
              <input id="domain-name" type="text" required placeholder="f.eks. medicinsk r\u00e5dgivning" />
            </div>
            <div class="row">
              <label for="domain-description">Beskrivelse (valgfri)</label>
              <textarea id="domain-description" placeholder="Kort beskrivelse af anvendelse og m\u00e5lgruppe"></textarea>
            </div>
            <div class="row">
              <label for="start-document">Upload PDF (valgfri)</label>
              <input id="start-document" type="file" accept="application/pdf" />
            </div>
            <div class="button-row">
              <button class="primary" type="submit">Start policy-definition</button>
            </div>
          </form>
        </section>

        <section id="panel-discovery" class="panel">
          <h2>Discovery-dialog</h2>
          <div class="row muted">Session: <span id="session-id" class="mono"></span></div>
          <div id="question-box" class="question"></div>
          <form id="answer-form">
            <div class="row">
              <label for="answer-text">Dit svar</label>
              <textarea id="answer-text" required></textarea>
            </div>
            <div class="button-row">
              <button class="primary" type="submit">Svar</button>
            </div>
          </form>
          <hr style="border-color: var(--border); margin: 16px 0;" />
          <form id="document-form">
            <div class="row">
              <label for="discovery-document">Upload ekstra PDF undervejs</label>
              <input id="discovery-document" type="file" accept="application/pdf" />
            </div>
            <div class="button-row">
              <button type="submit">Upload dokument</button>
            </div>
          </form>
        </section>

        <section id="panel-draft" class="panel">
          <h2>Draft-gennemgang</h2>
          <div class="row muted">Revisioner: <span id="revision-count">0</span></div>
          <div class="row">
            <div class="muted">Human summary</div>
            <div id="draft-summary" class="question"></div>
          </div>
          <div id="warnings-box" class="warnings" style="display:none;">
            <strong>Warnings</strong>
            <ul id="warnings-list"></ul>
          </div>
          <h3>Dimensioner</h3>
          <div id="dimensions-root"></div>
          <div class="button-row">
            <button id="approve-button" class="approve" type="button">Godkend policy</button>
            <button id="revise-button" class="revise" type="button">Revider</button>
            <button id="reject-button" class="reject" type="button">Afvis og start forfra</button>
          </div>

          <div id="revision-panel" style="display:none; margin-top: 14px;">
            <h3>Revision</h3>
            <div id="revision-options" class="revision-grid"></div>
            <div class="row">
              <label for="revision-notes">Revisionsnoter</label>
              <textarea id="revision-notes" placeholder="Beskriv hvad der skal justeres"></textarea>
            </div>
            <div class="button-row">
              <button id="send-revision-button" class="primary" type="button">Send til revision</button>
            </div>
          </div>
        </section>

        <section id="panel-confirmation" class="panel">
          <h2>Bekr\u00e6ftelse</h2>
          <div class="confirm-box">
            <div id="confirm-title">Policy er l\u00e5st og klar til brug</div>
            <div id="confirm-fingerprint-row" class="row">Fingerprint: <span id="confirm-fingerprint" class="mono"></span></div>
            <div class="row">Artifact: <span id="confirm-artifact" class="mono"></span></div>
          </div>
          <div class="button-row" style="margin-top: 12px;">
            <button id="new-session-button" class="primary" type="button">Start ny session</button>
          </div>
        </section>
      </main>
    </div>

    <script>
      const DIMENSION_LABELS = %DIMENSION_LABELS%;
      const DIMENSION_ORDER = %DIMENSION_ORDER%;
      let activeStage = "start";
      let sessionId = "";

      function setStage(stage) {
        activeStage = stage;
        document.querySelectorAll(".state-item").forEach((item) => {
          item.classList.toggle("active", item.dataset.stage === stage);
        });
        document.querySelectorAll(".panel").forEach((panel) => panel.classList.remove("active"));
        const target = document.getElementById(`panel-${stage}`);
        if (target) target.classList.add("active");
      }

      function showBanner(message, kind) {
        const banner = document.getElementById("banner");
        banner.textContent = message || "";
        banner.className = `banner ${kind || ""}`.trim();
        banner.style.display = message ? "block" : "none";
      }

      function defaultCoverage() {
        const out = {};
        DIMENSION_ORDER.forEach((name) => { out[name] = "missing"; });
        return out;
      }

      function coverageMeta(statusRaw) {
        const status = String(statusRaw || "").toLowerCase();
        if (status === "covered" || status === "complete" || status === "high") return { symbol: "●", klass: "complete" };
        if (status === "incomplete" || status === "partial" || status === "medium") return { symbol: "◑", klass: "partial" };
        return { symbol: "○", klass: "" };
      }

      function renderCoverage(coverage) {
        const root = document.getElementById("coverage-list");
        root.innerHTML = "";
        const source = Object.assign(defaultCoverage(), coverage || {});
        DIMENSION_ORDER.forEach((dimension) => {
          const row = document.createElement("li");
          const meta = coverageMeta(source[dimension]);
          row.className = `coverage-item ${meta.klass}`.trim();
          row.innerHTML = `<span class="symbol">${meta.symbol}</span><span>${DIMENSION_LABELS[dimension] || dimension}</span>`;
          root.appendChild(row);
        });
      }

      async function api(path, init) {
        let response;
        try {
          response = await fetch(path, init || {});
        } catch (_) {
          throw { status: 0, message: "Forbindelsesfejl \u2014 tjek at stacken k\u00f8rer" };
        }
        let payload = {};
        try {
          payload = await response.json();
        } catch (_) {
          payload = {};
        }
        if (!response.ok) {
          throw {
            status: response.status,
            message: payload.message || payload.error || "Anmodningen kunne ikke behandles",
          };
        }
        return payload;
      }

      async function uploadDocument(fileInputId) {
        const input = document.getElementById(fileInputId);
        if (!sessionId || !input || !input.files || !input.files[0]) return;
        const formData = new FormData();
        formData.append("file", input.files[0]);
        const result = await api(`/policy/sessions/${encodeURIComponent(sessionId)}/documents`, {
          method: "POST",
          body: formData,
        });
        showBanner(`Dokument behandlet (${result.extracted_pages || 0} sider)`, "success");
        input.value = "";
      }

      function openDraft(payload) {
        document.getElementById("revision-count").textContent = String(payload.revision_count || 0);
        document.getElementById("draft-summary").textContent =
          String(payload.human_summary || payload.draft_human || "Ingen summary tilg\u00e6ngelig");
        const draftQuality = String(payload.draft_quality || "full").toLowerCase();

        const warnings = Array.isArray(payload.warnings) ? Array.from(payload.warnings) : [];
        if (draftQuality !== "full") {
          warnings.unshift(`Draft quality: ${draftQuality}. Gennemgaa policy ekstra grundigt foer godkendelse.`);
        }
        const warningsBox = document.getElementById("warnings-box");
        const warningsList = document.getElementById("warnings-list");
        warningsList.innerHTML = "";
        warnings.forEach((item) => {
          const li = document.createElement("li");
          li.textContent = String(item);
          warningsList.appendChild(li);
        });
        warningsBox.style.display = warnings.length > 0 ? "block" : "none";

        const dimensions = Array.isArray(payload.dimensions) ? payload.dimensions : [];
        const root = document.getElementById("dimensions-root");
        root.innerHTML = "";
        const byDimension = {};
        dimensions.forEach((item) => {
          if (item && item.dimension) byDimension[String(item.dimension)] = item;
        });
        DIMENSION_ORDER.forEach((dimension) => {
          const card = document.createElement("div");
          card.className = "dimension-card";
          const data = byDimension[dimension] || {};
          card.innerHTML = `
            <div class="name">${DIMENSION_LABELS[dimension] || dimension}</div>
            <div class="value">${String(data.value || "")}</div>
            <div class="muted">Forklaring: ${String(data.human_explanation || "")}</div>
          `;
          root.appendChild(card);
        });

        const revisionRoot = document.getElementById("revision-options");
        revisionRoot.innerHTML = "";
        DIMENSION_ORDER.forEach((dimension) => {
          const label = document.createElement("label");
          label.className = "revision-option";
          label.innerHTML = `
            <input type="checkbox" value="${dimension}" />
            <span>${DIMENSION_LABELS[dimension] || dimension}</span>
          `;
          revisionRoot.appendChild(label);
        });
        setStage("draft");
      }

      async function loadDraft() {
        if (!sessionId) return;
        const payload = await api(`/policy/sessions/${encodeURIComponent(sessionId)}/draft`, { method: "GET" });
        openDraft(payload);
      }

      document.getElementById("start-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        showBanner("", "");
        const domain = document.getElementById("domain-name").value.trim();
        const description = document.getElementById("domain-description").value.trim();
        if (!domain) {
          showBanner("Dom\u00e6nenavn er p\u00e5kr\u00e6vet", "error");
          return;
        }
        try {
          const payload = await api("/policy/sessions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ domain_name: domain, description }),
          });
          sessionId = String(payload.session_id || "");
          document.getElementById("session-id").textContent = sessionId;
          document.getElementById("question-box").textContent = String(payload.first_question || "");
          renderCoverage(defaultCoverage());
          setStage("discovery");
          await uploadDocument("start-document");
        } catch (error) {
          showBanner(String(error.message || "Start mislykkedes"), "error");
        }
      });

      document.getElementById("answer-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!sessionId) return;
        const answer = document.getElementById("answer-text").value.trim();
        if (!answer) {
          showBanner("Skriv et svar f\u00f8r du forts\u00e6tter", "error");
          return;
        }
        try {
          const payload = await api(`/policy/sessions/${encodeURIComponent(sessionId)}/answers`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ answer }),
          });
          document.getElementById("answer-text").value = "";
          renderCoverage(payload.dimension_coverage || {});
          if (payload.status === "complete") {
            await loadDraft();
            return;
          }
          document.getElementById("question-box").textContent = String(payload.next_question || "");
        } catch (error) {
          showBanner(String(error.message || "Svar kunne ikke sendes"), "error");
        }
      });

      document.getElementById("document-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await uploadDocument("discovery-document");
        } catch (error) {
          showBanner(String(error.message || "Dokument-upload fejlede"), "error");
        }
      });

      document.getElementById("approve-button").addEventListener("click", async () => {
        if (!sessionId) return;
        try {
          const payload = await api(`/policy/sessions/${encodeURIComponent(sessionId)}/approve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "approve" }),
          });
          document.getElementById("confirm-title").textContent = "Policy er l\u00e5st og klar til brug";
          document.getElementById("confirm-fingerprint-row").style.display = "block";
          document.getElementById("confirm-fingerprint").textContent = String(payload.fingerprint || "");
          document.getElementById("confirm-artifact").textContent = String(payload.artifact_path || "");
          setStage("confirmation");
          showBanner("Policy godkendt", "success");
        } catch (error) {
          showBanner(String(error.message || "Godkendelse fejlede"), "error");
        }
      });

      document.getElementById("revise-button").addEventListener("click", () => {
        const panel = document.getElementById("revision-panel");
        panel.style.display = panel.style.display === "none" ? "block" : "none";
      });

      document.getElementById("send-revision-button").addEventListener("click", async () => {
        if (!sessionId) return;
        const checked = [];
        document.querySelectorAll("#revision-options input[type=checkbox]:checked").forEach((el) => {
          checked.push(String(el.value));
        });
        const notes = document.getElementById("revision-notes").value.trim();
        try {
          const payload = await api(`/policy/sessions/${encodeURIComponent(sessionId)}/approve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              action: "revise",
              revision_notes: notes,
              dimensions_to_revise: checked,
            }),
          });
          document.getElementById("question-box").textContent = String(payload.next_question || "");
          document.getElementById("answer-text").value = "";
          document.getElementById("revision-notes").value = "";
          document.querySelectorAll("#revision-options input[type=checkbox]").forEach((el) => { el.checked = false; });
          showBanner("Revision startet", "success");
          setStage("discovery");
        } catch (error) {
          showBanner(String(error.message || "Revision fejlede"), "error");
        }
      });

      document.getElementById("reject-button").addEventListener("click", async () => {
        if (!sessionId) return;
        try {
          await api(`/policy/sessions/${encodeURIComponent(sessionId)}/approve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "reject" }),
          });
          document.getElementById("confirm-title").textContent = "Policy er afvist";
          document.getElementById("confirm-fingerprint-row").style.display = "none";
          document.getElementById("confirm-artifact").textContent = "";
          setStage("confirmation");
          showBanner("Session afvist", "success");
        } catch (error) {
          showBanner(String(error.message || "Afvisning fejlede"), "error");
        }
      });

      document.getElementById("new-session-button").addEventListener("click", () => {
        window.location.href = "/policy";
      });

      renderCoverage(defaultCoverage());
    </script>
  </body>
</html>"""
    return (
        html.replace("%DIMENSION_LABELS%", json.dumps(DIMENSION_LABELS, ensure_ascii=True, sort_keys=True))
        .replace("%DIMENSION_ORDER%", json.dumps(list(DIMENSION_LABELS.keys()), ensure_ascii=True))
    )


def render_approvals_html() -> str:
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Approvals</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700;800&family=Syne:wght@600;700;800&display=swap" rel="stylesheet" />
    <style>
      :root {
        --bg: #080a12;
        --card: rgba(18, 22, 35, 0.78);
        --line: rgba(194, 202, 255, 0.18);
        --txt: #f2f4ff;
        --mut: #a4acd2;
        --vio: #9b6aff;
        --teal: #24dcc4;
        --rose: #ff6f95;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: "DM Sans", sans-serif;
        color: var(--txt);
        background:
          radial-gradient(circle at 9% 15%, rgba(155, 106, 255, 0.2), transparent 36%),
          radial-gradient(circle at 85% 80%, rgba(36, 220, 196, 0.16), transparent 40%),
          radial-gradient(circle at 60% 20%, rgba(255, 188, 90, 0.12), transparent 34%),
          var(--bg);
      }
      .wrap { max-width: 1300px; margin: 0 auto; padding: 20px; }
      .hero {
        border: 1px solid var(--line);
        border-radius: 18px;
        background: linear-gradient(180deg, rgba(19, 24, 39, 0.92), rgba(10, 12, 22, 0.98));
        backdrop-filter: blur(12px);
        padding: 16px;
      }
      .ey { font-size: 12px; letter-spacing: .14em; text-transform: uppercase; color: #b7bff0; }
      .title { margin: 6px 0 2px; font: 700 30px "Syne", sans-serif; }
      .muted { color: var(--mut); }
      .top-row { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
      .card {
        margin-top: 14px;
        border: 1px solid var(--line);
        border-radius: 16px;
        background: var(--card);
        padding: 14px;
        backdrop-filter: blur(12px);
      }
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        border-radius: 999px;
        padding: 5px 10px;
        font-size: 12px;
        font-weight: 700;
        border: 1px solid transparent;
      }
      .ok { background: rgba(36, 220, 196, 0.16); border-color: rgba(36, 220, 196, 0.36); color: #8ff8e9; }
      .off { background: rgba(255, 111, 149, 0.16); border-color: rgba(255, 111, 149, 0.36); color: #ffc0d1; }
      .list { display: grid; gap: 10px; margin-top: 10px; }
      .item {
        border: 1px solid rgba(164, 172, 210, 0.2);
        border-radius: 12px;
        background: rgba(20, 24, 38, 0.65);
        padding: 12px;
      }
      .row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
      .mono {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        font-size: 12px;
      }
      .sub { font-size: 12px; color: var(--mut); margin-top: 4px; }
      .btn-row { display: flex; gap: 8px; margin-top: 10px; }
      button {
        border: 1px solid rgba(194, 202, 255, 0.3);
        border-radius: 10px;
        padding: 8px 12px;
        color: #fff;
        font-weight: 700;
        cursor: pointer;
        background: rgba(22, 28, 46, 0.8);
      }
      button.approve { background: rgba(36, 220, 196, 0.22); border-color: rgba(36, 220, 196, 0.36); color: #8ff8e9; }
      button.deny { background: rgba(255, 111, 149, 0.2); border-color: rgba(255, 111, 149, 0.36); color: #ffc0d1; }
      a { color: #d5b7ff; text-decoration: none; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <section class="hero">
        <div class="top-row">
          <div>
            <div class="ey">Command Center</div>
            <h1 class="title">Approvals</h1>
            <div class="muted">Pending decision approvals from request-gate.</div>
          </div>
          <div><a href="/">Back to dashboard</a></div>
        </div>
      </section>

      <section class="card">
        <div class="row">
          <strong>Approval Queue</strong>
          <span id="status-chip" class="chip">loading</span>
        </div>
        <div id="status" class="sub"></div>
        <div id="pending-list" class="list"></div>
      </section>
    </div>
    <script>
      const status = document.getElementById("status");
      const statusChip = document.getElementById("status-chip");
      const pendingList = document.getElementById("pending-list");

      function setChip(kind, text) {
        statusChip.className = `chip ${kind || ""}`.trim();
        statusChip.textContent = String(text || "");
      }

      function action(decisionId, verb) {
        const who = prompt(`${verb} as (approved_by):`, "ui");
        if (who === null) return;
        const reason = prompt("Reason (optional):", verb.toUpperCase());
        fetch(`/api/approvals/${encodeURIComponent(decisionId)}/${verb}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approved_by: who || "ui", reason: reason || "" }),
        })
          .then(async (r) => ({ code: r.status, body: await r.json() }))
          .then(({ code, body }) => {
            if (code >= 200 && code < 300 && body && body.ok) {
              status.textContent = `${verb} sent for ${decisionId}`;
              loadPending();
              return;
            }
            status.textContent = `${verb} failed: ${body && body.error ? body.error : "unknown_error"}`;
          })
          .catch((err) => {
            status.textContent = `${verb} failed: ${String(err)}`;
          });
      }

      function loadPending() {
        fetch("/api/approvals/pending")
          .then((r) => r.json())
          .then((rows) => {
            pendingList.innerHTML = "";
            const list = Array.isArray(rows) ? rows : [];
            if (list.length === 0) {
              setChip("ok", "empty");
              status.textContent = "No pending approvals";
              const empty = document.createElement("div");
              empty.className = "item muted";
              empty.textContent = "Queue is empty.";
              pendingList.appendChild(empty);
              return;
            }
            setChip("off", `${list.length} pending`);
            status.textContent = `${list.length} pending approval(s)`;
            list.forEach((row) => {
              const decisionId = row.decision_id || "";
              const explanation = row.approval_explanation || {};
              const ifYouChange = Array.isArray(explanation.if_you_change) ? explanation.if_you_change : [];
              const ifYouChangeText = ifYouChange.length > 0 ? ifYouChange.join(" | ") : "n/a";
              const item = document.createElement("article");
              item.className = "item";
              item.innerHTML = `
                <div class="row">
                  <a href="/run?decision_id=${encodeURIComponent(decisionId)}"><strong>${decisionId}</strong></a>
                  <span class="chip">${row.policy_preset || "preset:n/a"}</span>
                </div>
                <div class="sub">context: ${row.context_id || ""}</div>
                <div class="sub">safety: ${explanation.safety_profile || row.safety_profile || ""} - write_scope: ${explanation.write_scope || row.write_scope || ""}</div>
                <div class="sub">fingerprint: <span class="mono">${row.required_policy_fingerprint || ""}</span></div>
                <div class="sub">summary: ${explanation.summary || row.explanation || ""}</div>
                <div class="sub">if_you_change: ${ifYouChangeText}</div>
                <div class="sub">created: ${row.created_at || ""}</div>
                <div class="btn-row">
                  <button type="button" class="approve" data-id="${decisionId}" data-verb="approve">Approve</button>
                  <button type="button" class="deny" data-id="${decisionId}" data-verb="deny">Deny</button>
                </div>
              `;
              pendingList.appendChild(item);
            });
            pendingList.querySelectorAll("button[data-id]").forEach((btn) => {
              btn.addEventListener("click", () => action(btn.dataset.id, btn.dataset.verb));
            });
          })
          .catch((err) => {
            setChip("off", "offline");
            status.textContent = `Failed to load pending approvals: ${String(err)}`;
          });
      }

      loadPending();
      setInterval(loadPending, 5000);
    </script>
  </body>
</html>"""


def render_run_html(decision_id: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Run {decision_id}</title>
    <style>
      body {{ font-family: Arial, sans-serif; margin: 20px; }}
      pre {{ background: #f8f8f8; padding: 12px; }}
    </style>
  </head>
  <body>
    <a href="/">Back</a>
    <h1>Run {decision_id}</h1>
    <div id="approval-banner" class="muted"></div>
    <p id="meta"></p>
    <pre id="payload">Loading...</pre>
    <script>
      fetch("/api/decision/{decision_id}/result")
        .then(r => r.json())
        .then(data => {{
          const out = data.generated_output;
          const text = out ? out.text : "(ingen output)";
          const meta = out ? `Model: ${{out.model_ref}} | Passed: ${{data.passed}}` : "";
          document.getElementById("payload").textContent = text;
          document.getElementById("meta").textContent = meta;
        }});
      fetch("/api/approvals/{decision_id}")
        .then(r => r.json())
        .then(data => {{
          if (!data || !data.decision_id) return;
          document.getElementById("approval-banner").innerHTML =
            `Waiting for approval. <a href="/approvals">Open approvals</a>`;
        }})
        .catch(() => {{}});
    </script>
  </body>
</html>"""


def render_lineage_html(file_path: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Lineage {file_path}</title>
    <style>
      body {{ font-family: Arial, sans-serif; margin: 20px; }}
      pre {{ background: #f8f8f8; padding: 12px; }}
    </style>
  </head>
  <body>
    <a href="/">Back</a>
    <h1>Lineage for {file_path}</h1>
    <pre id="payload">Loading...</pre>
    <script>
      fetch("/api/lineage?file={file_path}")
        .then(r => r.json())
        .then(data => {{
          document.getElementById("payload").textContent = JSON.stringify(data, null, 2);
        }});
    </script>
  </body>
</html>"""


# ---------------------------------------------------------------------------
# Trading dashboard helpers (Sprint 5)
# ---------------------------------------------------------------------------

_AUDIT_BASE_URL = os.getenv("AUDIT_BASE_URL", "http://localhost:8133")
_ORDER_MANAGER_DB = Path(os.getenv("ORDER_MANAGER_DB", "artifacts/order_manager/orders.db"))


def _audit_get(path: str) -> Dict[str, Any]:
    try:
        req = Request(f"{_AUDIT_BASE_URL}{path}")
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"error": str(exc)}


def _api_portfolio() -> Dict[str, Any]:
    today_report = _audit_get("/report/today")
    recent       = _audit_get("/events/recent")
    positions: List[Dict] = []
    try:
        import sqlite3 as _sq
        if _ORDER_MANAGER_DB.exists():
            conn = _sq.connect(str(_ORDER_MANAGER_DB))
            rows = conn.execute(
                "SELECT symbol, quantity, entry_price, current_price, unrealized_pnl, opened_at "
                "FROM positions WHERE is_open=1 ORDER BY opened_at DESC"
            ).fetchall()
            conn.close()
            positions = [
                {"symbol": r[0], "quantity": r[1], "entry_price": r[2],
                 "current_price": r[3], "unrealized_pnl": r[4], "opened_at": r[5]}
                for r in rows
            ]
    except Exception:
        pass
    return {
        "positions":    positions,
        "today_report": today_report,
        "recent_events": recent if isinstance(recent, list) else [],
    }


def _api_trading_audit(date_str: Optional[str] = None) -> Dict[str, Any]:
    path = f"/report/{date_str}" if date_str else "/report/today"
    return _audit_get(path)


def _render_portfolio_html() -> str:
    data    = _api_portfolio()
    pos     = data.get("positions", [])
    report  = data.get("today_report", {})
    pos_rows = "".join(
        f"<tr><td>{p['symbol']}</td><td>{p['quantity']:.6f}</td>"
        f"<td>{p['entry_price']:.4f}</td><td>{p['current_price']:.4f}</td>"
        f"<td style='color:{'green' if p['unrealized_pnl']>=0 else 'red'}'>"
        f"{p['unrealized_pnl']:+.4f}</td><td>{p['opened_at'][:19]}</td></tr>"
        for p in pos
    ) or "<tr><td colspan='6' style='color:#888'>Ingen åbne positioner</td></tr>"

    net_pnl   = report.get("net_pnl", 0)
    win_rate  = report.get("win_rate", 0)
    trades    = report.get("total_trades", 0)
    return f"""<!DOCTYPE html>
<html lang="da"><head><meta charset="utf-8"><title>Portfolio</title>
<style>body{{font-family:sans-serif;padding:20px;max-width:1100px;margin:auto}}
table{{width:100%;border-collapse:collapse;margin-top:16px}}
th,td{{padding:8px 12px;border:1px solid #ddd;text-align:left}}
th{{background:#f0f0f0}}.stat{{display:inline-block;background:#f9f9f9;
border-radius:8px;padding:12px 20px;margin:6px;text-align:center}}
.stat h3{{margin:0;font-size:1.6em}}.nav a{{margin-right:16px;text-decoration:none;color:#0066cc}}</style>
</head><body>
<div class="nav">
  <a href="/portfolio">Portfolio</a><a href="/strategies">Strategier</a>
  <a href="/keys">API-nøgler</a><a href="/audit">Audit</a>
</div>
<h1>Portfolio</h1>
<div>
  <div class="stat"><h3 style="color:{'green' if net_pnl>=0 else 'red'}">{net_pnl:+.4f}</h3><p>Dagens P&amp;L (USDT)</p></div>
  <div class="stat"><h3>{win_rate:.0%}</h3><p>Win rate</p></div>
  <div class="stat"><h3>{trades}</h3><p>Trades i dag</p></div>
  <div class="stat"><h3>{len(pos)}</h3><p>Åbne positioner</p></div>
</div>
<h2>Åbne positioner</h2>
<table><tr><th>Symbol</th><th>Antal</th><th>Entry</th><th>Kurs nu</th><th>Urealiseret P&L</th><th>Åbnet</th></tr>
{pos_rows}</table>
<p style="color:#888;margin-top:20px"><a href="/api/portfolio">JSON</a> · <a href="/audit">Fuld audit</a></p>
</body></html>"""


def _render_strategies_html() -> str:
    strategies = [
        {"id": "momentum-001",      "name": "Dual EMA Momentum",   "type": "momentum",      "enabled": True,  "symbols": "BTCUSDT,ETHUSDT,SOLUSDT", "timeframe": "1h"},
        {"id": "mean-reversion-001","name": "Bollinger Mean Rev.",  "type": "mean_reversion","enabled": False, "symbols": "BTCUSDT,ETHUSDT",          "timeframe": "4h"},
        {"id": "ecb-event-001",     "name": "ECB Event-Driven",     "type": "ecb_event",     "enabled": False, "symbols": "BTCUSDT,ETHUSDT",          "timeframe": "event"},
    ]
    rows = "".join(
        f"<tr><td>{s['name']}</td><td><code>{s['id']}</code></td>"
        f"<td>{s['type']}</td><td>{s['symbols']}</td><td>{s['timeframe']}</td>"
        f"<td><span style='color:{'green' if s['enabled'] else '#999'}'>{'Aktiv' if s['enabled'] else 'Inaktiv'}</span></td>"
        f"<td><button onclick=\"toggleStrategy('{s['id']}',{str(not s['enabled']).lower()})\">{'Pause' if s['enabled'] else 'Aktiver'}</button></td></tr>"
        for s in strategies
    )
    return f"""<!DOCTYPE html>
<html lang="da"><head><meta charset="utf-8"><title>Strategier</title>
<style>body{{font-family:sans-serif;padding:20px;max-width:1100px;margin:auto}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:8px 12px;border:1px solid #ddd}}
th{{background:#f0f0f0}}.nav a{{margin-right:16px;text-decoration:none;color:#0066cc}}</style>
</head><body>
<div class="nav">
  <a href="/portfolio">Portfolio</a><a href="/strategies">Strategier</a>
  <a href="/keys">API-nøgler</a><a href="/audit">Audit</a>
</div>
<h1>Handelsstrategier</h1>
<table><tr><th>Navn</th><th>ID</th><th>Type</th><th>Symboler</th><th>Timeframe</th><th>Status</th><th></th></tr>
{rows}</table>
<script>
async function toggleStrategy(id, enable) {{
  await fetch('/api/strategies/' + id + '/toggle', {{
    method: 'POST', headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{enabled: enable}})
  }});
  location.reload();
}}
</script>
</body></html>"""


def _render_keys_html() -> str:
    return """<!DOCTYPE html>
<html lang="da"><head><meta charset="utf-8"><title>API-nøgler</title>
<style>body{font-family:sans-serif;padding:20px;max-width:800px;margin:auto}
input{width:100%;padding:8px;margin:4px 0;box-sizing:border-box;border:1px solid #ccc;border-radius:4px}
button{background:#0066cc;color:white;border:none;padding:10px 24px;border-radius:4px;cursor:pointer}
.nav a{margin-right:16px;text-decoration:none;color:#0066cc}
.warning{background:#fff3cd;border:1px solid #ffc107;padding:12px;border-radius:4px;margin:16px 0}</style>
</head><body>
<div class="nav">
  <a href="/portfolio">Portfolio</a><a href="/strategies">Strategier</a>
  <a href="/keys">API-nøgler</a><a href="/audit">Audit</a>
</div>
<h1>BYOK — Bring Your Own Keys</h1>
<div class="warning">
  ⚠️ Dine API-nøgler krypteres med HashiCorp Vault og gemmes aldrig i plaintext.
  Opret <strong>kun læse + handel</strong> nøgler — aldrig med withdrawal-rettigheder.
</div>
<h2>Tilslut Binance</h2>
<form onsubmit="saveKeys(event,'binance')">
  <label>API Key<input type="password" id="binance_key" placeholder="Binance API Key"></label>
  <label>API Secret<input type="password" id="binance_secret" placeholder="Binance API Secret"></label>
  <button type="submit">Gem Binance-nøgler</button>
</form>
<h2>Tilslut Bybit</h2>
<form onsubmit="saveKeys(event,'bybit')">
  <label>API Key<input type="password" id="bybit_key" placeholder="Bybit API Key"></label>
  <label>API Secret<input type="password" id="bybit_secret" placeholder="Bybit API Secret"></label>
  <button type="submit">Gem Bybit-nøgler</button>
</form>
<div id="msg" style="margin-top:12px;font-weight:bold"></div>
<script>
async function saveKeys(e, exchange) {
  e.preventDefault();
  const key    = document.getElementById(exchange + '_key').value;
  const secret = document.getElementById(exchange + '_secret').value;
  const resp   = await fetch('/api/keys/' + exchange, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({api_key: key, api_secret: secret})
  });
  const data = await resp.json();
  document.getElementById('msg').textContent = data.ok ? '✅ Nøgler gemt!' : '❌ Fejl: ' + data.error;
  if (data.ok) { document.getElementById(exchange+'_key').value=''; document.getElementById(exchange+'_secret').value=''; }
}
</script>
</body></html>"""


def _render_audit_html(date_str: Optional[str] = None) -> str:
    report = _api_trading_audit(date_str)
    today  = report.get("report_date", "—")
    return f"""<!DOCTYPE html>
<html lang="da"><head><meta charset="utf-8"><title>Audit</title>
<style>body{{font-family:sans-serif;padding:20px;max-width:1000px;margin:auto}}
.stat{{display:inline-block;background:#f9f9f9;border-radius:8px;padding:12px 20px;margin:6px;text-align:center}}
.stat h3{{margin:0;font-size:1.5em}}.nav a{{margin-right:16px;text-decoration:none;color:#0066cc}}
table{{width:100%;border-collapse:collapse;margin-top:16px}}th,td{{padding:8px;border:1px solid #ddd}}
th{{background:#f0f0f0}}</style>
</head><body>
<div class="nav">
  <a href="/portfolio">Portfolio</a><a href="/strategies">Strategier</a>
  <a href="/keys">API-nøgler</a><a href="/audit">Audit</a>
</div>
<h1>Audit-trail — {today}</h1>
<form method="get" action="/audit">
  <input type="date" name="date" value="{date_str or ''}">
  <button type="submit">Vis dato</button>
</form>
<div style="margin-top:16px">
  <div class="stat"><h3>{report.get('total_trades',0)}</h3><p>Trades</p></div>
  <div class="stat"><h3 style="color:{'green' if (report.get('net_pnl') or 0)>=0 else 'red'}">{(report.get('net_pnl') or 0):+.4f}</h3><p>Net P&amp;L (USDT)</p></div>
  <div class="stat"><h3>{report.get('win_rate',0):.0%}</h3><p>Win rate</p></div>
  <div class="stat"><h3>{report.get('paper_trades',0)}</h3><p>Paper trades</p></div>
  <div class="stat"><h3>{report.get('live_trades',0)}</h3><p>Live trades</p></div>
</div>
<h2>Symboler handlet</h2>
<p>{', '.join(report.get('symbols_traded') or []) or '—'}</p>
<h2>Strategier brugt</h2>
<p>{', '.join(report.get('strategies_used') or []) or '—'}</p>
<p style="color:#888"><a href="/api/audit">JSON (i dag)</a> · Genereret: {report.get('generated_at','—')[:19]}</p>
</body></html>"""


def _api_save_keys(exchange: str, api_key: str, api_secret: str) -> Dict[str, Any]:
    try:
        from services.broker_gateway.broker_gateway.key_vault import KeyVault
        vault = KeyVault()
        ok = vault.store_credentials("default", exchange, api_key, api_secret)
        return {"ok": ok}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Fact-check review queue helpers
# ---------------------------------------------------------------------------

def _claim_review_service():
    from services.policy.src.review_queue_service import get_review_queue_service
    return get_review_queue_service(
        root=OPS_REVIEW_QUEUE_ROOT / "claims",
        path=OPS_REVIEW_QUEUE_CONFIG,
    )


def _api_list_claim_reviews() -> Dict[str, Any]:
    try:
        svc     = _claim_review_service()
        pending = svc.list_pending()
        metrics = svc.metrics()
        return {
            "pending":  [e.to_dict() for e in pending],
            "metrics":  metrics.to_dict(),
        }
    except Exception as exc:
        return {"pending": [], "metrics": {}, "error": str(exc)}


def _api_get_claim_review(review_id: str) -> Dict[str, Any]:
    try:
        entry = _claim_review_service().get(review_id)
        if entry is None:
            return {"error": "not_found"}
        return entry.to_dict()
    except Exception as exc:
        return {"error": str(exc)}


def _api_resolve_claim_review(
    review_id: str,
    *,
    resolution: str,
    reviewer: str,
    notes: Optional[str],
) -> Dict[str, Any]:
    try:
        entry = _claim_review_service().resolve(
            review_id,
            resolution=resolution,
            reviewer=reviewer,
            notes=notes,
        )
        return {"ok": True, "entry": entry.to_dict()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _render_claims_review_html() -> str:
    data = _api_list_claim_reviews()
    pending  = data.get("pending", [])
    metrics  = data.get("metrics", {})
    rows = ""
    for e in pending:
        verdict    = e.get("payload", {}).get("verdict", "—")
        claim_text = e.get("subject_id", "")[:80]
        sla        = e.get("sla_due_at", "")[:10]
        rid        = e.get("review_id", "")
        rows += (
            f"<tr>"
            f"<td>{claim_text}</td>"
            f"<td><b>{verdict}</b></td>"
            f"<td>{sla}</td>"
            f"<td>"
            f"<button onclick=\"resolve('{rid}','approved')\">✅ Godkend</button> "
            f"<button onclick=\"resolve('{rid}','rejected')\">❌ Afvis</button> "
            f"<button onclick=\"resolve('{rid}','revise')\">✏️ Revider</button>"
            f"</td>"
            f"</tr>\n"
        )
    backlog = int(metrics.get("backlog_size", 0))
    overdue = int(metrics.get("overdue_sla_count", 0))
    return f"""<!DOCTYPE html>
<html lang="da"><head><meta charset="utf-8">
<title>Fact-check Review Queue</title>
<style>body{{font-family:sans-serif;padding:20px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:8px;text-align:left}}
th{{background:#f0f0f0}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.85em}}
.warn{{background:#fff3cd}}</style>
</head><body>
<h1>Fact-check Review Queue</h1>
<p>Afventer: <b>{backlog}</b> &nbsp;|&nbsp; Overskredet SLA: <b class="{'warn' if overdue else ''}">{overdue}</b>
&nbsp;|&nbsp; <a href="/review/analytics">Analytics</a></p>
<table>
<thead><tr><th>Påstand</th><th>Verdict</th><th>SLA-frist</th><th>Handling</th></tr></thead>
<tbody>{rows if rows else "<tr><td colspan=4>Ingen afventende reviews</td></tr>"}</tbody>
</table>
<script>
async function resolve(id, resolution) {{
  const notes = resolution === 'revise' ? prompt('Revisionsnoter:') : null;
  const resp  = await fetch('/api/review/claims/' + id + '/resolve', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{resolution, reviewer: 'ui-user', notes}})
  }});
  const data = await resp.json();
  if (data.ok) location.reload();
  else alert('Fejl: ' + data.error);
}}
</script>
</body></html>"""


def _render_claims_analytics_html() -> str:
    data    = _api_list_claim_reviews()
    metrics = data.get("metrics", {})
    return f"""<!DOCTYPE html>
<html lang="da"><head><meta charset="utf-8">
<title>Fact-check Analytics</title>
<style>body{{font-family:sans-serif;padding:20px}}
.stat{{display:inline-block;background:#f5f5f5;border-radius:8px;
       padding:16px 24px;margin:8px;text-align:center}}
.stat h2{{margin:0;font-size:2em}} .stat p{{margin:4px 0;color:#555}}</style>
</head><body>
<h1>Fact-check Analytics</h1>
<a href="/review/claims">← Tilbage til review-køen</a>
<div style="margin-top:20px">
  <div class="stat"><h2>{metrics.get('pending_count', 0)}</h2><p>Afventer review</p></div>
  <div class="stat"><h2>{metrics.get('reviewed_count', 0)}</h2><p>Gennemgået</p></div>
  <div class="stat"><h2>{metrics.get('expired_count', 0)}</h2><p>Udløbet</p></div>
  <div class="stat"><h2>{metrics.get('overdue_sla_count', 0)}</h2><p>Overskredet SLA</p></div>
  <div class="stat"><h2>{metrics.get('sla_days', '—')}</h2><p>SLA-dage</p></div>
</div>
<p style="color:#888;margin-top:30px">Evalueret: {metrics.get('evaluated_at','—')}</p>
</body></html>"""


class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_policy(self, *, method: str, upstream_path: str, raw_body: bytes = b"", content_type: str = "") -> None:
        try:
            status, payload = policy_bootstrap_request(
                method=method,
                path=upstream_path,
                raw_body=raw_body,
                content_type=content_type,
            )
        except Exception:
            self._send_json(
                {
                    "ok": False,
                    "error": "proxy_upstream_unavailable",
                    "message": "Forbindelsesfejl \u2014 tjek at stacken k\u00f8rer",
                },
                status=502,
            )
            return
        if 200 <= int(status) < 300:
            self._send_json(payload, status=int(status))
            return
        self._send_json(map_policy_proxy_error(status=int(status), payload=payload), status=int(status))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/":
            _ensure_state_cache()
            self._send_html(render_index_html())
            return
        if parsed.path == "/approvals":
            self._send_html(render_approvals_html())
            return
        if parsed.path == "/policy":
            self._send_html(render_policy_bootstrap_html())
            return
        if parsed.path == "/health":
            self._send_json({"ok": True})
            return
        if parsed.path == "/run":
            decision_id = (params.get("decision_id") or [""])[0]
            self._send_html(render_run_html(decision_id))
            return
        if parsed.path == "/lineage":
            file_path = (params.get("file") or [""])[0]
            self._send_html(render_lineage_html(file_path))
            return
        if parsed.path == "/api/runs":
            self._send_json(load_runs())
            return
        if parsed.path == "/api/scoreline":
            self._send_json(load_scoreline())
            return
        if parsed.path == "/api/run":
            decision_id = (params.get("decision_id") or [""])[0]
            payload = load_run_detail(decision_id) or {}
            self._send_json(payload)
            return
        if parsed.path.startswith("/api/decision/") and parsed.path.endswith("/result"):
            parts = [segment for segment in parsed.path.split("/") if segment]
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "decision" and parts[3] == "result":
                decision_id = unquote(parts[2]).strip()
                self._send_json(load_decision_result(decision_id))
                return
            self._send_json({"error": "not_found"}, status=404)
            return
        if parsed.path == "/api/lineage":
            file_path = (params.get("file") or [""])[0]
            payload = lineage_for_file(file_path)
            self._send_json(payload)
            return
        if parsed.path == "/api/ops/dashboard":
            self._send_json(load_ops_dashboard())
            return
        if parsed.path == "/api/judge-summary":
            self._send_json(load_judge_summary())
            return
        if parsed.path == "/api/questions":
            _ensure_state_cache()
            decision_id = (params.get("decision_id") or [""])[0].strip()
            self._send_json(_STATE_CACHE.get_questions(decision_id) if decision_id else {})
            return
        if parsed.path == "/api/ready":
            _ensure_state_cache()
            decision_id = (params.get("decision_id") or [""])[0].strip()
            self._send_json(_STATE_CACHE.get_ready(decision_id) if decision_id else {})
            return
        if parsed.path == "/api/approvals/pending":
            try:
                payload = request_gate_json(method="GET", path="/approvals/pending")
                rows: List[Dict[str, Any]] = []
                if isinstance(payload, list):
                    for item in payload:
                        if not isinstance(item, dict):
                            continue
                        row = dict(item)
                        row["approval_explanation"] = format_policy_explanation(row)
                        rows.append(row)
                self._send_json(rows)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path.startswith("/api/approvals/"):
            decision_id = parsed.path.removeprefix("/api/approvals/").strip("/")
            if decision_id and "/" not in decision_id:
                try:
                    payload = request_gate_json(
                        method="GET",
                        path=f"/approvals/{quote(decision_id, safe='')}",
                    )
                    if isinstance(payload, dict):
                        out = dict(payload)
                        out["approval_explanation"] = format_policy_explanation(out)
                        self._send_json(out)
                    else:
                        self._send_json({}, status=404)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=404)
                return

        # ── Trading dashboard (Sprint 5) ─────────────────────────────────────
        if parsed.path == "/portfolio":
            self._send_html(_render_portfolio_html())
            return
        if parsed.path == "/strategies":
            self._send_html(_render_strategies_html())
            return
        if parsed.path == "/keys":
            self._send_html(_render_keys_html())
            return
        if parsed.path == "/audit":
            date_param = (params.get("date") or [""])[0].strip() or None
            self._send_html(_render_audit_html(date_param))
            return
        if parsed.path == "/api/portfolio":
            self._send_json(_api_portfolio())
            return
        if parsed.path == "/api/audit":
            date_param = (params.get("date") or [""])[0].strip() or None
            self._send_json(_api_trading_audit(date_param))
            return
        # ── End trading dashboard ─────────────────────────────────────────────

        # ── Fact-check review queue ──────────────────────────────────────────
        if parsed.path == "/review/claims":
            self._send_html(_render_claims_review_html())
            return
        if parsed.path == "/review/analytics":
            self._send_html(_render_claims_analytics_html())
            return
        if parsed.path == "/api/review/claims":
            self._send_json(_api_list_claim_reviews())
            return
        if parsed.path.startswith("/api/review/claims/"):
            review_id = parsed.path.removeprefix("/api/review/claims/").strip("/")
            if review_id:
                self._send_json(_api_get_claim_review(review_id))
                return
        # ── End fact-check review queue ──────────────────────────────────────

        policy_upstream = policy_proxy_get_upstream_path(parsed.path)
        if policy_upstream is not None:
            self._proxy_policy(method="GET", upstream_path=policy_upstream)
            return

        self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        policy_upstream = policy_proxy_post_upstream_path(parsed.path)
        is_approval_action = (
            parsed.path.startswith("/api/approvals/")
            and (parsed.path.endswith("/approve") or parsed.path.endswith("/deny"))
        )
        is_claim_resolve = (
            parsed.path.startswith("/api/review/claims/")
            and parsed.path.endswith("/resolve")
        )
        is_keys_save = parsed.path.startswith("/api/keys/")
        is_strategy_toggle = (
            parsed.path.startswith("/api/strategies/")
            and parsed.path.endswith("/toggle")
        )
        if (
            parsed.path not in {"/api/intent", "/api/answers"}
            and not is_approval_action
            and not is_claim_resolve
            and not is_keys_save
            and not is_strategy_toggle
            and policy_upstream is None
        ):
            self._send_json({"ok": False, "error": "not_found"}, status=404)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except Exception:
            content_length = 0
        raw_body = self.rfile.read(max(0, content_length))
        content_type = self.headers.get("Content-Type", "")

        if policy_upstream is not None:
            self._proxy_policy(
                method="POST",
                upstream_path=policy_upstream,
                raw_body=raw_body,
                content_type=content_type,
            )
            return

        try:
            values = _decode_submit_payload(content_type, raw_body)
        except Exception as exc:
            if is_approval_action:
                values = {}
            else:
                self._send_json({"ok": False, "error": f"invalid request: {exc}"}, status=400)
                return

        if is_claim_resolve:
            review_id = parsed.path.removeprefix("/api/review/claims/").removesuffix("/resolve").strip("/")
            try:
                body = _decode_submit_payload(content_type, raw_body)
            except Exception:
                body = {}
            resolution = str(body.get("resolution", "")).strip() or "approved"
            reviewer   = str(body.get("reviewer", "ui-user")).strip() or "ui-user"
            notes      = str(body.get("notes", "")).strip() or None
            self._send_json(_api_resolve_claim_review(review_id, resolution=resolution, reviewer=reviewer, notes=notes))
            return

        if is_keys_save:
            exchange = parsed.path.removeprefix("/api/keys/").strip("/")
            try:
                body       = json.loads(raw_body) if raw_body else {}
                api_key    = str(body.get("api_key", "")).strip()
                api_secret = str(body.get("api_secret", "")).strip()
                if not api_key or not api_secret:
                    self._send_json({"ok": False, "error": "api_key and api_secret required"}, status=400)
                    return
                self._send_json(_api_save_keys(exchange, api_key, api_secret))
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            return

        if is_strategy_toggle:
            strategy_id = parsed.path.removeprefix("/api/strategies/").removesuffix("/toggle").strip("/")
            try:
                body    = json.loads(raw_body) if raw_body else {}
                enabled = bool(body.get("enabled", True))
                # Strategy enable/disable is config — stub response for now
                self._send_json({"ok": True, "strategy_id": strategy_id, "enabled": enabled})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/intent":
            try:
                payload = build_intent_payload(
                    user_prompt=_first_text(values, "user_prompt"),
                    policy_preset=_first_text(values, "policy_preset") or "dev",
                    context_id=_first_text(values, "context_id"),
                    decision_id=_first_text(values, "decision_id"),
                    template_id=_first_text(values, "template_id"),
                )
                publish_kafka_payload(payload, topic=UI_INTENT_TOPIC)
                self._send_json({"ok": True, "decision_id": payload["decision_id"]}, status=200)
                return
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
                return

        if is_approval_action:
            suffix = parsed.path.removeprefix("/api/approvals/")
            decision_id, _, action = suffix.partition("/")
            decision_id = decision_id.strip()
            action = action.strip()
            if not decision_id or action not in {"approve", "deny"}:
                self._send_json({"ok": False, "error": "invalid approvals path"}, status=400)
                return
            payload = {
                "approved_by": _first_text(values, "approved_by") or "ui",
                "reason": _first_text(values, "reason"),
            }
            try:
                response = request_gate_json(
                    method="POST",
                    path=f"/approvals/{quote(decision_id, safe='')}/{action}",
                    payload=payload,
                )
                if isinstance(response, dict):
                    self._send_json(response)
                else:
                    self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/answers":
            try:
                answers_raw = values.get("answers")
                answers: Dict[str, Any] = {}
                if isinstance(answers_raw, dict):
                    answers = dict(answers_raw)
                elif isinstance(answers_raw, str) and answers_raw.strip().startswith("{"):
                    parsed_answers = json.loads(answers_raw)
                    if isinstance(parsed_answers, dict):
                        answers = parsed_answers
                else:
                    if isinstance(values.get("answers_json"), str):
                        parsed_answers = json.loads(str(values["answers_json"]))
                        if isinstance(parsed_answers, dict):
                            answers = parsed_answers
                    if not answers:
                        for key, value in values.items():
                            text_key = str(key)
                            if text_key.startswith("answer_"):
                                answers[text_key.removeprefix("answer_")] = value
                payload = build_answers_payload(
                    decision_id=_first_text(values, "decision_id"),
                    answers=answers,
                )
                publish_kafka_payload(payload, topic=UI_ANSWERS_TOPIC)
                self._send_json({"ok": True, "decision_id": payload["decision_id"]}, status=200)
                return
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
                return


def serve(host: str = UI_HOST, port: int = UI_PORT) -> None:
    _ensure_state_cache()
    server = HTTPServer((host, port), RequestHandler)
    print(f"UI listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    serve()
