"""Temporary helper: collect real deidentified Q&A groups for PPT selection."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pyarrow.dataset as ds


TASKS = Path(
    r"D:\Medipeer\data_app_chain\runs\t7_20261105T163029Z\review_tasks.jsonl"
)
SPLIT = Path(
    r"D:\Medipeer\data_app_chain\runs\t8_20261105T163144Z\split_manifest.csv"
)
DEID = Path(
    r"D:\Medipeer\data_app_chain\runs\t4_20261105T152301Z\deidentified_messages.parquet"
)
OUT = Path(r"D:\Medipeer\outputs\ppt_materials\candidate_real_qa_groups.md")


def main() -> None:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    with TASKS.open(encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("task_type") != "history_replay":
                continue
            groups[obj["consult_id"]].append(
                {
                    "answer_id": obj["focus_answer_id"],
                    "time": obj.get("cutoff_time", ""),
                    "text": obj.get("draft_text", ""),
                }
            )

    questions: dict[str, str] = {}
    counts: dict[str, int] = {}
    with SPLIT.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            questions[row["consult_id"]] = (
                row.get("duplicate_group_key") or row.get("selection_reason") or ""
            )
            counts[row["consult_id"]] = int(row["doctor_answer_count"])

    selected = [
        "wy_con_79476579656335362",
        "wy_con_100685336071405571",
        "wy_con_93464392783396864",
    ]

    df = pq.read_table(
        DEID,
        columns=[
            "message_id",
            "consult_id",
            "role",
            "text",
            "created_at",
            "source_record_id",
            "deid_status",
        ],
        filters=[("consult_id", "in", selected)],
    ).to_pandas()

    lines: list[str] = []
    for cid in selected:
        sub = df[df["consult_id"] == cid]
        patients = sub[sub["role"] == "patient"].sort_values("created_at")
        doctors = sub[sub["role"] == "doctor"].sort_values("created_at")
        question = (
            patients["text"].iloc[0]
            if len(patients)
            else questions.get(cid, "（脱敏表中没有患者消息）")
        )
        lines.append(f"## {cid}")
        lines.append(f"医生回答数(文件口径): {counts.get(cid)}；脱敏表实际医生消息: {len(doctors)}")
        lines.append(f"患者提问全文: {question}")
        lines.append("")
        for _, row in doctors.iterrows():
            text = str(row["text"]).replace("\n", " ")
            lines.append(
                f"- [{row['created_at']}] {row['message_id']} "
                f"(deid={row['deid_status']}): {text}"
            )
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    for cid in selected:
        sub = df[df["consult_id"] == cid]
        print(
            f"{cid}: doctor_messages={len(sub[sub['role']=='doctor'])}, "
            f"patient_messages={len(sub[sub['role']=='patient'])}"
        )


if __name__ == "__main__":
    main()
