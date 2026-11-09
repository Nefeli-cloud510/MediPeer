from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

import httpx

from ..core.config import Settings
from ..schemas.scope import ReplayScope
from .sentence_utils import split_draft_sentences as _split_draft_sentences


_CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_TOKEN = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:℃|度|天|小时|次)?|[A-Za-z]{2,}")
_STOPWORDS = {
    "患者",
    "目前",
    "医生",
    "建议",
    "注意",
    "情况",
    "需要",
    "可以",
    "如果",
    "已经",
    "没有",
    "一个",
    "一些",
    "什么",
    "是否",
    "怎样",
    "如何",
    "可能",
    "考虑",
    "这位",
    "但是",
    "因为",
    "所以",
    "的时候",
    "情况",
    "相关",
    "进行",
    "治疗",
    "用药",
    "检查",
    "处理",
    "这个",
    "这些",
    "那些",
    "其中",
    "以及",
    "并且",
    "或者",
    "同时",
}
_SENTENCE_SPLIT = re.compile(r"([。！？!?；;])")


def _keywords(text: str) -> list[str]:
    """Extract conservative word/shingle keys for the stub direct baseline."""
    keys: set[str] = set()
    for match in _ASCII_TOKEN.finditer(text):
        keys.add(match.group(0))
    for run in _CHINESE_RUN.findall(text):
        length = len(run)
        if length <= 2:
            keys.add(run)
        elif length <= 4:
            keys.add(run)
        else:
            for start in range(length - 3):
                keys.add(run[start : start + 4])
            keys.add(run[:2])
            keys.add(run[-2:])
    return sorted(key for key in keys if key not in _STOPWORDS and len(key) >= 2)


def _snippet_for(text: str, keyword: str) -> str:
    parts = _SENTENCE_SPLIT.split(text)
    current = ""
    for part in parts:
        current += part
        if keyword in current:
            return current.strip()
        if part in "。！？!?；;":
            current = ""
    if keyword in current:
        return current.strip()
    start = max(0, text.find(keyword))
    return text[start : start + 80].strip() or text[:80].strip()


def _numbered_draft_entries(draft_text: str) -> str:
    sentences = _split_draft_sentences(draft_text)
    if not sentences:
        return "（空草稿）"
    return "\n".join(f"{sentence.id}|{sentence.text}" for sentence in sentences)


def build_baseline_prompt(scope: ReplayScope, draft_text: str) -> str:
    if scope.review_context == "current":
        material_label = "当前审阅可见的患者材料（严格前序患者消息＋人工补录）"
        scope_note = (
            "当前模式只把人工保存的患者补录视为可见材料；"
            "原平台晚于焦点的患者消息仍不得使用。"
        )
    else:
        material_label = "焦点回答发出前可见的患者材料"
        scope_note = "不得使用焦点回答本身、同时刻回答、后续回答或未来的患者补充。"
    patient_block = "\n".join(
        f"[患者消息 {msg.id}{('，' + msg.created_at) if msg.created_at else ''}]\n{msg.text}"
        for msg in scope.visible_messages
        if msg.role == "patient"
    )
    prior_block = "\n".join(
        f"[前序医生消息 {msg.id}{('，' + msg.created_at) if msg.created_at else ''}]\n{msg.text}"
        for msg in scope.visible_messages
        if msg.role == "doctor"
    )
    system = (
        "你是 MediPeer 的医生回复审阅助手。当前任务是直接审阅待发草稿，"
        "不提前运行患者事实抽取或同行观点结构化模块。"
        f"只使用下列可见的原始消息；{scope_note}"
    )
    user = (
        "草稿已按句子切分为编号条目，每个编号恰好对应一条 finding："
        "不得把多个条目合并进一条，也不得遗漏或新增未提供的编号。"
        "draft_sentence_id 必须使用下列编号，"
        "draft_sentence_text 必须与对应条目逐字一致。\n"
        "请审阅草稿，输出 JSON，不要输出其他文本。\n"
        'JSON 结构：{"findings":[{"draft_sentence_id":"ds-0",'
        '"contribution":"new_claim|repeats_prior|refines_prior|condition_only|'
        'unsupported_claim|answers_prior_question|unknown",'
        '"patient_grounding":"patient_stated|patient_denied|patient_uncertain|'
        'doctor_question_only|unmentioned|professional_recommendation|unknown",'
        '"explanation":"中文判断理由",'
        '"evidence_refs":[{"message_id":"可见消息ID","role":"patient|doctor",'
        '"snippet":"必须逐字来自该消息原文","kind":"direct_support"}],'
        '"suggested_action":"keep_and_confirm|defer|revise_fact_statement|'
        'clarify_or_revise|clarify_condition|view_both_sources"}]}\n'
        "suggested_action 必须从上述枚举中选择一个值，不得省略、留空或用其他文案。\n"
        "注意：患者未提及不等于明确否认；未提供原文证据时 evidence_refs 为空数组，"
        "不要伪造引用；患者消息必须先于医生消息出现在上下文。\n"
        "doctor_question_only 表示草稿句仅对应前序医生提问、尚无患者作答；"
        "若该句超出可逐字对应的医生原文，evidence_refs 可为空，"
        "但不得把医生提问或患者未答写成患者的已确认事实。\n"
        f"【{material_label}】\n{patient_block}\n"
        f"【焦点回答发出前可见的前序医生回答】\n{prior_block}\n"
        f"【待审草稿（程序切分后的编号条目）】\n{_numbered_draft_entries(draft_text)}"
    )
    return system + "\n\n" + user


