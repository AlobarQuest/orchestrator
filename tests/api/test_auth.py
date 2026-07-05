from fastapi.testclient import TestClient


def test_m2m_authentication_reaches_route(client: TestClient) -> None:
    response = client.get(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000001/readiness",
        headers={
            "Authorization": "Bearer fixture-token",
            "X-Credential-Key-Id": "worker-key",
        },
    )

    assert response.status_code != 401


def test_human_authentication_reaches_route(client: TestClient) -> None:
    response = client.get(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000001/readiness",
        headers={
            "X-Alobar-Proxy": "fixture-marker",
            "X-Alobar-Email": "devon@example.invalid",
        },
    )

    assert response.status_code != 401


def test_spoofable_actor_headers_are_not_authentication(client: TestClient) -> None:
    response = client.get(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000001/readiness",
        headers={"X-Actor-Id": "devon", "X-Actor-Role": "human"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "valid authentication credentials are required",
        }
    }


def test_invalid_machine_credentials_have_stable_error(client: TestClient) -> None:
    response = client.get(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000001/readiness",
        headers={
            "Authorization": "Bearer wrong-token",
            "X-Credential-Key-Id": "worker-key",
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_failed",
            "message": "authentication credentials were rejected",
        }
    }


def test_duplicate_forward_auth_headers_are_rejected(client: TestClient) -> None:
    response = client.get(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000001/readiness",
        headers=[
            ("X-Alobar-Proxy", "fixture-marker"),
            ("X-Alobar-Email", "devon@example.invalid"),
            ("x-alobar-email", "attacker@example.invalid"),
        ],
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"
