from __future__ import annotations

from pydantic import BaseModel
from typing import Literal

from .case import MessageView


class ReplayScope(BaseModel):
    case_id: str
    focus_message_id: str
    cutoff_at: str | None = None
    review_context: Literal["replay", "current"] = "replay"
    visible_messages: list[MessageView]
    prior_answer_message_ids: list[str]
    patient_message_ids: list[str]
    excluded_message_ids: list[str]
    exclusion_reasons: dict[str, str]
    order_quality: str