def stub_baseline_review(
    scope: ReplayScope,
    draft_text: str,
    run_id: str,
) -> dict[str, Any]:
    """Deterministic placeholder for the direct baseline while no LLM is configured.

    This is explicitly not a real model: it performs simple direct word shingle
    matching over the same visible raw messages and never calls the structured
    patient-facts or doctor-opinions modules.
    """
    visible_by_id = {msg.id: msg for msg in scope.visible_messages}
    keyword_to_messages: dict[str, list[str]] = {}
    for msg in scope.visible_messages:
        for keyword in _keywords(msg.text):
            keyword_to_messages.setdefault(keyword, []).append(msg.id)

    total = sum(len(msg.text) for msg in scope.visible_messages)
    checked_ids = [
        msg.id for msg in scope.visible_messages if msg.role == "patient"
    ] + [
        msg.id for msg in scope.visible_messages if msg.role == "doctor"
    ]
    findings: list[dict[str, Any]] = []
    sentences = _split_draft_sentences(draft_text)
    for sentence in sentences:
        sentence_keys = _keywords(sentence.text)
        patient_ids: list[str] = []
        doctor_ids: list[str] = []
        if not sentence_keys:
            grounding = "needs_review"
            contribution = "unknown"
        else:
            matched_ids = sorted(
                {
                    message_id
                    for key in sentence_keys
                    for message_id in keyword_to_messages.get(key, [])
                }
            )
            patient_ids = [
                message_id
                for message_id in matched_ids
                if visible_by_id[message_id].role == "patient"
            ]
            doctor_ids = [
                message_id
                for message_id in matched_ids
                if visible_by_id[message_id].role == "doctor"
            ]
            if patient_ids:
                grounding = "patient_stated"
            elif doctor_ids:
                grounding = "doctor_question_only"
            else:
                grounding = "unmentioned"
            contribution = "repeats_prior" if doctor_ids else "new_claim"

        evidence_refs: list[dict[str, str]] = []
        for message_id in checked_ids[:6]:
            if not sentence_keys:
                break
            msg = visible_by_id[message_id]
            matched_key = next(
                (key for key in sentence_keys if key in msg.text), None
            )
            if matched_key is None:
                continue
            evidence_refs.append(
                {
                    "message_id": message_id,
                    "role": msg.role,
                    "snippet": _snippet_for(msg.text, matched_key),
                    "kind": "direct_support",
                }
            )
        if grounding == "unmentioned":
            explanation = (
                "直接基线：焦点前可见材料中未找到与草稿句匹配的原文，"
                "不能视为患者已陈述或明确否认。"
            )
            suggested_action = "clarify_or_revise"
        elif grounding == "doctor_question_only":
            explanation = (
                "直接基线：只有前序医生的提问或陈述与该句有词面重合，"
                "不能据此断定患者已确认。"
            )
            suggested_action = "clarify_or_revise"
        elif grounding == "professional_recommendation":
            explanation = "直接基线：该句更接近医学建议，患者依据不适用。"
            suggested_action = "defer"
        elif grounding == "needs_review":
            explanation = "直接基线：草稿句过短或没有可匹配原文的实质信息，需人工核查。"
            suggested_action = "clarify_or_revise"
        else:
            explanation = (
                "直接基线：该句在焦点前可见的患者材料或前序医生回答中"
                "找到直接词面重合，未运行结构化模块。"
            )
            suggested_action = "keep_and_confirm"

        signature_payload = (
            sentence.text
            + "|"
            + contribution
            + "|"
            + grounding
            + "|"
            + "|".join(ref["snippet"] for ref in evidence_refs)
        )
        findings.append(
            {
                "id": hashlib.sha256(
                    f"baseline:{run_id}:{sentence.id}:{signature_payload}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:32],
                "draft_sentence_id": sentence.id,
                "draft_sentence_text": sentence.text,
                "contribution": contribution,
                "patient_grounding": grounding,
                "explanation": explanation,
                "evidence_refs": evidence_refs,
                "related_fact_ids": [],
                "related_claim_ids": [],
                "checked_message_ids": checked_ids,
                "suggested_action": suggested_action,
                "review_status": "pending",
                "signature": hashlib.sha256(
                    signature_payload.encode("utf-8")
                ).hexdigest(),
                "baseline_scope_chars": total,
                "structured_modules_used": False,
            }
        )
    return {
        "sentences": [sentence.model_dump() for sentence in sentences],
        "findings": findings,
        "structured_modules_used": False,
        "visible_message_ids": [msg.id for msg in scope.visible_messages],
    }


def _parse_model_json(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def complete_baseline(
    scope: ReplayScope,
    draft_text: str,
    settings: Settings,
    run_id: str,
) -> dict[str, Any]:
    prompt = build_baseline_prompt(scope, draft_text)
    started = time.monotonic()
    provider = settings.resolved_llm_provider
    model = settings.resolved_llm_model
    base_url = settings.resolved_llm_base_url
    api_key = settings.resolved_llm_api_key
    if provider == "stub":
        content = stub_baseline_review(scope, draft_text, run_id)
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "status": "ok",
            "content": content,
            "output": json.dumps(content, ensure_ascii=False),
            "error_class": None,
            "error": None,
            "latency_ms": latency_ms,
            "request_prompt": prompt,
        }

    if provider != "openai_compatible":
        return {
            "status": "failed",
            "content": None,
            "output": None,
            "error_class": "configuration_error",
            "error": f"unsupported llm_provider: {provider}",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_prompt": prompt,
        }
    if not base_url or not api_key:
        return {
            "status": "failed",
            "content": None,
            "output": None,
            "error_class": "configuration_error",
            "error": "openai_compatible provider requires base_url and api_key",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_prompt": prompt,
        }

    system_prompt, user_prompt = prompt.split("\n\n", 1)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "response_format": {"type": "json_object"},
    }
    for key in ("temperature", "max_tokens"):
        if payload.get(key) is None:
            payload.pop(key)
    try:
        response = httpx.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        content = _parse_model_json(raw)
    except (httpx.HTTPError, TimeoutError, KeyError, IndexError, ValueError) as exc:
        return {
            "status": "failed",
            "content": None,
            "output": None,
            "error_class": exc.__class__.__name__,
            "error": str(exc),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_prompt": prompt,
        }
    return {
        "status": "ok",
        "content": content,
        "output": raw,
        "error_class": None,
        "error": None,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "request_prompt": prompt,
    }


