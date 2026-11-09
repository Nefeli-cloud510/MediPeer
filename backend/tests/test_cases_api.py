from __future__ import annotations

from fastapi.testclient import TestClient

from .helpers import login_headers


def test_login_success_and_wrong_password(client: TestClient):
    ok = client.post(
        "/api/auth/login",
        json={"username": "demo", "password": "demo1234"},
    )
    assert ok.status_code == 200
    assert ok.json()["user"]["username"] == "demo"
    assert ok.json()["token"]

    bad = client.post(
        "/api/auth/login",
        json={"username": "demo", "password": "wrong"},
    )
    assert bad.status_code == 401


def test_demo_templates_listed(client: TestClient):
    headers = login_headers(client)
    response = client.get("/api/demo/templates", headers=headers)
    assert response.status_code == 200
    keys = {item["key"] for item in response.json()}
    assert "demo_respiratory_multi_doctor" in keys
    assert "demo_respiratory_single_doctor" in keys


def test_create_and_load_demo_case(client: TestClient):
    headers = login_headers(client)
    created = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    case_id = body["id"]
    assert body["version_no"] == 1
    assert len(body["messages"]) == 7
    assert body["draft"]["current_text"].startswith("患者目前咳嗽伴发热")

    detail = client.get(f"/api/cases/{case_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == case_id
    assert len(detail.json()["messages"]) == 7

    draft = client.get(f"/api/cases/{case_id}/draft", headers=headers)
    assert draft.status_code == 200
    assert draft.json()["case_id"] == case_id


def test_replay_scope_excludes_focus_later_and_same_time(client: TestClient):
    headers = login_headers(client)
    created = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    ).json()
    case_id = created["id"]
    response = client.post(
        f"/api/cases/{case_id}/replay",
        headers=headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cutoff_at"] == "2026-10-01T09:00:00+00:00"
    assert body["prior_answer_message_ids"] == ["wy-a-2001", "wy-a-2002"]
    assert body["patient_message_ids"] == ["wy-q-1001"]
    visible_ids = [m["id"] for m in body["visible_messages"]]
    assert visible_ids == ["wy-q-1001", "wy-a-2001", "wy-a-2002"]
    assert body["order_quality"] == "ok"

    excluded = body["exclusion_reasons"]
    assert "wy-a-2003-focus" in excluded
    assert "wy-a-2004-same-time" in excluded
    assert "wy-p-1002" in excluded
    assert "wy-a-2005-future" in excluded
    assert "同时刻" in excluded["wy-a-2004-same-time"]
    assert "晚于焦点" in excluded["wy-p-1002"]


def test_replay_unknown_focus_returns_404(client: TestClient):
    headers = login_headers(client)
    case_id = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    ).json()["id"]
    response = client.post(
        f"/api/cases/{case_id}/replay",
        headers=headers,
        json={"focus_message_id": "not-exists"},
    )
    assert response.status_code == 404


def test_case_isolation_between_users(client: TestClient):
    demo_headers = login_headers(client, "demo", "demo1234")
    judge_headers = login_headers(client, "judge", "judge1234")
    demo_case = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=demo_headers,
    ).json()
    judge_case = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=judge_headers,
    ).json()
    assert demo_case["id"] != judge_case["id"]

    forbidden = client.get(f"/api/cases/{demo_case['id']}", headers=judge_headers)
    assert forbidden.status_code == 404
    replay = client.post(
        f"/api/cases/{demo_case['id']}/replay",
        headers=judge_headers,
        json={"focus_message_id": "wy-a-2003-focus"},
    )
    assert replay.status_code == 404


def test_case_restored_after_reload(client: TestClient):
    headers = login_headers(client)
    case_id = client.post(
        "/api/cases/demo/demo_respiratory_multi_doctor",
        headers=headers,
    ).json()["id"]
    first = client.get(f"/api/cases/{case_id}", headers=headers).json()
    second = client.get(f"/api/cases/{case_id}", headers=headers).json()
    assert first["id"] == second["id"]
    assert first["messages"] == second["messages"]
    assert first["draft"] == second["draft"]
