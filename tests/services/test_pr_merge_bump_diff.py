"""The policy path asks the pull request itself; a bound approval never does (ADR-0025 amendment 4).

A green head is all the policy path had: the agent that wrote the bump can reach the checks. So
each term here refuses with no record and no landing, and a person's approval changes none of it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import Event, UnitPrMerge, WorkUnit
from orchestrator.services.landing import pr_merge as pr_merge_module
from orchestrator.services.landing import pr_merge_admission as admission_module
from orchestrator.services.landing.pr_merge import (
    MANIFESTS_BY_ECOSYSTEM,
    PR_MERGE_FILE_LIST_TRUNCATED,
    PR_MERGE_FILE_LIST_UNREADABLE,
    PR_MERGE_GATE_FILE_CHANGED,
    PR_MERGE_HEAD_NOT_ADJUDICATED,
    PR_MERGE_HEAD_NOT_RUNNER_BRANCH,
    PR_MERGE_MANIFEST_UNCHANGED,
    PR_MERGE_UNIT_TRAILER_MISSING,
    ChangedFile,
    GitHubGatewayError,
    GitHubPullRequests,
    PullRequestState,
    _manifests,
    _state_from_body,
)
from orchestrator.services.membership.holds import ecosystem_of_package_id
from tests.services.test_pr_merge import FakeGateway, _the_runners
from tests.services.test_pr_merge_admission import HEAD, _approve_authority, _revision
from tests.services.test_pr_merge_bump_policy import REPOSITORY, _bump, _land

BUMP = (ChangedFile("pyproject.toml"), ChangedFile("uv.lock"))
BASE = PullRequestState(base_ref="main", default_branch="main", open=True, landed=False)


def _served(unit: WorkUnit, **changes: Any) -> PullRequestState:
    return replace(_the_runners(BASE, unit, REPOSITORY, HEAD), **changes)


def _refused(
    session: Session,
    unit: WorkUnit,
    code: str,
    state: PullRequestState | None = None,
    files: tuple[ChangedFile, ...] = BUMP,
) -> FakeGateway:
    gateway = FakeGateway(state=state or _served(unit), files=files)
    with pytest.raises(DomainError) as refused:
        _land(session, unit, gateway, as_served=True)
    assert refused.value.code == code
    assert gateway.merges == []
    assert session.scalars(select(UnitPrMerge)).all() == []
    return gateway


def _changed(*paths: str) -> tuple[ChangedFile, ...]:
    return (*BUMP, *(ChangedFile(path) for path in paths))


def test_a_clean_bump_lands(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "bump-diff")
    gateway = FakeGateway(state=_served(unit), files=BUMP)
    record = _land(migrated_session, unit, gateway, as_served=True)
    assert record.status == "merged"
    assert gateway.file_reads == [(REPOSITORY, 7)]


@pytest.mark.parametrize(
    "ref",
    ["feature", "sds/deadbeef-attempt-1", "main"],
)
def test_a_pull_request_from_another_branch_is_refused(migrated_session: Session, ref: str) -> None:
    unit = _bump(migrated_session, "bump-diff")
    state = _served(unit, head_ref=ref)
    gateway = _refused(migrated_session, unit, PR_MERGE_HEAD_NOT_RUNNER_BRANCH, state)
    assert gateway.file_reads == []


def test_the_branch_of_another_attempt_is_refused(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "bump-diff")
    good = _served(unit).head_ref
    other = f"{good.rsplit('-', 1)[0]}-{unit.attempt_count + 1}"
    _refused(
        migrated_session,
        unit,
        PR_MERGE_HEAD_NOT_RUNNER_BRANCH,
        _served(unit, head_ref=other),
    )


def test_the_right_branch_name_on_a_fork_is_refused(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "bump-diff")
    state = _served(unit, head_repository="someone/orchestrator")
    _refused(migrated_session, unit, PR_MERGE_HEAD_NOT_RUNNER_BRANCH, state)


def test_the_repository_is_compared_folded(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "bump-diff")
    gateway = FakeGateway(state=_served(unit, head_repository=REPOSITORY.lower()), files=BUMP)
    assert _land(migrated_session, unit, gateway, as_served=True).status == "merged"


def test_a_head_that_is_not_the_adjudicated_head_is_refused(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "bump-diff")
    state = _served(unit, head_sha="f" * 40)
    gateway = _refused(migrated_session, unit, PR_MERGE_HEAD_NOT_ADJUDICATED, state)
    assert gateway.file_reads == []


@pytest.mark.parametrize(
    "body",
    [
        "",
        "SDS-Unit: 00000000-0000-0000-0000-000000000000",
        "quoting `SDS-Unit: {unit}` in prose",
        "SDS-Unit: {unit}-and-more",
    ],
)
def test_a_body_without_the_units_trailer_is_refused(migrated_session: Session, body: str) -> None:
    unit = _bump(migrated_session, "bump-diff")
    state = _served(unit, body=body.format(unit=unit.id))
    gateway = _refused(migrated_session, unit, PR_MERGE_UNIT_TRAILER_MISSING, state)
    assert gateway.file_reads == []


def test_an_unreadable_file_list_is_refused(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "bump-diff")
    gateway = FakeGateway(state=_served(unit), files_error=GitHubGatewayError("files_status", 403))
    with pytest.raises(DomainError) as refused:
        _land(migrated_session, unit, gateway, as_served=True)
    assert refused.value.code == PR_MERGE_FILE_LIST_UNREADABLE
    assert gateway.merges == []
    assert migrated_session.scalars(select(UnitPrMerge)).all() == []


@pytest.mark.parametrize("count", [3, 3000, 3001, -1, 0, 1])
def test_a_list_that_disagrees_with_the_count_is_refused(
    migrated_session: Session, count: int
) -> None:
    unit = _bump(migrated_session, "bump-diff")
    state = _served(unit, changed_files=count)
    _refused(migrated_session, unit, PR_MERGE_FILE_LIST_TRUNCATED, state)


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/quality.yml",
        ".github/dependabot.yml",
        ".GitHub/workflows/quality.yml",
        "CODEOWNERS",
        "docs/CODEOWNERS",
        ".gitlab/codeowners",
        "Makefile",
        "makefile",
        "GNUmakefile",
    ],
)
def test_a_gate_file_is_refused(migrated_session: Session, path: str) -> None:
    unit = _bump(migrated_session, "bump-diff")
    state = _served(unit, changed_files=3)
    _refused(migrated_session, unit, PR_MERGE_GATE_FILE_CHANGED, state, _changed(path))


def test_moving_a_workflow_away_is_refused(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "bump-diff")
    moved = ChangedFile("old-quality.yml", ".github/workflows/quality.yml")
    state = _served(unit, changed_files=3)
    _refused(migrated_session, unit, PR_MERGE_GATE_FILE_CHANGED, state, (*BUMP, moved))


def test_moving_a_file_into_a_workflow_is_refused(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "bump-diff")
    moved = ChangedFile(".github/workflows/quality.yml", "old-quality.yml")
    state = _served(unit, changed_files=3)
    _refused(migrated_session, unit, PR_MERGE_GATE_FILE_CHANGED, state, (*BUMP, moved))


@pytest.mark.parametrize("path", ["src/Makefile", "notes/github.md", "a.github/x", "CODEOWNERS.md"])
def test_a_file_that_only_resembles_a_gate_lands(migrated_session: Session, path: str) -> None:
    unit = _bump(migrated_session, "bump-diff")
    gateway = FakeGateway(state=_served(unit, changed_files=3), files=_changed(path))
    assert _land(migrated_session, unit, gateway, as_served=True).status == "merged"


@pytest.mark.parametrize(
    "files",
    [
        (ChangedFile("uv.lock"), ChangedFile("src/app.py")),
        (ChangedFile("uv.lock"), ChangedFile("sub/pyproject.toml")),
        (ChangedFile("uv.lock"), ChangedFile("package.json")),
        (ChangedFile("uv.lock"), ChangedFile("pyproject.toml", removed=True)),
    ],
)
def test_a_manifest_that_did_not_move_is_refused(
    migrated_session: Session, files: tuple[ChangedFile, ...]
) -> None:
    unit = _bump(migrated_session, "bump-diff")
    _refused(migrated_session, unit, PR_MERGE_MANIFEST_UNCHANGED, files=files)


def test_each_tooling_names_the_generators_pin_sites() -> None:
    def named(package_id: str) -> frozenset[str]:
        ecosystem = ecosystem_of_package_id(package_id, REPOSITORY)
        assert ecosystem is not None
        return MANIFESTS_BY_ECOSYSTEM[ecosystem]

    assert named("orchestrator-uv-x") == {"pyproject.toml"}
    assert named("orchestrator-npm-x") == {"package.json"}
    assert named("orchestrator-pip-x") == {"requirements.txt", "requirements-dev.txt"}


def test_a_package_naming_no_tooling_has_no_manifest(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "bump-diff")
    revision = _revision(migrated_session, unit)
    assert _manifests(migrated_session, revision, "AlobarQuest/elsewhere") == frozenset()


def test_a_landed_pull_request_is_not_held_to_the_terms(migrated_session: Session) -> None:
    """A landing recorded late is a fact, and holding it would report a merged pull request."""
    unit = _bump(migrated_session, "bump-diff")
    gateway = FakeGateway(state=replace(BASE, open=False, landed=True))
    record = _land(migrated_session, unit, gateway, as_served=True)
    assert record.status == "already_merged"
    assert gateway.file_reads == []


def test_a_bound_approval_is_asked_none_of_it(migrated_session: Session) -> None:
    """The pull request a person approved may change a workflow, on any branch, with any body."""
    unit = _bump(migrated_session, "bump-diff")
    _approve_authority(migrated_session, unit)
    gateway = FakeGateway(
        state=replace(BASE, head_ref="feature", changed_files=1),
        files=(ChangedFile(".github/workflows/quality.yml"),),
    )
    record = _land(migrated_session, unit, gateway, as_served=True)
    assert record.status == "merged"
    assert gateway.file_reads == []


def test_a_refusal_writes_no_event(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "bump-diff")
    files = (ChangedFile("uv.lock"), ChangedFile("src/app.py"))
    _refused(migrated_session, unit, PR_MERGE_MANIFEST_UNCHANGED, files=files)
    actions = migrated_session.scalars(
        select(Event.action).where(Event.subject_id == unit.id)
    ).all()
    assert [action for action in actions if action.startswith("pr_merge.")] == []


def test_no_refusal_code_is_a_substring_of_another() -> None:
    new = {
        PR_MERGE_FILE_LIST_TRUNCATED,
        PR_MERGE_FILE_LIST_UNREADABLE,
        PR_MERGE_GATE_FILE_CHANGED,
        PR_MERGE_HEAD_NOT_ADJUDICATED,
        PR_MERGE_HEAD_NOT_RUNNER_BRANCH,
        PR_MERGE_MANIFEST_UNCHANGED,
        PR_MERGE_UNIT_TRAILER_MISSING,
    }
    every = {
        value
        for module in (pr_merge_module, admission_module)
        for name, value in vars(module).items()
        if name.isupper() and isinstance(value, str)
    }
    assert new <= every
    assert [(a, b) for a in new for b in every if a != b and (a in b or b in a)] == []


# ---------------------------------------------------------------------------------------------
# The real gateway, with the remote's answers stated.
# ---------------------------------------------------------------------------------------------


class _Answer:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is ValueError:
            raise ValueError("not JSON")
        return self._body


def _gateway(monkeypatch: pytest.MonkeyPatch, pages: list[_Answer]) -> list[int]:
    asked: list[int] = []

    def get(url: str, **kwargs: Any) -> _Answer:
        assert url.endswith(f"/repos/{REPOSITORY}/pulls/7/files")
        assert kwargs["params"]["per_page"] == 100
        asked.append(kwargs["params"]["page"])
        return pages[len(asked) - 1]

    monkeypatch.setattr(pr_merge_module.httpx, "get", get)
    return asked


def _read() -> tuple[ChangedFile, ...]:
    return GitHubPullRequests(lambda: "token").pull_request_files(repository=REPOSITORY, number=7)


def _page(size: int) -> _Answer:
    return _Answer(200, [{"filename": f"f{n}", "status": "modified"} for n in range(size)])


def test_the_list_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _gateway(monkeypatch, [_page(100), _page(100), _page(7)])
    assert len(_read()) == 207
    assert asked == [1, 2, 3]


def test_a_full_last_page_asks_once_more(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _gateway(monkeypatch, [_page(100), _page(0)])
    assert len(_read()) == 100
    assert asked == [1, 2]


def test_the_list_stops_at_what_the_remote_can_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _gateway(monkeypatch, [_page(100)] * 40)
    assert len(_read()) == 3100
    assert asked == list(range(1, 32))


def test_a_rename_and_a_removal_read_as_what_they_are(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        {"filename": "b", "previous_filename": "a", "status": "renamed"},
        {"filename": "c", "status": "removed"},
    ]
    _gateway(monkeypatch, [_Answer(200, entries)])
    assert _read() == (ChangedFile("b", "a"), ChangedFile("c", None, True))


@pytest.mark.parametrize(
    ("later", "code"),
    [
        (_Answer(403, []), "files_status"),
        (_Answer(200, ValueError), "files_response_invalid"),
        (_Answer(200, {"message": "no"}), "files_response_invalid"),
        (_Answer(200, ["a"]), "files_response_invalid"),
        (_Answer(200, [{"status": "modified"}]), "files_response_invalid"),
        (_Answer(200, [{"filename": ""}]), "files_response_invalid"),
        (_Answer(200, [{"filename": "a", "previous_filename": 3}]), "files_response_invalid"),
    ],
)
def test_a_bad_page_raises_rather_than_shortening_the_list(
    monkeypatch: pytest.MonkeyPatch, later: _Answer, code: str
) -> None:
    _gateway(monkeypatch, [_page(100), later])
    with pytest.raises(GitHubGatewayError) as raised:
        _read()
    assert raised.value.code == code


def test_the_pull_request_read_carries_the_head() -> None:
    state = _state_from_body(
        {
            "base": {"ref": "main", "repo": {"default_branch": "main"}},
            "head": {"ref": "sds/x", "sha": "abc", "repo": {"full_name": REPOSITORY}},
            "state": "open",
            "body": "text",
            "changed_files": 4,
        }
    )
    assert (state.head_ref, state.head_sha, state.head_repository) == ("sds/x", "abc", REPOSITORY)
    assert (state.body, state.changed_files) == ("text", 4)


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"head": None, "body": None, "changed_files": None},
        {"head": {"ref": 3, "sha": 3, "repo": None}, "changed_files": True},
    ],
)
def test_an_absent_or_mistyped_field_reads_as_nothing(extra: dict[str, Any]) -> None:
    body = {"base": {"ref": "main", "repo": {"default_branch": "main"}}, **extra}
    state = _state_from_body(body)
    assert (state.head_ref, state.head_sha, state.head_repository, state.body) == ("", "", "", "")
    assert state.changed_files == -1
