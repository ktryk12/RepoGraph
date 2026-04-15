"""Data models for Obsidian connector."""

from typing import Any
from pydantic import BaseModel, Field

class ObsidianConfigStatus(BaseModel):
    configured: bool
    status_message: str

class ObsidianNoteSummary(BaseModel):
    filename: str
    path: str
    content: str | None = None
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

class ObsidianSearchResponse(BaseModel):
    query: str
    results: list[ObsidianNoteSummary]
    status: str = "ok"

class ObsidianContextResult(BaseModel):
    notes: list[ObsidianNoteSummary]
    status: str = "ok"
