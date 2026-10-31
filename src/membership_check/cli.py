"""`membership-check`: compare the SDS member record with the declarations and lists.

Exits 0 clean, 1 failed (a token is unset), 2 disagreement, 3 incomplete (a source was
unreadable). Disagreements outrank gaps. Disagreements go to stdout, gaps to stderr.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Final

import typer

from bump_proposer.landing_policy import LandingPolicyError, read_landing_policy
from bump_proposer.standing import StandingError, discover
from change_proposer.criteria import _ROLLBACKS as ROLLBACKS
from deploy_watcher.workflows import ROLLOUT_WORKFLOWS
from fork_harden.rules import FORKS
from fork_sync.gh import CommandFailed, subprocess_runner
from fork_sync.watch import Unreadable, forks
from membership_check.auto_merge import AutoMergeState, GitHubAutoMergeSource
from membership_check.consistency import Scope, canonical, check, factory_targets
from membership_check.declaration import REGISTRY_FILENAME, GitHubDeclarationSource
from pin_watcher.github import GitHubReader, PinWatcherError, repositories
from rotation_worker.git_mirror import MirrorError, RepoMirror, default_mirror_root
from rotation_worker.registry import REGISTRY_REPOSITORIES, RegistryError, rotated_by_sds
from sds_members.client import Member, MembersUnreadable, read_members
from tool_installer.tools import TOOLS

OBSERVER_TOKEN_VARIABLE: Final = "SDS_MEMBERS_OBSERVER_TOKEN"
OBSERVER_KEY_ID: Final = "orchestrator-observer"
GITHUB_TOKEN_VARIABLE: Final = "SDS_MEMBERS_GITHUB_TOKEN"
CHANGE_MANAGER_TOKEN_VARIABLE: Final = "SDS_MEMBERS_CHANGE_MANAGER_TOKEN"
EXIT_FAILED: Final = 1
EXIT_DISAGREEMENT: Final = 2
EXIT_INCOMPLETE: Final = 3

app = typer.Typer(add_completion=False)


def _fail(message: str) -> typer.Exit:
    typer.echo(f"membership-check: {message}", err=True)
    return typer.Exit(EXIT_FAILED)


def _declarations(token: str, repositories_of_members: list[str]) -> tuple[Scope, list[str]]:
    """Factory targets among the account's repositories and the members', and the gaps."""
    try:
        with GitHubReader(token=token) as reader:
            owned = repositories(reader)
    except PinWatcherError as error:
        gap = f"the account's repositories couldn't be listed: {error}"
        return Scope([], [gap], list(repositories_of_members)), []
    known = {name.lower() for name in owned}
    extra = [name for name in repositories_of_members if name.lower() not in known]
    with GitHubDeclarationSource(token=token) as source:
        return factory_targets([*owned, *extra], source), owned


def _landing(
    token: str, owned: list[str]
) -> tuple[frozenset[str], frozenset[str], list[str], bool]:
    """The inert and deploying landing populations, the gaps, and whether both were decided."""
    try:
        policy = read_landing_policy(token)
    except LandingPolicyError as error:
        return frozenset(), frozenset(), [f"the landing policy couldn't be read: {error}"], False
    populations = (policy.inert, policy.deploying)
    gaps = [gap for names in populations for gap in canonical(names, owned).gaps] if owned else []
    decided = bool(policy.deploying)
    if not decided:
        # A GAP, never "nothing deploys": an absent or empty deploying population is a document
        # that did not decide, so this row could not be checked.
        gaps.append(
            f"landing policy version {policy.version} declares no deploying population, so "
            "the deploying row couldn't be checked"
        )
    inert, deploying = (frozenset(_spelled(names, owned)) for names in populations)
    return inert, deploying, gaps, decided


def _spelled(names: Iterable[str], owned: list[str]) -> list[str]:
    """`names` as GitHub spells them when the account listing is known, else as given."""
    spelling = {repository.lower(): repository for repository in owned}
    return [spelling.get(name.lower(), name) for name in names]


def _auto_merge(token: str, members: Iterable[str]) -> dict[str, AutoMergeState]:
    """Every member repository's auto-merge setting and arming workflows, read from GitHub."""
    with GitHubAutoMergeSource(token=token) as source:
        return {repository: source.state(repository) for repository in members}


