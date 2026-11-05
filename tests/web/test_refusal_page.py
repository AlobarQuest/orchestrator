"""A refused /review request renders the refusal page; the JSON API is unchanged.

Devon's decision 2 of the platform layer design: a person at a /review gate who is refused sees
one HTML page at the status the JSON had, with the refusal's code, message, recovery and current
state and version, and a link back. Every case goes through `create_app()`, because an included
router's routes are rebuilt per inclusion and the page must be proven through the app. A 500 stays
Part 2's JSON `internal_error`, and the decomposition grant refusal stays in place on its own page
(`test_decomposition_review.py`).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig, get_session
from orchestrator.api.error_shape import authentication_message
from orchestrator.errors import DomainError
from orchestrator.main import create_app
from orchestrator.persistence.models import ObservationSource, StagedPackageIntake, WorkUnit
from orchestrator.web import RefusalPageRoute, SessionDep
from orchestrator.web import router as web_router
from tests._support.refusal_page import refusal
from tests.api.test_lifecycle_api import HUMAN
from tests.api.test_observer_role_confinement import OBSERVER
from tests.web.test_staged_intake import _confirm_fields, _press, _stage

_PROBE_SOURCE = "refusal-page-probe"


def _form(page: str, unit_id: object, action: str) -> tuple[str, str]:
    form = re.search(rf'action="/review/units/{unit_id}/{action}">(.*?)</form>', page, re.DOTALL)
    assert form is not None, f"no {action} form on the unit page"
    token = re.search(r'name="csrf_token" value="([^"]+)"', form.group(1))
    key = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(1))
    assert token is not None and key is not None
    return token.group(1), key.group(1)


def _assert_page(response, status: int, code: str) -> dict[str, str]:
    assert response.status_code == status, (response.status_code, response.text)
    assert response.headers["content-type"].startswith("text/html")
    fields = refusal(response)
    assert fields["code"] == code, fields
    return fields


def _probe_router() -> APIRouter:
    probe = APIRouter(prefix="/review", route_class=RefusalPageRoute, include_in_schema=False)

    @probe.get("/_probe/escape")
    def escape() -> None:
        raise DomainError("x_refused", "<script>alert(1)</script>", "<b>r</b>")

    @probe.get("/_probe/crash")
    def crash() -> None:
        raise RuntimeError("not a refusal")

    @probe.post("/_probe/write-then-refuse")
    def write_then_refuse(session: SessionDep) -> None:
        session.add(
            ObservationSource(
                source_system=_PROBE_SOURCE,
                subject_reference="row",
                last_seen_at=datetime.now(UTC),
            )
        )
        session.flush()
        raise DomainError("probe_refused", "refused after a write", None)

    return probe


@pytest.fixture
def probe_client(auth_config: AuthConfig, migrated_engine: Engine) -> Iterator[TestClient]:
    """An app built as `db_client` builds it, with the throwaway router included."""
    app = create_app(auth_config)

    def database_session() -> Iterator[Session]:
        with Session(migrated_engine) as session:
            yield session

    app.dependency_overrides[get_session] = database_session
    app.include_router(_probe_router())
    with TestClient(
        app, base_url="https://testserver", raise_server_exceptions=False
    ) as test_client:
        yield test_client


def test_a_service_refusal_renders_the_page_with_the_current_version(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form(page.text, review_unit.id, "review")
    response = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(review_unit.version - 1),
            "outcome": "revision_required",
            "reason": "stale",
            "confirm": "yes",
        },
    )

    fields = _assert_page(response, 409, "version_conflict")
    assert fields["message"]
    assert fields["message"] in response.text
    assert int(fields["current-version"]) == review_unit.version
    assert "set-cookie" not in response.headers


def test_a_csrf_refusal_renders_the_page(db_client: TestClient, review_unit: WorkUnit) -> None:
    response = db_client.post(
        f"/review/units/{review_unit.id}/cancel",
        headers=HUMAN,
        data={
            "csrf_token": "forged.token",
            "idempotency_key": "forged",
            "expected_version": str(review_unit.version),
            "reason": "forged",
            "confirm": "yes",
        },
    )

    _assert_page(response, 403, "csrf_rejected")


def test_a_refusal_in_the_actor_dependency_renders_the_page(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    response = db_client.post(f"/review/units/{review_unit.id}/cancel", headers=OBSERVER, data={})

    _assert_page(response, 403, "role_forbidden")


def test_no_identity_is_a_401_page_with_the_bearer_challenge(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    response = db_client.get(f"/review/units/{review_unit.id}")

    fields = _assert_page(response, 401, "authentication_required")
    assert fields["message"] == authentication_message("authentication_required")
    assert response.headers["www-authenticate"] == "Bearer"


def test_a_validation_refusal_names_the_field_and_echoes_no_submitted_value(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    marker = "distinctive-submitted-value-" + "q" * 64
    response = db_client.post(
        f"/review/units/{review_unit.id}/cancel",
        headers=HUMAN,
        data={
            "csrf_token": marker,
            "idempotency_key": marker,
            "expected_version": str(review_unit.version),
            "reason": "",
            "confirm": "yes",
        },
    )

    fields = _assert_page(response, 422, "request_invalid")
    # FastAPI reads an empty form field as absent, so pydantic's message is "Field required".
    assert fields["message"] == "reason: Field required"
    assert marker not in response.text


def test_get_refusals_render_the_page(db_client: TestClient) -> None:
    _assert_page(
        db_client.get(f"/review/units/{uuid.uuid4()}", headers=HUMAN), 404, "work_unit_not_found"
    )
    malformed = _assert_page(
        db_client.get("/review/units/not-a-uuid", headers=HUMAN), 422, "request_invalid"
    )
    assert malformed["message"].startswith("unit_id: ")


def test_a_missing_csrf_secret_is_a_503_page_that_sets_no_cookie(
    db_client: TestClient, auth_config: AuthConfig, review_unit: WorkUnit
) -> None:
    cast(FastAPI, db_client.app).state.auth_config = replace(auth_config, csrf_secret=None)
    response = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    _assert_page(response, 503, "csrf_unavailable")
    assert "set-cookie" not in response.headers


def test_the_page_shows_the_recovery(db_client: TestClient, migrated_engine: Engine) -> None:
    staged_id = _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN)
    with Session(migrated_engine) as session:
        row = session.get(StagedPackageIntake, uuid.UUID(staged_id))
        assert row is not None
        row.payload = {key: value for key, value in row.payload.items() if key != "source_commit"}
        session.commit()

    response = _press(db_client, staged_id, _confirm_fields(page.text, staged_id))

    fields = _assert_page(response, 409, "intake_payload_invalid")
    assert "withdraw this staged intake and re-stage" in fields["recovery"]
    assert "withdraw this staged intake and re-stage" in response.text


def test_every_field_is_escaped(probe_client: TestClient) -> None:
    response = probe_client.get("/review/_probe/escape")

    fields = _assert_page(response, 409, "x_refused")
    assert fields["message"] == "<script>alert(1)</script>"
    assert fields["recovery"] == "<b>r</b>"
    assert "&lt;script&gt;" in response.text
    assert "<script>alert" not in response.text
    assert "<b>r</b>" not in response.text


def _back(response) -> str:
    links = re.findall(r'<a href="([^"]*)">Back</a>', response.text)
    assert len(links) == 1, links
    return links[0]


def test_the_link_back_is_a_review_path_only(db_client: TestClient) -> None:
    unit_path = f"/review/units/{uuid.uuid4()}"

    def back(referer: str | None) -> str:
        headers = dict(HUMAN) if referer is None else {**HUMAN, "Referer": referer}
        return _back(db_client.get(unit_path, headers=headers))

    assert back(f"https://sds.alobar.net{unit_path}") == unit_path
    assert back("https://sds.alobar.net/review") == "/review"
    for referer in (
        "https://evil.example/elsewhere",
        None,
        "https://sds.alobar.net/api/v1/work-units",
        "https://sds.alobar.net/reviewer",
    ):
        link = back(referer)
        assert link == "/review", (referer, link)
    # An unparseable Referer (unclosed IPv6 bracket) must not turn the refusal into a 500.
    for referer in ("http://[::1/review/x", "//[x/review", "http://a]/review"):
        assert back(referer) == "/review", referer
    # A path carrying markup is escaped into the attribute, never injected as markup.
    markup = '/review/x"><script>alert(1)</script>'
    response = db_client.get(unit_path, headers={**HUMAN, "Referer": f"https://h{markup}"})
    assert response.status_code == 404
    assert "<script>alert(1)</script>" not in response.text
    assert _back(response).startswith("/review")
    # Only the path is used, so another host's /review path cannot send a person off the host.
    for referer in (f"https://evil.example{unit_path}", f"//evil.example{unit_path}"):
        assert back(referer) == unit_path, referer


def test_a_500_stays_the_json_internal_error(probe_client: TestClient) -> None:
    response = probe_client.get("/review/_probe/crash")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "internal_error"


def test_a_write_made_before_the_refusal_is_not_kept(
    probe_client: TestClient, migrated_engine: Engine
) -> None:
    response = probe_client.post("/review/_probe/write-then-refuse")

    _assert_page(response, 409, "probe_refused")
    with Session(migrated_engine) as session:
        count = session.scalar(
            select(func.count())
            .select_from(ObservationSource)
            .where(ObservationSource.source_system == _PROBE_SOURCE)
        )
    assert count == 0


def test_the_production_session_does_not_keep_a_write_made_before_a_refusal(
    auth_config: AuthConfig, migrated_engine: Engine
) -> None:
    """With no `get_session` override: the route class swallows the refusal, so the generator
    exits normally, and it must not commit on that path."""
    app = create_app(auth_config)
    app.include_router(_probe_router())
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        response = client.post("/review/_probe/write-then-refuse")
    _assert_page(response, 409, "probe_refused")
    with Session(migrated_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ObservationSource)
                .where(ObservationSource.source_system == _PROBE_SOURCE)
            )
            == 0
        )


def test_the_production_session_generator_does_not_commit_on_normal_exit(
    migrated_engine: Engine,
) -> None:
    generator = get_session()
    session = next(generator)
    session.add(
        ObservationSource(
            source_system=_PROBE_SOURCE,
            subject_reference="direct",
            last_seen_at=datetime.now(UTC),
        )
    )
    session.flush()
    with pytest.raises(StopIteration):
        next(generator)
    with Session(migrated_engine) as other:
        assert (
            other.scalar(
                select(func.count())
                .select_from(ObservationSource)
                .where(ObservationSource.source_system == _PROBE_SOURCE)
            )
            == 0
        )


def test_an_unrouted_path_a_wrong_method_and_an_unparseable_body_stay_json(
    db_client: TestClient,
) -> None:
    unrouted = db_client.get("/review/no-such-page", headers=HUMAN)
    wrong_method = db_client.delete("/review", headers=HUMAN)
    unparseable = db_client.post(
        f"/review/units/{uuid.uuid4()}/cancel",
        headers={**HUMAN, "Content-Type": "multipart/form-data; boundary=x"},
        content=b"not a multipart body",
    )
    for response, status in ((unrouted, 404), (wrong_method, 405), (unparseable, 400)):
        assert response.status_code == status, (status, response.status_code, response.text)
        assert response.headers["content-type"].startswith("application/json")
        assert "detail" in response.json()


def _not_refusal_routes(routes: list) -> list[str]:
    return [
        route.path
        for route in routes
        if isinstance(route, APIRoute) and not isinstance(route, RefusalPageRoute)
    ]


def test_every_review_route_renders_refusals_as_the_page() -> None:
    assert web_router.routes
    assert _not_refusal_routes(web_router.routes) == []

    def plain() -> None:
        return None

    control = [*web_router.routes, APIRoute("/review/plain", plain)]
    assert _not_refusal_routes(control) == ["/review/plain"]
