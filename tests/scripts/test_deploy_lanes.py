"""The deploy command's lane phases (ADR-0059 decision 1 and Amendment 1).

As in `test_deploy_orchestrator.py`, `git` is REAL, against a throwaway origin and clone, so the
descendant refusal and the registry read at `origin/main` ask what git answers. Production's
`/health/live`, `gh`, the Healthchecks listing and the wall clock are fakes behind `World`.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import deploy_orchestrator as deploy
from scripts.deploy_orchestrator import (
    EXIT_CONDITION,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_TIMED_OUT,
    EXIT_USAGE,
    World,
)

ROOT = Path(__file__).resolve().parents[2]
WATCH_COMPOSE = """services:
  execution-watch:
    image: ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}
    command: ["execution-watch"]
    environment:
      BWS_ACCESS_TOKEN: ${EXECUTION_WATCH_BWS_ACCESS_TOKEN}
      LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}
    volumes:
      - sds-lanes-execution-watch-state:/var/lib/sds/execution-watch
    restart: unless-stopped

volumes:
  sds-lanes-execution-watch-state:
"""
# A stateless lane: it mounts no state volume, so its phases read and check none.
LEDGER_COMPOSE = """services:
  landing-ledger:
    image: ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}
    command: ["landing-ledger"]
    environment:
      BWS_ACCESS_TOKEN: ${LANDING_LEDGER_BWS_ACCESS_TOKEN}
      LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}
    restart: unless-stopped
"""
WATCH_TOKEN_ENTRY = """
[[credential]]
id = "bws-lane-execution-watch-token"

  [[credential.consumer]]
  kind = "coolify-env"
  instance = "prod"
  resource_type = "service"
  uuid = "ewuuid00000000000000000a"
  key = "EXECUTION_WATCH_BWS_ACCESS_TOKEN"
  redeploy = false
"""
# The lanes' secrets at `added`: two execution-watch reads, and one secret no lane reads.
OBSERVER = "11111111-1111-4111-8111-111111111111"
ALERT = "22222222-2222-4222-8222-222222222222"
SECRETS = f"""consumption = "environment-injection"

[[secret]]
uuid = "{OBSERVER}"
lanes = ["execution-watch"]

[[secret]]
uuid = "00000000-0000-4000-8000-000000000001"

[[secret]]
uuid = "{ALERT}"
lanes = ["execution-watch"]
"""
WATCH_UUID = "ewuuid00000000000000000a"
LEDGER_UUID = "lluuid00000000000000000a"
WATCH_VOLUME = f"{WATCH_UUID}_sds-lanes-execution-watch-state"
# 10:00 in New York (EST), outside estate landing's window; 03:00 is inside it.
QUIET = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
LANDING = datetime(2026, 11, 6, 8, 0, tzinfo=UTC)


def lane_digest(sha: str) -> str:
    return "sha256:" + hashlib.sha256(sha.encode()).hexdigest()


def tag(sha: str) -> str:
    return f"sha-{sha}"


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


def _done(stdout: str, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, "")


@dataclass
class Lanes:
    """A repository whose `main` is old -> added (the lanes' composes) -> following -> later."""

    tmp: Path
    work: Path
    old: str
    added: str
    following: str
    later: str
    served: str = ""
    now: datetime = QUIET
    checks: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"name": "sds-execution-watch", "started": False, "status": "up"},
            {"name": "sds-landing-ledger", "started": False, "status": "up"},
            {"name": "sds-pin-watcher", "started": True, "status": "up"},
        ]
    )
    listing_reads: int = 0
    successes: dict[str, int] = field(default_factory=dict)

    def _gh(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        commits = [self.old, self.added, self.following, self.later]
        if argv[1:3] == ["run", "list"]:
            runs = [
                {
                    "databaseId": index + 1,
                    "displayTitle": f"Release image {sha}",
                    "url": f"https://example.invalid/runs/{index + 1}",
                    "status": "completed",
                    "conclusion": "success",
                }
                for index, sha in enumerate(commits)
                for _ in range(self.successes.get(sha, 1))
            ]
            return _done(json.dumps(runs))
        if argv[1:3] == ["run", "view"] and "--log" in argv:
            sha = commits[int(argv[3]) - 1]
            step = "build-and-push\tCheck the lane image's pushed provenance\t2026-11-06T10:00:00Z"
            sha_tag = deploy.derivable_tag(sha, deploy.LANE_IMAGE)
            return _done(
                f"{step}   LANE_DIGEST: {lane_digest(sha)}\n"
                f"{step}   LANE_REVISION: {sha}\n"
                f"{step}   LANE_SHA_TAG: {sha_tag}\n"
            )
        raise AssertionError(f"unexpected gh call {argv}")

    def _listing(self) -> list[dict[str, Any]]:
        self.listing_reads += 1
        return self.checks

    def world(self) -> World:
        def run(argv, *, env=None, timeout=600):
            if argv[0] == "gh":
                return self._gh(argv)
            assert argv[0] == "git", argv
            return subprocess.run(argv, capture_output=True, text=True, check=False)

        def production(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health/live":
                return httpx.Response(200, json={"status": "ok", "revision": self.served})
            return httpx.Response(404, json={})

        def no_bearer(uuid: str) -> str:
            raise AssertionError("the lane phases read no bearer")

        return World(
            run=run,
            bearer=no_bearer,
            transport=httpx.MockTransport(production),
            checkout=self.work,
            lanes_state_dir=self.tmp / "deploy-lanes",
            now=lambda: self.now,
            checks=self._listing,
        )

    def main(self, *argv: str) -> int:
        return deploy.main(list(argv), self.world())

    def runs(self, lane: str) -> list[deploy.LaneRun]:
        return deploy.load_runs(self.world(), lane)

    # ---- the steps a session takes ----------------------------------------------------------

    def add(self, lane: str, sha: str, *extra: str) -> int:
        return self.main(
            "lanes-prepare",
            "--lane",
            lane,
            "--add",
            "--tag",
            tag(sha),
            "--resource-uuid",
            LEDGER_UUID if lane == "landing-ledger" else WATCH_UUID,
            *(extra or ("--schedule-written", "false")),
        )

    def recheck(self, lane: str, *change: str) -> int:
        return self.main("lanes-recheck", "--lane", lane, *change)

    def finish(self, lane: str, *change_and_reads: str) -> int:
        return self.main("lanes-finish", "--lane", lane, *change_and_reads)

    def added_ledger(self) -> None:
        self.served = self.added
        change = ("--add", "--tag", tag(self.added))
        assert self.add("landing-ledger", self.added) == EXIT_OK
        assert self.recheck("landing-ledger", *change) == EXIT_OK
        reads = (
            "--observed-digest",
            lane_digest(self.added),
            "--observed-schedule",
            "false",
            "--token-present",
            "true",
        )
        assert self.finish("landing-ledger", *change, *reads) == EXIT_OK

    def added_watch(self) -> None:
        self.served = self.added
        change = ("--add", "--tag", tag(self.added))
        assert self.add("execution-watch", self.added) == EXIT_OK
        assert self.recheck("execution-watch", *change) == EXIT_OK
        reads = (
            "--observed-digest",
            lane_digest(self.added),
            "--observed-schedule",
            "false",
            "--observed-volume",
            WATCH_VOLUME,
            "--token-present",
            "true",
        )
        assert self.finish("execution-watch", *change, *reads) == EXIT_OK

    def prepare_token(self, lane: str = "execution-watch", *extra: str) -> int:
        reads = ["--running-tag", tag(self.added)]
        if lane in deploy.STATEFUL_LANES:
            reads += ["--running-volume", WATCH_VOLUME]
        return self.main("lanes-prepare", "--lane", lane, "--token", *reads, *extra)

    def read_result(self, text: str) -> str:
        path = self.tmp / "read-result.txt"
        path.write_text(text)
        return str(path)

    def token_reads(self, *, present: str = "true", result: str | None = None) -> list[str]:
        """The reads of a container redeployed by `--token`, unchanged but for the token."""
        result = f"{OBSERVER} ok\n{ALERT} ok\n" if result is None else result
        return [
            *_watch_reads(self.added),
            "--token-present",
            present,
            "--read-result",
            self.read_result(result),
        ]

    def prepared_token(self) -> None:
        self.added_watch()
        assert self.prepare_token() == EXIT_OK
        assert self.recheck("execution-watch", "--token") == EXIT_OK

    def prepare_tag(self, lane: str, sha: str, *extra: str, running: str | None = None) -> int:
        reads = ["--running-tag", running or tag(self.added)]
        if lane in deploy.STATEFUL_LANES:
            reads += ["--running-volume", WATCH_VOLUME]
        return self.main("lanes-prepare", "--lane", lane, "--tag", tag(sha), *reads, *extra)


@pytest.fixture
def lanes(tmp_path: Path) -> Lanes:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    _git(work, "config", "user.email", "t@example.invalid")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "-q", "-b", "main")
    old = _commit(work, {".cred-consumers.toml": "version = 1\n"}, "before the lanes")
    added = _commit(
        work,
        {
            "lanes/execution-watch/compose.yaml": WATCH_COMPOSE,
            "lanes/landing-ledger/compose.yaml": LEDGER_COMPOSE,
            ".bws-secrets.toml": SECRETS,
        },
        "the lanes' composes",
    )
    following = _commit(
        work,
        {
            "lanes/execution-watch/compose.yaml": WATCH_COMPOSE.replace(
                "restart", "stop_grace_period: 30s\n    restart"
            )
        },
        "a compose edit",
    )
    later = _commit(work, {"README.md": "later\n"}, "a later release")
    _git(work, "push", "-q", "origin", "main")
    return Lanes(tmp_path, work, old, added, following, later)


def _status(lanes: Lanes, lane: str) -> list[str]:
    return [run.status for run in lanes.runs(lane)]


# ---- the quiet slot -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "check", "expected"),
    [
        (QUIET, {"started": False, "status": "up"}, EXIT_OK),
        (LANDING, {"started": False, "status": "up"}, EXIT_CONDITION),
        (QUIET, {"started": True, "status": "up"}, EXIT_CONDITION),
        (QUIET, {"started": True, "status": "grace"}, EXIT_CONDITION),
        # A pass that stopped without its final ping: `down` already reports it.
        (QUIET, {"started": True, "status": "down"}, EXIT_OK),
        (QUIET, {"status": "up"}, EXIT_REFUSED),
    ],
)
def test_each_quiet_slot_condition(
    lanes: Lanes, now: datetime, check: dict[str, Any], expected: int
) -> None:
    lanes.served, lanes.now = lanes.added, now
    lanes.checks = [{"name": "sds-execution-watch", **check}]
    assert lanes.add("execution-watch", lanes.added) == expected
    assert lanes.listing_reads == 1


