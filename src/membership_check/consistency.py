"""Does the member record agree with the declarations and the per-repository configuration lists?

The record says which repositories the SDS maintains and which services each is granted. Ten
other places still say the same thing in their own words: each repository's `factory-target.toml`,
change-manager's inert and deploying landing populations, and the lists the rollout, tool,
hardening, fork-sync and rotation lanes carry. This module compares them, so a repository added
to one and not the other is a line in a report rather than a lane that quietly does the wrong
thing.

`check` is pure: every input is a plain collection. Reading the sources is `cli.py`'s job.
Repository names compare ignoring case (GitHub spells them one way, the lists another), and a
retired member counts as not granted. An unreadable source is a GAP, never a disagreement.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from membership_check.auto_merge import AutoMergeState
from membership_check.declaration import DeclarationSource
from sds_members.client import Member


@dataclass(frozen=True)
class Scope:
    """The names that could be determined, and why any others couldn't be."""

    names: list[str]
    gaps: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Report:
    disagreements: list[str]
    gaps: list[str]


def factory_targets(owned: Iterable[str], source: DeclarationSource) -> Scope:
    """Every owned repository that declares itself a factory target."""
    targets: list[str] = []
    gaps: list[str] = []
    unreadable: list[str] = []
    for repository in owned:
        answer = source.declaration(repository)
        if answer.target is None:
            unreadable.append(repository)
            gaps.append(f"factory-target declaration unreadable: {repository} ({answer.detail})")
        elif answer.target:
            targets.append(repository)
    return Scope(sorted(targets), gaps, unreadable)


def canonical(names: Iterable[str], owned: Iterable[str]) -> Scope:
    """`names` respelled as GitHub spells them; any that isn't an active repository is a gap."""
    spelling: Mapping[str, str] = {repository.lower(): repository for repository in owned}
    known = sorted(spelling[name.lower()] for name in names if name.lower() in spelling)
    gaps = [
        f"the landing policy names {name}, which isn't an active repository of this account "
        "(archived, renamed or not owned)"
        for name in sorted(names)
        if name.lower() not in spelling
    ]
    return Scope(known, gaps)


def _granted(members: Iterable[Member], service: str) -> set[str]:
    return {
        m.repository.lower()
        for m in members
        if m.state != "retired" and m.grants.get(service) == "granted"
    }


def _require(source: str, repositories: Iterable[str], held: set[str], needs: str) -> list[str]:
    """One line per repository the source names and the record doesn't grant `needs`."""
    return [
        f"{repository}: {source} names it, but the member record does not grant {needs}"
        for repository in sorted(set(repositories), key=str.lower)
        if repository.lower() not in held
    ]


def check(
    members: Iterable[Member],
    *,
    declarations: Scope,
    inert: frozenset[str],
    deploying: frozenset[str],
    rollout_repositories: Iterable[str],
    tool_repositories: Iterable[str],
    hardened_forks: Iterable[str],
    synced_forks: Iterable[str],
    registry_repositories: Iterable[str],
    rollbacks: Iterable[str],
    standing_update_repositories: Iterable[str],
    auto_merge: Mapping[str, AutoMergeState],
) -> Report:
    members = list(members)
    app_changes = _granted(members, "app_changes")
    updates = _granted(members, "dependency_updates")
    patches = _granted(members, "security_patches")
    disagreements: list[str] = []

    disagreements += _require(
        "its factory-target.toml", declarations.names, app_changes, "app_changes"
    )
    declared = {n.lower() for n in declarations.names}
    unread = {n.lower() for n in declarations.unreadable}
    disagreements += [
        f"{m.repository}: the member record grants app_changes, but its factory-target.toml "
        "does not declare factory_target = true"
        for m in sorted(members, key=lambda m: m.repository.lower())
        if m.repository.lower() in app_changes and m.repository.lower() not in declared | unread
    ]
    disagreements += _require("the inert landing population", inert, updates, "dependency_updates")
    disagreements += _require(
        "the deploying landing population", deploying, updates, "dependency_updates"
    )
    rollouts = _granted(members, "rollouts")
    disagreements += _require(
        "deploy_watcher ROLLOUT_WORKFLOWS", rollout_repositories, rollouts, "rollouts"
    )
    disagreements += _require("tool_installer TOOLS", tool_repositories, rollouts, "rollouts")
    disagreements += _require("fork_harden FORKS", hardened_forks, patches, "security_patches")
    disagreements += _require("fork-sync's fork list", synced_forks, updates, "dependency_updates")
    disagreements += _require("fork-sync's fork list", synced_forks, patches, "security_patches")
    disagreements += _require(
        "rotation_worker REGISTRY_REPOSITORIES",
        registry_repositories,
        _granted(members, "credential_rotation"),
        "credential_rotation",
    )
    disagreements += _require("the rollback table", rollbacks, rollouts, "rollouts")
    disagreements += _require(
        "a standing dependency-update package",
        standing_update_repositories,
        updates,
        "dependency_updates",
    )
    auto_disagreements, auto_gaps = _auto_merge(members, auto_merge)
    disagreements += auto_disagreements
    return Report(disagreements, [*declarations.gaps, *auto_gaps])


def _auto_merge(
    members: list[Member], states: Mapping[str, AutoMergeState]
) -> tuple[list[str], list[str]]:
    """No member repository may allow auto-merge or carry a workflow that arms it (ADR-0038).

    Every member is read, retired ones included: this is about what a repository CAN do, not what
    the SDS is granted there. A member with no state, or with a field that couldn't be read, is a
    gap, never a pass.
    """
    by_name = {name.lower(): state for name, state in states.items()}
    disagreements: list[str] = []
    gaps: list[str] = []
    for member in sorted(members, key=lambda m: m.repository.lower()):
        repository = member.repository
        state = by_name.get(repository.lower())
        if state is None:
            gaps.append(f"auto-merge state unread: {repository} (no read was made)")
            continue
        if state.allowed is True:
            disagreements.append(
                f"{repository}: allow_auto_merge is on, so any writer can arm a merge; "
                "nothing in the SDS arms auto-merge (ADR-0038)"
            )
        disagreements += [
            f"{repository}: {path} arms auto-merge; nothing in the SDS arms auto-merge (ADR-0038)"
            for path in state.arming_workflows or ()
        ]
        if state.allowed is None or state.arming_workflows is None:
            gaps.append(f"auto-merge state unreadable: {repository} ({state.detail})")
    return disagreements, gaps
