"""t20: 真实模型同病例三轮审阅验收。

通过正在运行的 MediPeer API 完成：

1. 初次审阅：草稿“患者昨天开始有痰，目前无发热。”
2. 补录患者回复“我今天早上发过烧，现在没量。”后再次审阅。
3. 修改草稿为“患者昨天开始有痰，自述今晨发热，目前体温未提供。”后再次审阅。

脚本只通过 API 触发，不写死模型答案；失败运行保留在服务端数据库并写入产物目录。

用法：

    python data_tools/pipeline/t20_model_acceptance.py \
        --base-url http://127.0.0.1:8001
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    from data_tools.pipeline.t20_semantic_checks import run_checks
except ModuleNotFoundError:
    from t20_semantic_checks import run_checks


ROOT = Path(__file__).resolve().parents[2]
FOCUS = "wy-fq-a-2003-focus"
SAME_TIME = "wy-fq-a-2004-same-time"
PRIOR_DOCTORS = ["wy-fq-a-2001", "wy-fq-a-2002"]
PATIENT_FIRST = "wy-fq-q-1001"
ROUND1_DRAFT = "患者昨天开始有痰，目前无发热。"
SUPPLEMENT_TEXT = "我今天早上发过烧，现在没量。"
ROUND3_DRAFT = "患者昨天开始有痰，自述今晨发热，目前体温未提供。"
EXPECTED_EXCLUDED = {FOCUS, SAME_TIME}


def _now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _duration_seconds(started_at: str | None, finished_at: str | None) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
        return max(0.0, (finish - start).total_seconds())
    except ValueError:
        return None


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = {"text": response.text}
    if not isinstance(body, dict):
        body = {"payload": body}
    return body


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    out_dir: Path,
    tag: str,
    **kwargs: Any,
) -> tuple[int, dict[str, Any]]:
    started = time.perf_counter()
    try:
        response = client.request(method, url, **kwargs)
        body = _response_json(response)
        elapsed = time.perf_counter() - started
        request_payload = json.loads(json.dumps(kwargs))
        if "headers" in request_payload:
            headers = request_payload["headers"]
            if "Authorization" in headers:
                headers["Authorization"] = "Bearer <redacted>"
        response_payload = json.loads(json.dumps(body))
        if isinstance(response_payload, dict) and "token" in response_payload:
            if response_payload["token"]:
                response_payload["token"] = "<redacted-session-token>"
        _write_json(
            out_dir / f"http_{tag}.json",
            {
                "request": {"method": method, "url": url, "kwargs": request_payload},
                "status_code": response.status_code,
                "elapsed_seconds": round(elapsed, 3),
                "response": response_payload,
            },
        )
        return response.status_code, body
    except httpx.HTTPError as exc:
        elapsed = time.perf_counter() - started
        request_payload = json.loads(json.dumps(kwargs))
        if "headers" in request_payload:
            headers = request_payload["headers"]
            if "Authorization" in headers:
                headers["Authorization"] = "Bearer <redacted>"
        _write_json(
            out_dir / f"http_{tag}.json",
            {
                "request": {"method": method, "url": url, "kwargs": request_payload},
                "status_code": None,
                "elapsed_seconds": round(elapsed, 3),
                "error_class": exc.__class__.__name__,
                "error": str(exc),
            },
        )
        raise


def _validate_scope(scope: dict[str, Any], run_dir: Path, tag: str) -> None:
    refs = {
        "prior_answer_message_ids": PRIOR_DOCTORS,
        "patient_message_ids_expect_first": PATIENT_FIRST,
    }
    actual_prior = list(scope.get("prior_answer_message_ids") or [])
    actual_patients = list(scope.get("patient_message_ids") or [])
    checks = {
        "order_quality_ok": scope.get("order_quality") == "ok",
        "prior_doctors_exact": actual_prior == PRIOR_DOCTORS,
        "first_patient_present": PATIENT_FIRST in actual_patients,
        "focus_excluded": FOCUS not in {
            message["id"] for message in scope.get("visible_messages", [])
        },
        "same_time_excluded": SAME_TIME
        not in {message["id"] for message in scope.get("visible_messages", [])},
        "excluded_ids_include_focus_and_same": EXPECTED_EXCLUDED.issubset(
            set(scope.get("excluded_message_ids") or [])
        ),
    }
    _write_json(
        run_dir / f"scope_{tag}.json",
        {"scope": scope, "checks": checks},
    )
    if not all(checks.values()):
        raise SystemExit(f"scope check failed ({tag}): {checks}")


def _visible_texts(replay_body: dict[str, Any]) -> dict[str, str]:
    return {
        message["id"]: message["text"]
        for message in replay_body.get("visible_messages", [])
    }


def _validate_findings(
    run_body: dict[str, Any],
    replay_body: dict[str, Any],
    run_dir: Path,
    tag: str,
) -> dict[str, Any]:
    messages_by_id = {
        message["id"]: message
        for message in replay_body.get("visible_messages", [])
    }
    checks: list[dict[str, Any]] = []
    for finding in run_body.get("findings", []):
        ref_checks = []
        for ref in finding.get("evidence_refs", []):
            message_id = ref.get("message_id", "")
            snippet = ref.get("snippet", "")
            message = messages_by_id.get(message_id)
            present = bool(message) and bool(snippet) and snippet in message["text"]
            role_ok = bool(message) and message.get("role") == ref.get("role")
            ref_checks.append(
                {
                    "message_id": message_id,
                    "role": ref.get("role"),
                    "snippet": snippet,
                    "snippet_present": present,
                    "role_matches": role_ok,
                }
            )
        checks.append(
            {
                "draft_sentence_text": finding.get("draft_sentence_text"),
                "contribution": finding.get("contribution"),
                "patient_grounding": finding.get("patient_grounding"),
                "evidence_ref_checks": ref_checks,
            }
        )
    validation = {
        "passes": all(
            ref["snippet_present"] and ref["role_matches"]
            for check in checks
            for ref in check["evidence_ref_checks"]
        ),
        "findings": checks,
    }
    _write_json(run_dir / f"evidence_validation_{tag}.json", validation)
    return validation


def _load_run_records(db_path: Path, run_ids: list[str]) -> dict[str, Any]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    rows: dict[str, Any] = {}
    try:
        for run_id in run_ids:
            run = con.execute(
                """
                SELECT id, case_id, case_version_id, draft_id, draft_version_id,
                       focus_message_id, scope_message_ids, excluded_message_ids,
                       exclusion_reasons, input_chars, model_name, prompt_versions,
                       rules_version, source_mode, review_mode, status, error,
                       created_at, started_at, finished_at
                FROM analysis_runs WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                continue
            calls = con.execute(
                """
                SELECT tool, model, prompt_version, generation_config,
                       input_chars, output_chars, latency_ms, status,
                       error_class, prompt_text, output_text, created_at
                FROM model_calls WHERE run_id = ? ORDER BY created_at
                """,
                (run_id,),
            ).fetchall()
            rows[run_id] = {
                **{key: run[key] for key in run.keys()},
                "duration_seconds": _duration_seconds(
                    run["started_at"], run["finished_at"]
                ),
                "model_calls": [
                    {
                        **{key: call[key] for key in call.keys()},
                        "prompt_has_focus_id": bool(
                            call["prompt_text"]
                            and (FOCUS in call["prompt_text"])
                        ),
                        "prompt_has_same_time_id": bool(
                            call["prompt_text"]
                            and (SAME_TIME in call["prompt_text"])
                        ),
                    }
                    for call in calls
                ],
            }
    finally:
        con.close()
    return rows


