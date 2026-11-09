"""t2: 记录单位与关联关系审计。

只读读取合并版微医问答样本，审计问题线程、回答-医生关联、时间/排序一致性、
重复医生与稳定 ID 映射规则。研究派生列仅作背景，不参与本次校验。
所有派生输出写入 data_app_chain/runs/<run_id>/。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import duckdb

from common import file_meta, make_run_dir, project_root, utc_now_iso, write_json


INPUT_RELATIVE = "raw_input/major5_stratified_question_samples_answers.parquet"


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, params: tuple = ()) -> Any:
    return con.execute(sql, params).fetchone()[0]


def _multi(con: duckdb.DuckDBPyConnection, sql: str, params: tuple = ()) -> list[Any]:
    return con.execute(sql, params).fetchall()


def _quantiles(con: duckdb.DuckDBPyConnection, sql: str) -> list[float | None]:
    row = con.execute(sql).fetchone()
    if row is None or row[0] is None:
        return []
    return [round(float(v), 2) if v is not None else None for v in row[0]]


def main() -> int:
    root = project_root()
    raw = root / INPUT_RELATIVE
    if not raw.exists():
        print(f"missing input: {raw}", file=sys.stderr)
        return 2

    run_dir = make_run_dir("t2")
    report: dict[str, Any] = {
        "run": utc_now_iso(),
        "task": "t2_record_relationship_audit",
        "status": "completed",
        "input_file": file_meta(raw),
        "input_unchanged_since_t1": None,
        "total_rows": None,
        "unique_question_ids": None,
        "unique_doctor_ids": None,
        "record_units": {},
        "thread_audit": {},
        "time_order_audit": {},
        "doctor_audit": {},
        "stable_id_mapping": {},
        "question_meta_consistency": {},
        "risks": [],
        "not_verified": [],
    }

    con = duckdb.connect(":memory:")
    path_sql = str(raw).replace("\\", "/").replace("'", "''")
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW src AS "
        f"SELECT * FROM read_parquet('{path_sql}')"
    )

    total_rows = _scalar(con, "SELECT COUNT(*) FROM src")
    report["total_rows"] = total_rows
    report["unique_question_ids"] = _scalar(
        con, "SELECT COUNT(DISTINCT question_id) FROM src"
    )
    report["unique_doctor_ids"] = _scalar(
        con, "SELECT COUNT(DISTINCT user_id) FROM src"
    )

    t1_path = root / "data_app_chain" / "current_run.json"
    if t1_path.exists():
        try:
            current = json.loads(t1_path.read_text(encoding="utf-8"))
            pointer = current.get("current_t1_schema_report")
            if pointer and Path(pointer).exists():
                t1 = json.loads(Path(pointer).read_text(encoding="utf-8"))
                report["input_unchanged_since_t1"] = (
                    t1.get("input_file", {}).get("sha256")
                    == report["input_file"]["sha256"]
                )
        except (OSError, ValueError):
            report["input_unchanged_since_t1"] = None

    record_units: dict[str, Any] = {
        "granularity": "一行 = 一个医生对一个问题的一条回答",
        "candidate_composite_key": ["question_id", "seq", "user_id"],
        "composite_key_unique_rows": None,
        "question_seq_unique_rows": None,
        "question_seq_collision_groups": None,
        "question_seq_collision_rows": None,
        "question_seq_collision_sample": [],
        "answer_before_question_rows": None,
        "answer_before_question_questions": None,
        "answer_before_question_sample": [],
    }
    record_units["composite_key_unique_rows"] = _scalar(
        con,
        "SELECT COUNT(*) FROM ("
        "SELECT DISTINCT question_id, seq, user_id FROM src)",
    )
    record_units["question_seq_unique_rows"] = _scalar(
        con,
        "SELECT COUNT(*) FROM (SELECT DISTINCT question_id, seq FROM src)",
    )
    collision = _multi(
        con,
        """
        SELECT question_id, seq, COUNT(*) AS c
        FROM src
        GROUP BY question_id, seq
        HAVING COUNT(*) > 1
        ORDER BY c DESC
        """,
    )
    record_units["question_seq_collision_groups"] = len(collision)
    record_units["question_seq_collision_rows"] = sum(row[2] for row in collision)
    if collision:
        rows = _multi(
            con,
            """
            SELECT question_id, seq, user_id, answer_create_time
            FROM src
            WHERE (question_id, seq) IN (
                SELECT question_id, seq
                FROM src
                GROUP BY question_id, seq
                HAVING COUNT(*) > 1
            )
            ORDER BY question_id, seq, user_id
            """,
        )
        record_units["question_seq_collision_sample"] = [
            {
                "question_id": r[0],
                "seq": r[1],
                "user_id": r[2],
                "answer_create_time": str(r[3]),
            }
            for r in rows
        ]

    record_units["answer_before_question_rows"] = _scalar(
        con,
        "SELECT COUNT(*) FROM src "
        "WHERE answer_create_time < question_create_time",
    )
    record_units["answer_before_question_questions"] = _scalar(
        con,
        "SELECT COUNT(DISTINCT question_id) FROM src "
        "WHERE answer_create_time < question_create_time",
    )
    if record_units["answer_before_question_rows"]:
        sample = _multi(
            con,
            """
            SELECT question_id, seq, user_id,
                   question_create_time, answer_create_time
            FROM src
            WHERE answer_create_time < question_create_time
            ORDER BY question_create_time
            LIMIT 10
            """,
        )
        record_units["answer_before_question_sample"] = [
            {
                "question_id": r[0],
                "seq": r[1],
                "user_id": r[2],
                "question_create_time": str(r[3]),
                "answer_create_time": str(r[4]),
            }
            for r in sample
        ]
    report["record_units"] = record_units

    thread_audit: dict[str, Any] = {
        "answers_per_question_quantiles": [],
        "questions_with_one_answer": None,
        "questions_with_max_answers": None,
        "questions_with_fewer_rows_than_ans_count": None,
        "questions_with_equal_rows_to_ans_count": None,
        "questions_with_more_rows_than_ans_count": None,
        "question_desc_variant_questions": None,
        "question_time_variant_questions": None,
        "question_meta_sample": [],
        "same_ts_groups": None,
        "same_ts_rows": None,
        "same_ts_questions": None,
        "seq_gap_questions": None,
    }
    thread_audit["answers_per_question_quantiles"] = _quantiles(
        con,
        """
        SELECT APPROX_QUANTILE(cnt, [0.1, 0.25, 0.5, 0.75, 0.9, 0.99])
        FROM (SELECT question_id, COUNT(*) AS cnt FROM src GROUP BY question_id)
        """,
    )
    thread_audit["questions_with_one_answer"] = _scalar(
        con,
        "SELECT COUNT(*) FROM ("
        "SELECT question_id FROM src GROUP BY question_id HAVING COUNT(*) = 1)",
    )
    thread_audit["questions_with_max_answers"] = _scalar(
        con,
        "SELECT MAX(cnt) FROM ("
        "SELECT question_id, COUNT(*) AS cnt FROM src GROUP BY question_id)",
    )
    thread_audit["questions_with_fewer_rows_than_ans_count"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT question_id
            FROM src
            GROUP BY question_id
            HAVING COUNT(*) < MAX(ans_count)
        )
        """,
    )
    thread_audit["questions_with_equal_rows_to_ans_count"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT question_id
            FROM src
            GROUP BY question_id
            HAVING COUNT(*) = MAX(ans_count)
        )
        """,
    )
    thread_audit["questions_with_more_rows_than_ans_count"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT question_id
            FROM src
            GROUP BY question_id
            HAVING COUNT(*) > MAX(ans_count)
        )
        """,
    )
    thread_audit["question_desc_variant_questions"] = _scalar(
        con,
        "SELECT COUNT(*) FROM ("
        "SELECT question_id FROM src "
        "GROUP BY question_id "
        "HAVING COUNT(DISTINCT question_desc) > 1)",
    )
    thread_audit["question_time_variant_questions"] = _scalar(
        con,
        "SELECT COUNT(*) FROM ("
        "SELECT question_id FROM src "
        "GROUP BY question_id "
        "HAVING COUNT(DISTINCT question_create_time) > 1)",
    )
    meta_sample = _multi(
        con,
        """
        SELECT question_id, COUNT(DISTINCT question_desc) desc_variants,
               COUNT(DISTINCT question_create_time) time_variants
        FROM src
        GROUP BY question_id
        HAVING COUNT(DISTINCT question_desc) > 1
           OR COUNT(DISTINCT question_create_time) > 1
        ORDER BY desc_variants DESC, time_variants DESC
        LIMIT 20
        """,
    )
    thread_audit["question_meta_sample"] = [
        {
            "question_id": r[0],
            "question_desc_variants": r[1],
            "question_time_variants": r[2],
        }
        for r in meta_sample
    ]
    same_ts = _multi(
        con,
        """
        SELECT question_id, answer_create_time, COUNT(*) AS c
        FROM src
        WHERE answer_create_time IS NOT NULL
        GROUP BY question_id, answer_create_time
        HAVING COUNT(*) > 1
        """,
    )
    thread_audit["same_ts_groups"] = len(same_ts)
    thread_audit["same_ts_rows"] = sum(row[2] for row in same_ts)
    thread_audit["same_ts_questions"] = len({row[0] for row in same_ts})
    thread_audit["seq_gap_questions"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT question_id
            FROM src
            GROUP BY question_id
            HAVING COUNT(DISTINCT seq) != MAX(seq) - MIN(seq) + 1
        )
        """,
    )
    report["thread_audit"] = thread_audit

    time_order_audit: dict[str, Any] = {
        "strict_conflict_pairs": None,
        "seq_time_rank_row_mismatches": None,
        "strict_prior_answer_rows": None,
        "strict_prior_answer_questions": None,
        "no_strict_prior_answer_rows": None,
        "alignment_sample": [],
    }
    time_order_audit["strict_conflict_pairs"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM src a
        JOIN src b
          ON a.question_id = b.question_id
         AND a.answer_create_time < b.answer_create_time
         AND a.seq > b.seq
        """,
    )
    time_order_audit["seq_time_rank_row_mismatches"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT time_pos, seq_pos FROM (
                SELECT question_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY question_id
                           ORDER BY answer_create_time, seq, user_id
                       ) AS time_pos,
                       ROW_NUMBER() OVER (
                           PARTITION BY question_id
                           ORDER BY seq, answer_create_time, user_id
                       ) AS seq_pos
                FROM src
            ) WHERE time_pos <> seq_pos
        )
        """,
    )
    time_order_audit["strict_prior_answer_rows"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM src r
        WHERE EXISTS (
            SELECT 1 FROM src p
            WHERE p.question_id = r.question_id
              AND p.answer_create_time < r.answer_create_time
        )
        """,
    )
    time_order_audit["strict_prior_answer_questions"] = _scalar(
        con,
        """
        SELECT COUNT(DISTINCT r.question_id) FROM src r
        WHERE EXISTS (
            SELECT 1 FROM src p
            WHERE p.question_id = r.question_id
              AND p.answer_create_time < r.answer_create_time
        )
        """,
    )
    time_order_audit["no_strict_prior_answer_rows"] = (
        total_rows - time_order_audit["strict_prior_answer_rows"]
    )
    conflict_sample = _multi(
        con,
        """
        SELECT a.question_id, a.seq AS earlier_seq, a.user_id AS earlier_doctor,
               a.answer_create_time AS earlier_time,
               b.seq AS later_seq, b.user_id AS later_doctor,
               b.answer_create_time AS later_time
        FROM src a
        JOIN src b
          ON a.question_id = b.question_id
         AND a.answer_create_time < b.answer_create_time
         AND a.seq > b.seq
        ORDER BY a.question_id
        LIMIT 20
        """,
    )
    time_order_audit["alignment_sample"] = [
        {
            "question_id": r[0],
            "earlier_seq": r[1],
            "earlier_doctor": r[2],
            "earlier_time": str(r[3]),
            "later_seq": r[4],
            "later_doctor": r[5],
            "later_time": str(r[6]),
        }
        for r in conflict_sample
    ]
    report["time_order_audit"] = time_order_audit

    doctor_audit: dict[str, Any] = {
        "doctors_with_multiple_questions": None,
        "questions_per_doctor_quantiles": [],
        "answers_per_doctor_quantiles": [],
        "doctors_with_multiple_titles": None,
        "doctors_with_multiple_depts": None,
        "doctors_with_multiple_hospitals": None,
        "metadata_inconsistency_count": None,
        "metadata_inconsistency_sample": [],
    }
    doctor_audit["doctors_with_multiple_questions"] = _scalar(
        con,
        "SELECT COUNT(*) FROM ("
        "SELECT user_id FROM src "
        "GROUP BY user_id HAVING COUNT(DISTINCT question_id) > 1)",
    )
    doctor_audit["questions_per_doctor_quantiles"] = _quantiles(
        con,
        """
        SELECT APPROX_QUANTILE(cnt, [0.5, 0.9, 0.99])
        FROM (SELECT user_id, COUNT(DISTINCT question_id) AS cnt FROM src GROUP BY user_id)
        """,
    )
    doctor_audit["answers_per_doctor_quantiles"] = _quantiles(
        con,
        """
        SELECT APPROX_QUANTILE(cnt, [0.5, 0.9, 0.99])
        FROM (SELECT user_id, COUNT(*) AS cnt FROM src GROUP BY user_id)
        """,
    )
    doctor_audit["doctors_with_multiple_titles"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT user_id FROM src
            GROUP BY user_id HAVING COUNT(DISTINCT tech_title) > 1
        )
        """,
    )
    doctor_audit["doctors_with_multiple_depts"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT user_id FROM src
            GROUP BY user_id HAVING COUNT(DISTINCT dept) > 1
        )
        """,
    )
    doctor_audit["doctors_with_multiple_hospitals"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT user_id FROM src
            GROUP BY user_id HAVING COUNT(DISTINCT hospital) > 1
        )
        """,
    )
    doctor_audit["metadata_inconsistency_count"] = _scalar(
        con,
        """
        SELECT COUNT(*) FROM (
            SELECT user_id
            FROM src
            GROUP BY user_id
            HAVING COUNT(DISTINCT tech_title) > 1
                OR COUNT(DISTINCT dept) > 1
                OR COUNT(DISTINCT hospital) > 1
        )
        """,
    )
    meta_sample = _multi(
        con,
        """
        SELECT user_id,
               COUNT(DISTINCT tech_title) title_variants,
               COUNT(DISTINCT dept) dept_variants,
               COUNT(DISTINCT hospital) hospital_variants,
               COUNT(*) answer_rows
        FROM src
        GROUP BY user_id
        HAVING COUNT(DISTINCT tech_title) > 1
            OR COUNT(DISTINCT dept) > 1
            OR COUNT(DISTINCT hospital) > 1
        ORDER BY answer_rows DESC
        LIMIT 30
        """,
    )
    doctor_audit["metadata_inconsistency_sample"] = [
        {
            "user_id": r[0],
            "title_variants": r[1],
            "dept_variants": r[2],
            "hospital_variants": r[3],
            "answer_rows": r[4],
        }
        for r in meta_sample
    ]

    title_counts = _multi(
        con,
        "SELECT tech_title, COUNT(*) FROM src GROUP BY tech_title ORDER BY 2 DESC",
    )
    doctor_audit["tech_title_distribution"] = {
        str(c[0]): int(c[1]) for c in title_counts
    }
    report["doctor_audit"] = doctor_audit

    stable_id: dict[str, Any] = {
        "original_answer_id_column": "answer_event_proxy_key",
        "column_present_in_current_file": False,
        "composite_key_columns": ["question_id", "seq", "user_id"],
        "composite_key_unique_row_count": record_units["composite_key_unique_rows"],
        "total_rows": total_rows,
        "composite_key_is_candidate_unique_key": (
            record_units["composite_key_unique_rows"] == total_rows
        ),
        "proposed_app_mapping": (
            "app_id = SHA256(question_id|seq|user_id); "
            "source_key = 'wy_merged:' + question_id + ':' + seq + ':' + user_id; "
            "original_answer_event_proxy_key = NULL，等待原始 answer.parquet 补齐后回填"
        ),
        "mapping_limitation": (
            "当前合并文件本身不携带 answer_event_proxy_key；"
            "若未来接入原始 answer.parquet，需用 question_id+seq+user_id 找回到原始行，"
            "再回填真实稳定 ID；seq 冲突组（2 组 4 行）不能只用 question_id+seq 对齐。"
        ),
    }
    report["stable_id_mapping"] = stable_id

    question_meta: dict[str, Any] = {
        "question_desc_distinct_count": _scalar(
            con, "SELECT COUNT(DISTINCT question_desc) FROM src"
        ),
        "question_create_time_distinct_count": _scalar(
            con, "SELECT COUNT(DISTINCT question_create_time) FROM src"
        ),
        "questions_checked": report["unique_question_ids"],
    }
    report["question_meta_consistency"] = question_meta

    risks = [
        "当前合并文件缺 answer_event_proxy_key：任何应用内 ID 都只是可追溯复合键映射，"
        "不是原始微医稳定回答 ID。",
        f"合并文件是分问题抽样后的回答子集：{thread_audit['questions_with_fewer_rows_than_ans_count']} 个问题在 "
        f"文件内回答行数少于其 ans_count，seq 缺口主要在抽样口径下产生；"
        f"严格前序只能覆盖文件内可见回答，未包含的原始早答不在上下文内。",
        "question_id+seq 有两组冲突（4 行），稳定键必须含 user_id。",
        "存在 answer_create_time < question_create_time 的数据行，"
        "载入病例前需复查其来源或标记待核查。",
        "seq 与 answer_create_time 存在不一致（同一问题内时间更晚但 seq 更小的冲突对），"
        "严格前序只按 answer_create_time 判定，不按 seq 判定。",
        "医生元数据（职称/科室/医院）随回答变化，同一 user_id 在不同回答上可能带不同属性；"
        "展示时按回答行自身属性记录，不跨回答覆盖。",
        "同一问题内 question_desc / question_create_time 并非每个 question_id 都恒定，"
        "问题层导入时需选择首见值并记录其余变体。",
    ]
    not_verified = [
        "answer_event_proxy_key（原始稳定回答 ID）",
        "原始 answer.parquet / question.parquet 中的未合并字段（性别、年龄、地址、标签扩展等）",
        "seq 与真实回答提交顺序的直接证据（只有文件内时间和序号可供交叉核验）",
    ]
    report["risks"] = risks
    report["not_verified"] = not_verified

    json_path = run_dir / "relationship_report.json"
    write_json(json_path, report)

    conflict_rows = [
        {
            "question_id": r[0],
            "earlier_seq": r[1],
            "earlier_doctor": r[2],
            "earlier_time": str(r[3]),
            "later_seq": r[4],
            "later_doctor": r[5],
            "later_time": str(r[6]),
        }
        for r in conflict_sample
    ]
    conflict_path = run_dir / "alignment_conflict_sample.csv"
    if conflict_rows:
        with conflict_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(conflict_rows[0].keys()))
            writer.writeheader()
            writer.writerows(conflict_rows)
    else:
        conflict_path.write_text(
            "question_id,earlier_seq,earlier_doctor,earlier_time,"
            "later_seq,later_doctor,later_time\n",
            encoding="utf-8",
        )

    doctor_sample_rows = doctor_audit["metadata_inconsistency_sample"]
    doctor_path = run_dir / "doctor_attribute_inconsistency_sample.csv"
    if doctor_sample_rows:
        with doctor_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "user_id",
                    "title_variants",
                    "dept_variants",
                    "hospital_variants",
                    "answer_rows",
                ],
            )
            writer.writeheader()
            writer.writerows(doctor_sample_rows)
    else:
        doctor_path.write_text(
            "user_id,title_variants,dept_variants,hospital_variants,answer_rows\n",
            encoding="utf-8",
        )

    summary = [
        "## t2 记录单位与关联关系审计摘要",
        "",
        f"- 行/独立问题/独立医生: {total_rows} / {report['unique_question_ids']} / {report['unique_doctor_ids']}",
        f"- 复合键 question_id+seq+user_id 唯一行数: {record_units['composite_key_unique_rows']}",
        f"- question_id+seq 冲突组/行: {record_units['question_seq_collision_groups']} / {record_units['question_seq_collision_rows']}",
        f"- 回答早于提问行/问题: {record_units['answer_before_question_rows']} / {record_units['answer_before_question_questions']}",
        f"- 同时间戳组/行/问题: {thread_audit['same_ts_groups']} / {thread_audit['same_ts_rows']} / {thread_audit['same_ts_questions']}",
        f"- 行数少于 ans_count / 等于 ans_count 的问题数: {thread_audit['questions_with_fewer_rows_than_ans_count']} / {thread_audit['questions_with_equal_rows_to_ans_count']}",
        f"- 时间序与 seq 严格冲突对: {time_order_audit['strict_conflict_pairs']}",
        f"- 存在严格前序回答行: {time_order_audit['strict_prior_answer_rows']}",
        f"- 医生元数据不一致医生数: {doctor_audit['metadata_inconsistency_count']}",
        f"- 稳定回答 ID 字段: answer_event_proxy_key 缺失，当前用复合键映射并保留回填限制",
    ]
    md_path = run_dir / "audit_summary.md"
    md_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

    t1_pointer = (
        root
        / "data_app_chain"
        / "runs"
        / "t1_20261105T143947Z"
        / "schema_report.json"
    )
    current_ptr = {
        "current_t1_schema_report": str(t1_pointer),
        "current_t2_relationship_report": str(json_path),
        "last_run_utc": report["run"],
        "status": "completed",
        "total_rows": total_rows,
    }
    write_json(root / "data_app_chain" / "current_run.json", current_ptr)
    con.close()

    print(f"t2 完成: {json_path}")
    print(
        f"rows={total_rows} questions={report['unique_question_ids']} "
        f"doctors={report['unique_doctor_ids']} "
        f"key_unique={record_units['composite_key_unique_rows']} "
        f"conflict_pairs={time_order_audit['strict_conflict_pairs']}"
    )
    print("status: completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
