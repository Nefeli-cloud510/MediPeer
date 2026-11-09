"""t6: 构造严格前序 replay manifest。

对每条医生焦点回答生成:
- prior_message_ids: 同一咨询下、时间严格早于焦点且顺序可信的医生消息
- patient_message_ids: 同一咨询下、时间严格早于截止点且顺序可信的患者消息
- excluded_message_ids / exclusion_reason: 焦点自身、同时刻消息、后续消息、待核查消息

同时间戳的医生/患者消息一律不进入严格前序或截止前患者材料, 由人工核查原因保留。
焦点消息本身不进入 prior / patient 列表。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from common import file_meta, make_run_dir, project_root, utc_now_iso, write_json


RULE_VERSION = "t6-replay-manifest-v1"


def _iso(ts: Any) -> str:
    if hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%dT%H:%M:%S")
    return str(ts)


def _reason_label(reason: str) -> str:
    if "时间戳" in (reason or ""):
        return "same_timestamp_unreviewable"
    if "冲突" in (reason or ""):
        return "seq_conflict_unreviewable"
    return "unreviewable"


def _manual_default_rows() -> list[str]:
    return [
        "consult_id",
        "focus_message_id",
        "focus_answer_id",
        "cutoff_time",
        "manifest_prior_count",
        "direct_filter_prior_count",
        "manifest_patient_count",
        "direct_filter_patient_count",
        "same_timestamp_excluded_count",
        "later_excluded_count",
        "focus_in_prior",
        "focus_in_patient",
        "check_note",
    ]


def _write_parquet_from_df(con: duckdb.DuckDBPyConnection, df: pd.DataFrame, out: Path) -> None:
    tmp = out.with_suffix(".tmp.parquet")
    if tmp.exists():
        tmp.unlink()
    con.execute(f"COPY (SELECT * FROM df) TO '{str(tmp).replace(chr(92), '/')}' (FORMAT PARQUET)")
    tmp.replace(out)


def main() -> int:
    root = project_root()
    current = json.loads(
        (root / "data_app_chain" / "current_run.json").read_text(encoding="utf-8")
    )
    deid_path = current.get("current_t4_deidentified_messages")
    case_path = current.get("current_t5_case_index")
    if not deid_path or not Path(deid_path).exists():
        print("t6 前置产物缺失: 先运行 t4", file=sys.stderr)
        return 2
    if not case_path or not Path(case_path).exists():
        print("t6 前置产物缺失: 先运行 t5", file=sys.stderr)
        return 2

    run_dir = make_run_dir("t6")
    report: dict[str, Any] = {
        "run": utc_now_iso(),
        "task": "t6_replay_manifest",
        "status": "completed",
        "rule_version": RULE_VERSION,
        "input_deidentified_messages": file_meta(Path(deid_path)),
        "input_case_index": file_meta(Path(case_path)),
        "output_replay_manifest": None,
        "total_focus_rows": None,
        "focus_rows_by_scope": {},
        "prior_count_histogram": {},
        "zero_prior_focus_count": None,
        "integrity_checks": {},
        "same_timestamp_focus_count": None,
        "excluded_reason_counts": {},
        "manual_check_file": None,
        "risks": [],
        "not_verified": [],
    }

    con = duckdb.connect(":memory:")
    msgs = con.execute(
        "SELECT message_id, consult_id, source_record_id, role, speaker_id, "
        "text, created_at, needs_review, COALESCE(review_reason, '') AS review_reason, "
        "source_question_id "
        f"FROM read_parquet('{str(Path(deid_path)).replace(chr(92), '/')}') "
        "ORDER BY consult_id, created_at, message_id"
    ).df()
    msgs["created_at"] = pd.to_datetime(msgs["created_at"], errors="coerce")
    msgs["needs_review"] = msgs["needs_review"].fillna(False).astype(bool)

    idx = con.execute(
        "SELECT consult_id, respiratory_candidate, candidate_requires_manual_review, "
        "joint_review_eligible, scope_status, scope_reason, doctor_answer_count, "
        "identified_doctor_count, patient_message_count, same_timestamp_message_count, "
        "order_conflict_message_count, order_unknown_message_count "
        f"FROM read_parquet('{str(Path(case_path)).replace(chr(92), '/')}')"
    ).df()
    scope_map = {
        row["consult_id"]: row.to_dict() for _, row in idx.iterrows()
    }

    records: list[dict[str, Any]] = []
    excluded_reason_counts: dict[str, int] = {}

    for consult_id, grp in msgs.groupby("consult_id", sort=False):
        grp = grp.copy()
        grp = grp.sort_values(["created_at", "message_id"])
        scope = scope_map.get(consult_id, {})
        for focus in grp[grp["role"] == "doctor"].itertuples():
            cutoff = focus.created_at
            prior: list[str] = []
            patient: list[str] = []
            excluded: dict[str, str] = {}
            for m in grp.itertuples():
                if m.message_id == focus.message_id:
                    excluded[m.message_id] = "focus_self"
                    continue
                if bool(getattr(m, "needs_review")):
                    excluded[m.message_id] = f"order_unreviewable:{_reason_label(m.review_reason)}"
                    excluded_reason_counts["order_unreviewable"] = (
                        excluded_reason_counts.get("order_unreviewable", 0) + 1
                    )
                    continue
                if m.created_at < cutoff:
                    if m.role == "doctor":
                        prior.append(m.message_id)
                    elif m.role == "patient":
                        patient.append(m.message_id)
                    else:
                        excluded[m.message_id] = "unknown_role_before_cutoff"
                elif m.created_at == cutoff:
                    if m.role == "doctor":
                        excluded[m.message_id] = "doctor_same_timestamp"
                        excluded_reason_counts["doctor_same_timestamp"] = (
                            excluded_reason_counts.get("doctor_same_timestamp", 0) + 1
                        )
                    else:
                        excluded[m.message_id] = "patient_same_cutoff_timestamp"
                        excluded_reason_counts["patient_same_cutoff_timestamp"] = (
                            excluded_reason_counts.get("patient_same_cutoff_timestamp", 0) + 1
                        )
                else:
                    if m.role == "doctor":
                        excluded[m.message_id] = "doctor_later_than_focus"
                        excluded_reason_counts["doctor_later_than_focus"] = (
                            excluded_reason_counts.get("doctor_later_than_focus", 0) + 1
                        )
                    elif m.role == "patient":
                        excluded[m.message_id] = "patient_later_than_cutoff"
                        excluded_reason_counts["patient_later_than_cutoff"] = (
                            excluded_reason_counts.get("patient_later_than_cutoff", 0) + 1
                        )
                    else:
                        excluded[m.message_id] = "unknown_role_after_cutoff"

            excluded_keys = sorted(excluded.keys())
            records.append(
                {
                    "consult_id": consult_id,
                    "focus_answer_id": focus.source_record_id,
                    "focus_message_id": focus.message_id,
                    "cutoff_time": _iso(cutoff),
                    "prior_message_ids": prior,
                    "patient_message_ids": patient,
                    "excluded_message_ids": excluded_keys,
                    "exclusion_reason": json.dumps(
                        {k: excluded[k] for k in excluded_keys},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "excluded_count": len(excluded_keys),
                    "focus_order_unreliable": bool(getattr(focus, "needs_review")),
                    "scope_status": scope.get("scope_status"),
                    "respiratory_candidate": scope.get("respiratory_candidate", False),
                    "joint_review_eligible": scope.get("joint_review_eligible", False),
                    "candidate_requires_manual_review": scope.get(
                        "candidate_requires_manual_review", False
                    ),
                }
            )

    manifest_df = pd.DataFrame(records)
    manifest_path = run_dir / "replay_manifest.parquet"
    _write_parquet_from_df(con, manifest_df, manifest_path)
    report["output_replay_manifest"] = str(manifest_path)
    report["total_focus_rows"] = int(len(manifest_df))
    report["same_timestamp_focus_count"] = int(
        manifest_df["focus_order_unreliable"].fillna(False).astype(bool).sum()
    )

    scope_counts = (
        manifest_df.groupby(["scope_status", "respiratory_candidate", "joint_review_eligible"])
        .size()
        .to_dict()
    )
    report["focus_rows_by_scope"] = {str(k): int(v) for k, v in scope_counts.items()}

    prior_counts = manifest_df["prior_message_ids"].apply(len)
    hist = prior_counts.value_counts().sort_index().to_dict()
    report["prior_count_histogram"] = {str(k): int(v) for k, v in hist.items()}
    report["zero_prior_focus_count"] = int((prior_counts == 0).sum())
    report["excluded_reason_counts"] = {
        k: int(v) for k, v in sorted(excluded_reason_counts.items())
    }

    # 程序化完整性校验（逐行检查，不跨 focus 混算）
    prior_all = [x for row in manifest_df["prior_message_ids"] for x in row]
    patient_all = [x for row in manifest_df["patient_message_ids"] for x in row]
    msg_info = {
        (r.consult_id, r.message_id): (r.created_at, bool(getattr(r, "needs_review")))
        for r in msgs.itertuples()
    }
    focus_not_prior = True
    focus_not_patient = True
    patient_strict = True
    doctor_strict = True
    no_unreviewable_prior = True
    for r in manifest_df.itertuples():
        own_prior = set(r.prior_message_ids)
        own_patient = set(r.patient_message_ids)
        if r.focus_message_id in own_prior:
            focus_not_prior = False
        if r.focus_message_id in own_patient:
            focus_not_patient = False
        cutoff = pd.Timestamp(r.cutoff_time)
        for pid in own_patient:
            info = msg_info.get((r.consult_id, pid))
            if info is None or not (info[0] < cutoff):
                patient_strict = False
            if info is not None and info[1]:
                no_unreviewable_prior = False
        for mid in own_prior:
            info = msg_info.get((r.consult_id, mid))
            if info is None or not (info[0] < cutoff):
                doctor_strict = False
            if info is not None and info[1]:
                no_unreviewable_prior = False

    same_ts_needs_review = int(
        (
            msgs["needs_review"]
            & msgs["review_reason"].astype(str).str.contains("时间戳", na=False)
        ).sum()
    )
    report["integrity_checks"] = {
        "focus_not_in_prior": focus_not_prior,
        "focus_not_in_patient": focus_not_patient,
        "total_prior_refs": len(prior_all),
        "total_patient_refs": len(patient_all),
        "same_timestamp_unreviewable_excluded_count": same_ts_needs_review,
        "seq_conflict_unreviewable_excluded_count": int(
            (
                msgs["needs_review"]
                & msgs["review_reason"].astype(str).str.contains("冲突", na=False)
            ).sum()
        ),
        "patient_strictly_before_cutoff": patient_strict,
        "doctor_strict_prior_only": doctor_strict,
        "no_needs_review_in_prior": no_unreviewable_prior,
    }

    # 抽样逐例核对 CSV：与直接按时间过滤比较
    msg_lookup = {
        (r.consult_id, r.message_id): r
        for r in msgs.itertuples()
    }
    clean_candidates = manifest_df[
        (manifest_df["respiratory_candidate"])
        & (manifest_df["joint_review_eligible"])
        & (~manifest_df["focus_order_unreliable"])
        & (manifest_df["prior_message_ids"].apply(len) >= 1)
    ].head(8)
    same_ts_rows = manifest_df[
        manifest_df["focus_order_unreliable"]
    ].head(2)
    rows_for_check = pd.concat([clean_candidates, same_ts_rows])
    check_lines = [_manual_default_rows()]
    for row in rows_for_check.itertuples():
        consult_rows = msgs[msgs["consult_id"] == row.consult_id]
        cutoff = msg_lookup.get((row.consult_id, row.focus_message_id))
        cutoff_ts = cutoff.created_at if cutoff is not None else pd.NaT
        direct_prior = [
            m.message_id
            for m in consult_rows.itertuples()
            if m.role == "doctor"
            and m.message_id != row.focus_message_id
            and not bool(getattr(m, "needs_review"))
            and m.created_at < cutoff_ts
        ]
        direct_patient = [
            m.message_id
            for m in consult_rows.itertuples()
            if m.role == "patient"
            and not bool(getattr(m, "needs_review"))
            and m.created_at < cutoff_ts
        ]
        same_ts_count = sum(
            1
            for m in consult_rows.itertuples()
            if m.created_at == cutoff_ts and m.message_id != row.focus_message_id
        )
        later_count = sum(
            1
            for m in consult_rows.itertuples()
            if m.created_at is not None
            and cutoff_ts is not None
            and m.created_at > cutoff_ts
        )
        check_lines.append(
            [
                row.consult_id,
                row.focus_message_id,
                row.focus_answer_id,
                row.cutoff_time,
                len(row.prior_message_ids),
                len(direct_prior),
                len(row.patient_message_ids),
                len(direct_patient),
                same_ts_count,
                later_count,
                row.focus_message_id in direct_prior,
                row.focus_message_id in direct_patient,
                "compared_with_direct_filter",
            ]
        )

    manual_csv = run_dir / "replay_manifest_manual_check.csv"
    with manual_csv.open("w", encoding="utf-8", newline="") as fh:
        import csv

        writer = csv.writer(fh)
        writer.writerows(check_lines)
    report["manual_check_file"] = str(manual_csv)
    report["manual_check_rows"] = len(check_lines) - 1

    report["risks"] = [
        "同时间戳消息被排除而非排序, 对应焦点需人工复核 order 用途",
        "t3 标记的 seq 冲突消息即使时间不同也排除于严格前序",
        "cutoff 使用源时间戳, 未经人工核对时区与平台显示口径",
    ]
    report["not_verified"] = [
        "未对全量 13,406 条焦点逐条人工核对时间语义",
        "抽样核对只保证程序过滤与直接时间过滤一致, 不保证源时间本身正确",
    ]
    write_json(run_dir / "replay_manifest_report.json", report)
    print(f"t6 ok: {manifest_path} ({len(manifest_df)} focus rows)")
    print(f"manual check: {manual_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
