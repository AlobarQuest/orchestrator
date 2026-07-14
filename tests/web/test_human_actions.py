import re
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig
from orchestrator.identity.registry import RegistryAdapter
from orchestrator.kernel.authority import authority_fingerprint, normalize_authority
from orchestrator.kernel.states import WorkUnitState
from orchestrator.main import create_app
from orchestrator.persistence.models import Approval, Event, WorkUnit
from tests.api.test_lifecycle_api import HUMAN, WORKER

_ALLOWED_COMMANDS = (
    "uv add --dev &#39;httpx2&gt;=2.6.0&#39;",
    "uv sync --locked",
    "uv run make check",
)
_MUTATION_COMMANDS = (_ALLOWED_COMMANDS[0],)


def _assert_command_list(page: str, key: str, commands: tuple[str, ...]) -> None:
    match = re.search(rf"{key}:\s*<ol>(.*?)</ol>", page, re.DOTALL)
    assert match is not None
    assert tuple(re.findall(r"<code>(.*?)</code>", match.group(1))) == commands


def test_detail_has_human_actions_but_no_worker_or_creation_controls(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    assert "Review outcome" in page.text
    assert "Cancel work unit" in page.text
    assert "Authorize retry" in page.text
    assert "Claim work" not in page.text
    assert "Create work unit" not in page.text
    assert "<label" in page.text


def test_review_form_uses_post_redirect_get(db_client: TestClient, review_unit: WorkUnit) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    form = re.search(
        rf'action="/review/units/{review_unit.id}/review">(.*?)</form>',
        page.text,
    )
    assert form is not None
    token = re.search(r'name="csrf_token" value="([^"]+)"', form.group(1))
    idempotency = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(1))
    assert token is not None and idempotency is not None

    response = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=HUMAN,
        data={
            "csrf_token": token.group(1),
            "idempotency_key": idempotency.group(1),
            "expected_version": str(review_unit.version),
            "outcome": "revision_required",
            "reason": "Needs another pass",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/review/units/{review_unit.id}"

    replay = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=HUMAN,
        data={
            "csrf_token": token.group(1),
            "idempotency_key": idempotency.group(1),
            "expected_version": str(review_unit.version),
            "outcome": "revision_required",
            "reason": "Needs another pass",
            "confirm": "yes",
        },
        follow_redirects=False,
    )
    assert replay.status_code == 303
    detail = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    pack = db_client.get(f"/review/units/{review_unit.id}/evidence-pack", headers=HUMAN)
    assert "Needs another pass" in detail.text
    assert "Needs another pass" in pack.text


def _form(page: str, unit_id: object, action: str) -> tuple[str, str]:
    form = re.search(rf'action="/review/units/{unit_id}/{action}">(.*?)</form>', page)
    assert form is not None
    token = re.search(r'name="csrf_token" value="([^"]+)"', form.group(1))
    key = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(1))
    assert token is not None and key is not None
    return token.group(1), key.group(1)


def _dependency_update_authority() -> dict[str, object]:
    return {
        "capabilities": {
            "repository_write": "allowed",
            "repo.edit": "allowed",
            "command.run": "allowed",
        },
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "change_class": "dependency-update",
        "constraints": {
            "target_repository": "AlobarQuest/change-manager",
            "allowed_commands": [
                "uv add --dev 'httpx2>=2.6.0'",
                "uv sync --locked",
                "uv run make check",
            ],
            "mutation_commands": ["uv add --dev 'httpx2>=2.6.0'"],
        },
        "conformance": {
            "status": "green",
            "standards_touched": ["project-standards"],
            "accepted_standards": ["security-standards"],
        },
        "future_authority_marker": "fingerprinted by name",
    }


