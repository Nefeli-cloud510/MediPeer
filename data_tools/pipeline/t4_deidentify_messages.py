"""t4: 脱敏并保留脱敏后证据定位。

基于规范化消息表做规则化脱敏：识别身份证号、手机号、带上下文的电话/微信号/
QQ/邮箱、详细地址、门牌号和明确姓名表达，替换为固定占位符。原始替换值、
脱敏前后偏移单独写入受限映射文件，不进入应用输出或日志。
"""

from __future__ import annotations

import csv
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

import duckdb

from common import file_meta, make_run_dir, project_root, utc_now_iso, write_json


RULE_VERSION = "t4-deid-v3"

PII_PARTS = [
    ("id_label", r"(?:身份证(?:号码|号)?|ID号?|证件号)[:：]?\s*(?:是|为)?\s*"
                 r"[0-9A-Za-z]{6,20}"),
    ("id_card", r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])"
                r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]"),
    ("mobile", r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ("phone_label", r"(?:电话|电话号码|座机|联系方式)[:：]?\s*"
                     r"[0-9]{3,4}[- ]?[0-9]{7,8}"),
    ("landline", r"(?<!\d)0\d{2,3}[- ]?\d{7,8}(?!\d)"),
    ("hotline", r"(?<!\d)95\d{3,6}(?!\d)"),
    ("wechat", r"(?:微信号?|wx|vx|weixin|WeChat)[:：]?\s*[a-zA-Z][-_a-zA-Z0-9]{5,19}"
               r"|wxid_[a-zA-Z0-9_-]{8,24}"),
    ("qq", r"(?:QQ|扣扣|qq)[:：]?\s*[1-9]\d{4,11}"),
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("url", r"https?://[A-Za-z0-9._~:/?#@!$&'()*+,;=%\-]{3,1000}"
            r"|www\.[A-Za-z0-9._-]{2,120}"
            r"|(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
            r"(?:com|cn|net|org|gov|edu|io|co|info|top|cc|me|mobi|vip)"
            r"(?:\.cn)?(?::\d{2,5})?(?:/[\w./?=&#%+\-~@:]{0,160})?"),
    ("address", r"(?:地址|住址|家庭地址|家庭住址|现住址|现居地)[:：]\s*"
                r"[^，。；!?！？\n]{2,60}"),
    ("door", r"\d{1,5}\s*(?:栋|幢|号楼|单元|室|楼层|号院)"
             r"(?:[^\s，。；!?！？\n]{0,6})"),
    ("name", r"(?:姓名|我叫|名字叫)[:：]?\s*[\u4e00-\u9fa5]{2,4}"
             r"|我姓[\u4e00-\u9fa5]"),
]

PLACEHOLDERS = {
    "id_label": "[身份证号]",
    "id_card": "[身份证号]",
    "mobile": "[手机号]",
    "phone_label": "[联系电话]",
    "landline": "[联系电话]",
    "hotline": "[客服电话]",
    "wechat": "[微信号]",
    "qq": "[QQ号]",
    "email": "[邮箱]",
    "url": "[链接]",
    "address": "[详细地址]",
    "door": "[门牌号]",
    "name": "[姓名]",
}

PII_REGEX = re.compile(
    "|".join(f"(?P<{name}>{pat})" for name, pat in PII_PARTS)
)

SUSPICIOUS_REGEXES = [
    re.compile(r"(?<!\d)\d{8,}(?!\d)"),
    re.compile(
        r"(?:身份证(?:号|号码)?|ID号?|证件号|手机号|微信号?|wxid_|vx|wx|"
        r"QQ|扣扣|邮箱|银行卡号?)[:：]?\s*(?:是|为)?\s*[A-Za-z0-9_-]{6,}"
    ),
    re.compile(r"(?:电话|座机|手机|联系方式)[:：]?\s*[0-9]{6,}"),
    re.compile(r"(?:地址|住址)[:：]"),
]

