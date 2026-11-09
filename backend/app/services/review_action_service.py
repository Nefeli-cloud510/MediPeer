from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Case,
    CaseVersion,
    Draft,
    DraftVersion,
    Finding,
    ReviewAction,
    Supplement,
    User,
)
from ..schemas import DraftView, ReviewActionView, SupplementView
from .case_service import draft_view


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _messages_hash(messages: list[dict[str, Any]]) -> str:
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_finding(db: Session, case: Case, finding_id: str) -> Finding:
    finding = db.get(Finding, finding_id)
    if finding is None or finding.case_id != case.id:
        raise KeyError("finding not found in this case")
    return finding


def record_review_action(
    db: Session,
    case: Case,
    user: User,
    body: dict[str, Any],
) -> ReviewActionView:
    finding = _load_finding(db, case, body["finding_id"])
    action = body["action"]
    review_status = {
        "confirmed": "confirmed",
        "rejected": "rejected",
        "supplement_adopted": "question_adopted",
    }[action]
    finding.review_status = review_status
    record = ReviewAction(
        finding_id=finding.id,
        run_id=finding.run_id,
        action=action,
        note=body.get("note"),
        suggested_text=body.get("suggested_text"),
        created_by=user.username,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return ReviewActionView(
        id=record.id,
        finding_id=record.finding_id,
        run_id=record.run_id,
        action=record.action,
        note=record.note,
        suggested_text=record.suggested_text,
        supplement_id=record.supplement_id,
        created_by=record.created_by,
        created_at=record.created_at,
    )


def add_supplement(
    db: Session,
    case: Case,
    user: User,
    body: dict[str, Any],
) -> SupplementView:
    content = body["content"]
    finding_id = body.get("finding_id")
    if finding_id:
        _load_finding(db, case, finding_id)
    current = db.get(CaseVersion, case.current_version_id)
    if current is None:
        raise ValueError("case has no current version, supplement cannot be added")

    message_id = f"app-msg-{uuid.uuid4().hex[:12]}"
    now = _now()
    messages = [dict(item) for item in current.messages_json]
    messages.append(
        {
            "id": message_id,
            "role": "patient",
            "speaker_id": "patient-current",
            "speaker_name": "患者（人工补录）",
            "text": content,
            "created_at": now,
            "order_basis": "manual_supplement",
            "needs_review": False,
        }
    )
    version_no = current.version_no + 1
    version = CaseVersion(
        case_id=case.id,
        version_no=version_no,
        reason="人工补录患者信息",
        created_by=user.username,
        messages_json=messages,
        messages_hash=_messages_hash(messages),
    )
    db.add(version)
    db.flush()
    case.current_version_id = version.id
    case.version_no = version_no

    supplement = Supplement(
        case_id=case.id,
        case_version_id=version.id,
        finding_id=finding_id,
        source_type="manual_input",
        role="patient",
        content=content,
        created_by=user.username,
    )
    db.add(supplement)
    db.commit()
    db.refresh(supplement)
    return SupplementView(
        id=supplement.id,
        case_id=case.id,
        case_version_id=version.id,
        version_no=version_no,
        message_id=message_id,
        finding_id=supplement.finding_id,
        source_type=supplement.source_type,
        role=supplement.role,
        content=supplement.content,
        created_by=supplement.created_by,
        created_at=supplement.created_at,
    )


def update_draft(
    db: Session,
    case: Case,
    user: User,
    body: dict[str, Any],
) -> DraftView:
    draft_text = body["draft_text"]
    draft = db.scalar(select(Draft).where(Draft.case_id == case.id))
    if draft is None:
        draft = Draft(
            case_id=case.id,
            current_text=draft_text,
            current_hash=_text_hash(draft_text),
        )
        db.add(draft)
        db.flush()
        version_no = 1
    else:
        latest = db.scalar(
            select(DraftVersion)
            .where(DraftVersion.draft_id == draft.id)
            .order_by(DraftVersion.version_no.desc())
            .limit(1)
        )
        version_no = latest.version_no + 1 if latest else 1

    db.add(
        DraftVersion(
            draft_id=draft.id,
            version_no=version_no,
            text=draft_text,
            text_hash=_text_hash(draft_text),
        )
    )
    draft.current_text = draft_text
    draft.current_hash = _text_hash(draft_text)
    db.commit()
    db.refresh(draft)
    return draft_view(db, draft)
