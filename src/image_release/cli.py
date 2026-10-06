"""`image-release bind`: the deploy session's step after the digest and revision check.

Exit codes mirror the activation sweep's, because a person reads them the same way:

* 0 -- everything was done, or there was nothing to do.
* 2 -- a condition is unmet: the operator's digests disagree, production does not serve the built
  commit, or verification asks for revision or review.
* 3 -- an answer is missing: a refusal, an unreachable orchestrator, or a checkout that could not
  answer. This outranks 2.
* 1 -- the command itself is malformed: a missing token, a malformed sha or digest, a bad URL.

TWO BEARERS, TWO VARIABLES. Binding and observing are SYSTEM's; verifying is VERIFIER's. One
ambient token serving both identities is the failure this estate has already paid for in the
launchers and in `factory decompose`, so each has its own variable and its own key id.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Annotated

import typer

from image_release.client import (
    AnonymousClient,
    SystemClient,
    UnusableEndpointError,
    VerifierClient,
)
from image_release.release import Release, has_conditions, has_findings, release_pass

app = typer.Typer(no_args_is_help=True)

# The SYSTEM bearer, under the same variable the activation sweep's `bind` reads, because it is the
# same credential: the one identity admitted to bind and to observe.
SYSTEM_TOKEN_VARIABLE = "ACTIVATION_BIND_TOKEN"
VERIFIER_TOKEN_VARIABLE = "IMAGE_VERIFY_TOKEN"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_CONDITION = 2
EXIT_INCOMPLETE = 3

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@app.callback()
def _cli() -> None:
    """Deploy-time container-image release binder (SDS 1.1 item 5a, ADR-0039 amendment).

    A callback keeps the command named: Typer otherwise collapses a lone command to the top level.
    """


def _usage(message: str) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code=EXIT_USAGE)


@app.command("bind")
def bind_command(
    repository: Annotated[str, typer.Option(help="e.g. AlobarQuest/orchestrator")],
    checkout: Annotated[Path, typer.Option(help="A local clone holding the built commit.")],
    built_commit: Annotated[
        str, typer.Option(help="Full 40-character sha the image was built from.")
    ],
    digest: Annotated[str, typer.Option(help="The pushed image digest, sha256:<64 hex>.")],
    observed_digest: Annotated[
        str, typer.Option(help="The running container's digest, read on the host.")
    ],
    image_name: Annotated[str, typer.Option(help="e.g. orchestrator")],
    tag: Annotated[str, typer.Option(help="The human-readable tag that was deployed.")],
    workflow_run_url: Annotated[str, typer.Option(help="The Release image run that pushed it.")],
    base_url: Annotated[str, typer.Option(help="The orchestrator's production origin.")],
    deployer: Annotated[str, typer.Option(help="A short, non-secret name for who deployed.")],
    registry: Annotated[str, typer.Option()] = "ghcr.io",
    image_repository: Annotated[str, typer.Option()] = "alobarquest",
    environment: Annotated[str, typer.Option()] = "production",
    credential_key_id: Annotated[str, typer.Option()] = "orchestrator-system",
    verifier_key_id: Annotated[str, typer.Option()] = "orchestrator-verifier",
    dry_run: Annotated[
        bool, typer.Option(help="Read and probe, print what would be written; write nothing.")
    ] = False,
) -> None:
    """Bind, observe and verify every completed unit the deployed image carries.

    THE ORCHESTRATOR IS ITS OWN SUBJECT: `--base-url` is both where this command files what it
    found and the deployment it probes, because the image being bound is the orchestrator's.

    A DRY RUN STILL NEEDS THE SYSTEM BEARER, because knowing what would be bound means asking which
    units are candidates and the auth probe needs a configured read. It needs no VERIFIER bearer:
    it sends nothing to verify.
    """
    if not FULL_SHA.fullmatch(built_commit):
        raise _usage("--built-commit must be a full 40-character lowercase sha")
    if not SHA256_DIGEST.fullmatch(digest) or not SHA256_DIGEST.fullmatch(observed_digest):
        raise _usage("--digest and --observed-digest must be sha256:<64 lowercase hex>")
    if observed_digest != digest:
        # Before anything is read or written. The running container is not the image that was
        # pushed, so nothing this command could file would be true.
        typer.echo(
            "the running container's digest is not the pushed digest; nothing filed", err=True
        )
        raise typer.Exit(code=EXIT_CONDITION)
    system_token = os.environ.get(SYSTEM_TOKEN_VARIABLE, "")
    verifier_token = os.environ.get(VERIFIER_TOKEN_VARIABLE, "")
    if not system_token:
        raise _usage(f"{SYSTEM_TOKEN_VARIABLE} is required")
    if not dry_run and not verifier_token:
        raise _usage(f"{VERIFIER_TOKEN_VARIABLE} is required")

    release = Release(
        repository=repository,
        built_commit=built_commit,
        digest=digest,
        registry=registry,
        image_repository=image_repository,
        image_name=image_name,
        tag=tag,
        workflow_run_url=workflow_run_url,
        base_url=base_url.rstrip("/"),
        deployer=deployer,
        environment=environment,
    )
    try:
        system = SystemClient(
            base_url=base_url, credential_key_id=credential_key_id, token=system_token
        )
        anonymous = AnonymousClient(base_url=base_url)
        verifier = (
            None
            if dry_run
            else VerifierClient(
                base_url=base_url, credential_key_id=verifier_key_id, token=verifier_token
            )
        )
    except UnusableEndpointError as error:
        raise _usage(f"--base-url: {error}") from error
    try:
        summary = release_pass(
            release,
            checkout=checkout,
            system=system,
            verifier=verifier,
            anonymous=anonymous,
            dry_run=dry_run,
        )
    finally:
        for client in (system, anonymous, verifier):
            if client is not None:
                client.close()
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
    if has_findings(summary):
        raise typer.Exit(code=EXIT_INCOMPLETE)
    raise typer.Exit(code=EXIT_CONDITION if has_conditions(summary) else EXIT_OK)
