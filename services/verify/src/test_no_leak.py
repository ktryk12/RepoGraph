import json
from pathlib import Path

from tools.repo_reader import RepoReaderTool
from tools.search_local_index import SearchLocalIndexTool
from tools.base import ToolBudget
from bus.event_schemas import DecisionEvent, DecisionStatus, SCHEMA_VERSION, now_iso
from babyai_shared.storage.artifact_store import FileArtifactStore
from babyai_shared.privacy.redaction import contains_secret
from agents.failure_logger_agent import FailureLoggerAgent
from babyai_shared.bus.protocol import Context, Message, MessageType
from babyai_shared.privacy.gateway import install_logging_filter, PrivacyGateway
import logging
import io


def test_no_leak_seed_secrets(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    secret_line = "FAKE_AWS_KEY=ABCDEFGHIJKLMNOP\n"
    file_path = repo_root / "secrets.txt"
    file_path.write_text(secret_line, encoding="utf-8")

    # Tool outputs should be redacted
    repo_tool = RepoReaderTool(root=repo_root)
    res = repo_tool.run({"path": "secrets.txt"}, budget=ToolBudget(max_bytes=200))
    assert res.ok is True
    assert contains_secret(json.dumps(res.output)) is False

    search_tool = SearchLocalIndexTool(root=repo_root)
    res2 = search_tool.run({"query": "FAKE_AWS_KEY"}, budget=ToolBudget(max_results=5))
    assert res2.ok is True
    assert contains_secret(json.dumps(res2.output)) is False

    # Kafka event payload must not contain secret
    ev = DecisionEvent(
        schema_version=SCHEMA_VERSION,
        decision_id="dec-1",
        context_id="ctx-1",
        status=DecisionStatus.REQUESTED,
        timestamp=now_iso(),
        task_ref="artifact:sha256:deadbeef",
        truth_pack_ref="baseline",
        truth_pack_version="v1",
        metadata={"note": "FAKE_AWS_KEY=ABCDEFGHIJKLMNOP"},
    )
    payload = ev.to_json()
    assert contains_secret(payload) is False

    # Artifact metadata must not contain secret
    store = FileArtifactStore(root=tmp_path / "artifacts")
    ref = store.put(
        b"clean",
        context_id="ctx-1",
        name="test",
        metadata={"note": "FAKE_AWS_KEY=ABCDEFGHIJKLMNOP"},
    ).ref
    index = (tmp_path / "artifacts" / "by_context" / "ctx-1" / "names.json").read_text(encoding="utf-8")
    assert contains_secret(index) is False

    # Artifact payload must be scrubbed before persistence
    secret_payload = b"FAKE_AWS_KEY=ABCDEFGHIJKLMNOP"
    ref2 = store.put(secret_payload, context_id="ctx-1", name="payload").ref
    stored = store.get(ref2) or b""
    assert contains_secret(stored.decode("utf-8", errors="ignore")) is False

    # Failure logger JSONL must be scrubbed
    log_path = tmp_path / "failures.jsonl"
    logger_agent = FailureLoggerAgent(log_path=str(log_path))
    ctx = Context(context_id="ctx-2")
    msg = Message(
        message_id="m1",
        from_agent="test",
        to_agent="logger-001",
        message_type=MessageType.LOG_FAILURE,
        payload={"event": "FAKE_AWS_KEY=ABCDEFGHIJKLMNOP"},
        context_id="ctx-2",
        timestamp="now",
    )
    logger_agent.process(msg, ctx)
    log_text = log_path.read_text(encoding="utf-8")
    assert contains_secret(log_text) is False

    # Logging filter scrubs secrets
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    test_logger = logging.getLogger("privacy-test")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)
    install_logging_filter(test_logger, gateway=PrivacyGateway.default())
    test_logger.info("Secret: FAKE_AWS_KEY=ABCDEFGHIJKLMNOP")
    handler.flush()
    assert contains_secret(stream.getvalue()) is False
