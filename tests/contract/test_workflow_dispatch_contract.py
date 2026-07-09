"""The workflow contract: what dispatch sends must be what the caller workflow accepts.

The authority envelope has a cross-repo contract test. The *workflow* did not, and two
independent defects shipped under a green suite because every dispatch test substitutes a
fake dispatcher: the workflow id was sent as a path (GitHub 404s — it wants the file name
or the numeric id) and an undeclared `orchestrator_url` input was sent (GitHub 422s with
`Unexpected inputs provided`). Neither is observable without either GitHub or this file.
"""

import uuid
from pathlib import Path
from typing import Any

import yaml

from orchestrator.config import Settings
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.dispatch import _payload

CALLER_WORKFLOW = Path(".github/workflows/factory-runner-pilot.yml")


def _declared_inputs() -> set[str]:
    document = yaml.safe_load(CALLER_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the bare key `on` as the boolean True.
    triggers = document.get("on", document.get(True))
    return set(triggers["workflow_dispatch"].get("inputs") or {})


def _unit() -> WorkUnit:
    """`_payload` reads only these two ids; no session or row is needed."""
    return WorkUnit(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        work_package_revision_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
    )


def _dispatch_settings() -> Any:
    from orchestrator.services.dispatch import DispatchSettings

    return DispatchSettings(
        enabled=True,
        allowed_change_classes=frozenset({"dependency-update"}),
        enabled_capabilities=frozenset({"repo.edit"}),
        allowed_target_repositories=frozenset({"AlobarQuest/orchestrator"}),
        workflow_id=Settings.model_fields["dispatch_workflow_id"].default,
        workflow_ref="main",
        github_app_configured=True,
    )


def test_dispatch_sends_exactly_the_inputs_the_caller_workflow_declares() -> None:
    sent = set(
        _payload(_unit(), _dispatch_settings(), "AlobarQuest/orchestrator")["workflow_dispatch"][
            "inputs"
        ]
    )

    assert sent == _declared_inputs()


def test_the_default_workflow_id_is_a_file_name_not_a_path() -> None:
    """`POST /actions/workflows/{workflow_id}/dispatches` takes a file name or numeric id.

    A path puts extra segments in the URL and GitHub answers 404 — indistinguishable from
    a missing workflow, and it opens the failure-signature circuit breaker after 3 tries.
    """
    default = Settings.model_fields["dispatch_workflow_id"].default

    assert "/" not in default
    assert default == CALLER_WORKFLOW.name


def test_the_caller_workflow_exists_at_the_path_dispatch_assumes() -> None:
    assert CALLER_WORKFLOW.is_file()
