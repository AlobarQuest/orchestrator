"""One pass: read the recommendation, find every caller, classify each against it.

FAILING OPEN, PER REPOSITORY. A repository whose caller cannot be read costs that repository and
nothing else -- a pass that died on the third of six would discard the two it had already
classified. The recommendation is the one exception: it is the thing everything is measured
against, so a pass that cannot read it has no measurement to make and says so.

FAILING OPEN IS NOT EXITING ZERO. Unreadable repositories are returned alongside the callers so
the caller of this function can report an incomplete pass as incomplete. A pass that could not
read every repository cannot claim it found every drifted caller.
"""

from __future__ import annotations

from pin_watcher.compare import (
    UNPINNED,
    UNRESOLVABLE,
    Caller,
    state_from_comparison,
)
from pin_watcher.github import (
    SHA,
    GitHubReader,
    PinWatcherError,
    caller_pin,
    committed_at,
    comparison,
    recommendation,
    repositories,
)


class Pass:
    """What one sweep found, and what it could not read.

    Deliberately not a namedtuple of two lists: `unreadable` exists to be *checked*, and a shape
    that makes it easy to ignore is how an incomplete pass comes to be reported as a clean one.
    """

    def __init__(self, recommended: str, recommended_at: str | None = None) -> None:
        self.recommended = recommended
        # The recommendation's own committer date. It is the fallback `observed_at` for a caller
        # whose pin has no date -- an unpinned or unresolvable one -- and `record.py` explains why
        # that field may never be a wall clock.
        self.recommended_at = recommended_at
        self.callers: list[Caller] = []
        self.unreadable: list[str] = []

    @property
    def findings(self) -> list[Caller]:
        return [caller for caller in self.callers if caller.is_finding]

    @property
    def complete(self) -> bool:
        return not self.unreadable


def _classify(
    reader: GitHubReader,
    recommended: str,
    repository: str,
    pin: str,
    cache: dict[str, tuple[str, int | None, int | None, str | None]],
) -> Caller:
    """One caller's state. Cached per PIN, because callers cluster on the same revision.

    The cache is the reason a sweep of sixty repositories costs a handful of comparison calls
    rather than one per caller: when the chain is healthy every caller names the same SHA, and
    when it is broken they cluster on the revision they were all last advanced to.
    """
    if pin not in cache:
        if not SHA.match(pin):
            cache[pin] = (UNPINNED, None, None, None)
        else:
            result = comparison(reader, recommended, pin)
            if result is None:
                cache[pin] = (UNRESOLVABLE, None, None, None)
            else:
                cache[pin] = (
                    state_from_comparison(result.get("status", "")),
                    result.get("behind_by"),
                    result.get("ahead_by"),
                    committed_at(reader, pin),
                )
    state, behind_by, ahead_by, pinned_at = cache[pin]
    return Caller(
        repository=repository,
        pin=pin,
        state=state,
        behind_by=behind_by,
        ahead_by=ahead_by,
        pinned_at=pinned_at,
    )


def sweep(reader: GitHubReader) -> Pass:
    """Every caller in the account, measured against the recommendation."""
    recommended = recommendation(reader)
    result = Pass(recommended, committed_at(reader, recommended))
    cache: dict[str, tuple[str, int | None, int | None, str | None]] = {}
    for repository in repositories(reader):
        try:
            pin = caller_pin(reader, repository)
        except PinWatcherError:
            result.unreadable.append(repository)
            continue
        if pin is None:
            continue
        try:
            result.callers.append(_classify(reader, result.recommended, repository, pin, cache))
        except PinWatcherError:
            result.unreadable.append(repository)
    return result


def as_lines(result: Pass) -> list[str]:
    """The pass, one line per caller, for a launcher's log."""
    lines = [f"recommendation {result.recommended[:7]} ({len(result.callers)} callers)"]
    for caller in sorted(result.callers, key=lambda c: c.repository):
        detail = ""
        if caller.behind_by:
            detail = f" ({caller.behind_by} behind)"
        elif caller.ahead_by:
            detail = f" ({caller.ahead_by} ahead)"
        mark = "  " if not caller.is_finding else "->"
        lines.append(f"{mark} {caller.repository:<34} {caller.pin[:7]}  {caller.state}{detail}")
    for repository in sorted(result.unreadable):
        lines.append(f"?? {repository:<34} could not be read")
    return lines
