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
PLACEHOLDER_COMPOSE = (ROOT / "lanes/placeholder/compose.yaml").read_text()
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
PLACEHOLDER_UUID = "phuuid00000000000000000a"
WATCH_UUID = "ewuuid00000000000000000a"
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

    def add(self, lane: str, sha: str, *extra: str, uuid: str | None = None) -> int:
        uuid = uuid or (PLACEHOLDER_UUID if lane == "placeholder" else WATCH_UUID)
        return self.main(
            "lanes-prepare",
            "--lane",
            lane,
            "--add",
            "--tag",
            tag(sha),
            "--resource-uuid",
            uuid,
            *(extra or ("--schedule-written", "false")),
        )

    def recheck(self, lane: str, *change: str) -> int:
        return self.main("lanes-recheck", "--lane", lane, *change)

    def finish(self, lane: str, *change_and_reads: str) -> int:
        return self.main("lanes-finish", "--lane", lane, *change_and_reads)

    def added_placeholder(self) -> None:
        self.served = self.added
        change = ("--add", "--tag", tag(self.added))
        assert self.add("placeholder", self.added) == EXIT_OK
        assert self.recheck("placeholder", *change) == EXIT_OK
        reads = ("--observed-digest", lane_digest(self.added), "--observed-schedule", "false")
        assert self.finish("placeholder", *change, *reads) == EXIT_OK

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

    def prepare_tag(self, lane: str, sha: str, *extra: str, running: str | None = None) -> int:
        reads = ["--running-tag", running or tag(self.added)]
        if lane == "execution-watch":
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
            "lanes/placeholder/compose.yaml": PLACEHOLDER_COMPOSE,
            "lanes/execution-watch/compose.yaml": WATCH_COMPOSE,
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


def test_the_placeholder_reads_no_listing_and_its_slot_is_the_window_alone(lanes: Lanes) -> None:
    lanes.served = lanes.added
    lanes.now = LANDING
    assert lanes.add("placeholder", lanes.added) == EXIT_CONDITION
    lanes.now = LANDING + timedelta(hours=4)  # 07:00 in New York, within the 6-hour wait
    assert lanes.add("placeholder", lanes.added) == EXIT_OK
    assert lanes.recheck("placeholder", "--add", "--tag", tag(lanes.added)) == EXIT_OK
    assert lanes.listing_reads == 0


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
    lanes.added_placeholder()
    lanes.served, lanes.now = lanes.later, LANDING + timedelta(days=1)
    assert lanes.prepare_tag("placeholder", lanes.later) == EXIT_CONDITION
    waiting = lanes.runs("placeholder")[-1]
    assert waiting.status == "waiting"
    assert (
        lanes.main(
            "lanes-prepare",
            "--lane",
            "placeholder",
            "--schedule",
            "true",
            "--running-schedule",
            "false",
        )
        == EXIT_CONDITION
    )
    by_id = {r.run_id: r for r in lanes.runs("placeholder")}
    assert by_id[waiting.run_id].status == "superseded"
    assert _status(lanes, "placeholder")[-1] == "waiting"


def test_a_waiting_run_doesnt_block_a_re_run_and_an_unfinished_one_does(lanes: Lanes) -> None:
    lanes.served = lanes.added
    change = ("--add", "--tag", tag(lanes.added))
    assert lanes.add("placeholder", lanes.added) == EXIT_OK
    assert lanes.add("placeholder", lanes.added) == EXIT_OK
    assert lanes.recheck("placeholder", *change) == EXIT_OK
    assert lanes.add("placeholder", lanes.added) == EXIT_REFUSED
    assert _status(lanes, "placeholder") == ["unfinished"]


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
    assert lanes.add("placeholder", lanes.added, "--schedule-written", "true") == EXIT_REFUSED
    assert _status(lanes, "placeholder") == ["refused"]
    assert "--schedule-written must be false" in lanes.runs("placeholder")[0].reason


