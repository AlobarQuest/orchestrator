"""The population both lanes list: the members plus their accounts' public repositories.

ADR-0059 amendment 4, decision item 1. The account names below are fixtures; the code holds none.
"""

from __future__ import annotations

import pytest

from pin_watcher.github import (
    MAX_PAGES,
    PER_PAGE,
    GitHubReader,
    Listing,
    PinWatcherError,
    listing,
)
from pin_watcher.measure import sweep
from tests.pin_watcher.conftest import RECOMMENDED, Estate, identical


def _listing(estate: Estate, members: list[str] | None = None) -> Listing:
    with GitHubReader(token="t", transport=estate.transport()) as reader:
        return listing(reader, list(estate.members) if members is None else members)


def _sweep(estate: Estate):
    with GitHubReader(token="t", transport=estate.transport()) as reader:
        return sweep(reader, list(estate.members))


def test_a_user_account_is_listed_through_its_owner_listing() -> None:
    estate = Estate(callers={}, other_repositories=("u/one", "u/two"), members=("u/one",))
    assert set(_listing(estate).repositories) == {"u/one", "u/two"}
    assert estate.requests[0] == "/users/u"
    assert estate.listing_queries == [
        ("/users/u/repos", {"type": "owner", "per_page": str(PER_PAGE), "page": str(page)})
        for page in (1, 2)
    ]


def test_an_organization_is_listed_through_its_public_listing() -> None:
    estate = Estate(
        callers={},
        other_repositories=("org/one", "org/two"),
        members=("org/one",),
        accounts={"org": "Organization"},
    )
    assert set(_listing(estate).repositories) == {"org/one", "org/two"}
    assert estate.listing_queries == [
        ("/orgs/org/repos", {"type": "public", "per_page": str(PER_PAGE), "page": str(page)})
        for page in (1, 2)
    ]


def test_a_private_or_archived_non_member_is_filtered_even_when_the_endpoint_returns_it() -> None:
    """The Mac's login sees more than an installation token; the filter makes both hosts agree."""
    estate = Estate(
        callers={},
        other_repositories=("u/member", "u/open"),
        private=("u/secret",),
        archived=("u/frozen",),
        members=("u/member",),
    )
    assert set(_listing(estate).repositories) == {"u/member", "u/open"}


def test_each_owner_is_read_once_compared_without_case() -> None:
    estate = Estate(callers={}, other_repositories=("u/one", "U/two"), members=("u/one", "U/two"))
    _listing(estate)
    assert [path for path in estate.requests if path.count("/") == 2] == ["/users/u"]


def test_a_managed_user_login_with_an_underscore_is_listed() -> None:
    estate = Estate(callers={}, other_repositories=("dev_corp/one",))
    assert _listing(estate).repositories == ("dev_corp/one",)


def test_two_accounts_are_each_listed() -> None:
    estate = Estate(
        callers={},
        other_repositories=("u/one", "org/two", "org/three"),
        members=("u/one", "org/two"),
        accounts={"org": "Organization"},
    )
    assert set(_listing(estate).repositories) == {"u/one", "org/two", "org/three"}


def test_githubs_spelling_is_kept_and_a_private_member_keeps_the_records() -> None:
    estate = Estate(
        callers={},
        other_repositories=("u/Public-Member",),
        private=("u/Canary",),
        members=("u/public-member", "U/canary"),
    )
    found = _listing(estate)
    assert sorted(found.repositories) == ["U/canary", "u/Public-Member"]
    assert found.members == ("u/Public-Member", "U/canary")


def test_an_archived_public_member_stays_in_the_union_and_is_marked_archived() -> None:
    estate = Estate(callers={}, other_repositories=("u/one",), archived=("u/old",))
    found = _listing(estate, ["u/one", "u/old"])
    assert set(found.repositories) == {"u/one", "u/old"}
    assert found.archived == frozenset({"u/old"})
    assert "u/old" not in found.public


def test_no_member_is_unreadable_rather_than_an_empty_listing() -> None:
    with pytest.raises(PinWatcherError, match="no onboarding or member"):
        _listing(Estate(callers={}, other_repositories=("u/one",)), [])


