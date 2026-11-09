from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models import AnalysisRun, EvaluationRun
from backend.app.db.session import SessionLocal
from backend.app.services import baseline_review_service
from backend.app.services.evaluation_service import (
    PairEvaluationError,
    run_pair_evaluation,
)

from .helpers import login_headers


def _demo_user(client: TestClient):
    with SessionLocal() as db:
        from backend.app.db.models import User
        from sqlalchemy import select

        user = db.scalar(select(User).where(User.username == "demo"))
        assert user is not None
        return user


def _create_case(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def _walk_for_forbidden(value, forbidden: set[str]) -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if any(word in key.lower() for word in forbidden):
                hits.append(key)
            hits.extend(_walk_for_forbidden(child, forbidden))
    elif isinstance(value, list):
        for child in value:
            hits.extend(_walk_for_forbidden(child, forbidden))
    return hits


def test_pair_evaluation_multi_doctor_generates_three_artifacts(
    client: TestClient,
    tmp_path: Path,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    output_root = tmp_path / "eval_multi"

    with SessionLocal() as db:
        user = _demo_user(client)
        eval_run, output_dir = run_pair_evaluation(
            db,
            user=user,
            template_key="demo_respiratory_multi_doctor",
            name="test multi",
            case_id=case["id"],
            output_root=output_root,
        )

    assert output_dir == output_root
    assert (output_dir / "evaluation_detail.csv").exists()
    assert (output_dir / "evaluation_summary.json").exists()
    assert (output_dir / "failure_cases.md").exists()

    summary = json.loads(
        (output_dir / "evaluation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["task"] == "t17_pair_evaluation"
    assert summary["provider"] == "stub"
    assert summary["template_key"] == "demo_respiratory_multi_doctor"
    assert summary["focus_message_id"] == "wy-a-2003-focus"
    assert summary["binding"]["match"] is True
    assert summary["binding_match"] is True
    assert summary["failure_count"] == 0
    assert summary["totals"]["joint_succeeded"] == 1
    assert summary["totals"]["baseline_succeeded"] == 1
    assert "stub" in summary["not_applicable"][0] or summary["not_applicable"]
    assert not _walk_for_forbidden(summary, {"accuracy", "precision", "recall", "f1"})

    joint = summary["run_pairs"]["joint_review"]
    baseline = summary["run_pairs"]["baseline_review"]
    assert joint["run_id"] != baseline["run_id"]
    assert joint["mode"] == "joint_review"
    assert baseline["mode"] == "direct_baseline"
    assert joint["status"] == "succeeded"
    assert baseline["status"] == "succeeded"
    assert joint["input_chars"] == baseline["input_chars"]
    assert joint["scope_message_ids"] == baseline["scope_message_ids"]
    assert joint["findings"]["total"] >= 1
    assert baseline["findings"]["total"] >= 1

    with SessionLocal() as db:
        persist_run = db.get(EvaluationRun, eval_run.id)
        assert persist_run is not None
        assert persist_run.status == "succeeded"
        assert persist_run.case_ids == [case["id"]]

    rows = _read_csv_rows(output_dir / "evaluation_detail.csv")
    modes = {row["review_mode"] for row in rows}
    assert modes == {"joint_review", "direct_baseline"}
    assert len(rows) == joint["findings"]["total"] + baseline["findings"]["total"]
    for row in rows:
        if row["status"] == "succeeded":
            assert row["draft_sentence_id"]
            assert row["run_id"]


def test_pair_evaluation_single_doctor_template(
    client: TestClient,
    tmp_path: Path,
):
    with SessionLocal() as db:
        user = _demo_user(client)
        eval_run, output_dir = run_pair_evaluation(
            db,
            user=user,
            template_key="demo_respiratory_single_doctor",
            name="test single",
            output_root=tmp_path / "eval_single",
        )

    summary = json.loads(
        (output_dir / "evaluation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["focus_message_id"] == "sa-a-2001"
    assert "sq-q-2001" in summary["run_pairs"]["joint_review"]["scope_message_ids"]
    assert summary["run_pairs"]["baseline_review"]["status"] == "succeeded"
    assert eval_run.status == "succeeded"


def test_pair_evaluation_captures_baseline_failure(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def _fake_failed_call(*_args, **_kwargs):
        return {
            "status": "failed",
            "content": None,
            "output": None,
            "error_class": "mock_call_error",
            "error": "mock baseline failure",
            "latency_ms": 5,
            "request_prompt": "REQUEST-BODY",
        }

    monkeypatch.setattr(
        baseline_review_service.llm_client,
        "complete_baseline",
        _fake_failed_call,
    )
    with SessionLocal() as db:
        user = _demo_user(client)
        eval_run, output_dir = run_pair_evaluation(
            db,
            user=user,
            template_key="demo_respiratory_multi_doctor",
            name="failure injection",
            output_root=tmp_path / "eval_failure",
        )

    summary = json.loads(
        (output_dir / "evaluation_summary.json").read_text(encoding="utf-8")
    )
    assert eval_run.status == "succeeded"
    assert summary["failure_count"] >= 1
    assert summary["run_pairs"]["joint_review"]["status"] == "succeeded"
    assert summary["run_pairs"]["baseline_review"]["status"] == "failed"
    assert summary["run_pairs"]["baseline_review"]["error_class"] == "mock_call_error"
    failure_md = (output_dir / "failure_cases.md").read_text(encoding="utf-8")
    assert "mock baseline failure" in failure_md
    assert "mock_call_error" in failure_md

    with SessionLocal() as db:
        failed_run = db.scalar(
            select(AnalysisRun).where(
                AnalysisRun.review_mode == "direct_baseline",
                AnalysisRun.status == "failed",
            )
        )
        assert failed_run is not None


def test_pair_evaluation_rejects_invalid_focus(
    client: TestClient,
    tmp_path: Path,
):
    with SessionLocal() as db:
        user = _demo_user(client)
        with pytest.raises(PairEvaluationError):
            run_pair_evaluation(
                db,
                user=user,
                template_key="demo_respiratory_multi_doctor",
                focus_message_id="wy-a-2001",
                output_root=tmp_path / "eval_invalid",
            )


def test_pair_evaluation_isolates_cases_by_owner(
    client: TestClient,
    tmp_path: Path,
):
    headers = login_headers(client, "demo", "demo1234")
    case = _create_case(client, headers)
    with SessionLocal() as db:
        from backend.app.db.models import User
        from sqlalchemy import select

        judge = db.scalar(select(User).where(User.username == "judge"))
        assert judge is not None
        with pytest.raises(PermissionError):
            run_pair_evaluation(
                db,
                user=judge,
                template_key="demo_respiratory_multi_doctor",
                case_id=case["id"],
                output_root=tmp_path / "eval_isolated",
            )
