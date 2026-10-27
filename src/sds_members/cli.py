"""`sds-members`: stage a change to the SDS member record.

`check` compares the record with the declarations and the per-repository configuration lists
(`consistency.py`) and exits 0 clean, 1 failed, 2 disagreement, 3 incomplete.

`stage <file.toml>` posts the proposal as SYSTEM and prints the page a person confirms it on.
Staging changes no member. The idempotency key is the digest of the proposal itself, so staging
an unchanged file again replays the same proposal instead of staging a second.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Annotated, Any, Final

import typer

from bump_proposer.landing_policy import LandingPolicyError, read_inert_landing
from deploy_watcher.workflows import ROLLOUT_WORKFLOWS
from fork_harden.rules import FORKS
from fork_sync.gh import CommandFailed, subprocess_runner
from fork_sync.watch import Unreadable, forks
from pin_watcher.github import GitHubReader, PinWatcherError, repositories
from rotation_worker.registry import REGISTRY_REPOSITORIES
from sds_members.client import (
    DEFAULT_BASE_URL,
    MembersUnreadable,
    ProposalRefused,
    read_members,
    stage,
)
from sds_members.consistency import Scope, canonical, check, factory_targets
from tool_installer.tools import TOOLS
from work_carrier.declaration import GitHubDeclarationSource

TOKEN_VARIABLE: Final = "SDS_MEMBERS_SYSTEM_TOKEN"
KEY_ID: Final = "orchestrator-system"
OBSERVER_TOKEN_VARIABLE: Final = "SDS_MEMBERS_OBSERVER_TOKEN"
OBSERVER_KEY_ID: Final = "orchestrator-observer"
GITHUB_TOKEN_VARIABLE: Final = "SDS_MEMBERS_GITHUB_TOKEN"
CHANGE_MANAGER_TOKEN_VARIABLE: Final = "SDS_MEMBERS_CHANGE_MANAGER_TOKEN"
EXIT_DISAGREEMENT: Final = 2
EXIT_INCOMPLETE: Final = 3

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Maintain the SDS member record."""


def idempotency_key(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fail(message: str) -> typer.Exit:
    typer.echo(f"sds-members: {message}", err=True)
    return typer.Exit(1)


@app.command("stage")
def stage_command(file: Annotated[Path, typer.Argument(help="the proposal, as TOML")]) -> None:
    """Stage the proposal in FILE for a person to confirm."""
    token = os.environ.get(TOKEN_VARIABLE, "")
    if not token:
        raise _fail(f"{TOKEN_VARIABLE} is not set")
    try:
        body = tomllib.loads(file.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise _fail(f"{file} is unreadable: {type(error).__name__}") from None
    try:
        answer = stage(
            token=token,
            credential_key_id=KEY_ID,
            body=body,
            idempotency_key=idempotency_key(body),
        )
    except ProposalRefused as refusal:
        raise _fail(str(refusal)) from None
    proposal_id = answer.get("id")
    if not isinstance(proposal_id, str):
        raise _fail("the orchestrator staged the proposal but returned no id")
    typer.echo(f"staged {proposal_id}")
    typer.echo(f"{DEFAULT_BASE_URL}/review/member-proposals/{proposal_id}")


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
    return frozenset(policy.repositories), gaps


@app.command("check")
def check_command() -> None:
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
        typer.echo(f"sds-members: the member record is unreadable: {error}", err=True)
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
        rollout_repositories=ROLLOUT_WORKFLOWS,
        tool_repositories=[tool.repository for tool in TOOLS],
        hardened_forks=[fork.repository for fork in FORKS],
        synced_forks=synced,
        registry_repositories=REGISTRY_REPOSITORIES,
    )
    for line in report.disagreements:
        typer.echo(line)
    for gap in [*report.gaps, *gaps]:
        typer.echo(f"sds-members: {gap}", err=True)
    if report.disagreements:
        raise typer.Exit(EXIT_DISAGREEMENT)
    if report.gaps or gaps:
        raise typer.Exit(EXIT_INCOMPLETE)
