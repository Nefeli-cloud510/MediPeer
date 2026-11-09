"""t8: 划分开发、测试与演示集合。

按完整咨询划分, 同一咨询的所有医生回答和人工改写副本只进入一个集合;
人工改写副本随后续咨询分组。固定随机种子, 每个入选案例记录选择理由。
文档化近重复启发式: 患者问题正文去除空白后的前 60 字作为分组前缀,
同一前缀的咨询尽量归入同一集合（不跨集）。该启发式不等同于语义聚类。
"""

from __future__ import annotations

import csv
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from common import file_meta, make_run_dir, project_root, utc_now_iso, write_json


RULE_VERSION = "t8-split-v1"
SEED = 20261105
DEV_TARGET = 20
TEST_TARGET = 40
DEMO_TARGET = 3


def _prefix(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")[:60])


def main() -> int:
    root = project_root()
    current = json.loads(
        (root / "data_app_chain" / "current_run.json").read_text(encoding="utf-8")
    )
    case_path = current.get("current_t5_case_index")
    manifest_path = current.get("current_t6_replay_manifest")
    deid_path = current.get("current_t4_deidentified_messages")
    tasks_path = current.get("current_t7_review_tasks")
    for key, path in [
        ("case_index", case_path),
        ("replay_manifest", manifest_path),
        ("deidentified_messages", deid_path),
    ]:
        if not path or not Path(path).exists():
            print(f"t8 前置产物缺失: {key}", file=sys.stderr)
            return 2

    con = duckdb.connect(":memory:")
    idx = con.execute(
        "SELECT consult_id, source_question_id, respiratory_candidate, "
        "joint_review_eligible, candidate_requires_manual_review, "
        "doctor_answer_count, identified_doctor_count, patient_message_count, "
        "same_timestamp_message_count, order_conflict_message_count, "
        "order_unknown_message_count "
        f"FROM read_parquet('{str(Path(case_path)).replace(chr(92), '/')}')"
    ).df()
    manifest = con.execute(
        "SELECT focus_message_id, consult_id, focus_order_unreliable "
        f"FROM read_parquet('{str(Path(manifest_path)).replace(chr(92), '/')}')"
    ).df()
    msgs = con.execute(
        "SELECT consult_id, message_id, role, text "
        f"FROM read_parquet('{str(Path(deid_path)).replace(chr(92), '/')}')"
    ).df()
    patient_texts = msgs[msgs["role"] == "patient"].groupby("consult_id")["text"].agg(
        lambda x: "\n".join(str(v) for v in x)
    )
    # 每个咨询的待核查 focus 数: 同时间戳/冲突时间焦点
    focus_review = (
        manifest[manifest["focus_order_unreliable"]]
        .groupby("consult_id")
        .size()
        .rename("unreliable_focus_count")
    )

    idx = idx[
        (idx["respiratory_candidate"]) & (idx["joint_review_eligible"])
    ].copy()
    idx["candidate_requires_manual_review"] = idx[
        "candidate_requires_manual_review"
    ].fillna(True).astype(bool)
    idx["patient_text_chars"] = idx["consult_id"].map(patient_texts).fillna("").apply(len)
    idx["duplicate_group_key"] = idx["consult_id"].map(patient_texts).fillna("").apply(_prefix)
    idx["unreliable_focus_count"] = idx["consult_id"].map(focus_review).fillna(0).astype(int)

    rng = random.Random(SEED)
    assigned: dict[str, str] = {}
    reasons: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for _, row in idx.iterrows():
        groups.setdefault(row["duplicate_group_key"], []).append(row["consult_id"])

    def take_group_whole(group_key: str, split: str, reason: str) -> None:
        for cid in groups.get(group_key, []):
            if cid not in assigned:
                assigned[cid] = split
                reasons[cid] = reason

    def sample_group(pool: list[str], count: int, split: str) -> int:
        chosen = rng.sample(pool, min(count, len(pool)))
        for gk in chosen:
            take_group_whole(gk, split, f"fixed_seed_{SEED}_{split}_sample")
        return len(chosen)

    def group_has(predicate) -> list[str]:
        out = []
        for gk, cids in groups.items():
            if any(predicate(cid) for cid in cids):
                out.append(gk)
        return out

    def row_map(cid: str) -> pd.Series:
        return idx[idx["consult_id"] == cid].iloc[0]

    # 演示集: 三个能力覆盖
    demo_buckets = [
        ("2-3 位医生、患者材料较短（信息不足演示）", lambda r: 2 <= r["doctor_answer_count"] <= 3 and r["patient_text_chars"] <= 140),
        ("≥6 位医生（多医生观点演示）", lambda r: r["doctor_answer_count"] >= 6),
        ("4-6 位医生且识别医生数 ≥3（信息更新演示）", lambda r: 4 <= r["doctor_answer_count"] <= 6 and r["identified_doctor_count"] >= 3),
    ]
    usable = [cid for cid in idx["consult_id"] if cid not in assigned]
    for reason_label, pred in demo_buckets:
        pool = [gk for gk in groups if any(pred(row_map(cid)) for cid in groups[gk])]
        if pool:
            gk = rng.sample(pool, 1)[0]
            take_group_whole(gk, "demo", f"demo_capability:{reason_label}")

    # 开发集: 优先覆盖时间待核查, 再随机补齐
    priority_pool = group_has(
        lambda cid: row_map(cid)["same_timestamp_message_count"] > 0
        or row_map(cid)["order_conflict_message_count"] > 0
    )
    sample_group(priority_pool, DEV_TARGET - 6, "dev")
    remaining = [gk for gk in groups if not any(cid in assigned for cid in groups[gk])]
    sample_group(remaining, DEV_TARGET, "dev")
    # 测试集
    remaining = [gk for gk in groups if not any(cid in assigned for cid in groups[gk])]
    sample_group(remaining, TEST_TARGET, "test")

    rows_out: list[dict[str, Any]] = []
    for _, row in idx.iterrows():
        cid = row["consult_id"]
        split = assigned.get(cid, "unassigned")
        rows_out.append(
            {
                "consult_id": cid,
                "source_question_id": row["source_question_id"],
                "split": split,
                "fixed_seed": SEED,
                "doctor_answer_count": row["doctor_answer_count"],
                "identified_doctor_count": row["identified_doctor_count"],
                "patient_text_chars": row["patient_text_chars"],
                "same_timestamp_message_count": row["same_timestamp_message_count"],
                "order_conflict_message_count": row["order_conflict_message_count"],
                "order_unknown_message_count": row["order_unknown_message_count"],
                "unreliable_focus_count": row["unreliable_focus_count"],
                "duplicate_group_key": row["duplicate_group_key"],
                "candidate_requires_manual_review": bool(
                    row["candidate_requires_manual_review"]
                ),
                "selection_reason": reasons.get(cid, "unassigned"),
            }
        )

    out_df = pd.DataFrame(rows_out)
    run_dir = make_run_dir("t8")
    split_csv = run_dir / "split_manifest.csv"
    out_df.to_csv(split_csv, index=False, encoding="utf-8-sig")
    report: dict[str, Any] = {
        "run": utc_now_iso(),
        "task": "t8_split_manifest",
        "status": "completed",
        "rule_version": RULE_VERSION,
        "seed": SEED,
        "input_case_index": file_meta(Path(case_path)),
        "input_replay_manifest": file_meta(Path(manifest_path)),
        "output_split_manifest": str(split_csv),
        "target_counts": {
            "demo": DEMO_TARGET,
            "dev": DEV_TARGET,
            "test": TEST_TARGET,
        },
        "actual_counts": out_df["split"].value_counts().to_dict(),
        "cross_split_duplicate_group": int(
            out_df.groupby("duplicate_group_key")["split"].nunique().gt(1).sum()
        ),
        "sets_overlap": {},
        "coverage": {
            "dev_unreliable_focus_cases": int(
                (
                    (out_df["split"] == "dev")
                    & (out_df["same_timestamp_message_count"] > 0)
                ).sum()
            ),
            "test_unreliable_focus_cases": int(
                (
                    (out_df["split"] == "test")
                    & (out_df["same_timestamp_message_count"] > 0)
                ).sum()
            ),
            "demo_answer_count_hist": (
                out_df.loc[out_df["split"] == "demo", "doctor_answer_count"]
                .value_counts()
                .sort_index()
                .to_dict()
            ),
        },
        "demo_case_ids": sorted(out_df.loc[out_df["split"] == "demo", "consult_id"]),
        "risks": [
            "近重复启发式仅用前 60 字前缀，未做语义聚类，可能漏掉改写后近重复",
            "年龄适用性仍为候选项，需人工复核后再用于正式演示/评测",
        ],
        "not_verified": [
            "未对三集合做逐题语义去重",
            "测试集锁定句为一次划分，后续不得因指标调整而反复更换样本",
        ],
    }
    for a in ["demo", "dev", "test"]:
        ids_a = set(out_df.loc[out_df["split"] == a, "consult_id"])
        report["sets_overlap"][a] = {
            b: len(ids_a & set(out_df.loc[out_df["split"] == b, "consult_id"]))
            for b in ["demo", "dev", "test"]
        }

    if tasks_path and Path(tasks_path).exists():
        split_task_dir = run_dir / "task_sets"
        split_task_dir.mkdir(exist_ok=True)
        task_split: dict[str, list[dict[str, Any]]] = {"dev": [], "test": [], "demo": []}
        with Path(tasks_path).open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                task = json.loads(line)
                split = assigned.get(task.get("consult_id"), "unassigned")
                if split in task_split:
                    task_split[split].append(task)
        for split, items in task_split.items():
            (split_task_dir / f"{split}_tasks.jsonl").write_text(
                "".join(json.dumps(t, ensure_ascii=False, sort_keys=True) + "\n" for t in items),
                encoding="utf-8",
            )
        report["task_sets"] = {
            k: str(split_task_dir / f"{k}_tasks.jsonl") for k in task_split
        }
        report["task_counts"] = {k: len(v) for k, v in task_split.items()}

    write_json(run_dir / "split_report.json", report)
    print(f"t8 ok: {split_csv}")
    print(json.dumps(report["actual_counts"], ensure_ascii=False))
    print("demo:", report["demo_case_ids"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
