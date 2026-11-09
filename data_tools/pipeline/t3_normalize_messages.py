"""t3: 生成独立的规范化消息表。

只读读取合并版微医问答样本，将问题和回答转换为统一消息：
问题文本 -> patient 消息，回答文本 -> doctor 消息，同 question_id 进入同一
consult_id。保留原文、时间、来源映射和 needs_review 标记；研究派生列不作为
业务输入。输出 normalized_messages.parquet 及审计报告。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

import duckdb

from common import file_meta, make_run_dir, project_root, utc_now_iso, write_json


INPUT_RELATIVE = "raw_input/major5_stratified_question_samples_answers.parquet"
RULE_VERSION = "t3-v1"
PLATFORM = "weiyi"


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, params: tuple = ()) -> Any:
    return con.execute(sql, params).fetchone()[0]


def _compute_normalized(con: duckdb.DuckDBPyConnection, out_path: Path) -> None:
    out_sql = str(out_path).replace("\\", "/").replace("'", "''")
    con.execute(
        f"""
        COPY (
            WITH dup AS (
                SELECT question_id, seq, COUNT(*) AS c
                FROM src
                GROUP BY question_id, seq
                HAVING COUNT(*) > 1
            ),
            same_ts AS (
                SELECT question_id, answer_create_time
                FROM src
                GROUP BY question_id, answer_create_time
                HAVING COUNT(*) > 1
            ),
            patient_msgs AS (
                SELECT
                    CONCAT('wy_msg_', CAST(question_id AS VARCHAR), '_q') AS message_id,
                    CONCAT('wy_con_', CAST(question_id AS VARCHAR)) AS consult_id,
                    CONCAT('wy_que_', CAST(question_id AS VARCHAR)) AS source_record_id,
                    'patient' AS role,
                    CONCAT('wy_pat_', CAST(question_id AS VARCHAR)) AS speaker_id,
                    question_desc AS text,
                    MIN(question_create_time) AS created_at,
                    'question_create_time' AS order_basis,
                    FALSE AS needs_review,
                    '' AS review_reason,
                    '{PLATFORM}' AS source_platform,
                    '{RULE_VERSION}' AS extraction_rule_version,
                    question_id AS source_question_id,
                    CAST(NULL AS BIGINT) AS source_seq,
                    CAST(NULL AS VARCHAR) AS source_doctor_user_id
                FROM src
                GROUP BY question_id, question_desc
            ),
            doctor_msgs AS (
                SELECT
                    CONCAT('wy_msg_', CAST(s.question_id AS VARCHAR), '_',
                           CAST(s.seq AS VARCHAR), '_', s.user_id) AS message_id,
                    CONCAT('wy_con_', CAST(s.question_id AS VARCHAR)) AS consult_id,
                    CONCAT('wy_ans_', CAST(s.question_id AS VARCHAR), '_',
                           CAST(s.seq AS VARCHAR), '_', s.user_id) AS source_record_id,
                    'doctor' AS role,
                    s.user_id AS speaker_id,
                    s.answer AS text,
                    s.answer_create_time AS created_at,
                    'answer_create_time' AS order_basis,
                    (d.question_id IS NOT NULL OR t.question_id IS NOT NULL) AS needs_review,
                    CASE
                        WHEN d.question_id IS NOT NULL AND t.question_id IS NOT NULL
                            THEN 'question_id+seq 冲突且同时间戳，需人工核对原始回答记录'
                        WHEN d.question_id IS NOT NULL
                            THEN 'question_id+seq 冲突，需人工核对原始回答记录'
                        WHEN t.question_id IS NOT NULL
                            THEN '同问题下多回答共享同一时间戳，顺序未知，不作为彼此前序'
                        ELSE ''
                    END AS review_reason,
                    '{PLATFORM}' AS source_platform,
                    '{RULE_VERSION}' AS extraction_rule_version,
                    s.question_id AS source_question_id,
                    s.seq AS source_seq,
                    s.user_id AS source_doctor_user_id
                FROM src s
                LEFT JOIN dup d
                    ON d.question_id = s.question_id
                   AND d.seq = s.seq
                LEFT JOIN same_ts t
                    ON t.question_id = s.question_id
                   AND t.answer_create_time = s.answer_create_time
            )
            SELECT * FROM (
                SELECT * FROM patient_msgs
                UNION ALL
                SELECT * FROM doctor_msgs
            )
            ORDER BY consult_id,
                     CASE role WHEN 'patient' THEN 0 ELSE 1 END,
                     created_at,
                     message_id
        ) TO '{out_sql}' (FORMAT PARQUET)
        """
    )


def _verify(con: duckdb.DuckDBPyConnection, out_path: Path) -> dict[str, Any]:
    out_sql = str(out_path).replace("\\", "/").replace("'", "''")
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW norm AS "
        f"SELECT * FROM read_parquet('{out_sql}')"
    )
    total = _scalar(con, "SELECT COUNT(*) FROM norm")
    unique_msg = _scalar(
        con, "SELECT COUNT(DISTINCT message_id) FROM norm"
    )
    unique_consult = _scalar(
        con, "SELECT COUNT(DISTINCT consult_id) FROM norm"
    )
    role_counts = {
        str(role): count
        for role, count in con.execute(
            "SELECT role, COUNT(*) FROM norm GROUP BY role ORDER BY role"
        ).fetchall()
    }
    review_counts = {
        str(flag): count
        for flag, count in con.execute(
            "SELECT needs_review, COUNT(*) FROM norm "
            "GROUP BY needs_review ORDER BY needs_review"
        ).fetchall()
    }
    review_reasons = {
        str(reason): count
        for reason, count in con.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(review_reason), ''), '(无)'), COUNT(*)
            FROM norm
            GROUP BY 1
            ORDER BY 2 DESC
            """
        ).fetchall()
    }
    patient_per_consult = con.execute(
        """
        SELECT MIN(c), MAX(c) FROM (
            SELECT consult_id, COUNT(*) AS c
            FROM norm
            WHERE role = 'patient'
            GROUP BY consult_id
        )
        """
    ).fetchone()
    doctor_per_consult = con.execute(
        """
        SELECT MIN(c), MAX(c) FROM (
            SELECT consult_id, COUNT(*) AS c
            FROM norm
            WHERE role = 'doctor'
            GROUP BY consult_id
        )
        """
    ).fetchone()
    msg_per_consult = con.execute(
        """
        SELECT MIN(c), MAX(c) FROM (
            SELECT consult_id, COUNT(*) AS c FROM norm GROUP BY consult_id
        )
        """
    ).fetchone()

    doctor_text_mismatch = _scalar(
        con,
        """
        SELECT COUNT(*) FROM norm n
        JOIN src s
          ON s.question_id = n.source_question_id
         AND s.seq = n.source_seq
         AND s.user_id = n.source_doctor_user_id
        WHERE n.role = 'doctor'
          AND (n.text <> s.answer OR n.created_at <> s.answer_create_time)
        """,
    )
    doctor_rows = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT n.source_question_id, n.source_seq, n.source_doctor_user_id
            FROM norm n WHERE n.role = 'doctor'
            GROUP BY 1, 2, 3
        )
        """,
    )
    doctor_unmatched = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT n.source_question_id, n.source_seq, n.source_doctor_user_id
            FROM norm n
            WHERE n.role = 'doctor'
              AND NOT EXISTS (
                  SELECT 1 FROM src s
                  WHERE s.question_id = n.source_question_id
                    AND s.seq = n.source_seq
                    AND s.user_id = n.source_doctor_user_id
              )
        )
        """,
    )
    patient_text_mismatch = _scalar(
        con,
        """
        SELECT COUNT(*) FROM norm n
        WHERE n.role = 'patient'
          AND EXISTS (
              SELECT 1 FROM src s
              WHERE s.question_id = n.source_question_id
                AND (s.question_desc <> n.text
                     OR s.question_create_time <> n.created_at)
          )
        """,
    )
    patient_rows = _scalar(
        con, "SELECT COUNT(*) FROM norm WHERE role = 'patient'"
    )
    source_records = _scalar(
        con, "SELECT COUNT(DISTINCT source_record_id) FROM norm"
    )

    review_reason_rows = con.execute(
        """
        SELECT message_id, consult_id, source_record_id, role, speaker_id,
               created_at, needs_review, review_reason
        FROM norm
        WHERE needs_review
        ORDER BY consult_id, created_at, message_id
        """
    ).fetchall()

    return {
        "total_messages": total,
        "unique_message_ids": unique_msg,
        "unique_consult_ids": unique_consult,
        "role_counts": role_counts,
        "needs_review_counts": review_counts,
        "review_reason_distribution": review_reasons,
        "patient_messages_per_consult": {
            "min": patient_per_consult[0],
            "max": patient_per_consult[1],
        },
        "doctor_messages_per_consult": {
            "min": doctor_per_consult[0],
            "max": doctor_per_consult[1],
        },
        "messages_per_consult": {"min": msg_per_consult[0], "max": msg_per_consult[1]},
        "doctor_source_rows": doctor_rows,
        "doctor_source_rows_unmatched": doctor_unmatched,
        "doctor_text_or_time_mismatch": doctor_text_mismatch,
        "patient_text_or_time_mismatch_questions": patient_text_mismatch,
        "patient_message_count": patient_rows,
        "unique_source_record_ids": source_records,
        "needs_review_rows": [
            {
                "message_id": row[0],
                "consult_id": row[1],
                "source_record_id": row[2],
                "role": row[3],
                "speaker_id": row[4],
                "created_at": str(row[5]),
                "needs_review": bool(row[6]),
                "review_reason": row[7],
            }
            for row in review_reason_rows
        ],
    }