def test_authority_approval_records_the_unit_fingerprint(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        authority = _dependency_update_authority()
        unit.authority = authority
        unit.authority_fingerprint = authority_fingerprint(normalize_authority(authority))
        session.commit()
        session.refresh(unit)
        expected_version = unit.version
        expected_fingerprint = unit.authority_fingerprint

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    for value in (
        "repo.edit: allowed",
        "repository_write: allowed",
        "command.run: allowed",
        "max_attempts: 3",
        "max_llm_calls: 4",
        "dependency-update",
        "status: green",
        "standards_touched: [&#39;project-standards&#39;]",
        "accepted_standards: [&#39;security-standards&#39;]",
        "target_repository:",
        "AlobarQuest/change-manager",
        "future_authority_marker",
        expected_fingerprint,
    ):
        assert value in page.text
    assert "uv add --dev 'httpx2>=2.6.0'" not in page.text
    _assert_command_list(page.text, "allowed_commands", _ALLOWED_COMMANDS)
    _assert_command_list(page.text, "mutation_commands", _MUTATION_COMMANDS)
    token, key = _form(page.text, review_unit.id, "authority-approval")
    response = db_client.post(
        f"/review/units/{review_unit.id}/authority-approval",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(expected_version),
            "reason": "Approved exact authority envelope.",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        approval = session.scalar(select(Approval).where(Approval.idempotency_key == key))
        assert unit is not None and approval is not None
        assert approval.subject_type == "authority"
        assert approval.subject_revision_or_fingerprint == expected_fingerprint
        assert approval.approved_by == "devon"
        assert approval.reason == "Approved exact authority envelope."
        assert unit.authority_approval_id == approval.id


def test_invalid_legacy_authority_hides_and_rejects_authority_approval(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    invalid = _dependency_update_authority()
    invalid["constraints"] = {"allowed_commands": ["uv sync --locked"]}
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.authority = invalid
        session.commit()
        session.refresh(unit)
        expected_version = unit.version

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert "Approve this authority envelope" not in page.text
    assert "authority_mutation_commands_invalid" in page.text
    response = db_client.post(
        f"/review/units/{review_unit.id}/authority-approval",
        headers=HUMAN,
        data={
            "csrf_token": "unused",
            "idempotency_key": "invalid-authority-post",
            "expected_version": str(expected_version),
            "reason": "cannot approve invalid authority",
            "confirm": "yes",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "authority_mutation_commands_invalid"
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Approval)) == 1
        assert session.scalar(select(func.count()).select_from(Event)) == 0


def test_unit_page_renders_empty_unknown_fields(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert "Unknown fields</dt><dd>None" in page.text


def test_inactive_human_and_worker_posts_are_denied(
    auth_config: AuthConfig,
    db_client: TestClient,
    review_unit: WorkUnit,
) -> None:
    inactive_registry = RegistryAdapter(
        {
            "schema": "orchestrator-actor-bundle/v1",
            "source_revision": "0123456789abcdef0123456789abcdef01234567",
            "actors": [
                {
                    "agent_id": "devon",
                    "version": 1,
                    "status": "retired",
                    "runtime": "human",
                    "authority_profile": "human-operator-v1",
                }
            ],
        }
    )
    inactive = TestClient(create_app(replace(auth_config, registry=inactive_registry)))
    assert inactive.get("/review", headers=HUMAN).status_code == 401
    assert (
        inactive.post(
            f"/review/units/{review_unit.id}/cancel",
            headers=HUMAN,
            data={"reason": "denied", "expected_version": str(review_unit.version)},
        ).status_code
        == 401
    )

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form(page.text, review_unit.id, "cancel")
    denied = db_client.post(
        f"/review/units/{review_unit.id}/cancel",
        headers=WORKER,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(review_unit.version),
            "reason": "worker denied",
            "confirm": "yes",
        },
    )
    assert denied.status_code == 403


def test_stale_version_form_returns_conflict(db_client: TestClient, review_unit: WorkUnit) -> None:
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
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"
    assert response.json()["error"]["current_version"] == review_unit.version


def test_approval_replay_converges_to_one_row_and_event(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = WorkUnitState.AWAITING_APPROVAL
        session.commit()
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form(page.text, review_unit.id, "approval")
    data = {
        "csrf_token": token,
        "idempotency_key": key,
        "expected_version": str(review_unit.version),
        "reason": "exact replay",
        "confirm": "yes",
    }
    first = db_client.post(
        f"/review/units/{review_unit.id}/approval",
        headers=HUMAN,
        data=data,
        follow_redirects=False,
    )
    replay = db_client.post(
        f"/review/units/{review_unit.id}/approval",
        headers=HUMAN,
        data=data,
        follow_redirects=False,
    )
    assert first.status_code == replay.status_code == 303
    with Session(migrated_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Approval).where(Approval.idempotency_key == key)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.idempotency_key == key)
            )
            == 1
        )


def test_cancel_effect_persists_reason_and_renders(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = WorkUnitState.FAILED
        session.commit()
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form(page.text, review_unit.id, "cancel")
    response = db_client.post(
        f"/review/units/{review_unit.id}/cancel",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(review_unit.version),
            "reason": "Portfolio priority changed",
            "confirm": "yes",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        event = session.scalar(select(Event).where(Event.idempotency_key == key))
        assert unit is not None and unit.state == WorkUnitState.CANCELLED
        assert event is not None and event.payload["reason"] == "Portfolio priority changed"
    detail = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    pack = db_client.get(f"/review/units/{review_unit.id}/evidence-pack", headers=HUMAN)
    assert "Portfolio priority changed" in detail.text
    assert "Portfolio priority changed" in pack.text


def test_retry_form_applies_canonical_budget_and_ready_effect(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = WorkUnitState.FAILED
        unit.attempt_count = unit.max_attempts
        old_max = unit.max_attempts
        session.commit()
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form(page.text, review_unit.id, "retry")
    response = db_client.post(
        f"/review/units/{review_unit.id}/retry",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(review_unit.version),
            "new_max_attempts": str(old_max + 1),
            "reason": "One bounded retry",
            "confirm": "yes",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        assert unit.state == WorkUnitState.READY
        assert unit.max_attempts == old_max + 1
