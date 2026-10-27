"""The lanes' populations, derived from the member record. A retired member is in none of them."""

from __future__ import annotations

from collections.abc import Iterable

from sds_members.client import SERVICES, App, Member


def _live(members: Iterable[Member]) -> list[Member]:
    return [m for m in members if m.state != "retired"]


def observed(members: Iterable[Member]) -> list[str]:
    """Repositories in `onboarding` or `member`."""
    return [m.repository for m in _live(members)]


def working_copies(members: Iterable[Member]) -> list[str]:
    return [m.working_copy for m in _live(members) if m.working_copy is not None]


def bindable(members: Iterable[Member]) -> list[str]:
    """Working copies of machine-local members granted rollouts."""
    return [
        m.working_copy
        for m in _live(members)
        if m.machine_local and m.working_copy is not None and m.grants.get("rollouts") == "granted"
    ]


def granted(members: Iterable[Member], service: str) -> list[str]:
    if service not in SERVICES:
        raise ValueError(f"unknown service {service!r}")
    return [m.repository for m in _live(members) if m.grants.get(service) == "granted"]


def app_subjects(members: Iterable[Member]) -> list[tuple[str, App]]:
    return [(m.repository, app) for m in _live(members) for app in m.apps]