def main() -> int:
    root = project_root()
    raw = root / INPUT_RELATIVE
    if not raw.exists():
        print(f"missing input: {raw}", file=sys.stderr)
        return 2

    run_dir = make_run_dir("t3")
    report: dict[str, Any] = {
        "run": utc_now_iso(),
        "task": "t3_normalized_messages",
        "status": "completed",
        "rule_version": RULE_VERSION,
        "source_platform": PLATFORM,
        "input_file": file_meta(raw),
        "output_file": None,
        "verification": {},
        "risks": [],
        "not_verified": [],
    }

    con = duckdb.connect(":memory:")
    path_sql = str(raw).replace("\\", "/").replace("'", "''")
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW src AS "
        f"SELECT * FROM read_parquet('{path_sql}')"
    )

    out_parquet = run_dir / "normalized_messages.parquet"
    try:
        _compute_normalized(con, out_parquet)
        report["output_file"] = {
            "path": str(out_parquet),
            "size_bytes": out_parquet.stat().st_size,
        }
        report["verification"] = _verify(con, out_parquet)
    except Exception as exc:  # pragma: no cover - failure path
        report["status"] = "failed"
        report["error"] = str(exc)
        write_json(run_dir / "normalization_report.json", report)
        print(f"t3 failed: {exc}", file=sys.stderr)
        return 1

    verification = report["verification"]
    if verification["unique_message_ids"] != verification["total_messages"]:
        report["status"] = "failed"
        report["error"] = "message_id is not unique"
    if verification["role_counts"].get("patient") != 7000:
        report["status"] = "failed"
        report["error"] = "unexpected patient message count"
    if verification["role_counts"].get("doctor") != verification["doctor_source_rows"]:
        report["status"] = "failed"
        report["error"] = "doctor message count does not match source rows"
    if verification["doctor_source_rows_unmatched"] != 0:
        report["status"] = "failed"
        report["error"] = "some doctor messages cannot be traced back to source"
    if verification["patient_text_or_time_mismatch_questions"] != 0:
        report["status"] = "failed"
        report["error"] = "patient text or time mismatch detected"
    if verification["doctor_text_or_time_mismatch"] != 0:
        report["status"] = "failed"
        report["error"] = "doctor text or time mismatch detected"

    risks = [
        "当前归一化表来自合并抽样文件：部分问题在文件内的回答行数少于原始 ans_count，"
        "严格前序上下文只覆盖文件内可见消息；完整线程需原始 answer.parquet 补齐。",
        "answer_event_proxy_key 缺失：每条 doctor 消息通过 source_record_id "
        "(wy_ans_question_id_seq_user_id) 回源，真实稳定 ID 待原始 answer.parquet 回填。",
        "needs_review 只标记结构疑点（question_id+seq 冲突、同时间戳），不代表医学待核。",
    ]
    not_verified = [
        "原始稳定回答 ID answer_event_proxy_key",
        "原始 answer.parquet/question.parquet 中未含字段（性别、年龄、地址等）",
    ]
    report["risks"] = risks
    report["not_verified"] = not_verified

    write_json(run_dir / "normalization_report.json", report)

    exception_path = run_dir / "normalization_exceptions.csv"
    review_rows = verification["needs_review_rows"]
    if review_rows:
        with exception_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "message_id",
                    "consult_id",
                    "source_record_id",
                    "role",
                    "speaker_id",
                    "created_at",
                    "needs_review",
                    "review_reason",
                ],
            )
            writer.writeheader()
            writer.writerows(review_rows)
    else:
        exception_path.write_text(
            "message_id,consult_id,source_record_id,role,speaker_id,"
            "created_at,needs_review,review_reason\n",
            encoding="utf-8",
        )

    sample_path = run_dir / "normalized_sample.csv"
    sample = con.execute(
        """
        SELECT message_id, consult_id, source_record_id, role, speaker_id,
               created_at, order_basis, needs_review,
               LEFT(text, 60) AS text_preview
        FROM norm
        ORDER BY consult_id, CASE role WHEN 'patient' THEN 0 ELSE 1 END,
                 created_at, message_id
        LIMIT 30
        """
    ).fetchall()
    with sample_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "message_id",
                "consult_id",
                "source_record_id",
                "role",
                "speaker_id",
                "created_at",
                "order_basis",
                "needs_review",
                "text_preview",
            ]
        )
        for row in sample:
            writer.writerow(list(row))

    summary = [
        "## t3 规范化消息表审计摘要",
        "",
        f"- 输入: {raw.name}",
        f"- 消息总数: {verification['total_messages']}",
        f"- 唯一 message_id: {verification['unique_message_ids']}",
        f"- 唯一 consult_id: {verification['unique_consult_ids']}",
        f"- 角色: patient={verification['role_counts'].get('patient', 0)}, "
        f"doctor={verification['role_counts'].get('doctor', 0)}",
        f"- needs_review 消息数: {verification['needs_review_counts'].get('True', 0)}",
        f"- doctor 回源不匹配: {verification['doctor_source_rows_unmatched']}",
        f"- 原文或时间不一致: doctor={verification['doctor_text_or_time_mismatch']}, "
        f"patient={verification['patient_text_or_time_mismatch_questions']}",
    ]
    md_path = run_dir / "audit_summary.md"
    md_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

    current_ptr = {
        "current_t1_schema_report": str(
            root
            / "data_app_chain"
            / "runs"
            / "t1_20261105T143947Z"
            / "schema_report.json"
        ),
        "current_t2_relationship_report": str(
            root
            / "data_app_chain"
            / "runs"
            / "t2_20261105T144738Z"
            / "relationship_report.json"
        ),
        "current_t3_normalized_messages": str(out_parquet),
        "last_run_utc": report["run"],
        "status": report["status"],
        "total_rows": verification["total_messages"],
    }
    write_json(root / "data_app_chain" / "current_run.json", current_ptr)
    con.close()

    print(f"t3 完成: {out_parquet}")
    print(
        f"messages={verification['total_messages']} "
        f"consults={verification['unique_consult_ids']} "
        f"patient={verification['role_counts'].get('patient', 0)} "
        f"doctor={verification['role_counts'].get('doctor', 0)} "
        f"needs_review={verification['needs_review_counts'].get('True', 0)} "
        f"unmatched={verification['doctor_source_rows_unmatched']} "
        f"text_mismatch={verification['doctor_text_or_time_mismatch']}"
    )
    print(f"status: {report['status']}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
