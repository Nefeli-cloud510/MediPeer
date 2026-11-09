from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.services.patient_facts_service import extract_patient_facts
from backend.app.services.scope_service import compute_replay_scope

from .helpers import login_headers


def make_scope(patient_text: str, doctor_text: str | None = None):
    messages = [
        {
            "id": "q",
            "role": "patient",
            "speaker_id": "p1",
            "text": patient_text,
            "created_at": "2026-10-01T08:00:00+00:00",
        }
    ]
    if doctor_text:
        messages.append(
            {
                "id": "d-prior",
                "role": "doctor",
                "speaker_id": "d1",
                "text": doctor_text,
                "created_at": "2026-10-01T08:30:00+00:00",
            }
        )
    messages.append(
        {
            "id": "focus",
            "role": "doctor",
            "speaker_id": "d2",
            "text": "请继续观察。",
            "created_at": "2026-10-01T09:00:00+00:00",
        }
    )
    return compute_replay_scope("case-facts", messages, "focus")


def test_unmentioned_fever_is_unknown_not_denied():
    scope = make_scope("咳嗽三天。")
    facts = extract_patient_facts(scope, "患者目前发热。")
    unknown = [f for f in facts if f.status == "unknown" and "发热" in f.statement]
    assert unknown
    assert unknown[0].evidence_refs == []
    assert unknown[0].checked_message_ids == ["q"]


def test_doctor_question_is_not_patient_fact():
    scope = make_scope("咳嗽三天。", "请问是否发热？")
    facts = extract_patient_facts(scope, "患者目前发热。")
    asked = [f for f in facts if f.status == "asked_unanswered" and "发热" in f.statement]
    assert asked
    evidence = asked[0].evidence_refs
    assert evidence[0].role == "doctor"
    assert evidence[0].snippet in "请问是否发热？"
    assert evidence[0].message_id == "d-prior"


def test_explicit_fever_denial_is_denied():
    scope = make_scope("没有发热。")
    facts = extract_patient_facts(scope, "患者目前发热。")
    denied = [f for f in facts if f.status == "denied" and "发热" in f.statement]
    assert denied
    assert denied[0].evidence_refs[0].snippet in "没有发热。"


def test_yesterday_fever_today_not_keeps_two_time_states():
    scope = make_scope("昨天发热，今天没有。")
    facts = extract_patient_facts(scope, "患者目前发热。")
    pairs = {
        (fact.status, fact.time_range)
        for fact in facts
        if "发热" in fact.statement or "发热" in fact.statement
    }
    assert ("confirmed", "昨天") in pairs
    assert ("denied", "今天") in pairs


def test_patient_facts_api_runs_and_persists(client: TestClient):
    headers = login_headers(client)
    case_id = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    ).json()["id"]
    response = client.post(
        f"/api/cases/{case_id}/patient-facts",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"]
    assert body["order_quality"] == "ok"
    assert body["source_mode"] == "stub_rules"

    asthma_unknown = [
        fact
        for fact in body["facts"]
        if fact["status"] == "unknown" and "哮喘" in fact["statement"]
    ]
    assert asthma_unknown
    assert asthma_unknown[0]["evidence_refs"] == []
    assert asthma_unknown[0]["checked_message_ids"] == ["wy-q-1001"]
    assert any(
        fact["status"] == "asked_unanswered"
        and fact["evidence_refs"][0]["role"] == "doctor"
        for fact in body["facts"]
        if fact["evidence_refs"]
    )
    assert any(
        fact["status"] == "denied" and "基础病" in fact["statement"]
        for fact in body["facts"]
    )

    # 运行记录确实落库；同一会话再次获取时应仍可返回结果路径。
    second = client.post(
        f"/api/cases/{case_id}/patient-facts",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert second.status_code == 200
    assert second.json()["run_id"] != body["run_id"]


def test_patient_facts_respects_user_isolation(client: TestClient):
    headers = login_headers(client, "demo", "demo1234")
    other_headers = login_headers(client, "judge", "judge1234")
    case_id = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    ).json()["id"]
    response = client.post(
        f"/api/cases/{case_id}/patient-facts",
        headers=other_headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 404