@pytest.mark.parametrize("copies", [0, 2])
def test_a_check_that_isnt_listed_exactly_once_refuses(lanes: Lanes, copies: int) -> None:
    lanes.served = lanes.added
    lanes.checks = [{"name": "sds-execution-watch", "started": False, "status": "up"}] * copies
    assert lanes.add("execution-watch", lanes.added) == EXIT_REFUSED


def test_another_lanes_running_pass_doesnt_hold_this_lane(lanes: Lanes) -> None:
    lanes.served = lanes.added
    lanes.checks = [
        {"name": "sds-execution-watch", "started": False, "status": "up"},
        {"name": "sds-pin-watcher", "started": True, "status": "up"},
    ]
    assert lanes.add("execution-watch", lanes.added) == EXIT_OK


# ---- the deadline ---------------------------------------------------------------------------


def test_a_re_run_resumes_the_deadline_and_a_timeout_exits_4(
    lanes: Lanes, capsys: pytest.CaptureFixture[str]
) -> None:
    lanes.served, lanes.now = lanes.added, LANDING
    assert lanes.add("execution-watch", lanes.added) == EXIT_CONDITION
    [first] = lanes.runs("execution-watch")
    assert first.status == "waiting"
    assert datetime.fromisoformat(first.deadline) == LANDING + timedelta(hours=6)

    lanes.now = LANDING + timedelta(hours=1)
    assert lanes.add("execution-watch", lanes.added) == EXIT_CONDITION
    runs = lanes.runs("execution-watch")
    assert [(r.run_id, r.deadline) for r in runs] == [(first.run_id, first.deadline)]

    capsys.readouterr()
    lanes.now = LANDING + timedelta(hours=6)
    assert lanes.add("execution-watch", lanes.added) == EXIT_TIMED_OUT
    assert _status(lanes, "execution-watch") == ["timed_out"]
    out = capsys.readouterr()
    assert json.loads(out.out)["result"] == "timed_out"
    [line] = out.err.strip().splitlines()
    assert "execution-watch" in line and f"add:{lanes.added}" in line and first.deadline in line

    # The next run records a new deadline.
    lanes.now = QUIET + timedelta(days=1)
    assert lanes.add("execution-watch", lanes.added) == EXIT_OK
    newest = lanes.runs("execution-watch")[-1]
    assert newest.run_id != first.run_id
    assert datetime.fromisoformat(newest.deadline) == lanes.now + timedelta(hours=6)


