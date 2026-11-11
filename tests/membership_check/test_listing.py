"""The membership check's population: the members plus their accounts' public repositories.

ADR-0059 amendment 4, decision item 1, through the pin watcher's listing on a fake GitHub. The
account names below are fixtures; the code holds none.
"""

from __future__ import annotations

import pytest

from membership_check import cli
from membership_check.declaration import Declaration
from pin_watcher.github import GitHubReader, repositories
from tests.membership_check.test_consistency import CLEAN, Declarations, _invoke, _member, _wire
from tests.pin_watcher.conftest import Estate


class Recording(Declarations):
    """Declarations that remember which repositories were asked about."""

    def __init__(self, answers: dict[str, bool | None]) -> None:
        super().__init__(answers)
        self.asked: list[str] = []

    def declaration(self, slug: str) -> Declaration:
        self.asked.append(slug)
        return super().declaration(slug)


def _github(monkeypatch: pytest.MonkeyPatch, estate: Estate) -> None:
    """The real reader and listing, answered by `estate`."""
    monkeypatch.setattr(
        cli, "GitHubReader", lambda token: GitHubReader(token=token, transport=estate.transport())
    )
    monkeypatch.setattr(cli, "repositories", repositories)


def _declarations(monkeypatch: pytest.MonkeyPatch, estate: Estate, members: list[str]):
    _github(monkeypatch, estate)
    source = Recording({})
    monkeypatch.setattr(cli, "GitHubDeclarationSource", lambda token: source)
    scope, owned = cli._declarations("t", members)
    return scope, owned, source.asked


def test_the_factory_target_sweep_reads_the_whole_union(monkeypatch) -> None:
    estate = Estate(
        callers={},
        other_repositories=("u/member", "u/public"),
        private=("u/secret",),
        archived=("u/frozen",),
    )
    _, owned, asked = _declarations(monkeypatch, estate, ["u/member"])
    assert sorted(owned) == sorted(asked) == ["u/member", "u/public"]


def test_a_private_member_keeps_its_spelling_in_owned(monkeypatch) -> None:
    estate = Estate(callers={}, other_repositories=("u/member",), private=("u/Canary",))
    _, owned, _ = _declarations(monkeypatch, estate, ["u/member", "u/Canary"])
    assert "u/Canary" in owned
    assert cli._spelled(["U/CANARY"], owned) == ["u/Canary"]


def test_an_archived_member_stays_and_its_declaration_is_read(monkeypatch) -> None:
    estate = Estate(callers={}, other_repositories=("u/member",), archived=("u/old",))
    _, owned, asked = _declarations(monkeypatch, estate, ["u/member", "u/old"])
    assert "u/old" in owned and "u/old" in asked


def test_a_retired_member_is_not_passed_to_the_listing(monkeypatch) -> None:
    passed: list[list[str]] = []
    _wire(monkeypatch, members=[*CLEAN, _member("A/retired", state="retired")])
    monkeypatch.setattr(
        cli, "repositories", lambda _reader, members: passed.append(list(members)) or ["A/one"]
    )
    _invoke()
    assert passed == [["A/one"]]


LISTING_FAILURES = {
    "no member": {"members": (), "retired": ("A/one",)},
    "account type unreadable": {"unreadable_accounts": {"A": "type"}},
    "unknown account type": {"accounts": {"A": "Bot"}},
    "page unreadable": {"unreadable_accounts": {"A": "page"}},
    "ceiling reached": {"endless": True},
}


@pytest.mark.parametrize("failure", sorted(LISTING_FAILURES))
def test_a_listing_that_cannot_be_read_whole_is_one_gap_and_exits_three(
    monkeypatch, failure: str
) -> None:
    kwargs = LISTING_FAILURES[failure]
    if "members" in kwargs:
        # Every member retired: the landing policy names nothing, so the only finding is the gap.
        retired = [_member("A/one", state="retired")]
        _wire(monkeypatch, members=retired, policy=(), deploying=())
    else:
        _wire(monkeypatch, members=CLEAN)
    _github(monkeypatch, Estate(callers={}, other_repositories=("A/one",), **kwargs))
    result = _invoke()
    assert result.exit_code == 3, result.output
    listing_gaps = [line for line in result.stderr.splitlines() if "couldn't be listed" in line]
    assert len(listing_gaps) == 1
