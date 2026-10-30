"""The estate lane's watch pass: point back, revert, restore (Landing Part 3b, Task 4).

Each step is exercised against recording doubles of the confined hosting-platform client, the
registry, what production serves and the orchestrator's acts, so a test can say what was NOT done
as well as what was. Review focus 3: every exit before `restored` either leaves production as it
was with `landing-not-pointed-back` raised, or leaves it pinned with a record whose pinned alert is
open (`tests/lander/test_held_alerts.py` pins the alert per status).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest

from estate_lander.image_repoint import (
    Application,
    AppSpec,
    RegistryUnreadable,
    RepointRefused,
)
from estate_lander.revert_pass import Production, RevertPass, lane_rows, revert_pass
from estate_lander.served import ServedUnreadable
from lander.errors import LandingRefused, OrchestratorError
from lander.held_alerts import pinned_key, revert_alerts

BRAIN = "alobarquest/brain"
PR = 79
PARENT = "80b620ce1799c1eeeaac83f494e6cd9a553d5503"
MERGE = "271a2ac8de79a1dbc09bc5c05d4be73d08472449"
REVERT_MERGE = "f" * 40
LATER = "e" * 40
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/alobarquest/brain"
APPS = (
    AppSpec("app-brain", "https://app-brain.devonwatkins.com/api/health"),
    AppSpec("code-brain", "https://code-brain.devonwatkins.com/api/health"),
)


def _apps(tag: str = "latest", **tags: str) -> list[Application]:
    return [
        Application(
            uuid=f"u{i}",
            name=spec.name,
            health_url=spec.health_url,
            image=IMAGE,
            tag=tags.get(spec.name.replace("-", "_"), tag),
        )
        for i, spec in enumerate(APPS, start=1)
    ]


def _pins(restore: str = "latest", *, confirmed: bool = True) -> dict[str, dict[str, str]]:
    """The images a re-point recorded; `confirmed` once the lander has set every tag (the
    orchestrator marks each entry `pinned`)."""
    flag = {"pinned": "yes"} if confirmed else {}
    return {
        uuid: {"tag": PARENT, "digest": DIGEST, "restore_tag": restore, **flag}
        for uuid in ("u1", "u2")
    }


class Log(list[str]):
    """One ordered record of every act across the doubles, so order is assertable."""


class FakeImages:
    def __init__(
        self,
        log: Log,
        applications: list[Application],
        *,
        confine_error: Exception | None = None,
        fail_on: str | None = None,
    ) -> None:
        self.log, self.applications = log, applications
        self.confine_error, self.fail_on = confine_error, fail_on
        self.closed = False

    def confine(self, apps: Sequence[AppSpec]) -> tuple[Application, ...]:
        assert tuple(apps) == APPS
        self.log.append("confine")
        if self.confine_error is not None:
            raise self.confine_error
        return tuple(self.applications)

    def set_tag(self, uuid: str, tag: str) -> None:
        self.log.append(f"tag {uuid} {tag[:12]}")
        if self.fail_on == uuid:
            raise RepointRefused("Coolify PATCH answered HTTP 500")

    def start(self, uuid: str) -> None:
        self.log.append(f"start {uuid}")

    def close(self) -> None:
        self.closed = True


class FakeRegistry:
    def __init__(self, log: Log, held: dict[str, str] | None = None, error: bool = False) -> None:
        self.log, self.held, self.error = log, {PARENT: DIGEST} if held is None else held, error

    def digest(self, image: str, tag: str) -> str | None:
        self.log.append(f"digest {image} {tag[:12]}")
        if self.error:
            raise RegistryUnreadable("GHCR got no answer: ConnectError")
        return self.held.get(tag)


class FakeActs:
    def __init__(self, log: Log, *, error: Exception | None = None) -> None:
        self.log, self.error = log, error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _call(self, act: str, answer: dict[str, Any], **body: Any) -> dict[str, Any]:
        self.log.append(act)
        self.calls.append((act, body))
        if self.error is not None:
            raise self.error
        return answer

    def revert_landing(self, repository: str, pr_number: int, **body: Any) -> dict[str, Any]:
        answer = {"acted": True, "status": "revert_opened", "detail": ""}
        if body.get("expected_status") != "repointed":
            answer["status"] = "rerun_requested"
        return self._call("revert", answer, repository=repository, pr_number=pr_number, **body)

    def repoint(self, repository: str, pr_number: int, **body: Any) -> dict[str, Any]:
        answer = {"acted": True, "status": "repointed", "detail": ""}
        return self._call("repoint", answer, repository=repository, pr_number=pr_number, **body)

    def restored(self, repository: str, pr_number: int, **body: Any) -> dict[str, Any]:
        answer = {"acted": True, "status": "restored", "detail": ""}
        return self._call("restored", answer, repository=repository, pr_number=pr_number, **body)

    def pinned(self, repository: str, pr_number: int, **body: Any) -> dict[str, Any]:
        answer = {"acted": True, "status": "repointed", "detail": ""}
        return self._call("pinned", answer, repository=repository, pr_number=pr_number, **body)


class World:
    """The doubles a pass is run against, with production serving `serving` per application."""

    def __init__(
        self,
        applications: list[Application] | None = None,
        *,
        serving: dict[str, str | None] | None = None,
        token: bool = True,
        acts_error: Exception | None = None,
        registry: dict[str, str] | None = None,
        registry_error: bool = False,
        confine_error: Exception | None = None,
        fail_on: str | None = None,
        later: bool = False,
    ) -> None:
        self.log = Log()
        self.images = FakeImages(
            self.log,
            _apps() if applications is None else applications,
            confine_error=confine_error,
            fail_on=fail_on,
        )
        self.registry = FakeRegistry(self.log, registry, registry_error)
        self.acts = FakeActs(self.log, error=acts_error)
        self.serving = serving or {}
        self.token = token
        self.later = later

    def served(self, url: str) -> str | None:
        name = url.split("//")[1].split(".")[0]
        self.log.append(f"served {name}")
        value = self.serving.get(name)
        if value == "unreadable":
            raise ServedUnreadable("ConnectError")
        return value

    def production(self) -> Production:
        return Production(
            images=(lambda: self.images) if self.token else None,
            registry=self.registry,
            apps=lambda repository: APPS if repository == BRAIN else (),
            served=self.served,
            at_or_after=lambda repository, commit, served: self.later,
        )

    def run(self, *rows: dict[str, Any], submit: bool = True) -> RevertPass:
        return revert_pass(list(rows), self.acts, self.production(), submit)


def _row(verdict: str | None, status: str | None = None, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "repository": BRAIN,
        "pr_number": PR,
        "merge_commit_sha": MERGE,
        "verdict": verdict,
        "detail": f"{verdict or status}: test",
        "failed_job_names": [],
        "failed_runs": [],
        "status": status,
        "revert_pr_number": None,
        "hold_outcome": None,
        "reason": "served" if verdict == "bad" else None,
        "lane": "estate",
        "parent_sha": PARENT,
        "pinned_images": None,
        "revert_merge_sha": None,
    }
    row.update(extra)
    return row


def _only(result: RevertPass) -> tuple[str, str]:
    [outcome] = result.outcomes
    return outcome.status, outcome.detail


# --------------------------------------------------------------------------------------------
# Step 1: a bad landing is pointed back.
# --------------------------------------------------------------------------------------------


def test_a_bad_landing_is_recorded_repointed_before_production_is_touched() -> None:
    world = World(_apps(code_brain="main"))

    result = world.run(_row("bad"))

    assert _only(result)[0] == "repointed"
    assert world.log == [
        "confine",
        f"digest {IMAGE} {PARENT[:12]}",
        "repoint",
        # What each application serves now: one already on the parent needs no deploy.
        "served app-brain",
        "served code-brain",
        f"tag u1 {PARENT[:12]}",
        "start u1",
        f"tag u2 {PARENT[:12]}",
        "start u2",
        # Every tag is set: the pin is confirmed.
        "pinned",
    ]
    act, body = world.acts.calls[0]
    assert act == "repoint"
    assert body["expected_status"] is None
    assert body["pinned_images"] == {
        "u1": {"tag": PARENT, "digest": DIGEST, "restore_tag": "latest"},
        "u2": {"tag": PARENT, "digest": DIGEST, "restore_tag": "main"},
    }
    assert body["idempotency_key"] == f"estate-landing-repoint:{BRAIN}:{PR}:{PARENT}:{DIGEST}"
    assert result.unpinned == {} and world.images.closed


def test_a_checks_bad_names_the_rerun_record_it_read() -> None:
    world = World()

    world.run(_row("bad", "rerun_requested", reason="checks"))

    assert world.acts.calls[0][1]["expected_status"] == "rerun_requested"


@pytest.mark.parametrize(
    ("world", "row", "reason"),
    [
        (World(token=False), _row("bad"), "no Coolify token"),
        (World(), _row("bad", parent_sha=None), "no commit before the bump"),
        (
            World(confine_error=RepointRefused("app-brain: no application on the platform")),
            _row("bad"),
            "no application",
        ),
        (World(registry={}), _row("bad"), "registry holds no"),
        (World(registry_error=True), _row("bad"), "GHCR got no answer"),
        (
            World(
                [*_apps()[:1], *[a.__class__(**{**a.__dict__, "image": "x"}) for a in _apps()[1:]]]
            ),
            _row("bad"),
            "2 different images",
        ),
        (World(_apps(app_brain=MERGE)), _row("bad"), "already pinned to a commit: app-brain"),
        (World(acts_error=OrchestratorError("unreachable")), _row("bad"), "unreachable"),
    ],
)
def test_a_repoint_refused_before_anything_changed_leaves_production_and_raises_its_alert(
    world: World, row: dict[str, Any], reason: str
) -> None:
    """Review focus 3, the first exit: nothing pinned, the bad build still serves, and
    `landing-not-pointed-back` names why."""
    result = world.run(row)

    status, detail = _only(result)
    assert status == "repoint-refused" and reason in detail
    assert result.unpinned == {(BRAIN, PR): detail}
    assert not any(entry.startswith(("tag ", "start ")) for entry in world.log)


def test_a_landing_the_orchestrator_no_longer_judges_bad_is_left_alone_unalerted() -> None:
    world = World(acts_error=LandingRefused("judged pending now", "landing_repoint_not_bad"))

    result = world.run(_row("bad"))

    assert _only(result)[0] == "waiting"
    assert result.unpinned == {}
    assert not any(entry.startswith(("tag ", "start ")) for entry in world.log)


def test_a_pin_that_fails_part_way_is_an_error_and_the_record_already_says_pinned() -> None:
    """The record was written first, so its pinned alert is open; the next pass repairs."""
    world = World(fail_on="u2")

    result = world.run(_row("bad"))

    status, detail = _only(result)
    assert status == "error" and "1 of 2" in detail
    assert world.log.index("repoint") < world.log.index(f"tag u1 {PARENT[:12]}")
    assert result.unpinned == {}


# --------------------------------------------------------------------------------------------
# Step 2: once production serves the parent, the revert opens.
# --------------------------------------------------------------------------------------------


def _repointed(**extra: Any) -> dict[str, Any]:
    return _row(None, "repointed", pinned_images=_pins(), **extra)


def test_the_revert_is_asked_for_once_every_application_serves_the_parent() -> None:
    world = World(_apps(tag=PARENT), serving={"app-brain": PARENT, "code-brain": PARENT.upper()})

    result = world.run(_repointed())

    assert _only(result)[0] == "revert-opened"
    [(act, body)] = world.acts.calls
    assert (act, body["expected_status"]) == ("revert", "repointed")
    assert body["idempotency_key"] == f"estate-landing-revert:{BRAIN}:{PR}:repointed"
    assert not any(entry.startswith("tag ") for entry in world.log)


def test_an_application_not_on_the_parent_yet_holds_the_revert_and_is_pinned_again() -> None:
    """Its tag is still the parent's (a pin that failed part-way, or a deploy that never started):
    it is pointed and deployed again; the application already serving is left alone."""
    world = World(_apps(tag=PARENT), serving={"app-brain": PARENT, "code-brain": MERGE})

    result = world.run(_repointed())

    status, detail = _only(result)
    assert status == "pinned" and "code-brain" in detail
    assert world.acts.calls == []
    assert f"tag u2 {PARENT[:12]}" in world.log and f"tag u1 {PARENT[:12]}" not in world.log


@pytest.mark.parametrize(
    "world",
    [
        World(_apps(tag=PARENT), serving={"app-brain": PARENT, "code-brain": "unreadable"}),
        World(token=False),
        World(_apps(tag=PARENT)[:1], serving={"app-brain": PARENT}),
        World(confine_error=RepointRefused("app-brain: 2 applications on the platform")),
    ],
)
def test_production_stays_pinned_and_no_revert_is_asked_when_it_cannot_be_confirmed(
    world: World,
) -> None:
    result = world.run(_repointed())

    status, detail = _only(result)
    assert status == "error" and "production stays pinned" in detail
    assert world.acts.calls == []


# --------------------------------------------------------------------------------------------
# Step 4: after the revert landed, the moving tag comes back.
# --------------------------------------------------------------------------------------------


def _landed(**extra: Any) -> dict[str, Any]:
    return _row(
        None,
        "revert_landed",
        pinned_images={
            "u1": {"tag": PARENT, "digest": DIGEST, "restore_tag": "latest", "pinned": "yes"},
            "u2": {"tag": PARENT, "digest": DIGEST, "restore_tag": "main", "pinned": "yes"},
        },
        revert_merge_sha=REVERT_MERGE,
        **extra,
    )


def test_nothing_moves_until_the_reverts_own_image_is_built() -> None:
    world = World(_apps(tag=PARENT))

    result = world.run(_landed())

    status, detail = _only(result)
    assert status == "pinned" and "not built yet" in detail
    assert not any(entry.startswith("tag ") for entry in world.log)
    assert world.acts.calls == []


def test_each_application_gets_back_the_moving_tag_it_had() -> None:
    world = World(_apps(tag=PARENT), registry={PARENT: DIGEST, REVERT_MERGE: DIGEST})

    result = world.run(_landed())

    assert _only(result)[0] == "restoring"
    assert [e for e in world.log if e.startswith(("tag ", "start "))] == [
        "tag u1 latest",
        "start u1",
        "tag u2 main",
        "start u2",
    ]
    assert world.acts.calls == []


def test_restored_is_recorded_once_every_application_serves_the_revert() -> None:
    world = World(
        _apps(code_brain="main"),
        registry={REVERT_MERGE: DIGEST},
        serving={"app-brain": REVERT_MERGE, "code-brain": REVERT_MERGE},
    )

    result = world.run(_landed())

    assert _only(result)[0] == "restored"
    [(act, body)] = world.acts.calls
    assert (act, body["expected_status"]) == ("restored", "revert_landed")
    assert body["idempotency_key"] == f"estate-landing-restored:{BRAIN}:{PR}:{REVERT_MERGE}"


def test_a_later_build_than_the_revert_counts_as_serving_it() -> None:
    world = World(
        _apps(code_brain="main"),
        registry={REVERT_MERGE: DIGEST},
        serving={"app-brain": LATER, "code-brain": REVERT_MERGE},
        later=True,
    )

    assert _only(world.run(_landed()))[0] == "restored"


def test_an_application_still_on_the_parent_holds_the_restore() -> None:
    world = World(
        _apps(code_brain="main"),
        registry={REVERT_MERGE: DIGEST},
        serving={"app-brain": REVERT_MERGE, "code-brain": PARENT},
    )

    status, detail = _only(world.run(_landed()))

    assert status == "restoring" and "code-brain" in detail
    assert world.acts.calls == []


# --------------------------------------------------------------------------------------------
# The rest of the watch.
# --------------------------------------------------------------------------------------------


def test_a_flaky_landing_is_rerun_by_its_failing_runs() -> None:
    world = World()

    result = world.run(_row("flaky_retry", failed_runs=[[18_000_000_001, 1]]))

    assert _only(result)[0] == "reran"
    [(act, body)] = world.acts.calls
    assert body["idempotency_key"] == (
        f"estate-landing-revert:{BRAIN}:{PR}:flaky_retry:18000000001.1"
    )


def test_a_pass_that_does_not_submit_reads_and_writes_nothing() -> None:
    world = World()
    rows = [
        _row("bad"),
        _row("flaky_retry", pr_number=80),
        _repointed(pr_number=81),
        _landed(pr_number=82),
    ]

    result = world.run(*rows, submit=False)

    assert [o.status for o in result.outcomes] == [
        "would-repoint",
        "would-rerun",
        "pinned",
        "would-restore",
    ]
    assert world.log == []


@pytest.mark.parametrize(
    ("row", "status"),
    [
        (_row(None, "revert_landed"), "reverted"),
        (_row(None, "refused_overlap"), "revert-refused"),
        (_row(None, "revert_failed"), "revert-failed"),
        (_row("unreadable"), "unreadable"),
    ],
)
def test_a_record_past_its_acts_is_reported_by_its_status(row: dict[str, Any], status: str) -> None:
    world = World()

    assert _only(world.run(row))[0] == status
    assert world.log == []


def test_the_restored_and_clean_rows_say_nothing() -> None:
    world = World()

    result = world.run(_row(None, "restored", pinned_images=_pins()), _row("clean"))

    assert result.outcomes == []


def test_only_this_lanes_rows_are_its_own() -> None:
    rows = [_row("bad"), _row("bad", lane="inert"), _row("bad", lane=None)]

    assert lane_rows(rows) == [rows[0]]


def test_a_platform_client_that_cannot_be_built_refuses_the_repoint_by_name() -> None:
    world = World()

    def unbuildable() -> Any:
        raise RepointRefused("the Coolify URL must be https, or http to a tailnet address")

    production = replace(world.production(), images=unbuildable)
    result = revert_pass([_row("bad")], world.acts, production, True)

    status, detail = _only(result)
    assert status == "repoint-refused" and "tailnet" in detail
    assert result.unpinned == {(BRAIN, PR): detail}


def test_an_application_on_the_parents_tag_but_not_serving_it_is_deployed_again() -> None:
    """Its tag is right but its deploy never started (or failed): the next pass starts one."""
    world = World(_apps(tag=PARENT), serving={"app-brain": PARENT, "code-brain": MERGE})

    status, detail = _only(world.run(_repointed()))

    assert status == "pinned" and "code-brain" in detail
    assert "start u2" in world.log and "start u1" not in world.log
    assert world.acts.calls == []


def test_a_restored_tag_not_yet_serving_the_revert_is_deployed_again() -> None:
    world = World(
        _apps(code_brain="main"),
        registry={REVERT_MERGE: DIGEST},
        serving={"app-brain": REVERT_MERGE, "code-brain": PARENT},
    )

    world.run(_landed())

    assert [e for e in world.log if e.startswith(("tag ", "start "))] == ["start u2"]


# --------------------------------------------------------------------------------------------
# Fix round 1.
# --------------------------------------------------------------------------------------------


def _stuck(status: str) -> dict[str, Any]:
    """A pinned record whose revert could not be opened or landed."""
    return _row(
        None,
        status,
        pinned_images={
            "u1": {"tag": PARENT, "digest": DIGEST, "restore_tag": "latest", "pinned": "yes"},
            "u2": {"tag": PARENT, "digest": DIGEST, "restore_tag": "main", "pinned": "yes"},
        },
    )


@pytest.mark.parametrize("status", ["revert_failed", "refused_overlap"])
def test_a_hand_restore_of_a_record_with_no_revert_is_recorded_restored(status: str) -> None:
    """A person put every moving tag back: the pinned alert has no other way to resolve."""
    world = World(_apps(code_brain="main"))

    result = world.run(_stuck(status))

    assert _only(result)[0] == "restored"
    [(act, body)] = world.acts.calls
    assert (act, body["expected_status"]) == ("restored", status)
    assert not any(entry.startswith(("tag ", "start ")) for entry in world.log)


@pytest.mark.parametrize(
    ("status", "reported"),
    [("revert_failed", "revert-failed"), ("refused_overlap", "revert-refused")],
)
def test_a_partial_hand_restore_leaves_the_record_and_its_alert(status: str, reported: str) -> None:
    world = World(_apps(code_brain=PARENT))

    result = world.run(_stuck(status))

    assert _only(result)[0] == reported
    assert world.acts.calls == []
    assert not any(entry.startswith(("tag ", "start ")) for entry in world.log)


def test_a_record_with_no_revert_reports_itself_on_a_pass_that_does_not_submit() -> None:
    world = World(_apps(code_brain="main"))

    assert _only(world.run(_stuck("revert_failed"), submit=False))[0] == "revert-failed"
    assert world.log == []


def test_a_hand_restored_tag_while_pinned_is_left_alone() -> None:
    """A person put code-brain back while the revert waits: the pass does not undo it."""
    world = World(
        _apps(tag=PARENT, code_brain="latest"),
        serving={"app-brain": PARENT, "code-brain": MERGE},
    )

    status, detail = _only(world.run(_repointed()))

    assert status == "pinned" and "code-brain" in detail and "by hand" in detail
    assert not any(entry.startswith(("tag u2", "start u2")) for entry in world.log)
    assert world.acts.calls == []


@pytest.mark.parametrize(
    ("code", "status", "alerted"),
    [
        ("landing_repoint_not_bad", "waiting", False),
        ("landing_revert_record_moved", "waiting", False),
        # The lane's off switch is deliberate, not a finding.
        ("landing_revert_lane_off", "waiting", False),
        # A bad build is served and nothing will act on it.
        ("landing_repository_not_granted", "repoint-refused", True),
    ],
)
def test_a_refused_repoint_is_waiting_only_when_the_answer_moved(
    code: str, status: str, alerted: bool
) -> None:
    world = World(acts_error=LandingRefused("refused", code))

    result = world.run(_row("bad"))

    assert _only(result)[0] == status
    assert ((BRAIN, PR) in result.unpinned) is alerted


def test_an_application_already_serving_the_parent_is_pinned_without_a_deploy() -> None:
    """Brain #79's shape: the bump's image never came up, so production already runs the parent.
    The tag is pinned so a later deploy keeps it; no deploy is started for it."""
    world = World(serving={"app-brain": PARENT})

    world.run(_row("bad"))

    pins = [e for e in world.log if e.startswith(("tag ", "start "))]
    assert pins == [f"tag u1 {PARENT[:12]}", f"tag u2 {PARENT[:12]}", "start u2"]


def test_an_image_that_is_not_the_repositorys_refuses_the_repoint() -> None:
    other = [replace(app, image="ghcr.io/alobarquest/change-manager") for app in _apps()]
    world = World(other)

    status, detail = _only(world.run(_row("bad")))

    assert status == "repoint-refused" and "change-manager" in detail
    assert "repoint" not in world.log


def test_a_hand_restore_while_repointed_is_recorded_restored_and_opens_no_revert() -> None:
    world = World(_apps(code_brain="main"), serving={"app-brain": MERGE, "code-brain": MERGE})
    row = _row(
        None,
        "repointed",
        pinned_images={
            "u1": {"tag": PARENT, "digest": DIGEST, "restore_tag": "latest", "pinned": "yes"},
            "u2": {"tag": PARENT, "digest": DIGEST, "restore_tag": "main", "pinned": "yes"},
        },
    )

    assert _only(world.run(row))[0] == "restored"
    [(act, body)] = world.acts.calls
    assert (act, body["expected_status"]) == ("restored", "repointed")
    assert not any(entry.startswith(("tag ", "start ")) for entry in world.log)


def test_a_partial_hand_restore_while_repointed_changes_nothing() -> None:
    world = World(
        _apps(tag=PARENT, code_brain="latest"),
        serving={"app-brain": PARENT, "code-brain": MERGE},
    )

    status, _ = _only(world.run(_repointed()))

    assert status == "pinned" and world.acts.calls == []


# --------------------------------------------------------------------------------------------
# Fix round 3: a pin is confirmed before a tag on its restore tag can mean a person put it back.
# --------------------------------------------------------------------------------------------


def test_the_pin_is_confirmed_once_every_tag_is_set() -> None:
    world = World()

    assert _only(world.run(_row("bad")))[0] == "repointed"

    assert [act for act, _ in world.acts.calls] == ["repoint", "pinned"]
    assert world.log.index("pinned") > world.log.index(f"tag u2 {PARENT[:12]}")
    assert world.acts.calls[1][1]["expected_status"] == "repointed"


def test_a_pin_refused_on_the_first_application_is_not_confirmed_and_is_repaired() -> None:
    """Every tag still equals its restore tag after a PATCH refused on the first app: that is a pin
    that never happened, not a person's take-over. The next pass points both and confirms."""
    first = World(fail_on="u1")
    assert _only(first.run(_row("bad")))[0] == "error"
    assert [act for act, _ in first.acts.calls] == ["repoint"]

    world = World(_apps(code_brain="main"), serving={"app-brain": MERGE, "code-brain": MERGE})
    row = _row(None, "repointed", pinned_images=_unconfirmed_pins())

    status, _ = _only(world.run(row))

    assert status == "pinned"
    assert [act for act, _ in world.acts.calls] == ["pinned"]
    assert [e for e in world.log if e.startswith(("tag ", "start "))] == [
        f"tag u1 {PARENT[:12]}",
        "start u1",
        f"tag u2 {PARENT[:12]}",
        "start u2",
    ]


