from __future__ import annotations

from typing import Literal

from typing import Any

from pydantic import BaseModel, Field

from .review_actions import FindingDiffItem


class JointDraftSentenceView(BaseModel):
    id: str
    text: str


class JointReviewEvidenceRef(BaseModel):
    message_id: str
    role: Literal["patient", "doctor"]
    snippet: str
    kind: str


class JointFindingView(BaseModel):
    id: str
    draft_sentence_id: str
    draft_sentence_text: str
    contribution: str
    patient_grounding: str
    explanation: str
    evidence_refs: list[JointReviewEvidenceRef] = Field(default_factory=list)
    related_fact_ids: list[str] = Field(default_factory=list)
    related_claim_ids: list[str] = Field(default_factory=list)
    checked_message_ids: list[str] = Field(default_factory=list)
    suggested_action: str
    review_status: str = "pending"
    signature: str


class JointReviewAnalysisRequest(BaseModel):
    focus_message_id: str = Field(min_length=1, max_length=200)
    review_context: Literal["replay", "current"] = "replay"


class JointReviewAnalysisResponse(BaseModel):
    run_id: str
    case_id: str
    focus_message_id: str
    cutoff_at: str | None = None
    review_context: Literal["replay", "current"] = "replay"
    order_quality: str
    source_mode: str
    model_name: str
    rules_version: str
    review_mode: str = "joint_review"
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    generation_config: dict[str, Any] = Field(default_factory=dict)
    draft_text: str
    draft_sentences: list[JointDraftSentenceView]
    findings: list[JointFindingView]
    previous_run_id: str | None = None
    diff_reason: str | None = None
    diff: list[FindingDiffItem] = Field(default_factory=list)
