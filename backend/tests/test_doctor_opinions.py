from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.services.doctor_opinions_service import extract_doctor_opinions
from backend.app.services.scope_service import compute_replay_scope

from .helpers import login_headers


def build_scope(
    doctors: list[tuple[str, str]],
    *,
    focus_text: str = "请继续观察。",
    patient_text: str = "咳嗽三天。",
) -> object:
    messages = [
        {
            "id": "q",
            "role": "patient",
            "speaker_id": "p1",
            "text": patient_text,
            "created_at": "2026-10-01T08:00:00+00:00",
        }
    ]
    for index, (message_id, text) in enumerate(doctors, start=1):
        messages.append(
            {
                "id": message_id,
                "role": "doctor",
                "speaker_id": f"d{index}",
                "text": text,
                "created_at": f"2026-10-01T08:{10 + index * 10:02d}:00+00:00",
            }
        )
    messages.append(
        {
            "id": "focus",
            "role": "doctor",
            "speaker_id": "d-focus",
            "text": focus_text,
            "created_at": "2026-10-01T09:00:00+00:00",
        }
    )
    return compute_replay_scope("case-opinions", messages, "focus")


def test_only_prior_doctor_answers_enter_context():
    messages = [
        {
            "id": "q",
            "role": "patient",
            "speaker_id": "p1",
            "text": "咳嗽三天。",
            "created_at": "2026-10-01T08:00:00+00:00",
        },
        {
            "id": "d1",
            "role": "doctor",
            "speaker_id": "d1",
            "text": "建议观察体温变化。",
            "created_at": "2026-10-01T08:30:00+00:00",
        },
        {
            "id": "focus",
            "role": "doctor",
            "speaker_id": "d-focus",
            "text": "请继续观察。",
            "created_at": "2026-10-01T09:00:00+00:00",
        },
        {
            "id": "same-time",
            "role": "doctor",
            "speaker_id": "d-same",
            "text": "同时刻建议。",
            "created_at": "2026-10-01T09:00:00+00:00",
        },
        {
            "id": "p2",
            "role": "patient",
            "speaker_id": "p1",
            "text": "补充：今天没有再烧。",
            "created_at": "2026-10-01T09:10:00+00:00",
        },
        {
            "id": "future",
            "role": "doctor",
            "speaker_id": "d-future",
            "text": "后续回答。",
            "created_at": "2026-10-01T09:30:00+00:00",
        },
    ]
    scope = compute_replay_scope("case-opinions", messages, "focus")
    opinions, _ = extract_doctor_opinions(scope, "")
    assert opinions
    assert {opinion.source_message_id for opinion in opinions} == {"d1"}


def test_doctor_question_is_classified_as_question_with_doctor_evidence():
    scope = build_scope([("d1", "请问是否发热？")])
    opinions, _ = extract_doctor_opinions(scope, "")
    questions = [
        opinion
        for opinion in opinions
        if opinion.claim_type == "clarifying_question"
    ]
    assert questions
    evidence = questions[0].evidence_refs[0]
    assert evidence.role == "doctor"
    assert evidence.message_id == "d1"
    assert evidence.snippet in "请问是否发热？"


def test_conditional_recommendation_keeps_condition_and_certainty():
    scope = build_scope(
        [("d1", "如果体温超过 38.5℃ 且持续不退，需要就诊。")]
    )
    opinions, _ = extract_doctor_opinions(scope, "")
    recommendation = [
        opinion
        for opinion in opinions
        if opinion.claim_type == "recommendation" and "就诊" in opinion.statement
    ]
    assert recommendation
    assert recommendation[0].certainty == "conditional"
    assert recommendation[0].condition
    assert "38.5℃" in recommendation[0].condition


