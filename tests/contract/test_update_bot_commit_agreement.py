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


def commit(
    author: str | None = BOT,
    *,
    verified: bool = True,
    message: str | None = BLOCK,
    sha: str = "a" * 40,
) -> PullRequestCommit:
    return PullRequestCommit(
        sha=sha, author_login=author, committer_login="web-flow", verified=verified, message=message
    )


CASES = {
    "one_verified_bot_commit": ([commit()], BLOCK),
    "unverified": ([commit(verified=False)], None),
    "person_first_then_bot": ([commit("someone"), commit()], None),
    "two_bot_commits": ([commit(message="first"), commit(message="second")], "first"),
    "bot_under_merge_commit": ([commit("alobar-sds-dispatch[bot]"), commit()], None),
    "empty_list": ([], None),
    "bot_commit_without_message": ([commit(message=None)], None),
    "bot_commit_with_empty_message": ([commit(message="")], None),
    "unlinked_author": ([commit(None)], None),
}


class FakeReader:
    """Serves `GET /pulls/{n}/commits` oldest first and honours `per_page`, as the platform does."""

    def __init__(self, commits: list[PullRequestCommit]) -> None:
        self._commits = commits

    def get(self, path: str, **params: Any) -> Any:
        assert path.endswith("/pulls/7/commits")
        rows = [
            {
                "sha": c.sha,
                "author": None if c.author_login is None else {"login": c.author_login},
                "commit": {
                    "message": c.message,
                    "verification": {"verified": c.verified},
                },
            }
            for c in self._commits
        ]
        return rows[: params.get("per_page", 30)]


class FakeGateway:
    def __init__(self, commits: list[PullRequestCommit]) -> None:
        self._commits = tuple(commits)

    def pull_request_commits(
        self, *, repository: str, number: int
    ) -> tuple[PullRequestCommit, ...]:
        return self._commits


@pytest.mark.parametrize(("commits", "expected"), CASES.values(), ids=CASES.keys())
def test_both_rules_pick_the_same_message(
    commits: list[PullRequestCommit], expected: str | None
) -> None:
    gate = _gate_message(cast(GitHubReader, FakeReader(commits)), "AlobarQuest/x", 7)
    term = _update_bot_message(
        "AlobarQuest/x",
        pull_request(number=7),
        cast(EstateReadGateway, FakeGateway(commits)),
    )
    assert (gate or None) == term
    assert term == expected