@pytest.mark.parametrize(
    ("estate", "message"),
    [
        (
            Estate(callers={}, other_repositories=("u/one",), unreadable_accounts={"u": "type"}),
            "account u could not be read",
        ),
        (
            Estate(callers={}, other_repositories=("u/one",), accounts={"u": "Bot"}),
            "account u has an unknown type",
        ),
        (
            Estate(callers={}, other_repositories=("u/one",), unreadable_accounts={"u": "page"}),
            "github rejected GET /users/u/repos: 500",
        ),
        (
            Estate(callers={}, other_repositories=("u/one",), endless=True),
            f"account u has more than {MAX_PAGES * PER_PAGE} repositories",
        ),
    ],
)
def test_an_account_that_cannot_be_listed_whole_is_unreadable(estate: Estate, message: str) -> None:
    with pytest.raises(PinWatcherError, match=message):
        _listing(estate)


def test_a_listing_page_answering_404_is_unreadable_not_the_last_page() -> None:
    """A 404 must not read as an empty account, or the sweep would cover the members alone."""
    estate = Estate(callers={}, other_repositories=("u/one",), unreadable_accounts={"u": "missing"})
    with pytest.raises(PinWatcherError, match="account u's repositories could not be listed"):
        _listing(estate)


@pytest.mark.parametrize("name", ["no-slash", "../orgs/name", "me?x=1/name", "a%2Fb/name"])
def test_a_member_whose_owner_is_not_a_login_is_unreadable_and_nothing_is_sent(name: str) -> None:
    estate = Estate(callers={})
    with pytest.raises(PinWatcherError, match="not owner/name"):
        _listing(estate, [name])
    assert estate.requests == []


# ---- the pin watcher's sweep over the listing ---------------------------------------------------


def test_a_public_non_member_caller_is_swept() -> None:
    result = _sweep(
        Estate(
            callers={"u/member": RECOMMENDED, "u/public": RECOMMENDED},
            comparisons={RECOMMENDED: identical()},
            members=("u/member",),
        )
    )
    assert sorted(c.repository for c in result.callers) == ["u/member", "u/public"]
    assert result.complete


def test_a_private_non_member_caller_is_not_swept() -> None:
    estate = Estate(
        callers={"u/member": RECOMMENDED, "u/hidden": RECOMMENDED},
        comparisons={RECOMMENDED: identical()},
        private=("u/hidden",),
        members=("u/member",),
    )
    result = _sweep(estate)
    assert [c.repository for c in result.callers] == ["u/member"]


def test_a_private_member_is_read_from_its_repository_and_swept() -> None:
    estate = Estate(
        callers={"u/member": RECOMMENDED, "u/canary": RECOMMENDED},
        comparisons={RECOMMENDED: identical()},
        private=("u/canary",),
        members=("u/member", "u/canary"),
    )
    result = _sweep(estate)
    assert sorted(c.repository for c in result.callers) == ["u/canary", "u/member"]
    assert "/repos/u/canary" in estate.requests
    assert "/repos/u/member" not in estate.requests, "a public member is known from the listing"


def test_an_archived_public_member_leaves_the_sweep_without_a_repository_read() -> None:
    estate = Estate(
        callers={"u/member": RECOMMENDED},
        comparisons={RECOMMENDED: identical()},
        archived=("u/old",),
        members=("u/member", "u/old"),
    )
    result = _sweep(estate)
    assert [c.repository for c in result.callers] == ["u/member"]
    assert "/repos/u/old" not in estate.requests
    assert result.complete


def test_an_archived_private_member_leaves_the_sweep() -> None:
    estate = Estate(
        callers={"u/member": RECOMMENDED},
        comparisons={RECOMMENDED: identical()},
        private=("u/old",),
        archived=("u/old",),
        members=("u/member", "u/old"),
    )
    result = _sweep(estate)
    assert [c.repository for c in result.callers] == ["u/member"]
    assert "/repos/u/old" in estate.requests
    assert result.complete


def test_a_member_whose_repository_cannot_be_read_is_unreadable_not_dropped() -> None:
    estate = Estate(
        callers={"u/member": RECOMMENDED},
        comparisons={RECOMMENDED: identical()},
        private=("u/canary",),
        members=("u/member", "u/canary"),
        unreadable_repositories=("u/canary",),
    )
    result = _sweep(estate)
    assert result.unreadable == ["u/canary"]
    assert not result.complete
