from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.db.models import AnalysisRun, Finding
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


def test_joint_review_api_runs_and_persists(client: TestClient):
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_joint_review(client, case["id"], headers)

    assert body["run_id"]
    assert body["case_id"] == case["id"]
    assert body["focus_message_id"] == "wy-a-2003-focus"
    assert body["order_quality"] == "ok"
    assert body["source_mode"] == "stub_rules"
    assert body["draft_text"]
    assert len(body["draft_sentences"]) == 7
    assert len(body["findings"]) == len(body["draft_sentences"])
    assert all(finding["signature"] for finding in body["findings"])
    assert any(
        finding["patient_grounding"] == "detail_unverified"
        for finding in body["findings"]
    )
    assert any(
        finding["contribution"] in ("condition_only", "mixed_prior_views")
        for finding in body["findings"]
    )

    with SessionLocal() as db:
        run = db.get(AnalysisRun, body["run_id"])
        assert run is not None
        assert run.stage == "joint_review"
        assert run.status == "succeeded"
        persisted = (
            db.query(Finding).filter(Finding.run_id == run.id).count()
        )
        assert persisted == len(body["findings"])


def test_joint_review_evidence_stays_within_strict_scope(client: TestClient):
    headers = login_headers(client)
    case = _create_case(client, headers)
    detail = client.get(f"/api/cases/{case['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    message_texts = {
        message["id"]: message["text"] for message in detail.json()["messages"]
    }
    body = _run_joint_review(client, case["id"], headers)

    forbidden = {
        "wy-a-2003-focus",
        "wy-a-2004-same-time",
        "wy-p-1002",
        "wy-a-2005-future",
    }
    allowed = {"wy-q-1001", "wy-a-2001", "wy-a-2002"}
    evidence_ids = {
        ref["message_id"]
        for finding in body["findings"]
        for ref in finding["evidence_refs"]
    }
    assert evidence_ids <= allowed
    assert not (evidence_ids & forbidden)

    for finding in body["findings"]:
        assert set(finding["checked_message_ids"]) == allowed
        assert not (set(finding["checked_message_ids"]) & forbidden)
        for ref in finding["evidence_refs"]:
            assert ref["message_id"] in message_texts
            assert ref["snippet"] in message_texts[ref["message_id"]]


def test_joint_review_flags_unverified_and_unmentioned_details(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_joint_review(client, case["id"], headers)

    fever = next(
        finding
        for finding in body["findings"]
        if "38.5" in finding["draft_sentence_text"]
    )
    assert fever["patient_grounding"] == "detail_unverified"
    assert fever["suggested_action"] == "revise_fact_statement"
    assert any(
        ref["message_id"] in ("wy-q-1001", "wy-a-2001", "wy-a-2002")
        for ref in fever["evidence_refs"]
    )

    asthma = next(
        finding
        for finding in body["findings"]
        if "哮喘" in finding["draft_sentence_text"]
    )
    assert asthma["patient_grounding"] == "unmentioned"
    assert asthma["suggested_action"] == "revise_fact_statement"
    assert asthma["evidence_refs"] == []


def test_joint_review_condition_sentence_keeps_condition_context(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_joint_review(client, case["id"], headers)

    conditional = next(
        finding
        for finding in body["findings"]
        if "复查血常规" in finding["draft_sentence_text"]
    )
    assert conditional["contribution"] in {
        "condition_only",
        "mixed_prior_views",
    }
    assert conditional["suggested_action"] in {
        "clarify_condition",
        "view_both_sources",
    }
    assert any(
        word in conditional["explanation"] for word in ("适用条件", "条件", "分歧")
    )


def test_joint_review_signature_stable_across_runs(client: TestClient):
    headers = login_headers(client)
    case = _create_case(client, headers)
    first = _run_joint_review(client, case["id"], headers)
    second = _run_joint_review(client, case["id"], headers)

    assert second["run_id"] != first["run_id"]
    signatures_first = sorted(
        finding["signature"] for finding in first["findings"]
    )
    signatures_second = sorted(
        finding["signature"] for finding in second["findings"]
    )
    assert signatures_first == signatures_second


def test_joint_review_respects_user_isolation(client: TestClient):
    headers = login_headers(client, "demo", "demo1234")
    other_headers = login_headers(client, "judge", "judge1234")
    case = _create_case(client, headers)
    response = client.post(
        f"/api/cases/{case['id']}/joint-review",
        headers=other_headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 404
