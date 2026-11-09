from __future__ import annotations

import hashlib
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.models import (
    AnalysisRun,
    CaseVersion,
    Claim,
    ClaimRelation,
    Draft,
    DraftVersion,
    Finding,
    PatientFact,
    ModelCall,
)
from ..schemas.joint_review import (
    FindingDiffItem,
    JointDraftSentenceView,
    JointFindingView,
    JointReviewAnalysisResponse,
    JointReviewEvidenceRef,
)
from ..schemas.scope import ReplayScope
from .doctor_opinions_service import (
    ANCHOR_RULES,
    _build_peer_relations,
    _anchors_for,
    extract_doctor_opinions,
)
from . import llm_client
from .patient_facts_service import TOPIC_RULES, extract_patient_facts
from .sentence_utils import draft_sentence_text_matches
from .scope_service import compute_replay_scope
from .sentence_utils import split_draft_sentences as _split_draft_sentences


PATIENT_TOPIC_TOKENS = {
    token for _, _, tokens in TOPIC_RULES for token in tokens
}
DOCTOR_ANCHOR_TOKENS = {
    token for _, tokens in ANCHOR_RULES for token in tokens
}

NUMERIC_DETAIL = re.compile(r"\d+(?:\.\d+)?(?:℃|度|天|小时|次)?")
TIME_DETAIL = ("昨日", "昨晚", "前天", "以前", "既往", "之前")