def test_a_run_for_another_change_supersedes_the_waiting_one(lanes: Lanes) -> None:
    lanes.added_ledger()
    lanes.served, lanes.now = lanes.later, LANDING + timedelta(days=1)
    assert lanes.prepare_tag("landing-ledger", lanes.later) == EXIT_CONDITION
    waiting = lanes.runs("landing-ledger")[-1]
    assert waiting.status == "waiting"
    schedule = ("--schedule", "true", "--running-schedule", "false")
    assert lanes.main("lanes-prepare", "--lane", "landing-ledger", *schedule) == EXIT_CONDITION
    by_id = {r.run_id: r for r in lanes.runs("landing-ledger")}
    assert by_id[waiting.run_id].status == "superseded"
    assert _status(lanes, "landing-ledger")[-1] == "waiting"


def test_a_waiting_run_doesnt_block_a_re_run_and_an_unfinished_one_does(lanes: Lanes) -> None:
    lanes.served = lanes.added
    change = ("--add", "--tag", tag(lanes.added))
    assert lanes.add("landing-ledger", lanes.added) == EXIT_OK
    assert lanes.add("landing-ledger", lanes.added) == EXIT_OK
    assert lanes.recheck("landing-ledger", *change) == EXIT_OK
    assert lanes.add("landing-ledger", lanes.added) == EXIT_REFUSED
    assert _status(lanes, "landing-ledger") == ["unfinished"]


# ---- lanes-recheck --------------------------------------------------------------------------


def test_recheck_marks_the_run_unfinished_only_on_exit_0(lanes: Lanes) -> None:
    lanes.served = lanes.added
    change = ("--add", "--tag", tag(lanes.added))
    assert lanes.recheck("execution-watch", *change) == EXIT_REFUSED  # nothing prepared
    assert lanes.add("execution-watch", lanes.added) == EXIT_OK
    lanes.checks = [{"name": "sds-execution-watch", "started": True, "status": "up"}]
    assert lanes.recheck("execution-watch", *change) == EXIT_CONDITION
    assert _status(lanes, "execution-watch") == ["waiting"]
    # The slot closed, so recheck refuses until lanes-prepare finds it open again.
    lanes.checks = [{"name": "sds-execution-watch", "started": False, "status": "up"}]
    assert lanes.recheck("execution-watch", *change) == EXIT_REFUSED
    assert lanes.add("execution-watch", lanes.added) == EXIT_OK
    assert lanes.recheck("execution-watch", *change) == EXIT_OK
    assert _status(lanes, "execution-watch") == ["unfinished"]


# ---- the --add refusals ---------------------------------------------------------------------


def test_add_refuses_a_schedule_written_other_than_false(lanes: Lanes) -> None:
    lanes.served = lanes.added
    assert lanes.add("landing-ledger", lanes.added, "--schedule-written", "true") == EXIT_REFUSED
    assert _status(lanes, "landing-ledger") == ["refused"]
    assert "--schedule-written must be false" in lanes.runs("landing-ledger")[0].reason


@pytest.mark.parametrize(
    "mapping", ["${LANE_SCHEDULE_ENABLED:-true}", "${LANE_SCHEDULE_ENABLED}", "true", "false"]
)
def test_add_refuses_a_compose_whose_schedule_doesnt_default_to_false(
    lanes: Lanes, mapping: str
) -> None:
    edited = LEDGER_COMPOSE.replace("${LANE_SCHEDULE_ENABLED:-false}", mapping)
    sha = _commit(lanes.work, {"lanes/landing-ledger/compose.yaml": edited}, "a bad default")
    _git(lanes.work, "push", "-q", "origin", "main")
    lanes.served = sha
    lanes.successes[sha] = 1
    lanes.later = sha  # the gh fake serves the four named commits
    assert lanes.add("landing-ledger", sha) == EXIT_REFUSED
    assert "LANE_SCHEDULE_ENABLED" in lanes.runs("landing-ledger")[-1].reason


def test_an_adds_finish_requires_the_token_read_and_a_present_token(lanes: Lanes) -> None:
    lanes.served = lanes.added
    change = ("--add", "--tag", tag(lanes.added))
    assert lanes.add("execution-watch", lanes.added) == EXIT_OK
    assert lanes.recheck("execution-watch", *change) == EXIT_OK
    reads = (
        "--observed-digest",
        lane_digest(lanes.added),
        "--observed-schedule",
        "false",
        "--observed-volume",
        WATCH_VOLUME,
    )
    assert lanes.finish("execution-watch", *change, *reads) == EXIT_USAGE
    assert lanes.finish("execution-watch", *change, *reads, "--token-present", "false") == (
        EXIT_CONDITION
    )
    assert _status(lanes, "execution-watch") == ["unfinished"]
    assert lanes.finish("execution-watch", *change, *reads, "--token-present", "true") == EXIT_OK
    assert _status(lanes, "execution-watch") == ["finished"]
    assert lanes.runs("execution-watch")[0].volume == WATCH_VOLUME


def test_add_refuses_a_compose_that_maps_no_lane_token(lanes: Lanes) -> None:
    edited = WATCH_COMPOSE.replace(
        "      BWS_ACCESS_TOKEN: ${EXECUTION_WATCH_BWS_ACCESS_TOKEN}\n", ""
    )
    sha = _commit(lanes.work, {"lanes/execution-watch/compose.yaml": edited}, "no token")
    _git(lanes.work, "push", "-q", "origin", "main")
    lanes.served = lanes.later = sha
    assert lanes.add("execution-watch", sha) == EXIT_REFUSED
    assert "BWS_ACCESS_TOKEN" in lanes.runs("execution-watch")[-1].reason


@pytest.mark.parametrize("source", ["./state", "/srv/state", "${STATE_VOLUME}"])
def test_add_refuses_a_state_mount_that_isnt_a_named_volume(lanes: Lanes, source: str) -> None:
    edited = WATCH_COMPOSE.replace("- sds-lanes-execution-watch-state:", f"- {source}:")
    sha = _commit(lanes.work, {"lanes/execution-watch/compose.yaml": edited}, "a bind mount")
    _git(lanes.work, "push", "-q", "origin", "main")
    lanes.served = lanes.later = sha
    assert lanes.add("execution-watch", sha) == EXIT_REFUSED
    assert "no named volume" in lanes.runs("execution-watch")[-1].reason