@pytest.mark.parametrize(
    "mapping", ["${LANE_SCHEDULE_ENABLED:-true}", "${LANE_SCHEDULE_ENABLED}", "true", "false"]
)
def test_add_refuses_a_compose_whose_schedule_doesnt_default_to_false(
    lanes: Lanes, mapping: str
) -> None:
    edited = PLACEHOLDER_COMPOSE.replace("${LANE_SCHEDULE_ENABLED:-false}", mapping)
    sha = _commit(lanes.work, {"lanes/placeholder/compose.yaml": edited}, "a bad default")
    _git(lanes.work, "push", "-q", "origin", "main")
    lanes.served = sha
    lanes.successes[sha] = 1
    lanes.later = sha  # the gh fake serves the four named commits
    assert lanes.add("placeholder", sha) == EXIT_REFUSED
    assert "LANE_SCHEDULE_ENABLED" in lanes.runs("placeholder")[-1].reason


def test_add_skips_the_token_check_only_for_the_placeholder(lanes: Lanes) -> None:
    lanes.added_placeholder()
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
    assert lanes.add("placeholder", lanes.added) == EXIT_REFUSED
    assert "not the served orchestrator tag" in lanes.runs("placeholder")[0].reason


def test_a_tag_with_two_successful_releases_refuses(lanes: Lanes) -> None:
    lanes.served = lanes.added
    lanes.successes[lanes.added] = 2
    assert lanes.add("placeholder", lanes.added) == EXIT_REFUSED


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
    lanes.added_placeholder()
    lanes.served = lanes.later
    change = ("--tag", tag(lanes.later))
    assert lanes.prepare_tag("placeholder", lanes.later) == EXIT_OK
    assert lanes.recheck("placeholder", *change) == EXIT_OK
    capsys.readouterr()
    assert lanes.finish("placeholder", *change, "--redeploy-failed") == EXIT_CONDITION
    err = capsys.readouterr().err
    assert f'key="SDS_LANES_IMAGE_TAG", value="{tag(lanes.added)}"' in err
    assert PLACEHOLDER_COMPOSE.strip() in err  # the recorded compose, restored
    assert "lanes-finish --lane placeholder --tag" in err and "--restored" in err


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
    assert lanes.add("placeholder", lanes.added) == EXIT_OK
    assert lanes.recheck("placeholder", *change) == EXIT_OK
    capsys.readouterr()
    assert lanes.finish("placeholder", *change, "--redeploy-failed") == EXIT_CONDITION
    err = capsys.readouterr().err
    assert f'coolify_delete_service(uuid="{PLACEHOLDER_UUID}", instance="prod")' in err
    assert "D-20" in err and "10 minutes" in err
    gone = ("--restored", "--service-listed")
    assert lanes.finish("placeholder", *change, *gone) == EXIT_USAGE
    assert (
        lanes.finish("placeholder", *change, *gone, "true", "--container-present", "false")
        == EXIT_CONDITION
    )
    assert (
        lanes.finish("placeholder", *change, *gone, "false", "--container-present", "true")
        == EXIT_CONDITION
    )
    assert (
        lanes.finish("placeholder", *change, *gone, "false", "--container-present", "false")
        == EXIT_OK
    )
    assert _status(lanes, "placeholder") == ["restored"]


# ---- --tag and --schedule -------------------------------------------------------------------


def test_a_tag_below_the_lanes_add_refuses(lanes: Lanes) -> None:
    lanes.added_placeholder()
    lanes.served = lanes.old
    assert lanes.prepare_tag("placeholder", lanes.old) == EXIT_REFUSED
    assert "not a descendant" in lanes.runs("placeholder")[-1].reason


