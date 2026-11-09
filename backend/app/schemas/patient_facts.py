from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PatientFactEvidenceRef(BaseModel):
    message_id: str
    role: Literal["patient", "doctor"]
    snippet: str
    kind: Literal["patient_statement", "doctor_question_inference"] = (
        "patient_statement"
    )


class PatientFactView(BaseModel):
    id: str
    dimension: str
    statement: str
    status: str
    time_range: str | None = None
    condition: str | None = None
    evidence_refs: list[PatientFactEvidenceRef] = Field(default_factory=list)
    checked_message_ids: list[str] = Field(default_factory=list)


class PatientFactAnalysisRequest(BaseModel):
    focus_message_id: str = Field(min_length=1, max_length=200)
    review_context: Literal["replay", "current"] = "replay"


class PatientFactAnalysisResponse(BaseModel):
    run_id: str
    case_id: str
    focus_message_id: str
    cutoff_at: str | None = None
    review_context: Literal["replay", "current"] = "replay"
    order_quality: str
    source_mode: str
    model_name: str
    rules_version: str
    facts: list[PatientFactView]
