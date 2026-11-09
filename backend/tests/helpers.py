from __future__ import annotations

from fastapi.testclient import TestClient


def login_headers(
    client: TestClient,
    username: str = "demo",
    password: str = "demo1234",
) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}