def _standing_update_repositories() -> tuple[list[str], list[str]]:
    """Repositories with a standing dependency-update package; an unreadable checkout is a gap."""
    try:
        packages = discover()
    except (StandingError, OSError) as error:
        return [], [f"the packages checkout couldn't be read: {error}"]
    return [package.target_repository for package in packages.values()], []


def _flagged_repositories() -> tuple[list[str], list[str]]:
    """Repositories whose registry has a `rotated_by_sds` row at its `main`, and the gaps.

    Read from the lane's own mirror. An unread mirror is a gap and never an empty population, so
    the caller skips the direction that would read it as one.
    """
    try:
        rows = rotated_by_sds(RepoMirror(default_mirror_root("membership-check")))
    except (MirrorError, RegistryError, OSError) as error:
        return [], [f"the credential registries couldn't be read: {error}"]
    return sorted({entry.repository for entry in rows.values()}), []


def _registry_files(token: str, members: Iterable[Member]) -> tuple[list[str], list[str]]:
    """Non-retired members holding `.cred-consumers.toml`, and a gap for each that wasn't read.

    An absent file (a confirmed 404) is no finding; any other failure is a gap, never a
    disagreement.
    """
    holding: list[str] = []
    gaps: list[str] = []
    with GitHubDeclarationSource(token=token) as source:
        for member in members:
            if member.state == "retired":
                continue
            answer = source.holds_registry(member.repository)
            if answer.target is True:
                holding.append(member.repository)
            elif answer.target is None:
                gaps.append(f"{REGISTRY_FILENAME} unread: {member.repository} ({answer.detail})")
    return holding, gaps


@app.command()
def main() -> None:
    """Compare the member record with the declarations and the configuration lists."""
    tokens = {
        name: os.environ.get(name, "")
        for name in (OBSERVER_TOKEN_VARIABLE, GITHUB_TOKEN_VARIABLE, CHANGE_MANAGER_TOKEN_VARIABLE)
    }
    for name, value in tokens.items():
        if not value:
            raise _fail(f"{name} is not set")
    try:
        members = read_members(
            token=tokens[OBSERVER_TOKEN_VARIABLE], credential_key_id=OBSERVER_KEY_ID
        )
    except MembersUnreadable as error:
        typer.echo(f"membership-check: the member record is unreadable: {error}", err=True)
        raise typer.Exit(EXIT_INCOMPLETE) from None
    declarations, owned = _declarations(
        tokens[GITHUB_TOKEN_VARIABLE], [m.repository for m in members]
    )
    inert, deploying, gaps, landing_decided = _landing(tokens[CHANGE_MANAGER_TOKEN_VARIABLE], owned)
    synced: list[str] = []
    try:
        synced = forks(subprocess_runner)
    except (Unreadable, CommandFailed) as error:
        gaps.append(f"the fork-sync fork list couldn't be read: {error}")
    standing, standing_gaps = _standing_update_repositories()
    gaps += standing_gaps
    flagged, flagged_gaps = _flagged_repositories()
    gaps += flagged_gaps
    registry_files, registry_gaps = _registry_files(tokens[GITHUB_TOKEN_VARIABLE], members)
    gaps += registry_gaps
    report = check(
        members,
        declarations=declarations,
        inert=inert,
        deploying=deploying,
        landing_decided=landing_decided,
        rollout_repositories=_spelled(ROLLOUT_WORKFLOWS, owned),
        tool_repositories=_spelled([tool.repository for tool in TOOLS], owned),
        hardened_forks=_spelled([fork.repository for fork in FORKS], owned),
        synced_forks=_spelled(synced, owned),
        registry_repositories=_spelled(REGISTRY_REPOSITORIES, owned),
        rollbacks=_spelled(ROLLBACKS, owned),
        standing_update_repositories=_spelled(standing, owned),
        auto_merge=_auto_merge(tokens[GITHUB_TOKEN_VARIABLE], [m.repository for m in members]),
        flagged_repositories=_spelled(flagged, owned),
        registry_files=_spelled(registry_files, owned),
        registries_read=not flagged_gaps,
    )
    for line in report.disagreements:
        typer.echo(line)
    for gap in [*report.gaps, *gaps]:
        typer.echo(f"membership-check: {gap}", err=True)
    if report.disagreements:
        raise typer.Exit(EXIT_DISAGREEMENT)
    if report.gaps or gaps:
        raise typer.Exit(EXIT_INCOMPLETE)