_ALLOWED_JOINT_CONTRIBUTIONS = {
    "new_claim",
    "repeats_prior",
    "refines_prior",
    "reinforces_prior",
    "condition_only",
    "unsupported_claim",
    "answers_prior_question",
    "unanswered_question",
    "diverges_from_prior",
    "mixed_prior_views",
    "unknown",
}
_ALLOWED_JOINT_GROUNDINGS = {
    "patient_stated",
    "patient_denied",
    "patient_uncertain",
    "doctor_question_only",
    "unmentioned",
    "detail_unverified",
    "mixed_temporal",
    "professional_recommendation",
    "needs_review",
    "unknown",
}
_ALLOWED_ACTIONS = {
    "keep_and_confirm",
    "defer",
    "revise_fact_statement",
    "clarify_or_revise",
    "clarify_condition",
    "view_both_sources",
}
_GROUNDING_NEEDS_PATIENT_EVIDENCE = {
    "patient_stated",
    "patient_denied",
    "patient_uncertain",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _msg_text(message: Any) -> str:
    return str(message.get("text") if isinstance(message, dict) else getattr(message, "text", ""))


def _msg_id(message: Any) -> str:
    value = message.get("id") if isinstance(message, dict) else getattr(message, "id", "")
    return str(value)




def _story_tokens(sentence: str) -> set[str]:
    return {
        token
        for token in PATIENT_TOPIC_TOKENS | DOCTOR_ANCHOR_TOKENS
        if token in sentence
    }


def _fact_matches(fact: Any, sentence_tokens: set[str]) -> bool:
    haystack = " ".join(
        [
            fact.statement,
            fact.condition or "",
            fact.time_range or "",
            " ".join(ref.snippet for ref in fact.evidence_refs),
        ]
    )
    return any(token in haystack for token in sentence_tokens)


def _claim_matches(
    claim: Any,
    sentence: str,
    sentence_tokens: set[str],
    draft_labels: set[str],
) -> bool:
    if claim.topic in draft_labels:
        return True
    haystack = " ".join(
        [
            claim.statement,
            claim.condition or "",
            " ".join(ref.snippet for ref in claim.evidence_refs),
        ]
    )
    return any(token in haystack for token in sentence_tokens)


def _grounding(
    sentence: str,
    sentence_tokens: set[str],
    facts: list[Any],
    claims: list[Any],
) -> tuple[str, str]:
    if not facts:
        has_medical_advice = any(
            claim.claim_type in ("recommendation", "general_knowledge")
            for claim in claims
        )
        if has_medical_advice:
            return "professional_recommendation", "该句为医学建议，患者依据不适用，应由医生专业确认。"
        if sentence_tokens:
            return "unmentioned", "焦点前患者材料未提及该句涉及的信息，不能视为患者已陈述或明确否认。"
        return "needs_review", "该句没有可对位的患者事实，角色或信息范围需人工核查。"

    detail_terms = [term for term in TIME_DETAIL if term in sentence]
    detail_terms += [
        match.group(0)
        for match in NUMERIC_DETAIL.finditer(sentence)
        if match.group(0) in ("38.5", "38.5℃", "38.5度")
    ]
    if detail_terms:
        evidence_texts = " ".join(
            [
                fact.statement
                + (fact.condition or "")
                + (fact.time_range or "")
                + " ".join(ref.snippet for ref in fact.evidence_refs)
                for fact in facts
            ]
        )
        missing = [term for term in detail_terms if term not in evidence_texts]
        if missing:
            return (
                "detail_unverified",
                "草稿包含焦点前患者材料中未出现的细节（"
                + "、".join(sorted(set(missing)))
                + "），应核对补录来源后再写入。",
            )

    statuses = {fact.status for fact in facts}
    if "denied" in statuses and "confirmed" in statuses:
        return "mixed_temporal", "同一信息点同时存在患者陈述与明确否认，草稿需保留时间边界，避免合并成单一结论。"
    if "denied" in statuses:
        return "patient_denied", "患者已在焦点前明确否认，草稿表述与患者材料一致。"
    if "uncertain" in statuses:
        return "patient_uncertain", "患者仅提供不确定或未客观确认的表述，草稿不应写成无条件的患者事实。"
    if "confirmed" in statuses:
        return "patient_stated", "该句信息可由焦点前患者材料中的患者陈述支持。"
    if "asked_unanswered" in statuses:
        return "doctor_question_only", "该信息仅有前序医生提问，患者未在焦点前作答。"
    if "unknown" in statuses:
        return "unmentioned", "患者未在焦点前提及该信息，不能推断为已陈述或明确否认。"
    return "needs_review", "该句的患者依据状态无法由当前规则确定，需人工核查。"


def _peer_notes(
    sentence_tokens: set[str],
    claims: list[Any],
    relations: list[Any],
) -> str | None:
    claim_ids = {claim.id for claim in claims}
    relevant = [
        relation
        for relation in relations
        if relation.relation_type in ("divergence", "conditional_diff")
        and relation.source_claim_id in claim_ids
        and relation.target_claim_id in claim_ids
    ]
    if not relevant:
        return None
    first = relevant[0]
    if first.relation_type == "conditional_diff":
        return (
            "前序医生对同一信息点的适用条件不同（"
            + (first.applicability_conditions or "条件待核查")
            + "），草稿不能把不同适用条件下的建议合并成单一结论。"
        )
    if first.divergence_kind == "polarity":
        return "前序医生对同一信息点方向相反，草稿需说明分歧来源后再定稿。"
    return "前序医生对同一信息点存在分歧，草稿需保留对照条件并标注待核查。"


def _contribution(
    claims: list[Any],
    relations: list[Any],
    sentence: str,
) -> str:
    if not claims:
        return "new_claim"
    claim_ids = {claim.id for claim in claims}
    relation_types = {
        relation.relation_type
        for relation in relations
        if relation.source_claim_id in claim_ids and relation.target_claim_id in claim_ids
    }
    if relation_types & {"divergence", "conditional_diff"}:
        return "mixed_prior_views"
    if any(claim.draft_relation == "diverging" for claim in claims):
        return "diverges_from_prior"
    if any(
        claim.claim_type == "clarifying_question"
        and claim.draft_relation == "unanswered_question"
        for claim in claims
    ):
        return "unanswered_question"
    if any(
        claim.certainty == "conditional"
        and claim.condition
        and claim.condition not in sentence
        for claim in claims
    ):
        return "condition_only"
    if any(
        claim.claim_type == "clarifying_question"
        and claim.draft_relation == "answered_by_draft"
        for claim in claims
    ):
        return "answers_prior_question"
    if "repeat" in relation_types:
        return "repeats_prior"
    if any(
        claim.draft_relation in ("reinforcing", "complementing")
        for claim in claims
    ):
        return "reinforces_prior"
    return "new_claim"


def _suggested_action(
    grounding: str,
    contribution: str,
    sentence_tokens: set[str],
) -> str:
    if grounding in ("unmentioned", "detail_unverified"):
        return "revise_fact_statement" if sentence_tokens else "clarify_or_revise"
    if contribution == "mixed_prior_views":
        return "view_both_sources"
    if contribution in ("diverges_from_prior", "unanswered_question"):
        return "clarify_or_revise"
    if contribution == "condition_only":
        return "clarify_condition"
    if grounding == "doctor_question_only":
        return "clarify_or_revise"
    if grounding == "patient_uncertain":
        return "clarify_condition" if contribution == "condition_only" else "clarify_or_revise"
    if contribution == "new_claim":
        return "defer"
    if grounding == "professional_recommendation":
        return "defer"
    if grounding == "mixed_temporal":
        return "keep_and_confirm"
    return "keep_and_confirm"


def _claim_note(claim: Any) -> str:
    if claim.claim_type == "clarifying_question":
        if claim.draft_relation == "answered_by_draft":
            return "草稿已回应前序提问" + (f"“{claim.statement}”" if claim.statement else "") + "。"
        return "草稿未体现对前序提问" + (f"“{claim.statement}”" if claim.statement else "") + "的回应。"
    if claim.condition:
        return (
            f"前序观点以“{claim.condition}”为适用条件（{claim.statement}），"
            "草稿需要核对当前情况是否满足该条件。"
        )
    if claim.draft_relation == "diverging":
        return "草稿在前序医生已作出明确建议的信息点上方向相反，需说明分歧来源。"
    return "前序观点覆盖该信息点，可作为审阅对照。"


def _evidence_refs(facts: list[Any], claims: list[Any]) -> list[JointReviewEvidenceRef]:
    refs: list[JointReviewEvidenceRef] = []
    seen: set[tuple[str, str, str]] = set()
    for fact in facts:
        for ref in fact.evidence_refs:
            key = (ref.message_id, ref.snippet, ref.kind)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                JointReviewEvidenceRef(
                    message_id=ref.message_id,
                    role=ref.role,
                    snippet=ref.snippet,
                    kind=ref.kind,
                )
            )
    for claim in claims:
        for ref in claim.evidence_refs:
            key = (ref.message_id, ref.snippet, ref.kind)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                JointReviewEvidenceRef(
                    message_id=ref.message_id,
                    role=ref.role,
                    snippet=ref.snippet,
                    kind=ref.kind,
                )
            )
    return refs