def _fact_block(fact: Any) -> str:
    refs = getattr(fact, "evidence_refs", []) or []
    snippets = "；".join(
        ref.snippet
        if isinstance(ref, dict)
        else getattr(ref, "snippet", "")
        for ref in refs
    )
    condition = getattr(fact, "condition", None) or ""
    time_range = getattr(fact, "time_range", None) or ""
    return (
        f"[{getattr(fact, 'status', 'unknown')}|{getattr(fact, 'dimension', '')}"
        f"|id={getattr(fact, 'id', '')}] "
        f"{getattr(fact, 'statement', '')}"
        f"{('，时间=' + time_range) if time_range else ''}"
        f"{('，条件=' + condition) if condition else ''}"
        f"{('；证据=' + snippets) if snippets else ''}"
    )


def _opinion_block(opinion: Any) -> str:
    refs = getattr(opinion, "evidence_refs", []) or []
    snippets = "；".join(
        ref.snippet
        if isinstance(ref, dict)
        else getattr(ref, "snippet", "")
        for ref in refs
    )
    condition = getattr(opinion, "condition", None) or ""
    return (
        f"[{getattr(opinion, 'source_message_id', '')}|"
        f"{getattr(opinion, 'speaker_name', '') or '医生'}|"
        f"{getattr(opinion, 'topic', '')}|"
        f"{getattr(opinion, 'claim_type', '')}|"
        f"id={getattr(opinion, 'id', '')}] "
        f"{getattr(opinion, 'statement', '')}"
        f"{('，适用条件=' + condition) if condition else ''}"
        f"{('；证据=' + snippets) if snippets else ''}"
    )