def test_a_pin_refused_part_way_points_the_rest_on_the_next_pass() -> None:
    first = World(fail_on="u2")
    assert _only(first.run(_row("bad")))[0] == "error"
    assert [act for act, _ in first.acts.calls] == ["repoint"]

    world = World(
        _apps(tag=PARENT, code_brain="main"),
        serving={"app-brain": PARENT, "code-brain": MERGE},
    )
    row = _row(None, "repointed", pinned_images=_unconfirmed_pins())

    status, detail = _only(world.run(row))

    assert status == "pinned" and "by hand" not in detail
    assert [e for e in world.log if e.startswith(("tag ", "start "))] == [
        f"tag u2 {PARENT[:12]}",
        "start u2",
    ]
    assert [act for act, _ in world.acts.calls] == ["pinned"]


def test_an_unconfirmed_failed_revert_is_never_taken_as_a_hand_restore() -> None:
    world = World(_apps(code_brain="main"))
    row = _row(None, "revert_failed", pinned_images=_unconfirmed_pins())

    assert _only(world.run(row))[0] == "revert-failed"
    assert world.acts.calls == []


def _unconfirmed_pins() -> dict[str, dict[str, str]]:
    return {
        "u1": {"tag": PARENT, "digest": DIGEST, "restore_tag": "latest"},
        "u2": {"tag": PARENT, "digest": DIGEST, "restore_tag": "main"},
    }


