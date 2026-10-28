"""The bumps this lane cannot route, told to a person through alert-hub (bridge Part 3).

A failed bump the factory has no tooling for, a pip requirement range, a dependency already covered
by a package in another ecosystem, and every docker bump (Ruling 6) each hold one alert open, keyed
`dependency-bump:<repository>:<ecosystem>:<dependency>` (Ruling 5, `dependency_bump_key`). The key
fires once and resolves on the first writing pass that no longer raises it: its pull request has
merged or closed, or (for an ecosystem the cascade lands) a re-run passed. A re-run failing again
but not yet settled holds the key (`Raised.held`), so it neither resolves nor fires twice.

A key is never resolved for a repository this pass did not read: one GitHub could not answer, or one
outside a `--repository`-narrowed run. A dry run never reaches this module, so it sends nothing and
writes no state file.
"""

from __future__ import annotations

import os
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from alert_hub_client import (
    Alert,
    AlertHubError,
    KeyedAlerts,
    dependency_bump_key,
    dependency_bump_prefix,
)

USER_AGENT: Final = "bump-proposer/1 (+AlobarQuest/orchestrator)"
STATE_FILE: Final = "alerts.json"
DEFAULT_STATE_DIR: Final = Path.home() / "Library" / "Application Support" / "sds" / "bump-proposer"


@dataclass
class Raised:
    """What one pass raised: the keys firing, and the keys `held` -- a bump whose condition this
    pass can't decide yet (a re-run failing again, not settled), which neither fires nor resolves.
    """

    firing: dict[str, Alert] = field(default_factory=dict)
    held: set[str] = field(default_factory=set)


def key_for(repository: str, ecosystem: str, dependency: str) -> str:
    return dependency_bump_key(repository, ecosystem, dependency)


def alert_for(repository: str, number: int, title: str, why: str) -> Alert:
    return Alert(
        f"Dependency bump needs a person: {repository} #{number}",
        f"{title}\n\n{why}.",
        f"https://github.com/{repository}/pull/{number}",
    )


def state_dir() -> Path:
    return Path(os.environ.get("BUMP_PROPOSER_STATE_DIR") or DEFAULT_STATE_DIR)


def sync(
    token: str,
    raised: Raised,
    *,
    unreadable: Collection[str],
    requested: Collection[str] | None,
) -> str | None:
    """Bring alert-hub in line with `raised`. Answers why it could not, or None.

    `unreadable` are repositories whose pull requests this pass could not read; `requested`, when
    given, are the only repositories this pass read at all. Every remembered key outside what was
    read stays as it was.
    """
    directory = state_dir()
    alerts = KeyedAlerts(
        lambda: token,
        directory / STATE_FILE,
        user_agent=USER_AGENT,
        resolved_title="Dependency bump closed",
    )
    held = tuple(dependency_bump_prefix(name) for name in unreadable)
    read = None if requested is None else tuple(dependency_bump_prefix(n) for n in requested)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        unread = raised.held | {
            key
            for key in alerts.remembered()
            if key.startswith(held) or (read is not None and not key.startswith(read))
        }
        alerts.sync(raised.firing, unread)
    except AlertHubError as error:
        return f"alert-hub: {error}"
    except OSError as error:
        return f"alert-hub: the state directory is unusable: {type(error).__name__}"
    return None
