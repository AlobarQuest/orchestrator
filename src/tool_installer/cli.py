"""The installer's entry point. Operator- or launcher-invoked; there is no scheduler and no loop.

A BARE PASS WRITES NOTHING TO THE MACHINE. `--install` is what makes a pass act, exactly as
`--register` does for `work-carrier`. Without it the pass measures, reports what it WOULD do, and
touches no binary. It still files its observation, because the record of a machine running an old
tool is the finding's durable home and is worth having whether or not anybody permitted an
install; `--dry-run` is the switch that files nothing either.

`--install` AND THE WINDOW ARE SEPARATE TERMS AND BOTH MUST HOLD. `--install` says the operator
permits acting; the window says not now. There is deliberately NO window override -- an operator
who wants an out-of-hours install runs `cargo install` by hand, which is one command and honest
about being a human act.

EXIT CODES, and they are the whole interface a scheduled run has:

  0  measured, and either nothing to install, or an install succeeded and probed clean. A tool
     that is behind with nothing permitted to act on it is ALSO 0: that is the ordinary state
     between a merge and the next window, not a failure, and its durable home is the observation
     this pass files with `degraded` status.
  1  the tool itself failed -- a missing credential, an unusable URL.
  2  could not use its inputs: the window was unreadable, the fork's revision was unreadable, or
     an install was called for and there is no toolchain to do it with. ALSO the code for a pass
     whose finding could not be FILED -- an unfiled pass cannot claim it reported anything, which
     is the same principle its sibling lanes state as "the incomplete code outranks the finding
     code", with the numbers this group uses.
  3  something was found: the install failed, or the artifact failed its probe and was rolled back.

**THE ESTATE'S LAUNCHERS DO NOT SHARE ONE EXIT VOCABULARY, AND 2 AND 3 ARE INVERTED BETWEEN THE
TWO GROUPS.** This lane matches the ACTING group -- `estate-landing`, `change-proposer`,
`work-carrier`, `bump-proposer` -- because those are its siblings. A reader arriving from
`run-landing-ledger.sh`, `run-deploy-watcher.sh` or `run-activation-sweep.sh` will otherwise
assume the wrong one: there, 2 means something was found and 3 means something could not be read.

THE TOOLCHAIN IS RESOLVED LAZILY, only when the pass is about to act. A report does not need a
compiler, and refusing to describe the machine because it cannot build for it would make the
read-only half of this lane depend on the acting half's inputs.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from tool_installer.github import GitHubReader, GitHubReadError, commit
from tool_installer.install import (
    ACTION_NONE,
    ACTION_NOT_PERMITTED,
    ArtifactRecord,
    install_and_prove,
)
from tool_installer.install import ProbeResult as _ProbeResult
from tool_installer.orchestrator_client import (
    ObservationWriteError,
    UnusableEndpointError,
    open_client,
)
from tool_installer.plugin import (
    Sites,
    default_sites,
    install_and_prove_plugin,
    read_entry,
    resolve_claude,
)
from tool_installer.policy_client import PolicyReadError, open_policy_client
from tool_installer.policy_client import UnusableEndpointError as PolicyUrlError
from tool_installer.record import (
    FAILING_ACTIONS,
    STATE_CURRENT,
    Installation,
    installation_observation,
    summary_of,
)
from tool_installer.toolchain import (
    DEFAULT_BACKUP_ROOT,
    DEFAULT_INSTALL_ROOT,
    Cargo,
    ToolchainError,
    read_installed,
    resolve_cargo,
)
from tool_installer.tools import TOOLS, CargoInstall, Tool
from tool_installer.window import WindowUnreadable, window_from_policy

EXIT_OK = 0
EXIT_TOOL_FAILED = 1
EXIT_UNUSABLE = 2
EXIT_FOUND = 3

app = typer.Typer(add_completion=False, help=__doc__)


def _now() -> datetime:
    """The pass's clock, used ONLY to judge the window -- never to stamp a record."""
    return datetime.now(UTC)


def _installed(tool: Tool, install_root: Path, sites: Sites) -> ArtifactRecord | None:
    """What each strategy's OWN record says is installed, read without any toolchain.

    THE DISPATCH IS HERE AND NOT INSIDE EACH STRATEGY because reading is the half that must work
    on a bare pass: cargo's record is a file in the install root and Claude Code's is a file under
    the plugins root, so neither needs the thing that would do an install. Resolving a toolchain
    to answer "what is installed" would make the read-only half of this lane depend on the acting
    half's inputs.
    """
    row = tool.install
    if isinstance(row, CargoInstall):
        return read_installed(install_root, row.crate)
    return read_entry(sites, tool, row.marketplace)


