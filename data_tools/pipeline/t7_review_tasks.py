"""t7: 生成待审草稿任务（历史回放 + 人工改写副本）。

- 历史回放: 把呼吸道候选 + 联合审阅适用咨询中的真实后续回答作为待审草稿。
- 人工改写: 在明确标记的副本中加入一项事实变化, 原始数据不改; 改写类型覆盖:
  未知写成否认 / 历史写成当前 / 医生提问写成患者确认 / 条件变无条件 /
  重述已有观点 / 加入有患者依据的补充。

所有改写副本均带 synthetic=true 与 derivation 字段, 不得用于估计真实平台错误发生率。
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from common import file_meta, make_run_dir, project_root, utc_now_iso, write_json


RULE_VERSION = "t7-review-tasks-v1"

RESP_TERMS = [
    "咳嗽",
    "咳痰",
    "发热",
    "咽痛",
    "鼻塞",
    "流涕",
    "气促",
    "胸痛",
    "呼吸困难",
]

REWRITE_TYPES = [
    "unknown_as_denial",
    "history_as_current",
    "question_as_confirmed",
    "conditional_as_unconditional",
    "reiterate_existing",
    "supported_supplement",
]

REWRITES_PER_TYPE = 30
SEED = 20261105


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        return list(value)
    except TypeError:
        return [value]


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？；;!?])", text)
    return [p.strip() for p in parts if p.strip()]


def _first_nonempty_sentence(text: str, min_len: int = 5) -> str:
    for sent in _split_sentences(text or ""):
        if len(sent) >= min_len:
            return sent
    return (text or "").strip()


def _choose_absent_symptom(patient_text: str, used: list[str]) -> str:
    for term in RESP_TERMS:
        if term not in (patient_text or "") and term not in used:
            return term
    return "肺部啰音"


def _mut_unknown_as_denial(
    text: str, patient_text: str, used: list[str], prior_texts: list[str]
) -> tuple[str, str]:
    term = _choose_absent_symptom(patient_text, used)
    reason = f"模拟改写: 患者文本未提及{term}, 草稿副本写成明确否认"
    return f"{text}患者没有{term}。", reason


def _mut_history_as_current(
    text: str, patient_text: str, used: list[str], prior_texts: list[str]
) -> tuple[str, str]:
    marker = None
    for word in ["昨天", "昨日", "前天", "一周前", "以前", "曾经", "既往", "去年", "数月前"]:
        if word in (patient_text or "") or word in text:
            marker = word
            break
    claim = "患者目前仍有不适。" if marker is None else f"患者目前仍存在此前提到的相关情况。"
    reason = "模拟改写: 把历史/过去措辞升级为当前状态"
    if marker:
        reason = f"模拟改写: 把原文中的时间标记“{marker}”升格为当前状态"
    return f"{text}{claim}", reason


def _mut_question_as_confirmed(
    text: str, patient_text: str, used: list[str], prior_texts: list[str]
) -> tuple[str, str]:
    has_question = any(
        q in (text or "") for q in ["是否", "有没有", "有无", "吗", "是吗"]
    )
    reason = "模拟改写: 医生提问/推测写成患者已确认"
    if not has_question:
        reason += "; 原文未出现显式提问，仍按模拟副本处理"
    return f"{text}患者已确认相关情况存在。", reason


def _mut_conditional_as_unconditional(
    text: str, patient_text: str, used: list[str], prior_texts: list[str]
) -> tuple[str, str]:
    has_condition = any(
        c in (text or "") for c in ["如果", "若", "建议", "可能", "需要", "必要时"]
    )
    reason = "模拟改写: 条件性表述写成无条件结论"
    if not has_condition:
        reason += "; 原文未检测到条件词，按模拟副本处理"
    return f"{text}因此患者必须立即采取上述处理。", reason


def _mut_reiterate_existing(
    text: str, patient_text: str, used: list[str], prior_texts: list[str]
) -> tuple[str, str]:
    sentence = ""
    for prior in prior_texts:
        sentence = _first_nonempty_sentence(prior)
        if sentence:
            break
    if not sentence:
        sentence = "注意休息，多喝水。"
    return f"{text}{sentence}", "模拟改写: 逐字重述已有前序观点，且未在草稿中标注来源"


def _mut_supported_supplement(
    text: str, patient_text: str, used: list[str], prior_texts: list[str]
) -> tuple[str, str]:
    term = next((t for t in RESP_TERMS if t in (patient_text or "")), "咳嗽")
    reason = f"模拟改写: 加入一条有患者依据的补充（患者文本含“{term}”）"
    return f"{text}建议结合患者提到的“{term}”及其出现时间进一步评估。", reason


MUTATORS: dict[str, Any] = {
    "unknown_as_denial": _mut_unknown_as_denial,
    "history_as_current": _mut_history_as_current,
    "question_as_confirmed": _mut_question_as_confirmed,
    "conditional_as_unconditional": _mut_conditional_as_unconditional,
    "reiterate_existing": _mut_reiterate_existing,
    "supported_supplement": _mut_supported_supplement,
}


def main() -> int:
    root = project_root()
    current = json.loads(
        (root / "data_app_chain" / "current_run.json").read_text(encoding="utf-8")
    )
    deid_path = current.get("current_t4_deidentified_messages")
    manifest_path = current.get("current_t6_replay_manifest")
    if not deid_path or not Path(deid_path).exists():
        print("t7 前置产物缺失: 先运行 t6（需要 t4 消息）", file=sys.stderr)
        return 2
    if not manifest_path or not Path(manifest_path).exists():
        print("t7 前置产物缺失: 先运行 t6", file=sys.stderr)
        return 2

    con = duckdb.connect(":memory:")
    msgs = con.execute(
        "SELECT message_id, consult_id, source_record_id, role, speaker_id, "
        "text, created_at "
        f"FROM read_parquet('{str(Path(deid_path)).replace(chr(92), '/')}')"
    ).df()
    manifest = con.execute(
        "SELECT consult_id, focus_answer_id, focus_message_id, cutoff_time, "
        "prior_message_ids, patient_message_ids, excluded_message_ids, "
        "exclusion_reason, focus_order_unreliable, respiratory_candidate, "
        "joint_review_eligible, candidate_requires_manual_review "
        f"FROM read_parquet('{str(Path(manifest_path)).replace(chr(92), '/')}')"
    ).df()

    cand = manifest[
        (manifest["respiratory_candidate"].fillna(False))
        & (manifest["joint_review_eligible"].fillna(False))
    ].copy()
    for list_col in [
        "prior_message_ids",
        "patient_message_ids",
        "excluded_message_ids",
    ]:
        cand[list_col] = cand[list_col].apply(_as_list)
    focus_map = {
        row["focus_message_id"]: row.to_dict() for _, row in cand.iterrows()
    }
    msg_lookup = {
        (row["consult_id"], row["message_id"]): row.to_dict() for _, row in msgs.iterrows()
    }

    tasks: list[dict[str, Any]] = []
    # 历史回放: 呼吸道候选且联合审阅可用的全部焦点
    hist_count = 0
    for _, row in cand.sort_values(["consult_id", "focus_message_id"]).iterrows():
        msg = msg_lookup.get((row.consult_id, row.focus_message_id))
        if msg is None:
            continue
        consult_msgs = msgs[msgs["consult_id"] == row.consult_id]
        tasks.append(
            {
                "task_id": f"t7-hist-{hist_count:05d}",
                "task_type": "history_replay",
                "derivation_type": "real_history",
                "consult_id": row.consult_id,
                "focus_answer_id": row.focus_answer_id,
                "focus_message_id": row.focus_message_id,
                "cutoff_time": row.cutoff_time,
                "draft_text": msg["text"],
                "draft_text_hash": _sha(msg["text"]),
                "base_answer_id": row.focus_answer_id,
                "prior_message_ids": list(row.prior_message_ids),
                "patient_message_ids": list(row.patient_message_ids),
                "excluded_message_ids": list(row.excluded_message_ids),
                "exclusion_reason": row.exclusion_reason or "",
                "scope_status": "respiratory_candidate",
                "candidate_requires_manual_review": bool(
                    row.candidate_requires_manual_review
                ),
                "synthetic": False,
                "usage_note": "真实历史回答仅作为待审草稿输入，不代表参考答案",
                "focus_order_unreliable": bool(row.focus_order_unreliable),
                "source_sentence_count": len(_split_sentences(msg["text"])),
                "draft_char_count": len(msg["text"]),
            }
        )
        hist_count += 1

    # 人工改写: 每个类型固定数量，按确定性轮转挑选不同咨询
    rewrite_consults = sorted(
        set(
            m["consult_id"]
            for m in focus_map.values()
            if len(_as_list(m.get("prior_message_ids"))) >= 2
        )
    )
    # 用固定下标轮转，不引入随机状态
    rewrite_count = 0
    used_symptom: list[str] = []
    consult_focus_cache: dict[str, list[dict[str, Any]]] = {}
    for cid in rewrite_consults:
        rows = [
            m
            for _, m in sorted(
                focus_map.items(),
                key=lambda kv: kv[1]["focus_message_id"],
            )
            if m["consult_id"] == cid and _as_list(m.get("prior_message_ids"))
        ]
        consult_focus_cache[cid] = rows

    for type_idx, rtype in enumerate(REWRITE_TYPES):
        base = (type_idx * REWRITES_PER_TYPE) % max(len(rewrite_consults), 1)
        for k in range(REWRITES_PER_TYPE):
            consult_id = rewrite_consults[
                (base + k * 7) % len(rewrite_consults)
            ]
            consult_rows = consult_focus_cache.get(consult_id, [])
            if not consult_rows:
                continue
            row = consult_rows[k % len(consult_rows)]
            msg = msg_lookup.get((consult_id, row["focus_message_id"]))
            if msg is None:
                continue
            patient_text = "\n".join(
                msg_lookup.get((consult_id, pid), {}).get("text", "")
                for pid in _as_list(row.get("patient_message_ids"))
            )
            prior_texts = [
                msg_lookup.get((consult_id, pid), {}).get("text", "")
                for pid in _as_list(row.get("prior_message_ids"))
            ]
            draft_text, reason = MUTATORS[rtype](
                msg["text"], patient_text, used_symptom, prior_texts
            )
            tasks.append(
                {
                    "task_id": f"t7-rewrite-{rewrite_count:05d}",
                    "task_type": "rewrite",
                    "derivation_type": rtype,
                    "derivation_location": "appended_sentence",
                    "derivation_reason": reason,
                    "consult_id": consult_id,
                    "focus_answer_id": row["focus_answer_id"],
                    "focus_message_id": row["focus_message_id"],
                    "cutoff_time": row.get("cutoff_time"),
                    "draft_text": draft_text,
                    "draft_text_hash": _sha(draft_text),
                    "base_answer_id": row["focus_answer_id"],
                    "base_text_hash": _sha(msg["text"]),
                    "prior_message_ids": list(_as_list(row.get("prior_message_ids"))),
                    "patient_message_ids": list(_as_list(row.get("patient_message_ids"))),
                    "excluded_message_ids": list(_as_list(row.get("excluded_message_ids"))),
                    "exclusion_reason": row.get("exclusion_reason") or "",
                    "scope_status": "respiratory_candidate",
                    "candidate_requires_manual_review": True,
                    "synthetic": True,
                    "usage_note": "人工改写副本；不得用于估计真实平台错误发生率",
                    "focus_order_unreliable": bool(row.get("focus_order_unreliable")),
                }
            )
            rewrite_count += 1

    run_dir = make_run_dir("t7")
    out = run_dir / "review_tasks.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for task in tasks:
            fh.write(json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n")

    report: dict[str, Any] = {
        "run": utc_now_iso(),
        "task": "t7_review_tasks",
        "status": "completed",
        "rule_version": RULE_VERSION,
        "seed": SEED,
        "input_deidentified_messages": file_meta(Path(deid_path)),
        "input_replay_manifest": file_meta(Path(manifest_path)),
        "output_review_tasks": str(out),
        "total_tasks": len(tasks),
        "history_replay_count": hist_count,
        "rewrite_count": rewrite_count,
        "rewrite_types": {
            t: sum(
                1 for task in tasks if task["synthetic"] and task["derivation_type"] == t
            )
            for t in REWRITE_TYPES
        },
        "consult_count_history": len(set(t["consult_id"] for t in tasks if not t["synthetic"])),
        "consult_count_rewrite": len(set(t["consult_id"] for t in tasks if t["synthetic"])),
        "integrity_checks": {
            "rewrite_changed_text": int(
                all(
                    t["draft_text_hash"] != t.get("base_text_hash", t["draft_text_hash"])
                    for t in tasks
                    if t["synthetic"]
                )
            ),
            "focus_not_in_prior": int(
                all(
                    t["focus_message_id"] not in set(t["prior_message_ids"])
                    for t in tasks
                )
            ),
            "focus_not_in_patient": int(
                all(
                    t["focus_message_id"] not in set(t["patient_message_ids"])
                    for t in tasks
                )
            ),
            "no_none_draft": int(all(bool(t["draft_text"]) for t in tasks)),
        },
        "risks": [
            "改写副本由规则生成，只用于流程与引用校验烟雾测试，不用于真实错误率",
            "改写中“历史转当前/提问转确认”未结合完整人工改写，需人工审阅副本后再用于评测",
            "年龄字段缺失，呼吸道候选尚未人工确认成人适用",
        ],
        "not_verified": [
            "未调用真实模型审阅",
            "人工改写副本未经真人逐条校对",
        ],
    }
    write_json(run_dir / "review_tasks_report.json", report)

    sample_csv = run_dir / "review_task_sample.csv"
    with sample_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "task_id",
                "task_type",
                "derivation_type",
                "consult_id",
                "focus_answer_id",
                "draft_chars",
                "draft_text",
            ]
        )
        sample = [t for t in tasks if not t["synthetic"]][:3] + [
            t for t in tasks if t["synthetic"]
        ][:12]
        for t in sample:
            writer.writerow(
                [
                    t["task_id"],
                    t["task_type"],
                    t["derivation_type"],
                    t["consult_id"],
                    t["focus_answer_id"],
                    len(t["draft_text"]),
                    t["draft_text"][:120],
                ]
            )
    report["sample_file"] = str(sample_csv)
    write_json(run_dir / "review_tasks_report.json", report)
    print(f"t7 ok: {out} ({len(tasks)} tasks; hist={hist_count}, rewrite={rewrite_count})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
