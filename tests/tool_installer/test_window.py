"""The change window, proven to fire BOTH ways.

**A SINGLE OUT-OF-WINDOW ASSERTION PASSES FOR THE WRONG REASON MOST OF THE DAY.** The window is
four hours wide, so for twenty of every twenty-four a term that never read the clock at all agrees
with one that did. Every case here therefore comes in a PAIR whose answers must differ, and every
clock is injected -- nothing below reads the machine's.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from tool_installer.window import ChangeWindow, WindowUnreadable, window_from_policy

# The operator machine's window as production serves it, measured 2026-09-06.
LIVE = ChangeWindow(timezone="America/New_York", start=time(2, 0), end=time(6, 0))


def ny(hour: int, minute: int = 0) -> datetime:
    """The UTC instant at which New York's clock reads `hour:minute` on 2026-09-06.

    Built by CONVERTING a New York wall time rather than by adding four hours to a UTC one: the
    offset arithmetic silently overflows past 23:59 into the next day, and the tests that would
    catch a boundary error are precisely the ones near midnight.
    """
    return datetime(2026, 9, 6, hour, minute, tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)


def test_the_window_admits_inside_and_refuses_outside() -> None:
    """THE PAIR. A term that ignored its clock would give one answer to both of these."""
    assert LIVE.permits(ny(3)) is True
    assert LIVE.permits(ny(15)) is False


@pytest.mark.parametrize(
    ("hour", "minute", "permitted"),
    [
        (1, 59, False),  # one minute before
        (2, 0, True),  # start is INCLUSIVE
        (5, 59, True),  # one minute before the end
        (6, 0, False),  # end is EXCLUSIVE
        (0, 0, False),
        (12, 0, False),
        (23, 59, False),
    ],
)
def test_both_boundaries_are_the_orchestrators(hour: int, minute: int, permitted: bool) -> None:
    """Start inclusive, end exclusive -- copied from `orchestrator/factory_policy.py`.

    Both ends are asserted with the minute either side, so a predicate that moved one boundary is
    caught rather than a predicate that merely admits the middle.
    """
    assert LIVE.permits(ny(hour, minute)) is permitted


def test_the_window_is_judged_in_ITS_zone_and_not_the_machines() -> None:
    """03:00 UTC is 23:00 in New York -- outside -- and a predicate that skipped the conversion
    would admit it. The converse instant is asserted beside it so the pair discriminates.
    """
    assert LIVE.permits(datetime(2026, 9, 6, 3, 0, tzinfo=UTC)) is False
    assert LIVE.permits(datetime(2026, 9, 6, 7, 0, tzinfo=UTC)) is True


def test_a_window_that_wraps_midnight_admits_across_it() -> None:
    """The ordinary nightly shape, and the branch a plain `start <= x < end` gets wrong."""
    wrapping = ChangeWindow(timezone="America/New_York", start=time(22, 0), end=time(4, 0))
    assert wrapping.permits(ny(23)) is True
    assert wrapping.permits(ny(1)) is True
    assert wrapping.permits(ny(12)) is False


def test_a_naive_moment_is_refused_rather_than_guessed_at() -> None:
    """A naive instant would be read in whatever zone the machine holds, which is the assumption
    the window's own rationale exists to remove."""
    with pytest.raises(WindowUnreadable):
        LIVE.permits(datetime(2026, 9, 6, 3, 0))


def _policy(**window: object) -> dict[str, object]:
    return {
        "reach": [
            {"member": "source_repository"},
            {"member": "operator_machine", "change_window": window},
        ]
    }


def test_the_operator_machine_row_is_the_one_read() -> None:
    """Production's own shape, keyed by `member` rather than by position in the list."""
    window = window_from_policy(_policy(timezone="America/New_York", start="02:00", end="06:00"))
    assert (window.timezone, window.start, window.end) == (LIVE.timezone, LIVE.start, LIVE.end)


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param({}, id="no reach rows"),
        pytest.param({"reach": "not a list"}, id="reach is not a list"),
        pytest.param({"reach": [{"member": "live_estate"}]}, id="no operator_machine row"),
        pytest.param({"reach": [{"member": "operator_machine"}]}, id="row declares no window"),
        pytest.param("not an object", id="policy is not an object"),
    ],
)
def test_an_unreadable_window_refuses_and_never_defaults(policy: object) -> None:
    """A WINDOW THAT CANNOT BE READ IS A REFUSAL, NEVER A DEFAULT.

    A row present with NO window is in this list deliberately: two of the four reach members
    declare none, so it is a real state, and it means this lane has no hours to act in rather than
    any hours it likes.
    """
    with pytest.raises(WindowUnreadable):
        window_from_policy(policy)


@pytest.mark.parametrize(
    "window",
    [
        {"timezone": "America/New_York", "start": "not a time", "end": "06:00"},
        {"timezone": "America/New_York", "start": "02:00", "end": 6},
        {"timezone": "", "start": "02:00", "end": "06:00"},
        {"timezone": "Mars/Olympus", "start": "02:00", "end": "06:00"},
    ],
)
def test_a_malformed_window_refuses_rather_than_raising_later(window: dict[str, object]) -> None:
    """An unresolvable zone is caught HERE rather than inside `permits`, where it would read as a
    bad clock rather than as an unreadable policy."""
    with pytest.raises(WindowUnreadable):
        window_from_policy(_policy(**window))
