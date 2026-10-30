"""The SDS's own revert pull requests: filed as `dependency-revert`, never as a bump (Part 3b).

The Dispatch App opens the SDS's reverts. Before this, the producer filed any bot-authored pull
request as `dependency-update`, so a revert would have been approved under the bump's terms. Review
focus 5: a pull request the Dispatch App authored is NEVER filed as `dependency-update`, and is
filed as `dependency-revert` only from its revert branch, and only when the orchestrator recorded
opening exactly that revert.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from change_proposer import cli as cli_module
from change_proposer.cli import (
    BOT_CHANGE_CLASS,
    FINDING_STATUSES,
    REVERT_CHANGE_CLASS,
    _consider,
    _pass,
)
from change_proposer.criteria import rollback_for
from change_proposer.reverts import (
    DISPATCH_APP_LOGIN,
    OPENED_REVERTS_PATH,
    REVERT_BRANCH_PREFIX,
    RecordedRevert,
    RevertsUnreadable,
    _permits,
    read_recorded_reverts,
)
from deploy_watcher.github import GitHubReader
from orchestrator.services.landing import revert_build
from tests.change_proposer.test_change_proposer import (
    _any_transcribed_revision,
    _Ctx,
    _member,
    _Pulls,
    _Reader,
)

CM = "alobarquest/change-manager"
BRAIN = "alobarquest/brain"
BUMP = 70
REVERT = 71
RECORDED = frozenset({RecordedRevert(CM, BUMP, REVERT)})


def _transcribed() -> str:
    return _any_transcribed_revision()


def _revert_pull(**overrides: object) -> dict[str, Any]:
    pull = {
        "number": REVERT,
        "title": "Revert bump x (SDS)",
        "author": DISPATCH_APP_LOGIN,
        "is_bot": True,
        "draft": False,
        "base_ref": "main",
        "head_ref": f"{REVERT_BRANCH_PREFIX}pr-{BUMP}",
    }
    return {**pull, **overrides}


def _consider_revert(pull: dict[str, Any], reverts=RECORDED, repository: str = CM):
    return _consider(_Reader(revision=_transcribed()), repository, pull, reverts)


# --- the spellings are the other side's -----------------------------------------------------------


def test_the_branch_prefix_is_the_one_the_orchestrator_opens_reverts_on() -> None:
    assert REVERT_BRANCH_PREFIX == revert_build.REVERT_BRANCH_PREFIX


def test_the_class_and_the_author_are_the_ones_deploy_policy_14_names() -> None:
    """change-manager's `DEPENDENCY_REVERT` and `DISPATCH_APP`, as served in `revert_landing`."""
    assert REVERT_CHANGE_CLASS == "dependency-revert"
    assert DISPATCH_APP_LOGIN == "alobar-sds-dispatch[bot]"


# --- review focus 5 ------------------------------------------------------------------------------


def test_a_recorded_revert_is_filed_as_a_revert_with_the_reverts_remedy() -> None:
    proposal, why = _consider_revert(_revert_pull())

    assert why == "eligible" and proposal is not None
    assert proposal["change_class"] == REVERT_CHANGE_CLASS
    plan = rollback_for(CM, REVERT_CHANGE_CLASS)
    assert proposal["rollback_plan"] == {"steps": list(plan.steps), "target": plan.target}
    assert f"#{BUMP}" in proposal["reasoning"]
    assert proposal["pull_request_number"] == REVERT


def test_the_revert_carries_its_repositorys_criteria_and_the_same_fields_as_any_record() -> None:
    bump, _ = _consider(
        _Reader(revision=_transcribed()),
        CM,
        _revert_pull(author="dependabot[bot]", head_ref="dependabot/uv/x-1.2"),
    )
    revert, _ = _consider_revert(_revert_pull())

    assert bump is not None and revert is not None
    assert revert["acceptance_criteria"] == bump["acceptance_criteria"]
    assert set(revert) == set(bump)


def test_the_repository_is_matched_as_github_spells_it() -> None:
    proposal, _ = _consider_revert(_revert_pull(), repository="AlobarQuest/change-manager")
    assert proposal is not None and proposal["change_class"] == REVERT_CHANGE_CLASS


@pytest.mark.parametrize(
    "overrides",
    [
        {"head_ref": "dependabot/uv/x-1.2"},
        {"head_ref": "main"},
        {"head_ref": None},
    ],
    ids=["dependabot-branch", "main", "no-branch"],
)
def test_a_dispatch_app_pull_request_off_a_revert_branch_is_not_filed(overrides) -> None:
    """A skip, not a finding: the App opens pull requests that are not deploying merges this lane
    files, and no record could ever name one off a revert branch."""
    reader = _Pulls({CM: [_revert_pull(**overrides)]}, _transcribed())

    outcomes = _pass(reader, [CM], None, RECORDED)

    assert [o.status for o in outcomes] == ["skipped"]


