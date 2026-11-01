"""The workflow contract: what dispatch sends must be what a caller workflow accepts.

The authority envelope has a cross-repo contract test. The *workflow* did not, and two
independent defects shipped under a green suite because every dispatch test substitutes a
fake dispatcher: the workflow id was sent as a path (GitHub 404s — it wants the file name
or the numeric id) and an undeclared `orchestrator_url` input was sent (GitHub 422s with
`Unexpected inputs provided`). Neither is observable without either GitHub or this file.

THE SPECIMEN IS A FIXTURE, and it used to be this repository's own caller workflow.
ADR-0015's amendment of 2026-09-11 declared this repository `factory_target = false` and
deleted that file, so the contract lost the copy it read — while remaining live for the
five repositories that do host a caller and that dispatch actually fires. The specimen is
therefore the conformance kit's own template, copied into `tests/fixtures/`, which is what
`portfolio onboard` writes into a target repository; see that file's header for what the
copy does and does not pin.

The assertion this file DROPPED in the same change was `the caller workflow exists at the
path dispatch assumes`, which asserted exactly the state being removed. Its content is not
lost: `dispatch_workflow_id` is still held to the specimen's file name below, and whether a
given target repository hosts a caller at that path is `runner.caller`'s question, per
repository, which is where it belongs.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import yaml

from orchestrator.config import Settings
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.execution.dispatch import GitHubActionsDispatcher, _payload
from pin_watcher.github import CALLER_PATH

CALLER_WORKFLOW = Path("tests/fixtures/factory_runner_caller.yml")


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
    from orchestrator.services.execution.dispatch import DispatchSettings

    return DispatchSettings(
        enabled=True,
        workflow_id=Settings.model_fields["dispatch_workflow_id"].default,
        workflow_ref="main",
        github_app_configured=True,
    )


def test_dispatch_sends_exactly_the_inputs_the_caller_workflow_declares() -> None:
    sent = set(
        _payload(_unit(), _dispatch_settings(), "AlobarQuest/intent-packages")["workflow_dispatch"][
            "inputs"
        ]
    )

    assert sent == _declared_inputs()


def test_the_default_workflow_id_is_a_file_name_not_a_path() -> None:
    """`POST /actions/workflows/{workflow_id}/dispatches` takes a file name or numeric id.

    A path puts extra segments in the URL and GitHub answers 404 — indistinguishable from
    a missing workflow, and it opens the failure-signature circuit breaker after 3 tries.

    Held against the path a target repository's caller is written under, not against the
    specimen file's own name: the specimen lives in `tests/fixtures/` and is named for what
    it is, while the thing dispatch addresses is the workflow in the target repository. That
    path is taken from `pin_watcher`, which reads it in every OTHER repository, so the two
    readers of one value cannot drift apart silently — which is the property the assertion
    against this repository's own caller used to supply, before ADR-0015's amendment
    removed it.
    """
    default = Settings.model_fields["dispatch_workflow_id"].default

    assert "/" not in default
    assert default == Path(CALLER_PATH).name


def test_the_dispatch_request_is_unchanged() -> None:
    """What GitHub receives, whole: moving the transport moved none of it but the User-Agent."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(204)

    GitHubActionsDispatcher(
        lambda: "ghs_minted", transport=httpx.MockTransport(handler)
    ).dispatch_workflow(
        repository="AlobarQuest/brain",
        workflow_id="dir/caller.yml",
        ref="main",
        inputs={"work_unit_id": "u1"},
    )

    [request] = sent
    assert request.method == "POST"
    assert str(request.url) == (
        "https://api.github.com/repos/AlobarQuest/brain/actions/workflows/"
        "dir%2Fcaller.yml/dispatches"
    )
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["Authorization"] == "Bearer ghs_minted"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert request.headers["User-Agent"] == "orchestrator-dispatch/1 (+AlobarQuest/orchestrator)"
    assert json.loads(request.content) == {"ref": "main", "inputs": {"work_unit_id": "u1"}}
    assert request.extensions["timeout"] == {
        "connect": 10.0,
        "read": 10.0,
        "write": 10.0,
        "pool": 10.0,
    }