def _signature(sentence: JointDraftSentenceView, facts: list[Any], claims: list[Any]) -> str:
    fact_parts = sorted(
        f"{fact.status}:{fact.statement}:{fact.condition or ''}:{fact.time_range or ''}"
        for fact in facts
    )
    claim_parts = sorted(
        f"{claim.draft_relation}:{claim.statement}:{claim.condition or ''}"
        for claim in claims
    )
    payload = "|".join([sentence.text, *fact_parts, *claim_parts])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class JointReviewFailure(Exception):
    """Recorded model/pipeline failure that must remain visible in history."""

    def __init__(self, run_id: str, error_class: str, message: str) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.error_class = error_class


def _normalize_and_validate_joint(
    scope: ReplayScope,
    draft_text: str,
    content: dict[str, Any],
    facts: list[Any],
    claims: list[Any],
) -> tuple[list[JointDraftSentenceView], list[JointFindingView]]:
    """Validate one real-model joint review response against the strict scope.

    The model may choose which program-derived facts/claims relate to each
    draft sentence, but every referenced id must exist, every snippet must be
    verbatim from a visible message, every role must match, and grounded
    patient findings must carry patient evidence. Signatures are recomputed
    from the program facts/claims so unchanged claims are not diffed as new
    just because the model returned new finding ids.
    """
    sentences = _split_draft_sentences(draft_text)
    sentences_by_id = {sentence.id: sentence for sentence in sentences}
    visible_by_id = {message.id: message for message in scope.visible_messages}
    allowed_ids = set(visible_by_id)
    facts_by_id = {fact.id: fact for fact in facts}
    claims_by_id = {claim.id: claim for claim in claims}

    raw_findings = content.get("findings")
    if not isinstance(raw_findings, list):
        raise ValueError("joint output must contain a findings list")
    if content.get("structured_modules_used", False) is not False:
        raise ValueError("joint review must not claim structured modules were used")
    if len(raw_findings) != len(sentences):
        raise ValueError(
            f"joint output covers {len(raw_findings)} sentences, "
            f"expected {len(sentences)}"
        )

    seen_sentence_ids: set[str] = set()
    normalized: list[JointFindingView] = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            raise ValueError("each finding must be an object")
        sentence_id = str(raw.get("draft_sentence_id", ""))
        if sentence_id not in sentences_by_id:
            raise ValueError(f"unknown draft_sentence_id: {sentence_id}")
        if sentence_id in seen_sentence_ids:
            raise ValueError(f"duplicate draft_sentence_id: {sentence_id}")
        seen_sentence_ids.add(sentence_id)
        sentence = sentences_by_id[sentence_id]
        sentence_text = str(raw.get("draft_sentence_text", ""))
        if not draft_sentence_text_matches(sentence.text, sentence_text):
            raise ValueError(
                "draft_sentence_text must match the numbered draft sentence"
            )
        sentence_text = sentence.text

        contribution = str(raw.get("contribution", ""))
        grounding = str(raw.get("patient_grounding", ""))
        suggested_action = str(raw.get("suggested_action", ""))
        if contribution not in _ALLOWED_JOINT_CONTRIBUTIONS:
            raise ValueError(f"invalid contribution: {contribution}")
        if grounding not in _ALLOWED_JOINT_GROUNDINGS:
            raise ValueError(f"invalid patient_grounding: {grounding}")
        if suggested_action not in _ALLOWED_ACTIONS:
            raise ValueError(f"invalid suggested_action: {suggested_action}")

        evidence_refs: list[dict[str, str]] = []
        for ref in raw.get("evidence_refs") or []:
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

        related_fact_ids = [
            str(item) for item in (raw.get("related_fact_ids") or [])
        ]
        related_claim_ids = [
            str(item) for item in (raw.get("related_claim_ids") or [])
        ]
        if len(set(related_fact_ids)) != len(related_fact_ids):
            raise ValueError("related_fact_ids must not contain duplicates")
        if len(set(related_claim_ids)) != len(related_claim_ids):
            raise ValueError("related_claim_ids must not contain duplicates")
        unknown_facts = [fid for fid in related_fact_ids if fid not in facts_by_id]
        unknown_claims = [cid for cid in related_claim_ids if cid not in claims_by_id]
        if unknown_facts:
            raise ValueError(f"related_fact_ids not in program output: {unknown_facts}")
        if unknown_claims:
            raise ValueError(
                f"related_claim_ids not in program output: {unknown_claims}"
            )

        if grounding in _GROUNDING_NEEDS_PATIENT_EVIDENCE and not any(
            ref["role"] == "patient" for ref in evidence_refs
        ):
            raise ValueError(
                f"{grounding} requires at least one patient evidence reference"
            )
        if grounding == "doctor_question_only" and any(
            ref["role"] == "patient" for ref in evidence_refs
        ):
            raise ValueError(
                "doctor_question_only must not carry patient evidence refs; "
                "if the patient said it, use patient_stated/patient_uncertain"
            )
        if grounding == "unmentioned" and evidence_refs:
            raise ValueError("unmentioned must not carry fabricated evidence refs")

        matched_facts = [facts_by_id[fid] for fid in related_fact_ids]
        matched_claims = [claims_by_id[cid] for cid in related_claim_ids]
        checked_ids = [
            message.id
            for message in scope.visible_messages
            if message.role == "patient"
        ] + [
            message.id
            for message in scope.visible_messages
            if message.role == "doctor"
        ]
        normalized.append(
            JointFindingView(
                id=str(raw.get("id") or uuid.uuid4().hex),
                draft_sentence_id=sentence_id,
                draft_sentence_text=sentence.text,
                contribution=contribution,
                patient_grounding=grounding,
                explanation=str(raw.get("explanation", "")) or "模型未给出理由。",
                evidence_refs=[
                    JointReviewEvidenceRef.model_validate(ref)
                    for ref in evidence_refs
                ],
                related_fact_ids=related_fact_ids,
                related_claim_ids=related_claim_ids,
                checked_message_ids=checked_ids,
                suggested_action=suggested_action,
                review_status="pending",
                signature=_signature(sentence, matched_facts, matched_claims),
            )
        )
    return sentences, normalized


