"""`rotation-worker`: the credential-rotation executor's commands.

`run` is the service: one pass over the ready rotation units, started on a timer by the VM's
init system, or by launchd for the Keychain instance. `seed-canary` creates the canary
credential's keeper for ADR-0055 increment 4's end-to-end run, and `mint-bearer` the executor's
own WORKER bearer. `stage <credential> --occurrence <o>` is a person's step on the Keychain
instance (amendment 12): it asks for a new BWS access token at a hidden prompt and stages it.
`read-consumers <credential> --stored-at <instant>` is read-only too: it asks each scheduled
reader's read, from the executor's VM, whether the reader has run since that instant, and prints
the answer and the latest run it found. Exit 0 when every reader answers, 2 when one can't be
read. `compare-destinations <credential>` is read-only. It fetches intent-packages and the registry
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
import re
import secrets
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from getpass import getpass
from pathlib import Path
from typing import Final

from rotation_worker.adapters import ADAPTERS, AdapterContext, fake
from rotation_worker.compare import Comparison, report
from rotation_worker.config import DEFAULT_PATH, Config, ConfigError, load, read_root_token
from rotation_worker.contract import Rotation
from rotation_worker.destinations import DestinationError, parse, render
from rotation_worker.git_mirror import MirrorError, RepoMirror
from rotation_worker.github_actions import RotationApp
from rotation_worker.logs import configure
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.readers import (
    Healthchecks,
    ReadError,
    ReadUnavailable,
    instant,
    scheduled_reads,
)
from rotation_worker.registry import (
    PACKAGES_REPOSITORY,
    RegistryError,
    find_credential,
    latest_package_destinations,
)
from rotation_worker.secret_store import BwsStore, KeychainStore, SecretStore, StoreError
from rotation_worker.values import Value
from rotation_worker.worker import Setup, consumer_reads, run_pass

EXIT_EQUAL: Final = 0
EXIT_DIFFERENT: Final = 1
EXIT_UNREADABLE: Final = 2
CANARY_KEEPER: Final = "rotation-canary keeper"
BEARER_KEY: Final = "rotation-executor WORKER bearer"
# The Keychain instance's bearer, an account under its work service.
KEYCHAIN_BEARER: Final = "worker-bearer"
# A BWS access token: `0.`, the access-key id, `.`, the client secret, `:`, the encryption key.
BWS_TOKEN: Final = re.compile(
    r"^0\.[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.[A-Za-z0-9]+:[A-Za-z0-9+/]+=*$"
)
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
    if config.store == "keychain":
        return KeychainStore()
    if config.organization_id is None or config.root_token_file is None:
        raise ConfigError("a BWS executor needs organization_id and root_token_file")
    return BwsStore(config.organization_id, read_root_token(config.root_token_file))


def secret_id(config: Config, name: str) -> str:
    """The store's id for a secret the configuration names: a BWS UUID, or a Keychain account
    under the instance's own service."""
    if config.store == "keychain":
        return render({"kind": "keychain", "service": config.work_project_id, "account": name})
    return name


def open_healthchecks(config: Config, store: SecretStore) -> Healthchecks | None:
    if config.healthchecks_key_id is None:
        return None
    key_id = secret_id(config, config.healthchecks_key_id)
    return Healthchecks(lambda: store.read(key_id))


def open_github_app(config: Config, store: SecretStore) -> RotationApp | None:
    app_id, key_id = config.github_app_id, config.github_app_key_id
    if app_id is None or key_id is None:
        return None
    return RotationApp(app_id, lambda: store.read_key(key_id))


