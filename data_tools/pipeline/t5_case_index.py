"""t5: 首版场景筛选与病例索引。

输入 t4 脱敏消息表和原始微医 parquet（用于科室/标签/问题正文，不导出患者原文）。
输出全量病例索引、候选范围 CSV 与审计报告。首版只验证中文成人呼吸道咨询，
年龄字段缺失时不默认成人，所有呼吸道候选标记为需人工复核。
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from common import file_meta, make_run_dir, project_root, utc_now_iso, write_json


RULE_VERSION = "t5-case-index-v1"

DEPT_MARKER = "呼吸"

RESP_STRONG_KEYWORDS = [
    "咳嗽",
    "干咳",
    "咳痰",
    "咳血",
    "咯血",
    "痰",
    "支气管炎",
    "支气管",
    "肺炎",
    "肺部感染",
    "呼吸道",
    "上呼吸道",
    "哮喘",
    "气喘",
    "喘息",
    "气促",
    "呼吸困难",
    "鼻塞",
    "流鼻涕",
    "流涕",
    "咽痛",
    "咽喉痛",
    "喉咙痛",
    "喉咙",
    "咽炎",
    "喉炎",
    "扁桃体",
    "扁桃腺",
    "肺结节",
    "肺部结节",
]

RESP_GENERAL_KEYWORDS = ["发烧", "发热", "感冒", "流感"]

AGE_PATTERN = re.compile(r"\d{1,3}\s*岁|成人|成年|周岁")


def _list_positions(values: set[str], keywords: list[str]) -> list[str]:
    found: list[str] = []
    for word in keywords:
        if any(word in v for v in values):
            found.append(word)
    return found


def _question_terms(text: str) -> tuple[int, int, list[str], list[str]]:
    strong_hits = [w for w in RESP_STRONG_KEYWORDS if w in text]
    general_hits = [w for w in RESP_GENERAL_KEYWORDS if w in text]
    return len(strong_hits), len(general_hits), strong_hits, general_hits


def _fmt(flag: bool) -> str:
    return "true" if flag else "false"


def _short(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main() -> int:
    root = project_root()
    current = json.loads(
        (root / "data_app_chain" / "current_run.json").read_text(encoding="utf-8")
    )
    deid_path = current.get("current_t4_deidentified_messages")
    if not deid_path or not Path(deid_path).exists():
        print("t5 前置产物缺失: 先运行 t4_deidentify_messages.py", file=sys.stderr)
        return 2

    raw_candidates = list((root / "raw_input").glob("*.parquet"))
    if not raw_candidates:
        print("t5 前置原始 parquet 缺失: raw_input 下没有 parquet", file=sys.stderr)
        return 2
    raw_path = raw_candidates[0]

    run_dir = make_run_dir("t5")
    report: dict[str, Any] = {
        "run": utc_now_iso(),
        "task": "t5_case_index",
        "status": "completed",
        "rule_version": RULE_VERSION,
        "input_deidentified_messages": file_meta(Path(deid_path)),
        "input_raw_parquet": file_meta(raw_path),
        "output_case_index": None,
        "total_consults": None,
        "consult_qid_mismatch_count": None,
        "with_patient_text_count": None,
        "with_doctor_answers_count": None,
        "with_2plus_doctor_count": None,
        "respiratory_candidate_count": None,
        "candidate_requires_manual_review_count": None,
        "joint_review_eligible_count": None,
        "respiratory_candidate_and_joint_eligible_count": None,
        "signal_breakdown": {},
        "exclusion_reason_counts": {},
        "candidate_samples": [],
        "risks": [],
        "not_verified": [],
    }

    con = duckdb.connect(":memory:")
    deid_sql = str(Path(deid_path)).replace("\\", "/").replace("'", "''")
    raw_sql = str(raw_path).replace("\\", "/").replace("'", "''")
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW msg AS SELECT * FROM read_parquet('{deid_sql}')"
    )
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW raw_t AS SELECT * FROM read_parquet('{raw_sql}')"
    )

    consult_rows = con.execute(
        """
        SELECT consult_id,
               min(source_question_id) AS min_qid,
               max(source_question_id) AS max_qid,
               count(*) FILTER (WHERE role = 'patient') AS patient_count,
               count(*) FILTER (
                   WHERE role = 'patient' AND length(trim(text)) > 0
               ) AS patient_text_count,
               count(*) FILTER (WHERE role = 'doctor') AS doctor_count,
               count(DISTINCT CASE WHEN role = 'doctor'
                   THEN source_doctor_user_id END) AS doctor_ids,
               count(*) FILTER (WHERE needs_review) AS needs_review_count,
               count(*) FILTER (
                   WHERE needs_review AND contains(review_reason, '时间戳')
               ) AS same_timestamp_count,
               count(*) FILTER (
                   WHERE needs_review AND contains(review_reason, '冲突')
               ) AS conflict_count
        FROM msg
        GROUP BY consult_id
        ORDER BY consult_id
        """
    ).fetchall()

    question_rows = con.execute(
        """
        SELECT question_id,
               min(question_desc) AS question_desc,
               count(DISTINCT question_desc) AS question_desc_distinct,
               string_agg(DISTINCT dept, ' | ') AS dept_values,
               string_agg(DISTINCT tags, ' | ') AS tag_values
        FROM raw_t
        GROUP BY question_id
        ORDER BY question_id
        """
    ).fetchall()

    question_map: dict[int, dict[str, Any]] = {}
    for qid, qdesc, desc_distinct, depts, tags in question_rows:
        question_map[qid] = {
            "question_desc": qdesc or "",
            "desc_distinct_count": desc_distinct,
            "dept_values": set(v for v in (depts or "").split(" | ") if v),
            "tag_values": set(v for v in (tags or "").split(" | ") if v),
        }

    rows: list[dict[str, Any]] = []
    qid_mismatch = 0
    signal_breakdown = {
        "dept_match": 0,
        "tag_match": 0,
        "question_strong_match": 0,
        "question_general_only_match": 0,
    }
    exclusion_reason_counts: dict[str, int] = {}

    for consult_row in consult_rows:
        (
            consult_id,
            min_qid,
            max_qid,
            patient_count,
            patient_text_count,
            doctor_count,
            doctor_ids,
            needs_review_count,
            same_timestamp_count,
            conflict_count,
        ) = consult_row
        if min_qid != max_qid:
            qid_mismatch += 1
        qid = min_qid
        meta = question_map.get(qid, {})
        qdesc = meta.get("question_desc", "")

        dept_values: set[str] = meta.get("dept_values", set())
        tag_values: set[str] = meta.get("tag_values", set())
        dept_match = any(DEPT_MARKER in v for v in dept_values)
        tag_hits = _list_positions(
            tag_values, RESP_STRONG_KEYWORDS + RESP_GENERAL_KEYWORDS
        )
        tag_match = bool(tag_hits)
        strong_count, general_count, _strong_hits, _general_hits = _question_terms(
            qdesc
        )
        question_match = strong_count >= 1 or general_count >= 2

        respiratory_candidate = dept_match or tag_match or question_match
        if dept_match:
            signal_breakdown["dept_match"] += 1
        if tag_match:
            signal_breakdown["tag_match"] += 1
        if strong_count >= 1:
            signal_breakdown["question_strong_match"] += 1
        if question_match and strong_count == 0:
            signal_breakdown["question_general_only_match"] += 1

        age_mention = bool(AGE_PATTERN.search(qdesc))
        age_evidence = (
            "mentions_age_text_but_patient_unverified"
            if age_mention
            else "unknown_no_age_field"
        )
        candidate_requires_manual_review = (
            respiratory_candidate and age_evidence != "verified_adult"
        )

        joint_eligible = doctor_ids >= 2 and needs_review_count == 0

        reason_parts: list[str] = []
        if not respiratory_candidate:
            reason_parts.append(
                "not_respiratory_v1(dept=false;tag=false;question=false)"
            )
        else:
            reason_parts.append("respiratory_candidate")
            if dept_match:
                reason_parts.append("dept_match=true")
            if tag_match:
                reason_parts.append("tag_match=true")
            if strong_count >= 1:
                reason_parts.append(
                    "question_strong_match=true(hits=%d)" % strong_count
                )
            if question_match and strong_count == 0:
                reason_parts.append(
                    "question_general_match=true(hits=%d)" % general_count
                )
            reason_parts.append("age_evidence=%s" % age_evidence)
            if candidate_requires_manual_review:
                reason_parts.append("candidate_requires_manual_review")
            if doctor_ids < 2:
                reason_parts.append(
                    "joint_review_ineligible(doctor_ids=%d)" % doctor_ids
                )
            if needs_review_count:
                reason_parts.append(
                    "joint_review_ineligible(order_unknown=%d)" % needs_review_count
                )
            reason_parts.append("joint_review_eligible=%s" % _fmt(joint_eligible))

        scope_status = (
            "candidate_requires_manual_review"
            if respiratory_candidate
            else "not_in_scope"
        )
        if respiratory_candidate:
            if doctor_ids < 2:
                exclusion_reason_counts["respiratory_candidate_doctor_lt_2"] = (
                    exclusion_reason_counts.get(
                        "respiratory_candidate_doctor_lt_2", 0
                    )
                    + 1
                )
            if needs_review_count:
                exclusion_reason_counts["respiratory_candidate_order_unknown"] = (
                    exclusion_reason_counts.get(
                        "respiratory_candidate_order_unknown", 0
                    )
                    + 1
                )
        else:
            exclusion_reason_counts["not_respiratory_v1"] = (
                exclusion_reason_counts.get("not_respiratory_v1", 0) + 1
            )

        rows.append(
            {
                "consult_id": consult_id,
                "source_question_id": qid,
                "patient_message_count": patient_count,
                "patient_text_available": patient_text_count > 0,
                "doctor_answer_count": doctor_count,
                "identified_doctor_count": doctor_ids,
                "dept_values": " | ".join(sorted(dept_values)),
                "tag_values": " | ".join(sorted(tag_values)),
                "question_desc_available": bool(qdesc.strip()),
                "respiratory_dept_match": dept_match,
                "respiratory_tag_match": tag_match,
                "respiratory_question_strong_match": strong_count >= 1,
                "respiratory_question_general_match": (
                    question_match and strong_count == 0
                ),
                "respiratory_candidate": respiratory_candidate,
                "age_evidence": age_evidence,
                "candidate_requires_manual_review": candidate_requires_manual_review,
                "order_unknown_message_count": needs_review_count,
                "same_timestamp_message_count": same_timestamp_count,
                "order_conflict_message_count": conflict_count,
                "visibility_exclusion_count": needs_review_count,
                "joint_review_eligible": joint_eligible,
                "scope_status": scope_status,
                "scope_reason": "; ".join(reason_parts),
            }
        )

    total = len(rows)
    report["total_consults"] = total
    report["consult_qid_mismatch_count"] = qid_mismatch
    report["with_patient_text_count"] = sum(
        1 for r in rows if r["patient_text_available"]
    )
    report["with_doctor_answers_count"] = sum(
        1 for r in rows if r["doctor_answer_count"] > 0
    )
    report["with_2plus_doctor_count"] = sum(
        1 for r in rows if r["identified_doctor_count"] >= 2
    )
    report["respiratory_candidate_count"] = sum(
        1 for r in rows if r["respiratory_candidate"]
    )
    report["candidate_requires_manual_review_count"] = sum(
        1 for r in rows if r["candidate_requires_manual_review"]
    )
    report["joint_review_eligible_count"] = sum(
        1 for r in rows if r["joint_review_eligible"]
    )
    report["respiratory_candidate_and_joint_eligible_count"] = sum(
        1 for r in rows if r["respiratory_candidate"] and r["joint_review_eligible"]
    )
    report["signal_breakdown"] = signal_breakdown
    report["exclusion_reason_counts"] = exclusion_reason_counts

    out_parquet = run_dir / "case_index.parquet"
    path_sql = str(out_parquet).replace("\\", "/").replace("'", "''")
    con.register("case_rows", pd.DataFrame(rows))
    con.execute(
        f"COPY (SELECT * FROM case_rows ORDER BY consult_id) TO "
        f"'{path_sql}' (FORMAT PARQUET)"
    )
    report["output_case_index"] = {
        "path": str(out_parquet),
        "size_bytes": out_parquet.stat().st_size,
    }

    scope_path = run_dir / "case_scope.csv"
    with scope_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "consult_id",
                "source_question_id",
                "doctor_answer_count",
                "identified_doctor_count",
                "order_unknown_message_count",
                "visibility_exclusion_count",
                "respiratory_candidate",
                "age_evidence",
                "candidate_requires_manual_review",
                "joint_review_eligible",
                "scope_status",
                "scope_reason",
                "dept_values_short",
                "tag_values_short",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "consult_id": row["consult_id"],
                    "source_question_id": row["source_question_id"],
                    "doctor_answer_count": row["doctor_answer_count"],
                    "identified_doctor_count": row["identified_doctor_count"],
                    "order_unknown_message_count": row["order_unknown_message_count"],
                    "visibility_exclusion_count": row["visibility_exclusion_count"],
                    "respiratory_candidate": row["respiratory_candidate"],
                    "age_evidence": row["age_evidence"],
                    "candidate_requires_manual_review": row[
                        "candidate_requires_manual_review"
                    ],
                    "joint_review_eligible": row["joint_review_eligible"],
                    "scope_status": row["scope_status"],
                    "scope_reason": row["scope_reason"],
                    "dept_values_short": _short(row["dept_values"], 200),
                    "tag_values_short": _short(row["tag_values"], 200),
                }
            )

    candidates = [r for r in rows if r["respiratory_candidate"]]
    eligible = [r for r in candidates if r["joint_review_eligible"]]
    eligible.sort(
        key=lambda r: (
            -r["doctor_answer_count"],
            -r["identified_doctor_count"],
            r["order_unknown_message_count"],
        )
    )
    report["candidate_samples"] = [
        {
            "consult_id": r["consult_id"],
            "source_question_id": r["source_question_id"],
            "doctor_answer_count": r["doctor_answer_count"],
            "identified_doctor_count": r["identified_doctor_count"],
            "order_unknown_message_count": r["order_unknown_message_count"],
            "joint_review_eligible": r["joint_review_eligible"],
            "scope_reason": r["scope_reason"],
        }
        for r in eligible[:20]
    ]
    for r in eligible[:8]:
        print(
            f"eligible: {r['consult_id']} "
            f"answers={r['doctor_answer_count']} "
            f"doctors={r['identified_doctor_count']}"
        )

    risks = [
        "首版呼吸道筛选为启发式：科室含“呼吸”优先，其次是标签与问题关键词；",
        "单次出现 发烧/发热/感冒/流感 且无科室/标签/其他呼吸道词时不计入候选，",
        "可能漏掉极简提问，需要人工补充验证。",
        "原始数据没有患者年龄字段，问题文本中的年龄描述可能指亲属，不能直接判定成人。",
        "visibility_exclusion_count 是病例级待排除消息代理数，精确逐焦点排除以 t6 manifest 为准。",
        "本步骤不依据职称或医院等级做排除，也不对医学结论做任何正确性判断。",
    ]
    not_verified = [
        "患者年龄与成人身份未核验；candidate_requires_manual_review 不是医学诊断结论。",
        "呼吸道初筛召回率未做标注评估，不能声称覆盖全部呼吸道咨询。",
        "joint_review_eligible 只代表病例级结构满足条件，不代表医生回答质量或观点正确。",
    ]
    report["risks"] = risks
    report["not_verified"] = not_verified
    write_json(run_dir / "case_index_report.json", report)

    summary = [
        "## t5 病例索引摘要",
        "",
        f"- 规则版本: {RULE_VERSION}",
        f"- 咨询总数: {total}",
        f"- 有患者文本: {report['with_patient_text_count']}",
        f"- 有医生回答: {report['with_doctor_answers_count']}",
        f"- 呼吸道候选: {report['respiratory_candidate_count']}",
        f"- 候选需人工复核（年龄未知）: {report['candidate_requires_manual_review_count']}",
        f"- 病例级联合审阅可用: {report['joint_review_eligible_count']}",
        f"- 呼吸道候选且联合审阅可用: "
        f"{report['respiratory_candidate_and_joint_eligible_count']}",
        f"- consult_id 与 question_id 不匹配: {report['consult_qid_mismatch_count']}",
        "",
        "信号拆分:",
        f"- 科室命中: {signal_breakdown['dept_match']}",
        f"- 标签命中: {signal_breakdown['tag_match']}",
        f"- 问题强关键词命中: {signal_breakdown['question_strong_match']}",
        f"- 问题仅泛关键词命中: {signal_breakdown['question_general_only_match']}",
    ]
    (run_dir / "audit_summary.md").write_text(
        "\n".join(summary) + "\n", encoding="utf-8"
    )

    current_ptr = json.loads(
        (root / "data_app_chain" / "current_run.json").read_text(encoding="utf-8")
    )
    current_ptr["current_t5_case_index"] = str(out_parquet)
    current_ptr["current_t5_report"] = str(run_dir / "case_index_report.json")
    current_ptr["last_run_utc"] = report["run"]
    current_ptr["status"] = report["status"]
    write_json(root / "data_app_chain" / "current_run.json", current_ptr)
    con.close()

    print(f"t5 完成: {out_parquet}")
    print(
        f"consults={total} candidates={report['respiratory_candidate_count']} "
        f"manual_review={report['candidate_requires_manual_review_count']} "
        f"joint_eligible={report['respiratory_candidate_and_joint_eligible_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
