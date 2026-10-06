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
    uv run python -m scripts.deploy_orchestrator recheck --state <path>
    uv run python -m scripts.deploy_orchestrator finish --state <path>
        --observed-digest sha256:<64 hex>

`prepare` refuses a build whose health checks read `/health/ready` (migrate-first would become
an outage), refuses while a unit is live or a dispatched run has not been claimed yet, records
the pre-swap revision, builds the image with the `Release image` workflow, reads the pushed
digest from that run, and decides whether a migration is needed. `recheck`, run immediately
before the swap, repeats the nothing-live check and confirms production still serves the recorded
pre-swap revision, because the first check is as old as the build. `finish` refuses unless the
running container's digest is the pushed one. It then waits for `/health/live` to serve the
built commit, requires `/health/ready` and the OpenAPI document, and runs `image-release bind`
with the pre-swap revision `prepare` recorded, never a fresh read.

Exit codes: 0 done; 2 a condition (a live unit, a digest mismatch, a revision not served, a
binder condition); 3 unreadable or refused; 1 usage. Every command prints one JSON object on stdout.
`prepare` also prints the next steps on stderr.

Bearers are fetched in-process from BWS and passed to the binder in its environment, never in
argv. Nothing here prints a credential.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NoReturn

import httpx

from scripts.compute_image_tags import derivable_tag, readable_tag

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
MIGRATIONS_DIR = "migrations/versions/"
HEALTH_CHECK_FILES = ("Dockerfile", "docker-compose.yml")

# A restart in these states strands the unit with its attempt spent: its runner calls back at the
# end of its run (#82). `draft` is reported but does not block, because a draft is inert across a
# restart (Devon, 2026-10-06).
LIVE_STATES = frozenset({"claimed", "executing", "submitted", "verifying"})
REPORTED_STATES = frozenset({"draft"})
DISPATCHED_ACTION = "dispatch.dispatched"
CLAIMED_STATE = "claimed"

EXIT_OK, EXIT_USAGE, EXIT_CONDITION, EXIT_REFUSED = 0, 1, 2, 3
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
LOG_DIGEST = re.compile(r"\bDIGEST: (sha256:[0-9a-f]{64})\b")
LOG_REVISION = re.compile(r"\bREVISION: ([0-9a-f]{40})\b")
LOG_SHA_TAG = re.compile(r"\bSHA_TAG: (\S+)")
STATE_SCHEMA = 1

RUN_APPEAR_SECONDS = 120
BUILD_SECONDS = 45 * 60
SERVE_SECONDS = 300
POLL_SECONDS = 10


class Condition(Exception):
    """A deploy condition is unmet. The world may change; re-run when it does. Exit 2."""


class Refused(Exception):
    """A state this program cannot read, or one it will not deploy from. Exit 3."""


class Usage(Exception):
    """The command line is malformed. Exit 1."""


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
    schema: int = STATE_SCHEMA

    @property
    def tag_name(self) -> str:
        """The tag alone, as Coolify's `docker_registry_image_tag` and the binder take it."""
        return self.tag.rsplit(":", 1)[1]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")

    @classmethod
    def load(cls, path: Path) -> State:
        try:
            raw = json.loads(path.read_text())
            state = cls(**raw)
        except (OSError, ValueError, TypeError) as error:
            raise Refused(f"the state file {path} is missing or malformed: {error}") from error
        if state.schema != STATE_SCHEMA:
            raise Refused(f"the state file {path} has schema {state.schema}")
        shas = (state.built_commit, state.previous_commit)
        if not all(FULL_SHA.match(s) for s in shas) or not DIGEST.match(state.pushed_digest):
            raise Refused(f"the state file {path} does not hold full shas and a digest")
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


def _latest_run_id(world: World) -> int:
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
        "1",
        "--json",
        "databaseId",
    )
    try:
        return int(runs[0]["databaseId"]) if runs else 0
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise Refused("gh run list did not answer a run id") from error


def build_image(world: World, sha: str, label: str) -> tuple[int, str]:
    """Dispatch the release workflow and wait for it. Returns the run id and URL."""
    before = _latest_run_id(world)
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
        f"label={label}",
    )

    def appeared() -> int | None:
        latest = _latest_run_id(world)
        return latest if latest > before else None

    run_id = _deadline_poll(world, RUN_APPEAR_SECONDS, appeared)
    if run_id is None:
        raise Refused("the dispatched Release image run never appeared")

    def concluded() -> dict[str, Any] | None:
        view = _gh_json(
            world, "run", "view", str(run_id), "-R", REPOSITORY, "--json", "status,conclusion,url"
        )
        return view if isinstance(view, dict) and view.get("status") == "completed" else None

    view = _deadline_poll(world, BUILD_SECONDS, concluded)
    if view is None:
        raise Refused(f"Release image run {run_id} did not finish in {BUILD_SECONDS}s")
    if view.get("conclusion") != "success":
        raise Refused(f"Release image run {view.get('url')} concluded {view.get('conclusion')}")
    return run_id, str(view["url"])