def test_a_refused_change_keeps_the_waiting_runs_deadline(lanes: Lanes) -> None:
    lanes.added_watch()
    lanes.served, lanes.now = lanes.following, LANDING + timedelta(days=1)
    assert lanes.prepare_tag("execution-watch", lanes.following) == EXIT_CONDITION
    waiting = lanes.runs("execution-watch")[-1]
    lanes.checks = []  # the next change's slot read refuses
    lanes.now += timedelta(hours=1)
    schedule = ("--schedule", "true", "--running-schedule", "false")
    volume = ("--running-volume", WATCH_VOLUME)
    assert lanes.main("lanes-prepare", "--lane", "execution-watch", *schedule, *volume) == (
        EXIT_REFUSED
    )
    by_id = {r.run_id: r for r in lanes.runs("execution-watch")}
    assert by_id[waiting.run_id].status == "superseded"
    newest = lanes.runs("execution-watch")[-1]
    assert (newest.change_key, newest.status) == ("schedule:true", "waiting")
    assert datetime.fromisoformat(newest.deadline) == lanes.now + timedelta(hours=6)


def test_add_refuses_a_tag_production_doesnt_serve(lanes: Lanes) -> None:
    lanes.served = lanes.later
    assert lanes.add("landing-ledger", lanes.added) == EXIT_REFUSED
    assert "not the served orchestrator tag" in lanes.runs("landing-ledger")[0].reason


def test_a_tag_with_two_successful_releases_refuses(lanes: Lanes) -> None:
    lanes.served = lanes.added
    lanes.successes[lanes.added] = 2
    assert lanes.add("landing-ledger", lanes.added) == EXIT_REFUSED


# ---- lanes-finish ---------------------------------------------------------------------------


def _watch_reads(sha: str, volume: str = WATCH_VOLUME, schedule: str = "false") -> list[str]:
    return [
        "--observed-digest",
        lane_digest(sha),
        "--observed-schedule",
        schedule,
        "--observed-volume",
        volume,
    ]


def test_finish_checks_the_digest_the_schedule_and_the_volume(
    lanes: Lanes, capsys: pytest.CaptureFixture[str]
) -> None:
    lanes.added_watch()
    lanes.served = lanes.following
    change = ("--tag", tag(lanes.following))
    assert lanes.prepare_tag("execution-watch", lanes.following) == EXIT_OK
    assert lanes.recheck("execution-watch", *change) == EXIT_OK
    doubled = f"{WATCH_UUID}_{WATCH_VOLUME}"
    for reads in (
        _watch_reads(lanes.added),  # the old image
        _watch_reads(lanes.following, schedule="true"),
        _watch_reads(lanes.following, volume=doubled),
        _watch_reads(lanes.following, volume="sds-lanes-execution-watch-state"),
    ):
        capsys.readouterr()
        assert lanes.finish("execution-watch", *change, *reads) == EXIT_CONDITION
        assert "coolify_deploy" in capsys.readouterr().err  # the inverse
    assert _status(lanes, "execution-watch") == ["finished", "unfinished"]
    assert lanes.finish("execution-watch", *change, *_watch_reads(lanes.following)) == EXIT_OK
    assert _status(lanes, "execution-watch") == ["finished", "finished"]


@pytest.mark.parametrize(
    "volume",
    [
        f"{WATCH_UUID}_{WATCH_UUID}_sds-lanes-execution-watch-state",
        f"{WATCH_UUID}_sds-lanes-execution-watch-state_sds-lanes-execution-watch-state",
        f"{WATCH_UUID}_sds-lanes-other-state",
        "sds-lanes-execution-watch-state",
        "otheruuid000000000000000_sds-lanes-execution-watch-state",
    ],
)
def test_an_added_lanes_double_prefixed_volume_refuses(lanes: Lanes, volume: str) -> None:
    lanes.served = lanes.added
    change = ("--add", "--tag", tag(lanes.added))
    assert lanes.add("execution-watch", lanes.added) == EXIT_OK
    assert lanes.recheck("execution-watch", *change) == EXIT_OK
    reads = [*_watch_reads(lanes.added, volume=volume), "--token-present", "true"]
    assert lanes.finish("execution-watch", *change, *reads) == EXIT_CONDITION
    assert _status(lanes, "execution-watch") == ["unfinished"]


def test_a_failed_redeploy_prints_the_inverse_writes(
    lanes: Lanes, capsys: pytest.CaptureFixture[str]
) -> None:
    lanes.added_ledger()
    lanes.served = lanes.later
    change = ("--tag", tag(lanes.later))
    assert lanes.prepare_tag("landing-ledger", lanes.later) == EXIT_OK
    assert lanes.recheck("landing-ledger", *change) == EXIT_OK
    capsys.readouterr()
    assert lanes.finish("landing-ledger", *change, "--redeploy-failed") == EXIT_CONDITION
    err = capsys.readouterr().err
    assert f'key="SDS_LANES_IMAGE_TAG", value="{tag(lanes.added)}"' in err
    assert LEDGER_COMPOSE.strip() in err  # the recorded compose, restored
    assert "lanes-finish --lane landing-ledger --tag" in err and "--restored" in err


# ---- --restored -----------------------------------------------------------------------------


def test_restored_requires_the_replaced_values(lanes: Lanes) -> None:
    lanes.added_watch()
    lanes.served = lanes.following
    change = ("--tag", tag(lanes.following))
    assert lanes.prepare_tag("execution-watch", lanes.following) == EXIT_OK
    assert lanes.recheck("execution-watch", *change) == EXIT_OK
    still_new = _watch_reads(lanes.following)
    assert lanes.finish("execution-watch", *change, "--restored", *still_new) == EXIT_CONDITION
    back = _watch_reads(lanes.added)
    assert lanes.finish("execution-watch", *change, "--restored", *back) == EXIT_OK
    assert _status(lanes, "execution-watch") == ["finished", "restored"]
    # A restored run is never the lane's record: the add is still its last finished run.
    last = deploy.last_finished(lanes.runs("execution-watch"))
    assert last is not None and last.commit == lanes.added


def test_restored_after_an_add_takes_the_two_gone_reads(
    lanes: Lanes, capsys: pytest.CaptureFixture[str]
) -> None:
    lanes.served = lanes.added
    change = ("--add", "--tag", tag(lanes.added))
    assert lanes.add("landing-ledger", lanes.added) == EXIT_OK
    assert lanes.recheck("landing-ledger", *change) == EXIT_OK
    capsys.readouterr()
    assert lanes.finish("landing-ledger", *change, "--redeploy-failed") == EXIT_CONDITION
    err = capsys.readouterr().err
    assert f'coolify_delete_service(uuid="{LEDGER_UUID}", instance="prod")' in err
    assert "D-20" in err and "10 minutes" in err
    gone = ("--restored", "--service-listed")
    assert lanes.finish("landing-ledger", *change, *gone) == EXIT_USAGE
    assert (
        lanes.finish("landing-ledger", *change, *gone, "true", "--container-present", "false")
        == EXIT_CONDITION
    )
    assert (
        lanes.finish("landing-ledger", *change, *gone, "false", "--container-present", "true")
        == EXIT_CONDITION
    )
    assert (
        lanes.finish("landing-ledger", *change, *gone, "false", "--container-present", "false")
        == EXIT_OK
    )
    assert _status(lanes, "landing-ledger") == ["restored"]


