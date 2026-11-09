from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DoctorOpinionEvidenceRef(BaseModel):
    message_id: str
    role: Literal["doctor"] = "doctor"
    snippet: str
    kind: Literal["doctor_opinion", "doctor_question", "doctor_condition"] = (
        "doctor_opinion"
    )


class DoctorOpinionView(BaseModel):
    id: str
    source_message_id: str
    speaker_name: str | None = None
    speaker_title: str | None = None
    hospital: str | None = None
    topic: str
    claim_type: str
    statement: str
    polarity: Literal["positive", "negative"]
    certainty: Literal["asserted", "conditional", "hedged"]
    condition: str | None = None
    time_range: str | None = None
    draft_relation: str
    draft_explanation: str
    evidence_refs: list[DoctorOpinionEvidenceRef] = Field(default_factory=list)
    checked_message_ids: list[str] = Field(default_factory=list)


class PeerOpinionRelationView(BaseModel):
    id: str
    source_claim_id: str
    target_claim_id: str
    source_message_id: str
    target_message_id: str
    relation_type: Literal[
        "repeat", "supplement", "divergence", "conditional_diff", "unresolved"
    ]
    divergence_kind: str | None = None
    explanation: str
    applicability_conditions: str | None = None
    evidence_refs: list[DoctorOpinionEvidenceRef] = Field(default_factory=list)


class DoctorOpinionAnalysisRequest(BaseModel):
    focus_message_id: str = Field(min_length=1, max_length=200)
    review_context: Literal["replay", "current"] = "replay"


class DoctorOpinionAnalysisResponse(BaseModel):
    run_id: str
    case_id: str
    focus_message_id: str
    cutoff_at: str | None = None
    review_context: Literal["replay", "current"] = "replay"
    order_quality: str
    source_mode: str
    model_name: str
    rules_version: str
    opinions: list[DoctorOpinionView]
    peer_relations: list[PeerOpinionRelationView]