def test_a_recorded_revert_number_not_typed_a_bot_is_not_filed() -> None:
    """The login alone does not make the SDS's App: GitHub types the App's account `Bot`."""
    proposal, _ = _consider_revert(_revert_pull(is_bot=False))

    assert proposal is None


@pytest.mark.parametrize(
    "reverts",
    [
        frozenset(),
        frozenset({RecordedRevert(CM, BUMP, REVERT + 1)}),
        frozenset({RecordedRevert(BRAIN, BUMP, REVERT)}),
        frozenset({RecordedRevert(CM, BUMP + 1, REVERT)}),
    ],
    ids=["none-recorded", "other-revert-number", "other-repository", "other-bump-branch"],
)
def test_a_revert_branch_no_record_names_is_not_filed_and_is_a_finding(reverts) -> None:
    """The last case is the branch disagreeing with the record: the record says this pull request
    reverts #71, and its branch says it reverts #70."""
    reader = _Pulls({CM: [_revert_pull()]}, _transcribed())

    outcomes = _pass(reader, [CM], None, reverts)

    assert [o.status for o in outcomes] == ["unrecorded"]
    assert "unrecorded" in FINDING_STATUSES


def test_a_dispatch_app_pull_request_is_never_filed_when_the_records_are_unreadable() -> None:
    reader = _Pulls({CM: [_revert_pull()]}, _transcribed())

    outcomes = _pass(reader, [CM], None, None)

    assert [o.status for o in outcomes] == ["unreadable"]


@pytest.mark.parametrize(
    "pull",
    [
        _revert_pull(),
        _revert_pull(head_ref="dependabot/uv/x-1.2"),
        _revert_pull(head_ref=f"{REVERT_BRANCH_PREFIX}pr-{BUMP + 1}"),
        _revert_pull(is_bot=False),
        _revert_pull(title="SDS 3f2a: Bump x from 1 to 2"),
    ],
    ids=["recorded", "dependabot-branch", "wrong-revert-branch", "not-typed-bot", "factory-title"],
)
@pytest.mark.parametrize("reverts", [RECORDED, frozenset(), None], ids=["rec", "none", "unread"])
def test_the_dispatch_app_is_never_filed_as_a_bump(pull, reverts) -> None:
    """THE property, over every shape and every answer about the records."""
    proposal, _ = _consider_revert(pull, reverts)

    assert proposal is None or proposal["change_class"] == REVERT_CHANGE_CLASS
    assert json.dumps(proposal or {}).count(BOT_CHANGE_CLASS) == 0


def test_the_update_bot_is_still_filed_as_a_bump() -> None:
    """The control: the Dispatch App rule did not take Dependabot with it, even on a branch that
    looks like a revert."""
    proposal, _ = _consider_revert(
        _revert_pull(author="dependabot[bot]", head_ref="dependabot/uv/x-1.2")
    )

    assert proposal is not None and proposal["change_class"] == BOT_CHANGE_CLASS


@pytest.mark.parametrize(
    "author",
    ["dependabot[bot]", "Alobar-SDS-Dispatch[bot]", "alobar-sds-dispatch-renamed[bot]", "someone"],
    ids=["update-bot", "other-casing", "renamed", "person"],
)
@pytest.mark.parametrize("is_bot", [True, False], ids=["bot", "user"])
def test_a_revert_branch_is_never_filed_as_a_bump_whoever_opened_it(author, is_bot) -> None:
    """Whatever the author: a login rename or a casing difference must not route a revert branch
    into the bump class, which policy would approve under the bump's terms. Only the SDS's App
    files from one, and anyone else opening one is a finding."""
    reader = _Pulls({CM: [_revert_pull(author=author, is_bot=is_bot)]}, _transcribed())

    outcomes = _pass(reader, [CM], None, RECORDED)

    assert [o.status for o in outcomes] == ["unrecorded"]
    assert "unrecorded" in FINDING_STATUSES


def test_a_revert_against_another_base_is_skipped() -> None:
    proposal, why = _consider_revert(_revert_pull(base_ref="preview"))
    assert proposal is None and "preview" in why


# --- the read -------------------------------------------------------------------------------------


def _answering(status: int, body: object):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler), seen