# ---- --tag and --schedule -------------------------------------------------------------------


def test_a_tag_below_the_lanes_add_refuses(lanes: Lanes) -> None:
    lanes.added_ledger()
    lanes.served = lanes.old
    assert lanes.prepare_tag("landing-ledger", lanes.old) == EXIT_REFUSED
    assert "not a descendant" in lanes.runs("landing-ledger")[-1].reason


def test_a_tag_needs_a_passed_add_and_the_recorded_running_tag(lanes: Lanes) -> None:
    lanes.served = lanes.later
    assert lanes.prepare_tag("landing-ledger", lanes.later) == EXIT_REFUSED
    lanes.added_ledger()
    lanes.served = lanes.later
    stale = tag(lanes.old)
    assert lanes.prepare_tag("landing-ledger", lanes.later, running=stale) == EXIT_REFUSED
    assert lanes.prepare_tag("landing-ledger", lanes.later) == EXIT_OK


def test_a_tag_prints_the_writes_with_only_the_two_lane_variables(
    lanes: Lanes, capsys: pytest.CaptureFixture[str]
) -> None:
    lanes.added_watch()
    lanes.served = lanes.following
    capsys.readouterr()
    assert lanes.prepare_tag("execution-watch", lanes.following) == EXIT_OK
    err = capsys.readouterr().err
    assert f'coolify_update_service(uuid="{WATCH_UUID}", instance="prod")' in err
    assert (
        f'coolify_update_service_env(uuid="{WATCH_UUID}", instance="prod", '
        f'key="SDS_LANES_IMAGE_TAG", value="{tag(lanes.following)}")'
    ) in err
    assert "+    stop_grace_period: 30s" in err  # the diff names the compose edit
    # The writes and their inverse name one variable's key and value; the compose text names the
    # token variable as `${...}`, never a value.
    assert set(re.findall(r'key="([A-Z_]+)"', err)) == {"SDS_LANES_IMAGE_TAG"}
    assert set(re.findall(r'value="([^"]*)"', err)) == {tag(lanes.following), tag(lanes.added)}


def test_a_schedule_change_keeps_the_running_tag_and_compose(lanes: Lanes) -> None:
    lanes.added_ledger()
    change = ("--schedule", "true")
    prepare = ("lanes-prepare", "--lane", "landing-ledger", *change)
    assert lanes.main(*prepare, "--running-schedule", "true") == EXIT_REFUSED
    assert lanes.main(*prepare, "--running-schedule", "false") == EXIT_OK
    run = lanes.runs("landing-ledger")[-1]
    assert (run.tag, run.lane_digest, run.compose_sent) == (
        tag(lanes.added),
        lane_digest(lanes.added),
        run.compose_replaced,
    )
    assert lanes.recheck("landing-ledger", *change) == EXIT_OK
    reads = ("--observed-digest", lane_digest(lanes.added), "--observed-schedule")
    assert lanes.finish("landing-ledger", *change, *reads, "false") == EXIT_CONDITION
    assert lanes.finish("landing-ledger", *change, *reads, "true") == EXIT_OK


@pytest.mark.parametrize(
    "argv",
    [
        ["lanes-prepare", "--lane", "nope", "--schedule", "true", "--running-schedule", "false"],
        ["lanes-prepare", "--lane", "execution-watch", "--tag", "abc1234", "--running-tag", "x"],
        ["lanes-prepare", "--lane", "execution-watch", "--schedule", "true"],
        ["lanes-recheck", "--lane", "execution-watch"],
        [
            "lanes-recheck",
            "--lane",
            "execution-watch",
            "--tag",
            "sha-" + "a" * 40,
            "--schedule",
            "true",
        ],
        ["lanes-recheck", "--lane", "execution-watch", "--add", "--schedule", "true"],
        ["lanes-prepare", "--lane", "execution-watch", "--add", "--tag", "sha-" + "a" * 40],
        # The retired placeholder lane is no lane.
        ["lanes-prepare", "--lane", "placeholder", "--schedule", "true"],
    ],
)
def test_a_malformed_lane_command_is_usage(lanes: Lanes, argv: list[str]) -> None:
    assert lanes.main(*argv) == EXIT_USAGE
    assert not (lanes.tmp / "deploy-lanes").exists()


# ---- prepare's lag check --------------------------------------------------------------------


def _register_watch(lanes: Lanes) -> None:
    registry = "version = 1\n" + WATCH_TOKEN_ENTRY
    _commit(lanes.work, {".cred-consumers.toml": registry}, "register execution-watch")
    _git(lanes.work, "push", "-q", "origin", "main")


def test_the_lag_check_refuses_a_stale_registered_lane(lanes: Lanes) -> None:
    _register_watch(lanes)
    lanes.added_watch()  # finished at `added`
    with pytest.raises(deploy.Refused, match=r"execution-watch runs sha-.*--tag sha-"):
        deploy.require_lanes_current(lanes.world(), lanes.later, lanes.later)
    deploy.require_lanes_current(lanes.world(), lanes.added, lanes.later)


def test_the_lag_check_refuses_any_unfinished_run(lanes: Lanes) -> None:
    lanes.served = lanes.added
    assert lanes.add("execution-watch", lanes.added) == EXIT_OK
    assert lanes.recheck("execution-watch", "--add", "--tag", tag(lanes.added)) == EXIT_OK
    with pytest.raises(
        deploy.Refused, match="execution-watch .*unfinished|unfinished.*execution-watch"
    ):
        deploy.require_lanes_current(lanes.world(), lanes.added, lanes.later)


def test_the_lag_check_skips_an_unregistered_lane(lanes: Lanes) -> None:
    lanes.added_watch()  # its run files remain, but the registry at main has no token entry
    deploy.require_lanes_current(lanes.world(), lanes.later, lanes.later)


def test_the_lag_check_skips_a_registered_lane_with_no_passed_add(lanes: Lanes) -> None:
    _register_watch(lanes)
    world = lanes.world()
    assert deploy.registered_lanes(world) == {"execution-watch"}
    deploy.require_lanes_current(world, lanes.later, lanes.later)
    lanes.added_watch()
    with pytest.raises(deploy.Refused, match="execution-watch runs"):
        deploy.require_lanes_current(lanes.world(), lanes.later, lanes.later)