def pushed_digest(world: World, run_id: int, sha: str) -> str:
    """The digest the run pushed, read from its verify step, which also names the revision."""
    log = _gh(world, "run", "view", str(run_id), "-R", REPOSITORY, "--log")
    digests = set(LOG_DIGEST.findall(log))
    revisions = set(LOG_REVISION.findall(log))
    sha_tags = set(LOG_SHA_TAG.findall(log))
    if len(digests) != 1:
        raise Refused(f"run {run_id} logged {len(digests)} distinct digests, not one")
    if revisions != {sha} or sha_tags != {derivable_tag(sha)}:
        raise Refused(f"run {run_id} did not build {sha}; it is somebody else's run")
    return digests.pop()


def _prepare(args: argparse.Namespace, world: World) -> dict[str, Any]:
    state_path = Path(args.state)
    if state_path.exists():
        return {"result": "prepared", "resumed": True, **_resume(state_path, args.ref, world)}
    sha = resolve_commit(world, args.ref)
    refuse_readiness_health_checks(
        world, sha, args.coolify_health_check_enabled, args.coolify_health_check_path
    )
    seen = precheck(world, world.bearer(SYSTEM_BEARER_UUID))
    previous = pre_swap_revision(world)
    if previous == sha:
        raise Condition(f"production already serves {sha}")
    migrations = migrations_between(world, previous, sha)
    # The migration runs under the OUTGOING image, so its health check is the one that would fire.
    refuse_readiness_health_checks(
        world, previous, args.coolify_health_check_enabled, args.coolify_health_check_path
    )
    run_id, run_url = build_image(world, sha, args.label)
    tag = readable_tag(sha, args.label)
    state = State(
        built_commit=sha,
        previous_commit=previous,
        label=args.label,
        tag=tag,
        pushed_digest=pushed_digest(world, run_id, sha),
        workflow_run_url=run_url,
        migrations=migrations,
        expect_schema=list(args.expect_schema),
    )
    state.save(state_path)
    print(next_steps(state, state_path), file=sys.stderr)
    return {
        "result": "prepared",
        "resumed": False,
        "draft_units": seen["reported"],
        **asdict(state),
    }


