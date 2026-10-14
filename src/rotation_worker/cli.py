"""`rotation-worker`: the credential-rotation executor's commands.

`run` is the service: one pass over the ready rotation units, started on a timer by the VM's
init system. `seed-canary` creates the canary credential's keeper for ADR-0055 increment 4's
end-to-end run, and `mint-bearer` the executor's own WORKER bearer.
`compare-destinations <credential>` is read-only. It fetches intent-packages and the registry
repositories into the executor's mirrors and prints the registry's destinations, ready to paste
into a package, then compares them with the standing package's, at each repository's `main`.
Exit codes: 0 equal, 1 different (the package needs a revision before approval), 2 unreadable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import secrets
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from rotation_worker.adapters import ADAPTERS, AdapterContext, fake
from rotation_worker.compare import Comparison, report
from rotation_worker.config import DEFAULT_PATH, Config, ConfigError, load, read_root_token
from rotation_worker.destinations import DestinationError
from rotation_worker.git_mirror import MirrorError, RepoMirror
from rotation_worker.logs import configure
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.registry import (
    PACKAGES_REPOSITORY,
    RegistryError,
    find_credential,
    latest_package_destinations,
)
from rotation_worker.secret_store import BwsStore, SecretStore
from rotation_worker.values import Value
from rotation_worker.worker import Setup, run_pass

EXIT_EQUAL: Final = 0
EXIT_DIFFERENT: Final = 1
EXIT_UNREADABLE: Final = 2
CANARY_KEEPER: Final = "rotation-canary keeper"
BEARER_KEY: Final = "rotation-executor WORKER bearer"
BEARER_BYTES: Final = 48
LOG = logging.getLogger("rotation_worker")


def default_mirror_root() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "rotation-worker" / "mirrors"


def compare_destinations(credential_id: str, mirror: RepoMirror) -> int:
    try:
        entry = find_credential(mirror, credential_id)
        registry = entry.destinations
        print(f"credential {credential_id}")
        print(f"registry  {entry.repository}@{entry.commit[:12]}, as a package lists them:")
        for destination in registry:
            print(f"  - '{destination}'")
        package = latest_package_destinations(mirror, credential_id)
    except (MirrorError, RegistryError, DestinationError) as error:
        print(f"compare-destinations: {error}", file=sys.stderr)
        return EXIT_UNREADABLE
    except Exception as error:
        # Exit 1 means "different" to the runbook, and Python's uncaught exit is 1 too, so an
        # unforeseen failure is reported here as unreadable rather than as a verdict.
        print(f"compare-destinations: unexpected {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_UNREADABLE
    comparison = Comparison(tuple(registry), tuple(package.destinations))
    print(f"package   {PACKAGES_REPOSITORY}@{package.commit[:12]} {package.path}")
    for line in report(comparison):
        print(line)
    return EXIT_EQUAL if comparison.equal else EXIT_DIFFERENT


def open_store(config: Config) -> SecretStore:
    return BwsStore(config.organization_id, read_root_token(config.root_token_file))


def build_setup(config: Config) -> Setup:
    """Log in to BWS with the root token, then read the executor's own bearer from it."""
    if config.bearer_secret_id is None:
        raise ConfigError("bearer_secret_id is needed to run; mint the bearer first")
    store = open_store(config)
    bearer = store.read(config.bearer_secret_id)
    return Setup(
        client=OrchestratorClient(config.orchestrator_url, config.credential_key_id, bearer),
        mirror=RepoMirror(config.mirror_root),
        store=store,
        work_project_id=config.work_project_id,
        state_dir=config.state_dir,
        source_revision=f"rotation-worker@{config.source_revision}",
        admin_secrets=config.admin_secrets,
        orchestrator_url=config.orchestrator_url,
        coolify_url=config.coolify_url,
        orchestrator_application=config.orchestrator_application,
        executor_key_id=config.credential_key_id,
    )


def run(config: Config) -> int:
    """One pass. The VM's init system runs it on a timer, as launchd runs the other lanes."""
    setup = build_setup(config)
    LOG.info("started", extra={"revision": config.source_revision})
    submitted = run_pass(setup)
    LOG.info("pass_finished", extra={"submitted": submitted})
    return 0


def seed_canary(config: Config) -> int:
    """Create the canary credential's keeper in `Rotation / Work`, holding a fake-minted value."""
    store = open_store(config)
    existing = store.find(config.work_project_id, CANARY_KEEPER)
    if existing is None:
        context = AdapterContext(config.state_dir, store, admin_secret_id=None)
        value = ADAPTERS[fake.NAME].build(context).mint()
        existing = store.create(config.work_project_id, CANARY_KEEPER, value)
    print(json.dumps({"secret_id": existing.id}))
    return 0


def mint_bearer(config: Config) -> int:
    """Create the executor's WORKER bearer in `Rotation / Work`; print its id and hash only.

    The value is generated here and goes straight to BWS. A person then moves the secret to
    `Rotation / Executor` (a move keeps its UUID), and the hash, which isn't secret, goes into the
    orchestrator's `ORCHESTRATOR_M2M_CREDENTIALS`.
    """
    store = open_store(config)
    if store.find(config.work_project_id, BEARER_KEY) is not None:
        print("mint-bearer: a bearer is already waiting in Rotation / Work", file=sys.stderr)
        return EXIT_UNREADABLE
    bearer = Value(secrets.token_urlsafe(BEARER_BYTES))
    created = store.create(config.work_project_id, BEARER_KEY, bearer)
    print_ = bearer.fingerprint
    print(
        json.dumps(
            {
                "secret_id": created.id,
                "token_hash": hashlib.sha256(bearer.reveal().encode()).hexdigest(),
                "sha256_prefix": print_.sha256_prefix,
                "length": print_.length,
            }
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rotation-worker")
    commands = parser.add_subparsers(dest="command", required=True)
    comparing = commands.add_parser(
        "compare-destinations",
        help="print a credential's registry destinations beside its standing package's",
    )
    comparing.add_argument("credential_id")
    comparing.add_argument("--mirror-root", type=Path, default=None)
    running = commands.add_parser("run", help="one pass: claim and work ready rotation units")
    seeding = commands.add_parser("seed-canary", help="create the canary's keeper; print its id")
    minting = commands.add_parser(
        "mint-bearer", help="create the executor's WORKER bearer; print its id and hash"
    )
    for command in (running, seeding, minting):
        command.add_argument("--config", type=Path, default=DEFAULT_PATH)
    arguments = parser.parse_args(argv)
    if arguments.command == "compare-destinations":
        mirror = RepoMirror(arguments.mirror_root or default_mirror_root())
        return compare_destinations(arguments.credential_id, mirror)
    configure()
    config = load(arguments.config)
    if arguments.command == "run":
        return run(config)
    if arguments.command == "mint-bearer":
        return mint_bearer(config)
    return seed_canary(config)
