from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.models import AnalysisRun, CaseVersion, Claim, ClaimRelation, Draft, DraftVersion
from ..schemas.doctor_opinions import (
    DoctorOpinionAnalysisResponse,
    DoctorOpinionEvidenceRef,
    DoctorOpinionView,
    PeerOpinionRelationView,
)
from ..schemas.scope import ReplayScope
from .scope_service import compute_replay_scope


QUESTION_HINT = re.compile(r"(?:是否|有没有|有无|会不会|能不能|多少|几度|多久|吗)")
ADVICE_HINT = re.compile(
    r"(?:建议|需要|应当|可以|应|要|需|注意|观察|就诊|复查|检查|采血|记录体温|喝水|休息|避免)"
)
CONDITION_HINT = re.compile(
    r"(?:如果|若|一旦|出现|超过|持续不退|持续不降|持续无好转|加重|必要时|未缓解|未改善|无好转|无明显好转)"
)
HEDGE_HINT = re.compile(r"(?:可能|可以|考虑|建议)")
NEGATED_ACTION = re.compile(
    r"(?:不建议|暂不|不要|无需|不需|不用|不能|避免|勿|不推荐|没有必要|不要用)"
)
NEGATION_NEAR = re.compile(r"(?:不|未|无|没有)(?:建议|需要|要|用|使用|采取|安排|就诊|复查|继续|加用|服用)?")

OPINION_TIME_TERMS = [
    ("三到五天", "三到五天"),
    ("三至五天", "三至五天"),
    ("一周", "一周"),
    ("三天", "三天"),
    ("两天", "两天"),
    ("近期", "近期"),
    ("目前", "目前"),
    ("当前", "当前"),
    ("平时", "平时"),
]

ANCHOR_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("体温监测", ("体温", "测体温", "量体温", "记录体温", "发热", "发烧")),
    ("就诊评估", ("就诊", "医院", "急诊", "复查", "采血", "血常规", "感染指标", "检查")),
    ("观察与休息", ("观察", "多喝水", "休息", "补水", "注意休息", "居家")),
    ("对症处理", ("退热", "退烧", "止咳", "化痰", "对症处理")),
    ("抗生素", ("抗生素", "抗菌", "消炎药", "抗菌药")),
    ("症状核查", ("胸闷", "气短", "气促", "胸痛", "呼吸困难", "咳嗽")),
    ("既往病史", ("哮喘", "基础病", "过敏史", "过敏")),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _msg_text(message: Any) -> str:
    return str(message.get("text") if isinstance(message, dict) else getattr(message, "text", ""))


def _msg_id(message: Any) -> str:
    value = message.get("id") if isinstance(message, dict) else getattr(message, "id", "")
    return str(value)


def _msg_attr(message: Any, name: str) -> str | None:
    value = message.get(name) if isinstance(message, dict) else getattr(message, name, None)
    return str(value) if value else None


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"([。！？!？;；\n])", text)
    sentences: list[str] = []
    current = ""
    for part in parts:
        current += part
        if part in "。！？!？;；\n":
            cleaned = current.strip()
            if cleaned:
                sentences.append(cleaned)
            current = ""
    if current.strip():
        sentences.append(current.strip())
    return sentences


def _anchors_for(text: str) -> set[str]:
    return {key for key, tokens in ANCHOR_RULES if any(token in text for token in tokens)}


def _anchor_tokens_for(text: str) -> list[str]:
    hits = [token for _, tokens in ANCHOR_RULES for token in tokens if token in text]
    return sorted(dict.fromkeys(hits))


def _topic_label(text: str) -> str:
    anchors = _anchors_for(text)
    if not anchors:
        return "其他"
    for key, _ in ANCHOR_RULES:
        if key in anchors:
            return key
    return "其他"


def _extract_condition(text: str) -> str | None:
    matches = [
        match
        for match in CONDITION_HINT.finditer(text)
        if text[max(0, match.start() - 4) : match.start()] not in ("不", "未持续")
    ]
    if not matches:
        return None
    match = matches[0]
    tail = text[match.end() :]
    tail = re.split(r"[，。；;]", tail, maxsplit=1)[0].strip()
    return f"{match.group(0)}{tail}".strip()


def _extract_time(text: str) -> str | None:
    for phrase, label in OPINION_TIME_TERMS:
        if phrase in text:
            return label
    return None


