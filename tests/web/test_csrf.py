import re
from dataclasses import replace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import orchestrator.web
from orchestrator.api.dependencies import AuthConfig
from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import HUMAN, WORKER


def test_human_mutation_rejects_missing_csrf_and_confirmation(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    response = db_client.post(
        f"/review/units/{review_unit.id}/cancel",
        headers=HUMAN,
        data={
            "expected_version": str(review_unit.version),
            "reason": "No longer required",
        },
    )

    assert response.status_code == 403


def _form_fields(page: str, unit_id: object, action: str) -> tuple[str, str]:
    form = re.search(rf'action="/review/units/{unit_id}/{action}">(.*?)</form>', page)
    assert form is not None
    token = re.search(r'name="csrf_token" value="([^"]+)"', form.group(1))
    key = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(1))
    assert token is not None and key is not None
    return token.group(1), key.group(1)


def test_csrf_is_secure_bound_and_expires(
    db_client: TestClient, review_unit: WorkUnit, monkeypatch
) -> None:
    now = 1_800_000_000
    monkeypatch.setattr(orchestrator.web.time, "time", lambda: now)
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form_fields(page.text, review_unit.id, "review")
    cookie = page.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie

    common = {
        "csrf_token": token,
        "idempotency_key": key,
        "expected_version": str(review_unit.version),
        "reason": "Bound command",
        "confirm": "yes",
    }
    cross_action = db_client.post(
        f"/review/units/{review_unit.id}/cancel", headers=HUMAN, data=common
    )
    assert cross_action.status_code == 403
    cross_unit = db_client.post(
        "/review/units/00000000-0000-0000-0000-000000000001/review",
        headers=HUMAN,
        data={**common, "outcome": "revision_required"},
    )
    assert cross_unit.status_code == 403
    cross_actor = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=WORKER,
        data={**common, "outcome": "revision_required"},
    )
    assert cross_actor.status_code == 403
    db_client.cookies.set(orchestrator.web.CSRF_COOKIE, "attacker-fixed-session")
    fixation = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=HUMAN,
        data={**common, "outcome": "revision_required"},
    )
    assert fixation.status_code == 403
    original_cookie = re.search(rf"{orchestrator.web.CSRF_COOKIE}=([^;]+)", cookie)
    assert original_cookie is not None
    db_client.cookies.set(orchestrator.web.CSRF_COOKIE, original_cookie.group(1))

    monkeypatch.setattr(
        orchestrator.web.time, "time", lambda: now + orchestrator.web.CSRF_TTL_SECONDS + 1
    )
    expired = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=HUMAN,
        data={**common, "outcome": "revision_required"},
    )
    assert expired.status_code == 403


def test_attacker_cookie_is_rotated_before_valid_post(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    db_client.cookies.set(orchestrator.web.CSRF_COOKIE, "attacker-fixed-session")
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form_fields(page.text, review_unit.id, "review")
    cookie = re.search(rf"{orchestrator.web.CSRF_COOKIE}=([^;]+)", page.headers["set-cookie"])
    assert cookie is not None
    assert cookie.group(1) != "attacker-fixed-session"
    db_client.cookies.set(orchestrator.web.CSRF_COOKIE, cookie.group(1))

    response = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(review_unit.version),
            "outcome": "revision_required",
            "reason": "Signed session accepted",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_malformed_base64_is_rejected_not_raised(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    response = db_client.post(
        f"/review/units/{review_unit.id}/cancel",
        headers=HUMAN,
        data={
            "csrf_token": "/w==.invalid",
            "idempotency_key": "malformed",
            "expected_version": str(review_unit.version),
            "reason": "malformed",
            "confirm": "yes",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_rejected"


def test_csrf_secret_configuration_is_strong_and_missing_is_stable(
    db_client: TestClient,
    auth_config: AuthConfig,
    review_unit: WorkUnit,
) -> None:
    for value in (b"", b"weak"):
        with pytest.raises(ValueError, match="at least 32 bytes"):
            replace(auth_config, csrf_secret=value)

    cast(FastAPI, db_client.app).state.auth_config = replace(auth_config, csrf_secret=None)
    response = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "csrf_unavailable"