def _root_of(tool: Tool, install_root: Path, sites: Sites) -> str:
    """Where this row's artifact lives, for the record: cargo's root, or the plugins root."""
    if isinstance(tool.install, CargoInstall):
        return str(install_root)
    return str(sites.plugins_root)


def _measure(
    tool: Tool,
    reader: GitHubReader,
    install_root: Path,
    sites: Sites,
) -> Installation:
    """What is installed, what is available, and nothing about what to do with either."""
    head = commit(reader, tool.repository, tool.branch)
    if head is None:
        raise GitHubReadError(f"{tool.repository} has no {tool.branch}")
    available_revision, available_committed_at = head

    installed = _installed(tool, install_root, sites)
    installed_committed_at: str | None = None
    if installed is not None:
        if installed.revision == available_revision:
            installed_committed_at = available_committed_at
        else:
            # A revision the fork's history no longer has -- a force-push -- is a real state and
            # not an unreadable GitHub, so it yields `None` here and the record falls back to the
            # available revision's clock rather than refusing the whole pass.
            found = commit(reader, tool.repository, installed.revision)
            installed_committed_at = found[1] if found else None

    return Installation(
        tool=tool,
        install_root=_root_of(tool, install_root, sites),
        installed_revision=installed.revision if installed else None,
        installed_version=installed.version if installed else None,
        installed_committed_at=installed_committed_at,
        available_revision=available_revision,
        available_committed_at=available_committed_at,
        action=ACTION_NONE,
    )


def _acted(
    base: Installation,
    action: str,
    probes: tuple[_ProbeResult, ...],
    detail: str,
    install_root: Path,
    sites: Sites,
) -> Installation:
    """Re-read the strategy's own record after an act, so the row describes the machine as it is."""
    installed = _installed(base.tool, install_root, sites)
    committed_at = base.installed_committed_at
    if installed is not None and installed.revision == base.available_revision:
        committed_at = base.available_committed_at
    return Installation(
        tool=base.tool,
        install_root=base.install_root,
        installed_revision=installed.revision if installed else None,
        installed_version=installed.version if installed else None,
        installed_committed_at=committed_at,
        available_revision=base.available_revision,
        available_committed_at=base.available_committed_at,
        action=action,
        probes=probes,
        detail=detail,
    )


