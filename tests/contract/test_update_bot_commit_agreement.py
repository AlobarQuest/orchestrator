"""The hold term and the gate's metadata read pick the same commit, by the same rule.

The ledger (`landing_ledger.github._gate_message`) mirrors what `dependabot/fetch-metadata` reads;
the orchestrator's hold term (`terms._update_bot_message`) must read the same commit or a hold could
be judged against a list the gate never saw. Each case goes through both.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from landing_ledger.github import GitHubReader, _gate_message
from orchestrator.services.landing.interfaces import EstateReadGateway, PullRequestCommit
from orchestrator.services.landing.terms import _update_bot_message
from tests.services.estate_landing_doubles import pull_request

BOT = "dependabot[bot]"
BLOCK = "Bump x\n\nupdated-dependencies:\n- dependency-name: x\n"


def row(
    author: str | None = BOT,
    *,
    verified: object = True,
    message: str | None = BLOCK,
) -> dict[str, Any]:
    """One `GET /pulls/{n}/commits` row, as the platform sends it."""
    return {
        "sha": "a" * 40,
        "author": None if author is None else {"login": author},
        "committer": {"login": "web-flow"},
        "commit": {"message": message, "verification": {"verified": verified}},
    }


CASES = {
    "one_verified_bot_commit": ([row()], BLOCK),
    "unverified": ([row(verified=False)], None),
    "verified_as_the_string_true": ([row(verified="true")], None),
    "verified_as_one": ([row(verified=1)], None),
    "person_first_then_bot": ([row("someone"), row()], None),
    "two_bot_commits": ([row(message="first"), row(message="second")], "first"),
    "bot_under_merge_commit": ([row("alobar-sds-dispatch[bot]"), row()], None),
    "empty_list": ([], None),
    "bot_commit_without_message": ([row(message=None)], None),
    "bot_commit_with_empty_message": ([row(message="")], None),
    "unlinked_author": ([row(None)], None),
}


class FakeReader:
    """Serves `GET /pulls/{n}/commits` oldest first and honours `per_page`, as the platform does."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def get(self, path: str, **params: Any) -> Any:
        assert path.endswith("/pulls/7/commits")
        return self._rows[: params.get("per_page", 30)]


class FakeGateway:
    """Converts the same rows as the real gateway: the linked login, and `verified` only when the
    platform says exactly `true` (`estate_pr_merge.py::pull_request_commits`)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._commits = tuple(
            PullRequestCommit(
                sha=r["sha"],
                author_login=(r["author"] or {}).get("login"),
                committer_login=(r["committer"] or {}).get("login"),
                verified=r["commit"]["verification"]["verified"] is True,
                message=r["commit"]["message"],
            )
            for r in rows
        )

    def pull_request_commits(
        self, *, repository: str, number: int
    ) -> tuple[PullRequestCommit, ...]:
        return self._commits


@pytest.mark.parametrize(("rows", "expected"), CASES.values(), ids=CASES.keys())
def test_both_rules_pick_the_same_message(rows: list[dict[str, Any]], expected: str | None) -> None:
    gate = _gate_message(cast(GitHubReader, FakeReader(rows)), "AlobarQuest/x", 7)
    term = _update_bot_message(
        "AlobarQuest/x",
        pull_request(number=7),
        cast(EstateReadGateway, FakeGateway(rows)),
    )
    assert (gate or None) == term
    assert term == expected