def _write_markdown_report(
    out_dir: Path,
    case: dict[str, Any],
    runs: dict[str, Any],
    run_bodies: dict[str, dict[str, Any]],
    validation: dict[str, Any],
    exports: list[dict[str, Any]],
) -> None:
    lines = [
        "# t20 真实模型同病例三轮审阅验收",
        "",
        f"- 生成：{_now_tag()}",
        f"- 病例：{case.get('id')}",
        f"- 焦点：{FOCUS}",
        f"- 模板：demo_fever_sputum_three_rounds",
        f"- 模型：{runs.get('round1', {}).get('model_name', '')}",
        "",
    ]
    labels = {
        "round1": "初次审阅（replay）",
        "round2": "补录后复核（current）",
        "round3": "改稿后复核（current）",
    }
    for key, label in labels.items():
        body = run_bodies.get(key, {})
        run = runs.get(key, {})
        lines.append(f"## {label}")
        lines.append("")
        lines.append(f"- 运行：{body.get('run_id')}")
        lines.append(f"- 状态：{run.get('status')}")
        lines.append(f"- 来源：{body.get('source_mode')}")
        lines.append(f"- 病例版本：{run.get('case_version_id')}")
        lines.append(f"- 草稿版本：{run.get('draft_version_id')}")
        lines.append(f"- 耗时（秒）：{run.get('duration_seconds')}")
        for finding in body.get("findings", []):
            refs = "；".join(
                f"{ref.get('role')} {ref.get('message_id')}「{ref.get('snippet')}」"
                for ref in finding.get("evidence_refs", [])
            )
            lines.append(
                f"- 主张「{finding.get('draft_sentence_text')}」："
                f"{finding.get('contribution')} / {finding.get('patient_grounding')}；"
                f"引用：{refs or '（无）'}"
            )
        if body.get("diff"):
            lines.append("- 与上一运行的提示变化：")
            for item in body["diff"]:
                lines.append(
                    f"  - {item.get('change')}：{item.get('draft_sentence_text')}"
                    f"（{item.get('old_status') or '无'} → {item.get('new_status')}）"
                )
        lines.append("")
    lines.append("## 引用与范围校验")
    lines.append("")
    lines.append(f"- 事实/观点结构化来源：程序在请求中提供；真实模型负责判断。")
    lines.append(f"- 三轮证据校验通过：{validation.get('passes')}")
    lines.append("- prompt 中未出现焦点回答 ID 与同时刻回答 ID：")
    for key, run in runs.items():
        prompt_checks = [
            not call["prompt_has_focus_id"]
            and not call["prompt_has_same_time_id"]
            for call in run.get("model_calls", [])
        ]
        lines.append(f"  - {key}: {all(prompt_checks) if prompt_checks else '未记录'}")
    lines.append("")
    lines.append("## 导出")
    lines.append("")
    for export in exports:
        path = export.get("export_path")
        exists = Path(path).exists() if path else False
        lines.append(
            f"- {export.get('format')}：{export.get('file_name')}，"
            f"本地可读={exists}，内容长度={export.get('content_length')}"
        )
    lines.append("")
    lines.append("> 程序只校验引用可定位、角色与时间范围一致；医学结论仍需人工核验。")
    (out_dir / "acceptance_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _find_supplement_id(run_bodies: dict[str, Any]) -> str:
    messages = (
        run_bodies.get("_replay_scopes", {})
        .get("round2", {})
        .get("visible_messages", [])
    )
    for message in messages:
        if SUPPLEMENT_TEXT in str(message.get("text", "")):
            return str(message["id"])
    raise SystemExit("supplement message not found in round2 scope")


def _append_semantic_report(out_dir: Path, semantic: dict[str, Any]) -> None:
    report = out_dir / "acceptance_report.md"
    if not report.exists():
        return
    lines = ["", "## 语义校验（最终重跑）", ""]
    lines.append(f"- 全部通过：{semantic.get('all_pass')}")
    for check in semantic.get("checks", []):
        lines.append(
            f"- {check.get('name')}: "
            f"{'通过' if check.get('passes') else '未通过'} - "
            f"{check.get('detail')}"
        )
    with report.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="MediPeer t20 真实模型三轮验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--db-path", default=str(ROOT / "data" / "medipeer_t20_accept.db"))
    parser.add_argument(
        "--recheck-dir",
        default=None,
        help="已有运行目录相对路径；只重跑语义校验，不调用模型或 API",
    )
    args = parser.parse_args()

    if args.recheck_dir:
        out_dir = ROOT / args.recheck_dir
        if not out_dir.is_dir():
            print(f"recheck dir not found: {out_dir}", file=sys.stderr)
            return 2
        acceptance = json.loads(
            (out_dir / "acceptance.json").read_text(encoding="utf-8")
        )
        run_bodies = json.loads(
            (out_dir / "run_bodies.json").read_text(encoding="utf-8")
        )
        semantic = run_checks(
            out_dir,
            str(acceptance.get("case_id", "")),
            run_bodies,
            acceptance.get("evidence_validation") or {},
            supplement_message_id=_find_supplement_id(run_bodies),
        )
        _append_semantic_report(out_dir, semantic)
        print("semantic_checks:", json.dumps(semantic, ensure_ascii=False))
        return 0 if semantic.get("all_pass") else 4

    out_dir = ROOT / "data_app_chain" / "runs" / f"t20_real_model_{_now_tag()}_{uuid.uuid4().hex[:6]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(base_url=args.base_url, timeout=660.0) as client:
        status, login = _request(
            client,
            "POST",
            "/api/auth/login",
            out_dir,
            "login",
            json={"username": "demo", "password": "demo1234"},
        )
        if status != 200:
            print(f"login failed: {status}", file=sys.stderr)
            return 2
        headers = {"Authorization": f"Bearer {login['token']}"}

        status, case = _request(
            client,
            "POST",
            "/api/cases/demo/demo_fever_sputum_three_rounds",
            out_dir,
            "create_case",
            headers=headers,
        )
        if status != 200:
            print(f"create case failed: {status}", file=sys.stderr)
            return 2
        case_id = case["id"]

        run_bodies: dict[str, dict[str, Any]] = {}
        run_ids: list[str] = []
        validation: dict[str, Any] = {}
        replay_scopes: dict[str, dict[str, Any]] = {}

        def _review(tag: str, context: str) -> dict[str, Any]:
            status, body = _request(
                client,
                "POST",
                f"/api/cases/{case_id}/joint-review",
                out_dir,
                f"joint_{tag}",
                headers=headers,
                json={"focus_message_id": FOCUS, "review_context": context},
            )
            if status != 200:
                print(f"joint review failed ({tag}): {status}", file=sys.stderr)
                print(json.dumps(body, ensure_ascii=False, indent=2), file=sys.stderr)
                return body
            return body

        # 初次审阅
        replay1 = _request(
            client,
            "POST",
            f"/api/cases/{case_id}/replay",
            out_dir,
            "replay_round1",
            headers=headers,
            json={"focus_message_id": FOCUS, "review_context": "replay"},
        )[1]
        replay_scopes["round1"] = replay1
        _validate_scope(replay1, out_dir, "round1")
        body1 = _review("round1", "replay")
        if body1.get("status") == "failed" or "run_id" not in body1:
            json.dump({"flow": "round1_failed", "body": body1}, (out_dir / "flow_failed.json").open("w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print("round1 failed and was preserved; see flow_failed.json", file=sys.stderr)
            return 3
        run_bodies["round1"] = body1
        run_ids.append(body1["run_id"])
        validation["round1"] = _validate_findings(body1, replay1, out_dir, "round1")

        # 补录患者回复
        status, supplement = _request(
            client,
            "POST",
            f"/api/cases/{case_id}/supplements",
            out_dir,
            "supplement_round2",
            headers=headers,
            json={"content": SUPPLEMENT_TEXT},
        )
        if status != 200:
            print(f"supplement failed: {status}", file=sys.stderr)
            return 2
        message_id = supplement["message_id"]

        replay2 = _request(
            client,
            "POST",
            f"/api/cases/{case_id}/replay",
            out_dir,
            "replay_round2",
            headers=headers,
            json={"focus_message_id": FOCUS, "review_context": "current"},
        )[1]
        replay_scopes["round2"] = replay2
        prior_ok = list(replay2.get("prior_answer_message_ids") or []) == PRIOR_DOCTORS
        supplement_ok = message_id in (replay2.get("patient_message_ids") or [])
        _write_json(
            out_dir / "scope_round2_extra.json",
            {"prior_doctor_exact": prior_ok, "supplement_visible": supplement_ok},
        )
        if not (prior_ok and supplement_ok):
            raise SystemExit("round2 scope did not include supplement with exact prior doctors")

        body2 = _review("round2", "current")
        if "run_id" not in body2:
            print("round2 failed and was preserved; see flow_failed.json", file=sys.stderr)
            json.dump({"flow": "round2_failed", "body": body2}, (out_dir / "flow_failed.json").open("w", encoding="utf-8"), ensure_ascii=False, indent=2)
            return 3
        run_bodies["round2"] = body2
        run_ids.append(body2["run_id"])
        validation["round2"] = _validate_findings(body2, replay2, out_dir, "round2")

        # 修改草稿
        status, _draft_view = _request(
            client,
            "PUT",
            f"/api/cases/{case_id}/draft",
            out_dir,
            "update_draft_round3",
            headers=headers,
            json={"draft_text": ROUND3_DRAFT, "reason": "三轮验收改稿"},
        )
        if status != 200:
            print(f"draft update failed: {status}", file=sys.stderr)
            return 2

        replay3 = _request(
            client,
            "POST",
            f"/api/cases/{case_id}/replay",
            out_dir,
            "replay_round3",
            headers=headers,
            json={"focus_message_id": FOCUS, "review_context": "current"},
        )[1]
        replay_scopes["round3"] = replay3
        run_bodies["_replay_scopes"] = replay_scopes
        body3 = _review("round3", "current")
        if "run_id" not in body3:
            print("round3 failed and was preserved; see flow_failed.json", file=sys.stderr)
            json.dump({"flow": "round3_failed", "body": body3}, (out_dir / "flow_failed.json").open("w", encoding="utf-8"), ensure_ascii=False, indent=2)
            return 3
        run_bodies["round3"] = body3
        run_ids.append(body3["run_id"])
        validation["round3"] = _validate_findings(body3, replay3, out_dir, "round3")

        # 导出本次真实运行
        exports: list[dict[str, Any]] = []
        for fmt in ("json", "markdown"):
            export = _request(
                client,
                "GET",
                f"/api/cases/{case_id}/exports",
                out_dir,
                f"export_round3_{fmt}",
                headers=headers,
                params={"run_id": body3["run_id"], "format": fmt},
            )[1]
            file_name = export.get("file_name", "")
            export_path = ROOT / "data" / "exports" / file_name
            (out_dir / file_name).write_text(
                export.get("content", ""), encoding="utf-8"
            )
            exports.append(
                {
                    "format": export.get("format"),
                    "file_name": file_name,
                    "content_length": len(export.get("content", "")),
                    "export_path": str(export_path),
                    "export_exists": export_path.exists(),
                }
            )

    runs = _load_run_records(Path(args.db_path), run_ids)
    _write_json(out_dir / "run_records.json", runs)
    _write_json(out_dir / "run_bodies.json", run_bodies)
    _write_json(
        out_dir / "acceptance.json",
        {
            "task": "t20_real_model_three_rounds",
            "generated_at": _now_tag(),
            "case_id": case_id,
            "template_key": "demo_fever_sputum_three_rounds",
            "focus_message_id": FOCUS,
            "round1_draft": ROUND1_DRAFT,
            "supplement": SUPPLEMENT_TEXT,
            "round3_draft": ROUND3_DRAFT,
            "run_ids": run_ids,
            "run_records_path": "run_records.json",
            "evidence_validation": validation,
            "exports": exports,
        },
    )
    _write_markdown_report(out_dir, case, runs, run_bodies, validation, exports)

    print(f"output_dir: {out_dir}")
    print(f"case_id: {case_id}")
    for key, body in run_bodies.items():
        print(f"{key}: run_id={body.get('run_id')} source={body.get('source_mode')}")
    print("evidence_validation:", json.dumps(validation, ensure_ascii=False))
    semantic = run_checks(
        out_dir,
        case_id,
        run_bodies,
        validation,
        supplement_message_id=message_id,
    )
    print("semantic_checks:", json.dumps(semantic, ensure_ascii=False))
    return 0 if semantic.get("all_pass") else 4


if __name__ == "__main__":
    raise SystemExit(main())
