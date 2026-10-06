"""The deploy command refuses where the procedure says a deploy must not proceed.

The world is faked at its edges only. Production is an `httpx.MockTransport`; `gh` and the binder
are recorded subprocess fakes; `git` is REAL, against a throwaway origin and clone, because the
refusals that read history (not on main, a rollback, a missing commit) and migration detection
are only worth testing against what git actually answers.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import deploy_orchestrator as deploy
from scripts.deploy_orchestrator import (
    EXIT_CONDITION,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_USAGE,
    State,
    World,
)

GOOD_DOCKERFILE = (
    "FROM python:3.14-slim AS runtime\n"
    'HEALTHCHECK CMD ["python", "-c", "urlopen(\'http://127.0.0.1:8000/health/live\')"]\n'
)
GOOD_COMPOSE = (
    "services:\n  orchestrator:\n    healthcheck:\n"
    '      test: ["CMD", "curl", "http://127.0.0.1:8000/health/live"]\n'
)
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
SYSTEM_TOKEN = "system-secret-value"
VERIFIER_TOKEN = "verifier-secret-value"
UNIT = "11111111-1111-1111-1111-111111111111"


# --------------------------------------------------------------------------------------------
# git: a real origin and clone
# --------------------------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(work: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", message)
    return _git(work, "rev-parse", "HEAD")


@dataclass
class Repo:
    work: Path
    base: str  # what production serves
    migrated: str  # adds a migration on top of base
    plain: str  # changes no migration on top of migrated
    side: str  # never pushed to main
    readiness: str  # on main, Dockerfile health check reads readiness


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    _git(work, "config", "user.email", "t@example.invalid")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "-q", "-b", "main")
    base = _commit(
        work,
        {
            "Dockerfile": GOOD_DOCKERFILE,
            "docker-compose.yml": GOOD_COMPOSE,
            "migrations/versions/0001_first.py": "x = 1\n",
        },
        "base",
    )
    migrated = _commit(work, {"migrations/versions/0002_second.py": "x = 2\n"}, "migrate")
    plain = _commit(
        work, {"README.md": "hello\n", "migrations/versions/README": "notes\n"}, "plain"
    )
    readiness = _commit(
        work, {"Dockerfile": GOOD_DOCKERFILE.replace("/health/live", "/health/ready")}, "ready"
    )
    _git(work, "push", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-b", "side", plain)
    side = _commit(work, {"SIDE.md": "x\n"}, "side")
    _git(work, "checkout", "-q", migrated)
    return Repo(work, base, migrated, plain, side, readiness)


# --------------------------------------------------------------------------------------------
# production, gh and the binder
# --------------------------------------------------------------------------------------------


def _ledger_row(state: str, key: str = "unit-a", unit_id: str = UNIT) -> dict[str, Any]:
    return {"unit_id": unit_id, "unit_key": key, "unit_state": state}


@dataclass
class Production:
    ledger: Any = field(default_factory=lambda: [_ledger_row("completed")])
    ledger_status: int = 200
    histories: dict[str, Any] = field(default_factory=dict)
    live: list[Any] = field(default_factory=list)
    ready: tuple[int, dict[str, Any]] = (200, {"status": "ok"})
    openapi: Any = field(
        default_factory=lambda: {
            "paths": {"/health/live": {}},
            "components": {"schemas": {"Thing": {"properties": {"field_a": {}}}}},
        }
    )
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/v1/status-ledger":
            return httpx.Response(self.ledger_status, json=self.ledger)
        if path.startswith("/api/v1/work-units/") and path.endswith("/history"):
            return httpx.Response(200, json=self.histories.get(path.split("/")[4], []))
        if path == "/health/live":
            body = self.live.pop(0) if len(self.live) > 1 else self.live[0]
            return httpx.Response(200, json={"status": "ok", "revision": body})
        if path == "/health/ready":
            return httpx.Response(self.ready[0], json=self.ready[1])
        if path == "/openapi.json":
            return httpx.Response(200, json=self.openapi)
        return httpx.Response(404, json={})

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


@dataclass
class Gh:
    """GitHub as `gh` answers it: one release run per dispatch, numbered after the last."""

    built: str = ""
    conclusion: str = "success"
    appears: bool = True
    log_revision: str | None = None
    log_sha_tag_revision: str | None = None
    digests: tuple[str, ...] = (DIGEST,)
    dispatched: list[list[str]] = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[1:3] == ["workflow", "run"]:
            self.dispatched.append(argv)
            return _done("")
        if argv[1:3] == ["run", "list"]:
            latest = 100 + (len(self.dispatched) if self.appears else 0)
            return _done(json.dumps([{"databaseId": latest}]))
        if argv[1:3] == ["run", "view"] and "--log" in argv:
            return _done(self._log())
        if argv[1:3] == ["run", "view"]:
            url = f"https://github.com/AlobarQuest/orchestrator/actions/runs/{argv[3]}"
            return _done(
                json.dumps({"status": "completed", "conclusion": self.conclusion, "url": url})
            )
        raise AssertionError(f"unexpected gh call {argv}")

    def _log(self) -> str:
        revision = self.log_revision or self.built
        sha_tag = deploy.derivable_tag(self.log_sha_tag_revision or revision)
        lines = [f"build-and-push\tRefuse\tZ   SHA_TAG: {sha_tag}"]
        lines += [f"build-and-push\tVerify\tZ   DIGEST: {d}" for d in self.digests]
        lines += [
            f"build-and-push\tVerify\tZ   REVISION: {revision}",
            f"build-and-push\tVerify\tZ   SHA_TAG: {sha_tag}",
        ]
        return "\n".join(lines)


@dataclass
class Binder:
    code: int = 0
    calls: list[tuple[list[str], dict[str, str]]] = field(default_factory=list)

    def __call__(
        self, argv: list[str], env: dict[str, str] | None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, env or {}))
        return _done(json.dumps({"bound": 1}), self.code)


def _done(stdout: str, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, "")


@dataclass
class Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Harness:
    repo: Repo
    production: Production
    gh: Gh
    binder: Binder
    tmp: Path
    bearers: list[str] = field(default_factory=list)

    @property
    def state_path(self) -> Path:
        return self.tmp / "scratch" / "deploy.json"

    def world(self) -> World:
        clock = Clock()

        def run(argv, *, env=None, timeout=600):
            if argv[0] == "gh":
                return self.gh(argv)
            if argv[0] == str(self.binder_path):
                return self.binder(argv, env)
            assert argv[0] == "git", argv
            return subprocess.run(argv, capture_output=True, text=True, check=False)

        def bearer(uuid: str) -> str:
            self.bearers.append(uuid)
            return {deploy.SYSTEM_BEARER_UUID: SYSTEM_TOKEN}.get(uuid, VERIFIER_TOKEN)

        return World(
            run=run,
            bearer=bearer,
            transport=httpx.MockTransport(self.production.handle),
            sleep=clock.sleep,
            clock=clock,
            checkout=self.repo.work,
            binder=self.binder_path,
        )

    @property
    def binder_path(self) -> Path:
        return self.tmp / "venv" / "bin" / "image-release"

    def prepare(self, ref: str, *extra: str, enabled: str = "false", path: str = "/") -> int:
        self.gh.built = self.gh.built or ref
        argv = [
            "prepare",
            "--ref",
            ref,
            "--state",
            str(self.state_path),
            "--coolify-health-check-enabled",
            enabled,
            "--coolify-health-check-path",
            path,
            *extra,
        ]
        return deploy.main(argv, self.world())

    def finish(self, observed: str = DIGEST) -> int:
        argv = ["finish", "--state", str(self.state_path), "--observed-digest", observed]
        return deploy.main(argv, self.world())


@pytest.fixture
def h(repo: Repo, tmp_path: Path) -> Harness:
    production = Production(live=[repo.base])
    return Harness(repo, production, Gh(), Binder(), tmp_path)


def _output(capsys: pytest.CaptureFixture[str]) -> tuple[dict[str, Any], str]:
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


# --------------------------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------------------------


def test_prepare_records_the_pre_swap_revision_builds_and_prints_the_steps(
    h: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    h.production.ledger = [_ledger_row("completed"), _ledger_row("draft", "unit-d")]

    assert h.prepare(h.repo.migrated, "--label", "deploycmd") == EXIT_OK

    out, steps = _output(capsys)
    state = State.load(h.state_path)
    assert state.previous_commit == h.repo.base
    assert state.built_commit == h.repo.migrated
    assert state.pushed_digest == DIGEST
    assert state.migrations == ["migrations/versions/0002_second.py"]
    assert state.tag_name == f"{h.repo.migrated[:7]}-deploycmd-amd64"
    assert out["draft_units"] == ["unit-d"]
    (dispatch,) = h.gh.dispatched
    assert dispatch[dispatch.index("--ref") + 1] == "main"
    assert f"ref={h.repo.migrated}" in dispatch
    assert f'docker_registry_image_tag="{state.tag_name}"' in steps
    assert "alembic upgrade head" in steps
    assert steps.index("coolify_update_application") < steps.index("alembic upgrade head")
    assert steps.index("alembic upgrade head") < steps.index("coolify_deploy")
    assert f"orchestrator@{DIGEST}" in steps
    assert SYSTEM_TOKEN not in steps + json.dumps(out)
    ledger = next(r for r in h.production.requests if r.url.path == "/api/v1/status-ledger")
    assert ledger.headers["Authorization"] == f"Bearer {SYSTEM_TOKEN}"
    assert ledger.url.params["include_inactive"] == "true"
    assert ledger.headers["User-Agent"] == deploy.USER_AGENT


def test_prepare_with_no_migration_says_so(h: Harness, capsys: pytest.CaptureFixture[str]) -> None:
    h.production.live = [h.repo.migrated]

    assert h.prepare(h.repo.plain) == EXIT_OK

    _, steps = _output(capsys)
    assert State.load(h.state_path).migrations == []
    assert "No migration" in steps
    assert "alembic upgrade head" not in steps


def test_prepare_refuses_a_build_whose_dockerfile_health_check_reads_readiness(
    h: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    assert h.prepare(h.repo.readiness) == EXIT_REFUSED

    out, _ = _output(capsys)
    assert "Dockerfile" in out["reason"]
    assert h.gh.calls == [] and h.production.requests == []


def test_prepare_reads_the_built_commit_not_the_working_tree(
    h: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    (h.repo.work / "Dockerfile").write_text("HEALTHCHECK CMD /health/ready\n")

    assert h.prepare(h.repo.migrated) == EXIT_OK


def test_prepare_refuses_a_compose_health_check_that_reads_readiness(
    h: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(h.repo.work, "checkout", "-q", "main")
    sha = _commit(
        h.repo.work,
        {"docker-compose.yml": GOOD_COMPOSE.replace("/health/live", "/health/ready")},
        "compose ready",
    )
    _git(h.repo.work, "push", "-q", "origin", "main")

    assert h.prepare(sha) == EXIT_REFUSED

    assert "docker-compose.yml" in _output(capsys)[0]["reason"]


def test_prepare_refuses_an_enabled_coolify_health_check_on_readiness(
    h: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    assert h.prepare(h.repo.migrated, enabled="true", path="/health/ready") == EXIT_REFUSED

    assert "Coolify" in _output(capsys)[0]["reason"]
    assert h.gh.calls == []


def test_a_disabled_coolify_health_check_does_not_refuse(h: Harness) -> None:
    assert h.prepare(h.repo.migrated, enabled="false", path="/health/ready") == EXIT_OK


@pytest.mark.parametrize("state", ["claimed", "executing", "submitted", "verifying"])
def test_prepare_refuses_while_a_unit_is_live(
    h: Harness, state: str, capsys: pytest.CaptureFixture[str]
) -> None:
    h.production.ledger = [_ledger_row("completed", "done"), _ledger_row(state, "busy")]

    assert h.prepare(h.repo.migrated) == EXIT_CONDITION

    assert "busy" in _output(capsys)[0]["reason"]
    assert h.gh.dispatched == []


def _history(*events: tuple[str, str | None]) -> list[dict[str, Any]]:
    return [{"action": action, "to_state": to_state} for action, to_state in events]


def test_prepare_refuses_while_a_dispatched_run_has_not_claimed_its_ready_unit(
    h: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    h.production.ledger = [_ledger_row("ready", "starting")]
    h.production.histories[UNIT] = _history(
        ("work_unit.transitioned", "ready"), ("dispatch.dispatched", "ready")
    )

    assert h.prepare(h.repo.migrated) == EXIT_CONDITION

    assert "starting" in _output(capsys)[0]["reason"]
    assert h.gh.dispatched == []


@pytest.mark.parametrize(
    "history",
    [
        _history(("work_unit.transitioned", "ready")),
        _history(("dispatch.skipped", "ready")),
        _history(
            ("dispatch.dispatched", "ready"),
            ("work_unit.transitioned", "claimed"),
            ("work_unit.transitioned", "failed"),
            ("work_unit.transitioned", "ready"),
        ),
    ],
    ids=["never-dispatched", "skipped", "dispatched-then-claimed"],
)
def test_a_ready_unit_without_an_unclaimed_dispatch_does_not_refuse(
    h: Harness, history: list[dict[str, Any]]
) -> None:
    h.production.ledger = [_ledger_row("ready", "idle")]
    h.production.histories[UNIT] = history

    assert h.prepare(h.repo.migrated) == EXIT_OK


@pytest.mark.parametrize(
    ("status", "ledger"),
    [(401, []), (200, {"items": []}), (200, [{"unit_state": "completed"}])],
    ids=["unauthorized", "not-a-list", "row-missing-keys"],
)
def test_prepare_refuses_an_unreadable_ledger(h: Harness, status: int, ledger: Any) -> None:
    h.production.ledger_status = status
    h.production.ledger = ledger

    assert h.prepare(h.repo.migrated) == EXIT_REFUSED
    assert h.gh.dispatched == []


@pytest.mark.parametrize("revision", [None, "abc1234"])
def test_prepare_refuses_when_the_outgoing_image_states_no_revision(
    h: Harness, revision: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    h.production.live = [revision]

    assert h.prepare(h.repo.migrated) == EXIT_REFUSED
    assert "cannot state what it carries" in _output(capsys)[0]["reason"]
    assert h.gh.dispatched == []


def test_prepare_stops_when_production_already_serves_the_commit(h: Harness) -> None:
    h.production.live = [h.repo.migrated]

    assert h.prepare(h.repo.migrated) == EXIT_CONDITION
    assert h.gh.dispatched == []


def test_prepare_refuses_a_commit_that_is_not_on_main(h: Harness) -> None:
    assert h.prepare(h.repo.side) == EXIT_REFUSED
    assert h.gh.calls == []


def test_prepare_refuses_a_pre_swap_revision_the_checkout_does_not_hold(
    h: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    h.production.live = ["f" * 40]

    assert h.prepare(h.repo.migrated) == EXIT_REFUSED
    assert "fetch and re-run" in _output(capsys)[0]["reason"]
    assert h.gh.dispatched == []


def test_prepare_refuses_a_rollback(h: Harness) -> None:
    h.production.live = [h.repo.plain]

    assert h.prepare(h.repo.migrated) == EXIT_REFUSED
    assert h.gh.dispatched == []


@pytest.mark.parametrize(
    "gh",
    [
        Gh(conclusion="failure"),
        Gh(appears=False),
        Gh(log_revision="c" * 40),
        Gh(log_sha_tag_revision="c" * 40),
        Gh(log_revision="c" * 40, log_sha_tag_revision="__built__"),
        Gh(digests=(DIGEST, OTHER_DIGEST)),
        Gh(digests=()),
    ],
    ids=[
        "build-failed",
        "run-never-appeared",
        "someone-elses-run",
        "someone-elses-tag",
        "someone-elses-revision",
        "two-digests",
        "no-digest",
    ],
)
def test_prepare_refuses_a_build_it_cannot_attribute(h: Harness, gh: Gh) -> None:
    if gh.log_sha_tag_revision == "__built__":
        gh.log_sha_tag_revision = h.repo.migrated
    h.gh = gh

    assert h.prepare(h.repo.migrated) == EXIT_REFUSED
    assert not h.state_path.exists()


def test_prepare_refuses_a_malformed_label_before_anything_runs(h: Harness) -> None:
    assert h.prepare(h.repo.migrated, "--label", "Bad Label") == EXIT_USAGE
    assert h.gh.calls == [] and h.production.requests == []


def test_a_second_prepare_resumes_rather_than_rebuilding(
    h: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    assert h.prepare(h.repo.migrated) == EXIT_OK
    capsys.readouterr()

    assert h.prepare(h.repo.migrated) == EXIT_OK

    out, steps = _output(capsys)
    assert out["resumed"] is True
    assert len(h.gh.dispatched) == 1
    assert "coolify_deploy" in steps


def test_a_second_prepare_for_another_commit_refuses(h: Harness) -> None:
    assert h.prepare(h.repo.migrated) == EXIT_OK

    assert h.prepare(h.repo.plain) == EXIT_REFUSED
    assert len(h.gh.dispatched) == 1


# --------------------------------------------------------------------------------------------
# finish
# --------------------------------------------------------------------------------------------


def _state(repo: Repo, **overrides: Any) -> State:
    values: dict[str, Any] = {
        "built_commit": repo.migrated,
        "previous_commit": repo.base,
        "label": "",
        "tag": deploy.readable_tag(repo.migrated),
        "pushed_digest": DIGEST,
        "workflow_run_url": "https://github.com/AlobarQuest/orchestrator/actions/runs/101",
        "migrations": ["migrations/versions/0002_second.py"],
        "expect_schema": ["Thing", "Thing.field_a"],
    }
    values.update(overrides)
    return State(**values)


@pytest.fixture
def prepared(h: Harness) -> Harness:
    _state(h.repo).save(h.state_path)
    h.production.live = [h.repo.base, h.repo.base, h.repo.migrated]
    return h


def test_finish_verifies_and_binds_with_the_recorded_pre_swap_revision(
    prepared: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    assert prepared.finish() == EXIT_OK

    out, _ = _output(capsys)
    assert out["result"] == "deployed"
    assert "no migration drift" in out["health_ready"]
    ((argv, env),) = prepared.binder.calls
    assert argv[argv.index("--previous-commit") + 1] == prepared.repo.base
    assert argv[argv.index("--built-commit") + 1] == prepared.repo.migrated
    assert argv[argv.index("--observed-digest") + 1] == DIGEST
    assert argv[argv.index("--tag") + 1] == f"{prepared.repo.migrated[:7]}-amd64"
    assert env["ACTIVATION_BIND_TOKEN"] == SYSTEM_TOKEN
    assert env["IMAGE_VERIFY_TOKEN"] == VERIFIER_TOKEN
    assert SYSTEM_TOKEN not in argv and VERIFIER_TOKEN not in argv
    assert prepared.bearers == [deploy.SYSTEM_BEARER_UUID, deploy.VERIFIER_BEARER_UUID]


def test_finish_never_reads_the_pre_swap_revision_again(prepared: Harness) -> None:
    """After the swap `/health/live` serves the built commit, and reading it there would turn every
    unbound unit into `shipped_earlier`. The value bound is the recorded one, whatever is served."""
    prepared.production.live = [prepared.repo.migrated]

    assert prepared.finish() == EXIT_OK

    ((argv, _),) = prepared.binder.calls
    assert argv[argv.index("--previous-commit") + 1] == prepared.repo.base
    assert prepared.production.paths().count("/health/live") == 1
    assert "/api/v1/status-ledger" not in prepared.production.paths()


def test_finish_refuses_a_digest_that_is_not_the_pushed_one(prepared: Harness) -> None:
    assert prepared.finish(OTHER_DIGEST) == EXIT_CONDITION
    assert prepared.production.requests == [] and prepared.binder.calls == []


def test_finish_refuses_a_malformed_digest(prepared: Harness) -> None:
    assert prepared.finish("sha256:short") == EXIT_USAGE
    assert prepared.binder.calls == []


@pytest.mark.parametrize("content", [None, "{not json", json.dumps({"built_commit": "x"})])
def test_finish_refuses_a_missing_or_malformed_state(h: Harness, content: str | None) -> None:
    if content is not None:
        h.state_path.parent.mkdir(parents=True)
        h.state_path.write_text(content)

    assert h.finish() == EXIT_REFUSED
    assert h.binder.calls == []


@pytest.mark.parametrize(
    "overrides",
    [{"previous_commit": "abc1234"}, {"pushed_digest": "sha256:short"}, {"schema": 99}],
    ids=["short-previous", "bad-digest", "unknown-schema"],
)
def test_finish_refuses_a_state_it_does_not_recognise(
    h: Harness, overrides: dict[str, Any]
) -> None:
    _state(h.repo, **overrides).save(h.state_path)
    h.production.live = [h.repo.migrated]

    assert h.finish() == EXIT_REFUSED
    assert h.binder.calls == []


def test_finish_refuses_a_checkout_that_is_not_the_built_commit(prepared: Harness) -> None:
    _git(prepared.repo.work, "checkout", "-q", prepared.repo.plain)

    assert prepared.finish() == EXIT_REFUSED
    assert prepared.binder.calls == []


def test_finish_stops_when_the_built_commit_is_never_served(prepared: Harness) -> None:
    prepared.production.live = [prepared.repo.base]

    assert prepared.finish() == EXIT_CONDITION
    assert prepared.binder.calls == []


def test_finish_stops_at_once_on_a_revision_nobody_built_here(prepared: Harness) -> None:
    prepared.production.live = ["d" * 40, prepared.repo.migrated]

    assert prepared.finish() == EXIT_CONDITION
    assert prepared.production.paths().count("/health/live") == 1
    assert prepared.binder.calls == []


def test_finish_requires_readiness(prepared: Harness, capsys: pytest.CaptureFixture[str]) -> None:
    prepared.production.ready = (503, {"status": "unavailable", "reason": "migration_drift"})

    assert prepared.finish() == EXIT_CONDITION
    assert "migration_drift" in _output(capsys)[0]["reason"]
    assert prepared.binder.calls == []


def test_finish_refuses_an_openapi_document_with_no_paths(prepared: Harness) -> None:
    prepared.production.openapi = {"paths": {}}

    assert prepared.finish() == EXIT_REFUSED
    assert prepared.binder.calls == []


@pytest.mark.parametrize("expected", [["Missing"], ["Thing.field_b"]])
def test_finish_stops_when_the_served_schema_lacks_an_expected_model(
    h: Harness, expected: list[str]
) -> None:
    _state(h.repo, expect_schema=expected).save(h.state_path)
    h.production.live = [h.repo.migrated]

    assert h.finish() == EXIT_CONDITION
    assert h.binder.calls == []


@pytest.mark.parametrize(
    ("binder_code", "expected"),
    [(2, EXIT_CONDITION), (3, EXIT_REFUSED), (1, EXIT_REFUSED)],
)
def test_finish_carries_the_binder_outcome(
    prepared: Harness, binder_code: int, expected: int
) -> None:
    prepared.binder.code = binder_code

    assert prepared.finish() == expected


def test_state_round_trips(repo: Repo, tmp_path: Path) -> None:
    state = _state(repo)
    path = tmp_path / "nested" / "state.json"

    state.save(path)

    assert State.load(path) == state
    assert asdict(State.load(path)) == asdict(state)


def test_the_readiness_predicate_names_every_file_that_reads_readiness() -> None:
    files = {"Dockerfile": "HEALTHCHECK /health/ready", "docker-compose.yml": "/health/live"}

    assert deploy.readiness_consumers(files) == ["Dockerfile"]
    assert deploy.readiness_consumers({"Dockerfile": GOOD_DOCKERFILE}) == []
