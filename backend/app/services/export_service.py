from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.models import (
    AnalysisRun,
    Case,
    CaseVersion,
    Draft,
    DraftVersion,
    ExportRecord,
    Finding,
    ReviewAction,
    Supplement,
    User,
)
from ..schemas.exports import CaseExportView
from .case_service import _message_view
from .joint_review_service import _diff_reason, _finding_diff, _select_previous_run


ExportFormat = Literal["json", "markdown"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _elapsed(run: AnalysisRun) -> int | None:
    if not run.started_at or not run.finished_at:
        return None
    try:
        start = datetime.fromisoformat(run.started_at)
        end = datetime.fromisoformat(run.finished_at)
        return max(0, int((end - start).total_seconds()))
    except ValueError:
        return None


def _messages_in_scope(
    version: CaseVersion,
    scope_message_ids: list[str],
) -> list[dict[str, Any]]:
    by_id = {message["id"]: message for message in version.messages_json}
    return [
        by_id[message_id]
        for message_id in scope_message_ids
        if message_id in by_id
    ]


def _validated_finding(
    finding: Finding,
    message_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    refs: list[dict[str, Any]] = []
    for ref in finding.evidence_refs or []:
        message_id = ref.get("message_id", "")
        snippet = ref.get("snippet", "")
        source = message_by_id.get(message_id)
        present = bool(source) and bool(snippet) and snippet in source["text"]
        refs.append(
            {
                **dict(ref),
                "snippet_present_in_message": present,
                "message_role_match": bool(source) and source["role"] == ref.get("role"),
            }
        )
    return {
        "id": finding.id,
        "draft_sentence_id": finding.draft_sentence_id,
        "draft_sentence_text": finding.draft_sentence_text,
        "contribution": finding.contribution,
        "patient_grounding": finding.patient_grounding,
        "explanation": finding.explanation,
        "evidence_refs": refs,
        "related_fact_ids": finding.related_fact_ids or [],
        "related_claim_ids": finding.related_claim_ids or [],
        "checked_message_ids": finding.checked_message_ids or [],
        "suggested_action": finding.suggested_action,
        "review_status": finding.review_status,
        "signature": finding.signature,
    }


def _build_payload(
    db: Session,
    case: Case,
    run: AnalysisRun,
) -> dict[str, Any]:
    version = db.get(CaseVersion, run.case_version_id)
    if version is None:
        raise ValueError("run refers to an unknown case version")
    messages = _messages_in_scope(version, run.scope_message_ids or [])
    message_by_id = {message["id"]: message for message in messages}
    scope_messages = [_message_view(message).model_dump() for message in messages]

    findings = list(
        db.scalars(select(Finding).where(Finding.run_id == run.id)).all()
    )
    finding_views = [
        _validated_finding(finding, message_by_id) for finding in findings
    ]
    evidence_errors: list[str] = []
    for finding in finding_views:
        for ref in finding["evidence_refs"]:
            if not ref["snippet_present_in_message"]:
                evidence_errors.append(
                    f"{finding['draft_sentence_text']} -> {ref['message_id']}: "
                    "snippet not verbatim in message"
                )
            if not ref["message_role_match"]:
                evidence_errors.append(
                    f"{finding['draft_sentence_text']} -> {ref['message_id']}: "
                    "evidence role mismatch"
                )

    draft: Draft | None = db.scalar(
        select(Draft).where(Draft.case_id == case.id)
    )
    draft_version: DraftVersion | None = None
    if draft is not None:
        draft_version = db.scalar(
            select(DraftVersion)
            .where(DraftVersion.draft_id == draft.id)
            .order_by(DraftVersion.version_no.desc())
            .limit(1)
        )

    actions = list(
        db.scalars(
            select(ReviewAction)
            .where(ReviewAction.run_id == run.id)
            .order_by(ReviewAction.created_at)
        ).all()
    )
    supplements = list(
        db.scalars(
            select(Supplement)
            .where(
                Supplement.case_id == case.id,
                Supplement.case_version_id == run.case_version_id,
            )
            .order_by(Supplement.created_at)
        ).all()
    )

    previous_candidates = list(
        db.scalars(
            select(AnalysisRun)
            .where(
                AnalysisRun.case_id == case.id,
                AnalysisRun.focus_message_id == run.focus_message_id,
                AnalysisRun.review_mode == run.review_mode,
                AnalysisRun.status == "succeeded",
                AnalysisRun.id != run.id,
            )
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        ).all()
    )
    previous_run = _select_previous_run(run, previous_candidates)
    old_findings: list[Finding] = []
    if previous_run is not None:
        old_findings = list(
            db.scalars(
                select(Finding).where(Finding.run_id == previous_run.id)
            ).all()
        )
    diff_reason = _diff_reason(previous_run, run)
    diff_items = [
        item.model_dump()
        for item in _finding_diff(
            previous_run,
            old_findings,
            list(findings),
            diff_reason,
        )
    ]

    return {
        "export_kind": "medipeer_review_record",
        "export_version": "1",
        "generated_at": _now(),
        "run": {
            "run_id": run.id,
            "case_id": case.id,
            "case_version_id": run.case_version_id,
            "case_version_no": version.version_no,
            "draft_id": draft.id if draft else None,
            "draft_version_id": draft_version.id if draft_version else None,
            "draft_version_no": draft_version.version_no if draft_version else None,
            "focus_message_id": run.focus_message_id,
            "review_context": run.scope_message_ids and "scoped" or "",
            "cutoff_at": None,
            "review_mode": run.review_mode,
            "source_mode": run.source_mode,
            "model_name": run.model_name,
            "rules_version": run.rules_version,
            "prompt_versions": run.prompt_versions or {},
            "status": run.status,
            "elapsed_seconds": _elapsed(run),
            "input_chars": run.input_chars,
            "excluded_message_ids": run.excluded_message_ids or [],
            "exclusion_reasons": run.exclusion_reasons or {},
        },
        "case": {
            "id": case.id,
            "title": case.title,
            "department": case.department,
            "platform": case.platform,
            "validation_scope": case.validation_scope,
            "template_key": case.template_key,
            "created_at": case.created_at,
        },
        "draft": {
            "id": draft.id if draft else None,
            "current_text": draft.current_text if draft else "",
            "version_no": draft_version.version_no if draft_version else 1,
        },
        "visible_messages": scope_messages,
        "findings": finding_views,
        "evidence_validation": {
            "checked": True,
            "errors": evidence_errors,
            "passes": not evidence_errors,
        },
        "review_actions": [
            {
                "id": action.id,
                "finding_id": action.finding_id,
                "action": action.action,
                "note": action.note,
                "suggested_text": action.suggested_text,
                "supplement_id": action.supplement_id,
                "created_by": action.created_by,
                "created_at": action.created_at,
            }
            for action in actions
        ],
        "supplements": [
            {
                "id": supplement.id,
                "case_version_id": supplement.case_version_id,
                "finding_id": supplement.finding_id,
                "source_type": supplement.source_type,
                "role": supplement.role,
                "content": supplement.content,
                "created_by": supplement.created_by,
                "created_at": supplement.created_at,
            }
            for supplement in supplements
        ],
        "version_changes": {
            "previous_run_id": previous_run.id if previous_run else None,
            "diff_reason": diff_reason,
            "diff": diff_items,
        },
    }


def _markdown_text(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        "# MediPeer 审阅记录",
        "",
        f"- 病例：{payload['case']['title']}（{payload['case']['id']}）",
        f"- 运行：{payload['run']['run_id']}",
        f"- 模式：{payload['run']['review_mode']} | 来源：{payload['run']['source_mode']}",
        f"- 模型：{payload['run']['model_name']} | 规则：{payload['run']['rules_version']}",
        f"- 病例版本：v{payload['run']['case_version_no']}（{payload['run']['case_version_id']}）",
        (
            f"- 草稿版本：v{payload['draft']['version_no']}"
            f"（{payload['run']['draft_version_id']}）"
        ),
        f"- 状态：{payload['run']['status']}",
        "",
        "## 草稿",
        "",
        payload["draft"]["current_text"],
        "",
        "## 可见消息",
        "",
    ]
    for message in payload["visible_messages"]:
        role = "患者" if message["role"] == "patient" else "医生"
        lines.append(
            f"1. [{role}] {message['id']}"
            f"{(' · ' + str(message['created_at'])) if message.get('created_at') else ''}: "
            f"{message['text']}"
        )
    lines.append("")
    lines.append("## 逐主张审阅")
    lines.append("")
    if not payload["findings"]:
        lines.append("无审阅结果。")
        lines.append("")
    for index, finding in enumerate(payload["findings"], start=1):
        lines.append(f"### {index}. {finding['draft_sentence_text']}")
        lines.append("")
        lines.append(f"- 观点贡献：{finding['contribution']}")
        lines.append(f"- 患者依据：{finding['patient_grounding']}")
        lines.append(f"- 建议动作：{finding['suggested_action']}")
        lines.append(f"- 处理状态：{finding['review_status']}")
        lines.append(f"- 说明：{finding['explanation']}")
        lines.append("- 引用：")
        if not finding["evidence_refs"]:
            lines.append("  - （无）")
        for ref in finding["evidence_refs"]:
            role = "患者" if ref["role"] == "patient" else "医生"
            presence = "通过" if ref["snippet_present_in_message"] else "未通过"
            lines.append(
                f"  - [{role}] {ref['message_id']}（原文校验{role}）：{ref['snippet']}"
            )
        lines.append("")

    lines.append("## 人工处理记录")
    lines.append("")
    if not payload["review_actions"]:
        lines.append("无。")
    for action in payload["review_actions"]:
        lines.append(
            f"- {action['created_at']} {action['created_by']}: "
            f"{action['action']}（finding {action['finding_id']}）"
        )
        if action.get("suggested_text"):
            lines.append(f"  - 补录建议：{action['suggested_text']}")
        if action.get("note"):
            lines.append(f"  - 备注：{action['note']}")
    lines.append("")

    lines.append("## 补录材料")
    lines.append("")
    if not payload["supplements"]:
        lines.append("无。")
    for supplement in payload["supplements"]:
        lines.append(
            f"- {supplement['created_at']} {supplement['created_by']}: "
            f"{supplement['content']}"
        )
    lines.append("")

    lines.append("## 版本变化")
    lines.append("")
    change = payload["version_changes"]
    lines.append(f"- 上一运行：{change['previous_run_id'] or '无'}")
    lines.append(f"- 变化原因：{change['diff_reason'] or '无'}")
    if not change["diff"]:
        lines.append("- 提示变化：无")
    for item in change["diff"]:
        lines.append(
            f"- {item['change']}：{item['draft_sentence_text']}"
            f"（{item['old_status'] or '无'} → {item['new_status']}）"
        )
    lines.append("")
    lines.append("## 引用校验")
    lines.append("")
    validation = payload["evidence_validation"]
    if validation["passes"]:
        lines.append("所有引用片段可定位到对应原文，角色一致。")
    else:
        lines.append("存在未通过的引用：")
        for error in validation["errors"]:
            lines.append(f"- {error}")
    lines.append("")
    lines.append("> 程序只校验引用可定位与角色/时间范围；医学结论仍需人工核验。")
    return "\n".join(lines)


def _file_name(case_id: str, run_id: str, fmt: ExportFormat) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    extension = "md" if fmt == "markdown" else "json"
    return f"medipeer_{case_id}_{run_id}_{stamp}.{extension}"


def build_case_export(
    db: Session,
    case: Case,
    run_id: str,
    user: User,
    fmt: ExportFormat = "json",
) -> CaseExportView:
    run = db.get(AnalysisRun, run_id)
    if run is None or run.case_id != case.id:
        raise KeyError("run not found in this case")
    payload = _build_payload(db, case, run)
    if fmt == "markdown":
        content = _markdown_text(payload)
    else:
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    settings = get_settings()
    export_dir = Path(settings.project_root) / "data" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    file_name = _file_name(case.id, run.id, fmt)
    file_path = export_dir / file_name
    file_path.write_text(content, encoding="utf-8")

    record = ExportRecord(
        id=uuid.uuid4().hex,
        case_id=case.id,
        run_id=run.id,
        export_type=fmt,
        file_name=file_name,
        file_path=str(file_path),
        created_by=user.username,
    )
    db.add(record)
    db.commit()
    return CaseExportView(
        export_id=record.id,
        case_id=case.id,
        run_id=run.id,
        format=fmt,
        file_name=file_name,
        content=content,
        created_at=record.created_at,
    )
