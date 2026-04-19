"""TaskMemory schemas — arbejdsjournal for agent task sessions."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PatchRecord(BaseModel):
    patch_id: str
    attempted_at: str
    diff_summary: str
    symbols_touched: list[str] = Field(default_factory=list)
    verification_result: str | None = None   # pass | fail | skip
    failure_reason: str | None = None


class TestFailureRecord(BaseModel):
    test_symbol: str
    failure_message: str
    recorded_at: str


class PrecisionSignals(BaseModel):
    consumer_accepted: bool | None = None
    patch_applied: bool | None = None
    verification_passed: bool | None = None


class TaskMemoryRecord(BaseModel):
    task_id: str
    query: str
    task_family: str
    working_set_id: str = ""
    retrieval_id: str = ""
    created_at: str
    updated_at: str
    status: str = "active"            # active | completed | failed | abandoned
    patches: list[PatchRecord] = Field(default_factory=list)
    test_failures: list[TestFailureRecord] = Field(default_factory=list)
    signals: PrecisionSignals = Field(default_factory=PrecisionSignals)
    notes: str = ""

    @property
    def patches_attempted(self) -> int:
        return len(self.patches)

    @property
    def last_patch_result(self) -> str | None:
        if not self.patches:
            return None
        return self.patches[-1].verification_result
