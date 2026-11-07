#!/usr/bin/env python3
"""Deploy the orchestrator to production by its recorded procedure (SDS 1.1 item 7a).

The procedure is `docs/operations/deploy.md` (#305, "Binding the release", #61, #82): build,
migrate, swap, verify, bind. This program runs every step it is allowed to run, in order, and
refuses where the procedure says a deploy must not proceed. It is split into two phases because
the workspace rule routes every Coolify change and every host read through the infraops MCP
tools, which a local program cannot call. Between the phases the deploying session makes those
calls, which `prepare` prints exactly.

    uv run python -m scripts.deploy_orchestrator prepare --ref <sha|origin/main> --state <path>
        --coolify-health-check-enabled <bool> --coolify-health-check-path <path>
        [--label <label>] [--expect-schema <Model[.field]>]
    uv run python -m scripts.deploy_orchestrator prepare --resume --state <path>
    uv run python -m scripts.deploy_orchestrator recheck --state <path>
    uv run python -m scripts.deploy_orchestrator finish --state <path>
        --observed-digest sha256:<64 hex>
    uv run python -m scripts.deploy_orchestrator lanes-prepare --lane <lane> <change> <reads>
    uv run python -m scripts.deploy_orchestrator lanes-recheck --lane <lane> <change>
    uv run python -m scripts.deploy_orchestrator lanes-finish --lane <lane> <change> <reads>
        [--restored]

where <change> is exactly one of `--tag sha-<40 hex>`, `--schedule <true|false>` or
`--add --tag sha-<40 hex>` (ADR-0059 decision 1 and Amendment 1).

`prepare` refuses a build whose health checks read `/health/ready` (migrate-first would become
an outage), refuses while a unit is live or a dispatched run has not been claimed yet, and
refuses a database already ahead of the outgoing image. It records the pre-swap revision, saves
its state, builds the image with the `Release image` workflow, reads the pushed digest from that
run, and decides whether a migration is needed. A re-run resumes the same build. `recheck`, run
immediately before the swap, requires readiness to prove the migration ran (503 drift with one,
200 without), repeats the nothing-live check, and confirms production still serves the recorded
pre-swap revision. `finish` refuses unless the
running container's digest is the pushed one. It then waits for `/health/live` to serve the
built commit, requires `/health/ready` and the OpenAPI document, and runs `image-release bind`
with the pre-swap revision `prepare` recorded, never a fresh read. `prepare` also refuses while a
registered lane runs a tag other than the served one or any lane-phase run is unfinished.

The three lane phases change one lane's Coolify resource, `sds-lane-<lane>`, the same way:
`lanes-prepare` waits for the lane's quiet slot across runs, records the change in the lane's run
file and prints the session's exact infraops calls and their inverse; `lanes-recheck` repeats the
quiet-slot reads immediately before the session's writes; `lanes-finish` checks the session's reads
of the redeployed container. The command creates and deletes nothing, holds no Coolify credential,
and sends no alert. Run files live under `LANES_STATE_DIR`, one chain per lane.

Exit codes: 0 done; 2 a condition (a live unit, a digest mismatch, a revision not served, a
binder condition, a lane's quiet slot unmet); 3 unreadable or refused; 4 a lane's quiet-slot wait
timed out; 1 usage. Every command prints one JSON object on stdout. `prepare` and the lane phases
also print the next steps on stderr.

Bearers are fetched in-process from BWS and passed to the binder in its environment, never in
argv. Nothing here prints a credential.
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid as uuid_module
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

import httpx
import yaml

from scripts.compute_image_tags import LANE_IMAGE, derivable_tag, readable_tag

REPO_ROOT = Path(__file__).resolve().parent.parent

REPOSITORY = "AlobarQuest/orchestrator"
API_BASE = "https://sds.alobar.net"
APPLICATION_UUID = "eqj5l7k705fhi12x9i74fqf0"
RELEASE_WORKFLOW = "release-image.yml"
WORKFLOW_FILE_REF = "main"
SYSTEM_BEARER_UUID = "221a48d5-3f29-4898-b300-b4820140c880"  # orchestrator-system
VERIFIER_BEARER_UUID = "660d5846-abcb-4751-be86-b483012899eb"  # orchestrator-verifier
SYSTEM_KEY_ID = "orchestrator-system"
USER_AGENT = "deploy-orchestrator/1 (+AlobarQuest/orchestrator)"
READINESS_PATH = "/health/ready"
DRIFT = "migration_drift"
IMAGE_NAME = "ghcr.io/alobarquest/orchestrator"
MIGRATIONS_DIR = "migrations/versions/"
HEALTH_CHECK_FILES = ("Dockerfile", "docker-compose.yml")

# A restart in these states strands the unit with its attempt spent: its runner calls back at the
# end of its run (#82). `draft` is reported but does not block, because a draft is inert across a
# restart (Devon, 2026-10-06).
LIVE_STATES = frozenset({"claimed", "executing", "submitted", "verifying"})
REPORTED_STATES = frozenset({"draft"})
DISPATCHED_ACTION = "dispatch.dispatched"
CLAIMED_STATE = "claimed"

EXIT_OK, EXIT_USAGE, EXIT_CONDITION, EXIT_REFUSED, EXIT_TIMED_OUT = 0, 1, 2, 3, 4
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
# The verify steps' `env:` echoes. The orchestrator's step logs `DIGEST:`, `REVISION:` and
# `SHA_TAG:`, the lane image's logs the same names behind `LANE_` (ADR-0059 amendment 1, item 7).
# No name may be preceded by a word character or `-`, so each reader matches only its own lines.
LOG_PREFIX_ORCHESTRATOR, LOG_PREFIX_LANES = "", "LANE_"


def _log_line(prefix: str, name: str, value: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){prefix}{name}: ({value})")


STATE_SCHEMA = 1

RUN_APPEAR_SECONDS = 120
BUILD_SECONDS = 45 * 60
SERVE_SECONDS = 300
POLL_SECONDS = 10
GH_RETRIES = 3


class Condition(Exception):
    """A deploy condition is unmet. The world may change; re-run when it does. Exit 2."""


class Refused(Exception):
    """A state this program cannot read, or one it will not deploy from. Exit 3."""


class Usage(Exception):
    """The command line is malformed. Exit 1."""


class TimedOut(Exception):
    """A lane's quiet-slot wait passed its recorded deadline (ADR-0059 amendment 1). Exit 4."""


# --------------------------------------------------------------------------------------------
# the world: every seam a test replaces
# --------------------------------------------------------------------------------------------

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str], *, env: dict[str, str] | None = None, timeout: float = 600):
    return subprocess.run(
        list(argv), capture_output=True, text=True, timeout=timeout, env=env, check=False
    )


@dataclass
class World:
    run: Runner = _run
    bearer: Callable[[str], str] = field(default=lambda uuid: _bws_value(uuid))
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    checkout: Path = REPO_ROOT
    binder: Path = Path(sys.executable).parent / "image-release"
    # The lane phases' seams: wall-clock time (a deadline outlives the process, which `clock`
    # can't measure), the lanes' run files, and the Healthchecks check listing.
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    lanes_state_dir: Path = field(default_factory=lambda: LANES_STATE_DIR)
    checks: Callable[[], list[dict[str, Any]]] = field(default=lambda: _healthchecks_listing())


def _bws_value(uuid: str) -> str:
    """One BWS secret, parsed in-process. The value never reaches argv or stdout."""
    access = os.environ.get("BWS_ACCESS_TOKEN") or _keychain_access_token()
    if not access:
        raise Refused("no BWS access token (Keychain service=Claude account=BWS_ACCESS_TOKEN_SDS)")
    environment = {
        k: v for k, v in os.environ.items() if k not in {"FORCE_COLOR", "CLICOLOR_FORCE"}
    }
    environment["BWS_ACCESS_TOKEN"] = access
    try:
        completed = _run(
            ["bws", "secret", "get", uuid, "--output", "json", "--color", "no"],
            env=environment,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Refused(f"bws unavailable: {error}") from error
    if completed.returncode != 0:
        raise Refused(f"bws secret get {uuid} exited {completed.returncode}")
    try:
        return json.loads(completed.stdout)["value"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise Refused(f"could not parse the bws response for {uuid}") from error


def _keychain_access_token() -> str:
    try:
        return _run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                "Claude",
                "-a",
                "BWS_ACCESS_TOKEN_SDS",
                "-w",
            ],
            timeout=30,
        ).stdout.strip()
    except OSError, subprocess.SubprocessError:
        return ""


def _get(world: World, path: str, bearer: str | None = None) -> httpx.Response:
    headers = {"User-Agent": USER_AGENT}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
        headers["X-Credential-Key-Id"] = SYSTEM_KEY_ID
    with httpx.Client(base_url=API_BASE, transport=world.transport, timeout=60) as client:
        return client.get(path, headers=headers)