def build_joint_review_prompt(
    scope: ReplayScope,
    draft_text: str,
    facts: list[Any],
    opinions: list[Any],
) -> str:
    """Build the real-model joint review prompt.

    The prompt includes raw visible messages plus program-derived patient
    facts and prior doctor opinions. Everything comes from the strict scope
    computed for the current focus answer.
    """
    if scope.review_context == "current":
        raw_label = "当前审阅可见的原始消息（严格前序患者消息＋人工补录＋严格前序医生回答）"
        scope_note = (
            "当前模式只把人工保存的患者补录视为可见；"
            "原平台晚于焦点的患者消息、焦点回答本身、同时刻回答和后续医生回答仍不得使用。"
        )
    else:
        raw_label = "焦点回答发出前可见的原始消息"
        scope_note = (
            "不得使用焦点回答本身、同时刻回答、后续回答或未来的患者补充。"
        )
    raw_blocks: list[str] = []
    for message in scope.visible_messages:
        raw_blocks.append(
            f"[{message.role} {message.id}{('，' + message.created_at) if message.created_at else ''}]\n"
            f"{message.text}"
        )
    raw_block = "\n".join(raw_blocks) if raw_blocks else "（无可见消息）"
    facts_block = "\n".join(_fact_block(fact) for fact in facts) or "（无患者事实）"
    opinions_block = (
        "\n".join(_opinion_block(opinion) for opinion in opinions)
        or "（无前序医生观点）"
    )
    system = (
        "你是 MediPeer 的医生回复审阅助手。当前任务是对草稿逐主张联合审阅："
        "一方面判断草稿相对严格前序医生的同行回答新增或回应了什么，"
        "另一方面判断草稿中的患者事实是否有焦点前患者材料的原文依据。"
        "下列患者事实与医生观点已由程序按当前审阅范围生成，引用片段逐字来自原文；"
        f"你只能使用它们和可见原始消息；{scope_note}患者未提及不等于明确否认；"
        "医生提问或推测不能转成患者事实；历史时间不能自动变成当前事实；"
        "观点新增不等于正确，重复不等于无价值。"
    )
    user = (
        "草稿已按句子切分为编号条目，每个编号恰好对应一条 finding："
        "不得把多个条目合并进一条，也不得遗漏或新增未提供的编号。"
        "draft_sentence_id 必须使用下列编号，"
        "draft_sentence_text 必须与对应条目逐字一致。\n"
        "请对草稿输出 JSON，只输出 JSON，不要输出其他文本。\n"
        'JSON 结构：{"findings":[{"draft_sentence_id":"ds-0",'
        '"draft_sentence_text":"与输入草稿句逐字一致",'
        '"contribution":"new_claim|repeats_prior|refines_prior|'
        'reinforces_prior|condition_only|unsupported_claim|'
        'answers_prior_question|unanswered_question|diverges_from_prior|'
        'mixed_prior_views|unknown",'
        '"patient_grounding":"patient_stated|patient_denied|'
        'patient_uncertain|doctor_question_only|unmentioned|'
        'detail_unverified|mixed_temporal|professional_recommendation|'
        'needs_review|unknown",'
        '"explanation":"中文判断理由，说明新增/重复、来源和分歧来源",'
        '"evidence_refs":[{"message_id":"可见消息ID","role":"patient|doctor",'
        '"snippet":"必须逐字来自该消息原文","kind":"direct_support"}],'
        '"related_claim_ids":["上面前序观点中的id，必须来自列表"],'
        '"related_fact_ids":["上面前序患者事实列表 id= 后的值，'
        '必须来自列表，不得使用状态|主题占位"],'
        '"suggested_action":"keep_and_confirm|defer|'
        'revise_fact_statement|clarify_or_revise|clarify_condition|'
        'view_both_sources"}]}\n'
        "注意：patient_stated/patient_denied/patient_uncertain 必须有至少一条"
        "逐字患者证据引用；doctor_question_only 表示仅对应前序医生提问、"
        "尚无患者作答，若草稿句超出可逐字对应的医生原文，evidence_refs 可为空，"
        "但不得把医生提问或患者未答写成患者的已确认事实；"
        "找不到患者依据时 evidence_refs 为空数组。"
        "structured_modules_used 必须为 false，因为结构化模块已由程序在外部运行。"
        f"\n【{raw_label}】\n{raw_block}\n"
        f"【程序整理的患者事实（焦点前）】\n{facts_block}\n"
        f"【程序整理的严格前序医生观点】\n{opinions_block}\n"
        f"【待审草稿（程序切分后的编号条目）】\n{_numbered_draft_entries(draft_text)}"
    )
    return system + "\n\n" + user


