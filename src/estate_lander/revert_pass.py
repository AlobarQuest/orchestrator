"""The estate lane's watch: a landed bump that went bad is pointed back, reverted, and restored.

Landing Part 3b, Task 4. The orchestrator judges each of this lane's landings (`GET
/api/v1/landing-watch`, the rows whose `lane` is `estate`); this pass acts on the judgment, ONE
STEP PER PASS, each recorded before the next:

1. `bad` (by its checks or by what production served) -- resolve the parent's image in the
   registry, then ask the orchestrator to record `repointed` with the images (it judges again,
   and refuses unless the landing is still bad), THEN point each application at the parent's image
   and start a deploy of it. Recording first means every pin has a record, and the record's
   `landing-production-pinned` alert, behind it; a pin that fails part-way is repeated next pass.
2. `repointed` -- once every application serves the parent, ask the orchestrator to open the
   revert (it opens from `repointed` only). Until then, any application not on the parent's tag is
   pointed at it again.
3. The revert is filed by `change_proposer`, approved by deploy policy 14, and landed by this
   lane's landing pass (any hour); the merge path records `revert_landed` and holds the dependency.
4. `revert_landed` with images pinned -- once the registry holds the revert's own build, set each
   application back to the moving tag it had and start a deploy; once every application serves the
   revert's merge commit (or a later one), ask the orchestrator to record `restored`.

A record that pinned production but whose revert was refused (`refused_overlap`) or failed
(`revert_failed`) has no revert to land; a person fixes the default branch and puts the moving tags
back by hand. Once every pinned application's tag is its recorded restore tag again, the pass asks
the orchestrator to record `restored`, and the pinned alert resolves.

A person's change to an application's tag while production is pinned is never undone by the
confirm step: only an application still on the parent's tag is deployed again. The restore after a
landed revert, though, sets every application back to the tag it had before the pin, overwriting
whatever a person set in between.

A Compose application whose domains live in Traefik labels has an empty Coolify `fqdn`, so no
Coolify application matches its health host and the re-point is refused by design (alerted).

A `flaky_retry` is rerun as in the inert lane. Every exit before `restored` leaves production
pinned or untouched and an alert open (`lander.held_alerts`): pinned for every record that pinned
images, and `landing-not-pointed-back` for a bad landing this pass could not point back at all --
no token, an application no Coolify application serves (or two do), the parent's image missing
from the registry, an image not this estate's, or a tag already pinned to a commit by hand.

The re-point is confined: a new hosting-platform client per repository, matched from the member
record (`image_repoint.CoolifyImages.confine`). Without `--submit` nothing is read from or written
to the platform, the registry or the orchestrator's acts; each row reports what a pass would do.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from estate_lander.image_repoint import (
    Application,
    AppSpec,
    RegistryUnreadable,
    RepointRefused,
    image_name,
)
from estate_lander.served import ServedUnreadable, serves
from lander.core import Outcome
from lander.errors import LandingRefused, OrchestratorError

ESTATE_LANE: Final = "estate"
_FULL_SHA: Final = re.compile(r"[0-9a-f]{40}")

# Record statuses and verdicts, as the watch serves them.
_BAD: Final = "bad"
_FLAKY_RETRY: Final = "flaky_retry"
_UNREADABLE: Final = "unreadable"
_REPOINTED: Final = "repointed"
_REVERT_LANDED: Final = "revert_landed"
_RESTORED: Final = "restored"
_REPORTED_RECORDS: Final = {  # not-a-vocabulary: the watch's record statuses, relayed
    "refused_overlap": "revert-refused",
    "revert_failed": "revert-failed",
}
# The step a pinned record with no revert to land waits on: a person's hand restore.
_HAND_RESTORE: Final = "hand_restore"
# The orchestrator's refusals of a re-point that say only that its answer moved since the watch was
# read: nothing was touched, and the next pass asks again. Every other refusal is alerted.
_ANSWER_MOVED: Final = frozenset(  # not-a-vocabulary: the orchestrator's refusal codes, relayed
    {"landing_revert_record_moved", "landing_repoint_not_bad"}
)
_ACT_ANSWERS: Final = {  # not-a-vocabulary: the act's answers, relayed
    "rerun_requested": "reran",
    "revert_opened": "revert-opened",
}


class Images(Protocol):
    """The confined hosting-platform client (`image_repoint.CoolifyImages`)."""

    def confine(self, apps: Sequence[AppSpec]) -> tuple[Application, ...]: ...

    def set_tag(self, uuid: str, tag: str) -> None: ...

    def start(self, uuid: str) -> None: ...

    def close(self) -> None: ...


class Digests(Protocol):
    """The registry (`image_repoint.Registry`)."""

    def digest(self, image: str, tag: str) -> str | None: ...


class Acts(Protocol):
    """The orchestrator's three acts on a watched landing."""

    def revert_landing(
        self, repository: str, pr_number: int, *, expected_status: str | None, idempotency_key: str
    ) -> dict[str, Any]: ...

    def repoint(
        self,
        repository: str,
        pr_number: int,
        *,
        expected_status: str | None,
        pinned_images: dict[str, dict[str, str]],
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def restored(
        self, repository: str, pr_number: int, *, expected_status: str, idempotency_key: str
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Production:
    """Everything this pass reads production through, built by the CLI for a submitting pass.

    `images` builds a new confined client (one per repository); None when the launcher fetched no
    token, which refuses every re-point and restore by name. `apps` is the member record's
    applications for a repository; `served` an application's served commit; `at_or_after` whether
    a served commit is the given commit or a later one on the default branch.
    """

    images: Callable[[], Images] | None
    registry: Digests
    apps: Callable[[str], tuple[AppSpec, ...]]
    served: Callable[[str], str | None]
    at_or_after: Callable[[str, str, str], bool]


@dataclass
class RevertPass:
    outcomes: list[Outcome] = field(default_factory=list)
    # Bad landings this pass could not point back, with why: `landing-not-pointed-back`.
    unpinned: dict[tuple[str, int], str] = field(default_factory=dict)


class _Refused(Exception):
    """A step that could not be taken before anything changed. Its message is the reason."""


def lane_rows(watch: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """This lane's rows of the watch: the landings that redeploy."""
    return [dict(row) for row in watch if row.get("lane") == ESTATE_LANE]


def revert_pass(
    rows: Sequence[Mapping[str, Any]],
    client: Acts,
    production: Production | None,
    submit: bool,
) -> RevertPass:
    """Take the next step for each of this lane's watched landings. `production` is None on a pass
    that does not submit."""
    result = RevertPass()
    for row in rows:
        repository, number = row.get("repository"), row.get("pr_number")
        if not isinstance(repository, str) or not isinstance(number, int):
            continue
        outcome = _step(row, repository, number, client, production if submit else None, result)
        if outcome is not None:
            result.outcomes.append(outcome)
    return result


def _step(
    row: Mapping[str, Any],
    repository: str,
    number: int,
    client: Acts,
    production: Production | None,
    result: RevertPass,
) -> Outcome | None:
    """The one step this landing is at: acted on with `production`, else what a pass would do."""
    step = _next_step(row)
    if step is None:
        return _reported(row, repository, number)
    if production is None:
        if step == _HAND_RESTORE:
            return _reported(row, repository, number)
        return Outcome(repository, number, _DRY_RUN[step], str(row.get("detail") or ""))
    if step == _FLAKY_RETRY:
        return _rerun(row, repository, number, client)
    if step == _BAD:
        return _repoint(row, repository, number, client, production, result)
    if step == _REPOINTED:
        return _confirm_parent(row, repository, number, client, production)
    if step == _HAND_RESTORE:
        return _hand_restored(row, repository, number, client, production)
    return _restore(row, repository, number, client, production)


# What a pass that does not submit reports for each step, keyed as `_next_step` names them.
_DRY_RUN: Final = {  # not-a-vocabulary: report statuses
    _FLAKY_RETRY: "would-rerun",
    _BAD: "would-repoint",
    _REPOINTED: "pinned",
    _REVERT_LANDED: "would-restore",
}


def _next_step(row: Mapping[str, Any]) -> str | None:
    """Which act this landing waits on: its verdict's, or its record's; None for none."""
    verdict, status = row.get("verdict"), row.get("status")
    if verdict in (_FLAKY_RETRY, _BAD):
        return str(verdict)
    if verdict is None and status == _REPOINTED:
        return _REPOINTED
    if verdict is None and status == _REVERT_LANDED and row.get("pinned_images"):
        return _REVERT_LANDED
    if verdict is None and status in _REPORTED_RECORDS and row.get("pinned_images"):
        return _HAND_RESTORE
    return None


def _reported(row: Mapping[str, Any], repository: str, number: int) -> Outcome | None:
    """A row that waits on no act here, reported when it says something."""
    verdict, status = row.get("verdict"), row.get("status")
    detail = str(row.get("detail") or "")
    if verdict == _UNREADABLE:
        return Outcome(repository, number, "unreadable", detail)
    if verdict is not None:
        return None
    if status == _REVERT_LANDED:
        return Outcome(repository, number, "reverted", detail)
    if isinstance(status, str) and status in _REPORTED_RECORDS:
        return Outcome(repository, number, _REPORTED_RECORDS[status], detail)
    return None


def _rerun(row: Mapping[str, Any], repository: str, number: int, client: Acts) -> Outcome:
    runs = row.get("failed_runs")
    named = ",".join(
        f"{run[0]}.{run[1]}"
        for run in (runs if isinstance(runs, list) else [])
        if isinstance(run, list) and len(run) == 2
    )
    status = row.get("status")
    try:
        answer = client.revert_landing(
            repository,
            number,
            expected_status=status if isinstance(status, str) else None,
            idempotency_key=f"estate-landing-revert:{repository}:{number}:{_FLAKY_RETRY}:{named}",
        )
    except (LandingRefused, OrchestratorError) as error:
        return Outcome(repository, number, "error", str(error))
    return _answered(answer, repository, number)


def _answered(answer: Mapping[str, Any], repository: str, number: int) -> Outcome:
    """The act's answer as a report line: what it recorded, or that it judged nothing to do."""
    detail = str(answer.get("detail") or "")
    status = answer.get("status")
    if answer.get("acted") is False:
        if answer.get("verdict") == _UNREADABLE:
            return Outcome(repository, number, "unreadable", detail or "a read in the act failed")
        return Outcome(repository, number, "waiting", detail or "nothing to do yet")
    if answer.get("acted") is not True or not isinstance(status, str):
        return Outcome(repository, number, "unreadable", detail or "nothing was done")
    return Outcome(repository, number, _ACT_ANSWERS.get(status, status), detail)


def _repoint(
    row: Mapping[str, Any],
    repository: str,
    number: int,
    client: Acts,
    production: Production,
    result: RevertPass,
) -> Outcome:
    """Step 1: resolve the parent's image, record `repointed`, then pin and deploy each app.

    Everything that can refuse is asked before the record, and nothing is changed before it: a
    refusal leaves production as it was and raises `landing-not-pointed-back`.
    """
    parent = str(row.get("parent_sha") or "").lower()
    status = row.get("status")
    try:
        if not _FULL_SHA.fullmatch(parent):
            raise _Refused("the watch names no commit before the bump")
        images = _open(production)
    except _Refused as error:
        result.unpinned[(repository, number)] = str(error)
        return Outcome(repository, number, "repoint-refused", str(error))
    try:
        try:
            applications = images.confine(production.apps(repository))
            image = _unpinned_image(applications)
            if image_name(image) != repository.split("/")[-1].lower():
                raise _Refused(f"the applications run {image}, not {repository}'s image")
            digest = production.registry.digest(image, parent)
            if digest is None:
                raise _Refused(f"the registry holds no {image}:{parent[:12]}")
            client.repoint(
                repository,
                number,
                expected_status=status if isinstance(status, str) else None,
                pinned_images={
                    app.uuid: {"tag": parent, "digest": digest, "restore_tag": app.tag}
                    for app in applications
                },
                idempotency_key=f"estate-landing-repoint:{repository}:{number}:{parent}:{digest}",
            )
        except LandingRefused as refused:
            if refused.code in _ANSWER_MOVED:
                # Judged again and found nothing to point back, or the record moved: nothing was
                # recorded and nothing was touched, and the next pass asks again.
                return Outcome(repository, number, "waiting", str(refused))
            result.unpinned[(repository, number)] = str(refused)
            return Outcome(repository, number, "repoint-refused", str(refused))
        except (_Refused, RepointRefused, RegistryUnreadable, OrchestratorError) as error:
            result.unpinned[(repository, number)] = str(error)
            return Outcome(repository, number, "repoint-refused", str(error))
        already = _serving(production, applications, parent)
        failed = _point(images, applications, parent, serving=already)
    finally:
        images.close()
    if failed is not None:
        return Outcome(repository, number, "error", failed)
    return Outcome(
        repository, number, "repointed", f"{len(applications)} application(s) on {parent[:12]}"
    )


def _point(
    images: Images,
    applications: Sequence[Application],
    tag: str,
    *,
    serving: frozenset[str] = frozenset(),
) -> str | None:
    """Set each application's tag and start a deploy of it -- none for an application `serving`
    names, which already runs that build; None when all succeeded, else what was done and what
    refused. Production stays recorded as pinned either way."""
    done: list[str] = []
    for app in applications:
        try:
            images.set_tag(app.uuid, tag)
            if app.name not in serving:
                images.start(app.uuid)
        except RepointRefused as error:
            return f"{app.name}: {error}; {len(done)} of {len(applications)} pointed at {tag[:12]}"
        done.append(app.name)
    return None


def _serving(
    production: Production, applications: Sequence[Application], commit: str
) -> frozenset[str]:
    """The applications that already serve `commit`; one that cannot be read is not among them."""
    serving: set[str] = set()
    for app in applications:
        try:
            if serves(production.served(app.health_url), commit):
                serving.add(app.name)
        except ServedUnreadable:
            continue
    return frozenset(serving)


def _confirm_parent(
    row: Mapping[str, Any], repository: str, number: int, client: Acts, production: Production
) -> Outcome:
    """Step 2: every application serves the parent, so ask for the revert; else start a deploy of
    each application not serving it whose tag is still the parent's (its deploy never started, or
    the pin failed part-way). An application whose tag a person changed is left alone and named;
    production stays pinned and the alert open."""
    parent = str(row.get("parent_sha") or "").lower()
    try:
        images = _open(production)
    except _Refused as error:
        return Outcome(repository, number, "error", f"production stays pinned: {error}")
    try:
        applications = _pinned_applications(images, row, production, repository)
        serving = {app.name: production.served(app.health_url) for app in applications}
        off = sorted(name for name, served in serving.items() if not serves(served, parent))
        if not off:
            answer = client.revert_landing(
                repository,
                number,
                expected_status=_REPOINTED,
                idempotency_key=f"estate-landing-revert:{repository}:{number}:{_REPOINTED}",
            )
            return _answered(answer, repository, number)
        lagging = [app for app in applications if app.name in off]
        again = [app for app in lagging if app.tag.lower() == parent]
        by_hand = sorted(app.name for app in lagging if app.tag.lower() != parent)
        failed = _point(images, again, parent)
        if failed is not None:
            return Outcome(repository, number, "error", failed)
        detail = f"not serving {parent[:12]} yet: {', '.join(off)}"
        if by_hand:
            detail += f"; tag changed by hand on {', '.join(by_hand)}, left alone"
        return Outcome(repository, number, "pinned", detail)
    except (_Refused, RepointRefused, ServedUnreadable) as error:
        return Outcome(repository, number, "error", f"production stays pinned: {error}")
    except (LandingRefused, OrchestratorError) as error:
        return Outcome(repository, number, "error", str(error))
    finally:
        images.close()


def _hand_restored(
    row: Mapping[str, Any], repository: str, number: int, client: Acts, production: Production
) -> Outcome:
    """A pinned record with no revert to land: once a person has put every moving tag back, ask
    the orchestrator to record `restored`; until then it is reported as it was, still a finding."""
    status = str(row.get("status"))
    pinned = row.get("pinned_images")
    waiting = Outcome(
        repository,
        number,
        _REPORTED_RECORDS[status],
        f"{row.get('detail') or status}; production stays pinned until a person restores it",
    )
    if not isinstance(pinned, Mapping):
        return waiting
    try:
        images = _open(production)
    except _Refused as error:
        return Outcome(repository, number, "error", f"production stays pinned: {error}")
    try:
        applications = _pinned_applications(images, row, production, repository)
        if any(app.tag != str(pinned[app.uuid].get("restore_tag")) for app in applications):
            return waiting
        answer = client.restored(
            repository,
            number,
            expected_status=status,
            idempotency_key=f"estate-landing-restored:{repository}:{number}:{status}",
        )
    except (_Refused, RepointRefused) as error:
        return Outcome(repository, number, "error", f"production stays pinned: {error}")
    except (LandingRefused, OrchestratorError) as error:
        return Outcome(repository, number, "error", str(error))
    finally:
        images.close()
    if answer.get("status") != _RESTORED:
        return Outcome(repository, number, "unreadable", str(answer.get("detail") or ""))
    return Outcome(repository, number, "restored", "the moving tags were put back by hand")


def _restore(
    row: Mapping[str, Any], repository: str, number: int, client: Acts, production: Production
) -> Outcome:
    """Step 4: once the revert's build exists, put each moving tag back and deploy; once every
    application serves the revert (or later), record `restored`."""
    merge = str(row.get("revert_merge_sha") or "").lower()
    pinned = row.get("pinned_images")
    if not _FULL_SHA.fullmatch(merge) or not isinstance(pinned, Mapping):
        return Outcome(repository, number, "error", "the watch names no revert merge commit")
    try:
        images = _open(production)
    except _Refused as error:
        return Outcome(repository, number, "error", f"production stays pinned: {error}")
    try:
        applications = _pinned_applications(images, row, production, repository)
        image = _one_image(applications)
        if production.registry.digest(image, merge) is None:
            return Outcome(
                repository, number, "pinned", f"the revert's image {merge[:12]} is not built yet"
            )
        waiting = _put_back(images, applications, pinned, production, repository, merge)
        if waiting is not None:
            return Outcome(repository, number, "restoring", waiting)
        answer = client.restored(
            repository,
            number,
            expected_status=_REVERT_LANDED,
            idempotency_key=f"estate-landing-restored:{repository}:{number}:{merge}",
        )
    except (_Refused, RepointRefused, RegistryUnreadable, ServedUnreadable) as error:
        return Outcome(repository, number, "error", f"production stays pinned: {error}")
    except (LandingRefused, OrchestratorError) as error:
        return Outcome(repository, number, "error", str(error))
    finally:
        images.close()
    if answer.get("status") != _RESTORED:
        return Outcome(repository, number, "unreadable", str(answer.get("detail") or ""))
    return Outcome(repository, number, "restored", f"serving {merge[:12]} or later")


def _put_back(
    images: Images,
    applications: Sequence[Application],
    pinned: Mapping[str, Any],
    production: Production,
    repository: str,
    merge: str,
) -> str | None:
    """Put each moving tag back and start a deploy; else start a deploy of each application not
    yet serving the revert (its last one may never have started). None once every application
    serves the revert or a later commit."""
    restore = {uuid: str(pinned[uuid]["restore_tag"]) for uuid in pinned}
    moved = [app for app in applications if app.tag != restore[app.uuid]]
    if moved:
        for app in moved:
            images.set_tag(app.uuid, restore[app.uuid])
            images.start(app.uuid)
        return f"moving tag put back on {', '.join(app.name for app in moved)}"
    off = [
        app
        for app in applications
        if not _at_or_after(production, repository, merge, production.served(app.health_url))
    ]
    for app in off:
        images.start(app.uuid)
    if off:
        return f"not serving {merge[:12]} yet, deployed again: {', '.join(a.name for a in off)}"
    return None


def _open(production: Production) -> Images:
    """A new confined client, or `_Refused` naming why there is none."""
    if production.images is None:
        raise _Refused("no Coolify token: the launcher fetched none")
    try:
        return production.images()
    except RepointRefused as error:
        raise _Refused(str(error)) from None


def _one_image(applications: Sequence[Application]) -> str:
    """The one image every application of the repository runs."""
    images = {app.image.lower() for app in applications}
    if len(images) != 1:
        raise _Refused(f"the applications run {len(images)} different images")
    return images.pop()


def _unpinned_image(applications: Sequence[Application]) -> str:
    """`_one_image`, refused when an application is already pinned to a commit (by a person, or by
    a re-point nobody recorded): the moving tag to restore would be lost."""
    pinned = sorted(app.name for app in applications if _FULL_SHA.fullmatch(app.tag.lower()))
    if pinned:
        raise _Refused(f"already pinned to a commit: {', '.join(pinned)}")
    return _one_image(applications)


def _pinned_applications(
    images: Images, row: Mapping[str, Any], production: Production, repository: str
) -> tuple[Application, ...]:
    """The repository's applications, confined again this pass, and exactly the ones pinned."""
    applications = images.confine(production.apps(repository))
    pinned = row.get("pinned_images")
    if not isinstance(pinned, Mapping) or set(pinned) != {app.uuid for app in applications}:
        raise _Refused("the applications the platform serves are not the ones pinned")
    return applications


def _at_or_after(production: Production, repository: str, commit: str, served: str | None) -> bool:
    if served is None:
        return False
    return serves(served, commit) or production.at_or_after(repository, commit, served)
