from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Case, CaseVersion, Draft, DraftVersion, User
from ..demo.templates import get_demo_template
from ..schemas.case import CaseDetail, CaseSummary, DraftView, MessageView


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _messages_hash(messages: list[dict[str, Any]]) -> str:
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _message_view(message: dict[str, Any]) -> MessageView:
    if not message.get("id") or message.get("role") not in ("patient", "doctor"):
        raise ValueError("message must have a valid id and role")
    return MessageView.model_validate(message)


def create_demo_case(db: Session, user: User, template_key: str) -> Case:
    template = get_demo_template(template_key)
    messages = [dict(item) for item in template["messages"]]
    case = Case(
        owner_id=user.id,
        title=template["title"],
        department=template.get("department"),
        platform=template.get("platform", "weiyi"),
        validation_scope=template.get("validation_scope", "generic_text"),
        template_key=template["key"],
        seed_draft_text=template.get("seed_draft_text", ""),
    )
    db.add(case)
    db.flush()

    version = CaseVersion(
        case_id=case.id,
        version_no=1,
        reason="从演示模板复制到当前会话",
        created_by=user.username,
        messages_json=messages,
        messages_hash=_messages_hash(messages),
    )
    db.add(version)
    db.flush()
    case.current_version_id = version.id

    draft = Draft(
        case_id=case.id,
        current_text=template.get("seed_draft_text", ""),
        current_hash=_text_hash(template.get("seed_draft_text", "")),
    )
    db.add(draft)
    db.flush()
    db.add(
        DraftVersion(
            draft_id=draft.id,
            version_no=1,
            text=template.get("seed_draft_text", ""),
            text_hash=_text_hash(template.get("seed_draft_text", "")),
        )
    )
    db.commit()
    db.refresh(case)
    return case


def list_cases(db: Session, user: User) -> list[CaseSummary]:
    cases = db.scalars(
        select(Case)
        .where(Case.owner_id == user.id, Case.status == "active")
        .order_by(Case.created_at.desc())
    ).all()
    return [CaseSummary.model_validate(_case_summary_dict(c)) for c in cases]


def _case_summary_dict(case: Case) -> dict[str, Any]:
    return {
        "id": case.id,
        "title": case.title,
        "department": case.department,
        "platform": case.platform,
        "validation_scope": case.validation_scope,
        "version_no": case.version_no,
        "template_key": case.template_key,
        "status": case.status,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


def get_case_for_user(db: Session, case_id: str, user: User) -> Case:
    case = db.get(Case, case_id)
    if case is None or case.owner_id != user.id:
        raise PermissionError("case not found or not owned by current user")
    return case


def get_case_detail(db: Session, case_id: str, user: User) -> CaseDetail:
    case = get_case_for_user(db, case_id, user)
    version = (
        db.get(CaseVersion, case.current_version_id) if case.current_version_id else None
    )
    messages = version.messages_json if version else []
    draft = db.scalar(select(Draft).where(Draft.case_id == case.id))
    data = _case_summary_dict(case)
    data["messages"] = [_message_view(m).model_dump() for m in messages]
    data["draft"] = draft_view(db, draft) if draft else None
    return CaseDetail.model_validate(data)


def get_draft_for_case(db: Session, case: Case) -> Draft | None:
    return db.scalar(select(Draft).where(Draft.case_id == case.id))


def draft_view(db: Session, draft: Draft) -> DraftView:
    latest = db.scalar(
        select(DraftVersion)
        .where(DraftVersion.draft_id == draft.id)
        .order_by(DraftVersion.version_no.desc())
        .limit(1)
    )
    return DraftView(
        id=draft.id,
        case_id=draft.case_id,
        current_text=draft.current_text,
        current_hash=draft.current_hash,
        version_no=latest.version_no if latest else 1,
        updated_at=draft.updated_at,
    )
