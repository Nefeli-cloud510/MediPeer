from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .session import Base


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TimestampMixin:
    created_at: Mapped[str] = mapped_column(String(40), default=_now, index=True)
    updated_at: Mapped[str] = mapped_column(
        String(40), default=_now, onupdate=_now, index=True
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(32), default="doctor")
    password_hash: Mapped[str] = mapped_column(String(256))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=_now)

    cases: Mapped[list["Case"]] = relationship(back_populates="owner")


class Case(TimestampMixin, Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    department: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    platform: Mapped[str] = mapped_column(String(32), default="weiyi")
    source_note: Mapped[Optional[str]] = mapped_column(Text, default=None)
    validation_scope: Mapped[str] = mapped_column(String(40), default="generic_text")
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    current_version_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("case_versions.id"), nullable=True
    )
    template_key: Mapped[Optional[str]] = mapped_column(String(80), default=None, index=True)
    seed_draft_text: Mapped[Optional[str]] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(32), default="active")

    owner: Mapped["User"] = relationship(back_populates="cases")
    versions: Mapped[list["CaseVersion"]] = relationship(
        back_populates="case", foreign_keys="CaseVersion.case_id"
    )
    drafts: Mapped[list["Draft"]] = relationship(back_populates="case")
    supplements: Mapped[list["Supplement"]] = relationship(back_populates="case")
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="case")


class CaseVersion(Base):
    __tablename__ = "case_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200), default="创建病例")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    messages_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    messages_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), default=_now)

    case: Mapped["Case"] = relationship(
        back_populates="versions", foreign_keys="CaseVersion.case_id"
    )
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="case_version")


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), unique=True, index=True)
    current_text: Mapped[str] = mapped_column(Text, default="")
    current_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default=_now, onupdate=_now)

    case: Mapped["Case"] = relationship(back_populates="drafts")
    versions: Mapped[list["DraftVersion"]] = relationship(back_populates="draft")
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="draft")


class DraftVersion(Base):
    __tablename__ = "draft_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), default=_now)

    draft: Mapped["Draft"] = relationship(back_populates="versions")
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="draft_version")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    case_version_id: Mapped[str] = mapped_column(
        ForeignKey("case_versions.id"), index=True
    )
    draft_id: Mapped[Optional[str]] = mapped_column(ForeignKey("drafts.id"), index=True)
    draft_version_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("draft_versions.id"), index=True
    )
    task_id: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    focus_message_id: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    scope_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    exclusion_reasons: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    model_name: Mapped[str] = mapped_column(String(120), default="")
    prompt_versions: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    rules_version: Mapped[str] = mapped_column(String(80), default="")
    source_mode: Mapped[str] = mapped_column(String(40), default="manual")
    review_mode: Mapped[str] = mapped_column(
        String(40), default="joint_review", server_default="joint_review", index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[Optional[str]] = mapped_column(String(80), default=None)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_at: Mapped[str] = mapped_column(String(40), default=_now, index=True)
    started_at: Mapped[Optional[str]] = mapped_column(String(40), default=None)
    finished_at: Mapped[Optional[str]] = mapped_column(String(40), default=None)

    case: Mapped["Case"] = relationship(back_populates="analysis_runs")
    case_version: Mapped["CaseVersion"] = relationship(back_populates="analysis_runs")
    draft: Mapped[Optional["Draft"]] = relationship(back_populates="analysis_runs")
    draft_version: Mapped[Optional["DraftVersion"]] = relationship(
        back_populates="analysis_runs"
    )
    facts: Mapped[list["PatientFact"]] = relationship(back_populates="run")
    claims: Mapped[list["Claim"]] = relationship(back_populates="run")
    relations: Mapped[list["ClaimRelation"]] = relationship(back_populates="run")
    findings: Mapped[list["Finding"]] = relationship(back_populates="run")


class PatientFact(Base):
    __tablename__ = "patient_facts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    dimension: Mapped[str] = mapped_column(String(40))
    statement: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40))
    time_range: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    condition: Mapped[Optional[str]] = mapped_column(Text, default=None)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    checked_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list)

    run: Mapped["AnalysisRun"] = relationship(back_populates="facts")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    source_message_id: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    speaker_id: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    speaker_title: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    hospital: Mapped[Optional[str]] = mapped_column(String(200), default=None)
    department: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    claim_type: Mapped[str] = mapped_column(String(40))
    statement: Mapped[str] = mapped_column(Text)
    time_range: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    condition: Mapped[Optional[str]] = mapped_column(Text, default=None)
    certainty: Mapped[str] = mapped_column(String(40), default="unknown")
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    run: Mapped["AnalysisRun"] = relationship(back_populates="claims")


class ClaimRelation(Base):
    __tablename__ = "claim_relations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    source_claim_id: Mapped[str] = mapped_column(String(32))
    target_claim_id: Mapped[str] = mapped_column(String(32))
    relation_type: Mapped[str] = mapped_column(String(40))
    divergence_kind: Mapped[Optional[str]] = mapped_column(String(40), default=None)
    explanation: Mapped[str] = mapped_column(Text)
    applicability_conditions: Mapped[Optional[str]] = mapped_column(Text, default=None)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    run: Mapped["AnalysisRun"] = relationship(back_populates="relations")


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    draft_sentence_id: Mapped[str] = mapped_column(String(80))
    draft_sentence_text: Mapped[str] = mapped_column(Text)
    contribution: Mapped[str] = mapped_column(String(40))
    patient_grounding: Mapped[str] = mapped_column(String(40))
    explanation: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    related_fact_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    related_claim_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    checked_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    suggested_action: Mapped[str] = mapped_column(String(40))
    review_status: Mapped[str] = mapped_column(String(40), default="pending")
    signature: Mapped[str] = mapped_column(String(64), index=True)

    run: Mapped["AnalysisRun"] = relationship(back_populates="findings")


class ReviewAction(Base):
    __tablename__ = "review_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    note: Mapped[Optional[str]] = mapped_column(Text, default=None)
    supplement_id: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    suggested_text: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[str] = mapped_column(String(40), default=_now)


class Supplement(Base):
    __tablename__ = "supplements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    case_version_id: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    finding_id: Mapped[Optional[str]] = mapped_column(ForeignKey("findings.id"), default=None)
    source_type: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(20), default="patient")
    content: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[str] = mapped_column(String(40), default=_now)

    case: Mapped["Case"] = relationship(back_populates="supplements")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    kind: Mapped[str] = mapped_column(String(32), default="analysis")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[Optional[str]] = mapped_column(String(80), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_id: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    created_at: Mapped[str] = mapped_column(String(40), default=_now, index=True)
    started_at: Mapped[Optional[str]] = mapped_column(String(40), default=None)
    finished_at: Mapped[Optional[str]] = mapped_column(String(40), default=None)


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    run_id: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(120))
    prompt_version_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), default=_now)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_hit_at: Mapped[Optional[str]] = mapped_column(String(40), default=None)


class ModelCall(Base):
    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    task_id: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    run_id: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    tool: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    generation_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    output_chars: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="ok")
    error_class: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    prompt_text: Mapped[Optional[str]] = mapped_column(Text, default=None)
    output_text: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_at: Mapped[str] = mapped_column(String(40), default=_now)


class ExportRecord(TimestampMixin, Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    export_type: Mapped[str] = mapped_column(String(20))
    file_name: Mapped[str] = mapped_column(String(240))
    file_path: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(80))


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    case_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[str] = mapped_column(String(40), default=_now)
    finished_at: Mapped[Optional[str]] = mapped_column(String(40), default=None)