def _diff_reason(previous: Any, current: Any) -> str | None:
    if previous is None:
        return None
    case_changed = previous.case_version_id != current.case_version_id
    draft_changed = (previous.draft_version_id or None) != (
        current.draft_version_id or None
    )
    if case_changed and draft_changed:
        return "input_changed"
    if case_changed:
        return "case_version_changed"
    if draft_changed:
        return "draft_version_changed"
    return None


def _is_stub_run(run: Any) -> bool:
    source_mode = (getattr(run, "source_mode", None) or "").lower()
    if source_mode.startswith("stub_"):
        return True
    return str(getattr(run, "model_name", "") or "").startswith("medipeer-stub")


def _is_earlier_than(left: Any, right: Any) -> bool:
    left_key = (str(getattr(left, "created_at", "") or ""), str(getattr(left, "id", "") or ""))
    right_key = (str(getattr(right, "created_at", "") or ""), str(getattr(right, "id", "") or ""))
    return left_key < right_key


def _has_different_input(left: Any, right: Any) -> bool:
    return (getattr(left, "case_version_id", None) or None) != (
        getattr(right, "case_version_id", None) or None
    ) or (getattr(left, "draft_version_id", None) or None) != (
        getattr(right, "draft_version_id", None) or None
    )


def _select_previous_run(current_run: Any, candidates: list[Any]) -> Any | None:
    """Pick the comparison baseline, preferring earlier real runs with changed input."""
    if not candidates:
        return None
    earlier = [
        run
        for run in candidates
        if _is_earlier_than(run, current_run)
    ] or candidates
    if not _is_stub_run(current_run):
        real_candidates = [run for run in earlier if not _is_stub_run(run)]
        if real_candidates:
            for candidate in real_candidates:
                if _has_different_input(candidate, current_run):
                    return candidate
            return real_candidates[0]
    return earlier[0]