LONG_DIGIT_EXCEPTIONS = re.compile(
    r"(?:国食健字|国药准字|注册证号|保健食品批准文号|卫生许可证号|"
    r"生产许可证号|医疗器械注册证号)\s*[A-Za-z]?$"
)

CLINICAL_MARKERS = [
    "发热",
    "发烧",
    "咳嗽",
    "咳",
    "痰",
    "喘息",
    "气短",
    "胸闷",
    "胸痛",
    "咽痛",
    "否认",
    "没有",
    "曾经",
    "目前",
    "现在",
]


def _deidentify(text: str) -> tuple[str, list[dict[str, Any]], list[str]]:
    mappings: list[dict[str, Any]] = []
    parts: list[str] = []
    cursor = 0
    for match in PII_REGEX.finditer(text):
        kind = next(name for name, _pat in PII_PARTS if match.group(name))
        placeholder = PLACEHOLDERS[kind]
        deid_start = len("".join(parts))
        parts.append(text[cursor : match.start()])
        deid_start = len("".join(parts))
        parts.append(placeholder)
        deid_end = len("".join(parts))
        mappings.append(
            {
                "message_id": None,
                "pii_type": kind,
                "placeholder": placeholder,
                "original_start": match.start(),
                "original_end": match.end(),
                "original_value": text[match.start() : match.end()],
                "deid_start": deid_start,
                "deid_end": deid_end,
            }
        )
        cursor = match.end()
    parts.append(text[cursor:])
    deid_text = "".join(parts)

    suspicious: list[str] = []
    for idx, regex in enumerate(SUSPICIOUS_REGEXES):
        hit = regex.search(deid_text)
        if idx == 0 and hit:
            hit = None
            for m in re.finditer(r"(?<!\d)\d{8,}(?!\d)", deid_text):
                context = deid_text[max(0, m.start() - 12) : m.start()]
                if not LONG_DIGIT_EXCEPTIONS.search(context):
                    hit = m
                    break
        if hit:
            if idx == 0:
                suspicious.append("长数字串疑似敏感信息未覆盖")
            else:
                suspicious.append("敏感词后跟长串疑似未覆盖")

    for marker in CLINICAL_MARKERS:
        before = text.count(marker)
        after = deid_text.count(marker)
        if before > after:
            suspicious.append(f"临床标记词被脱敏改动: {marker}")
            break

    return deid_text, mappings, suspicious


def _fmt_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and "T" in value and value.endswith("Z"):
        return value
    return value


