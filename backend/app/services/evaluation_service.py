"""完整方案配对评测（t17）。

同一演示病例、同一焦点、同一草稿版本、同一严格前序上下文下分别运行：

- joint_review：完整方案（患者事实 + 医生观点 + 草稿联合审阅）
- baseline_review：同模型直接审阅（不运行结构化模块）

首版没有人工标签，因此本模块只统计结构数量、失败与不适用情况，
不生成准确率/召回率/F1 等性能结论。
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.models import AnalysisRun, Case, EvaluationRun, Finding, ModelCall, User
from ..demo.templates import get_demo_template
from . import case_service
from .baseline_review_service import BaselineReviewFailure
from .joint_review_service import run_joint_review
from .baseline_review_service import run_baseline_review


_SUPPORTED_TEMPLATES = {
    "demo_respiratory_multi_doctor": "wy-a-2003-focus",
    "demo_respiratory_single_doctor": "sa-a-2001",
    "demo_fever_sputum_three_rounds": "wy-fq-a-2003-focus",
}


class PairEvaluationError(Exception):
    """评测运行层面失败：模板未知、焦点无效、权限不足等。"""


@dataclass
class _RunData:
    run: AnalysisRun
    findings: list[Finding]
    error: str | None
    error_class: str | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
            fh.write("\n")
        shutil.move(str(tmp_path), str(path))
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _elapsed_seconds(run: AnalysisRun) -> int | None:
    if not run.started_at or not run.finished_at:
        return None
    try:
        start = datetime.fromisoformat(run.started_at)
        end = datetime.fromisoformat(run.finished_at)
        return max(0, int((end - start).total_seconds()))
    except ValueError:
        return None


def _run_pairs_defaults() -> dict[str, Any]:
    return {
        "joint_review": {"mode": None, "run_id": None, "error": None},
        "baseline_review": {"mode": None, "run_id": None, "error": None},
    }


def _finding_counts(findings: list[Finding]) -> dict[str, int]:
    return {
        "total": len(findings),
        "with_evidence_refs": sum(
            1 for finding in findings if finding.evidence_refs
        ),
        "evidence_refs": sum(len(finding.evidence_refs) for finding in findings),
        "pending": sum(1 for finding in findings if finding.review_status == "pending"),
        "confirmed": sum(
            1 for finding in findings if finding.review_status == "confirmed"
        ),
        "rejected": sum(
            1 for finding in findings if finding.review_status == "rejected"
        ),
    }


def _distribution(findings: list[Finding], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        value = str(getattr(finding, attr, "") or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _binding_fingerprint(run: AnalysisRun) -> tuple[Any, ...]:
    return (
        run.case_version_id,
        run.draft_version_id,
        run.focus_message_id,
        run.model_name,
        tuple(run.scope_message_ids or []),
    )


def _findings_for_run(db: Session, run_id: str) -> list[Finding]:
    return list(db.scalars(select(Finding).where(Finding.run_id == run_id)).all())


def _model_call_summary(
    db: Session, run_id: str
) -> dict[str, Any] | None:
    calls = list(
        db.scalars(select(ModelCall).where(ModelCall.run_id == run_id)).all()
    )
    if not calls:
        return None
    failed = [call for call in calls if call.status != "ok"]
    return {
        "count": len(calls),
        "failed": len(failed),
        "tools": sorted({call.tool for call in calls}),
        "max_latency_ms": max((call.latency_ms or 0) for call in calls),
        "output_chars": sum(call.output_chars or 0 for call in calls),
        "first_error_class": ((failed[0].error_class) if failed else None),
    }


def _capture_run(
    db: Session,
    run: AnalysisRun,
    error: str | None = None,
    error_class: str | None = None,
) -> _RunData:
    return _RunData(
        run=run,
        findings=_findings_for_run(db, run.id),
        error=error,
        error_class=error_class,
    )


def _try_run(
    db: Session,
    case: Case,
    focus_message_id: str,
    mode: str,
) -> tuple[AnalysisRun, str | None, str | None]:
    try:
        if mode == "joint_review":
            response = run_joint_review(db, case, focus_message_id)
        else:
            response = run_baseline_review(db, case, focus_message_id)
    except BaselineReviewFailure as exc:
        run = db.scalar(
            select(AnalysisRun)
            .where(
                AnalysisRun.case_id == case.id,
                AnalysisRun.review_mode == mode,
                AnalysisRun.status == "failed",
            )
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .limit(1)
        )
        run_id = exc.run_id
        error_class = exc.error_class
        if run is None and run_id:
            run = db.get(AnalysisRun, run_id)
        if run is None:
            raise PairEvaluationError(
                f"{mode} failed before creating a run: {exc}"
            ) from exc
        return run, str(exc), error_class
    except ValueError as exc:
        run = db.scalar(
            select(AnalysisRun)
            .where(
                AnalysisRun.case_id == case.id,
                AnalysisRun.review_mode == mode,
                AnalysisRun.status == "failed",
            )
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .limit(1)
        )
        if run is None:
            raise PairEvaluationError(
                f"{mode} failed before creating a run: {exc}"
            ) from exc
        return run, str(exc), exc.__class__.__name__

    run = db.get(AnalysisRun, response.run_id)
    if run is None:
        raise PairEvaluationError(f"{mode} returned unknown run_id: {response.run_id}")
    return run, None, None


def _default_focus(template_key: str) -> str:
    template = get_demo_template(template_key)
    focus = next(
        (
            message["id"]
            for message in template["messages"]
            if message.get("id", "").endswith("-focus")
        ),
        None,
    )
    if focus:
        return focus
    doctor_ids = [
        message["id"]
        for message in template["messages"]
        if message.get("role") == "doctor"
    ]
    if not doctor_ids:
        raise PairEvaluationError(f"template has no doctor message: {template_key}")
    return doctor_ids[-1]


def _write_detail_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "case_id",
        "template_key",
        "focus_message_id",
        "review_mode",
        "run_id",
        "status",
        "error",
        "error_class",
        "draft_sentence_id",
        "draft_sentence_text",
        "contribution",
        "patient_grounding",
        "suggested_action",
        "review_status",
        "evidence_ref_count",
        "related_fact_count",
        "related_claim_count",
        "input_chars",
        "model_name",
        "rules_version",
        "prompt_versions",
    ]
    report_dir = path.parent
    report_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = report_dir / f".{path.name}.tmp"
    with tmp_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            normalized = dict(row)
            normalized["evidence_ref_count"] = int(
                normalized.get("evidence_ref_count") or 0
            )
            normalized["related_fact_count"] = int(
                normalized.get("related_fact_count") or 0
            )
            normalized["related_claim_count"] = int(
                normalized.get("related_claim_count") or 0
            )
            writer.writerow(normalized)
    tmp_path.replace(path)


def _write_failure_cases(
    path: Path,
    detail_rows: list[dict[str, Any]],
    pair_errors: dict[str, dict[str, str | None]],
) -> None:
    lines = [
        "# t17 配对评测失败案例",
        "",
        "失败定义：模式未成功（status=failed）、运行错误、"
        "证据引用缺失（grounding 要求实证但 evidence_refs 为空）、输入绑定不一致。",
        "首版为工程配对，不承担医学正确性结论。",
        "",
    ]
    failed = [
        row
        for row in detail_rows
        if row.get("status") == "failed" or row.get("error")
    ]
    if not failed and not any(pair_errors.values()):
        lines.append("本运行无失败案例。")
    else:
        lines.append("## 运行失败")
        for mode, info in pair_errors.items():
            if info.get("run_id"):
                lines.append(
                    f"- {mode}: run_id={info['run_id']}, "
                    f"error_class={info.get('error_class') or '-'}, "
                    f"error={info.get('error') or '-'}"
                )
        lines.append("")
        lines.append("## 条目失败 / 异常")
        for row in failed:
            lines.append(
                f"- {row.get('review_mode')} | {row.get('run_id')} | "
                f"{row.get('draft_sentence_id')} | "
                f"grounding={row.get('patient_grounding')} | "
                f"evidence={row.get('evidence_ref_count')} | "
                f"error={row.get('error') or '-'}"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_pair_evaluation(
    db: Session,
    user: User,
    template_key: str = "demo_respiratory_multi_doctor",
    focus_message_id: str | None = None,
    name: str = "t17 配对评测",
    case_id: str | None = None,
    run_id: str | None = None,
    output_root: Path | None = None,
) -> tuple[EvaluationRun, Path]:
    """运行一次 joint + baseline 配对评测并落盘。

    未配置真实模型时自动使用 stub 规则/基线，输出中明确标注 source_mode，
    不把结构评测宣传为模型性能验证。
    """

    settings = get_settings()
    if template_key not in _SUPPORTED_TEMPLATES:
        raise PairEvaluationError(
            f"unsupported template for evaluation: {template_key}"
        )
    if case_id is None:
        case = case_service.create_demo_case(db, user, template_key)
    else:
        case = db.get(Case, case_id)
        if case is None or case.owner_id != user.id:
            raise PermissionError("case not found or not owned by current user")
    if focus_message_id is None:
        focus_message_id = _default_focus(template_key)
    if focus_message_id not in _SUPPORTED_TEMPLATES[template_key]:
        raise PairEvaluationError(
            f"focus {focus_message_id} is not the supported focus for {template_key}"
        )

    eval_run = EvaluationRun(
        id=run_id or uuid.uuid4().hex,
        name=name,
        status="running",
        case_ids=[case.id],
        config={
            "task": "t17_pair_evaluation",
            "template_key": template_key,
            "focus_message_id": focus_message_id,
            "joint_mode": "joint_review",
            "baseline_mode": "direct_baseline",
        },
        summary={},
        created_by=user.username,
        created_at=_now_iso(),
    )
    db.add(eval_run)
    db.flush()

    pairs = _run_pairs_defaults()
    pair_errors: dict[str, dict[str, str | None]] = {
        "joint_review": {"run_id": None, "error_class": None, "error": None},
        "baseline_review": {"run_id": None, "error_class": None, "error": None},
    }

    detail_rows: list[dict[str, Any]] = []
    for mode in ("joint_review", "baseline_review"):
        run, error, error_class = _try_run(db, case, focus_message_id, mode)
        captured = _capture_run(db, run, error, error_class)
        pairs[mode] = {
            "mode": run.review_mode,
            "run_id": run.id,
            "error": error,
            "error_class": error_class,
            "status": run.status,
        }
        pair_errors[mode] = {
            "run_id": error and run.id or None,
            "error_class": error_class,
            "error": error,
        }
        if error:
            detail_rows.append(
                {
                    "case_id": case.id,
                    "template_key": template_key,
                    "focus_message_id": focus_message_id,
                    "review_mode": run.review_mode,
                    "run_id": run.id,
                    "status": run.status,
                    "error": error,
                    "error_class": error_class,
                    "input_chars": run.input_chars,
                    "model_name": run.model_name,
                    "rules_version": run.rules_version,
                    "prompt_versions": json.dumps(
                        run.prompt_versions or {}, ensure_ascii=False
                    ),
                }
            )
            continue
        for finding in captured.findings:
            detail_rows.append(
                {
                    "case_id": case.id,
                    "template_key": template_key,
                    "focus_message_id": focus_message_id,
                    "review_mode": run.review_mode,
                    "run_id": run.id,
                    "status": run.status,
                    "error": "",
                    "error_class": "",
                    "draft_sentence_id": finding.draft_sentence_id,
                    "draft_sentence_text": finding.draft_sentence_text,
                    "contribution": finding.contribution,
                    "patient_grounding": finding.patient_grounding,
                    "suggested_action": finding.suggested_action,
                    "review_status": finding.review_status,
                    "evidence_ref_count": len(finding.evidence_refs or []),
                    "related_fact_count": len(finding.related_fact_ids or []),
                    "related_claim_count": len(finding.related_claim_ids or []),
                    "input_chars": run.input_chars,
                    "model_name": run.model_name,
                    "rules_version": run.rules_version,
                    "prompt_versions": json.dumps(
                        run.prompt_versions or {}, ensure_ascii=False
                    ),
                }
            )

    joint = pairs.get("joint_review", {})
    baseline = pairs.get("baseline_review", {})
    joint_run = (
        db.get(AnalysisRun, joint["run_id"]) if joint.get("run_id") else None
    )
    baseline_run = (
        db.get(AnalysisRun, baseline["run_id"]) if baseline.get("run_id") else None
    )
    binding_match = False
    if joint_run is not None and baseline_run is not None:
        binding_match = _binding_fingerprint(joint_run) == _binding_fingerprint(
            baseline_run
        )

    summary: dict[str, Any] = {
        "task": "t17_pair_evaluation",
        "evaluation_run_id": eval_run.id,
        "status": "succeeded" if not pair_errors["joint_review"]["error"] else "degraded",
        "generated_at": _now_iso(),
        "provider": settings.resolved_llm_provider,
        "model_name": settings.resolved_llm_model,
        "rules_version": settings.rules_version,
        "template_key": template_key,
        "case_ids": [case.id],
        "focus_message_id": focus_message_id,
        "binding": {
            "match": binding_match,
            "joint": {
                "case_version_id": joint_run.case_version_id if joint_run else None,
                "draft_version_id": joint_run.draft_version_id if joint_run else None,
                "focus_message_id": joint_run.focus_message_id if joint_run else None,
                "scope_message_ids": joint_run.scope_message_ids if joint_run else [],
                "model_name": joint_run.model_name if joint_run else None,
            },
            "baseline": {
                "case_version_id": baseline_run.case_version_id if baseline_run else None,
                "draft_version_id": baseline_run.draft_version_id
                if baseline_run
                else None,
                "focus_message_id": baseline_run.focus_message_id
                if baseline_run
                else None,
                "scope_message_ids": baseline_run.scope_message_ids
                if baseline_run
                else [],
                "model_name": baseline_run.model_name if baseline_run else None,
            },
        },
        "cases": [
            {
                "case_id": case.id,
                "template_key": template_key,
                "focus_message_id": focus_message_id,
                "joint_review_run_id": None,
                "baseline_review_run_id": None,
            }
        ],
        "run_pairs": {},
        "totals": {
            "cases": 1,
            "joint_succeeded": 0,
            "baseline_succeeded": 0,
            "status_distribution": {},
        },
        "failures": [],
        "not_applicable": [
            "无人工标签，不生成准确率/召回率/F1 或效果图",
            (
                "provider=stub 时仅能核对结构一致性与引用绑定，"
                "不代表模型性能验证"
                if settings.resolved_llm_provider == "stub"
                else "真实模型运行保留 模型调用/耗时/失败状态，医学结论仍需人工核验"
            ),
        ],
    }

    for mode in ("joint_review", "baseline_review"):
        info = pairs[mode]
        run = db.get(AnalysisRun, info["run_id"]) if info.get("run_id") else None
        if run is None:
            summary["failures"].append(
                f"{mode} failed before run persisted: {info.get('error') or 'unknown'}"
            )
            continue
        findings = _findings_for_run(db, run.id) if run.status == "succeeded" else []
        entry = {
            "mode": run.review_mode,
            "run_id": run.id,
            "status": run.status,
            "error": info.get("error"),
            "error_class": info.get("error_class"),
            "source_mode": run.source_mode,
            "review_mode": run.review_mode,
            "input_chars": run.input_chars,
            "model_name": run.model_name,
            "rules_version": run.rules_version,
            "prompt_versions": run.prompt_versions,
            "scope_message_ids": run.scope_message_ids or [],
            "excluded_message_ids": run.excluded_message_ids or [],
            "elapsed_seconds": _elapsed_seconds(run),
            "findings": _finding_counts(findings),
            "contribution_distribution": _distribution(findings, "contribution"),
            "grounding_distribution": _distribution(findings, "patient_grounding"),
            "action_distribution": _distribution(findings, "suggested_action"),
            "model_call": _model_call_summary(db, run.id),
        }
        summary["run_pairs"][mode] = entry
        if summary["cases"]:
            case_summary = summary["cases"][0]
            if mode == "joint_review":
                case_summary["joint_review_run_id"] = run.id
            else:
                case_summary["baseline_review_run_id"] = run.id
        if info.get("error"):
            summary["failures"].append(
                {
                    "case_id": case.id,
                    "mode": run.review_mode,
                    "run_id": run.id,
                    "error_class": info.get("error_class"),
                    "error": info.get("error"),
                }
            )
        if mode == "joint_review" and run.status == "succeeded":
            summary["totals"]["joint_succeeded"] = 1
        if mode == "baseline_review" and run.status == "succeeded":
            summary["totals"]["baseline_succeeded"] = 1

    for entry in summary["run_pairs"].values():
        mode = entry["mode"]
        state = (entry["status"] or "not_run") + (
            "_failed" if entry.get("error") else ""
        )
        summary["totals"]["status_distribution"][mode] = state
    summary["failure_count"] = len(summary["failures"])
    summary["binding_match"] = binding_match

    output_dir = output_root or (
        Path(__file__).resolve().parents[3]
        / "data_app_chain"
        / "runs"
        / f"t17_eval_{uuid.uuid4().hex[:8]}"
    )

    _write_json_atomic(output_dir / "evaluation_summary.json", summary)
    _write_detail_csv(output_dir / "evaluation_detail.csv", detail_rows)
    _write_failure_cases(
        output_dir / "failure_cases.md",
        detail_rows,
        pair_errors,
    )

    eval_run.status = "succeeded"
    eval_run.summary = summary
    eval_run.finished_at = _now_iso()
    db.commit()
    return eval_run, output_dir