def _finding_diff(
    previous_run: Any,
    old_rows: list[Any],
    new_rows: list[Any],
    diff_reason: str | None,
) -> list[FindingDiffItem]:
    if previous_run is None or diff_reason is None:
        return []
    old_counts = Counter(row.signature for row in old_rows)
    new_counts = Counter(row.signature for row in new_rows)
    old_by_signature = {row.signature: row for row in old_rows}
    new_by_signature = {row.signature: row for row in new_rows}
    items: list[FindingDiffItem] = []
    for signature in sorted(set(old_counts) | set(new_counts)):
        old_count = old_counts.get(signature, 0)
        new_count = new_counts.get(signature, 0)
        shared = min(old_count, new_count)
        if new_count > old_count:
            for _ in range(new_count - old_count):
                row = new_by_signature[signature]
                items.append(
                    FindingDiffItem(
                        signature=signature,
                        draft_sentence_text=row.draft_sentence_text,
                        change="added",
                        old_status=None,
                        new_status=row.review_status,
                    )
                )
        if old_count > new_count:
            for _ in range(old_count - new_count):
                row = old_by_signature[signature]
                items.append(
                    FindingDiffItem(
                        signature=signature,
                        draft_sentence_text=row.draft_sentence_text,
                        change="resolved",
                        old_status=row.review_status,
                        new_status="resolved",
                    )
                )
        for _ in range(shared):
            old_row = old_by_signature[signature]
            new_row = new_by_signature[signature]
            items.append(
                FindingDiffItem(
                    signature=signature,
                    draft_sentence_text=new_row.draft_sentence_text,
                    change="unchanged",
                    old_status=old_row.review_status,
                    new_status=new_row.review_status,
                )
            )
    items.sort(key=lambda item: (item.change, item.draft_sentence_text))
    return items


