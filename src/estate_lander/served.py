"""What an application serves, read from its health route. Landing Part 3b, Task 4.

The estate lander confirms production serves the bump's parent before it asks for the revert, and
the revert's merge commit before it records the restore. It reads each application itself, now,
rather than waiting on `revision_watcher`'s hourly reading -- but the way that lane reads it,
through its own reader and parser (`revision_watcher.estate.ApplicationReader`,
`subjects.revision_of`): the body is read whatever the status, because an unwell application still
names the build it runs.
"""

from __future__ import annotations

import httpx

from revision_watcher.estate import ApplicationReader, UnreadableSubject
from revision_watcher.subjects import revision_of


class ServedUnreadable(Exception):
    """The application could not be asked. Never read as "it serves something else"."""


def served_revision(health_url: str, *, transport: httpx.BaseTransport | None = None) -> str | None:
    """The commit the application at `health_url` names, or None when it names none."""
    with ApplicationReader(transport=transport) as reader:
        try:
            return revision_of(reader.health(health_url))
        except UnreadableSubject as error:
            raise ServedUnreadable(str(error)) from None


def serves(served: str | None, commit: str) -> bool:
    """Is `served` exactly `commit`? Full commits, case-folded; a short or absent one is not."""
    return served is not None and len(served) == len(commit) and served.lower() == commit.lower()
