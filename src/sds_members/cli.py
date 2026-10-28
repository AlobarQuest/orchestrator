"""`sds-members`: stage a change to the SDS member record.

`stage <file.toml>` posts the proposal as SYSTEM and prints the page a person confirms it on.
Staging changes no member. The idempotency key is the digest of the proposal itself, so staging
an unchanged file again replays the same proposal instead of staging a second.

A hold is a `[[changes.holds]]` table under its member's change, spelled as Dependabot spells the
update (`npm_and_yarn`, not `npm`; `github_actions`, not `github-actions`):

    summary = "hold sqlalchemy"

    [[changes]]
    repository = "AlobarQuest/orchestrator"
    reason = "2.0.55 broke the build"   # becomes the hold's reason

    [[changes.holds]]
    action = "place"                    # or "lift", which names no ceiling
    ecosystem = "uv"
    directory = "/"                     # optional; "/" when left out
    dependency = "sqlalchemy"
    ceiling = "2.0.54"                  # the highest release allowed
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Annotated, Any, Final

import typer

from sds_members.client import DEFAULT_BASE_URL, ProposalRefused, stage

TOKEN_VARIABLE: Final = "SDS_MEMBERS_SYSTEM_TOKEN"
KEY_ID: Final = "orchestrator-system"

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
