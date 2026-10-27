"""`membership-check`: compare the SDS member record with the declarations and lists.

Exits 0 clean, 1 failed (a token is unset), 2 disagreement, 3 incomplete (a source was
unreadable). Disagreements outrank gaps. Disagreements go to stdout, gaps to stderr.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Final

import typer

from bump_proposer.landing_policy import LandingPolicyError, read_inert_landing
from deploy_watcher.workflows import ROLLOUT_WORKFLOWS
from fork_harden.rules import FORKS
from fork_sync.gh import CommandFailed, subprocess_runner
from fork_sync.watch import Unreadable, forks
from membership_check.consistency import Scope, canonical, check, factory_targets
from pin_watcher.github import GitHubReader, PinWatcherError, repositories
from rotation_worker.registry import REGISTRY_REPOSITORIES
from sds_members.client import MembersUnreadable, read_members
from tool_installer.tools import TOOLS
from work_carrier.declaration import GitHubDeclarationSource

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


def _inert(token: str, owned: list[str]) -> tuple[frozenset[str], list[str]]:
    try:
        policy = read_inert_landing(token)
    except LandingPolicyError as error:
        return frozenset(), [f"the landing policy couldn't be read: {error}"]
    gaps = canonical(policy.repositories, owned).gaps if owned else []
    return frozenset(_spelled(policy.repositories, owned)), gaps


def _spelled(names: Iterable[str], owned: list[str]) -> list[str]:
    """`names` as GitHub spells them when the account listing is known, else as given."""
    spelling = {repository.lower(): repository for repository in owned}
    return [spelling.get(name.lower(), name) for name in names]


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
    inert, inert_gaps = _inert(tokens[CHANGE_MANAGER_TOKEN_VARIABLE], owned)
    gaps = inert_gaps
    synced: list[str] = []
    try:
        synced = forks(subprocess_runner)
    except (Unreadable, CommandFailed) as error:
        gaps.append(f"the fork-sync fork list couldn't be read: {error}")
    report = check(
        members,
        declarations=declarations,
        inert=inert,
        rollout_repositories=_spelled(ROLLOUT_WORKFLOWS, owned),
        tool_repositories=_spelled([tool.repository for tool in TOOLS], owned),
        hardened_forks=_spelled([fork.repository for fork in FORKS], owned),
        synced_forks=_spelled(synced, owned),
        registry_repositories=_spelled(REGISTRY_REPOSITORIES, owned),
    )
    for line in report.disagreements:
        typer.echo(line)
    for gap in [*report.gaps, *gaps]:
        typer.echo(f"membership-check: {gap}", err=True)
    if report.disagreements:
        raise typer.Exit(EXIT_DISAGREEMENT)
    if report.gaps or gaps:
        raise typer.Exit(EXIT_INCOMPLETE)