def test_the_retired_placeholder_lane_is_neither_registered_nor_named_by_the_lag_check(
    lanes: Lanes,
) -> None:
    """The placeholder lane retired once real lanes ran (ADR-0059): no registry entry, no name.
    Its passed `--add`, left in the operator's lane state, is a run file the lag check skips."""
    assert "placeholder" not in deploy.LANES
    world = lanes.world()
    assert deploy.registered_lanes(world) == set()
    deploy._save_run(
        world,
        deploy.LaneRun(
            lane="placeholder",
            change="add",
            change_key=f"add:{lanes.old}",
            status=deploy.FINISHED,
            created_at="2026-11-06T15:00:00+00:00",
            updated_at="2026-11-06T15:00:00+00:00",
            tag=tag(lanes.old),
            commit=lanes.old,
            run_id="20261106T150000000000Z-add-00000000",
            sequence=1,
        ),
    )
    deploy.require_lanes_current(world, lanes.later, lanes.later)
    assert deploy.lanes_to_follow(world) == []
    _register_watch(lanes)
    assert deploy.registered_lanes(lanes.world()) == {"execution-watch"}


# ---- the lane-token matcher -----------------------------------------------------------------


def test_the_matcher_reads_a_lane_token_entry_and_nothing_else() -> None:
    other = WATCH_TOKEN_ENTRY.replace('resource_type = "service"', 'resource_type = "application"')
    assert deploy.lanes_in_registry("version = 1\n" + WATCH_TOKEN_ENTRY) == {"execution-watch"}
    assert deploy.lanes_in_registry("version = 1\n" + other) == set()
    hashed = WATCH_TOKEN_ENTRY.replace('kind = "coolify-env"', 'kind = "coolify-env-hash"')
    assert deploy.lanes_in_registry("version = 1\n" + hashed) == set()
    plain = WATCH_TOKEN_ENTRY.replace("EXECUTION_WATCH_BWS_ACCESS_TOKEN", "BWS_ACCESS_TOKEN")
    assert deploy.lanes_in_registry("version = 1\n" + plain) == set()


def test_the_matcher_refuses_an_unknown_lane_or_two_entries() -> None:
    unknown = WATCH_TOKEN_ENTRY.replace("EXECUTION_WATCH", "NO_SUCH_LANE")
    with pytest.raises(deploy.Refused, match="unknown lane"):
        deploy.lanes_in_registry("version = 1\n" + unknown)
    with pytest.raises(deploy.Refused, match="more than one"):
        deploy.lanes_in_registry("version = 1\n" + WATCH_TOKEN_ENTRY * 2)


def test_the_repositorys_registry_registers_the_two_moved_lanes() -> None:
    """Increment 2 adds the first lane token entry and increment 3 the landing ledger's (ADR-0059).
    A registered lane holds no release until its `--add` passes."""
    text = (ROOT / ".cred-consumers.toml").read_text()
    assert deploy.lanes_in_registry(text) == {"execution-watch", "landing-ledger"}


# ---- the tables, held to their sources ------------------------------------------------------

# The launchers that stay on the Mac (ADR-0059 decision 4) and so are no lane.
STAYS_ON_THE_MAC = {"activation-sweep", "tool-installer"}
_ARM = re.compile(r"^\s*sds_deadman_arm\s+(sds-[a-z-]+)\b", re.MULTILINE)
_CHECK_NAME = re.compile(r'^\s*CHECK_NAME="(sds-[a-z-]+)"', re.MULTILINE)


def _launcher_check(path: Path) -> str:
    text = path.read_text()
    names = set(_ARM.findall(text)) | set(_CHECK_NAME.findall(text))
    assert len(names) == 1, (path.name, names)
    return names.pop()


def test_the_check_names_are_the_launchers() -> None:
    launchers = {
        path.name.removeprefix("run-").removesuffix(".sh"): _launcher_check(path)
        for path in sorted((ROOT / "scripts").glob("run-*.sh"))
    }
    moving = {lane: name for lane, name in launchers.items() if lane not in STAYS_ON_THE_MAC}
    assert deploy.LANE_CHECKS == moving
    assert STAYS_ON_THE_MAC <= launchers.keys()


def test_the_stateful_lanes_are_the_lane_images_state_directories() -> None:
    dockerfile = (ROOT / "lanes/Dockerfile").read_text()
    named = re.search(r"for lane in (.*?); do", dockerfile, re.DOTALL)
    assert named is not None
    directories = set(named.group(1).replace("\\", " ").split())
    assert deploy.STATEFUL_LANES == directories
    assert deploy.STATEFUL_LANES <= deploy.LANE_CHECKS.keys()


def test_the_landing_window_is_the_estate_landers() -> None:
    from estate_lander import cli as estate

    assert deploy.LANDING_ZONE == estate.LANDING_ZONE
    assert deploy.LANDING_HOURS == estate.LANDING_HOURS


def test_the_healthchecks_key_is_in_the_manifest() -> None:
    manifest = (ROOT / ".bws-secrets.toml").read_text()
    assert f'uuid = "{deploy.HEALTHCHECKS_READ_KEY_UUID}"' in manifest


# ---- --token (ADR-0059 Amendment 2) ---------------------------------------------------------


def test_a_token_run_redeploys_what_the_lane_runs_and_writes_no_variable(
    lanes: Lanes, capsys: pytest.CaptureFixture[str]
) -> None:
    lanes.added_watch()
    added = lanes.runs("execution-watch")[0]
    capsys.readouterr()
    assert lanes.prepare_token() == EXIT_OK
    err = capsys.readouterr().err
    run = lanes.runs("execution-watch")[-1]
    assert (run.change, run.change_key, run.status) == ("token", "token", "waiting")
    assert (run.tag, run.commit, run.lane_digest, run.schedule, run.volume) == (
        added.tag,
        added.commit,
        added.lane_digest,
        added.schedule,
        added.volume,
    )
    assert run.compose_sent == run.compose_replaced == added.compose_sent
    assert run.read_uuids == [OBSERVER, ALERT]
    # The compose and the redeploy, and no variable: the executor wrote the token.
    assert f'coolify_update_service(uuid="{WATCH_UUID}", instance="prod")' in err
    assert f'3. coolify_deploy(uuid="{WATCH_UUID}", instance="prod")' in err
    assert "coolify_update_service_env" not in err and "coolify_create_service_env" not in err
    assert f'docker exec "$C" lane-run --read {OBSERVER} {ALERT}' in err
    assert "lanes-finish --lane execution-watch --token" in err and "--read-result <read>" in err
    assert "has no inverse write" in err

    assert lanes.recheck("execution-watch", "--token") == EXIT_OK
    assert lanes.finish("execution-watch", "--token", *lanes.token_reads()) == EXIT_OK
    assert _status(lanes, "execution-watch") == ["finished", "finished"]
    last = deploy.last_finished(lanes.runs("execution-watch"))
    assert last is not None and (last.change, last.commit) == ("token", lanes.added)