def main() -> int:
    root = project_root()
    current = json.loads(
        (root / "data_app_chain" / "current_run.json").read_text(encoding="utf-8")
    )
    norm_path_ok = current.get("current_t3_normalized_messages")
    if not norm_path_ok or not Path(norm_path_ok).exists():
        print("t4 前置产物缺失: 先运行 t3_normalize_messages.py", file=sys.stderr)
        return 2
    norm_path = Path(norm_path_ok)

    run_dir = make_run_dir("t4")
    report: dict[str, Any] = {
        "run": utc_now_iso(),
        "task": "t4_deidentify_messages",
        "status": "completed",
        "rule_version": RULE_VERSION,
        "input_file": file_meta(norm_path),
        "output_file": None,
        "total_messages": None,
        "deid_status_counts": {},
        "replacement_counts_by_type": {},
        "suspicious_counts": {},
        "needs_review_reason_counts": {},
        "clinical_marker_loss_message_count": None,
        "remaining_raw_match_message_count": None,
        "random_check_changed": [],
        "random_check_needs_review": [],
        "restricted_mapping_file": None,
        "risks": [],
        "not_verified": [],
    }

    con = duckdb.connect(":memory:")
    path_sql = str(norm_path).replace("\\", "/").replace("'", "''")
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW norm AS SELECT * FROM read_parquet('{path_sql}')"
    )
    columns = [
        "message_id",
        "consult_id",
        "source_record_id",
        "role",
        "speaker_id",
        "text",
        "created_at",
        "order_basis",
        "needs_review",
        "review_reason",
        "source_platform",
        "extraction_rule_version",
        "source_question_id",
        "source_seq",
        "source_doctor_user_id",
    ]
    rows = con.execute(
        f"SELECT {', '.join(columns)} FROM norm ORDER BY consult_id, message_id"
    ).fetchall()
    total = len(rows)
    report["total_messages"] = total

    con.execute(
        """
        CREATE TABLE deid_tmp (
            message_id VARCHAR,
            consult_id VARCHAR,
            source_record_id VARCHAR,
            role VARCHAR,
            speaker_id VARCHAR,
            text VARCHAR,
            created_at TIMESTAMP,
            order_basis VARCHAR,
            needs_review BOOLEAN,
            review_reason VARCHAR,
            source_platform VARCHAR,
            extraction_rule_version VARCHAR,
            source_question_id BIGINT,
            source_seq BIGINT,
            source_doctor_user_id VARCHAR,
            deid_status VARCHAR,
            deid_review_reason VARCHAR
        )
        """
    )

    mapping_rows: list[dict[str, Any]] = []
    status_counter = {"unchanged": 0, "changed": 0, "needs_review": 0}
    type_counter: dict[str, int] = {}
    reason_counter: dict[str, int] = {}
    marker_loss_count = 0
    remaining_raw_count = 0
    changed_ids: list[str] = []
    needs_review_ids: list[str] = []

    db_rows: list[tuple[Any, ...]] = []
    for row in rows:
        message_id, text = row[0], row[5]
        deid_text, mappings, suspicious = _deidentify(text)
        for mapping in mappings:
            mapping["message_id"] = message_id
            mapping_rows.append(mapping)
            type_counter[mapping["pii_type"]] = (
                type_counter.get(mapping["pii_type"], 0) + 1
            )

        changed = bool(mappings)
        if suspicious:
            status = "needs_review"
            reason = "；".join(sorted(set(suspicious)))
            reason_counter[reason] = reason_counter.get(reason, 0) + 1
            needs_review_ids.append(message_id)
            if any("临床标记词" in s for s in suspicious):
                marker_loss_count += 1
        elif changed:
            status = "changed"
            reason = ""
            changed_ids.append(message_id)
        else:
            status = "unchanged"
            reason = ""

        status_counter[status] = status_counter.get(status, 0) + 1
        if PII_REGEX.search(deid_text):
            remaining_raw_count += 1

        db_rows.append(
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                deid_text,
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                RULE_VERSION,
                row[12],
                row[13],
                row[14],
                status,
                reason,
            )
        )

    con.executemany(
        "INSERT INTO deid_tmp VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        db_rows,
    )
    out_parquet = run_dir / "deidentified_messages.parquet"
    out_sql = str(out_parquet).replace("\\", "/").replace("'", "''")
    con.execute(
        f"COPY (SELECT * FROM deid_tmp "
        f"ORDER BY consult_id, message_id) TO '{out_sql}' (FORMAT PARQUET)"
    )
    report["output_file"] = {
        "path": str(out_parquet),
        "size_bytes": out_parquet.stat().st_size,
    }
    report["deid_status_counts"] = status_counter
    report["replacement_counts_by_type"] = type_counter
    report["suspicious_counts"] = {
        "clinical_marker_loss_message_count": marker_loss_count,
        "remaining_raw_match_message_count": remaining_raw_count,
    }
    report["clinical_marker_loss_message_count"] = marker_loss_count
    report["remaining_raw_match_message_count"] = remaining_raw_count
    report["needs_review_reason_counts"] = reason_counter

    if remaining_raw_count:
        report["status"] = "failed"
        report["error"] = "deidentified text still contains recognized PII patterns"

    if mapping_rows:
        mapping_path = run_dir / "identity_mapping_restricted.csv"
        with mapping_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "message_id",
                    "pii_type",
                    "placeholder",
                    "original_start",
                    "original_end",
                    "original_value",
                    "deid_start",
                    "deid_end",
                ],
            )
            writer.writeheader()
            writer.writerows(mapping_rows)
        try:
            os.chmod(mapping_path, 0o600)
        except OSError:
            pass
        report["restricted_mapping_file"] = str(mapping_path)
        report["restricted_mapping_rows"] = len(mapping_rows)

    rng = random.Random(20261105)
    report["random_check_changed"] = rng.sample(
        changed_ids, k=min(30, len(changed_ids))
    )
    rng2 = random.Random(20261106)
    report["random_check_needs_review"] = rng2.sample(
        needs_review_ids, k=min(30, len(needs_review_ids))
    )

    risks = [
        "本步骤使用保守规则模式：未加上下文标签的座机、没有明确姓名表达的中文姓名"
        "等待原始大模型/人工复核，不做全量 NER 声称。",
        "国食健字/国药准字等批准文号数字串视为非 PII，不在长数字串待复核范围。",
        "原始替换值写入 identity_mapping_restricted.csv，属于受限文件，"
        "不得进入前端、公开导出、日志或参赛材料。",
        "拿不到原始 answer.parquet 前，真实稳定回答 ID 仍未补齐。",
    ]
    not_verified = [
        "无标注的任意人名/无标签座机/未按规则识别的 PII",
        "answer_event_proxy_key",
    ]
    report["risks"] = risks
    report["not_verified"] = not_verified

    write_json(run_dir / "deidentification_report.json", report)

    sample_ids = list(report["random_check_changed"])
    sample_ids += list(report["random_check_needs_review"])
    sample_path = run_dir / "deidentified_sample.csv"
    if sample_ids:
        sample_rows = con.execute(
            """
            SELECT message_id, consult_id, role, deid_status,
                   deid_review_reason, LEFT(text, 80) AS text_preview
            FROM deid_tmp
            WHERE message_id = ANY($ids)
            ORDER BY consult_id, message_id
            """,
            {"ids": sample_ids},
        ).fetchall()
        with sample_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "message_id",
                    "consult_id",
                    "role",
                    "deid_status",
                    "deid_review_reason",
                    "text_preview",
                ]
            )
            for row in sample_rows:
                writer.writerow(
                    [
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        (row[5] or "")[:80],
                    ]
                )

    summary = [
        "## t4 脱敏审计摘要",
        "",
        f"- 消息总数: {report['total_messages']}",
        f"- 状态: unchanged={status_counter.get('unchanged', 0)}, "
        f"changed={status_counter.get('changed', 0)}, "
        f"needs_review={status_counter.get('needs_review', 0)}",
        f"- 替换类型统计: {type_counter}",
        f"- 临床标记词受损消息数: {marker_loss_count}",
        f"- 脱敏后仍匹配原始 PII 模式消息数: {remaining_raw_count}",
        f"- 受限映射文件: identity_mapping_restricted.csv",
    ]
    md_path = run_dir / "audit_summary.md"
    md_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

    current_ptr = json.loads(
        (root / "data_app_chain" / "current_run.json").read_text(encoding="utf-8")
    )
    current_ptr["current_t4_deidentified_messages"] = str(out_parquet)
    current_ptr["last_run_utc"] = report["run"]
    current_ptr["status"] = report["status"]
    write_json(root / "data_app_chain" / "current_run.json", current_ptr)
    con.close()

    print(f"t4 完成: {out_parquet}")
    print(
        f"messages={report['total_messages']} "
        f"changed={status_counter.get('changed', 0)} "
        f"needs_review={status_counter.get('needs_review', 0)} "
        f"marker_loss={marker_loss_count} "
        f"remaining_raw={remaining_raw_count}"
    )
    print(f"status: {report['status']}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