def complete_joint_review(
    scope: ReplayScope,
    draft_text: str,
    facts: list[Any],
    opinions: list[Any],
    settings: Settings,
    run_id: str,
) -> dict[str, Any]:
    """Call the configured real model for the joint review step.

    The stub path is intentionally not handled here: deterministic joint review
    lives in joint_review_service so the placeholder is never mistaken for a
    model call.
    """
    prompt = build_joint_review_prompt(scope, draft_text, facts, opinions)
    started = time.monotonic()
    provider = settings.resolved_llm_provider
    model = settings.resolved_llm_model
    base_url = settings.resolved_llm_base_url
    api_key = settings.resolved_llm_api_key

    if provider == "stub":
        return {
            "status": "failed",
            "content": None,
            "output": None,
            "error_class": "stub_not_supported",
            "error": "complete_joint_review must not be called in stub mode",
            "latency_ms": 0,
            "request_prompt": prompt,
        }
    if provider != "openai_compatible":
        return {
            "status": "failed",
            "content": None,
            "output": None,
            "error_class": "configuration_error",
            "error": f"unsupported llm_provider: {provider}",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_prompt": prompt,
        }
    if not base_url or not api_key:
        return {
            "status": "failed",
            "content": None,
            "output": None,
            "error_class": "configuration_error",
            "error": "openai_compatible provider requires base_url and api_key",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_prompt": prompt,
        }

    system_prompt, user_prompt = prompt.split("\n\n", 1)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "response_format": {"type": "json_object"},
    }
    for key in ("temperature", "max_tokens"):
        if payload.get(key) is None:
            payload.pop(key)
    try:
        response = httpx.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        content = _parse_model_json(raw)
    except (httpx.HTTPError, TimeoutError, KeyError, IndexError, ValueError) as exc:
        return {
            "status": "failed",
            "content": None,
            "output": None,
            "error_class": exc.__class__.__name__,
            "error": str(exc),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_prompt": prompt,
        }
    return {
        "status": "ok",
        "content": content,
        "output": raw,
        "error_class": None,
        "error": None,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "request_prompt": prompt,
    }
def generation_config(settings: Settings) -> dict[str, Any]:
    return {
        "provider": settings.resolved_llm_provider,
        "timeout_seconds": settings.llm_timeout_seconds,
        "max_retries": settings.llm_max_retries,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "model": settings.resolved_llm_model,
    }
