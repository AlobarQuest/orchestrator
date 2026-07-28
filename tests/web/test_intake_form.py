"""The `/review` package-intake form -- the human surface ADR-0006 requires.

Package intake is a human gate, and `POST /api/v1/package-intakes` is unreachable by any principal
in production: it demands `ActorRole.HUMAN` while sitting on the machine-only router. Until this
form existed the gate was crossed by pasting a `fetch()` into browser devtools.

The load-bearing property under test is that the form is a new WAY IN and not a new SET OF RULES:
it must reach the same service through the same validated model as the API route, so a payload the
API would refuse is refused here too.
"""

import json
import re

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Event, WorkPackageRevision
from tests.api.test_lifecycle_api import HUMAN, WORKER
from tests.api.test_package_intake_api import intake_payload


def _form_fields(page_text: str) -> dict[str, str]:
    form = re.search(r'action="/review/intakes">(.*?)</form>', page_text, re.DOTALL)
    assert form is not None, "the new-intake page does not render the intake form"
    return dict(re.findall(r'name="(csrf_token|idempotency_key)" value="([^"]+)"', form.group(1)))


def _submit(client: TestClient, payload: object, **overrides: str) -> tuple[int, dict[str, str]]:
    page = client.get("/review/intakes/new", headers=HUMAN)
    assert page.status_code == 200
    fields = _form_fields(page.text)
    body = {
        "payload": payload if isinstance(payload, str) else json.dumps(payload),
        "csrf_token": fields["csrf_token"],
        "idempotency_key": fields["idempotency_key"],
        "confirm": "yes",
        **overrides,
    }
    response = client.post("/review/intakes", data=body, headers=HUMAN, follow_redirects=False)
    return response.status_code, fields


def test_the_new_intake_page_renders_a_human_form(db_client: TestClient) -> None:
    page = db_client.get("/review/intakes/new", headers=HUMAN)

    assert page.status_code == 200
    assert 'action="/review/intakes"' in page.text
    assert 'name="payload"' in page.text
    fields = _form_fields(page.text)
    assert fields["csrf_token"] and fields["idempotency_key"]


def test_the_new_intake_page_is_not_shadowed_by_the_revision_detail_route(
    db_client: TestClient,
) -> None:
    """`/intakes/new` and `/intakes/{revision_id}` share a shape; declaration order decides.

    If the parameterised route is ever moved above this one, `new` is parsed as a UUID and the
    form becomes unreachable -- a 422, not a 404, so it would not look like a routing mistake.
    """
    assert db_client.get("/review/intakes/new", headers=HUMAN).status_code == 200


def test_a_valid_payload_registers_the_intake_and_redirects_to_it(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    status, _ = _submit(db_client, intake_payload())

    assert status == 303
    with Session(migrated_engine) as session:
        revision = session.scalars(
            select(WorkPackageRevision).where(WorkPackageRevision.content_hash == "sha256:one")
        ).one()
        # The service, not the form, decides these -- proving the form reached the real path.
        assert revision.verification_mode == "caller_attested_cli_verified"
        assert revision.registered_by == "devon"


def test_the_form_commits_so_the_revision_survives_the_request(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """A flush would make the redirect look right and leave the table empty (the WS-P2.1 defect).

    Read on a FRESH session so this cannot pass on the request session's identity map.
    """
    _submit(db_client, intake_payload())

    with Session(migrated_engine) as session:
        assert session.scalars(select(WorkPackageRevision)).all() != []


def test_a_non_human_actor_is_refused(db_client: TestClient) -> None:
    page = db_client.get("/review/intakes/new", headers=WORKER)
    assert page.status_code == 403

    response = db_client.post(
        "/review/intakes",
        data={"payload": json.dumps(intake_payload()), "confirm": "yes"},
        headers=WORKER,
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_a_submission_without_csrf_is_refused(db_client: TestClient) -> None:
    response = db_client.post(
        "/review/intakes",
        data={"payload": json.dumps(intake_payload()), "confirm": "yes"},
        headers=HUMAN,
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_rejected"


def test_an_unconfirmed_submission_is_refused(db_client: TestClient) -> None:
    status, _ = _submit(db_client, intake_payload(), confirm="")
    assert status == 403


def test_malformed_json_is_a_named_error_not_a_500(db_client: TestClient) -> None:
    """`json.JSONDecodeError` has no exception handler -- unwrapped it is a bare HTTP 500."""
    status, _ = _submit(db_client, "{not json at all")

    assert status == 409


def test_a_json_array_is_a_named_error_not_a_500(db_client: TestClient) -> None:
    """Valid JSON, wrong shape: `model_validate` on a list raises before any field is read."""
    status, _ = _submit(db_client, [1, 2, 3])
    assert status == 409


def test_a_payload_missing_required_fields_is_a_named_error(db_client: TestClient) -> None:
    """Pydantic's `ValidationError` also has no handler; it must be translated, not escape."""
    status, _ = _submit(db_client, {"package_id": "pkg-only"})

    assert status == 409


def test_the_form_does_not_relax_the_cli_verification_requirement(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The whole point of ADR-0006's "a new way in, not a new set of rules"."""
    status, _ = _submit(db_client, intake_payload(verification_mode="trust_me_i_am_a_browser"))

    assert status == 409
    with Session(migrated_engine) as session:
        assert session.scalars(select(WorkPackageRevision)).all() == []


def test_the_form_does_not_relax_the_approved_status_requirement(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    status, _ = _submit(db_client, intake_payload(status_at_intake="draft"))

    assert status == 409
    with Session(migrated_engine) as session:
        assert session.scalars(select(WorkPackageRevision)).all() == []


def test_a_payload_missing_expected_version_is_refused_exactly_as_the_api_refuses_it(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The form must not default a field the API route requires.

    `emit-intake-payload` always emits `expected_version: 0`, so defaulting it here would be dead
    code that nonetheless made the browser path laxer than the machine path -- the precise shape
    ADR-0006 rules out.
    """
    payload = intake_payload()
    del payload["expected_version"]

    status, _ = _submit(db_client, payload)

    assert status == 409
    with Session(migrated_engine) as session:
        assert session.scalars(select(WorkPackageRevision)).all() == []


def test_the_forms_idempotency_key_wins_over_the_pasted_payload(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The CSRF token is bound to the form's key. If the pasted text could override it, one signed
    token would register an unbounded number of distinct intakes."""
    status, fields = _submit(db_client, intake_payload(idempotency_key="attacker-chosen-key"))

    assert status == 303
    with Session(migrated_engine) as session:
        keys = set(session.scalars(select(Event.idempotency_key)))
        assert fields["idempotency_key"] in keys
        assert "attacker-chosen-key" not in keys