@pytest.mark.parametrize(
    ("reads", "mismatch"),
    [
        ({"present": "false"}, "holds no BWS_ACCESS_TOKEN"),
        ({"result": f"{OBSERVER} ok\n{ALERT} refused\n"}, f"{ALERT} refused"),
        ({"result": f"{OBSERVER} ok\n"}, f"didn't read {ALERT}"),
    ],
)
def test_a_token_mismatch_names_the_restored_close_and_records_why(
    lanes: Lanes, capsys: pytest.CaptureFixture[str], reads: dict[str, str], mismatch: str
) -> None:
    lanes.prepared_token()
    capsys.readouterr()
    assert lanes.finish("execution-watch", "--token", *lanes.token_reads(**reads)) == (
        EXIT_CONDITION
    )
    out = capsys.readouterr()
    reason = json.loads(out.out)["reason"]
    assert "lanes-finish --restored" in reason and "Run the inverse" not in reason
    assert "closed_failed" in reason
    assert "lanes-finish --lane execution-watch --token --restored" in out.err
    run = lanes.runs("execution-watch")[-1]
    assert run.status == "unfinished" and mismatch in run.reason


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--observed-digest", lane_digest("f" * 40)),
        ("--observed-schedule", "true"),
        ("--observed-volume", f"{WATCH_UUID}_{WATCH_VOLUME}"),
    ],
)
def test_a_token_run_fails_when_the_digest_schedule_or_volume_changed(
    lanes: Lanes, flag: str, value: str
) -> None:
    lanes.prepared_token()
    reads = lanes.token_reads()
    reads[reads.index(flag) + 1] = value
    assert lanes.finish("execution-watch", "--token", *reads) == EXIT_CONDITION
    assert _status(lanes, "execution-watch") == ["finished", "unfinished"]


def test_restored_closes_a_failed_token_run_and_it_never_becomes_the_record(
    lanes: Lanes, capsys: pytest.CaptureFixture[str]
) -> None:
    lanes.prepared_token()
    failing = lanes.token_reads(present="false")
    assert lanes.finish("execution-watch", "--token", *failing) == EXIT_CONDITION
    capsys.readouterr()
    assert lanes.finish("execution-watch", "--token", "--restored") == EXIT_OK
    out = capsys.readouterr()
    assert json.loads(out.out)["result"] == "closed_failed"
    assert "closed as failed: it holds no BWS_ACCESS_TOKEN" in out.err
    runs = lanes.runs("execution-watch")
    assert [r.status for r in runs] == ["finished", "closed_failed"]
    assert runs[-1].reason == "it holds no BWS_ACCESS_TOKEN"
    # `last_finished` ignores it: the lane's record is still its --add.
    last = deploy.last_finished(runs)
    assert last is not None and last.change == "add"
    # `require_lanes_current` ignores it: nothing is unfinished, and the lag check reads the add.
    deploy.require_lanes_current(lanes.world(), lanes.added, lanes.later)
    # The lane can take a retry --token.
    assert lanes.prepare_token() == EXIT_OK
    assert _status(lanes, "execution-watch") == ["finished", "closed_failed", "waiting"]


def test_the_lag_check_reads_past_a_closed_failed_token_run(lanes: Lanes) -> None:
    _register_watch(lanes)
    lanes.prepared_token()
    assert lanes.finish("execution-watch", "--token", "--redeploy-failed") == EXIT_CONDITION
    with pytest.raises(deploy.Refused, match="unfinished"):
        deploy.require_lanes_current(lanes.world(), lanes.added, lanes.later)
    assert lanes.finish("execution-watch", "--token", "--restored") == EXIT_OK
    assert lanes.runs("execution-watch")[-1].reason == "the redeploy failed"
    deploy.require_lanes_current(lanes.world(), lanes.added, lanes.later)
    with pytest.raises(deploy.Refused, match=f"execution-watch runs {tag(lanes.added)}"):
        deploy.require_lanes_current(lanes.world(), lanes.later, lanes.later)


def test_last_finished_takes_only_finished_runs() -> None:
    def run(status: str, sequence: int) -> deploy.LaneRun:
        lane, change = "execution-watch", "token"
        return deploy.LaneRun(lane, change, change, status, "", "", sequence=sequence)

    runs = [run("finished", 1), run("closed_failed", 2), run("restored", 3)]
    assert deploy.last_finished(runs) is runs[0]
    assert deploy.last_finished([run("closed_failed", 1)]) is None


def test_restored_refuses_a_token_run_with_no_failed_finish(lanes: Lanes) -> None:
    lanes.prepared_token()
    assert lanes.finish("execution-watch", "--token", "--restored") == EXIT_REFUSED
    assert _status(lanes, "execution-watch") == ["finished", "unfinished"]


def test_token_refuses_a_lane_with_no_passed_add(lanes: Lanes) -> None:
    lanes.served = lanes.added
    assert lanes.prepare_token() == EXIT_REFUSED
    assert "no passed --add" in lanes.runs("execution-watch")[-1].reason


def test_token_refuses_a_container_not_on_the_recorded_tag(lanes: Lanes) -> None:
    lanes.added_watch()
    assert (
        lanes.main(
            "lanes-prepare",
            "--lane",
            "execution-watch",
            "--token",
            "--running-tag",
            tag(lanes.later),
            "--running-volume",
            WATCH_VOLUME,
        )
        == EXIT_REFUSED
    )


@pytest.mark.parametrize(
    ("manifest", "reason"),
    [
        (SECRETS.replace('lanes = ["execution-watch"]', ""), "lists no secret"),
        (SECRETS.replace('lanes = ["execution-watch"]', 'lanes = ["no-such-lane"]'), "unknown"),
        (SECRETS.replace(OBSERVER, "not-a-uuid"), "malformed"),
    ],
)
def test_token_refuses_a_manifest_that_gives_the_read_nothing_sound(
    lanes: Lanes, manifest: str, reason: str
) -> None:
    lanes.added_watch()
    sha = _commit(lanes.work, {".bws-secrets.toml": manifest}, "a bad manifest")
    _git(lanes.work, "push", "-q", "origin", "main")
    # The read list comes from the manifest at the tag the lane runs: move the lane there first.
    lanes.served = lanes.later = sha  # the gh fake serves the four named commits
    assert lanes.prepare_tag("execution-watch", sha) == EXIT_OK
    assert lanes.recheck("execution-watch", "--tag", tag(sha)) == EXIT_OK
    assert lanes.finish("execution-watch", "--tag", tag(sha), *_watch_reads(sha)) == EXIT_OK
    reads = ["--running-tag", tag(sha), "--running-volume", WATCH_VOLUME]
    assert lanes.main("lanes-prepare", "--lane", "execution-watch", "--token", *reads) == (
        EXIT_REFUSED
    )
    assert reason in lanes.runs("execution-watch")[-1].reason