def _resume(state_path: Path, ref: str, world: World) -> dict[str, Any]:
    """Re-print an earlier prepare. The workflow refuses to rebuild a tag, so it never re-runs."""
    state = State.load(state_path)
    sha = _git(world, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    if sha != state.built_commit:
        raise Refused(f"{state_path} records {state.built_commit}, not {sha}; use a new --state")
    print(next_steps(state, state_path), file=sys.stderr)
    return asdict(state)


def _container() -> str:
    return (
        f"C=$(docker ps -q --filter name={APPLICATION_UUID} | head -1); "
        '[ -n "$C" ] || { echo "no running container" >&2; exit 1; }'
    )


def migration_command(state: State) -> str:
    """Alembic from the NEW image, on the coolify network; the DB URL never leaves the host."""
    image = f"ghcr.io/alobarquest/orchestrator@{state.pushed_digest}"
    return (
        f"{_container()}; "
        "DB=$(docker inspect \"$C\" --format '{{range .Config.Env}}{{println .}}{{end}}' "
        "| grep '^ORCHESTRATOR_DATABASE_URL=' | cut -d= -f2-); "
        f'docker run --rm --network coolify -e ORCHESTRATOR_DATABASE_URL="$DB" {image} '
        "sh -c 'cd /app && .venv/bin/alembic upgrade head && .venv/bin/alembic current "
        "&& .venv/bin/alembic heads'"
    )


def repo_digest_command() -> str:
    """Container -> image -> RepoDigests: a container has no RepoDigests of its own (#52)."""
    return (
        f'{_container()}; docker image inspect "$(docker inspect "$C" --format '
        "'{{.Image}}')\" --format '{{range .RepoDigests}}{{println .}}{{end}}'"
    )


def next_steps(state: State, state_path: Path) -> str:
    lines = [
        f"Built {state.built_commit} as {state.tag} ({state.pushed_digest}).",
        f"Pre-swap revision recorded: {state.previous_commit}.",
        "Make these infraops calls IN THIS ORDER. The tag write comes first, so the window",
        "between migration and swap is one call long (deploy.md #305):",
        "",
        f'1. coolify_update_application(uuid="{APPLICATION_UUID}", instance="prod", '
        f'docker_registry_image_tag="{state.tag_name}")',
    ]
    if state.migrations:
        lines += [
            f'2. Migrate ({", ".join(state.migrations)}) with vps_exec(instance="prod", '
            "timeout=300000) and this command:",
            "",
            migration_command(state),
            "",
            "   Confirm the printed `current` equals `heads` before the swap.",
        ]
    else:
        lines.append(
            f"2. No migration: nothing under {MIGRATIONS_DIR} changed since "
            f"{state.previous_commit[:7]}."
        )
    lines += [
        "3. Immediately before the swap, re-check that nothing is live and that production still",
        f"   serves {state.previous_commit}. The first check is as old as the build:",
        f"   uv run python -m scripts.deploy_orchestrator recheck --state {state_path}",
        f'   Only if it exits 0: coolify_deploy(uuid="{APPLICATION_UUID}", instance="prod")',
        "4. Once the deployment has finished, read the running container's digest with",
        '   vps_exec(instance="prod") and this command:',
        "",
        repo_digest_command(),
        "",
        "5. From a checkout at the built commit, with its own venv:",
        f"   uv run python -m scripts.deploy_orchestrator finish --state {state_path} "
        "--observed-digest <the sha256:... after '@' in step 4>",
    ]
    return "\n".join(lines)


def _recheck(args: argparse.Namespace, world: World) -> dict[str, Any]:
    """The prepare-time checks that age: run again immediately before the swap (#82)."""
    state = State.load(Path(args.state))
    seen = precheck(world, world.bearer(SYSTEM_BEARER_UUID))
    serving = pre_swap_revision(world)
    if serving != state.previous_commit:
        raise Condition(
            f"production serves {serving}, not the recorded pre-swap {state.previous_commit}: "
            "somebody else deployed"
        )
    return {"result": "clear", "previous_commit": serving, "draft_units": seen["reported"]}


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
    try:
        response = _get(world, READINESS_PATH)
    except httpx.HTTPError as error:
        raise Refused(f"GET {READINESS_PATH} failed: {error}") from error
    if response.status_code != 200:
        try:
            reason = response.json().get("reason")
        except ValueError, AttributeError:
            reason = None
        raise Condition(f"{READINESS_PATH} answered {response.status_code} ({reason})")
    # `api/health.py::ready` answers 200 only when the database has exactly one head and it is
    # the code's single head, so a 200 rules out migration drift.
    return "200: the database is at the code's single alembic head, so there is no migration drift"


def check_openapi(world: World, expected: list[str]) -> dict[str, Any]:
    document = _get_json(world, "/openapi.json")
    paths = document.get("paths") if isinstance(document, dict) else None
    if not isinstance(paths, dict) or not paths:
        raise Refused("/openapi.json served no paths")
    schemas = document.get("components", {}).get("schemas", {})
    missing = []
    for name in expected:
        model, _, prop = name.partition(".")
        properties = schemas.get(model, {}).get("properties", {}) if model in schemas else None
        if properties is None or (prop and prop not in properties):
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
    wait_for_revision(world, state)
    ready = require_ready(world)
    openapi = check_openapi(world, state.expect_schema)
    binder = run_binder(world, state, args.observed_digest)
    return {
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
    prepare.add_argument("--ref", required=True, help="a commit or origin/main")
    prepare.add_argument("--state", required=True, help="a JSON path outside the repository")
    prepare.add_argument("--label", default="", help="optional readable-tag suffix")
    prepare.add_argument(
        "--coolify-health-check-enabled",
        required=True,
        type=_boolean,
        help="health_check_enabled from coolify_get_application",
    )
    prepare.add_argument(
        "--coolify-health-check-path",
        required=True,
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
    return parser


def _validate(args: argparse.Namespace) -> None:
    """Refuse a malformed command before anything is read or run."""
    if args.phase == "prepare":
        try:
            readable_tag("0" * 40, args.label)
        except ValueError as error:
            raise Usage(str(error)) from error
    elif args.phase == "finish" and not DIGEST.match(args.observed_digest):
        raise Usage("--observed-digest must be sha256:<64 lowercase hex>")


def main(argv: Sequence[str] | None = None, world: World | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except Usage as error:
        print(json.dumps({"result": "usage", "reason": str(error)}, indent=2, sort_keys=True))
        return EXIT_USAGE
    world = world or World()
    phase = {"prepare": _prepare, "recheck": _recheck, "finish": _finish}[args.phase]
    try:
        _validate(args)
        result, code = phase(args, world), EXIT_OK
    except Usage as error:
        result, code = {"result": "usage", "reason": str(error)}, EXIT_USAGE
    except Condition as error:
        result, code = {"result": "condition", "reason": str(error)}, EXIT_CONDITION
    except Refused as error:
        result, code = {"result": "refused", "reason": str(error)}, EXIT_REFUSED
    print(json.dumps({"phase": args.phase, **result}, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