def test_a_tag_needs_a_passed_add_and_the_recorded_running_tag(lanes: Lanes) -> None:
    lanes.served = lanes.later
    assert lanes.prepare_tag("placeholder", lanes.later) == EXIT_REFUSED
    lanes.added_placeholder()
    lanes.served = lanes.later
    assert lanes.prepare_tag("placeholder", lanes.later, running=tag(lanes.old)) == EXIT_REFUSED
    assert lanes.prepare_tag("placeholder", lanes.later) == EXIT_OK


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
    lanes.added_placeholder()
    change = ("--schedule", "true")
    prepare = ("lanes-prepare", "--lane", "placeholder", *change)
    assert lanes.main(*prepare, "--running-schedule", "true") == EXIT_REFUSED
    assert lanes.main(*prepare, "--running-schedule", "false") == EXIT_OK
    run = lanes.runs("placeholder")[-1]
    assert (run.tag, run.lane_digest, run.compose_sent) == (
        tag(lanes.added),
        lane_digest(lanes.added),
        run.compose_replaced,
    )
    assert lanes.recheck("placeholder", *change) == EXIT_OK
    reads = ("--observed-digest", lane_digest(lanes.added), "--observed-schedule")
    assert lanes.finish("placeholder", *change, *reads, "false") == EXIT_CONDITION
    assert lanes.finish("placeholder", *change, *reads, "true") == EXIT_OK


@pytest.mark.parametrize(
    "argv",
    [
        ["lanes-prepare", "--lane", "nope", "--schedule", "true", "--running-schedule", "false"],
        ["lanes-prepare", "--lane", "placeholder", "--tag", "abc1234", "--running-tag", "x"],
        ["lanes-prepare", "--lane", "placeholder", "--schedule", "true"],
        ["lanes-recheck", "--lane", "placeholder"],
        [
            "lanes-recheck",
            "--lane",
            "placeholder",
            "--tag",
            "sha-" + "a" * 40,
            "--schedule",
            "true",
        ],
        ["lanes-recheck", "--lane", "placeholder", "--add", "--schedule", "true"],
        ["lanes-prepare", "--lane", "placeholder", "--add", "--tag", "sha-" + "a" * 40],
    ],
)
def test_a_malformed_lane_command_is_usage(lanes: Lanes, argv: list[str]) -> None:
    assert lanes.main(*argv) == EXIT_USAGE
    assert not (lanes.tmp / "deploy-lanes" / "placeholder").exists()


# ---- prepare's lag check --------------------------------------------------------------------


def test_the_lag_check_refuses_a_stale_registered_lane(lanes: Lanes) -> None:
    lanes.added_placeholder()  # registered by name, finished at `added`
    with pytest.raises(deploy.Refused, match=r"placeholder runs sha-.*--tag sha-"):
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
    registry = "version = 1\n" + WATCH_TOKEN_ENTRY
    _commit(lanes.work, {".cred-consumers.toml": registry}, "register execution-watch")
    _git(lanes.work, "push", "-q", "origin", "main")
    world = lanes.world()
    assert deploy.registered_lanes(world) == {"placeholder", "execution-watch"}
    deploy.require_lanes_current(world, lanes.later, lanes.later)
    lanes.added_watch()
    with pytest.raises(deploy.Refused, match="execution-watch runs"):
        deploy.require_lanes_current(lanes.world(), lanes.later, lanes.later)


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


def test_the_repositorys_registry_registers_no_lane_yet() -> None:
    """Increment 2 adds the first lane token entry; this increment adds none."""
    assert deploy.lanes_in_registry((ROOT / ".cred-consumers.toml").read_text()) == set()


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
    assert "placeholder" not in deploy.LANE_CHECKS


def test_the_stateful_lanes_are_the_lane_images_state_directories() -> None:
    dockerfile = (ROOT / "lanes/Dockerfile").read_text()
    named = re.search(r"for lane in (.*?); do", dockerfile, re.DOTALL)
    assert named is not None
    directories = set(named.group(1).replace("\\", " ").split())
    assert deploy.STATEFUL_LANES == directories - {"placeholder"}
    assert deploy.STATEFUL_LANES <= deploy.LANE_CHECKS.keys()


def test_the_landing_window_is_the_estate_landers() -> None:
    from estate_lander import cli as estate

    assert deploy.LANDING_ZONE == estate.LANDING_ZONE
    assert deploy.LANDING_HOURS == estate.LANDING_HOURS


def test_the_healthchecks_key_is_in_the_manifest() -> None:
    manifest = (ROOT / ".bws-secrets.toml").read_text()
    assert f'uuid = "{deploy.HEALTHCHECKS_READ_KEY_UUID}"' in manifest
