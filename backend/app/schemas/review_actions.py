from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReviewActionRequest(BaseModel):
    finding_id: str = Field(min_length=1, max_length=32)
    action: Literal["confirmed", "rejected", "supplement_adopted"]
    note: str | None = Field(default=None, max_length=2000)
    suggested_text: str | None = Field(default=None, max_length=4000)


class ReviewActionView(BaseModel):
    id: str
    finding_id: str
    run_id: str
    action: str
    note: str | None = None
    suggested_text: str | None = None
    supplement_id: str | None = None
    created_by: str
    created_at: str


class SupplementRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    finding_id: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=2000)


class SupplementView(BaseModel):
    id: str
    case_id: str
    case_version_id: str
    version_no: int
    message_id: str
    finding_id: str | None = None
    source_type: str
    role: str
    content: str
    created_by: str
    created_at: str


class DraftUpdateRequest(BaseModel):
    draft_text: str = Field(max_length=20000)
    reason: str | None = Field(default=None, max_length=2000)


class FindingDiffItem(BaseModel):
    signature: str
    draft_sentence_text: str
    change: Literal["added", "resolved", "unchanged"]
    old_status: str | None = None
    new_status: str = "pending"

