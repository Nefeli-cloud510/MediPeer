"""t0: 定位输入文件，建立只读数据清单。

原始文件放在 raw_input/（可由 MEDIPEEPER_RAW_INPUT_DIR 覆盖）。
上传文件后重跑即可更新 sha256、大小、修改时间；旧运行输出保留在
data_app_chain/runs/<run_id>/，不会被覆盖。
"""

from __future__ import annotations

import sys
from pathlib import Path

from common import file_meta, make_run_dir, project_root, utc_now_iso, write_json


EXPECTED_INPUTS = [
    {
        "name": "微医 · 医生回答",
        "file_candidates": ["answer.parquet"],
        "platform": "weiyi",
        "role": "weiyi_answer",
        "required": False,
    },
    {
        "name": "微医 · 患者问题",
        "file_candidates": ["question.parquet"],
        "platform": "weiyi",
        "role": "weiyi_question",
        "required": False,
    },
    {
        "name": "微医 · 医院信息",
        "file_candidates": ["wy_hospital.parquet", "hospital.parquet"],
        "platform": "weiyi",
        "role": "weiyi_hospital",
        "required": False,
    },
    {
        "name": "好大夫 · 六列中间表",
        "file_candidates": [
            "haodf_consult_intermediate.parquet",
            "haodf_consult_intermediate.csv",
            "hdf_*.parquet",
            "hdf_*.csv",
        ],
        "platform": "haodafu",
        "role": "haodafu_consult",
        "required": False,
    },
    {
        "name": "微医 · 分层抽样问答样本（合并文件）",
        "file_candidates": [
            "major5_stratified_question_samples_answers.parquet",
            "major5_*.parquet",
        ],
        "platform": "weiyi",
        "role": "weiyi_combined_sample",
        "required": True,
    },
]


def candidate_names(entry: dict) -> list[str]:
    names: list[str] = []
    for pattern in entry["file_candidates"]:
        names.append(pattern)
    return names


def find_matching_file(raw_dir: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            matches = sorted(raw_dir.glob(pattern))
            if matches:
                return matches[0]
        else:
            direct = raw_dir / pattern
            if direct.exists() and direct.is_file():
                return direct
    return None


def scan_upload_dir(raw_dir: Path) -> list[dict]:
    """扫描上传区全部文件；只计算元数据，不改动任何文件。"""
    found: list[dict] = []
    if not raw_dir.exists():
        return found
    for path in sorted(raw_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name == "README.md":
            continue
        meta = file_meta(path)
        meta["schema_verified"] = False
        found.append(meta)
    return found


def main() -> int:
    raw_dir = project_root() / "raw_input"
    env_dir = sys.argv[1] if len(sys.argv) > 1 else None
    if env_dir:
        raw_dir = Path(env_dir)
    raw_dir = Path(raw_dir).resolve()

    scan_dir = raw_dir if raw_dir.exists() else None
    found_files = scan_upload_dir(raw_dir) if scan_dir else []
    found_by_name = {}
    for item in found_files:
        found_by_name[Path(item["path"]).name] = item

    required_status = []
    for entry in EXPECTED_INPUTS:
        patterns = candidate_names(entry)
        match = find_matching_file(raw_dir, patterns) if scan_dir else None
        if match is not None:
            status = "uploaded"
            item = found_by_name.get(match.name)
        else:
            status = "pending_upload"
            item = None
        required_status.append(
            {
                "name": entry["name"],
                "platform": entry["platform"],
                "role": entry["role"],
                "required": entry["required"],
                "file_candidates": patterns,
                "status": status,
                "file": item,
            }
        )

    research_metadata = (
        project_root()
        / ".."
        / "medical_research"
        / "metadata"
        / "schema_mapping.yml"
    )
    research_inventory = (
        project_root()
        / ".."
        / "medical_research"
        / "outputs"
        / "audit"
        / "00_inventory_summary.json"
    )

    field_locations_expected = [
        {
            "product_field": "source_question_id",
            "weiyi_table": "question.parquet / answer.parquet",
            "weiyi_column": "question_id",
            "hdf_table": "好大夫中间表",
            "hdf_column": "consult_id",
            "verified_in_t0": False,
        },
        {
            "product_field": "patient_text",
            "weiyi_table": "question.parquet",
            "weiyi_column": "question_desc",
            "hdf_table": "好大夫中间表",
            "hdf_column": "table_text / consult_text",
            "verified_in_t0": False,
        },
        {
            "product_field": "answer_text",
            "weiyi_table": "answer.parquet",
            "weiyi_column": "answer",
            "hdf_table": "好大夫中间表",
            "hdf_column": "consult_text / table_text",
            "verified_in_t0": False,
        },
        {
            "product_field": "doctor_id",
            "weiyi_table": "answer.parquet",
            "weiyi_column": "user_id",
            "hdf_table": "好大夫中间表",
            "hdf_column": "physician_id / doctor_key",
            "verified_in_t0": False,
        },
        {
            "product_field": "answer_time",
            "weiyi_table": "answer.parquet",
            "weiyi_column": "create_time",
            "hdf_table": "好大夫中间表",
            "hdf_column": "待字段审计确认",
            "verified_in_t0": False,
        },
        {
            "product_field": "department",
            "weiyi_table": "answer.parquet",
            "weiyi_column": "dept",
            "hdf_table": "好大夫中间表",
            "hdf_column": "待字段审计确认",
            "verified_in_t0": False,
        },
    ]

    all_uploaded = all(
        (item["status"] == "uploaded")
        for item in required_status
        if item["required"]
    )

    manifest = {
        "run": utc_now_iso(),
        "task": "t0_source_manifest",
        "input_dir": str(raw_dir),
        "status": "ready_for_schema_verification" if all_uploaded else "pending_upload",
        "upload_area_placeholder": "上传文件到 raw_input/ 后重跑本脚本，旧运行文件保留",
        "required_inputs": required_status,
        "found_files": found_files,
        "field_locations_expected": field_locations_expected,
        "research_metadata_evidence": {
            "schema_mapping_path": (
                str(research_metadata) if research_metadata.exists() else None
            ),
            "inventory_summary_path": (
                str(research_inventory) if research_inventory.exists() else None
            ),
            "note": "仅作为背景参考；字段是否可读、是否失真由 t1 对真实上传文件复核。",
        },
        "raw_read_only_policy": "raw_input 与上传文件只读；所有派生输出写入 data_app_chain/runs/。",
    }

    run_dir = make_run_dir("t0")
    manifest_path = run_dir / "source_manifest.json"
    write_json(manifest_path, manifest)

    current_ptr = {
        "current_t0_manifest": str(manifest_path),
        "last_run_utc": manifest["run"],
        "status": manifest["status"],
    }
    write_json(project_root() / "data_app_chain" / "current_run.json", current_ptr)

    print(f"t0 完成: {manifest_path}")
    print(f"状态: {manifest['status']}")
    for item in required_status:
        print(f"  {item['status']:14s} {item['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