@pytest.mark.parametrize(
    "result",
    [
        f"{OBSERVER} ok\nsecret-value-xyz\n",
        f"{OBSERVER} ok\n{ALERT} maybe\n",
        f"{OBSERVER} ok\n{OBSERVER} ok\n{ALERT} ok\n",
        f"{OBSERVER} ok\n{ALERT} ok\n33333333-3333-4333-8333-333333333333 refused\n",
    ],
)
def test_a_read_result_that_isnt_the_printed_reads_is_usage_and_never_echoed(
    lanes: Lanes, capsys: pytest.CaptureFixture[str], result: str
) -> None:
    lanes.prepared_token()
    capsys.readouterr()
    reads = lanes.token_reads(result=result)
    assert lanes.finish("execution-watch", "--token", *reads) == EXIT_USAGE
    out = capsys.readouterr()
    assert "secret-value-xyz" not in out.out + out.err
    assert _status(lanes, "execution-watch") == ["finished", "unfinished"]
    assert lanes.runs("execution-watch")[-1].reason == ""


def test_a_missing_read_result_file_is_usage(lanes: Lanes) -> None:
    lanes.prepared_token()
    reads = [*lanes.token_reads()[:-1], str(lanes.tmp / "absent.txt")]
    assert lanes.finish("execution-watch", "--token", *reads) == EXIT_USAGE


@pytest.mark.parametrize(
    "argv",
    [
        ["lanes-prepare", "--lane", "execution-watch", "--token", "--tag", "sha-" + "a" * 40],
        ["lanes-prepare", "--lane", "execution-watch", "--token", "--schedule", "true"],
        ["lanes-prepare", "--lane", "execution-watch", "--token", "--add"],
        ["lanes-prepare", "--lane", "execution-watch", "--token", "--running-volume", "v"],
        ["lanes-finish", "--lane", "execution-watch", "--token", *_watch_reads("a" * 40)],
        [
            "lanes-finish",
            "--lane",
            "execution-watch",
            "--tag",
            "sha-" + "a" * 40,
            *_watch_reads("a" * 40),
            "--read-result",
            "x",
        ],
    ],
)
def test_a_malformed_token_command_is_usage(lanes: Lanes, argv: list[str]) -> None:
    assert lanes.main(*argv) == EXIT_USAGE
    assert not (lanes.tmp / "deploy-lanes" / "execution-watch").exists()


# ---- the execution-watch lane's tracked files ----------------------------------------------


def test_the_execution_watch_compose_meets_the_add_refusals() -> None:
    compose = (ROOT / "lanes/execution-watch/compose.yaml").read_text()
    service = deploy.lane_service(compose, "execution-watch")
    assert deploy.add_refusals(service, "execution-watch") == []
    environment = deploy._environment(service)
    assert environment["EXECUTION_WATCH_STATE_DIR"] == "/var/lib/sds/execution-watch"
    assert deploy._state_volume(service, "execution-watch") == "sds-lanes-execution-watch-state"
    assert service["restart"] == "unless-stopped"
    # Every variable is a fixed path, a `${...}` reference, or a BWS secret id, which is no secret
    # (`SDS_DEADMAN_PING_URL_UUID`): the compose holds no value.
    for name, value in environment.items():
        named = value.startswith(("${", "/")) or deploy.BWS_UUID.match(value)
        assert named, (name, value)
    assert "networks" not in service and "ports" not in service and "labels" not in service


def test_the_execution_watch_crontab_runs_the_scheduled_pass_every_fifteen_minutes() -> None:
    lines = [
        line
        for line in (ROOT / "lanes/execution-watch/crontab").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["*/15 * * * * lane-run execution-watch --submit"]
    # The Mac job's cadence, which this replaces.
    plist = (ROOT / "scripts/com.devon.execution-watch.plist").read_text()
    assert re.search(r"<key>StartInterval</key>\s*<integer>900</integer>", plist)


def test_the_repositorys_manifest_lists_execution_watchs_secrets() -> None:
    manifest = (ROOT / ".bws-secrets.toml").read_text()
    ping_url_secret = "97802aac-5a7f-45e8-8185-b4fc00cd318e"
    assert deploy.lane_secrets(manifest, "execution-watch") == [
        "f793576f-e9aa-4f9d-8089-b4a000b9e2d5",
        "b415d03b-f92d-46b1-8a9d-b4f10103df49",
        ping_url_secret,
    ]
    # The launcher names each secret it reads, except the ping-URL secret, whose id reaches it
    # from the compose through `lane-run`.
    launcher = (ROOT / "scripts/run-execution-watch.sh").read_text()
    for uuid in deploy.lane_secrets(manifest, "execution-watch"):
        assert uuid in launcher or uuid == ping_url_secret, uuid


def test_every_lanes_ping_url_secret_is_listed_for_that_lane() -> None:
    """A lane's `--token` proof reads what `.bws-secrets.toml` lists for it, so a ping-URL secret
    its compose names but the manifest doesn't list would go unproved until its first ping. An
    empty value is a placeholder until the lane's ping-URL secret exists, allowed only for a lane
    named in `placeholders`; a later change writes the id and removes the lane from it."""
    manifest = (ROOT / ".bws-secrets.toml").read_text()
    placeholders = {"deploy-watcher"}
    named = {}
    blank = set()
    for path in sorted((ROOT / "lanes").glob("*/compose.yaml")):
        lane = path.parent.name
        environment = deploy._environment(deploy.lane_service(path.read_text(), lane))
        if environment.get("SDS_DEADMAN_PING_URL_UUID") == "":
            blank.add(lane)
        elif "SDS_DEADMAN_PING_URL_UUID" in environment:
            named[lane] = environment["SDS_DEADMAN_PING_URL_UUID"]
    assert blank == placeholders
    assert {"execution-watch", "landing-ledger"} <= set(named), named
    for lane, uuid in named.items():
        assert uuid in deploy.lane_secrets(manifest, lane), lane
