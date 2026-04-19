"""WorkingSet — first-class structured context object for consumers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkingSetSymbol(BaseModel):
    symbol: str
    in_file: str | None = None
    at_line: str | None = None
    signature: str | None = None
    summary: str | None = None
    risk_level: str = "medium"
    callers: int = 0
    calls: list[str] = Field(default_factory=list)


class WorkingSetFile(BaseModel):
    filepath: str
    file_summary: str | None = None
    symbols: list[WorkingSetSymbol] = Field(default_factory=list)


class WorkingSet(BaseModel):
    id: str
    query: str
    task_family: str
    retrieval_id: str
    files: list[WorkingSetFile] = Field(default_factory=list)
    symbols: list[WorkingSetSymbol] = Field(default_factory=list)
    token_estimate: int = 0
    token_budget: int = 4096
    compression: str = "none"   # none | drop_low_risk | signatures_only | symbols_only
    explanation: str = ""
    duration_ms: int = 0
