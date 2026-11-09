from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.models import (
    AnalysisRun,
    CaseVersion,
    Draft,
    DraftVersion,
    Finding,
    ModelCall,
)
from ..schemas.joint_review import (
    JointDraftSentenceView,
    JointFindingView,
    JointReviewAnalysisResponse,
)
from ..schemas.scope import ReplayScope
from . import llm_client
from .joint_review_service import _split_draft_sentences
from .sentence_utils import draft_sentence_text_matches
from .scope_service import compute_replay_scope


_FAILED_GROUNDING_WITHOUT_EVIDENCE = {
    "patient_stated",
    "patient_denied",
    "patient_uncertain",
}
_ALLOWED_GROUNDINGS = {
    "patient_stated",
    "patient_denied",
    "patient_uncertain",
    "doctor_question_only",
    "unmentioned",
    "professional_recommendation",
    "needs_review",
    "unknown",
}
_ALLOWED_CONTRIBUTIONS = {
    "new_claim",
    "repeats_prior",
    "refines_prior",
    "condition_only",
    "unsupported_claim",
    "answers_prior_question",
    "unknown",
}
_ALLOWED_ACTIONS = {
    "keep_and_confirm",
    "clarify_or_revise",
    "revise_fact_statement",
    "clarify_condition",
    "view_both_sources",
    "defer",
}