def test_the_read_asks_the_one_route_as_the_observer() -> None:
    transport, seen = _answering(
        200, {"reverts": [{"repository": CM, "pr_number": BUMP, "revert_pr_number": REVERT}]}
    )

    assert read_recorded_reverts(token="t", transport=transport) == RECORDED
    assert [(r.method, r.url.path) for r in seen] == [("GET", OPENED_REVERTS_PATH)]
    assert seen[0].headers["X-Credential-Key-Id"] == "orchestrator-observer"
    assert seen[0].headers["User-Agent"].startswith("change-proposer/")


def test_the_repository_is_folded_on_arrival() -> None:
    transport, _ = _answering(
        200,
        {"reverts": [{"repository": "AlobarQuest/X", "pr_number": 1, "revert_pr_number": 2}]},
    )
    assert read_recorded_reverts(token="t", transport=transport) == {
        RecordedRevert("alobarquest/x", 1, 2)
    }


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, {}),
        (302, {}),
        (200, []),
        (200, {"reverts": "x"}),
        (200, {"reverts": [{"repository": CM, "pr_number": "70", "revert_pr_number": 71}]}),
        (200, {"reverts": [{"repository": CM, "pr_number": True, "revert_pr_number": 71}]}),
        (200, {"reverts": [{"pr_number": 70, "revert_pr_number": 71}]}),
    ],
    ids=["5xx", "redirect", "list", "not-a-list", "string-number", "bool-number", "no-repo"],
)
def test_anything_but_the_listing_is_unreadable_never_an_empty_one(status, body) -> None:
    transport, _ = _answering(status, body)
    with pytest.raises(RevertsUnreadable):
        read_recorded_reverts(token="t", transport=transport)


def test_an_unreachable_orchestrator_is_unreadable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(RevertsUnreadable):
        read_recorded_reverts(token="t", transport=httpx.MockTransport(fail))


def test_the_client_permits_one_get_and_nothing_else() -> None:
    assert _permits("GET", OPENED_REVERTS_PATH)
    for method, path in [
        ("POST", OPENED_REVERTS_PATH),
        ("GET", OPENED_REVERTS_PATH + "/"),
        ("GET", "/api/v1/landing-watch"),
        ("GET", "/api/v1/members"),
        ("POST", "/api/v1/observations"),
    ]:
        assert not _permits(method, path), (method, path)


# --- the pass and the run -----------------------------------------------------------------------


def test_the_pull_request_projection_carries_the_head_branch() -> None:
    body = [
        {
            "number": REVERT,
            "title": "t",
            "user": {"login": DISPATCH_APP_LOGIN, "type": "Bot"},
            "draft": False,
            "base": {"ref": "main"},
            "head": {"ref": f"{REVERT_BRANCH_PREFIX}pr-{BUMP}", "sha": "a" * 40},
        }
    ]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    reader = GitHubReader("gh", transport=transport)
    try:
        (pull,) = reader.open_pull_requests(CM)
    finally:
        reader.close()

    assert pull["head_ref"] == f"{REVERT_BRANCH_PREFIX}pr-{BUMP}"
    assert pull["author"] == DISPATCH_APP_LOGIN and pull["is_bot"] is True


def _run(monkeypatch, reader, reverts_answer) -> int:
    monkeypatch.setenv("CHANGE_PROPOSER_GITHUB_TOKEN", "gh")
    monkeypatch.setattr(
        cli_module, "read_members_from_environment", lambda **_: [_member(CM), _member(BRAIN)]
    )
    monkeypatch.setattr(cli_module, "GitHubReader", lambda token: _Ctx(reader))
    monkeypatch.setattr(
        cli_module, "read_recorded_reverts_from_environment", lambda **_: reverts_answer()
    )
    return cli_module.run(["--repository", CM])


def test_a_run_files_the_recorded_revert(monkeypatch, capsys) -> None:
    reader = _Pulls({CM: [_revert_pull()]}, _transcribed())

    assert _run(monkeypatch, reader, lambda: RECORDED) == cli_module.EXIT_OK
    assert f"{CM}#{REVERT}  would-propose" in capsys.readouterr().out


def test_an_unreadable_listing_is_a_finding_on_every_pass(monkeypatch, capsys) -> None:
    """Even with no Dispatch App pull request open: a read that broke must not wait for the one
    pass that needs it to be noticed."""

    def broken() -> frozenset[RecordedRevert]:
        raise RevertsUnreadable("the orchestrator answered 500")

    reader = _Pulls({CM: []}, _transcribed())

    assert _run(monkeypatch, reader, broken) == cli_module.EXIT_FINDINGS
    assert "500" in capsys.readouterr().out
