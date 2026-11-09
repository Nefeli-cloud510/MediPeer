from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.db.models import (
    AnalysisRun,
    CaseVersion,
    DraftVersion,
    Finding,
    ReviewAction,
    Supplement,
)
from backend.app.db.session import SessionLocal

from .helpers import login_headers


def _create_case(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _run_joint_review(
    client: TestClient,
    case_id: str,
    headers: dict[str, str],
) -> dict:
    response = client.post(
        f"/api/cases/{case_id}/joint-review",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _finding_by_text(body: dict, fragment: str) -> dict:
    return next(
        finding
        for finding in body["findings"]
        if fragment in finding["draft_sentence_text"]
    )


def test_confirm_and_reject_persist_without_changing_versions(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_joint_review(client, case["id"], headers)
    finding_id = body["findings"][0]["id"]

    confirmed = client.post(
        f"/api/cases/{case['id']}/review-actions",
        headers=headers,
        json={
            "finding_id": finding_id,
            "action": "confirmed",
            "note": "与原文一致",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["action"] == "confirmed"
    assert confirmed.json()["note"] == "与原文一致"

    rejected = client.post(
        f"/api/cases/{case['id']}/review-actions",
        headers=headers,
        json={"finding_id": finding_id, "action": "rejected"},
    )
    assert rejected.status_code == 200, rejected.text

    with SessionLocal() as db:
        finding = db.get(Finding, finding_id)
        assert finding.review_status == "rejected"
        actions = (
            db.query(ReviewAction)
            .filter(ReviewAction.finding_id == finding_id)
            .order_by(ReviewAction.created_at)
            .all()
        )
        assert [action.action for action in actions] == ["confirmed", "rejected"]

    detail = client.get(f"/api/cases/{case['id']}", headers=headers).json()
    assert detail["version_no"] == 1
    draft = client.get(f"/api/cases/{case['id']}/draft", headers=headers).json()
    assert draft["version_no"] == 1


def test_supplement_creates_new_case_version_and_stays_out_of_old_scope(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    first = _run_joint_review(client, case["id"], headers)

    response = client.post(
        f"/api/cases/{case['id']}/supplements",
        headers=headers,
        json={
            "content": "患者今早已量过体温，目前不发烧，仍有咳嗽。",
            "finding_id": first["findings"][0]["id"],
        },
    )
    assert response.status_code == 200, response.text
    supplement = response.json()
    assert supplement["version_no"] == 2
    assert supplement["role"] == "patient"

    detail = client.get(f"/api/cases/{case['id']}", headers=headers).json()
    assert detail["version_no"] == 2
    assert len(detail["messages"]) == 8
    supplemented = [message for message in detail["messages"] if message["id"] == supplement["message_id"]]
    assert len(supplemented) == 1
    assert supplemented[0]["text"] == "患者今早已量过体温，目前不发烧，仍有咳嗽。"

    with SessionLocal() as db:
        versions = (
            db.query(CaseVersion)
            .filter(CaseVersion.case_id == case["id"])
            .order_by(CaseVersion.version_no)
            .all()
        )
        assert [version.version_no for version in versions] == [1, 2]
        assert len(versions[0].messages_json) == 7
        assert len(versions[1].messages_json) == 8
        supplement_row = db.query(Supplement).filter(Supplement.id == supplement["id"]).one()
        assert supplement_row.finding_id == first["findings"][0]["id"]

    replay = client.post(
        f"/api/cases/{case['id']}/replay",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    ).json()
    assert replay["prior_answer_message_ids"] == ["wy-a-2001", "wy-a-2002"]
    assert supplement["message_id"] in replay["excluded_message_ids"]
    assert "晚于焦点" in replay["exclusion_reasons"][supplement["message_id"]]

    second = _run_joint_review(client, case["id"], headers)
    assert second["diff_reason"] == "case_version_changed"
    collected = {
        ref["message_id"]
        for finding in second["findings"]
        for ref in finding["evidence_refs"]
    }
    assert supplement["message_id"] not in collected
    assert all(
        supplement["message_id"] not in finding["checked_message_ids"]
        for finding in second["findings"]
    )


def test_adopted_question_remains_pending_until_patient_answer(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    first = _run_joint_review(client, case["id"], headers)
    target = _finding_by_text(first, "发热 38.5℃")

    adopted = client.post(
        f"/api/cases/{case['id']}/review-actions",
        headers=headers,
        json={
            "finding_id": target["id"],
            "action": "supplement_adopted",
            "suggested_text": "先补问：患者是否已量体温并记录具体数值？",
        },
    )
    assert adopted.status_code == 200, adopted.text

    with SessionLocal() as db:
        finding = db.get(Finding, target["id"])
        assert finding.review_status == "question_adopted"

    rerun = _run_joint_review(client, case["id"], headers)
    carried = next(
        finding
        for finding in rerun["findings"]
        if finding["signature"] == target["signature"]
    )
    assert carried["review_status"] == "question_adopted"
    assert rerun["diff"] == []

    supplement = client.post(
        f"/api/cases/{case['id']}/supplements",
        headers=headers,
        json={
            "content": "患者回复：昨晚测过 38.5℃，今天早上没再烧。",
            "finding_id": carried["id"],
        },
    )
    assert supplement.status_code == 200, supplement.text

    third = _run_joint_review(client, case["id"], headers)
    unchanged = next(
        item
        for item in third["diff"]
        if item["signature"] == target["signature"]
    )
    assert unchanged["change"] == "unchanged"
    assert unchanged["old_status"] == "question_adopted"
    assert unchanged["new_status"] == "question_adopted"


def test_draft_update_creates_new_version_and_diff_groups_changes(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    first = _run_joint_review(client, case["id"], headers)
    asthma = _finding_by_text(first, "哮喘")
    target = _finding_by_text(first, "发热 38.5℃")

    rejected = client.post(
        f"/api/cases/{case['id']}/review-actions",
        headers=headers,
        json={"finding_id": target["id"], "action": "rejected"},
    )
    assert rejected.status_code == 200, rejected.text

    updated_draft = (
        "患者目前咳嗽伴发热，建议继续观察体温。"
        "患者昨日发热 38.5℃，现已退热；"
        "建议多喝水，如持续发热可考虑复查血常规。"
    )
    update = client.put(
        f"/api/cases/{case['id']}/draft",
        headers=headers,
        json={"draft_text": updated_draft, "reason": "删除缺少来源的哮喘表述"},
    )
    assert update.status_code == 200, update.text
    assert update.json()["version_no"] == 2
    assert update.json()["current_text"] == updated_draft

    with SessionLocal() as db:
        versions = (
            db.query(DraftVersion).order_by(DraftVersion.version_no).all()
        )
        assert [version.version_no for version in versions] == [1, 2]

    second = _run_joint_review(client, case["id"], headers)
    assert second["diff_reason"] == "draft_version_changed"
    assert second["previous_run_id"] == first["run_id"]
    resolved_asthma = next(
        item
        for item in second["diff"]
        if item["change"] == "resolved" and "哮喘" in item["draft_sentence_text"]
    )
    assert resolved_asthma["signature"] == asthma["signature"]
    carried_rejected = next(
        item
        for item in second["diff"]
        if item["signature"] == target["signature"]
    )
    assert carried_rejected["change"] == "unchanged"
    assert carried_rejected["new_status"] == "rejected"

    with SessionLocal() as db:
        old_run = db.get(AnalysisRun, first["run_id"])
        assert old_run.status == "succeeded"
        assert (
            db.query(Finding)
            .filter(Finding.run_id == old_run.id)
            .count()
            == len(first["findings"])
        )


def test_review_actions_respect_user_isolation(client: TestClient):
    headers = login_headers(client)
    other_headers = login_headers(client, "judge", "judge1234")
    case = _create_case(client, headers)
    first = _run_joint_review(client, case["id"], headers)
    finding_id = first["findings"][0]["id"]

    action = client.post(
        f"/api/cases/{case['id']}/review-actions",
        headers=other_headers,
        json={"finding_id": finding_id, "action": "confirmed"},
    )
    assert action.status_code == 404

    supplement = client.post(
        f"/api/cases/{case['id']}/supplements",
        headers=other_headers,
        json={"content": "越权补录不应写入"},
    )
    assert supplement.status_code == 404

    update = client.put(
        f"/api/cases/{case['id']}/draft",
        headers=other_headers,
        json={"draft_text": "越权修改不应写入"},
    )
    assert update.status_code == 404


def test_supplement_and_draft_reject_too_long_input(client: TestClient):
    headers = login_headers(client)
    case = _create_case(client, headers)
    too_long = "长" * 20001

    supplement = client.post(
        f"/api/cases/{case['id']}/supplements",
        headers=headers,
        json={"content": too_long},
    )
    assert supplement.status_code == 422

    update = client.put(
        f"/api/cases/{case['id']}/draft",
        headers=headers,
        json={"draft_text": too_long},
    )
    assert update.status_code == 422


def test_unknown_finding_returns_404(client: TestClient):
    headers = login_headers(client)
    case = _create_case(client, headers)
    response = client.post(
        f"/api/cases/{case['id']}/review-actions",
        headers=headers,
        json={"finding_id": "not-exists", "action": "confirmed"},
    )
    assert response.status_code == 404