def build_joint_findings(
    scope: ReplayScope,
    draft_text: str,
) -> tuple[list[JointDraftSentenceView], list[JointFindingView], list[Any], list[Any], list[Any]]:
    facts = extract_patient_facts(scope, draft_text)
    opinions, relations = extract_doctor_opinions(scope, draft_text)
    sentences = _split_draft_sentences(draft_text)
    findings: list[JointFindingView] = []

    for sentence in sentences:
        sentence_tokens = _story_tokens(sentence.text)
        draft_labels = _anchors_for(sentence.text)
        matched_facts = [fact for fact in facts if _fact_matches(fact, sentence_tokens)]
        matched_claims = [
            claim
            for claim in opinions
            if _claim_matches(claim, sentence.text, sentence_tokens, draft_labels)
        ]
        grounding, grounding_explanation = _grounding(
            sentence.text,
            sentence_tokens,
            matched_facts,
            matched_claims,
        )
        contribution = _contribution(matched_claims, relations, sentence.text)
        peer_note = _peer_notes(sentence_tokens, matched_claims, relations)
        explanations = [grounding_explanation]
        if matched_claims:
            explanations.append(_claim_note(matched_claims[0]))
        else:
            explanations.append("该句在焦点前没有可对照的前序医生观点，属于草稿独立新增内容。")
        if peer_note:
            explanations.append(peer_note)
        evidence = _evidence_refs(matched_facts, matched_claims)
        finding_id = uuid.uuid4().hex
        findings.append(
            JointFindingView(
                id=finding_id,
                draft_sentence_id=sentence.id,
                draft_sentence_text=sentence.text,
                contribution=contribution,
                patient_grounding=grounding,
                explanation=" ".join(explanations),
                evidence_refs=evidence,
                related_fact_ids=[fact.id for fact in matched_facts],
                related_claim_ids=[claim.id for claim in matched_claims],
                checked_message_ids=list(scope.patient_message_ids)
                + list(scope.prior_answer_message_ids),
                suggested_action=_suggested_action(
                    grounding,
                    contribution,
                    sentence_tokens,
                ),
                review_status="pending",
                signature=_signature(sentence, matched_facts, matched_claims),
            )
        )

    return sentences, findings, facts, opinions, relations


