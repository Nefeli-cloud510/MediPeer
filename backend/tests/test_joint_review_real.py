from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.db.models import AnalysisRun, Base, Case, Finding, ModelCall
from backend.app.db.session import SessionLocal, configure_database, get_engine
from backend.app.main import app
from backend.app.services import (
    joint_review_service,
    llm_client,
    patient_facts_service,
)
from backend.app.services.auth_service import ensure_seed_users
from backend.app.services.sentence_utils import split_draft_sentences

from .helpers import login_headers


def _fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        resolved_llm_provider="openai_compatible",
        resolved_llm_model="qwen-test-real",
        resolved_llm_base_url="http://fake.invalid/v1",
        resolved_llm_api_key="test-only",
        max_input_chars=200_000,
        prompt_version_patient_facts="v1",
        prompt_version_prior_claims="v1",
        prompt_version_claim_relations="v1",
        prompt_version_draft_review="v1",
        rules_version="test.1",
        llm_timeout_seconds=30,
        llm_max_retries=0,
        llm_temperature=0.2,
        llm_max_tokens=2000,
    )


def _create_case(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _valid_content(
    scope: object,
    draft_text: str,
    facts: list[object],
    opinions: list[object],
) -> dict:
    """Build schema-valid model output from the program-derived facts/claims.

    This is a test fixture, not a real model. It only needs to satisfy the
    structural and reference validator used by the real path.
    """
    sentences = split_draft_sentences(draft_text)
    findings: list[dict] = []

    def has_text(value: object) -> str:
        return str(getattr(value, "statement", "") or "")

    def matching_fact(term: str) -> object | None:
        return next(
            (fact for fact in facts if term in has_text(fact)),
            None,
        )

    def matching_claim(term: str) -> object | None:
        return next(
            (claim for claim in opinions if term in has_text(claim)),
            None,
        )

    for index, sentence in enumerate(sentences):
        text = sentence.text
        term = next(
            (candidate for candidate in ("痰", "发热", "体温", "血常规") if candidate in text),
            None,
        )
        fact = matching_fact(term) if term else None
        claim = matching_claim(term) if term else None
        if fact is not None:
            refs = [
                {
                    "message_id": ref.message_id,
                    "role": ref.role,
                    "snippet": ref.snippet,
                    "kind": ref.kind,
                }
                for ref in fact.evidence_refs
            ]
            status = str(fact.status)
            if status == "confirmed" and any(
                ref["role"] == "patient" for ref in refs
            ):
                grounding = "patient_stated"
            elif status == "uncertain":
                grounding = "patient_uncertain"
            elif status == "asked_unanswered":
                grounding = "doctor_question_only"
                doctor_refs = [
                    {
                        "message_id": ref.message_id,
                        "role": ref.role,
                        "snippet": ref.snippet,
                        "kind": ref.kind,
                    }
                    for ref in claim.evidence_refs
                ] if claim is not None else []
                refs = doctor_refs
            else:
                grounding = "unmentioned"
                refs = []
            related_fact_ids = [str(fact.id)] if fact is not None else []
            related_claim_ids = [str(claim.id)] if claim is not None else []
        elif claim is not None:
            grounding = "doctor_question_only"
            refs = [
                {
                    "message_id": ref.message_id,
                    "role": ref.role,
                    "snippet": ref.snippet,
                    "kind": ref.kind,
                }
                for ref in claim.evidence_refs
            ]
            related_fact_ids: list[str] = []
            related_claim_ids = [str(claim.id)]
        else:
            grounding = "unmentioned"
            refs = []
            related_fact_ids = []
            related_claim_ids = []

        findings.append(
            {
                "id": f"llm-finding-{index}",
                "draft_sentence_id": sentence.id,
                "draft_sentence_text": sentence.text,
                "contribution": "repeats_prior"
                if claim is not None
                else "new_claim",
                "patient_grounding": grounding,
                "explanation": "测试桩输出：按程序事实与观点生成结构合法的审阅结果。",
                "evidence_refs": refs,
                "related_fact_ids": related_fact_ids,
                "related_claim_ids": related_claim_ids,
                "suggested_action": "keep_and_confirm"
                if grounding == "patient_stated"
                else "clarify_or_revise",
                "review_status": "pending",
            }
        )
    return {"findings": findings}


def _patch_real_mode(
    monkeypatch,
    fake_complete=None,
) -> None:
    settings = _fake_settings()
    monkeypatch.setattr(joint_review_service, "get_settings", lambda: settings)
    if fake_complete is None:
        def fake_complete(
            scope,
            draft_text,
            facts,
            opinions,
            settings_,
            run_id,
        ):
            content = _valid_content(scope, draft_text, facts, opinions)
            return {
                "status": "ok",
                "content": content,
                "output": json.dumps(content, ensure_ascii=False),
                "error_class": None,
                "error": None,
                "latency_ms": 123,
                "request_prompt": "test prompt without credentials",
            }
    monkeypatch.setattr(llm_client, "complete_joint_review", fake_complete)


def _run_joint(
    client: TestClient,
    case_id: str,
    headers: dict[str, str],
    review_context: str = "replay",
) -> dict:
    response = client.post(
        f"/api/cases/{case_id}/joint-review",
        headers=headers,
        json={
            "focus_message_id": "wy-a-2003-focus",
            "review_context": review_context,
        },
    )
    return response


def test_real_prompts_enumerate_draft_entries_one_finding_each():
    scope = SimpleNamespace(
        review_context="replay",
        visible_messages=[
            SimpleNamespace(
                id="wy-q-1001",
                role="patient",
                created_at="2026-01-01T08:00:00",
                text="前天开始鼻塞，昨天开始有痰。",
            ),
            SimpleNamespace(
                id="wy-a-2001",
                role="doctor",
                created_at="2026-01-01T09:00:00",
                text="这两天量过体温吗？",
            ),
        ],
    )
    draft_text = "患者昨天开始有痰，目前无发热。"

    joint_prompt = llm_client.build_joint_review_prompt(
        scope,
        draft_text,
        facts=[],
        opinions=[],
    )
    baseline_prompt = llm_client.build_baseline_prompt(scope, draft_text)

    for prompt in (joint_prompt, baseline_prompt):
        assert "恰好对应一条 finding" in prompt
        assert "ds-0|患者昨天开始有痰，" in prompt
        assert "ds-1|目前无发热。" in prompt


def test_real_joint_review_success_persists_model_call(
    client: TestClient,
    monkeypatch,
):
    _patch_real_mode(monkeypatch)
    headers = login_headers(client)
    case = _create_case(client, headers)

    response = _run_joint(client, case["id"], headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_mode"] == "llm_joint"
    assert body["model_name"] == "qwen-test-real"
    assert body["review_context"] == "replay"
    assert len(body["findings"]) == len(body["draft_sentences"])

    with SessionLocal() as db:
        run = db.get(AnalysisRun, body["run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.source_mode == "llm_joint"
        call = (
            db.query(ModelCall)
            .filter(ModelCall.run_id == run.id, ModelCall.tool == "joint_review")
            .one()
        )
        assert call.status == "ok"
        assert call.latency_ms == 123
        assert call.model == "qwen-test-real"
        assert "test key" not in (call.prompt_text or "")
        persisted = db.query(Finding).filter(Finding.run_id == run.id).count()
        assert persisted == len(body["findings"])


def test_real_joint_review_model_failure_stays_failed(
    client: TestClient,
    monkeypatch,
):
    def fake_failure(*args, **kwargs):
        return {
            "status": "failed",
            "content": None,
            "output": None,
            "error_class": "timeout_error",
            "error": "simulated timeout",
            "latency_ms": 500,
            "request_prompt": "test prompt",
        }

    _patch_real_mode(monkeypatch, fake_complete=fake_failure)
    headers = login_headers(client)
    case = _create_case(client, headers)
    response = _run_joint(client, case["id"], headers)
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["error_class"] == "timeout_error"

    with SessionLocal() as db:
        run = db.get(AnalysisRun, detail["run_id"])
        assert run is not None
        assert run.status == "failed"
        call = db.query(ModelCall).filter(ModelCall.run_id == run.id).one()
        assert call.status == "failed"
        assert call.error_class == "timeout_error"


def test_real_joint_review_rejects_out_of_scope_evidence(
    client: TestClient,
    monkeypatch,
):
    _patch_real_mode(monkeypatch)
    headers = login_headers(client)
    case = _create_case(client, headers)

    def fake_bad_scope(scope, draft_text, facts, opinions, settings_, run_id):
        content = _valid_content(scope, draft_text, facts, opinions)
        content["findings"][0]["evidence_refs"].append(
            {
                "message_id": "wy-a-2003-focus",
                "role": "doctor",
                "snippet": "焦点回答",
                "kind": "direct_support",
            }
        )
        content["findings"][0]["patient_grounding"] = "doctor_question_only"
        return {
            "status": "ok",
            "content": content,
            "output": json.dumps(content, ensure_ascii=False),
            "error_class": None,
            "error": None,
            "latency_ms": 10,
            "request_prompt": "test",
        }

    _patch_real_mode(monkeypatch, fake_complete=fake_bad_scope)
    response = _run_joint(client, case["id"], headers)
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["error_class"] == "output_validation_error"
    assert "outside strict scope" in detail["error"]
    with SessionLocal() as db:
        run = db.get(AnalysisRun, detail["run_id"])
        assert run.status == "failed"


def test_real_joint_review_rejects_role_mismatch(
    client: TestClient,
    monkeypatch,
):
    _patch_real_mode(monkeypatch)
    headers = login_headers(client)
    case = _create_case(client, headers)
    detail_response = client.get(f"/api/cases/{case['id']}", headers=headers)
    messages = {msg["id"]: msg for msg in detail_response.json()["messages"]}

    def fake_role_mismatch(scope, draft_text, facts, opinions, settings_, run_id):
        content = _valid_content(scope, draft_text, facts, opinions)
        content["findings"][1]["patient_grounding"] = "doctor_question_only"
        content["findings"][1]["evidence_refs"] = [
            {
                "message_id": "wy-q-1001",
                "role": "patient",
                "snippet": messages["wy-q-1001"]["text"],
                "kind": "direct_support",
            }
        ]
        return {
            "status": "ok",
            "content": content,
            "output": json.dumps(content, ensure_ascii=False),
            "error_class": None,
            "error": None,
            "latency_ms": 10,
            "request_prompt": "test",
        }

    _patch_real_mode(monkeypatch, fake_complete=fake_role_mismatch)
    response = _run_joint(client, case["id"], headers)
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "role mismatch" in detail["error"] or "evidence" in detail["error"]


def test_current_scope_keeps_prior_doctors_and_adds_supplement(
    client: TestClient,
    monkeypatch,
):
    _patch_real_mode(monkeypatch)
    headers = login_headers(client)
    case = _create_case(client, headers)
    response = client.post(
        f"/api/cases/{case['id']}/supplements",
        headers=headers,
        json={"content": "我今天早上发过烧，现在没量。"},
    )
    assert response.status_code == 200, response.text
    supplement = response.json()

    strict = client.post(
        f"/api/cases/{case['id']}/replay",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus", "review_context": "replay"},
    )
    current = client.post(
        f"/api/cases/{case['id']}/replay",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus", "review_context": "current"},
    )
    assert strict.status_code == 200, strict.text
    assert current.status_code == 200, current.text
    strict_body = strict.json()
    current_body = current.json()
    assert strict_body["review_context"] == "replay"
    assert current_body["review_context"] == "current"
    assert set(current_body["prior_answer_message_ids"]) == set(
        strict_body["prior_answer_message_ids"]
    )
    assert supplement["message_id"] in current_body["patient_message_ids"]
    assert supplement["message_id"] not in strict_body["patient_message_ids"]
    assert "wy-p-1002" not in current_body["patient_message_ids"]

    joint = _run_joint(client, case["id"], headers, review_context="current")
    assert joint.status_code == 200, joint.text
    joint_body = joint.json()
    assert joint_body["review_context"] == "current"
    assert supplement["message_id"] in joint_body["findings"][0][
        "checked_message_ids"
    ] or supplement["message_id"] in set(
        msg_id
        for finding in joint_body["findings"]
        for msg_id in finding["checked_message_ids"]
    )
    assert any(
        "患者（人工补录）" in msg.get("speaker_name", "")
        for msg in current_body["visible_messages"]
        if msg["id"] == supplement["message_id"]
    )


def test_commit_before_model_call_releases_write_lock_for_concurrent_analysis(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "concurrency.db"
    configure_database(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.drop_all(bind=get_engine())
    Base.metadata.create_all(bind=get_engine())
    with SessionLocal() as db:
        ensure_seed_users(db)

    client = TestClient(app)
    headers = login_headers(client)
    case = _create_case(client, headers)

    started = threading.Event()
    release = threading.Event()

    def slow_complete(scope, draft_text, facts, opinions, settings_, run_id):
        started.set()
        release.wait(10)
        content = _valid_content(scope, draft_text, facts, opinions)
        return {
            "status": "ok",
            "content": content,
            "output": json.dumps(content, ensure_ascii=False),
            "error_class": None,
            "error": None,
            "latency_ms": 123,
            "request_prompt": "test prompt without credentials",
        }

    _patch_real_mode(monkeypatch, fake_complete=slow_complete)
    joint_errors: list[Exception] = []

    def run_joint():
        try:
            with SessionLocal() as db:
                case_obj = db.get(Case, case["id"])
                joint_review_service.run_joint_review(
                    db,
                    case_obj,
                    "wy-a-2003-focus",
                    "replay",
                )
        except Exception as exc:  # noqa: BLE001 - collected for assertion
            joint_errors.append(exc)

    thread = threading.Thread(target=run_joint)
    thread.start()
    try:
        assert started.wait(8), "joint review did not reach the model call"
        with SessionLocal() as db:
            case_obj = db.get(Case, case["id"])
            response = patient_facts_service.run_patient_fact_analysis(
                db,
                case_obj,
                "wy-a-2003-focus",
                "replay",
            )
        assert response.source_mode == "stub_rules"
    finally:
        release.set()
        thread.join(15)

    assert not thread.is_alive(), "joint review thread did not finish"
    assert joint_errors == []
