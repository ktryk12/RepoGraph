"""Shared retrieval contract models — SharedRetrievalRequest + SharedRetrievalResponse."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class SharedRetrievalRequest(BaseModel):
    repo_path: str
    query: str
    task_hint: str | None = None
    consumer: str = "generic"         # claude_code | codex | babyai_agent | newmodel | generic
    session_id: str | None = None
    task_id: str | None = None
    target_model: str | None = None   # hint for model router (glm-5.1 | qwen | mixtral | ...)
    adapter_version: str = "v1"
    repo_revision: str | None = None
    content_hash: str | None = None
    target_context: int = 4096        # token budget for the output
    output_profile: str = "small"     # tiny | small | medium | patch | review
    include_debug: bool = False
    tenant_id: str = "default"
    force_refresh: bool = False       # bypass Redis cache
    analysis_step_id: str | None = None
    include_analysis_plan: bool = True
    system_instructions: str = ""
    required_tool_schemas: list[dict] = Field(default_factory=list)
    active_task_memory: dict | list | str | None = None
    tool_results: dict | list | str | None = None
    reserved_output_tokens: int = 0
    safety_margin_tokens: int = 0
    safety_margin_ratio: float = 0.0
    baseline_tokens: int | None = None


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class PromptBlock(BaseModel):
    role: str = "context"             # preamble | objective | context | retry | verification
    label: str = ""
    content: str = ""
    token_estimate: int = 0
    why_included: str = ""


class PromptPack(BaseModel):
    preamble: str = ""
    objective: str = ""
    context_blocks: list[PromptBlock] = Field(default_factory=list)
    total_tokens: int = 0
    strategy: str = "summary_first"   # summary_first | symbol_first | patch_first | test_first | retry
    target_context: int = 4096


class VerificationPlan(BaseModel):
    tests: list[str] = Field(default_factory=list)   # test files to run
    lint: bool = True
    typecheck: bool = False
    static_analysis: bool = False


class CacheInfo(BaseModel):
    used: bool = False
    keys_hit: list[str] = Field(default_factory=list)


class AnalysisStep(BaseModel):
    step_id: str
    step_kind: str
    objective: str
    step_query: str
    task_hint: str | None = None
    working_set_hint: str = ""
    suggested_output_profile: str = "small"
    suggested_target_context: int = 4096
    verification_plan: VerificationPlan = Field(default_factory=VerificationPlan)
    priority: int = 0
    depends_on: list[str] = Field(default_factory=list)


class AnalysisPlan(BaseModel):
    plan_kind: str = "analyze_code"
    query: str
    rationale: str = ""
    steps: list[AnalysisStep] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

class SharedRetrievalResponse(BaseModel):
    task_family: str
    task_id: str
    working_set_id: str
    retrieval_trace_id: str
    consumer: str = "generic"
    source_mode: str = "shared_retrieval"
    payload_mode: str = "retrieval_envelope"
    prompt_assembly_owner: str = "consumer"
    prompt_pack: PromptPack
    working_set: dict = Field(default_factory=dict)   # WorkingSet.model_dump()
    verification_plan: VerificationPlan = Field(default_factory=VerificationPlan)
    verification_plan_available: bool = False
    retry_pack_available: bool = False
    task_memory_refs: list[str] = Field(default_factory=list)
    cache: CacheInfo = Field(default_factory=CacheInfo)
    duration_ms: int = 0
    debug: dict = Field(default_factory=dict)
    analysis_step_id: str | None = None
    analysis_step_kind: str | None = None
    analysis_plan: AnalysisPlan | None = None