def run_joint_review(
    db: Session,
    case: Any,
    focus_message_id: str,
    review_context: str = "replay",
) -> JointReviewAnalysisResponse:
    version = db.get(CaseVersion, case.current_version_id)
    if version is None:
        raise ValueError("case has no current version")
    scope = compute_replay_scope(
        case.id,
        version.messages_json,
        focus_message_id,
        review_context,
    )
    if scope.order_quality == "unknown":
        raise ValueError("focus time cannot be parsed, strict scope unavailable")

    draft = db.scalar(select(Draft).where(Draft.case_id == case.id))
    draft_id = draft.id if draft else None
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
    input_chars = sum(len(_msg_text(message)) for message in scope.visible_messages) + len(
        draft_text
    )
    if input_chars > settings.max_input_chars:
        raise ValueError(
            f"input exceeds max_input_chars={settings.max_input_chars}; "
            "no silent truncation"
        )
    real_mode = settings.resolved_llm_provider != "stub"
    source_mode = "llm_joint" if real_mode else "stub_rules"
    run_id = uuid.uuid4().hex
    run = AnalysisRun(
        id=run_id,
        case_id=case.id,
        case_version_id=version.id,
        draft_id=draft_id,
        draft_version_id=draft_version_id,
        focus_message_id=focus_message_id,
        scope_message_ids=[message.id for message in scope.visible_messages],
        excluded_message_ids=scope.excluded_message_ids,
        exclusion_reasons=scope.exclusion_reasons,
        input_chars=input_chars,
        model_name=settings.resolved_llm_model,
        prompt_versions={
            "patient_facts": settings.prompt_version_patient_facts,
            "doctor_opinions": settings.prompt_version_prior_claims,
            "claim_relations": settings.prompt_version_claim_relations,
            "draft_review": settings.prompt_version_draft_review,
        },
        rules_version=settings.rules_version,
        source_mode=source_mode,
        review_mode="joint_review",
        status="running",
        stage="joint_review",
        started_at=_now(),
    )
    db.add(run)
    db.flush()

    previous_candidates = list(
        db.scalars(
            select(AnalysisRun)
            .where(
                AnalysisRun.case_id == case.id,
                AnalysisRun.focus_message_id == focus_message_id,
                AnalysisRun.review_mode == "joint_review",
                AnalysisRun.status == "succeeded",
                AnalysisRun.id != run_id,
            )
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        ).all()
    )
    previous_run = _select_previous_run(
        run,
        previous_candidates,
    )
    old_findings: list[Finding] = []
    status_by_signature: dict[str, str] = {}
    if previous_run is not None:
        old_findings = list(
            db.scalars(
                select(Finding).where(Finding.run_id == previous_run.id)
            ).all()
        )
        for finding in old_findings:
            status_by_signature.setdefault(finding.signature, finding.review_status)

    if real_mode:
        if not draft_text.strip():
            run.status = "failed"
            run.error = "draft is empty, joint review requires a draft"
            run.finished_at = _now()
            db.commit()
            raise JointReviewFailure(run_id, "input_error", run.error)

        call = ModelCall(
            id=uuid.uuid4().hex,
            run_id=run_id,
            tool="joint_review",
            model=settings.resolved_llm_model,
            prompt_version=settings.prompt_version_draft_review,
            generation_config=llm_client.generation_config(settings),
            input_chars=input_chars,
            status="running",
        )
        db.add(call)
        db.flush()
        # Persist the pending record before the long model call so the SQLite
        # write transaction is not held open across network latency. Concurrent
        # patient-facts/doctor-opinions writes then only contend for short
        # insert/update transactions instead of timing out on the busy lock.
        db.commit()

        try:
            facts = extract_patient_facts(scope, draft_text)
            opinions, peer_relations = extract_doctor_opinions(
                scope, draft_text
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            call.status = "failed"
            call.error_class = "structured_module_error"
            call.error = str(exc)
            run.status = "failed"
            run.error = str(exc)
            run.finished_at = _now()
            db.commit()
            raise JointReviewFailure(run_id, "structured_module_error", str(exc))

        try:
            result = llm_client.complete_joint_review(
                scope,
                draft_text,
                facts,
                opinions,
                settings,
                run_id,
            )
        except Exception as exc:  # noqa: BLE001 - real network call must fail visibly
            call.status = "failed"
            call.error_class = exc.__class__.__name__
            call.error = str(exc)
            run.status = "failed"
            run.error = str(exc)
            run.finished_at = _now()
            db.commit()
            raise JointReviewFailure(run_id, call.error_class, str(exc))

        call.latency_ms = result["latency_ms"]
        call.output_chars = len(result.get("output") or "")
        call.prompt_text = result["request_prompt"]
        if result["status"] != "ok" or result["content"] is None:
            call.status = "failed"
            call.error_class = result["error_class"] or "model_call_failed"
            call.error = result["error"] or "joint review model call failed"
            run.status = "failed"
            run.error = call.error
            run.finished_at = _now()
            db.commit()
            raise JointReviewFailure(run_id, call.error_class, run.error)

        try:
            sentences, findings = _normalize_and_validate_joint(
                scope,
                draft_text,
                result["content"],
                facts,
                opinions,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            call.status = "failed"
            call.error_class = "output_validation_error"
            call.error = str(exc)
            call.output_text = result.get("output")
            run.status = "failed"
            run.error = str(exc)
            run.finished_at = _now()
            db.commit()
            raise JointReviewFailure(run_id, "output_validation_error", str(exc))

        call.status = "ok"
        call.output_text = result.get("output")
    else:
        (
            sentences,
            findings,
            facts,
            opinions,
            peer_relations,
        ) = build_joint_findings(scope, draft_text)

    for fact in facts:
        db.add(
            PatientFact(
                id=fact.id,
                run_id=run_id,
                dimension=fact.dimension,
                statement=fact.statement,
                status=fact.status,
                time_range=fact.time_range,
                condition=fact.condition,
                evidence_refs=[ref.model_dump() for ref in fact.evidence_refs],
                checked_message_ids=fact.checked_message_ids,
            )
        )

    for opinion in opinions:
        speaker_id = next(
            (
                source.get("speaker_id")
                for source in version.messages_json
                if source.get("id") == opinion.source_message_id
            ),
            None,
        )
        db.add(
            Claim(
                id=opinion.id,
                run_id=run_id,
                case_id=case.id,
                source_message_id=opinion.source_message_id,
                speaker_id=speaker_id,
                speaker_title=opinion.speaker_title,
                hospital=opinion.hospital,
                department=None,
                claim_type=opinion.claim_type,
                statement=opinion.statement,
                time_range=opinion.time_range,
                condition=opinion.condition,
                certainty=opinion.certainty,
                evidence_refs=[ref.model_dump() for ref in opinion.evidence_refs],
            )
        )

    for relation in peer_relations:
        db.add(
            ClaimRelation(
                id=relation.id,
                run_id=run_id,
                source_claim_id=relation.source_claim_id,
                target_claim_id=relation.target_claim_id,
                relation_type=relation.relation_type,
                divergence_kind=relation.divergence_kind,
                explanation=relation.explanation,
                applicability_conditions=relation.applicability_conditions,
                evidence_refs=[ref.model_dump() for ref in relation.evidence_refs],
            )
        )

    persisted_findings: list[Finding] = []
    for finding in findings:
        carried_status = status_by_signature.get(
            finding.signature, finding.review_status
        )
        finding.review_status = carried_status
        persisted = Finding(
            id=finding.id,
            run_id=run_id,
            case_id=case.id,
            draft_sentence_id=finding.draft_sentence_id,
            draft_sentence_text=finding.draft_sentence_text,
            contribution=finding.contribution,
            patient_grounding=finding.patient_grounding,
            explanation=finding.explanation,
            evidence_refs=[ref.model_dump() for ref in finding.evidence_refs],
            related_fact_ids=finding.related_fact_ids,
            related_claim_ids=finding.related_claim_ids,
            checked_message_ids=finding.checked_message_ids,
            suggested_action=finding.suggested_action,
            review_status=carried_status,
            signature=finding.signature,
        )
        persisted_findings.append(persisted)
        db.add(
            persisted
        )

    run.status = "succeeded"
    run.finished_at = _now()
    db.commit()
    diff_reason = _diff_reason(previous_run, run)
    diff = _finding_diff(
        previous_run,
        old_findings,
        persisted_findings,
        diff_reason,
    )

    return JointReviewAnalysisResponse(
        run_id=run_id,
        case_id=case.id,
        focus_message_id=focus_message_id,
        cutoff_at=scope.cutoff_at,
        review_context=scope.review_context,
        order_quality=scope.order_quality,
        source_mode=source_mode,
        model_name=settings.resolved_llm_model,
        rules_version=settings.rules_version,
        review_mode="joint_review",
        prompt_versions={
            "patient_facts": settings.prompt_version_patient_facts,
            "doctor_opinions": settings.prompt_version_prior_claims,
            "claim_relations": settings.prompt_version_claim_relations,
            "draft_review": settings.prompt_version_draft_review,
        },
        generation_config=llm_client.generation_config(settings),
        draft_text=draft_text,
        draft_sentences=sentences,
        findings=findings,
        previous_run_id=previous_run.id if previous_run else None,
        diff_reason=diff_reason,
        diff=diff,
    )