def test_title_and_hospital_are_not_used_as_facts_or_correctness():
    messages = [
        {
            "id": "q",
            "role": "patient",
            "speaker_id": "p1",
            "text": "咳嗽三天。",
            "created_at": "2026-10-01T08:00:00+00:00",
        },
        {
            "id": "d1",
            "role": "doctor",
            "speaker_id": "d1",
            "speaker_name": "王医生",
            "speaker_title": "主任医师",
            "hospital": "三甲医院",
            "text": "如症状加重需要就诊，否则居家观察。",
            "created_at": "2026-10-01T08:30:00+00:00",
        },
        {
            "id": "focus",
            "role": "doctor",
            "speaker_id": "d-focus",
            "text": "请继续观察。",
            "created_at": "2026-10-01T09:00:00+00:00",
        },
    ]
    scope = compute_replay_scope("case-opinions", messages, "focus")
    opinions, _ = extract_doctor_opinions(scope, "")
    assert opinions
    assert all(
        opinion.claim_type != "patient_fact_statement"
        and opinion.claim_type != "possible_explanation"
        for opinion in opinions
    )
    assert all("主任医师" not in opinion.statement for opinion in opinions)
    assert all("三甲医院" not in opinion.statement for opinion in opinions)
    assert opinions[0].speaker_title == "主任医师"
    assert opinions[0].hospital == "三甲医院"


def test_draft_relation_marks_answered_and_unanswered_questions():
    scope = build_scope([("d1", "请问是否发热？有无胸闷气短？")])
    opinions, _ = extract_doctor_opinions(scope, "患者发热一天，建议多喝水。")
    fever = next(
        opinion for opinion in opinions if "发热" in opinion.statement
    )
    chest = next(
        opinion for opinion in opinions if "胸闷" in opinion.statement
    )
    assert fever.draft_relation == "answered_by_draft"
    assert chest.draft_relation == "unanswered_question"


def test_prior_opinion_opposing_draft_is_diverging():
    scope = build_scope([("d1", "暂不需要立即就诊，建议居家观察。")])
    opinions, _ = extract_doctor_opinions(scope, "建议立即就诊。")
    assert opinions
    recommendation = next(
        opinion for opinion in opinions if opinion.claim_type == "recommendation"
    )
    assert recommendation.polarity == "negative"
    assert recommendation.draft_relation == "diverging"


def test_peer_opinion_polarity_divergence():
    scope = build_scope(
        [
            ("d1", "暂不需要立即就诊。"),
            ("d2", "建议立即就诊。"),
        ]
    )
    _, relations = extract_doctor_opinions(scope, "")
    divergences = [
        relation for relation in relations if relation.relation_type == "divergence"
    ]
    assert divergences
    assert divergences[0].divergence_kind == "polarity"
    assert {divergences[0].source_message_id, divergences[0].target_message_id} == {
        "d1",
        "d2",
    }


def test_peer_opinion_conditional_difference():
    scope = build_scope(
        [
            ("d1", "如果体温超过 38.5℃，建议就诊。"),
            ("d2", "建议就诊。"),
        ]
    )
    _, relations = extract_doctor_opinions(scope, "")
    conditional = [
        relation
        for relation in relations
        if relation.relation_type == "conditional_diff"
    ]
    assert conditional
    assert conditional[0].divergence_kind == "condition"
    assert conditional[0].applicability_conditions
    assert "38.5℃" in conditional[0].applicability_conditions


def test_doctor_opinions_api_runs_and_persists(client: TestClient):
    headers = login_headers(client)
    case_id = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    ).json()["id"]
    response = client.post(
        f"/api/cases/{case_id}/doctor-opinions",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"]
    assert body["order_quality"] == "ok"
    assert body["source_mode"] == "stub_rules"
    assert {opinion["source_message_id"] for opinion in body["opinions"]} <= {
        "wy-a-2001",
        "wy-a-2002",
    }
    assert any(
        opinion["claim_type"] == "clarifying_question"
        for opinion in body["opinions"]
    )
    assert any(
        opinion["certainty"] == "conditional" and opinion["condition"]
        for opinion in body["opinions"]
    )
    assert any(
        opinion["draft_relation"] == "answered_by_draft"
        for opinion in body["opinions"]
    )
    assert body["peer_relations"]

    second = client.post(
        f"/api/cases/{case_id}/doctor-opinions",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert second.status_code == 200
    assert second.json()["run_id"] != body["run_id"]


def test_doctor_opinions_respects_user_isolation(client: TestClient):
    headers = login_headers(client, "demo", "demo1234")
    other_headers = login_headers(client, "judge", "judge1234")
    case_id = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    ).json()["id"]
    response = client.post(
        f"/api/cases/{case_id}/doctor-opinions",
        headers=other_headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 404
