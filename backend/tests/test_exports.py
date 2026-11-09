from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app.db.models import ExportRecord
from backend.app.db.session import SessionLocal
from backend.app.services import export_service

from .helpers import login_headers


def _create_case(
    client: TestClient,
    headers: dict[str, str],
    template: str = "demo_respiratory_multi_doctor",
) -> dict:
    response = client.post(f"/api/cases/demo/{template}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _run_joint_review(
    client: TestClient,
    case_id: str,
    headers: dict[str, str],
    focus_message_id: str = "wy-a-2003-focus",
) -> dict:
    response = client.post(
        f"/api/cases/{case_id}/joint-review",
        headers=headers,
        json={"focus_message_id": focus_message_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _get_export(
    client: TestClient,
    case_id: str,
    run_id: str,
    headers: dict[str, str],
    fmt: str = "json",
) -> dict:
    response = client.get(
        f"/api/cases/{case_id}/exports",
        headers=headers,
        params={"run_id": run_id, "format": fmt},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _patch_export_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        export_service,
        "get_settings",
        lambda: SimpleNamespace(project_root=tmp_path),
    )


def test_export_json_contains_run_versions_and_validated_citations(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_export_root(monkeypatch, tmp_path)
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_joint_review(client, case["id"], headers)

    export = _get_export(client, case["id"], body["run_id"], headers)
    assert export["export_id"]
    assert export["case_id"] == case["id"]
    assert export["run_id"] == body["run_id"]
    assert export["format"] == "json"
    assert export["file_name"].endswith(".json")

    payload = json.loads(export["content"])
    assert payload["export_kind"] == "medipeer_review_record"
    assert payload["run"]["run_id"] == body["run_id"]
    assert payload["run"]["case_version_id"]
    assert payload["run"]["model_name"] == "medipeer-stub"
    assert payload["case"]["id"] == case["id"]
    assert payload["draft"]["current_text"]
    assert payload["visible_messages"]
    assert payload["findings"]
    assert payload["evidence_validation"]["checked"] is True
    assert payload["evidence_validation"]["passes"] is True
    assert not payload["evidence_validation"]["errors"]

    file_path = tmp_path / "data" / "exports" / export["file_name"]
    assert file_path.exists()
    with SessionLocal() as db:
        record = db.get(ExportRecord, export["export_id"])
        assert record is not None
        assert record.case_id == case["id"]
        assert record.run_id == body["run_id"]


def test_export_markdown_human_readable(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_export_root(monkeypatch, tmp_path)
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_joint_review(client, case["id"], headers)

    export = _get_export(
        client,
        case["id"],
        body["run_id"],
        headers,
        fmt="markdown",
    )
    assert export["format"] == "markdown"
    assert export["file_name"].endswith(".md")
    assert "# MediPeer 审阅记录" in export["content"]
    assert "## 逐主张审阅" in export["content"]
    assert "## 引用校验" in export["content"]
    assert payload_review_status_marker(export["content"])


def payload_review_status_marker(content: str) -> bool:
    return "处理状态" in content


def test_export_isolates_case_and_run_by_owner(
    client: TestClient,
):
    headers = login_headers(client, "demo", "demo1234")
    other_headers = login_headers(client, "judge", "judge1234")
    case = _create_case(client, headers)
    body = _run_joint_review(client, case["id"], headers)

    response = client.get(
        f"/api/cases/{case['id']}/exports",
        headers=other_headers,
        params={"run_id": body["run_id"]},
    )
    assert response.status_code == 404


def test_export_rejects_unknown_run_and_bad_format(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_joint_review(client, case["id"], headers)

    missing = client.get(
        f"/api/cases/{case['id']}/exports",
        headers=headers,
        params={"run_id": "does-not-exist"},
    )
    assert missing.status_code == 404

    bad_format = client.get(
        f"/api/cases/{case['id']}/exports",
        headers=headers,
        params={"run_id": body["run_id"], "format": "pdf"},
    )
    assert bad_format.status_code == 400