class BaselineReviewFailure(Exception):
    """Recorded model/pipeline failure that must remain visible in history."""

    def __init__(self, run_id: str, error_class: str, message: str) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.error_class = error_class


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fallback_signature(sentence_text: str, finding: dict[str, Any]) -> str:
    payload = "|".join(
        [
            sentence_text,
            finding["contribution"],
            finding["patient_grounding"],
            "|".join(
                ref.get("snippet", "") for ref in finding.get("evidence_refs", [])
            ),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_and_validate(
    scope: ReplayScope,
    draft_text: str,
    content: dict[str, Any],
) -> tuple[list[JointDraftSentenceView], list[dict[str, Any]]]:
    sentences = _split_draft_sentences(draft_text)
    sentences_by_id = {sentence.id: sentence for sentence in sentences}
    visible_by_id = {message.id: message for message in scope.visible_messages}
    allowed_ids = set(visible_by_id)
    raw_findings = content.get("findings")
    if not isinstance(raw_findings, list) or not raw_findings:
        raise ValueError("baseline output must contain a non-empty findings list")
    if content.get("structured_modules_used", False) not in (None, False):
        raise ValueError("baseline must not claim structured modules were used")

    normalized: list[dict[str, Any]] = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            raise ValueError("each finding must be an object")
        sentence_id = str(raw.get("draft_sentence_id", ""))
        if sentence_id not in sentences_by_id:
            raise ValueError(f"unknown draft_sentence_id: {sentence_id}")
        sentence_text = str(raw.get("draft_sentence_text", ""))
        authoritative_text = sentences_by_id[sentence_id].text
        if not draft_sentence_text_matches(authoritative_text, sentence_text):
            raise ValueError(
                "draft_sentence_text must match the numbered draft sentence"
            )
        sentence_text = authoritative_text
        contribution = str(raw.get("contribution", ""))
        grounding = str(raw.get("patient_grounding", ""))
        suggested_action = str(raw.get("suggested_action", ""))
        if contribution not in _ALLOWED_CONTRIBUTIONS:
            raise ValueError(f"invalid contribution: {contribution}")
        if grounding not in _ALLOWED_GROUNDINGS:
            raise ValueError(f"invalid patient_grounding: {grounding}")
        if suggested_action not in _ALLOWED_ACTIONS:
            raise ValueError(f"invalid suggested_action: {suggested_action}")

        evidence_refs: list[dict[str, str]] = []
        for ref in raw.get("evidence_refs", []):
            if not isinstance(ref, dict):
                raise ValueError("evidence_refs entries must be objects")
            message_id = str(ref.get("message_id", ""))
            if message_id not in allowed_ids:
                raise ValueError(f"evidence points outside strict scope: {message_id}")
            snippet = str(ref.get("snippet", ""))
            if not snippet or snippet not in visible_by_id[message_id].text:
                raise ValueError(f"evidence snippet is not verbatim: {message_id}")
            role = str(ref.get("role", "")) or visible_by_id[message_id].role
            if role != visible_by_id[message_id].role:
                raise ValueError(f"evidence role mismatch: {message_id}")
            evidence_refs.append(
                {
                    "message_id": message_id,
                    "role": role,
                    "snippet": snippet,
                    "kind": str(ref.get("kind", "direct_support")),
                }
            )
        if grounding in _FAILED_GROUNDING_WITHOUT_EVIDENCE and not evidence_refs:
            raise ValueError(
                f"{grounding} requires at least one verbatim evidence reference"
            )
        if grounding == "doctor_question_only" and any(
            ref.get("role") == "patient" for ref in evidence_refs
        ):
            raise ValueError(
                "doctor_question_only must not carry patient evidence refs"
            )

        checked_ids = [
            message.id
            for message in scope.visible_messages
            if message.role == "patient"
        ] + [
            message.id
            for message in scope.visible_messages
            if message.role == "doctor"
        ]
        explanation = str(raw.get("explanation", "")) or "直接基线未给出理由。"
        signature = str(raw.get("signature", "")) or _fallback_signature(
            sentence_text, raw
        )
        normalized.append(
            {
                "id": str(raw.get("id") or uuid.uuid4().hex),
                "draft_sentence_id": sentence_id,
                "draft_sentence_text": sentence_text,
                "contribution": contribution,
                "patient_grounding": grounding,
                "explanation": explanation,
                "evidence_refs": evidence_refs,
                "related_fact_ids": [],
                "related_claim_ids": [],
                "checked_message_ids": checked_ids,
                "suggested_action": suggested_action,
                "review_status": "pending",
                "signature": signature,
            }
        )
    return sentences, normalized


def run_baseline_review(
    db: Session,
    case: Any,
    focus_message_id: str,
    review_context: str = "replay",
) -> JointReviewAnalysisResponse:
    version = db.get(CaseVersion, case.current_version_id)
    if version is None:
        raise ValueError("case has no current version")
    scope = compute_replay_scope(
        case.id, version.messages_json, focus_message_id, review_context
    )
    if scope.order_quality == "unknown":
        raise ValueError("focus time cannot be parsed, strict scope unavailable")

    draft = db.scalar(select(Draft).where(Draft.case_id == case.id))
    draft_text = draft.current_text if draft else ""
    draft_version_id = None
    if draft is not None:
        draft_version = db.scalar(
            select(DraftVersion)
            .where(DraftVersion.draft_id == draft.id)
            .order_by(DraftVersion.version_no.desc())
            .limit(1)
        )
        draft_version_id = draft_version.id if draft_version else None

    settings = get_settings()
    input_chars = sum(len(message.text) for message in scope.visible_messages) + len(
        draft_text
    )
    if input_chars > settings.max_input_chars:
        raise ValueError(
            f"input exceeds max_input_chars={settings.max_input_chars}; "
            "no silent truncation"
        )

    run_id = uuid.uuid4().hex
    resolved_provider = settings.resolved_llm_provider
    resolved_model = settings.resolved_llm_model
    source_mode = (
        "stub_baseline" if resolved_provider == "stub" else "llm_direct"
    )
    run = AnalysisRun(
        id=run_id,
        case_id=case.id,
        case_version_id=version.id,
        draft_id=draft.id if draft else None,
        draft_version_id=draft_version_id,
        focus_message_id=focus_message_id,
        scope_message_ids=[message.id for message in scope.visible_messages],
        excluded_message_ids=scope.excluded_message_ids,
        exclusion_reasons=scope.exclusion_reasons,
        input_chars=input_chars,
        model_name=resolved_model,
        prompt_versions={
            "direct_baseline": settings.prompt_version_direct_baseline,
        },
        rules_version=settings.rules_version,
        source_mode=source_mode,
        review_mode="direct_baseline",
        status="running",
        stage="baseline_direct_review",
        started_at=_now(),
    )
    db.add(run)
    db.flush()

    call = ModelCall(
        id=uuid.uuid4().hex,
        run_id=run_id,
        tool="baseline_direct_review",
        model=resolved_model,
        prompt_version=settings.prompt_version_direct_baseline,
        generation_config=llm_client.generation_config(settings),
        input_chars=len(llm_client.build_baseline_prompt(scope, draft_text)),
        status="running",
    )
    db.add(call)
    db.flush()
    # Same pattern as joint review: release the SQLite write lock before the
    # network call so concurrent analysis writes are not blocked for its full
    # latency.
    db.commit()

    result = llm_client.complete_baseline(scope, draft_text, settings, run_id)
    call.latency_ms = result["latency_ms"]
    call.output_chars = len(result.get("output") or "")
    call.prompt_text = result["request_prompt"]
    if result["status"] != "ok" or result["content"] is None:
        call.status = "failed"
        call.error_class = result["error_class"]
        run.status = "failed"
        run.error = result["error"] or "baseline model call failed"
        run.finished_at = _now()
        db.commit()
        raise BaselineReviewFailure(
            run_id=run_id,
            error_class=call.error_class or "model_call_failed",
            message=run.error,
        )

    try:
        sentences, normalized = _normalize_and_validate(
            scope, draft_text, result["content"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        call.status = "failed"
        call.error_class = "output_validation_error"
        call.error = str(exc)
        call.output_text = result.get("output")
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = _now()
        db.commit()
        raise BaselineReviewFailure(
            run_id=run_id,
            error_class="output_validation_error",
            message=str(exc),
        )

    call.status = "ok"
    call.output_text = result.get("output")

    previous_run = db.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.case_id == case.id,
            AnalysisRun.focus_message_id == focus_message_id,
            AnalysisRun.review_mode == "direct_baseline",
            AnalysisRun.status == "succeeded",
            AnalysisRun.id != run_id,
        )
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .limit(1)
    )
    status_by_signature: dict[str, str] = {}
    if previous_run is not None:
        previous_findings = db.scalars(
            select(Finding).where(Finding.run_id == previous_run.id)
        ).all()
        for finding in previous_findings:
            status_by_signature.setdefault(finding.signature, finding.review_status)

    persisted: list[Finding] = []
    for item in normalized:
        carried_status = status_by_signature.get(
            item["signature"], item["review_status"]
        )
        item["review_status"] = carried_status
        persisted.append(
            Finding(
                id=item["id"],
                run_id=run_id,
                case_id=case.id,
                draft_sentence_id=item["draft_sentence_id"],
                draft_sentence_text=item["draft_sentence_text"],
                contribution=item["contribution"],
                patient_grounding=item["patient_grounding"],
                explanation=item["explanation"],
                evidence_refs=item["evidence_refs"],
                related_fact_ids=[],
                related_claim_ids=[],
                checked_message_ids=item["checked_message_ids"],
                suggested_action=item["suggested_action"],
                review_status=carried_status,
                signature=item["signature"],
            )
        )
        db.add(persisted[-1])

    run.status = "succeeded"
    run.finished_at = _now()
    db.commit()

    return JointReviewAnalysisResponse(
        run_id=run_id,
        case_id=case.id,
        focus_message_id=focus_message_id,
        cutoff_at=scope.cutoff_at,
        review_context=scope.review_context,
        order_quality=scope.order_quality,
        source_mode=source_mode,
        model_name=resolved_model,
        rules_version=settings.rules_version,
        review_mode="direct_baseline",
        prompt_versions={"direct_baseline": settings.prompt_version_direct_baseline},
        generation_config=llm_client.generation_config(settings),
        draft_text=draft_text,
        draft_sentences=sentences,
        findings=[JointFindingView.model_validate(item) for item in normalized],
        previous_run_id=previous_run.id if previous_run else None,
        diff_reason=None,
        diff=[],
    )