def test_a_pin_marked_on_only_some_images_is_not_confirmed() -> None:
    """Every image carries the mark, or the pin is unconfirmed and completed again."""
    pins = _unconfirmed_pins()
    pins["u1"] = {**pins["u1"], "pinned": "yes"}
    world = World(_apps(code_brain="main"), serving={"app-brain": MERGE, "code-brain": MERGE})

    status, _ = _only(world.run(_row(None, "repointed", pinned_images=pins)))

    assert status == "pinned"
    assert [act for act, _ in world.acts.calls] == ["pinned"]


# --------------------------------------------------------------------------------------------
# Fix round 4.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        _row(
            "unreadable",
            "repointed",
            detail="landing_revert_lane_off",
            pinned_images=_unconfirmed_pins(),
        ),
        _row(
            "unreadable",
            "revert_landed",
            detail="landing_revert_lane_off",
            pinned_images=_pins(),
            revert_merge_sha=REVERT_MERGE,
        ),
    ],
)
def test_a_record_whose_lane_is_off_is_never_stepped_and_stays_pinned(row: dict[str, Any]) -> None:
    """A person switched the estate lane off to take over: nothing on Coolify moves, and the pinned
    alert stays open (it keys on the record's status and pins, not on the verdict)."""
    world = World(_apps(code_brain="main"), registry={PARENT: DIGEST, REVERT_MERGE: DIGEST})

    status, _ = _only(world.run(row))

    assert status == "unreadable"
    assert world.log == [] and world.acts.calls == []
    assert pinned_key(BRAIN, PR) in revert_alerts([row])


@pytest.mark.parametrize("act", ["revert", "restored"])
def test_an_unconfirmed_pin_refused_by_the_orchestrator_is_waiting(act: str) -> None:
    refused = LandingRefused("the pin is not confirmed", "landing_pin_unconfirmed")
    world = World(
        _apps(tag=PARENT) if act == "revert" else _apps(code_brain="main"),
        serving={"app-brain": PARENT, "code-brain": PARENT},
        acts_error=refused,
    )

    assert _only(world.run(_repointed()))[0] == "waiting"


def test_any_other_refusal_while_confirming_is_a_finding() -> None:
    refused = LandingRefused("the record moved", "landing_revert_record_moved")
    world = World(
        _apps(tag=PARENT), serving={"app-brain": PARENT, "code-brain": PARENT}, acts_error=refused
    )

    assert _only(world.run(_repointed()))[0] == "error"
