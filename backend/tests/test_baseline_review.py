from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.db.models import (
    AnalysisRun,
    Claim,
    ClaimRelation,
    Finding,
    ModelCall,
    PatientFact,
)
from backend.app.db.session import SessionLocal
from backend.app.services import baseline_review_service

from .helpers import login_headers


def _create_case(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _run_baseline(
    client: TestClient,
    case_id: str,
    headers: dict[str, str],
) -> dict:
    response = client.post(
        f"/api/cases/{case_id}/baseline-review",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_baseline_review_returns_parallel_units_and_records_call(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_baseline(client, case["id"], headers)

    assert body["run_id"]
    assert body["review_mode"] == "direct_baseline"
    assert body["source_mode"] == "stub_baseline"
    assert body["model_name"] == "medipeer-stub"
    assert body["generation_config"]["provider"] == "stub"
    assert body["prompt_versions"] == {"direct_baseline": "v1"}
    assert len(body["findings"]) == len(body["draft_sentences"]) == 7
    for finding in body["findings"]:
        assert finding["related_fact_ids"] == []
        assert finding["related_claim_ids"] == []
        assert finding["signature"]
        for ref in finding["evidence_refs"]:
            assert ref["message_id"] in {
                "wy-q-1001",
                "wy-a-2001",
                "wy-a-2002",
            }

    with SessionLocal() as db:
        run = db.get(AnalysisRun, body["run_id"])
        assert run is not None
        assert run.stage == "baseline_direct_review"
        assert run.review_mode == "direct_baseline"
        assert run.status == "succeeded"
        call = db.query(ModelCall).filter(ModelCall.run_id == run.id).one()
        assert call.tool == "baseline_direct_review"
        assert call.prompt_version == "v1"
        assert call.status == "ok"
        assert call.latency_ms >= 0
        assert call.prompt_text
        assert "医生您好，我最近三天一直咳嗽" in call.prompt_text
        assert "多喝水，注意休息" in call.prompt_text
        assert call.output_text


def test_baseline_review_scope_omits_later_and_focus_messages(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_baseline(client, case["id"], headers)

    with SessionLocal() as db:
        run = db.get(AnalysisRun, body["run_id"])
        assert run is not None
        assert set(run.scope_message_ids) == {
            "wy-q-1001",
            "wy-a-2001",
            "wy-a-2002",
        }
        call = db.query(ModelCall).filter(ModelCall.run_id == run.id).one()
        for forbidden in (
            "如果没有气促、胸痛，可以先在家观察",
            "咳嗽三天伴白痰，注意休息即可",
            "昨晚八点量过体温",
            "患者已补充发热和退热情况",
        ):
            assert forbidden not in call.prompt_text


def test_baseline_review_does_not_create_structured_rows(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_baseline(client, case["id"], headers)

    with SessionLocal() as db:
        run_id = body["run_id"]
        assert db.query(PatientFact).filter(PatientFact.run_id == run_id).count() == 0
        assert db.query(Claim).filter(Claim.run_id == run_id).count() == 0
        assert (
            db.query(ClaimRelation).filter(ClaimRelation.run_id == run_id).count()
            == 0
        )


def test_baseline_review_model_failure_is_persisted(
    client: TestClient,
    monkeypatch,
):
    def _fake_call(*_args, **_kwargs):
        return {
            "status": "failed",
            "content": None,
            "output": None,
            "error_class": "mock_call_error",
            "error": "mock failure",
            "latency_ms": 7,
            "request_prompt": "REQUEST-BODY",
        }

    monkeypatch.setattr(
        baseline_review_service.llm_client,
        "complete_baseline",
        _fake_call,
    )
    headers = login_headers(client)
    case = _create_case(client, headers)
    response = client.post(
        f"/api/cases/{case['id']}/baseline-review",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 502
    run_id = response.json()["detail"]["run_id"]
    assert response.json()["detail"]["error_class"] == "mock_call_error"

    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "mock failure"
        call = db.query(ModelCall).filter(ModelCall.run_id == run_id).one()
        assert call.status == "failed"
        assert call.error_class == "mock_call_error"
        assert call.prompt_text == "REQUEST-BODY"
        assert call.output_text is None


def test_baseline_review_invalid_output_is_persisted_as_failed(
    client: TestClient,
    monkeypatch,
):
    def _fake_call(*_args, **_kwargs):
        return {
            "status": "ok",
            "content": {
                "structured_modules_used": False,
                "findings": [
                    {
                        "draft_sentence_id": "ds-0",
                        "draft_sentence_text": "患者目前咳嗽伴发热，建议继续观察体温。",
                        "contribution": "not-a-valid-kind",
                        "patient_grounding": "patient_stated",
                        "suggested_action": "keep_and_confirm",
                        "explanation": "bad",
                        "evidence_refs": [],
                    }
                ],
            },
            "output": "{}",
            "error_class": None,
            "error": None,
            "latency_ms": 3,
            "request_prompt": "REQUEST-BODY",
        }

    monkeypatch.setattr(
        baseline_review_service.llm_client,
        "complete_baseline",
        _fake_call,
    )
    headers = login_headers(client)
    case = _create_case(client, headers)
    response = client.post(
        f"/api/cases/{case['id']}/baseline-review",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 502
    run_id = response.json()["detail"]["run_id"]

    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        assert run is not None
        assert run.status == "failed"
        call = db.query(ModelCall).filter(ModelCall.run_id == run_id).one()
        assert call.status == "failed"
        assert call.error_class == "output_validation_error"


def test_real_baseline_uses_resolved_model_and_program_sentence_text(
    client: TestClient,
    monkeypatch,
):
    settings = SimpleNamespace(
        llm_provider="stub",
        llm_model="medipeer-stub",
        resolved_llm_provider="openai_compatible",
        resolved_llm_model="qwen-test-real",
        resolved_llm_base_url="http://fake.invalid/v1",
        resolved_llm_api_key="test-only",
        max_input_chars=200_000,
        rules_version="test.1",
        prompt_version_direct_baseline="v1",
        llm_timeout_seconds=30,
        llm_max_retries=0,
        llm_temperature=0.2,
        llm_max_tokens=2000,
    )

    def fake_complete(scope, draft_text, settings_, run_id):
        content = baseline_review_service.llm_client.stub_baseline_review(
            scope, draft_text, run_id
        )
        for finding in content["findings"]:
            finding.pop("draft_sentence_text", None)
        return {
            "status": "ok",
            "content": content,
            "output": json.dumps(content, ensure_ascii=False),
            "error_class": None,
            "error": None,
            "latency_ms": 17,
            "request_prompt": "test prompt without credentials",
        }

    monkeypatch.setattr(baseline_review_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        baseline_review_service.llm_client,
        "complete_baseline",
        fake_complete,
    )
    headers = login_headers(client)
    case = _create_case(client, headers)
    body = _run_baseline(client, case["id"], headers)

    assert body["source_mode"] == "llm_direct"
    assert body["model_name"] == "qwen-test-real"
    assert len(body["findings"]) == len(body["draft_sentences"])
    assert all(
        finding["draft_sentence_text"] == sentence["text"]
        for finding, sentence in zip(
            body["findings"], body["draft_sentences"]
        )
    )

    with SessionLocal() as db:
        run = db.get(AnalysisRun, body["run_id"])
        assert run is not None
        assert run.source_mode == "llm_direct"
        assert run.model_name == "qwen-test-real"
        call = db.query(ModelCall).filter(ModelCall.run_id == run.id).one()
        assert call.status == "ok"
        assert call.model == "qwen-test-real"
        findings = (
            db.query(Finding)
            .filter(Finding.run_id == run.id)
            .order_by(Finding.draft_sentence_id)
            .all()
        )
        assert findings
        assert all(
            finding.draft_sentence_text
            for finding in findings
        )


def test_baseline_review_respects_user_isolation(client: TestClient):
    headers = login_headers(client, "demo", "demo1234")
    other_headers = login_headers(client, "judge", "judge1234")
    case = _create_case(client, headers)
    response = client.post(
        f"/api/cases/{case['id']}/baseline-review",
        headers=other_headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 404


def test_baseline_and_joint_review_share_input_binding(
    client: TestClient,
):
    headers = login_headers(client)
    case = _create_case(client, headers)
    joint = client.post(
        f"/api/cases/{case['id']}/joint-review",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    ).json()
    baseline = _run_baseline(client, case["id"], headers)

    with SessionLocal() as db:
        joint_run = db.get(AnalysisRun, joint["run_id"])
        baseline_run = db.get(AnalysisRun, baseline["run_id"])
        assert joint_run is not None and baseline_run is not None
        assert joint_run.case_version_id == baseline_run.case_version_id
        assert joint_run.draft_version_id == baseline_run.draft_version_id
        assert joint_run.focus_message_id == baseline_run.focus_message_id
        assert joint_run.model_name == baseline_run.model_name
        assert set(joint_run.scope_message_ids) == set(
            baseline_run.scope_message_ids
        )
