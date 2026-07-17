from __future__ import annotations

from enum import Enum
from typing import List
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentType(Enum):
    MAMBA = "mamba"
    LLM = "llm"


class PolicyDirective(BaseModel):
    policy_id: str
    domain: str
    directive: str
    priority: int = Field(default=5, ge=1, le=10)
    tags: List[str] = Field(default_factory=list)


class Conflict(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    policy_a: PolicyDirective
    policy_b: PolicyDirective
    dimension: str
    severity: float = Field(ge=0.0, le=1.0)
    request_context: str


class AgentVote(BaseModel):
    agent_id: str
    agent_type: AgentType
    round: int
    score_a: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""
    reputation_weight: float = 1.0
    raw_output: str = ""


class PolicyDecision(BaseModel):
    winning_policy: PolicyDirective
    confidence: float = Field(ge=0.0, le=1.0)
    rounds_to_converge: int
    dissent_ratio: float = Field(ge=0.0, le=1.0)
    rationale: str
    fallback_used: bool = False


class FinalDecision(BaseModel):
    winning_policy: PolicyDirective
    final_confidence: float = Field(ge=0.0, le=1.0)
    rounds_used: int
    fallback_used: bool
    total_duration_ms: float
    skills_used: List[str] = Field(default_factory=list)

