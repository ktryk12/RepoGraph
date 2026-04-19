"""Verification result schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VerificationStep(BaseModel):
    name: str                    # test | lint | type_check | static_analysis | dependency | smoke
    status: str                  # pass | fail | skip | error
    duration_ms: int = 0
    output: str = ""
    failure_count: int = 0
    tool_used: str = ""
    tool_missing: bool = False


class VerificationResult(BaseModel):
    verification_id: str
    task_id: str | None = None
    repo_path: str
    symbols_verified: list[str] = Field(default_factory=list)
    files_verified: list[str] = Field(default_factory=list)
    steps: list[VerificationStep] = Field(default_factory=list)
    overall_status: str = "pass"    # pass | fail | partial | error
    duration_ms: int = 0
    verified_at: str = ""

    def summary(self) -> dict[str, str]:
        return {s.name: s.status for s in self.steps}

    @property
    def passed(self) -> bool:
        return self.overall_status == "pass"