@app.command()
def main(  # noqa: PLR0911, PLR0912, C901
    install: Annotated[
        bool,
        typer.Option("--install", help="Permit this pass to replace a binary. Off by default."),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Measure and report; file no observation.")
    ] = False,
    install_root: Annotated[
        Path | None,
        typer.Option(
            "--install-root",
            help="Cargo install root. Defaults to ~/.cargo; a scratch root proves the rollback.",
        ),
    ] = None,
    backup_root: Annotated[
        Path | None,
        typer.Option(
            "--backup-root",
            help="Where outgoing artifacts are kept. Defaults to ~/.local/bin-backups.",
        ),
    ] = None,
) -> None:
    root = install_root or DEFAULT_INSTALL_ROOT
    backups = backup_root or DEFAULT_BACKUP_ROOT
    sites = default_sites()

    # BOTH CREDENTIALS ARE CHECKED HERE, BEFORE ANY BINARY CAN BE REPLACED. Checking the observer
    # one at the filing step instead -- which is where it is used -- means a pass can swap the
    # tool and then discover it cannot record that it did. The trigger is ordinary rather than
    # exotic: the launcher runs under `set -uo pipefail` with no `-e`, and its `_bws_value` yields
    # an empty string when `bws secret get` fails, so a transient failure on the SECOND of two
    # fetches produces exactly that. An unrecorded swap of the tool that filters every command on
    # this machine is the one outcome worth refusing a whole pass to avoid.
    base_url = os.environ.get("ORCHESTRATOR_API_URL", "").strip()
    policy_key = os.environ.get("ORCHESTRATOR_POLICY_CREDENTIAL_KEY_ID", "").strip()
    policy_token = os.environ.get("ORCHESTRATOR_POLICY_TOKEN", "").strip()
    observer_key = os.environ.get("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "").strip()
    observer_token = os.environ.get("ORCHESTRATOR_API_TOKEN", "").strip()
    if not (base_url and policy_key and policy_token):
        typer.echo("FATAL: the orchestrator policy credential is not configured.", err=True)
        raise typer.Exit(EXIT_TOOL_FAILED)
    if not dry_run and not (observer_key and observer_token):
        # Not required for a dry run, which files nothing and so needs no write credential.
        typer.echo("FATAL: the observer credential is not configured.", err=True)
        raise typer.Exit(EXIT_TOOL_FAILED)

    github_token = os.environ.get("TOOL_INSTALLER_GITHUB_TOKEN", "").strip()
    if not github_token:
        typer.echo("FATAL: set TOOL_INSTALLER_GITHUB_TOKEN, or authenticate gh.", err=True)
        raise typer.Exit(EXIT_TOOL_FAILED)

    # 1. THE WINDOW, FROM PRODUCTION. Unreadable is a refusal and never a default: no fallback
    #    hours exist, because a default would be this program deciding, from a policy it could
    #    not read, that now is a fine time to replace the tool that filters every command here.

    try:
        with open_policy_client(
            base_url=base_url, credential_key_id=policy_key, token=policy_token
        ) as policy_client:
            window = window_from_policy(policy_client.factory_policy())
    except PolicyUrlError as error:
        typer.echo(f"FATAL: {error}", err=True)
        raise typer.Exit(EXIT_TOOL_FAILED) from error
    except (PolicyReadError, WindowUnreadable) as error:
        typer.echo(f"UNUSABLE: the change window could not be read: {error}", err=True)
        raise typer.Exit(EXIT_UNUSABLE) from error

    in_window = window.permits(_now())
    if dry_run and install:
        typer.echo("--dry-run: reporting only; --install is ignored and nothing will be replaced.")
    typer.echo(
        f"change window {window.start.isoformat(timespec='minutes')}-"
        f"{window.end.isoformat(timespec='minutes')} {window.timezone}: "
        f"{'open' if in_window else 'closed'}"
    )

    # 2 and 3. WHAT IS AVAILABLE AND WHAT IS INSTALLED.
    try:
        with GitHubReader(token=github_token) as reader:
            measured = [_measure(tool, reader, root, sites) for tool in TOOLS]
    except GitHubReadError as error:
        typer.echo(f"UNUSABLE: the fork's revision could not be read: {error}", err=True)
        raise typer.Exit(EXIT_UNUSABLE) from error

    rows: list[Installation] = []
    unusable = False
    for row in measured:
        # 4. NOTHING TO DO. Reported and filed anyway: a row saying the machine is current is what
        #    makes "this lane is watching that tool" a fact rather than an inference from silence.
        if row.state == STATE_CURRENT:
            rows.append(row)
            continue
        # 5. EVERY TERM MUST HOLD, and the conjunction is the whole gate: `--install` says the
        #    operator permits acting, the window says not now, and neither substitutes for the
        #    other. There is no override -- an out-of-hours install is a person running one
        #    command, which is honest about being a human act.
        #
        #    `--dry-run` IS THE THIRD TERM, and it belongs here rather than only at the filing
        #    step. Consulted only there, `--install --dry-run` inside the window replaced the
        #    binary and recorded nothing -- and the launcher's dead-man switch does not arm under
        #    `--dry-run` either, so the one act this lane exists to record happened with neither
        #    an observation nor a liveness ping. A flag documented as "touches nothing" must not
        #    be the one flag that makes an act invisible.
        if not (install and in_window and not dry_run):
            rows.append(dataclasses.replace(row, action=ACTION_NOT_PERMITTED))
            continue
        try:
            # WHICHEVER TOOL WOULD DO THE INSTALL, RESOLVED LAZILY AND HERE. Both raise the same
            # error type deliberately: "this lane cannot reach the thing that would act" is one
            # condition with one exit code, and two branches for it would be two places to keep
            # agreeing. Neither is looked for until a row is about to be acted on.
            actor: Cargo | Path = (
                resolve_cargo() if isinstance(row.tool.install, CargoInstall) else resolve_claude()
            )
        except ToolchainError as error:
            # THE MEASUREMENT IS ALREADY COMPLETE, so it is FILED before the pass refuses. Raising
            # straight out of this loop threw away a finished "this machine is behind" finding at
            # exactly the moment the lane cannot fix itself -- and since the scheduled pass always
            # passes `--install`, a toolchain that drifted out of reach would have made every
            # night exit 2 saying nothing. The pass still ends at EXIT_UNUSABLE; it simply says
            # what it learned first.
            typer.echo(f"UNUSABLE: no usable toolchain: {error}", err=True)
            rows.append(dataclasses.replace(row, action=ACTION_NOT_PERMITTED, detail=str(error)))
            unusable = True
            continue
        # 6-9. BACK UP, INSTALL, PROVE, RESTORE ON FAILURE.
        #
        # The backup is DURABLE and named for the version going out -- never a temporary
        # directory. The in-pass rollback covers a proof that fails; this covers the case the
        # probe cannot see, a binary that passes every check and misbehaves in use hours later,
        # and it is only useful the next morning if it survived the pass. `rtk-0.42.4` sitting in
        # that directory since June is the precedent, and it is what a person reaches for.
        # An absent installed version means a first install, which has no outgoing artifact to
        # name -- `first` rather than a version that does not exist.
        #
        # THE PLUGIN ROW HAS NO BACKUP DIRECTORY, and that is not an omission. Its outgoing
        # artifacts are a commit in each of two git repositories and the cache directory the
        # previous version already occupies -- all three already durable, all three restored by
        # the rollback, and the newest-other cache directory deliberately left unpruned for
        # exactly the case a copied binary covers on the cargo side.
        #
        # DISPATCHED ON THE RESOLVED ACTOR rather than on the row a second time: a cargo toolchain
        # performs the cargo install and a `claude` executable performs the plugin install, so the
        # value that was resolved for this row IS the discriminator, and the two readings cannot
        # drift apart the way a repeated `isinstance` on the row could.
        outgoing = row.installed_version or "first"
        outcome = (
            install_and_prove(
                tool=row.tool,
                cargo=actor,
                install_root=root,
                backup_root=backups / f"{row.tool.name}-{outgoing}",
            )
            if isinstance(actor, Cargo)
            else install_and_prove_plugin(
                tool=row.tool,
                claude=actor,
                sites=sites,
                head_revision=row.available_revision,
            )
        )
        rows.append(_acted(row, outcome.action, tuple(outcome.probes), outcome.detail, root, sites))

    for row in rows:
        typer.echo(summary_of(row))

    # 10. RECORD, EITHER WAY.
    unfiled = 0
    if dry_run:
        typer.echo(f"dry run: {len(rows)} rows not filed")
    else:
        key_id = os.environ.get("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "").strip()
        bearer = os.environ.get("ORCHESTRATOR_API_TOKEN", "").strip()
        if not (key_id and bearer):
            typer.echo("FATAL: the observer credential is not configured.", err=True)
            raise typer.Exit(EXIT_TOOL_FAILED)
        try:
            with open_client(base_url=base_url, credential_key_id=key_id, token=bearer) as client:
                for row in rows:
                    try:
                        client.record_observation(installation_observation(row))
                    except ObservationWriteError as error:
                        typer.echo(f"!! {row.tool.name} row not filed: {error}", err=True)
                        unfiled += 1
        except UnusableEndpointError as error:
            typer.echo(f"FATAL: {error}", err=True)
            raise typer.Exit(EXIT_TOOL_FAILED) from error

    failed = [row for row in rows if row.action in FAILING_ACTIONS]
    behind = [row for row in rows if row.state != STATE_CURRENT]
    typer.echo(
        f"{len(rows)} tools, {len(behind)} not current, {len(failed)} failed, {unfiled} unfiled"
    )
    if unfiled or unusable:
        raise typer.Exit(EXIT_UNUSABLE)
    if failed:
        raise typer.Exit(EXIT_FOUND)
    raise typer.Exit(EXIT_OK)


# NO `run()` WRAPPER AND NO `__main__` BLOCK. `pyproject.toml` registers this module's `app` as
# the console script, so a wrapper would be reachable only through `python -m` -- and the obvious
# one, `app(standalone_mode=False)`, does not honour the exit contract documented above: under
# that flag Click RE-RAISES a usage error instead of returning a code, so a mistyped option would
# leave a traceback where a launcher expects 1. One entry point, one contract.
