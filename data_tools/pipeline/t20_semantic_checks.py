"""t20 三轮验收的语义校验（不参与模型调用，只核对关键业务规则）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def run_checks(
    run_dir: Path,
    case_id: str,
    run_bodies: dict[str, dict[str, Any]],
    validations: dict[str, dict[str, Any]],
    supplement_message_id: str,
) -> dict[str, Any]:
    """Check the three rounds against the task's expected business rules.

    The model still determines concrete labels; these checks only enforce
    non-negotiable behaviors such as evidence presence and no confirmation
    without patient material.
    """
    checks: list[dict[str, Any]] = []

    def _add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "passes": bool(ok), "detail": detail})

    supplement_text = "我今天早上发过烧，现在没量。"

    def _msg_texts(run_key: str) -> dict[str, str]:
        return {
            message["id"]: message["text"]
            for message in (
                run_bodies.get("_replay_scopes", {}).get(run_key, {}).get(
                    "visible_messages", []
                )
            )
        }

    def _findings(run_key: str) -> list[dict[str, Any]]:
        return run_bodies[run_key].get("findings") or []

    def _finding_by_index(run_key: str, index: int) -> dict[str, Any] | None:
        findings = _findings(run_key)
        if index + 1 <= len(findings):
            return findings[index]
        # 模型若排序不同，按草稿句文本回退匹配。
        return next(
            (
                item
                for item in findings
                if str(item.get("draft_sentence_index", "")).endswith(str(index))
                or str(item.get("draft_sentence_id", "")) == f"ds-{index}"
            ),
            None,
        )

    def _evidence_refs(finding: dict[str, Any]) -> list[dict[str, Any]]:
        return finding.get("evidence_refs") or []

    def _has_patient_ref(finding: dict[str, Any]) -> bool:
        return any(
            ref.get("role") == "patient" and ref.get("snippet")
            for ref in _evidence_refs(finding)
        )

    round1 = _findings("round1")
    _add(
        "round1_two_findings",
        len(round1) == 2,
        f"findings={len(round1)}",
    )
    if len(round1) >= 2:
        first, second = round1[0], round1[1]
        _add(
            "round1_sputum_has_patient_evidence",
            "有痰" in str(first.get("draft_sentence_text") or "")
            and _has_patient_ref(first),
            f"text={first.get('draft_sentence_text')}",
        )
        second_grounding = second.get("patient_grounding") or ""
        second_refs = _evidence_refs(second)
        _add(
            "round1_fever_not_confirmed",
            "发热" in str(second.get("draft_sentence_text") or "")
            and second_grounding
            in {
                "doctor_question_only",
                "unmentioned",
                "patient_uncertain",
                "needs_review",
                "detail_unverified",
                "unknown",
            }
            and not any(
                ref.get("role") == "patient"
                and ref.get("snippet", "").find("没量") >= 0
                for ref in second_refs
            ),
            f"grounding={second_grounding}, refs={second_refs}",
        )

    round2 = _findings("round2")
    if len(round2) >= 2:
        sputum = next(
            (
                item
                for item in round2
                if "有痰" in str(item.get("draft_sentence_text") or "")
            ),
            None,
        )
        fever = next(
            (
                item
                for item in round2
                if "发热" in str(item.get("draft_sentence_text") or "")
            ),
            None,
        )
        _add(
            "round2_sputum_judgement_kept",
            sputum is not None and _has_patient_ref(sputum),
            f"text={sputum and sputum.get('draft_sentence_text')}",
        )
        _add(
            "round2_supplement_visible",
            supplement_message_id in _msg_texts("round2")
            and any(
                supplement_text in text
                for text in _msg_texts("round2").values()
            ),
            f"message_id={supplement_message_id}",
        )
        _add(
            "round2_supplement_not_treated_as_current_denial",
            fever is not None
            and not any(
                ref.get("message_id") == supplement_message_id
                for ref in _evidence_refs(fever)
            )
            and (fever.get("patient_grounding") or "") not in {
                "patient_stated",
                "patient_denied",
            },
            f"grounding={fever and fever.get('patient_grounding')}, "
            f"refs={fever and _evidence_refs(fever)}",
        )
        _add(
            "round2_no_fever_explanation_considers_supplement",
            fever is not None
            and any(
                marker in (fever.get("explanation") or "")
                for marker in ("今天早上", "早上发过烧", "发烧", "现在没量")
            ),
            f"explanation={fever and fever.get('explanation')}",
        )
        _add(
            "round2_current_fever_not_confirmed",
            fever is not None
            and (fever.get("patient_grounding") or "") not in {"patient_stated"},
            f"grounding={fever and fever.get('patient_grounding')}",
        )

    round3 = _findings("round3")
    changed = run_bodies.get("round3", {}).get("diff") or []
    _add(
        "round3_diff_present",
        bool(changed),
        f"diff_items={len(changed)}",
    )
    for item in changed:
        if "无发热" in str(item.get("draft_sentence_text") or "") and item.get(
            "change"
        ) in {"resolved", "changed", "removed"}:
            _add(
                "round3_no_fever_prompt_resolved",
                True,
                "原无发热提示已因改稿解决",
            )
            break
    else:
        _add(
            "round3_no_fever_prompt_resolved",
            False,
            "未找到“无发热”提示的解除记录",
        )

    _add(
        "evidence_validation_all_pass",
        all(
            validation.get("passes")
            for validation in validations.values()
            if isinstance(validation, dict)
        ),
        json.dumps(validations, ensure_ascii=False, default=str),
    )

    summary = {
        "task": "t20_semantic_acceptance",
        "case_id": case_id,
        "checks": checks,
        "all_pass": all(check["passes"] for check in checks),
    }
    (run_dir / "semantic_checks.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return summary
