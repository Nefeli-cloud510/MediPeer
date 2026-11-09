from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.models import AnalysisRun, CaseVersion, Draft, DraftVersion, PatientFact
from ..schemas.patient_facts import (
    PatientFactAnalysisResponse,
    PatientFactEvidenceRef,
    PatientFactView,
)
from ..schemas.scope import ReplayScope
from .scope_service import compute_replay_scope


TOPIC_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("症状特征", "发热", ("发热", "发烧")),
    ("症状特征", "咳嗽", ("咳嗽",)),
    ("症状特征", "咳痰", ("咳痰", "白痰", "黄痰", "痰")),
    ("症状特征", "鼻塞", ("鼻塞", "鼻堵")),
    ("症状特征", "流涕", ("流涕", "流鼻涕")),
    ("症状特征", "咽痛", ("咽痛", "咽喉痛", "嗓子疼", "喉咙痛")),
    ("症状特征", "胸痛", ("胸痛", "胸口痛")),
    ("症状特征", "胸闷", ("胸闷", "胸口闷")),
    ("症状特征", "喘息", ("喘息",)),
    ("症状特征", "气短", ("气促", "气短", "呼吸困难")),
    ("症状特征", "头晕", ("头晕", "头昏")),
    ("信息连续性", "哮喘", ("哮喘",)),
    ("信息连续性", "基础病", ("基础病",)),
    ("信息连续性", "过敏史", ("过敏史", "过敏")),
]

NEGATION_PATTERN = re.compile(r"(?:从未|从来没有过|从来没有|没有过|未曾|没有|无|未|不伴有|否认)")
UNCERTAIN_WORDS = re.compile(r"(?:觉得|感觉|好像|可能|疑似|像|大约)")
TIME_TERMS = [
    ("今天早上", "今天早上"),
    ("昨晚", "昨晚"),
    ("今天", "今天"),
    ("最近三天", "最近三天"),
    ("最近几天", "最近几天"),
    ("最近", "最近"),
    ("前天", "前天"),
    ("昨天", "昨天"),
    ("目前", "目前"),
    ("现在", "现在"),
    ("平时", "平时"),
    ("之前", "之前"),
    ("以前", "以前"),
    ("既往", "既往"),
]
QUESTION_PATTERN = re.compile(r"(?:是否|有没有|有无|会不会|吗)")
RELATIONSHIP_DIMENSIONS = {"哮喘", "基础病", "过敏史"}
DOCTOR_QUESTION_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("发热", ("体温", "量体温", "测体温")),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _msg_text(message: Any) -> str:
    return str(message.get("text") if isinstance(message, dict) else getattr(message, "text", ""))


def _msg_id(message: Any) -> str:
    value = message.get("id") if isinstance(message, dict) else getattr(message, "id", "")
    return str(value)


def _msg_role(message: Any) -> str:
    value = (
        message.get("role")
        if isinstance(message, dict)
        else getattr(message, "role", "patient")
    )
    return str(value or "patient")


def _extract_time(text: str, index: int) -> str | None:
    window = text[max(0, index - 14) : index]
    best: tuple[int, str] | None = None
    for phrase, label in TIME_TERMS:
        pos = window.rfind(phrase)
        if pos >= 0 and (best is None or pos > best[0]):
            best = (pos, label)
    return best[1] if best else None


def _snippet(text: str, start: int, end: int, padding: int = 10) -> str:
    return text[max(0, start - padding) : min(len(text), end + padding)]


def _is_negated(text: str, token_start: int) -> bool:
    left = text[max(0, token_start - 8) : token_start]
    return bool(NEGATION_PATTERN.search(left))


def _mentions_for_topic(text: str, token: str) -> list[tuple[int, int]]:
    return [
        (match.start(), match.end())
        for match in re.finditer(re.escape(token), text)
        if not _is_negated(text, match.start())
    ]


def _evidence(
    message: Any,
    snippet: str,
    kind: str = "patient_statement",
) -> PatientFactEvidenceRef:
    message_id = _msg_id(message)
    text = _msg_text(message)
    if snippet not in text:
        raise ValueError(f"evidence snippet is not a substring of message {message_id}")
    return PatientFactEvidenceRef(
        message_id=message_id,
        role=_msg_role(message),
        snippet=snippet,
        kind=kind,
    )


def _make_fact(
    fact_id: str,
    dimension: str,
    statement: str,
    status: str,
    time_range: str | None,
    condition: str | None,
    evidence_refs: list[PatientFactEvidenceRef],
    checked_message_ids: list[str],
) -> PatientFactView:
    return PatientFactView(
        id=fact_id,
        dimension=dimension,
        statement=statement,
        status=status,
        time_range=time_range,
        condition=condition,
        evidence_refs=evidence_refs,
        checked_message_ids=checked_message_ids,
    )


