from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MessageView(BaseModel):
    id: str
    role: Literal["patient", "doctor"]
    speaker_id: str | None = None
    speaker_name: str | None = None
    speaker_title: str | None = None
    hospital: str | None = None
    text: str
    created_at: str | None = None
    order_basis: str = "unknown"
    needs_review: bool = False
    review_reason: str | None = None


class CaseSummary(BaseModel):
    id: str
    title: str
    department: str | None = None
    platform: str
    validation_scope: str
    version_no: int
    template_key: str | None = None
    status: str
    created_at: str
    updated_at: str


class DraftView(BaseModel):
    id: str
    case_id: str
    current_text: str
    current_hash: str
    version_no: int
    updated_at: str


class CaseDetail(CaseSummary):
    messages: list[MessageView]
    draft: DraftView | None = None


class ReplayRequest(BaseModel):
    focus_message_id: str = Field(min_length=1, max_length=200)
    review_context: Literal["replay", "current"] = "replay"


class ReplayResponse(BaseModel):
    case_id: str
    focus_message_id: str
    cutoff_at: str | None = None
    review_context: Literal["replay", "current"] = "replay"
    visible_messages: list[MessageView]
    prior_answer_message_ids: list[str]
    patient_message_ids: list[str]
    excluded_message_ids: list[str]
    exclusion_reasons: dict[str, str]
    order_quality: Literal["ok", "partial", "unknown"]
