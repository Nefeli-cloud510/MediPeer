"""t1: 真实字段映射与 schema 审计。

只读读取合并版微医问答样本，输出真实 schema、关联键、行数、缺失、时间、
重复与派生字段标注。所有派生输出写入 data_app_chain/runs/<run_id>/。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

import duckdb

from common import file_meta, make_run_dir, project_root, utc_now_iso, write_json


INPUT_RELATIVE = "raw_input/major5_stratified_question_samples_answers.parquet"

PRODUCT_FIELDS = [
    {
        "product_field": "source_question_id",
        "source_column": "question_id",
        "required": True,
        "note": "患者问题主键；同问题多医生回答组成一个病例线程。",
    },
    {
        "product_field": "source_answer_id",
        "source_column": "answer_event_proxy_key",
        "required": True,
        "note": "原始稳定回答 ID；当前合并文件未含该列，需从 answer.parquet 补齐或在导入时生成可追溯复合键并明示限制。",
    },
    {
        "product_field": "patient_text",
        "source_column": "question_desc",
        "required": True,
        "note": "患者初始叙述；空值影响病例可用。",
    },
    {
        "product_field": "answer_text",
        "source_column": "answer",
        "required": True,
        "note": "医生回答正文。",
    },
    {
        "product_field": "doctor_id",
        "source_column": "user_id",
        "required": True,
        "note": "医生标识；合并文件为字符串。",
    },
    {
        "product_field": "doctor_title",
        "source_column": "tech_title",
        "required": False,
        "note": "职称仅作医生属性展示，不参与观点正确性裁定。",
    },
    {
        "product_field": "department",
        "source_column": "dept",
        "required": False,
        "note": "科室名称。",
    },
    {
        "product_field": "hospital",
        "source_column": "hospital",
        "required": False,
        "note": "医院名称。",
    },
    {
        "product_field": "answer_time",
        "source_column": "answer_create_time",
        "required": True,
        "note": "医生回答时间；严格前序依赖该字段。",
    },
    {
        "product_field": "question_time",
        "source_column": "question_create_time",
        "required": True,
        "note": "患者提问时间。",
    },
    {
        "product_field": "answer_seq",
        "source_column": "seq",
        "required": False,
        "note": "同一问题内回答序号；需与时间字段交叉核验是否一致。",
    },
    {
        "product_field": "hospital_level",
        "source_column": "hospitallevel_final",
        "required": False,
        "note": "医院等级为研究派生字段，不视为原始业务输入。",
    },
]

DERIVED_COLUMNS = [
    "time_seq",
    "observed_seq_by_time",
    "prior_answer_count",
    "prior_answer_count_by_time",
    "answer_delay_hours",
    "is_answer_time_valid",
    "is_seq_time_aligned",
    "time_rank_within_question",
    "has_prior_answers",
    "hospitallevel",
    "hospital_key",
    "hospitallevel_final",
    "hospitallevel_fill_method",
    "hospitallevel_source",
    "hospitallevel_note",
    "manual_hospitallevel",
    "manual_hospitallevel_source",
    "manual_hospitallevel_note",
    "has_round2_override",
    "question_answer_join_ready",
    "question_user_answer_count",
    "doctor_answer_order_within_question",
    "sample_layer",
    "sample_draw_order",
]


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, params: tuple = ()) -> Any:
    return con.execute(sql, params).fetchone()[0]


def _distinct_multi(con: duckdb.DuckDBPyConnection, columns: list[str]) -> int:
    cols = ", ".join(f'"{c}"' for c in columns)
    return _scalar(con, f"SELECT COUNT(*) FROM (SELECT DISTINCT {cols} FROM src)")


def _bool_counts(con: duckdb.DuckDBPyConnection, column: str) -> dict[str, int]:
    rows = con.execute(
        f'SELECT "{column}", COUNT(*) FROM src GROUP BY "{column}" ORDER BY 1'
    ).fetchall()
    return {str(value): count for value, count in rows}


def main() -> int:
    root = project_root()
    raw = root / INPUT_RELATIVE
    if not raw.exists():
        print(f"missing input: {raw}", file=sys.stderr)
        return 2

    run_dir = make_run_dir("t1")
    report: dict[str, Any] = {
        "run": utc_now_iso(),
        "task": "t1_schema_audit",
        "status": "completed",
        "input_file": file_meta(raw),
        "input_unchanged_since_t0": None,
        "total_rows": None,
        "schema": [],
        "column_stats": [],
        "row_level_audit": {},
        "time_audit": {},
        "text_audit": {},
        "derived_flags": {},
        "field_mapping": [],
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
    t0_path = root / "data_app_chain" / "runs" / "t0_20261105T020821Z" / "source_manifest.json"
    if t0_path.exists():
        import json

        t0_manifest = json.loads(t0_path.read_text(encoding="utf-8"))
        for item in t0_manifest["found_files"]:
            if Path(item["path"]).name == raw.name:
                report["input_unchanged_since_t0"] = (
                    item.get("sha256") == report["input_file"]["sha256"]
                )
                break

    desc = con.execute("DESCRIBE SELECT * FROM src").fetchall()
    column_names = [row[0].lower() for row in desc]

    for row in desc:
        name, typ = row[0], row[1]
        quoted = f'"{name}"'
        stats: dict[str, Any] = {
            "name": name,
            "type": typ,
            "null_count": _scalar(con, f"SELECT COUNT(*) - COUNT({quoted}) FROM src"),
            "distinct_count": _scalar(
                con, f"SELECT COUNT(DISTINCT {quoted}) FROM src"
            ),
        }
        null_count = stats["null_count"]
        stats["non_null_count"] = total_rows - null_count
        stats["null_pct"] = round(null_count / total_rows * 100, 3) if total_rows else 0.0
        upper_typ = typ.upper()
        if "VARCHAR" in upper_typ:
            stats["empty_string_count"] = _scalar(
                con,
                f"SELECT COUNT(*) FROM src WHERE {quoted} IS NOT NULL "
                f"AND TRIM({quoted}) = ''",
            )
        else:
            stats["empty_string_count"] = None
        if "TIMESTAMP" in upper_typ:
            lo, hi = con.execute(
                f"SELECT MIN({quoted}), MAX({quoted}) FROM src"
            ).fetchone()
            stats["min"] = str(lo) if lo is not None else None
            stats["max"] = str(hi) if hi is not None else None
        elif upper_typ in {"BIGINT", "DOUBLE", "INTEGER", "DECIMAL"}:
            vals = con.execute(
                f"SELECT MIN({quoted}), MAX({quoted}), AVG({quoted}) FROM src"
            ).fetchone()
            stats["min"] = vals[0]
            stats["max"] = vals[1]
            stats["avg"] = round(vals[2], 2) if vals[2] is not None else None
        if upper_typ == "BOOLEAN":
            stats["value_counts"] = _bool_counts(con, name)
        report["column_stats"].append(stats)

    report["schema"] = [
        {"name": row[0], "type": row[1], "nullable": row[2], "key": row[3]}
        for row in desc
    ]

    audit: dict[str, Any] = {
        "unique_question_ids": _scalar(
            con, "SELECT COUNT(DISTINCT question_id) FROM src"
        ),
        "null_question_ids": _scalar(
            con, 'SELECT COUNT(*) FROM src WHERE question_id IS NULL'
        ),
        "unique_user_ids": _scalar(con, "SELECT COUNT(DISTINCT user_id) FROM src"),
        "null_user_ids": _scalar(
            con, 'SELECT COUNT(*) FROM src WHERE user_id IS NULL OR TRIM(user_id) = \'\''
        ),
        "unique_question_seq_pairs": _distinct_multi(con, ["question_id", "seq"]),
        "unique_question_seq_user_triples": _distinct_multi(
            con, ["question_id", "seq", "user_id"]
        ),
        "unique_question_user_pairs": _distinct_multi(con, ["question_id", "user_id"]),
        "null_seq_count": _scalar(con, 'SELECT COUNT(*) FROM src WHERE seq IS NULL'),
        "question_seq_collision_groups": None,
        "question_seq_collision_rows": None,
        "question_seq_collision_sample": [],
        "exact_duplicate_rows": None,
        "same_question_same_doctor_multiple_rows": _scalar(
            con,
            """
            SELECT COUNT(*) FROM (
                SELECT question_id, user_id, COUNT(*) AS c
                FROM src GROUP BY question_id, user_id HAVING COUNT(*) > 1
            )
            """,
        ),
        "answer_count_distribution": {},
        "doctor_count_distribution": {},
    }
    distinct_all = _scalar(con, "SELECT COUNT(*) FROM (SELECT DISTINCT * FROM src)")
    audit["exact_duplicate_rows"] = total_rows - distinct_all
    collision = con.execute(
        """
        SELECT question_id, seq, COUNT(*) AS c
        FROM src
        GROUP BY question_id, seq
        HAVING COUNT(*) > 1
        ORDER BY c DESC
        """
    ).fetchall()
    audit["question_seq_collision_groups"] = len(collision)
    audit["question_seq_collision_rows"] = sum(row[2] for row in collision)
    sample = collision[:5]
    if sample:
        rows = con.execute(
            """
            SELECT question_id, seq, user_id
            FROM src
            WHERE (question_id, seq) IN (SELECT question_id, seq FROM (
                SELECT question_id, seq, COUNT(*) AS c
                FROM src
                GROUP BY question_id, seq
                HAVING COUNT(*) > 1
            ))
            ORDER BY question_id, seq, user_id
            LIMIT 10
            """
        ).fetchall()
        audit["question_seq_collision_sample"] = [
            {"question_id": r[0], "seq": r[1], "user_id": r[2]} for r in rows
        ]
    audit["answer_count_distribution"] = {
        str(count): rows
        for count, rows in con.execute(
            """
            SELECT per_question, COUNT(*) AS questions FROM (
                SELECT question_id, COUNT(*) AS per_question
                FROM src GROUP BY question_id
            ) GROUP BY per_question ORDER BY per_question
            """
        ).fetchall()
    }
    audit["doctor_count_distribution"] = {
        str(count): rows
        for count, rows in con.execute(
            """
            SELECT per_question, COUNT(*) AS questions FROM (
                SELECT question_id, COUNT(DISTINCT user_id) AS per_question
                FROM src GROUP BY question_id
            ) GROUP BY per_question ORDER BY per_question
            """
        ).fetchall()
    }
    report["row_level_audit"] = audit

    time_audit: dict[str, Any] = {
        "answer_time_min": str(
            _scalar(con, "SELECT MIN(answer_create_time) FROM src")
        ),
        "answer_time_max": str(
            _scalar(con, "SELECT MAX(answer_create_time) FROM src")
        ),
        "question_time_min": str(
            _scalar(con, "SELECT MIN(question_create_time) FROM src")
        ),
        "question_time_max": str(
            _scalar(con, "SELECT MAX(question_create_time) FROM src")
        ),
        "same_timestamp_rows": None,
        "same_timestamp_questions": None,
        "same_timestamp_rows_by_user": None,
    }
    same_ts = con.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT question_id) FROM (
            SELECT question_id, answer_create_time, COUNT(*) AS c
            FROM src
            WHERE answer_create_time IS NOT NULL
            GROUP BY question_id, answer_create_time
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()
    time_audit["same_timestamp_rows"] = same_ts[0]
    time_audit["same_timestamp_questions"] = same_ts[1]
    same_ts_user = con.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT question_id, user_id, answer_create_time, COUNT(*) AS c
            FROM src
            WHERE answer_create_time IS NOT NULL
            GROUP BY question_id, user_id, answer_create_time
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    time_audit["same_timestamp_rows_by_user"] = same_ts_user
    report["time_audit"] = time_audit

    text_audit = {
        "empty_question_desc": _scalar(
            con,
            "SELECT COUNT(*) FROM src "
            "WHERE question_desc IS NULL OR TRIM(question_desc) = ''",
        ),
        "empty_answer": _scalar(
            con, "SELECT COUNT(*) FROM src WHERE answer IS NULL OR TRIM(answer) = ''"
        ),
        "empty_tags": _scalar(
            con, "SELECT COUNT(*) FROM src WHERE tags IS NULL OR TRIM(tags) = ''"
        ),
        "answer_chars": {},
        "question_desc_chars": {},
    }
    for label, column in (
        ("answer_chars", "answer"),
        ("question_desc_chars", "question_desc"),
    ):
        vals = con.execute(
            f"""
            SELECT
                MIN(LENGTH({column})),
                AVG(LENGTH({column})),
                MAX(LENGTH({column})),
                APPROX_QUANTILE(LENGTH({column}), [0.5, 0.9, 0.99])
            FROM src
            WHERE {column} IS NOT NULL
            """
        ).fetchone()
        text_audit[label] = {
            "min_chars": vals[0],
            "avg_chars": round(vals[1], 2) if vals[1] is not None else None,
            "max_chars": vals[2],
            "p50_p90_p99_chars": vals[3],
        }
    report["text_audit"] = text_audit

    report["derived_flags"] = {
        "question_answer_join_ready": _bool_counts(con, "question_answer_join_ready"),
        "has_prior_answers": _bool_counts(con, "has_prior_answers"),
        "is_answer_time_valid": _bool_counts(con, "is_answer_time_valid"),
        "is_seq_time_aligned": _bool_counts(con, "is_seq_time_aligned"),
        "has_round2_override": _bool_counts(con, "has_round2_override"),
        "sample_layer": _bool_counts(con, "sample_layer") if "sample_layer" in column_names else {},
        "hospitallevel_fill_method": (
            _bool_counts(con, "hospitallevel_fill_method")
            if "hospitallevel_fill_method" in column_names
            else {}
        ),
    }

    field_mapping: list[dict[str, Any]] = []
    for item in PRODUCT_FIELDS:
        source = item["source_column"]
        exists = source.lower() in column_names
        field_mapping.append(
            {
                **item,
                "column_exists": exists,
                "verified": exists and source not in {
                    "hospitallevel_final",
                    "hospital_key",
                },
                "status": (
                    "verified"
                    if exists
                    and source != "answer_event_proxy_key"
                    and source != "hospitallevel_final"
                    else (
                        "missing"
                        if not exists
                        else "research_derived"
                    )
                ),
            }
        )
    report["field_mapping"] = field_mapping
    report["derived_column_list"] = DERIVED_COLUMNS

    risks = [
        "合并文件缺少 answer_event_proxy_key，无法直接保留原始稳定回答 ID；"
        "导入应用前需补齐 source answer.parquet，或使用 question_id+seq 复合键并记录限制。",
        "研究派生字段（time_seq/prior_*/is_*/hospitallevel_*/sample_* 等）仅作背景参考，"
        "不作为原始业务输入参与审阅上下文。",
        "同一问题存在相同 answer_create_time 的多个回答，严格前序必须排除同时刻回答。",
    ]
    not_verified = [
        "answer_event_proxy_key",
        "原始匹配到 answer.parquet 的完整字段（当前只有合并样本，无独立 answer/question 文件）",
    ]
    report["risks"] = risks
    report["not_verified"] = not_verified

    json_path = run_dir / "schema_report.json"
    write_json(json_path, report)

    mapping_path = run_dir / "field_mapping.csv"
    with mapping_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "product_field",
                "source_column",
                "required",
                "status",
                "note",
            ],
        )
        writer.writeheader()
        for item in field_mapping:
            writer.writerow(
                {
                    "product_field": item["product_field"],
                    "source_column": item["source_column"],
                    "required": item["required"],
                    "status": item["status"],
                    "note": item["note"],
                }
            )

    summary = [
        "## t1 schema 审计摘要",
        "",
        f"- 文件: {raw.name}",
        f"- 行数: {total_rows}",
        f"- 独立问题数: {audit['unique_question_ids']}",
        f"- 独立医生数: {audit['unique_user_ids']}",
        f"- 精确重复行: {audit['exact_duplicate_rows']}",
        f"- 同问题同医生多行: {audit['same_question_same_doctor_multiple_rows']}",
        f"- 同时间戳多回答行/问题: {time_audit['same_timestamp_rows']} / {time_audit['same_timestamp_questions']}",
        f"- question_desc 空值: {text_audit['empty_question_desc']}",
        f"- answer 空值: {text_audit['empty_answer']}",
        f"- 缺失稳定回答 ID 字段: answer_event_proxy_key",
    ]
    md_path = run_dir / "audit_summary.md"
    md_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

    current_ptr = {
        "current_t1_schema_report": str(json_path),
        "last_run_utc": report["run"],
        "status": "completed",
        "total_rows": total_rows,
    }
    write_json(root / "data_app_chain" / "current_run.json", current_ptr)
    con.close()

    print(f"t1 完成: {json_path}")
    print(
        f"rows={total_rows} questions={audit['unique_question_ids']} "
        f"doctors={audit['unique_user_ids']} "
        f"dup_rows={audit['exact_duplicate_rows']} "
        f"same_ts_rows={time_audit['same_timestamp_rows']}"
    )
    print(f"status: completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