def _get_json(world: World, path: str, bearer: str | None = None) -> Any:
    try:
        response = _get(world, path, bearer)
    except httpx.HTTPError as error:
        raise Refused(f"GET {path} failed: {error}") from error
    if response.status_code != 200:
        raise Refused(f"GET {path} answered HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as error:
        raise Refused(f"GET {path} did not answer JSON") from error


def _git(world: World, *args: str) -> str:
    completed = world.run(["git", "-C", str(world.checkout), *args])
    if completed.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def _gh(world: World, *args: str) -> str:
    completed = world.run(["gh", *args])
    if completed.returncode != 0:
        raise Refused(f"gh {' '.join(args[:3])} failed: {completed.stderr.strip()}")
    return completed.stdout


def _gh_json(world: World, *args: str) -> Any:
    try:
        return json.loads(_gh(world, *args))
    except ValueError as error:
        raise Refused(f"gh {' '.join(args[:3])} did not answer JSON") from error


# --------------------------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------------------------


@dataclass
class State:
    built_commit: str
    previous_commit: str
    label: str
    tag: str
    pushed_digest: str
    workflow_run_url: str
    migrations: list[str]
    expect_schema: list[str]
    runs_before: int = 0
    run_id: int | None = None
    schema: int = STATE_SCHEMA
    # The lane image the same `Release image` run pushed. Defaulted, so a state file written
    # before the lane image existed still loads.
    lane_digest: str = ""

    @property
    def tag_name(self) -> str:
        """The tag alone, as Coolify's `docker_registry_image_tag` and the binder take it."""
        return self.tag.rsplit(":", 1)[1]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")

    @classmethod
    def load(cls, path: Path, *, complete: bool = True) -> State:
        """`complete=False` admits a prepare whose build has not been read yet (resume only)."""
        try:
            raw = json.loads(path.read_text())
            state = cls(**raw)
        except (OSError, ValueError, TypeError) as error:
            raise Refused(f"the state file {path} is missing or malformed: {error}") from error
        if state.schema != STATE_SCHEMA:
            raise Refused(f"the state file {path} has schema {state.schema}")
        shas = (state.built_commit, state.previous_commit)
        if not all(isinstance(s, str) and FULL_SHA.match(s) for s in shas):
            raise Refused(f"the state file {path} does not hold full shas")
        if state.pushed_digest == "" and not complete:
            return state
        if not isinstance(state.pushed_digest, str) or not DIGEST.match(state.pushed_digest):
            raise Refused(f"the state file {path} holds no pushed digest; resume prepare first")
        return state


# --------------------------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------------------------


def readiness_consumers(files: dict[str, str]) -> list[str]:
    """Every health-check source that names `/health/ready`. Migrate-first needs none (#61)."""
    return sorted(name for name, text in files.items() if READINESS_PATH in text)


def coolify_consults_readiness(enabled: bool, path: str) -> bool:
    return enabled and READINESS_PATH in path


def resolve_commit(world: World, ref: str) -> str:
    _git(world, "fetch", "-q", "origin")
    sha = _git(world, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    if not FULL_SHA.match(sha):
        raise Refused(f"{ref} did not resolve to a full sha")
    if (
        world.run(
            ["git", "-C", str(world.checkout), "merge-base", "--is-ancestor", sha, "origin/main"]
        ).returncode
        != 0
    ):
        raise Refused(f"{sha} is not on origin/main; only a merged commit is deployed")
    return sha


def refuse_readiness_health_checks(world: World, sha: str, enabled: bool, path: str) -> None:
    files = {name: _git(world, "show", f"{sha}:{name}") for name in HEALTH_CHECK_FILES}
    consumers = readiness_consumers(files)
    if coolify_consults_readiness(enabled, path):
        consumers.append("Coolify health check")
    if consumers:
        raise Refused(
            f"{', '.join(consumers)} at {sha[:7]} reads {READINESS_PATH}: migrating before the "
            "swap would make the running container unhealthy and kill it. Re-decide the "
            "migration order first (deploy.md #61)."
        )


def dispatched_and_unclaimed(history: list[dict[str, Any]]) -> bool:
    """A dispatch the runner has not claimed yet: its run is starting in the target repository."""
    pending = False
    for event in history:
        if event.get("action") == DISPATCHED_ACTION:
            pending = True
        elif event.get("to_state") == CLAIMED_STATE:
            pending = False
    return pending


def precheck(world: World, token: str) -> dict[str, list[str]]:
    """Refuse while a restart could strand a unit (#82). Returns what was seen."""
    rows = _get_json(world, "/api/v1/status-ledger?include_inactive=true", token)
    keys = {"unit_id", "unit_key", "unit_state"}
    if not isinstance(rows, list) or not all(
        isinstance(r, dict) and keys <= r.keys() for r in rows
    ):
        raise Refused("the status ledger did not answer rows carrying unit_id/key/state")
    live = sorted(
        f"{r['unit_key']} ({r['unit_state']})" for r in rows if r["unit_state"] in LIVE_STATES
    )
    if live:
        raise Condition(f"units are live, so a restart would strand them: {', '.join(live)}")
    dispatched = []
    for row in rows:
        if row["unit_state"] != "ready":
            continue
        history = _get_json(world, f"/api/v1/work-units/{row['unit_id']}/history", token)
        if not isinstance(history, list):
            raise Refused(f"the history of {row['unit_key']} is not a list")
        if dispatched_and_unclaimed(history):
            dispatched.append(row["unit_key"])
    if dispatched:
        raise Condition(
            "a dispatched run has not claimed its unit yet, so it is starting now: "
            + ", ".join(sorted(dispatched))
        )
    return {"reported": sorted(r["unit_key"] for r in rows if r["unit_state"] in REPORTED_STATES)}


def pre_swap_revision(world: World) -> str:
    body = _get_json(world, "/health/live")
    revision = body.get("revision") if isinstance(body, dict) else None
    if not isinstance(revision, str) or not FULL_SHA.match(revision):
        raise Refused(
            f"/health/live reports revision {revision!r}, so the outgoing image "
            "cannot state what it carries"
        )
    return revision


def migrations_between(world: World, previous: str, built: str) -> list[str]:
    for sha in (previous, built):
        if (
            world.run(
                ["git", "-C", str(world.checkout), "cat-file", "-e", f"{sha}^{{commit}}"]
            ).returncode
            != 0
        ):
            raise Refused(f"{sha} is not in the checkout; fetch and re-run")
    if (
        world.run(
            ["git", "-C", str(world.checkout), "merge-base", "--is-ancestor", previous, built]
        ).returncode
        != 0
    ):
        raise Refused(
            f"production's {previous} is not an ancestor of {built}; a rollback is "
            "not this procedure"
        )
    changed = _git(world, "diff", "--name-only", previous, built, "--", MIGRATIONS_DIR)
    return sorted(line for line in changed.splitlines() if line.endswith(".py"))


def _deadline_poll(world: World, seconds: float, probe: Callable[[], Any]) -> Any:
    deadline = world.clock() + seconds
    while True:
        found = probe()
        if found is not None:
            return found
        if world.clock() >= deadline:
            return None
        world.sleep(POLL_SECONDS)


def _tolerant(probe: Callable[[], Any]) -> Callable[[], Any]:
    """A probe that survives up to GH_RETRIES consecutive failed `gh` calls while polling."""
    failures = 0

    def attempt() -> Any:
        nonlocal failures
        try:
            found = probe()
        except Refused:
            failures += 1
            if failures > GH_RETRIES:
                raise
            return None
        failures = 0
        return found

    return attempt


def release_runs(world: World) -> list[dict[str, Any]]:
    runs = _gh_json(
        world,
        "run",
        "list",
        "-R",
        REPOSITORY,
        "--workflow",
        RELEASE_WORKFLOW,
        "--event",
        "workflow_dispatch",
        "--limit",
        "20",
        "--json",
        "databaseId,displayTitle,url",
    )
    if not isinstance(runs, list) or not all(
        isinstance(r, dict) and isinstance(r.get("databaseId"), int) for r in runs
    ):
        raise Refused("gh run list did not answer runs with ids")
    return runs


def run_title(sha: str) -> str:
    """The workflow's `run-name`, which is how a dispatched run is attributed to its commit."""
    return f"Release image {sha}"


def find_run(world: World, state: State) -> dict[str, Any]:
    """The first run newer than the dispatch whose title names this commit."""

    def appeared() -> dict[str, Any] | None:
        mine = [
            r
            for r in release_runs(world)
            if r["databaseId"] > state.runs_before
            and r.get("displayTitle") == run_title(state.built_commit)
        ]
        return min(mine, key=lambda r: r["databaseId"]) if mine else None

    run = _deadline_poll(world, RUN_APPEAR_SECONDS, _tolerant(appeared))
    if run is None:
        raise Refused(
            f"no Release image run titled {run_title(state.built_commit)!r} appeared. If the "
            "dispatch never happened, remove the state file and prepare again."
        )
    return run


def wait_for_build(world: World, run_id: int) -> None:
    def concluded() -> dict[str, Any] | None:
        view = _gh_json(
            world, "run", "view", str(run_id), "-R", REPOSITORY, "--json", "status,conclusion,url"
        )
        return view if isinstance(view, dict) and view.get("status") == "completed" else None

    view = _deadline_poll(world, BUILD_SECONDS, _tolerant(concluded))
    if view is None:
        raise Refused(f"Release image run {run_id} did not finish in {BUILD_SECONDS}s; resume")
    if view.get("conclusion") != "success":
        raise Refused(f"Release image run {view.get('url')} concluded {view.get('conclusion')}")


def logged_digest(log: str, run_id: int, sha: str, prefix: str, image: str) -> str:
    """The digest one image's verify step logged, with the revision and tag it names.

    Refuses more than one distinct digest, and a revision or tag that isn't `sha`'s on `image`.
    """
    digests = set(_log_line(prefix, "DIGEST", r"sha256:[0-9a-f]{64}\b").findall(log))
    revisions = set(_log_line(prefix, "REVISION", r"[0-9a-f]{40}\b").findall(log))
    sha_tags = set(_log_line(prefix, "SHA_TAG", r"\S+").findall(log))
    label = f"{prefix}DIGEST"
    if len(digests) != 1:
        raise Refused(f"run {run_id} logged {len(digests)} distinct {label} values, not one")
    if revisions != {sha} or sha_tags != {derivable_tag(sha, image)}:
        raise Refused(f"run {run_id} did not build {sha}; it is somebody else's run")
    return digests.pop()


def _run_log(world: World, run_id: int) -> str:
    return _gh(world, "run", "view", str(run_id), "-R", REPOSITORY, "--log")


def pushed_digest(world: World, run_id: int, sha: str) -> str:
    """The orchestrator digest the run pushed, read from its verify step's `DIGEST:` line."""
    return logged_digest(_run_log(world, run_id), run_id, sha, LOG_PREFIX_ORCHESTRATOR, IMAGE_NAME)


def pushed_lane_digest(world: World, run_id: int, sha: str) -> str:
    """The lane image digest the same run pushed, read from its `LANE_DIGEST:` line."""
    return logged_digest(_run_log(world, run_id), run_id, sha, LOG_PREFIX_LANES, LANE_IMAGE)


def readiness(world: World) -> tuple[int, str | None]:
    """`/health/ready`'s status and reason. 200 only when the DB is at the code's single head."""
    try:
        response = _get(world, READINESS_PATH)
    except httpx.HTTPError as error:
        raise Refused(f"GET {READINESS_PATH} failed: {error}") from error
    try:
        body = response.json()
        reason = body.get("reason") if isinstance(body, dict) else None
    except ValueError:
        reason = None
    return response.status_code, reason


def require_outgoing_ready(world: World) -> None:
    """The DB must be at the outgoing image's head, or the drift `recheck` reads means nothing."""
    status, reason = readiness(world)
    if status == 200:
        return
    if reason == DRIFT:
        raise Refused(
            f"{READINESS_PATH} answers 503 {DRIFT} BEFORE this deploy: the database is not at the "
            "outgoing image's head (a migration applied without its swap, or a hand change). "
            "Resolve that first; this deploy could not tell its own migration from it."
        )
    raise Refused(f"{READINESS_PATH} answered {status} ({reason}) before the deploy")


def _prepare(args: argparse.Namespace, world: World) -> dict[str, Any]:
    state_path = Path(args.state)
    if state_path.exists() or args.resume:
        return _resume(state_path, args.ref, world)
    if args.ref is None:
        raise Usage("--ref is required unless --resume")
    sha = resolve_commit(world, args.ref)
    enabled, path = args.coolify_health_check_enabled, args.coolify_health_check_path
    if enabled is None or path is None:
        raise Usage("--coolify-health-check-enabled and --coolify-health-check-path are required")
    refuse_readiness_health_checks(world, sha, enabled, path)
    seen = precheck(world, world.bearer(SYSTEM_BEARER_UUID))
    previous = pre_swap_revision(world)
    if previous == sha:
        raise Condition(f"production already serves {sha}")
    require_lanes_current(world, previous, sha)
    migrations = migrations_between(world, previous, sha)
    # The migration runs under the OUTGOING image, so its health check is the one that would fire.
    refuse_readiness_health_checks(world, previous, enabled, path)
    require_outgoing_ready(world)
    state = State(
        built_commit=sha,
        previous_commit=previous,
        label=args.label,
        tag=readable_tag(sha, args.label),
        pushed_digest="",
        workflow_run_url="",
        migrations=migrations,
        expect_schema=list(args.expect_schema),
        runs_before=max((r["databaseId"] for r in release_runs(world)), default=0),
    )
    # Saved BEFORE the dispatch: the workflow refuses to rebuild a tag, so from here on a re-run
    # must find this run rather than start another.
    state.save(state_path)
    _gh(
        world,
        "workflow",
        "run",
        RELEASE_WORKFLOW,
        "-R",
        REPOSITORY,
        "--ref",
        WORKFLOW_FILE_REF,
        "-f",
        f"ref={sha}",
        "-f",
        f"label={args.label}",
    )
    return {"resumed": False, "draft_units": seen["reported"], **_build(world, state, state_path)}


def _build(world: World, state: State, state_path: Path) -> dict[str, Any]:
    """Find, wait for and read the dispatched run, saving each fact as it is learned."""
    run_id = state.run_id
    if run_id is None:
        run = find_run(world, state)
        run_id = state.run_id = run["databaseId"]
        state.workflow_run_url = str(run.get("url", ""))
        state.save(state_path)
    wait_for_build(world, run_id)
    log = _run_log(world, run_id)
    sha = state.built_commit
    state.pushed_digest = logged_digest(log, run_id, sha, LOG_PREFIX_ORCHESTRATOR, IMAGE_NAME)
    state.lane_digest = logged_digest(log, run_id, sha, LOG_PREFIX_LANES, LANE_IMAGE)
    state.save(state_path)
    print(next_steps(state, state_path), file=sys.stderr)
    return {"result": "prepared", **asdict(state)}


def _resume(state_path: Path, ref: str | None, world: World) -> dict[str, Any]:
    """Continue an earlier prepare. It never dispatches: that run is already this deploy's."""
    state = State.load(state_path, complete=False)
    if ref is not None:
        sha = _git(world, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
        if sha != state.built_commit:
            raise Refused(
                f"{state_path} records {state.built_commit}, not {sha}; use a new --state"
            )
    if state.pushed_digest:
        print(next_steps(state, state_path), file=sys.stderr)
        return {"result": "prepared", "resumed": True, **asdict(state)}
    return {"resumed": True, **_build(world, state, state_path)}


def _exactly_one(variable: str, what: str) -> str:
    return (
        f'[ "$(printf \'%s\\n\' "${variable}" | grep -c .)" -eq 1 ] || '
        f'{{ echo "expected exactly one {what}, found: ${variable}" >&2; exit 1; }}'
    )


def migration_command(state: State) -> str:
    """Alembic from the NEW image, on the coolify network; the DB URL never leaves the host.

    Run before the swap, so the one running container is the outgoing one.
    """
    image = f"{IMAGE_NAME}@{state.pushed_digest}"
    return (
        f"C=$(docker ps -q --filter name={APPLICATION_UUID}); "
        f"{_exactly_one('C', 'running container')}; "
        "DB=$(docker inspect \"$C\" --format '{{range .Config.Env}}{{println .}}{{end}}' "
        "| grep '^ORCHESTRATOR_DATABASE_URL=' | cut -d= -f2-); "
        '[ -n "$DB" ] || { echo "no ORCHESTRATOR_DATABASE_URL in the running container" >&2; '
        "exit 1; }; "
        f'docker run --rm --network coolify -e ORCHESTRATOR_DATABASE_URL="$DB" {image} '
        "sh -c 'cd /app && .venv/bin/alembic upgrade head && .venv/bin/alembic current "
        "&& .venv/bin/alembic heads'"
    )


def repo_digest_command(state: State) -> str:
    """Container -> image -> RepoDigests (#52), for the container running the NEW tag only."""
    return (
        f"C=$(docker ps -q --filter name={APPLICATION_UUID} "
        f"--filter ancestor={IMAGE_NAME}:{state.tag_name}); "
        f"{_exactly_one('C', f'container running {state.tag_name}')}; "
        'docker image inspect "$(docker inspect "$C" --format '
        "'{{.Image}}')\" --format '{{range .RepoDigests}}{{println .}}{{end}}'"
    )


def next_steps(state: State, state_path: Path) -> str:
    recheck = f"uv run python -m scripts.deploy_orchestrator recheck --state {state_path}"
    lines = [
        f"Built {state.built_commit} as {state.tag} ({state.pushed_digest}).",
        f"Pre-swap revision recorded: {state.previous_commit}.",
        "Make these infraops calls IN THIS ORDER (deploy.md #305):",
        "",
        f'1. coolify_update_application(uuid="{APPLICATION_UUID}", instance="prod", '
        f'docker_registry_image_tag="{state.tag_name}")',
        "   From here Coolify's tag names the NEW image: any Coolify restart or redeploy of this",
        "   application swaps it in, whether or not the migration has run.",
    ]
    if state.migrations:
        lines += [
            f'2. Migrate ({", ".join(state.migrations)}) with vps_exec(instance="prod", '
            "timeout=300000) and this command:",
            "",
            migration_command(state),
            "",
            "   Confirm the printed `current` equals `heads`. The old image now answers",
            f"   {READINESS_PATH} 503 {DRIFT}; no health check reads it, so it keeps serving.",
        ]
    else:
        lines.append(
            f"2. No migration: nothing under {MIGRATIONS_DIR} changed since "
            f"{state.previous_commit[:7]}."
        )
    lines += [
        "3. Immediately before the swap, re-check. The first check is as old as the build:",
        f"   {recheck}",
        f'   Only if it exits 0: coolify_deploy(uuid="{APPLICATION_UUID}", instance="prod")',
        "   If it stops on a live unit after the migration, do NOT roll back: the old image",
        "   serves the migrated database, and only readiness (which nothing reads) reports it.",
        "   Wait for the unit to finish, then run recheck again.",
        "4. Once the deployment has finished, read the new container's digest with",
        '   vps_exec(instance="prod") and this command:',
        "",
        repo_digest_command(state),
        "",
        "5. From a checkout at the built commit, with its own venv:",
        f"   uv run python -m scripts.deploy_orchestrator finish --state {state_path} "
        "--observed-digest <the sha256:... after '@' in step 4>",
    ]
    return "\n".join(lines)


def require_migration_state(world: World, state: State) -> str:
    """Readiness proves the migration ran: 503 drift with migrations, 200 without (#61)."""
    status, reason = readiness(world)
    if state.migrations and (status, reason) == (503, DRIFT):
        return f"503 {DRIFT}: the migration ran and the outgoing image is behind it"
    if not state.migrations and status == 200:
        return "200: no migration in this deploy, and the database is at the outgoing head"
    expected = f"503 {DRIFT}" if state.migrations else "200"
    hint = " Run step 2 (the migration) first." if state.migrations and status == 200 else ""
    raise Refused(f"{READINESS_PATH} answered {status} ({reason}), not {expected}.{hint}")


def _recheck(args: argparse.Namespace, world: World) -> dict[str, Any]:
    """The prepare-time checks that age, run again immediately before the swap (#82)."""
    state = State.load(Path(args.state))
    serving = pre_swap_revision(world)
    if serving == state.built_commit:
        raise Condition(f"production already serves {serving}: already swapped, run finish")
    if serving != state.previous_commit:
        raise Condition(
            f"production serves {serving}, not the recorded pre-swap {state.previous_commit}: "
            "somebody else deployed"
        )
    migration = require_migration_state(world, state)
    seen = precheck(world, world.bearer(SYSTEM_BEARER_UUID))
    return {
        "result": "clear",
        "previous_commit": serving,
        "migration": migration,
        "draft_units": seen["reported"],
    }


# --------------------------------------------------------------------------------------------
# finish
# --------------------------------------------------------------------------------------------


def wait_for_revision(world: World, state: State) -> None:
    """Poll `/health/live` until it serves the built commit. Another revision stops at once."""

    def serving() -> str | None:
        try:
            response = _get(world, "/health/live")
            revision = response.json().get("revision") if response.status_code == 200 else None
        except httpx.HTTPError, ValueError, AttributeError:
            return None  # the swap is not zero-downtime (#267); keep asking
        if revision == state.built_commit:
            return revision
        if revision not in (None, state.previous_commit):
            raise Condition(
                f"/health/live serves {revision}: neither the built nor the "
                "pre-swap commit, so somebody else deployed"
            )
        return None

    if _deadline_poll(world, SERVE_SECONDS, serving) is None:
        raise Condition(f"/health/live did not serve {state.built_commit} in {SERVE_SECONDS}s")


def require_ready(world: World) -> str:
    status, reason = readiness(world)
    if status != 200:
        raise Condition(f"{READINESS_PATH} answered {status} ({reason})")
    # `api/health.py::ready` answers 200 only when the database has exactly one head and it is
    # the code's single head, so a 200 rules out migration drift.
    return "200: the database is at the code's single alembic head, so there is no migration drift"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def check_openapi(world: World, expected: list[str]) -> dict[str, Any]:
    document = _mapping(_get_json(world, "/openapi.json"))
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise Refused("/openapi.json served no paths")
    schemas = _mapping(_mapping(document.get("components")).get("schemas"))
    missing = []
    for name in expected:
        model, _, prop = name.partition(".")
        properties = _mapping(schemas[model]).get("properties") if model in schemas else None
        if not isinstance(properties, dict) or (prop and prop not in properties):
            missing.append(name)
    if missing:
        raise Condition(f"the served schema lacks {', '.join(missing)}")
    return {"paths": len(paths), "expected_schema": expected}


def run_binder(world: World, state: State, observed: str) -> dict[str, Any]:
    environment = {
        k: v for k, v in os.environ.items() if k not in {"FORCE_COLOR", "CLICOLOR_FORCE"}
    }
    environment["ACTIVATION_BIND_TOKEN"] = world.bearer(SYSTEM_BEARER_UUID)
    environment["IMAGE_VERIFY_TOKEN"] = world.bearer(VERIFIER_BEARER_UUID)
    argv = [
        str(world.binder),
        "bind",
        "--repository",
        REPOSITORY,
        "--checkout",
        str(world.checkout),
        "--previous-commit",
        state.previous_commit,
        "--built-commit",
        state.built_commit,
        "--digest",
        state.pushed_digest,
        "--observed-digest",
        observed,
        "--image-name",
        "orchestrator",
        "--tag",
        state.tag_name,
        "--workflow-run-url",
        state.workflow_run_url,
        "--base-url",
        API_BASE,
        "--deployer",
        "hq-session",
    ]
    completed = world.run(argv, env=environment, timeout=900)
    try:
        summary = json.loads(completed.stdout)
    except ValueError:
        summary = None
    result = {
        "exit": completed.returncode,
        "summary": summary,
        "stderr": completed.stderr.strip()[-2000:],
    }
    if completed.returncode == EXIT_CONDITION:
        raise Condition(json.dumps({"binder": result}))
    if completed.returncode != EXIT_OK:
        raise Refused(json.dumps({"binder": result}))
    return result


def _finish(args: argparse.Namespace, world: World) -> dict[str, Any]:
    state = State.load(Path(args.state))
    if args.observed_digest != state.pushed_digest:
        raise Condition(
            f"the running container is {args.observed_digest}, not the pushed {state.pushed_digest}"
        )
    head = _git(world, "rev-parse", "HEAD").strip()
    if head != state.built_commit:
        raise Refused(
            f"run finish from a checkout at {state.built_commit}, not {head}: the "
            "binder must match the served image"
        )
    # Read before the binder, so a registry that can't be read stops `finish` before it binds.
    _git(world, "fetch", "-q", "origin")
    follow = lanes_to_follow(world)
    wait_for_revision(world, state)
    ready = require_ready(world)
    openapi = check_openapi(world, state.expect_schema)
    binder = run_binder(world, state, args.observed_digest)
    lane_phases = [
        "uv run python -m scripts.deploy_orchestrator lanes-prepare "
        f"--lane {lane} --tag sha-{state.built_commit}"
        for lane in follow
    ]
    if lane_phases:
        print(
            "Move each lane to the served tag, each with the reads deploy.md names:\n"
            + "\n".join(lane_phases),
            file=sys.stderr,
        )
    return {
        "lane_phases": lane_phases,
        "result": "deployed",
        "built_commit": state.built_commit,
        "previous_commit": state.previous_commit,
        "digest": state.pushed_digest,
        "tag": state.tag,
        "migrations": state.migrations,
        "health_ready": ready,
        "openapi": openapi,
        "binder": binder,
    }


# --------------------------------------------------------------------------------------------
# the lane phases (ADR-0059 decision 1 and Amendment 1)
# --------------------------------------------------------------------------------------------

# One directory per lane, one JSON file per lane-phase run (verify report item 8, option A): a
# fixed place on the Mac outside every repository, so any session on it finds the lane's chain.
LANES_STATE_DIR = Path.home() / "Library" / "Application Support" / "sds" / "deploy-lanes"
LANE_RESOURCE_PREFIX = "sds-lane-"
LANE_COMPOSE = "lanes/{lane}/compose.yaml"
LANE_STATE_MOUNT = "/var/lib/sds/{lane}"
CRED_CONSUMERS = ".cred-consumers.toml"
COOLIFY_INSTANCE = "prod"
TAG_VARIABLE = "SDS_LANES_IMAGE_TAG"
SCHEDULE_VARIABLE = "LANE_SCHEDULE_ENABLED"
SCHEDULE_MAPPING = "${LANE_SCHEDULE_ENABLED:-false}"
TOKEN_VARIABLE = "BWS_ACCESS_TOKEN"

# Increment 1's placeholder lane: no token, no registry entry, no Healthchecks check, no state.
# Every exception the phases make for it names it.
PLACEHOLDER = "placeholder"

# Each moving lane's Healthchecks check, as its launcher names it in its dead-man ping
# (`sds_deadman_arm <name>` or `CHECK_NAME=`); `test_the_check_names_are_the_launchers` holds the
# table to `scripts/run-*.sh`. A table, not a derivation: fork hardening pings
# `sds-fork-hardening`. The two launchers that stay on the Mac (ADR-0059 decision 4) have no row.
LANE_CHECKS: dict[str, str] = {
    "bump-driver": "sds-bump-driver",
    "bump-proposer": "sds-bump-proposer",
    "change-proposer": "sds-change-proposer",
    "deploy-watcher": "sds-deploy-watcher",
    "estate-landing": "sds-estate-landing",
    "execution-watch": "sds-execution-watch",
    "fork-harden": "sds-fork-hardening",
    "fork-sync-watch": "sds-fork-sync-watch",
    "inert-landing": "sds-inert-landing",
    "landing-ledger": "sds-landing-ledger",
    "membership-check": "sds-membership-check",
    "pin-watcher": "sds-pin-watcher",
    "revision-watcher": "sds-revision-watcher",
    "rotation-decomposer": "sds-rotation-decomposer",
    "rotation-proposer": "sds-rotation-proposer",
    "unit-verifier": "sds-unit-verifier",
    "work-carrier": "sds-work-carrier",
}
LANES = frozenset({*LANE_CHECKS, PLACEHOLDER})
# The eleven lanes whose state volume `lanes-finish` checks (decision 1, "Lane state").
STATEFUL_LANES = frozenset(
    {
        "bump-driver",
        "bump-proposer",
        "estate-landing",
        "execution-watch",
        "inert-landing",
        "pin-watcher",
        "revision-watcher",
        "rotation-decomposer",
        "rotation-proposer",
        "unit-verifier",
        "work-carrier",
    }
)

# The lane token's registry entry: a consumer of kind `coolify-env` on a `service` resource whose
# key is `<LANE>_BWS_ACCESS_TOKEN`, the lane's name upper-cased with `-` as `_` (Amendment 1,
# item 3). `docs/operations/deploy.md`, "The lane phases", documents it for increment 2.
LANE_TOKEN_KIND = "coolify-env"
LANE_TOKEN_RESOURCE = "service"
LANE_TOKEN_KEY = re.compile(r"^([A-Z][A-Z0-9_]*)_BWS_ACCESS_TOKEN$")

# The quiet slot: outside estate landing's 02:00 to 06:00 New York window
# (`estate_lander/cli.py`, held equal by `test_the_landing_window_is_the_estate_landers`), and
# the lane's check not `started` unless it is also `down`.
LANDING_ZONE = ZoneInfo("America/New_York")
LANDING_HOURS = range(2, 6)
QUIET_SLOT_WAIT = timedelta(hours=6)
HEALTHCHECKS_LISTING = "https://healthcheck.alobar.net/api/v3/checks/"
# HC_API_KEY in `Rotation / Healthchecks`, the self-hosted instance's read-only key, read as
# sds-operator through a direct grant (ADR-0059 Q5).
HEALTHCHECKS_READ_KEY_UUID = "f07b1368-f937-4287-baf1-b4ed00b6b274"
# How far back `lanes-prepare` looks for a tag's `Release image` run.
LANE_RELEASE_LOOKBACK = 200

SHA_TAG = re.compile(r"^sha-([0-9a-f]{40})$")
RESOURCE_UUID = re.compile(r"^[a-z0-9]{8,64}$")
VOLUME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

LANE_RUN_SCHEMA = 1
WAITING, REFUSED, TIMED_OUT, SUPERSEDED = "waiting", "refused", "timed_out", "superseded"
UNFINISHED, FINISHED, RESTORED = "unfinished", "finished", "restored"


def _healthchecks_listing() -> list[dict[str, Any]]:
    """Every check the read-only key lists. The key goes in one header and nowhere else."""
    key = _bws_value(HEALTHCHECKS_READ_KEY_UUID)
    headers = {"User-Agent": USER_AGENT, "X-Api-Key": key}
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(HEALTHCHECKS_LISTING, headers=headers)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        # The error's text is not repeated: only its type, so no request detail leaves.
        raise Refused(
            f"the Healthchecks listing could not be read ({type(error).__name__})"
        ) from error
    if response.status_code != 200:
        raise Refused(f"the Healthchecks listing answered HTTP {response.status_code}")
    try:
        checks = response.json().get("checks")
    except (ValueError, AttributeError) as error:
        raise Refused("the Healthchecks listing did not answer JSON") from error
    if not isinstance(checks, list):
        raise Refused("the Healthchecks listing carries no checks list")
    return checks


@dataclass(frozen=True)
class Change:
    """One lane-phase change: `tag`, `schedule` or `add`, as its arguments name it."""

    kind: str
    tag: str = ""  # sha-<commit> for `tag` and `add`
    schedule: str = ""  # "true" or "false" for `schedule`

    @property
    def commit(self) -> str:
        named = SHA_TAG.match(self.tag)
        return named.group(1) if named else ""

    @property
    def key(self) -> str:
        return (
            f"schedule:{self.schedule}" if self.kind == "schedule" else f"{self.kind}:{self.commit}"
        )

    def argv(self) -> list[str]:
        if self.kind == "schedule":
            return ["--schedule", self.schedule]
        return (["--add"] if self.kind == "add" else []) + ["--tag", self.tag]


@dataclass
class LaneRun:
    """One lane-phase run's record. `commit`, `tag` and `lane_digest` are what the lane runs once
    the run is `finished`; the `replaced_*` fields are what it ran before."""

    lane: str
    change: str
    change_key: str
    status: str
    created_at: str
    updated_at: str
    deadline: str = ""
    reason: str = ""
    resource_uuid: str = ""
    tag: str = ""
    commit: str = ""
    lane_digest: str = ""
    workflow_run_url: str = ""
    schedule: str = ""
    volume: str = ""
    volume_slug: str = ""
    compose_sent: str = ""
    compose_replaced: str | None = None
    replaced_tag: str = ""
    replaced_schedule: str = ""
    replaced_digest: str = ""
    slot_open: bool = False
    run_id: str = ""
    # The run's place in its lane's chain, assigned under the lane's lock: the order, even for two
    # runs created in the same instant.
    sequence: int = 0
    schema: int = LANE_RUN_SCHEMA


def _lane_dir(world: World, lane: str) -> Path:
    return world.lanes_state_dir / lane


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


@contextmanager
def _lane_lock(world: World, lane: str) -> Iterator[None]:
    """One session at a time on a lane's chain (`flock`, released when the file closes)."""
    directory = _lane_dir(world, lane)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Condition(f"another session holds lane {lane}'s run chain; re-run") from error
        yield


def _save_run(world: World, run: LaneRun) -> None:
    """Write the run's file atomically: a temporary file in the same directory, then a rename."""
    run.updated_at = _stamp(world.now())
    directory = _lane_dir(world, run.lane)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")
    with os.fdopen(descriptor, "w") as handle:
        handle.write(json.dumps(asdict(run), indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, directory / f"{run.run_id}.json")


def _new_run(world: World, lane: str, change: Change, status: str, runs: list[LaneRun]) -> LaneRun:
    now = world.now()
    run_id = f"{now.astimezone(UTC):%Y%m%dT%H%M%S%fZ}-{change.kind}-{uuid_module.uuid4().hex[:8]}"
    return LaneRun(
        lane=lane,
        change=change.kind,
        change_key=change.key,
        status=status,
        created_at=_stamp(now),
        updated_at=_stamp(now),
        run_id=run_id,
        sequence=max((r.sequence for r in runs), default=0) + 1,
    )


def load_runs(world: World, lane: str) -> list[LaneRun]:
    """The lane's runs, oldest first. A file this program didn't write is refused, not skipped."""
    runs = []
    for path in sorted(_lane_dir(world, lane).glob("*.json")):
        try:
            run = LaneRun(**json.loads(path.read_text()))
        except (OSError, ValueError, TypeError) as error:
            raise Refused(f"the lane run file {path} is malformed: {error}") from error
        if run.schema != LANE_RUN_SCHEMA or run.lane != lane or run.run_id != path.stem:
            raise Refused(f"the lane run file {path} is not lane {lane}'s run {path.stem}")
        runs.append(run)
    return sorted(runs, key=lambda r: r.sequence)


def last_finished(runs: list[LaneRun]) -> LaneRun | None:
    """The newest run `lanes-finish` passed: what Coolify was last given for the lane. A restored
    run returned the lane to that run's values, so it never replaces it."""
    finished = [r for r in runs if r.status == FINISHED]
    return finished[-1] if finished else None


def first_add(runs: list[LaneRun]) -> LaneRun | None:
    added = [r for r in runs if r.status == FINISHED and r.change == "add"]
    return added[0] if added else None


def _lane_of_token_consumer(consumer: Any) -> str | None:
    if not isinstance(consumer, dict) or consumer.get("kind") != LANE_TOKEN_KIND:
        return None
    if consumer.get("resource_type") != LANE_TOKEN_RESOURCE:
        return None
    named = LANE_TOKEN_KEY.match(str(consumer.get("key", "")))
    return named.group(1).lower().replace("_", "-") if named else None


def lanes_in_registry(text: str) -> set[str]:
    """The lanes whose token entry the registry holds, by the lane-token matcher."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise Refused(f"{CRED_CONSUMERS} at main doesn't parse: {error}") from error
    credentials = document.get("credential", [])
    if not isinstance(credentials, list):
        raise Refused(f"{CRED_CONSUMERS} at main has no credential array")
    found: list[str] = []
    for credential in credentials:
        consumers = credential.get("consumer", []) if isinstance(credential, dict) else []
        for consumer in consumers if isinstance(consumers, list) else []:
            lane = _lane_of_token_consumer(consumer)
            if lane is None:
                continue
            if lane not in LANE_CHECKS:
                raise Refused(f"{CRED_CONSUMERS} at main names a token for unknown lane {lane!r}")
            found.append(lane)
    twice = sorted({lane for lane in found if found.count(lane) > 1})
    if twice:
        raise Refused(f"{CRED_CONSUMERS} at main has more than one token entry for {twice}")
    return set(found)


def registered_lanes(world: World) -> set[str]:
    """Each lane the release hold covers: the registry's at `origin/main`, plus the placeholder."""
    return lanes_in_registry(_git(world, "show", f"origin/main:{CRED_CONSUMERS}")) | {PLACEHOLDER}


def _recorded_lanes(world: World) -> list[str]:
    root = world.lanes_state_dir
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def require_lanes_current(world: World, served: str, sha: str) -> None:
    """Refuse a release while a lane-phase run is unfinished or a registered lane lags `served`.

    A registered lane with no passed `--add` holds nothing and is skipped.
    """
    unfinished = sorted(
        f"{run.lane} ({run.change_key}, run {run.run_id})"
        for lane in _recorded_lanes(world)
        for run in load_runs(world, lane)
        if run.status == UNFINISHED
    )
    if unfinished:
        raise Refused(
            f"a lane-phase run is unfinished, so no release starts: {', '.join(unfinished)}. "
            "Run its lanes-finish (or lanes-finish --restored) first."
        )
    lagging = []
    for lane in sorted(registered_lanes(world)):
        last = last_finished(load_runs(world, lane))
        if last is not None and last.commit != served:
            lagging.append(f"{lane} runs {last.tag}")
    if lagging:
        raise Refused(
            f"before building {sha}: production serves {served}, but {'; '.join(lagging)}. "
            f"Re-run that lane's phases with --tag sha-{served}, or roll that lane back "
            "(ADR-0059, 'Rollback for one lane')."
        )


def lanes_to_follow(world: World) -> list[str]:
    """The registered lanes with a passed `--add`, which follow the served tag after `finish`."""
    return sorted(
        lane
        for lane in registered_lanes(world)
        if last_finished(load_runs(world, lane)) is not None
    )


def lane_release(world: World, sha: str) -> tuple[str, str]:
    """The lane digest and run URL of `sha`'s one successful `Release image` run."""
    runs = _gh_json(
        world,
        "run",
        "list",
        "-R",
        REPOSITORY,
        "--workflow",
        RELEASE_WORKFLOW,
        "--event",
        "workflow_dispatch",
        "--limit",
        str(LANE_RELEASE_LOOKBACK),
        "--json",
        "databaseId,displayTitle,url,status,conclusion",
    )
    if not isinstance(runs, list):
        raise Refused("gh run list did not answer a list")
    mine = [
        r
        for r in runs
        if isinstance(r, dict)
        and r.get("displayTitle") == run_title(sha)
        and r.get("status") == "completed"
        and r.get("conclusion") == "success"
        and isinstance(r.get("databaseId"), int)
    ]
    if len(mine) != 1:
        raise Refused(
            f"{len(mine)} successful Release image runs titled {run_title(sha)!r} in the last "
            f"{LANE_RELEASE_LOOKBACK}, not one"
        )
    return pushed_lane_digest(world, mine[0]["databaseId"], sha), str(mine[0].get("url", ""))


def require_descendant(world: World, added: str, sha: str) -> None:
    """A lane's tag can't go below the commit that added it: that image has no launcher for it."""
    for commit in (added, sha):
        check = ["git", "-C", str(world.checkout), "cat-file", "-e", f"{commit}^{{commit}}"]
        if world.run(check).returncode != 0:
            raise Refused(f"{commit} is not in the checkout; fetch and re-run")
    ancestry = ["git", "-C", str(world.checkout), "merge-base", "--is-ancestor", added, sha]
    if world.run(ancestry).returncode != 0:
        raise Refused(
            f"{sha} is not a descendant of {added}, the commit that added this lane: its image "
            "has no launcher for it. Return the lane to the Mac first (Amendment 1, item 5)."
        )


def _environment(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("environment") or {}
    if isinstance(raw, list):
        pairs = (str(item).partition("=") for item in raw)
        return {name: value for name, _, value in pairs}
    if isinstance(raw, dict):
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}
    raise Refused("the lane's compose service has an unreadable environment")


def _state_volume(service: dict[str, Any], lane: str) -> str | None:
    """The named volume the lane's service mounts at its state directory, or None."""
    target = LANE_STATE_MOUNT.format(lane=lane)
    for entry in service.get("volumes") or []:
        source = None
        if isinstance(entry, str):
            named, _, rest = entry.partition(":")
            source = named if rest.split(":")[0] == target else None
        elif isinstance(entry, dict) and entry.get("target") == target:
            source = str(entry.get("source", ""))
        # A bind mount (`./x`, `/x`) or an interpolated source is no named volume.
        if source is not None:
            return source if VOLUME_NAME.match(source) else None
    return None


def lane_service(compose: str, lane: str) -> dict[str, Any]:
    """The lane's service: the compose service named after the lane."""
    try:
        document = yaml.safe_load(compose)
    except yaml.YAMLError as error:
        raise Refused(f"the lane's compose doesn't parse: {error}") from error
    services = document.get("services") if isinstance(document, dict) else None
    service = services.get(lane) if isinstance(services, dict) else None
    if not isinstance(service, dict):
        raise Refused(f"the lane's compose has no service named {lane!r}")
    return service


def add_refusals(service: dict[str, Any], lane: str) -> list[str]:
    """What an `--add`'s tracked compose entry must hold before its container may start."""
    refusals = []
    environment = _environment(service)
    if environment.get(SCHEDULE_VARIABLE) != SCHEDULE_MAPPING:
        refusals.append(
            f"it maps {SCHEDULE_VARIABLE} as {environment.get(SCHEDULE_VARIABLE)!r}, "
            f"not {SCHEDULE_MAPPING!r}"
        )
    if lane == PLACEHOLDER:
        if TOKEN_VARIABLE in environment:
            refusals.append(f"the placeholder maps {TOKEN_VARIABLE}, and it holds no token")
    else:
        mapped = "${" + lane.upper().replace("-", "_") + "_BWS_ACCESS_TOKEN}"
        if environment.get(TOKEN_VARIABLE) != mapped:
            refusals.append(f"it doesn't map {TOKEN_VARIABLE} as {mapped!r}")
    if lane in STATEFUL_LANES and _state_volume(service, lane) is None:
        refusals.append(f"it mounts no named volume at {LANE_STATE_MOUNT.format(lane=lane)}")
    return refusals


def quiet_slot_unmet(world: World, lane: str) -> list[str]:
    """Why the lane's resource may not be redeployed now; empty when it may."""
    unmet = []
    local = world.now().astimezone(LANDING_ZONE)
    if local.hour in LANDING_HOURS:
        unmet.append(f"it is {local:%H:%M} New York, inside estate landing's 02:00-06:00 window")
    if lane == PLACEHOLDER:
        return unmet  # no launcher, no check: the window is its whole quiet slot
    name = LANE_CHECKS[lane]
    named = [c for c in world.checks() if isinstance(c, dict) and c.get("name") == name]
    if len(named) != 1:
        raise Refused(f"the Healthchecks listing has {len(named)} checks named {name!r}, not one")
    started, status = named[0].get("started"), named[0].get("status")
    if not isinstance(started, bool):
        raise Refused(f"check {name!r} carries no boolean started field")
    # A pass that stopped without its final ping leaves `started` set; `down` already reports it.
    if started and status != "down":
        unmet.append(f"check {name!r} is started ({status}): a pass is running")
    return unmet


def _take_served_tag(world: World, change: Change, run: LaneRun) -> None:
    """Resolve a sha tag to its commit and its release's lane digest. It must be the served tag."""
    sha = resolve_commit(world, change.commit)
    served = pre_swap_revision(world)
    if sha != served:
        raise Refused(f"{change.tag} is not the served orchestrator tag sha-{served}")
    run.tag, run.commit = change.tag, sha
    run.lane_digest, run.workflow_run_url = lane_release(world, sha)


def _plan_add(world: World, args: argparse.Namespace, run: LaneRun, change: Change) -> None:
    if args.schedule_written != "false":
        raise Refused(
            f"--schedule-written must be false: the session writes {SCHEDULE_VARIABLE} false "
            "before an added lane's first start"
        )
    run.resource_uuid, run.schedule = args.resource_uuid, "false"
    _take_served_tag(world, change, run)


def _plan_change(
    world: World, args: argparse.Namespace, run: LaneRun, change: Change, runs: list[LaneRun]
) -> None:
    """A `--tag` or `--schedule` replaces the lane's last finished run's values."""
    lane = run.lane
    last = last_finished(runs)
    added = first_add(runs)
    if last is None or added is None:
        raise Refused(f"lane {lane} has no passed --add, so it has no resource to change")
    if args.resource_uuid and args.resource_uuid != last.resource_uuid:
        raise Refused(f"lane {lane}'s resource is {last.resource_uuid}, not {args.resource_uuid}")
    if lane in STATEFUL_LANES and args.running_volume != last.volume:
        raise Refused(
            f"the running container mounts {args.running_volume!r}, not the recorded volume "
            f"{last.volume!r}"
        )
    run.resource_uuid, run.volume = last.resource_uuid, last.volume
    run.compose_replaced = last.compose_sent
    run.replaced_tag, run.replaced_schedule = last.tag, last.schedule
    run.replaced_digest = last.lane_digest
    if change.kind == "schedule":
        if args.running_schedule != last.schedule:
            raise Refused(
                f"the running container's {SCHEDULE_VARIABLE} is {args.running_schedule!r}, "
                f"not the recorded {last.schedule!r}"
            )
        if change.schedule == last.schedule:
            raise Refused(f"lane {lane} already runs with {SCHEDULE_VARIABLE}={change.schedule}")
        run.tag, run.commit, run.lane_digest = last.tag, last.commit, last.lane_digest
        run.workflow_run_url, run.schedule = last.workflow_run_url, change.schedule
        return
    if args.running_tag != last.tag:
        raise Refused(
            f"the running container runs {args.running_tag!r}, not the recorded {last.tag!r}"
        )
    if change.tag == last.tag:
        raise Refused(f"lane {lane} already runs {change.tag}")
    run.schedule = last.schedule
    _take_served_tag(world, change, run)  # fetches first, so the descendant check sees the tag
    require_descendant(world, added.commit, run.commit)


def _plan(world: World, args: argparse.Namespace, run: LaneRun, runs: list[LaneRun]) -> None:
    """Fill the run with the change, the compose it writes and the values it replaces."""
    lane, change = run.lane, _change(args)
    if change.kind == "add":
        last = last_finished(runs)
        if last is not None:
            raise Refused(f"lane {lane} was already added (run {last.run_id}); use --tag")
        _plan_add(world, args, run, change)
    else:
        _plan_change(world, args, run, change, runs)
    run.compose_sent = _git(world, "show", f"{run.commit}:{LANE_COMPOSE.format(lane=lane)}")
    service = lane_service(run.compose_sent, lane)
    if change.kind == "add":
        refusals = add_refusals(service, lane)
        if refusals:
            raise Refused(f"the tracked compose of lane {lane} at {run.tag}: {'; '.join(refusals)}")
        run.volume_slug = _state_volume(service, lane) or ""


def _change(args: argparse.Namespace) -> Change:
    """Exactly one change: `--tag`, `--schedule` or `--add --tag`."""
    if args.add:
        if not args.tag or args.schedule is not None:
            raise Usage("--add takes --tag <sha tag> and no --schedule")
        return Change("add", tag=args.tag)
    if args.tag and args.schedule is not None:
        raise Usage("give exactly one change: --tag, --schedule or --add --tag")
    if args.tag:
        return Change("tag", tag=args.tag)
    if args.schedule is not None:
        return Change("schedule", schedule=args.schedule)
    raise Usage("give exactly one change: --tag, --schedule or --add --tag")


def _current(runs: list[LaneRun], change: Change, status: str) -> LaneRun | None:
    """The newest run of this change in this status."""
    found = [r for r in runs if r.change_key == change.key and r.status == status]
    return found[-1] if found else None


def _container(run: LaneRun) -> str:
    return (
        f"C=$(docker ps -q --filter name={run.resource_uuid}); "
        f"{_exactly_one('C', 'running container')}; "
    )


def lane_reads(run: LaneRun) -> list[tuple[str, str]]:
    """The `lanes-finish` arguments and the `vps_exec` command that reads each. No read prints
    another variable's value: each prints one image, one variable, one mount or a boolean."""
    container = _container(run)
    reads = [
        (
            "--observed-digest",
            container + 'docker image inspect "$(docker inspect "$C" --format '
            "'{{.Image}}')\" --format '{{range .RepoDigests}}{{println .}}{{end}}'",
        ),
        ("--observed-schedule", container + f'docker exec "$C" printenv {SCHEDULE_VARIABLE}'),
    ]
    if run.lane in STATEFUL_LANES:
        mount = LANE_STATE_MOUNT.format(lane=run.lane)
        reads.append(
            (
                "--observed-volume",
                container + 'docker inspect "$C" --format \'{{range .Mounts}}'
                f'{{{{if eq .Destination "{mount}"}}}}{{{{.Name}}}}{{{{end}}}}{{{{end}}}}\'',
            )
        )
    if run.change == "add" and run.lane != PLACEHOLDER:
        reads.append(
            (
                "--token-present",
                container + f'docker exec "$C" sh -c \'test -n "${TOKEN_VARIABLE}"\' '
                "&& echo true || echo false",
            )
        )
    return reads


def _call(tool: str, run: LaneRun, **fields: str) -> str:
    named = "".join(f', {k}="{v}"' for k, v in fields.items())
    return f'{tool}(uuid="{run.resource_uuid}", instance="{COOLIFY_INSTANCE}"{named})'


def _variable_write(run: LaneRun, *, inverse: bool) -> str:
    """The one variable a change writes, or its inverse: the value it replaces."""
    if run.change == "schedule":
        value = run.replaced_schedule if inverse else run.schedule
        return _call("coolify_update_service_env", run, key=SCHEDULE_VARIABLE, value=value)
    if run.change == "add":
        return _call("coolify_create_service_env", run, key=TAG_VARIABLE, value=run.tag)
    value = run.replaced_tag if inverse else run.tag
    return _call("coolify_update_service_env", run, key=TAG_VARIABLE, value=value)


def _phase_command(phase: str, run: LaneRun, *extra: str) -> str:
    change = Change(run.change, tag=run.tag, schedule=run.schedule)
    argv = ["--lane", run.lane, *change.argv(), *extra]
    return f"uv run python -m scripts.deploy_orchestrator {phase} {' '.join(argv)}"


def _compose_block(title: str, text: str) -> list[str]:
    return [f"----- {title} -----", text.rstrip("\n"), "----- end -----"]


def lane_inverse(run: LaneRun) -> str:
    """What undoes the run's writes, for `lanes-finish` to name when the redeploy fails."""
    resource = f"{LANE_RESOURCE_PREFIX}{run.lane}"
    if run.change == "add":
        restored = _phase_command(
            "lanes-finish", run, "--restored", "--service-listed false", "--container-present false"
        )
        return "\n".join(
            [
                f"The inverse of an --add is the delete of {resource} alone, with no second",
                "coolify_deploy. HQ runs it as its own announced production act under D-20",
                "(prod announce, the delete, a check at the source, prod verified):",
                f"   {_call('coolify_delete_service', run)}",
                "Coolify only queues the deletion: re-read for up to 10 minutes until",
                f"coolify_list_services no longer lists {resource} and vps_docker_ps shows no",
                "container of it, then pass both reads in:",
                f"   {restored}",
            ]
        )
    reads = " ".join(f"{name} <read>" for name, _ in lane_reads(run))
    lines = [
        f"The inverse, for a failed redeploy of {resource}: these writes, then a second redeploy.",
        f"1. {_call('coolify_update_service', run)} with docker_compose_raw set to the recorded",
        "   compose this run replaced:",
        *_compose_block("compose to restore", run.compose_replaced or ""),
        f"2. {_variable_write(run, inverse=True)}",
        f"3. {_call('coolify_deploy', run)}",
        "4. The reads below, of the restored container, then:",
        f"   {_phase_command('lanes-finish', run, '--restored', reads)}",
    ]
    return "\n".join(lines)


def lane_next_steps(run: LaneRun) -> str:
    resource = f"{LANE_RESOURCE_PREFIX}{run.lane}"
    replaced = run.compose_replaced if run.compose_replaced is not None else ""
    diff = difflib.unified_diff(
        replaced.splitlines(),
        run.compose_sent.splitlines(),
        fromfile="recorded" if run.compose_replaced is not None else "none (an --add)",
        tofile=f"{LANE_COMPOSE.format(lane=run.lane)} at {run.tag}",
        lineterm="",
    )
    reads = lane_reads(run)
    lines = [
        f"Lane {run.lane}'s quiet slot is open. Run {run.run_id} changes {resource} "
        f"({run.change_key}); the lane image is {LANE_IMAGE}@{run.lane_digest}.",
        "How the compose this run writes differs from the recorded one:",
        *diff,
        "",
        "Make these calls IN THIS ORDER, with no other step between them:",
        f"1. {_phase_command('lanes-recheck', run)}",
        "   Only if it exits 0, the writes. A non-zero exit means go back to lanes-prepare.",
        f"2. {_call('coolify_update_service', run)} with docker_compose_raw set to exactly:",
        *_compose_block("compose to send", run.compose_sent),
        f"3. {_variable_write(run, inverse=False)}",
        f"4. {_call('coolify_deploy', run)}",
        "5. Once the deployment has finished, read the new container with vps_exec(instance="
        f'"{COOLIFY_INSTANCE}"):',
    ]
    for name, command in reads:
        lines += [f"   {name}:", f"   {command}"]
    finish = " ".join(f"{name} <read>" for name, _ in reads)
    lines += [
        f"6. {_phase_command('lanes-finish', run, finish)}",
        "   If the redeploy itself failed, run lanes-finish with --redeploy-failed instead.",
        "",
        lane_inverse(run),
    ]
    return "\n".join(lines)


def _run_summary(run: LaneRun, world: World) -> dict[str, Any]:
    summary = {k: v for k, v in asdict(run).items() if not k.startswith("compose_")}
    return {**summary, "run_file": str(_lane_dir(world, run.lane) / f"{run.run_id}.json")}


def _lanes_prepare(args: argparse.Namespace, world: World) -> dict[str, Any]:
    lane, change = args.lane, _change(args)
    with _lane_lock(world, lane):
        runs = load_runs(world, lane)
        unfinished = [r.run_id for r in runs if r.status == UNFINISHED]
        if unfinished:
            raise Refused(
                f"lane {lane} has an unfinished run ({', '.join(unfinished)}): finish or restore "
                "it before another change writes the lane's resource"
            )
        fresh = _new_run(world, lane, change, WAITING, runs)
        try:
            _plan(world, args, fresh, runs)
        except Refused as error:
            fresh.status, fresh.reason = REFUSED, str(error)
            _save_run(world, fresh)
            raise
        resumed = _current(runs, change, WAITING)
        if resumed is None:
            run = fresh
            run.deadline = _stamp(world.now() + QUIET_SLOT_WAIT)
        else:
            run = replace(
                fresh,
                run_id=resumed.run_id,
                created_at=resumed.created_at,
                deadline=resumed.deadline,
                sequence=resumed.sequence,
            )
        # Saved before anything it supersedes, so no later refusal loses a recorded deadline.
        _save_run(world, run)
        for other in runs:
            if other.status == WAITING and other.run_id != run.run_id:
                other.status, other.reason = SUPERSEDED, f"superseded by run {run.run_id}"
                _save_run(world, other)
        if world.now() >= datetime.fromisoformat(run.deadline):
            run.status = TIMED_OUT
            run.reason = (
                f"lane {lane}: the quiet-slot wait for {change.key} passed its deadline "
                f"{run.deadline}"
            )
            _save_run(world, run)
            print(run.reason, file=sys.stderr)
            raise TimedOut(run.reason)
        unmet = quiet_slot_unmet(world, lane)
        run.slot_open, run.reason = not unmet, "; ".join(unmet)
        _save_run(world, run)
        if unmet:
            raise Condition(
                f"lane {lane}'s quiet slot is unmet: {run.reason}. Re-run lanes-prepare; the "
                f"wait ends at {run.deadline}"
            )
    print(lane_next_steps(run), file=sys.stderr)
    return {"result": "prepared", "resumed": resumed is not None, **_run_summary(run, world)}


def _lanes_recheck(args: argparse.Namespace, world: World) -> dict[str, Any]:
    """The quiet-slot reads again, just before the writes. Exit 0 marks the run unfinished."""
    lane, change = args.lane, _change(args)
    with _lane_lock(world, lane):
        run = _current(load_runs(world, lane), change, WAITING)
        if run is None:
            raise Refused(f"lane {lane} has no waiting run for {change.key}; run lanes-prepare")
        if not run.slot_open:
            raise Refused(f"lanes-prepare has not found run {run.run_id}'s slot open; run it")
        unmet = quiet_slot_unmet(world, lane)
        if unmet:
            run.slot_open, run.reason = False, "; ".join(unmet)
            _save_run(world, run)
            raise Condition(f"lane {lane}'s quiet slot closed: {run.reason}. Run lanes-prepare")
        run.status, run.reason = UNFINISHED, ""
        _save_run(world, run)
    return {"result": "clear", **_run_summary(run, world)}


def volume_refusal(run: LaneRun, observed: str) -> str | None:
    """Why the container's state mount isn't the lane's volume, or None. Coolify's `parse()` can
    double-prefix a volume's name and start on a new, empty volume (workspace invariant #4)."""
    if run.change != "add":
        if observed == run.volume:
            return None
        return f"it mounts {observed!r}, not the recorded volume {run.volume!r}"
    # Coolify names a compose volume `<resource uuid>_<name>` (the verify report's item 2).
    expected = f"{run.resource_uuid}_{run.volume_slug}"
    if observed == expected:
        return None
    return f"it mounts {observed!r}, not the resource's new volume {expected!r}"


def finish_mismatches(args: argparse.Namespace, run: LaneRun) -> list[str]:
    """How the redeployed container differs from the change and no other."""
    found = []
    if args.observed_digest != run.lane_digest:
        found.append(f"it runs {args.observed_digest}, not the release's {run.lane_digest}")
    if args.observed_schedule != run.schedule:
        found.append(f"its {SCHEDULE_VARIABLE} is {args.observed_schedule}, not {run.schedule}")
    if run.change == "add" and run.lane != PLACEHOLDER and args.token_present != "true":
        found.append(f"it holds no {TOKEN_VARIABLE}")
    if run.lane in STATEFUL_LANES:
        refusal = volume_refusal(run, args.observed_volume)
        if refusal:
            found.append(refusal)
    return found


def _lanes_restored(args: argparse.Namespace, world: World, run: LaneRun) -> dict[str, Any]:
    if run.change == "add":
        if args.service_listed or args.container_present:
            raise Condition(
                f"{LANE_RESOURCE_PREFIX}{run.lane} is still there (listed: "
                f"{args.service_listed}, container: {args.container_present}); re-read for up to "
                "10 minutes after the delete, then pass the reads in again"
            )
    else:
        found = []
        if args.observed_digest != run.replaced_digest:
            found.append(f"it runs {args.observed_digest}, not {run.replaced_digest}")
        if args.observed_schedule != run.replaced_schedule:
            found.append(f"its {SCHEDULE_VARIABLE} is {args.observed_schedule}")
        if run.lane in STATEFUL_LANES and args.observed_volume != run.volume:
            found.append(f"it mounts {args.observed_volume!r}, not {run.volume!r}")
        if found:
            raise Condition(f"lane {run.lane} isn't restored: {'; '.join(found)}")
    run.status, run.reason = RESTORED, ""
    _save_run(world, run)
    return {"result": "restored", **_run_summary(run, world)}


def _lanes_finish(args: argparse.Namespace, world: World) -> dict[str, Any]:
    lane, change = args.lane, _change(args)
    with _lane_lock(world, lane):
        run = _current(load_runs(world, lane), change, UNFINISHED)
        if run is None:
            raise Refused(f"lane {lane} has no unfinished run for {change.key}")
        if args.restored:
            return _lanes_restored(args, world, run)
        found = ["the redeploy failed"] if args.redeploy_failed else finish_mismatches(args, run)
        if found:
            print(lane_inverse(run), file=sys.stderr)
            raise Condition(
                f"lane {lane} doesn't show {change.key} and no other: {'; '.join(found)}. Run "
                "the inverse printed on stderr now"
            )
        if run.change == "add" and lane in STATEFUL_LANES:
            run.volume = args.observed_volume
        run.status, run.reason = FINISHED, ""
        _save_run(world, run)
    return {"result": "finished", **_run_summary(run, world)}


# --------------------------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------------------------


def _boolean(value: str) -> bool:
    if value.lower() not in {"true", "false"}:
        raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")
    return value.lower() == "true"


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a bad command line, which is this program's CONDITION code."""

    def error(self, message: str) -> NoReturn:
        raise Usage(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Deploy the orchestrator by its recorded procedure.")
    phases = parser.add_subparsers(dest="phase", required=True)
    prepare = phases.add_parser("prepare", help="precheck, record, build, print the next steps")
    prepare.add_argument("--ref", help="a commit or origin/main (optional with --resume)")
    prepare.add_argument(
        "--resume", action="store_true", help="continue the build recorded in --state"
    )
    prepare.add_argument("--state", required=True, help="a JSON path outside the repository")
    prepare.add_argument("--label", default="", help="optional readable-tag suffix")
    prepare.add_argument(
        "--coolify-health-check-enabled",
        type=_boolean,
        help="health_check_enabled from coolify_get_application",
    )
    prepare.add_argument(
        "--coolify-health-check-path",
        help="health_check_path from coolify_get_application",
    )
    prepare.add_argument(
        "--expect-schema",
        action="append",
        default=[],
        help="Model or Model.field the served schema must carry",
    )
    recheck = phases.add_parser("recheck", help="nothing live, still pre-swap; before the swap")
    recheck.add_argument("--state", required=True)
    finish = phases.add_parser("finish", help="verify the swap, then bind the release")
    finish.add_argument("--state", required=True)
    finish.add_argument("--observed-digest", required=True)
    _lane_parsers(phases)
    return parser


def _lane_parsers(phases: Any) -> None:
    change = _Parser(add_help=False)
    change.add_argument("--lane", required=True, help="the lane whose resource this changes")
    change.add_argument("--tag", help="the release's sha tag, sha-<40 hex>")
    change.add_argument("--schedule", choices=["true", "false"], help="LANE_SCHEDULE_ENABLED")
    change.add_argument("--add", action="store_true", help="the lane's first start (with --tag)")
    prepare = phases.add_parser(
        "lanes-prepare", parents=[change], help="wait for the quiet slot, print the calls"
    )
    prepare.add_argument("--resource-uuid", help="the lane resource's uuid (--add)")
    prepare.add_argument("--running-tag", help="the running container's tag (--tag)")
    prepare.add_argument("--running-schedule", choices=["true", "false"], help="(--schedule)")
    prepare.add_argument("--running-volume", help="the running state mount (stateful lanes)")
    prepare.add_argument("--schedule-written", help="the LANE_SCHEDULE_ENABLED written (--add)")
    phases.add_parser("lanes-recheck", parents=[change], help="the quiet slot, before the writes")
    finish = phases.add_parser(
        "lanes-finish", parents=[change], help="check the redeployed container's reads"
    )
    finish.add_argument("--observed-digest", help="the container's image RepoDigest")
    finish.add_argument("--observed-schedule", choices=["true", "false"])
    finish.add_argument("--observed-volume", help="the source of the state mount")
    finish.add_argument("--token-present", choices=["true", "false"], help="(--add)")
    finish.add_argument("--redeploy-failed", action="store_true")
    finish.add_argument("--restored", action="store_true", help="check the inverse took")
    finish.add_argument("--service-listed", type=_boolean, help="(--add --restored)")
    finish.add_argument("--container-present", type=_boolean, help="(--add --restored)")


def _require(args: argparse.Namespace, *names: str) -> None:
    missing = [f"--{n.replace('_', '-')}" for n in names if getattr(args, n) is None]
    if missing:
        raise Usage(f"{args.phase} {_change(args).kind} needs {', '.join(missing)}")


def _validate_lane(args: argparse.Namespace) -> None:
    if args.lane not in LANES:
        raise Usage(f"--lane must be one of {', '.join(sorted(LANES))}")
    change = _change(args)
    if change.tag and not SHA_TAG.match(change.tag):
        raise Usage("--tag must be the release's sha tag, sha-<40 lowercase hex>")
    if args.phase == "lanes-prepare":
        _validate_lanes_prepare(args, change)
    elif args.phase == "lanes-finish":
        _validate_lanes_finish(args, change)


def _validate_lanes_prepare(args: argparse.Namespace, change: Change) -> None:
    if args.resource_uuid is not None and not RESOURCE_UUID.match(args.resource_uuid):
        raise Usage("--resource-uuid must be a Coolify uuid")
    if change.kind == "add":
        _require(args, "resource_uuid", "schedule_written")
        return
    if args.schedule_written is not None:
        raise Usage("--schedule-written is for --add only")
    _require(args, "running_tag" if change.kind == "tag" else "running_schedule")
    if args.lane in STATEFUL_LANES:
        _require(args, "running_volume")


def _validate_lanes_finish(args: argparse.Namespace, change: Change) -> None:
    if args.restored and args.redeploy_failed:
        raise Usage("--restored and --redeploy-failed are separate steps")
    if args.redeploy_failed:
        return
    if args.restored and change.kind == "add":
        _require(args, "service_listed", "container_present")
        return
    _require(args, "observed_digest", "observed_schedule")
    if not DIGEST.match(args.observed_digest):
        raise Usage("--observed-digest must be sha256:<64 lowercase hex>")
    if args.lane in STATEFUL_LANES:
        _require(args, "observed_volume")
        if not VOLUME_NAME.match(args.observed_volume):
            raise Usage("--observed-volume must be a Docker volume name")
    if change.kind == "add" and args.lane != PLACEHOLDER and not args.restored:
        _require(args, "token_present")


def _validate(args: argparse.Namespace) -> None:
    """Refuse a malformed command before anything is read or run."""
    if args.phase == "prepare":
        try:
            readable_tag("0" * 40, args.label)
        except ValueError as error:
            raise Usage(str(error)) from error
    elif args.phase == "finish" and not DIGEST.match(args.observed_digest):
        raise Usage("--observed-digest must be sha256:<64 lowercase hex>")
    elif args.phase.startswith("lanes-"):
        _validate_lane(args)


def main(argv: Sequence[str] | None = None, world: World | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except Usage as error:
        print(json.dumps({"result": "usage", "reason": str(error)}, indent=2, sort_keys=True))
        return EXIT_USAGE
    world = world or World()
    phase = {
        "prepare": _prepare,
        "recheck": _recheck,
        "finish": _finish,
        "lanes-prepare": _lanes_prepare,
        "lanes-recheck": _lanes_recheck,
        "lanes-finish": _lanes_finish,
    }[args.phase]
    try:
        _validate(args)
        result, code = phase(args, world), EXIT_OK
    except Usage as error:
        result, code = {"result": "usage", "reason": str(error)}, EXIT_USAGE
    except Condition as error:
        result, code = {"result": "condition", "reason": str(error)}, EXIT_CONDITION
    except Refused as error:
        result, code = {"result": "refused", "reason": str(error)}, EXIT_REFUSED
    except TimedOut as error:
        result, code = {"result": "timed_out", "reason": str(error)}, EXIT_TIMED_OUT
    except (OSError, subprocess.SubprocessError) as error:
        # A missing `gh`, a timed-out subprocess: the command could not ask, so it refuses.
        reason = f"{type(error).__name__}: {error}"
        result, code = {"result": "refused", "reason": reason}, EXIT_REFUSED
    print(json.dumps({"phase": args.phase, **result}, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