def _classify_statement(text: str) -> tuple[str, str, str, str | None]:
    if QUESTION_HINT.search(text) or text.endswith(("？", "?")):
        claim_type = "clarifying_question"
    elif ADVICE_HINT.search(text):
        claim_type = "recommendation"
    else:
        claim_type = "general_knowledge"

    polarity = "negative" if NEGATED_ACTION.search(text) else "positive"
    condition = _extract_condition(text)
    if condition:
        certainty = "conditional"
    elif HEDGE_HINT.search(text):
        certainty = "hedged"
    else:
        certainty = "asserted"
    return claim_type, polarity, certainty, condition


def _evidence(message: Any, snippet: str, kind: str) -> DoctorOpinionEvidenceRef:
    text = _msg_text(message)
    message_id = _msg_id(message)
    if snippet not in text:
        raise ValueError(f"evidence snippet is not a substring of message {message_id}")
    return DoctorOpinionEvidenceRef(
        message_id=message_id,
        role="doctor",
        snippet=snippet,
        kind=kind,
    )


def _evidence_kind(claim_type: str) -> str:
    if claim_type == "clarifying_question":
        return "doctor_question"
    return "doctor_opinion"


def _make_opinion(
    message: Any,
    sentence: str,
    prior_ids: list[str],
    draft_text: str,
) -> DoctorOpinionView:
    claim_type, polarity, certainty, condition = _classify_statement(sentence)
    kind = _evidence_kind(claim_type)
    if condition:
        kind = "doctor_condition"
    evidence = _evidence(message, sentence, kind)
    draft_relation, draft_explanation = _draft_relation(
        claim_type=claim_type,
        polarity=polarity,
        condition=condition,
        sentence=sentence,
        draft_text=draft_text,
    )
    return DoctorOpinionView(
        id=uuid.uuid4().hex,
        source_message_id=_msg_id(message),
        speaker_name=_msg_attr(message, "speaker_name"),
        speaker_title=_msg_attr(message, "speaker_title"),
        hospital=_msg_attr(message, "hospital"),
        topic=_topic_label(sentence),
        claim_type=claim_type,
        statement=sentence,
        polarity=polarity,
        certainty=certainty,
        condition=condition,
        time_range=_extract_time(sentence),
        draft_relation=draft_relation,
        draft_explanation=draft_explanation,
        evidence_refs=[evidence],
        checked_message_ids=list(prior_ids),
    )


def _draft_polarity(draft_text: str, tokens: list[str]) -> str | None:
    positions = [(draft_text.find(token), token) for token in tokens if token in draft_text]
    if not positions:
        return None
    start, token = min(positions)
    window = draft_text[max(0, start - 12) : start + len(token) + 6]
    return "negative" if NEGATION_NEAR.search(window) else "positive"


def _draft_relation(
    claim_type: str,
    polarity: str,
    condition: str | None,
    sentence: str,
    draft_text: str,
) -> tuple[str, str]:
    anchor_tokens = _anchor_tokens_for(sentence)
    if claim_type == "clarifying_question":
        answered = any(token in draft_text for token in anchor_tokens)
        if answered:
            return (
                "answered_by_draft",
                "草稿已覆盖该前序提问所关注的信息或处理方向。",
            )
        return (
            "unanswered_question",
            "草稿未体现对该前序提问的回应，需确认是否为刻意省略或遗漏。",
        )

    draft_polarity = _draft_polarity(draft_text, anchor_tokens)
    if draft_polarity is not None and draft_polarity != polarity:
        if condition:
            return (
                "diverging",
                f"前序观点为{'肯定' if polarity == 'positive' else '否定/限制'}表述，草稿方向相反；适用条件“{condition}”需要一并核对。",
            )
        return (
            "diverging",
            "前序观点与草稿在相同信息点方向相反，需说明分歧来源。",
        )

    if condition and not CONDITION_HINT.search(draft_text):
        return (
            "conditional_only",
            f"前序观点以“{condition}”为适用条件，草稿需要说明该条件是否已被满足。",
        )

    if draft_polarity is None:
        return (
            "complementing",
            "草稿包含前序观点未覆盖的内容，需核对新增方向是否有患者材料支持。",
        )
    return (
        "reinforcing",
        "草稿与前序观点在相同信息点上方向一致。",
    )