def read_consumers(setup: Setup, credential_id: str, stored_at: datetime) -> int:
    """Each scheduled reader's answer for a store at `stored_at`. Reads only."""
    try:
        entry = find_credential(setup.mirror, credential_id)
        reads = scheduled_reads(entry.credential)
        keeper = setup.store.locate(parse(entry.keeper))
    except (MirrorError, RegistryError, DestinationError) as error:
        print(f"read-consumers: {error}", file=sys.stderr)
        return EXIT_UNREADABLE
    if keeper is None:
        print("read-consumers: the keeper isn't kept where this executor reads", file=sys.stderr)
        return EXIT_UNREADABLE
    consumers = consumer_reads(setup, reads, keeper)
    unreadable = 0
    for destination in sorted(reads):
        try:
            answer = consumers.answer(destination, stored_at)
        except (ReadError, ReadUnavailable, StoreError) as error:
            unreadable += 1
            print(f"unreadable  {destination}: {error}")
            continue
        verdict = "switched" if answer.switched else "not yet"
        last_run = answer.last_run.isoformat() if answer.last_run else "none"
        print(f"{verdict:<10}  {destination}  last run {last_run}")
    return EXIT_UNREADABLE if unreadable else EXIT_EQUAL


def build_setup(config: Config) -> Setup:
    """Log in to BWS with the root token, then read the executor's own bearer from it."""
    if config.bearer_secret_id is None:
        raise ConfigError("bearer_secret_id is needed to run; mint the bearer first")
    store = open_store(config)
    bearer = store.read(secret_id(config, config.bearer_secret_id))
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
        change_manager_application=config.change_manager_application,
        executor_key_id=config.credential_key_id,
        healthchecks=open_healthchecks(config, store),
        github_app=open_github_app(config, store),
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
    key = KEYCHAIN_BEARER if config.store == "keychain" else BEARER_KEY
    if store.find(config.work_project_id, key) is not None:
        print(f"mint-bearer: a bearer is already waiting as {key!r}", file=sys.stderr)
        return EXIT_UNREADABLE
    bearer = Value(secrets.token_urlsafe(BEARER_BYTES))
    created = store.create(config.work_project_id, key, bearer)
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


def stage(
    config: Config, credential_id: str, occurrence: str, ask: Callable[[str], str] = getpass
) -> int:
    """A person's stage step on the Keychain instance: the new token goes from a hidden prompt
    straight into the staging item, and only its fingerprint is printed, for `/review`."""
    if config.store != "keychain":
        print("stage: only the Keychain executor stages by prompt", file=sys.stderr)
        return EXIT_UNREADABLE
    store = open_store(config)
    rotation = Rotation(credential_id, occurrence, "", config.work_project_id, ())
    if store.find(config.work_project_id, rotation.staging_key) is not None:
        print(f"stage: {rotation.staging_key} is already staged", file=sys.stderr)
        return EXIT_UNREADABLE
    value = Value(ask("New BWS access token (hidden): ").strip())
    if not BWS_TOKEN.fullmatch(value.reveal()):
        print("stage: that isn't the shape of a BWS access token; nothing staged", file=sys.stderr)
        return EXIT_UNREADABLE
    store.create(config.work_project_id, rotation.staging_key, value)
    print_ = value.fingerprint
    print(json.dumps({"sha256_prefix": print_.sha256_prefix, "length": print_.length}))
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
    reading = commands.add_parser(
        "read-consumers",
        help="ask whether each scheduled reader of a credential has run since an instant",
    )
    reading.add_argument("credential_id")
    reading.add_argument("--stored-at", type=_instant, required=True)
    staging = commands.add_parser(
        "stage", help="stage a new BWS access token from a hidden prompt (Keychain executor)"
    )
    staging.add_argument("credential_id")
    staging.add_argument("--occurrence", required=True)
    for command in (running, seeding, minting, reading, staging):
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
    if arguments.command == "read-consumers":
        return read_consumers(build_setup(config), arguments.credential_id, arguments.stored_at)
    if arguments.command == "stage":
        return stage(config, arguments.credential_id, arguments.occurrence)
    return seed_canary(config)


def _instant(text: str) -> datetime:
    try:
        return instant(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{error}; give the zone, for example +00:00") from None