def _patient_stated_facts(
    patient_messages: list[Any],
    patient_ids: list[str],
) -> tuple[list[PatientFactView], set[str], set[str]]:
    facts: list[PatientFactView] = []
    stated_topics: set[str] = set()
    uncertain_topics: set[str] = set()
    seen: set[tuple[str, str, str | None, str | None, str]] = set()

    for message in patient_messages:
        text = _msg_text(message)
        message_id = _msg_id(message)
        for dimension, canonical, tokens in TOPIC_RULES:
            for token in tokens:
                if token not in text:
                    continue
                for match in re.finditer(re.escape(token), text):
                    if not _is_negated(text, match.start()):
                        continue
                    snippet = _snippet(text, match.start(), match.end())
                    time_range = _extract_time(text, match.start()) or "未标注"
                    fact = _make_fact(
                        uuid.uuid4().hex,
                        dimension,
                        f"患者明确否认{canonical}",
                        "denied",
                        time_range,
                        None,
                        [_evidence(message, snippet)],
                        [message_id],
                    )
                    stated_topics.add(canonical)
                    facts.append(fact)
                for start, end in _mentions_for_topic(text, token):
                    snippet = _snippet(text, start, end)
                    time_range = _extract_time(text, start) or "未标注"
                    uncertain = bool(UNCERTAIN_WORDS.search(text[max(0, start - 12) : end + 12]))
                    has_unmeasured = bool(
                        re.search(r"(?:没量过体温|未量体温|没测过体温|未测体温|没有量体温)", text)
                    )
                    if token in ("白痰", "黄痰"):
                        statement = f"患者陈述有{token}"
                    elif canonical == "发热" and uncertain:
                        statement = "患者自觉发热"
                    else:
                        statement = f"患者陈述{canonical}"
                    condition = None
                    status = "uncertain" if uncertain else "confirmed"
                    if has_unmeasured and canonical == "发热":
                        condition = "未测量体温，客观体温未确认"
                        uncertain_topics.add(canonical)
                    elif uncertain:
                        condition = "患者表述带有不确定性"
                        uncertain_topics.add(canonical)
                    evidence = _evidence(message, snippet)
                    key = (
                        dimension,
                        statement,
                        status,
                        time_range,
                        condition or "",
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    stated_topics.add(canonical)
                    facts.append(
                        _make_fact(
                            uuid.uuid4().hex,
                            dimension,
                            statement,
                            status,
                            time_range,
                            condition,
                            [evidence],
                            [message_id],
                        )
                    )

        # 覆盖“昨天发热，今天没有”这类省略主语的相对否认表述。
        for match in re.finditer(r"(?:今天|现在|目前|这两天)\s*(?:没有|未再出现|不烧了|退了)", text):
            previous = text[: match.start()]
            last_topic: str | None = None
            for dim, canonical, tokens in TOPIC_RULES:
                if any(token in previous for token in tokens):
                    last_topic = canonical
            if last_topic is None:
                continue
            time_range = _extract_time(text, match.start() + len(match.group(0))) or "今天"
            snippet = _snippet(text, match.start(), match.end(), 4)
            facts.append(
                _make_fact(
                    uuid.uuid4().hex,
                    "症状特征",
                    f"患者今天没有再出现{last_topic}",
                    "denied",
                    time_range,
                    None,
                    [_evidence(message, snippet)],
                    [message_id],
                )
            )

    for message in patient_messages:
        text = _msg_text(message)
        message_id = _msg_id(message)
        base_disease = re.search(r"(?:平时|既往|以前)?(?:没有|无|未)(?:其他)?基础病", text)
        if base_disease:
            snippet = _snippet(text, base_disease.start(), base_disease.end(), 6)
            fact_id = uuid.uuid4().hex
            facts.append(
                _make_fact(
                    fact_id,
                    "信息连续性",
                    "患者陈述平时没有其他基础病",
                    "denied",
                    "平时",
                    None,
                    [_evidence(message, snippet)],
                    [message_id],
                )
            )
            stated_topics.add("基础病")

        unmeasured = re.search(
            r"(?:没量过体温|未量体温|没测过体温|未测体温|没有量体温|现在没量|现在没测)",
            text,
        )
        if unmeasured:
            snippet = _snippet(text, unmeasured.start(), unmeasured.end(), 6)
            time_range = _extract_time(text, unmeasured.start()) or "焦点前"
            facts.append(
                _make_fact(
                    uuid.uuid4().hex,
                    "临床时间信息",
                    "患者陈述未测量体温",
                    "confirmed",
                    time_range,
                    None,
                    [_evidence(message, snippet)],
                    [message_id],
                )
            )

    return facts, stated_topics, uncertain_topics


def _doctor_question_refs(
    doctor_messages: list[Any],
    patient_ids: list[str],
) -> dict[str, list[PatientFactEvidenceRef]]:
    asked: dict[str, list[PatientFactEvidenceRef]] = {}
    alias_sets = [
        (canonical, set(tokens))
        for canonical, tokens in DOCTOR_QUESTION_ALIASES
    ]
    for message in doctor_messages:
        text = _msg_text(message)
        for _, canonical, tokens in TOPIC_RULES:
            for token in tokens:
                if token not in text:
                    continue
                match = QUESTION_PATTERN.search(text)
                if not match:
                    continue
                for start, end in _mentions_for_topic(text, token):
                    window = text[max(0, start - 14) : end + 14]
                    if not QUESTION_PATTERN.search(window):
                        continue
                    ev = _evidence(
                        message,
                        _snippet(text, start, end),
                        "doctor_question_inference",
                    )
                    if ev not in asked.setdefault(canonical, []):
                        asked[canonical].append(ev)
        for canonical, tokens in alias_sets:
            if not any(token in text for token in tokens):
                continue
            match = QUESTION_PATTERN.search(text)
            if not match:
                continue
            for token in tokens:
                for start, end in _mentions_for_topic(text, token):
                    ev = _evidence(
                        message,
                        _snippet(text, start, end),
                        "doctor_question_inference",
                    )
                    if ev not in asked.setdefault(canonical, []):
                        asked[canonical].append(ev)
    return asked


def _draft_topics(draft_text: str) -> list[tuple[str, str, tuple[str, ...]]]:
    return [
        rule
        for rule in TOPIC_RULES
        if any(token in draft_text for token in rule[2])
    ]


def extract_patient_facts(
    scope: ReplayScope,
    draft_text: str,
) -> list[PatientFactView]:
    """Extract patient-side facts from the visible replay scope only."""
    patient_by_id = {
        _msg_id(m): m for m in scope.visible_messages
        if _msg_id(m) in set(scope.patient_message_ids)
    }
    patient_messages = [patient_by_id[message_id] for message_id in scope.patient_message_ids]
    doctor_messages = [
        message
        for message in scope.visible_messages
        if _msg_id(message) in set(scope.prior_answer_message_ids)
    ]

    facts, stated_topics, uncertain_topics = _patient_stated_facts(
        patient_messages, scope.patient_message_ids
    )
    asked_refs = _doctor_question_refs(doctor_messages, scope.patient_message_ids)

    for dimension, canonical, tokens in TOPIC_RULES:
        if canonical in stated_topics:
            if canonical in uncertain_topics and asked_refs.get(canonical):
                ev = asked_refs[canonical][0]
                fact_id = uuid.uuid4().hex
                facts.append(
                    _make_fact(
                        fact_id,
                        dimension,
                        f"前序医生已询问{canonical}，患者仅提供自觉或未确证表述",
                        "asked_unanswered",
                        "焦点前",
                        "客观确认待完成",
                        [ev],
                        list(scope.patient_message_ids) + [ev.message_id],
                    )
                )
            continue

        evs = asked_refs.get(canonical)
        if evs:
            ev = evs[0]
            facts.append(
                _make_fact(
                    uuid.uuid4().hex,
                    dimension,
                    f"前序医生已询问{canonical}，患者未在焦点前作答",
                    "asked_unanswered",
                    "焦点前",
                    None,
                    [ev],
                    list(scope.patient_message_ids) + [ev.message_id],
                )
            )
            continue

        if any(token in draft_text for token in tokens):
            statement = f"患者未在焦点前陈述{canonical}"
            if canonical in RELATIONSHIP_DIMENSIONS:
                statement += "，不能视为既往已确认或否认"
            facts.append(
                _make_fact(
                    uuid.uuid4().hex,
                    dimension,
                    statement,
                    "unknown",
                    "焦点前",
                    None,
                    [],
                    list(scope.patient_message_ids),
                )
            )

    # 草稿中出现的主题优先，缺失信息排在最前，便于审阅时定位。
    patient_sort_key = scope.patient_message_ids[0] if scope.patient_message_ids else ""
    facts.sort(
        key=lambda fact: (
            0 if fact.status == "unknown" else 1 if fact.status == "asked_unanswered" else 2,
            patient_sort_key,
            fact.dimension,
        )
    )
    return facts


def run_patient_fact_analysis(
    db: Session,
    case: Any,
    focus_message_id: str,
    review_context: str = "replay",
) -> PatientFactAnalysisResponse:
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
        prompt_versions={"patient_facts": settings.prompt_version_patient_facts},
        rules_version=settings.rules_version,
        source_mode="stub_rules",
        status="running",
        stage="patient_facts",
        started_at=_now(),
    )
    db.add(run)
    db.flush()

    facts = extract_patient_facts(scope, draft_text)
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

    run.status = "succeeded"
    run.finished_at = _now()
    db.commit()

    return PatientFactAnalysisResponse(
        run_id=run_id,
        case_id=case.id,
        focus_message_id=focus_message_id,
        cutoff_at=scope.cutoff_at,
        review_context=scope.review_context,
        order_quality=scope.order_quality,
        source_mode="stub_rules",
        model_name=settings.llm_model,
        rules_version=settings.rules_version,
        facts=facts,
    )