def extract_doctor_opinions(
    scope: ReplayScope,
    draft_text: str,
) -> tuple[list[DoctorOpinionView], list[PeerOpinionRelationView]]:
    prior_ids = list(scope.prior_answer_message_ids)
    messages_by_id = {_msg_id(message): message for message in scope.visible_messages}
    opinions: list[DoctorOpinionView] = []
    for message_id in prior_ids:
        message = messages_by_id.get(message_id)
        if message is None:
            continue
        text = _msg_text(message)
        for sentence in _split_sentences(text):
            cleaned = sentence.strip("，,。；;？? ")
            if not cleaned:
                continue
            opinions.append(_make_opinion(message, sentence, prior_ids, draft_text))
    peer_relations = _build_peer_relations(opinions)
    return opinions, peer_relations


def _build_peer_relations(
    opinions: list[DoctorOpinionView],
) -> list[PeerOpinionRelationView]:
    relations: list[PeerOpinionRelationView] = []
    for index, left in enumerate(opinions):
        for right in opinions[index + 1 :]:
            if left.source_message_id == right.source_message_id:
                continue
            shared = _anchors_for(left.statement) & _anchors_for(right.statement)
            if not shared:
                continue
            anchor_label = sorted(shared)[0]
            relation_type = "repeat"
            divergence_kind: str | None = None
            conditions: str | None = None
            if left.polarity != right.polarity:
                relation_type = "divergence"
                divergence_kind = "polarity"
                explanation = (
                    "两位前序医生在相同信息点方向相反，需结合患者材料核对分歧来源。"
                )
            elif left.condition != right.condition:
                relation_type = "conditional_diff"
                divergence_kind = "condition"
                parts = []
                if left.condition:
                    parts.append(f"{left.condition}；适用回答：{left.statement}")
                if right.condition:
                    parts.append(f"{right.condition}；适用回答：{right.statement}")
                conditions = "；".join(parts)
                explanation = "两位前序医生的建议适用条件不同，草稿需说明当前条件归属。"
            elif left.claim_type != right.claim_type:
                relation_type = "supplement"
                divergence_kind = "scope"
                explanation = (
                    "两位前序医生从不同角度覆盖同一信息点，观点之间为互补关系。"
                )
            else:
                divergence_kind = None
                explanation = (
                    f"两位前序医生就“{anchor_label}”给出方向一致的重复观点，重复不等于无价值。"
                )

            evidence: list[DoctorOpinionEvidenceRef] = []
            if left.evidence_refs:
                evidence.append(left.evidence_refs[0])
            if right.evidence_refs:
                evidence.append(right.evidence_refs[0])
            relations.append(
                PeerOpinionRelationView(
                    id=uuid.uuid4().hex,
                    source_claim_id=left.id,
                    target_claim_id=right.id,
                    source_message_id=left.source_message_id,
                    target_message_id=right.source_message_id,
                    relation_type=relation_type,
                    divergence_kind=divergence_kind,
                    explanation=explanation,
                    applicability_conditions=conditions,
                    evidence_refs=evidence,
                )
            )
    return relations


def run_doctor_opinion_analysis(
    db: Session,
    case: Any,
    focus_message_id: str,
    review_context: str = "replay",
) -> DoctorOpinionAnalysisResponse:
    version = db.get(CaseVersion, case.current_version_id)
    if version is None:
        raise ValueError("case has no current version")
    scope = compute_replay_scope(
        case.id, version.messages_json, focus_message_id, review_context
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
    input_chars = sum(len(_msg_text(message)) for message in scope.visible_messages)
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
        model_name=settings.llm_model,
        prompt_versions={
            "doctor_opinions": settings.prompt_version_prior_claims,
            "claim_relations": settings.prompt_version_claim_relations,
        },
        rules_version=settings.rules_version,
        source_mode="stub_rules",
        status="running",
        stage="doctor_opinions",
        started_at=_now(),
    )
    db.add(run)
    db.flush()

    opinions, peer_relations = extract_doctor_opinions(scope, draft_text)
    claims: list[Claim] = []
    for opinion in opinions:
        speaker_id = next(
            (
                source.get("speaker_id")
                for source in version.messages_json
                if source.get("id") == opinion.source_message_id
            ),
            None,
        )
        claims.append(
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
    db.add_all(claims)
    db.flush()

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

    run.status = "succeeded"
    run.finished_at = _now()
    db.commit()

    return DoctorOpinionAnalysisResponse(
        run_id=run_id,
        case_id=case.id,
        focus_message_id=focus_message_id,
        cutoff_at=scope.cutoff_at,
        review_context=scope.review_context,
        order_quality=scope.order_quality,
        source_mode="stub_rules",
        model_name=settings.llm_model,
        rules_version=settings.rules_version,
        opinions=opinions,
        peer_relations=peer_relations,
    )
