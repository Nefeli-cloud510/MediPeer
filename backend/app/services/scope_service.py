from __future__ import annotations

from datetime import datetime
from typing import Any

from ..schemas.case import MessageView
from ..schemas.scope import ReplayScope


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_replay_scope(
    case_id: str,
    messages: list[dict[str, Any]],
    focus_message_id: str,
    review_context: str = "replay",
) -> ReplayScope:
    """Build the strict context visible when the focus answer was written.

    Rules enforced here: the focus itself is never part of its own context;
    same-timestamp messages are not strict predecessors; later patient
    supplements and later answers are excluded; unparseable times are
    excluded with a review flag instead of being guessed.

    review_context="current" additionally includes manually saved patient
    supplements from the current review session even when they arrived after
    the focus, while doctor answers and original platform patient messages
    still use the strict predecessor window around the focus.
    """
    focus = next((m for m in messages if m.get("id") == focus_message_id), None)
    if focus is None:
        raise KeyError(f"focus message not found: {focus_message_id}")
    if _parse_iso(focus.get("created_at")) is None:
        return ReplayScope(
            case_id=case_id,
            focus_message_id=focus_message_id,
            cutoff_at=None,
            review_context=review_context,
            visible_messages=[],
            prior_answer_message_ids=[],
            patient_message_ids=[],
            excluded_message_ids=[m["id"] for m in messages],
            exclusion_reasons={
                m["id"]: "焦点时间不可解析，无法建立严格前序"
                for m in messages
                if m.get("id")
            },
            order_quality="unknown",
        )

    focus_time = _parse_iso(focus["created_at"])
    assert focus_time is not None
    visible: list[MessageView] = []
    prior_answer_ids: list[str] = []
    patient_ids: list[str] = []
    excluded: dict[str, str] = {}
    missing_time: list[str] = []

    visible_patient_times: list[datetime] = []
    for message in messages:
        message_id = message.get("id")
        if message_id == focus_message_id:
            excluded[message_id] = "焦点回答本身不作为前序上下文"
            continue
        message_time = _parse_iso(message.get("created_at"))
        if message_time is None:
            missing_time.append(message_id)
            excluded[message_id] = "时间不可解析，无法确认是否早于焦点"
            continue
        if review_context == "current" and message.get("role") == "patient":
            if message.get("order_basis") == "manual_supplement":
                # 当前审阅把人工确认的患者补录视为可见患者材料，
                # 原始平台晚于焦点的患者消息仍按严格前序排除。
                view = MessageView.model_validate(message)
                visible.append(view)
                patient_ids.append(message_id)
                visible_patient_times.append(message_time)
                continue
            if message_time >= focus_time:
                excluded[message_id] = "患者信息晚于焦点时点，焦点发出时不可见"
                continue
        if message_time >= focus_time:
            if message_time == focus_time:
                reason = (
                    "与焦点同时刻，不构成严格前序"
                    if message.get("role") == "doctor"
                    else "患者补充与焦点同时刻，不视为焦点前已知"
                )
            elif message.get("role") == "patient":
                reason = "患者信息晚于焦点时点，焦点发出时不可见"
            else:
                reason = "后续回答，焦点发出时不可见"
            excluded[message_id] = reason
            continue
        view = MessageView.model_validate(message)
        visible.append(view)
        if message.get("role") == "doctor":
            prior_answer_ids.append(message_id)
        else:
            patient_ids.append(message_id)

    visible.sort(key=lambda item: (item.created_at or "", item.id))
    quality = "ok"
    if missing_time:
        quality = "partial"
    if review_context == "current":
        cutoff = max(visible_patient_times) if visible_patient_times else focus_time
        cutoff_at = cutoff.isoformat()
    else:
        cutoff_at = focus["created_at"]
    return ReplayScope(
        case_id=case_id,
        focus_message_id=focus_message_id,
        cutoff_at=cutoff_at,
        review_context=review_context,
        visible_messages=visible,
        prior_answer_message_ids=prior_answer_ids,
        patient_message_ids=patient_ids,
        excluded_message_ids=list(excluded.keys()),
        exclusion_reasons=excluded,
        order_quality=quality,
    )
