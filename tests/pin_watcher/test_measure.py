"""What one pass finds, and what it refuses to call clean."""

from __future__ import annotations

import pytest

from pin_watcher.compare import AHEAD, BEHIND, CURRENT, DIVERGED, UNPINNED, UNRESOLVABLE
from pin_watcher.github import GitHubReader, PinWatcherError
from pin_watcher.measure import sweep
from tests.pin_watcher.conftest import (
    RECOMMENDED,
    Estate,
    ahead,
    behind,
    diverged,
    identical,
)


def _sweep(estate: Estate):
    with GitHubReader(token="t", transport=estate.transport()) as reader:
        return sweep(reader)


def test_a_caller_at_the_recommendation_is_not_a_finding() -> None:
    result = _sweep(Estate(callers={"o/a": RECOMMENDED}, comparisons={RECOMMENDED: identical()}))
    assert [c.state for c in result.callers] == [CURRENT]
    assert result.findings == []
    assert result.complete


def test_a_caller_behind_the_recommendation_is_a_finding_carrying_its_distance() -> None:
    pin = "b" * 40
    result = _sweep(
        Estate(
            callers={"o/a": pin},
            comparisons={pin: behind(23)},
            dates={pin: "2026-08-20T09:00:00Z"},
        )
    )
    (caller,) = result.callers
    assert caller.state == BEHIND
    assert caller.behind_by == 23
    assert caller.is_finding
    assert result.findings == [caller]


def test_a_caller_ahead_is_reported_as_ahead_rather_than_as_behind() -> None:
    """The recommendation is the stale half, and collapsing the two loses which to advance."""
    pin = "c" * 40
    result = _sweep(
        Estate(
            callers={"o/a": pin},
            comparisons={pin: ahead(2)},
            dates={pin: "2026-09-02T09:00:00Z"},
        )
    )
    (caller,) = result.callers
    assert (caller.state, caller.ahead_by, caller.behind_by) == (AHEAD, 2, 0)
    assert caller.is_finding


def test_a_caller_off_the_default_branch_is_diverged() -> None:
    pin = "d" * 40
    result = _sweep(
        Estate(
            callers={"o/a": pin},
            comparisons={pin: diverged(3, 4)},
            dates={pin: "2026-08-30T09:00:00Z"},
        )
    )
    (caller,) = result.callers
    assert caller.state == DIVERGED
    assert (caller.behind_by, caller.ahead_by) == (3, 4)


def test_a_caller_naming_a_branch_is_unpinned_rather_than_absent() -> None:
    """The GAP-4 state. A pattern matching only hex would report this as having no caller."""
    result = _sweep(Estate(callers={"o/a": "main"}))
    (caller,) = result.callers
    assert (caller.state, caller.pin) == (UNPINNED, "main")
    assert caller.is_finding
    assert caller.behind_by is None


def test_a_sha_the_runner_does_not_have_is_unresolvable_and_not_an_unreadable_pass() -> None:
    """A fact about the caller, never a fact about the pass -- the two carry different exits."""
    result = _sweep(Estate(callers={"o/a": "e" * 40}, comparisons={}))
    (caller,) = result.callers
    assert caller.state == UNRESOLVABLE
    assert result.complete, "an unresolvable pin is a finding, not an incomplete measurement"


def test_a_repository_with_no_caller_is_not_measured_and_is_not_a_finding() -> None:
    result = _sweep(
        Estate(
            callers={"o/a": RECOMMENDED},
            comparisons={RECOMMENDED: identical()},
            other_repositories=("o/unrelated", "o/also-unrelated"),
        )
    )
    assert [c.repository for c in result.callers] == ["o/a"]


def test_an_archived_repository_is_never_swept() -> None:
    result = _sweep(
        Estate(
            callers={"o/a": RECOMMENDED},
            comparisons={RECOMMENDED: identical()},
            archived=("o/frozen",),
        )
    )
    assert [c.repository for c in result.callers] == ["o/a"]


def test_an_unreadable_caller_costs_that_repository_and_nothing_else() -> None:
    """Failing open, per repository -- and an incomplete pass says so rather than exiting clean."""
    estate = Estate(
        callers={"o/a": RECOMMENDED},
        comparisons={RECOMMENDED: identical()},
        unreadable={"o/broken"},
    )
    result = _sweep(estate)
    assert [c.repository for c in result.callers] == ["o/a"]
    assert result.unreadable == ["o/broken"]
    assert not result.complete


def test_an_unreadable_recommendation_fails_the_whole_pass() -> None:
    """One unreadable file must not become six findings about six innocent repositories."""
    estate = Estate(callers={"o/a": RECOMMENDED}, recommended="not-a-sha")
    with pytest.raises(PinWatcherError, match="forty-character sha"):
        _sweep(estate)


def test_callers_sharing_a_pin_are_compared_once() -> None:
    """When the chain is healthy every caller names the same revision; comparing each is waste."""
    estate = Estate(
        callers={"o/a": RECOMMENDED, "o/b": RECOMMENDED, "o/c": RECOMMENDED},
        comparisons={RECOMMENDED: identical()},
    )
    result = _sweep(estate)
    assert len(result.callers) == 3
    compares = [p for p in estate.requests if "/compare/" in p]
    assert len(compares) == 1, "the per-pin cache did not hold"
