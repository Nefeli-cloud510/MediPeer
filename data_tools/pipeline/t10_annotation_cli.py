"""t10: 双人独立标注工具与分歧裁定脚手架。

当前没有两名真人标注者, 因此默认模式只生成 pending 模板:
- annotations_a.jsonl / annotations_b.jsonl: 每个待审草稿句一个标注单位
- reference_labels.jsonl: 空文件（未裁决前不写入任何“参考标签”）
- annotation_disagreements.csv: 仅表头
- annotation_status.json: status=pending_manual_annotation

之后真人按 annotation_guideline.md 独立补填 A/B 文件, 再用 merge 模式
生成分歧表与裁决后参考标签。本工具不把模板数量当作人工完成数。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import make_run_dir, utc_now_iso, write_json


RULE_VERSION = "t10-annotation-scaffold-v1"


def _split_sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[。！？；;!?])", text or "") if p.strip()]


def _task_lines(tasks_path: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    with tasks_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def _pending_unit(task: dict[str, Any], sentence_idx: int, sentence: str, annotator: str) -> dict[str, Any]:
    return {
        "unit_id": f"u-{annotator.lower()}-{task['task_id']}-{sentence_idx:02d}",
        "task_id": task["task_id"],
        "consult_id": task["consult_id"],
        "focus_message_id": task["focus_message_id"],
        "synthetic": task.get("synthetic", False),
        "derivation_type": task.get("derivation_type", "real_history"),
        "draft_sentence_index": sentence_idx,
        "draft_sentence": sentence,
        "claim_type": None,
        "patient_grounding_status": None,
        "peer_contribution": None,
        "relation_to_prior": [],
        "evidence_refs": [],
        "suggested_action": None,
        "annotation_notes": None,
        "reviewed": False,
        "status": "pending_manual_annotation",
        "annotator": annotator,
        "created_at": utc_now_iso(),
    }


def _prepare(run_dir: Path, tasks_path: Path, guideline_path: Path, acceptance_path: Path) -> None:
    tasks = _task_lines(tasks_path)
    units_a: list[dict[str, Any]] = []
    units_b: list[dict[str, Any]] = []
    for task in tasks:
        sentences = _split_sentences(task.get("draft_text", ""))
        if not sentences:
            sentences = [str(task.get("draft_text", ""))]
        for idx, sent in enumerate(sentences):
            units_a.append(_pending_unit(task, idx, sent, "A"))
            units_b.append(_pending_unit(task, idx, sent, "B"))
    with (run_dir / "annotations_a.jsonl").open("w", encoding="utf-8") as fh:
        for u in units_a:
            fh.write(json.dumps(u, ensure_ascii=False, sort_keys=True) + "\n")
    with (run_dir / "annotations_b.jsonl").open("w", encoding="utf-8") as fh:
        for u in units_b:
            fh.write(json.dumps(u, ensure_ascii=False, sort_keys=True) + "\n")
    (run_dir / "reference_labels.jsonl").write_text("", encoding="utf-8")
    with (run_dir / "annotation_disagreements.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "unit_id",
                "task_id",
                "field",
                "annotator_a_value",
                "annotator_b_value",
                "adjudicated_value",
                "adjudication_reason",
                "status",
            ]
        )
    write_json(
        run_dir / "annotation_status.json",
        {
            "run": utc_now_iso(),
            "task": "t10_double_annotation",
            "status": "pending_manual_annotation",
            "rule_version": RULE_VERSION,
            "prepared_units": len(units_a),
            "annotator_a_reviewed": 0,
            "annotator_b_reviewed": 0,
            "reference_labels_count": 0,
            "disagreement_count": 0,
            "annotation_guideline": str(guideline_path),
            "acceptance_case": str(acceptance_path),
            "template_note": "模板数量不是人工标注完成数",
            "not_verified": [
                "未由两名真人独立完成标注",
                "未生成任何人工参考标签或评测准确率",
                "医学正确性相关内容未做专业复核",
            ],
        },
    )
    print(f"t10 prepare ok: {run_dir} ({len(units_a)} units per annotator, pending)")


def _merge(run_dir: Path, a_path: Path, b_path: Path, adjudications: Path | None) -> None:
    def load(path: Path) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                out[item["unit_id"]] = item
        return out

    a = load(a_path)
    b = load(b_path)
    dispute_rows: list[list[str]] = []
    reference_items: list[dict[str, Any]] = []
    fields = [
        "claim_type",
        "patient_grounding_status",
        "peer_contribution",
        "relation_to_prior",
        "evidence_refs",
        "suggested_action",
    ]
    adjudicated: dict[str, dict[str, Any]] = {}
    if adjudications and adjudications.exists():
        with adjudications.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    item = json.loads(line)
                    adjudicated[item["unit_id"]] = item
    for unit_id in sorted(set(a) | set(b)):
        if unit_id not in a or unit_id not in b:
            dispute_rows.append(
                [
                    unit_id,
                    "",
                    "missing_counterpart",
                    "" if unit_id not in a else "present",
                    "" if unit_id not in b else "present",
                    "",
                    "无法合并：缺任一标注",
                    "pending_adjudication",
                ]
            )
            continue
        for field in fields:
            va = a[unit_id].get(field)
            vb = b[unit_id].get(field)
            if va != vb:
                merged = adjudicated.get(unit_id, {}).get(field)
                reason = ""
                if merged is not None:
                    reason = "adjudicated_by_merge_input"
                dispute_rows.append(
                    [
                        unit_id,
                        a[unit_id].get("task_id", ""),
                        field,
                        json.dumps(va, ensure_ascii=False, sort_keys=True),
                        json.dumps(vb, ensure_ascii=False, sort_keys=True),
                        json.dumps(merged, ensure_ascii=False, sort_keys=True)
                        if merged is not None else "",
                        reason,
                        "adjudicated" if merged is not None else "pending_adjudication",
                    ]
                )
        base = dict(a[unit_id])
        base["status"] = "merged"
        reference_items.append(base)
    with (run_dir / "reference_labels.jsonl").open("w", encoding="utf-8") as fh:
        for item in reference_items:
            fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    with (run_dir / "annotation_disagreements.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "unit_id",
                "task_id",
                "field",
                "annotator_a_value",
                "annotator_b_value",
                "adjudicated_value",
                "adjudication_reason",
                "status",
            ]
        )
        writer.writerows(dispute_rows)
    pending = sum(1 for row in dispute_rows if row[7] == "pending_adjudication")
    write_json(
        run_dir / "annotation_status.json",
        {
            "run": utc_now_iso(),
            "task": "t10_double_annotation_merge",
            "status": "merged_if_all_adjudicated" if pending == 0 else "pending_adjudication",
            "rule_version": RULE_VERSION,
            "prepared_units": len(reference_items),
            "disagreement_rows": len(dispute_rows),
            "pending_adjudication_rows": pending,
            "reference_labels_count": len(reference_items),
        },
    )
    print(
        f"t10 merge ok: {len(reference_items)} units merged, "
        f"{len(dispute_rows)} disagreement rows, {pending} pending"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["prepare", "merge"], default="prepare")
    parser.add_argument("--tasks", help="dev/test/demo review tasks JSONL")
    parser.add_argument("--guideline", help="annotation_guideline.md path")
    parser.add_argument("--acceptance", help="acceptance_case_01.json path")
    parser.add_argument("--annotations-a")
    parser.add_argument("--annotations-b")
    parser.add_argument("--adjudications")
    parser.add_argument("--out")
    args = parser.parse_args()

    run_dir = Path(args.out) if args.out else make_run_dir("t10")
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "prepare":
        if not args.tasks or not Path(args.tasks).exists():
            print("--tasks 指向 dev/test 任务 JSONL", file=sys.stderr)
            return 2
        guideline = Path(args.guideline or "")
        acceptance = Path(args.acceptance or "")
        _prepare(run_dir, Path(args.tasks), guideline, acceptance)
        return 0

    if not args.annotations_a or not args.annotations_b:
        print("merge 需要 --annotations-a 与 --annotations-b", file=sys.stderr)
        return 2
    _merge(
        run_dir,
        Path(args.annotations_a),
        Path(args.annotations_b),
        Path(args.adjudications) if args.adjudications else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
