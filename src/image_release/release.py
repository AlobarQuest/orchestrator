"""One pass: bind what THIS deploy shipped, observe it once, verify what that minted.

A UNIT IS BOUND TO THE FIRST DEPLOYED IMAGE THAT CARRIES IT, from this program's first run
onward. "Carries" is reachability from `--built-commit`; "first" is "not already carried by
`--previous-commit`", the revision production served before the swap. So the first real run binds
only what that deploy shipped, rather than stamping the whole history onto today's digest, and a
unit an earlier image shipped is SHIPPED EARLIER and stays unbound.

NOTHING IS WRITTEN UNTIL PRODUCTION IS CONFIRMED. The probe runs once per pass, before any
binding, retried until production is fully healthy. If it still does not serve the built commit,
or answers an unauthenticated read with anything but 401 (which the orchestrator cannot record),
the pass refuses with nothing written. A probe still otherwise degraded after every retry is filed
as it is: the observation is final, and a genuine failure is what it exists to record.

WHAT IS NOT A FINDING: a unit that landed after the build (NOT CARRIED), one an earlier image
shipped (SHIPPED EARLIER), and one bound to another digest whose observation exists (RELEASED BY
AN EARLIER IMAGE). Bindings are write-once per source tuple, so this image cannot re-bind those.

A CONDITION (exit 2): production does not serve the build or its auth posture is unrecordable; a
binding to another digest was never observed (orphaned); a merge commit the checkout does not hold
(fetch and re-run); or verification asks for revision or review.

A FINDING (exit 3): an answer that is missing -- a refusal, an unreachable orchestrator, a checkout
that cannot answer, or a unit bound to this digest that the built commit does not carry.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from image_release.ancestry import AncestryError, carries, holds_commit
from image_release.client import (
    DEAD_LETTER_ENDPOINT,
    HEALTH_LIVE,
    HEALTH_READY,
    OPENAPI_DOCUMENT,
    ForbiddenEndpointError,
    Probe,
    ReleaseCallError,
)

CONTAINER_IMAGE_KIND = "container_image"

# The routes a healthy orchestrator image must serve for THIS deploy step and the lanes beside it
# to work: the health pair, the dead-letter read the auth probe uses, the candidate read, and the
# three routes this program writes through. Chosen from what this program itself depends on, so a
# route rename that would break the next deploy's binding reddens this one's verification first.
# Every one is either GET-only or a route this program's ADR-0039 row already permits it to write.
REQUIRED_ROUTES = (
    HEALTH_LIVE,
    HEALTH_READY,
    DEAD_LETTER_ENDPOINT,
    "/api/v1/machine-activation-candidates",
    "/api/v1/work-units/{unit_id}/release-artifacts",
    "/api/v1/release-artifacts/{binding_id}/deployment-observations",
    "/api/v1/work-units/{unit_id}/verify",
)

HEALTHY_MIN = 200
HEALTHY_MAX = 299
UNAUTHENTICATED_STATUS = 401
AUTHENTICATED_STATUS = 200

# A post-deploy unit is minted by `_post_deploy_work_unit` at the column default, version 1, and
# no route the VERIFIER may call reads a unit's version. A unit already terminal replays through
# the verifier's own idempotency key whatever version is sent; a unit left in VERIFYING by an
# interrupted call is at version 2 and answers `version_conflict`, which this pass reports as a
# refusal for a person to look at.
MINTED_POST_DEPLOY_VERSION = 1

# The whole production probe is retried until production is FULLY healthy, because what is filed
# is final: one observation per binding per environment, and the unit it mints has one attempt. A
# swap that is seconds from settling must not become a permanent revision_required.
PROBE_ATTEMPTS = 6
PROBE_INTERVAL_SECONDS = 10.0

# Per-phase outcomes.
BOUND = "bound"  # bound by this pass
ALREADY_BOUND = "already_bound"  # bound to this digest by an earlier pass
EARLIER_IMAGE = "released_by_earlier_image"  # bound to another digest, and observed
ORPHANED_BINDING = "orphaned_binding"  # bound to another digest, and never observed
NOT_CARRIED = "not_carried"  # landed after the built commit
SHIPPED_EARLIER = "shipped_earlier"  # the previous image already carried it
MERGE_COMMIT_MISSING = "merge_commit_missing"  # the checkout cannot answer; fetch and re-run
OBSERVED = "observed"
ALREADY_OBSERVED = "already_observed"
REVISION_MISMATCH = "revision_mismatch"
AUTH_POSTURE_UNRECORDABLE = "auth_posture_unrecordable"
REFUSED = "refused"
UNAVAILABLE = "unavailable"
DRY_RUN = "dry_run"

COMPLETED = "completed"
REVISION_REQUIRED = "revision_required"
AWAITING_REVIEW = "awaiting_review"

FINDING_OUTCOMES = frozenset({REFUSED, UNAVAILABLE})
CONDITION_OUTCOMES = frozenset(
    {
        REVISION_MISMATCH,
        AUTH_POSTURE_UNRECORDABLE,
        ORPHANED_BINDING,
        MERGE_COMMIT_MISSING,
        REVISION_REQUIRED,
        AWAITING_REVIEW,
    }
)

RECOVERABLE = (ReleaseCallError, AncestryError, KeyError, TypeError, ValueError)
# A request aimed outside a client's surface is a programming error, never "production could not
# be asked". It subclasses `ReleaseCallError`, so it is named to keep it from being absorbed.
UNRECOVERABLE = (ForbiddenEndpointError,)


class System(Protocol):
    def candidates(self, repository: str) -> list[dict[str, Any]]: ...

    def observations(self, binding_id: str) -> list[dict[str, Any]]: ...

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def observe(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def authenticated_probe(self, path: str) -> Probe: ...


class Verifier(Protocol):
    def verify(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class Anonymous(Protocol):
    def probe(self, path: str) -> Probe: ...


@dataclass(frozen=True)
class Release:
    """What the operator verified on the host, and where the image came from."""

    repository: str
    built_commit: str
    # The revision `/health/live` reported BEFORE the swap. What it already carried was shipped by
    # an earlier image, so this deploy binds only what lies between the two.
    previous_commit: str
    digest: str
    registry: str
    image_repository: str
    image_name: str
    tag: str
    workflow_run_url: str
    base_url: str
    deployer: str
    environment: str

    @property
    def derivable_tag(self) -> str:
        # The tag `scripts/compute_image_tags.py` pushes beside the readable one: a function of
        # the full sha alone, so a production-revision observation can be joined on it later.
        return f"sha-{self.built_commit}"


@dataclass(frozen=True)
class Candidate:
    """One row of the orchestrator's answer, read defensively: every field is required.

    A response model DROPS every key it does not declare, so a field that stopped being served
    would arrive as absence rather than as an error.
    """

    work_unit_id: str
    package_revision_id: str
    package_revision_hash: str
    unit_key: str
    work_unit_version: int
    source_repository: str
    pr_number: int
    source_commit: str
    merge_commit: str
    binding_id: str | None
    binding_artifact_digest: str | None
    observed: bool

    @classmethod
    def of(cls, row: dict[str, Any]) -> Candidate:
        required = (
            "work_unit_id",
            "work_package_revision_id",
            "package_revision_hash",
            "unit_key",
            "source_repository",
            "pr_number",
            "source_commit",
            "merge_commit",
        )
        missing = [name for name in required if not row.get(name)]
        # Presence, not truthiness: a version is never 0 today, but 0 is a real version.
        if row.get("work_unit_version") is None:
            missing.append("work_unit_version")
        if row.get("binding_id") is not None and row.get("binding_artifact_digest") is None:
            # A bound candidate always has a digest. An absent one is a narrowed contract, and
            # folded into the digest comparison it would read every unit as an earlier image's.
            missing.append("binding_artifact_digest")
        if missing:
            raise ReleaseCallError(
                f"the orchestrator's candidate is missing {', '.join(sorted(missing))}"
            )
        return cls(
            work_unit_id=str(row["work_unit_id"]),
            package_revision_id=str(row["work_package_revision_id"]),
            package_revision_hash=str(row["package_revision_hash"]),
            unit_key=str(row["unit_key"]),
            work_unit_version=int(row["work_unit_version"]),
            source_repository=str(row["source_repository"]),
            pr_number=int(row["pr_number"]),
            source_commit=str(row["source_commit"]),
            merge_commit=str(row["merge_commit"]),
            binding_id=None if row.get("binding_id") is None else str(row["binding_id"]),
            binding_artifact_digest=(
                None
                if row.get("binding_artifact_digest") is None
                else str(row["binding_artifact_digest"])
            ),
            observed=row.get("observation_id") is not None,
        )


@dataclass(frozen=True)
class Production:
    """What one probe pass of production measured, already in the observation's own shapes."""

    served_revision: object
    revision_matches: bool
    healthy: bool
    missing_m2m_status: int
    probe_summary: dict[str, Any]
    route_summary: dict[str, Any]
    auth_summary: dict[str, Any]
    status_summary: dict[str, Any]
    observed_at: str


def binding_payload(candidate: Candidate, release: Release) -> dict[str, Any]:
    return {
        # A function of the unit alone, so a response lost inside one pass retries with the same
        # bytes; a second image never reaches this, because the candidate then reports the
        # binding and the pass skips it.
        "idempotency_key": f"image-release:{candidate.work_unit_id}",
        # Required and non-nullable on the wire: `None` is a 422 before the service is reached.
        "expected_version": candidate.work_unit_version,
        "kind": CONTAINER_IMAGE_KIND,
        "package_revision_id": candidate.package_revision_id,
        "package_revision_hash": candidate.package_revision_hash,
        "source_repository": candidate.source_repository,
        "implementation_pr_number": candidate.pr_number,
        "source_commit": candidate.source_commit,
        "merge_commit": candidate.merge_commit,
        "artifact_registry": release.registry,
        "artifact_repository": release.image_repository,
        "artifact_name": release.image_name,
        "artifact_digest": release.digest,
        "artifact_tag": release.derivable_tag,
        "workflow_run_url": release.workflow_run_url,
        "summary": {"image": {"built_commit": release.built_commit, "tag": release.tag}},
    }


def observation_payload(
    binding_id: str, release: Release, production: Production
) -> dict[str, Any]:
    return {
        # One observation per binding per environment is all the service admits, so the key is
        # exactly that pair.
        "idempotency_key": f"image-observation:{binding_id}:{release.environment}",
        # Required by the route's model; the subject is the immutable binding, so 0.
        "expected_version": 0,
        "kind": CONTAINER_IMAGE_KIND,
        "environment": release.environment,
        "base_url": release.base_url,
        "observed_artifact_digest": release.digest,
        "deployment_ref": release.tag,
        "deployment_url": release.workflow_run_url,
        "deployer": release.deployer,
        "observed_at": production.observed_at,
        "probe_summary": production.probe_summary,
        "route_summary": production.route_summary,
        "auth_summary": production.auth_summary,
        "status_summary": production.status_summary,
    }


def verify_payload(post_deploy_unit_id: str) -> dict[str, Any]:
    return {
        "idempotency_key": f"image-verify:{post_deploy_unit_id}",
        "expected_version": MINTED_POST_DEPLOY_VERSION,
    }


def probe_until_healthy(
    anonymous: Anonymous,
    system: System,
    release: Release,
    *,
    sleep: Callable[[float], None],
    now: datetime | None = None,
) -> tuple[Production | None, int, str | None]:
    """Probe up to `PROBE_ATTEMPTS` times until production is fully healthy and serves the build.

    Returns the last measurement, the attempts it took, and why production could not be asked at
    all when the last attempt could not. A measurement still degraded after every attempt is
    returned as it is: that is a real failure, and filing it is the point.
    """
    production: Production | None = None
    reason: str | None = None
    for attempt in range(1, PROBE_ATTEMPTS + 1):
        if attempt > 1:
            sleep(PROBE_INTERVAL_SECONDS)
        try:
            production = measure_production(anonymous, system, release, now=now)
        except UNRECOVERABLE:
            raise
        except RECOVERABLE as error:
            production, reason = None, f"production could not be probed: {error}"
            continue
        reason = None
        if production.healthy and production.revision_matches:
            return production, attempt, None
    return production, PROBE_ATTEMPTS, reason


def measure_production(
    anonymous: Anonymous, system: System, release: Release, *, now: datetime | None = None
) -> Production:
    """Probe production once. Raises `ReleaseCallError` when it could not be asked at all."""
    observed_at = (now or datetime.now(UTC)).isoformat()
    live = anonymous.probe(HEALTH_LIVE)
    ready = anonymous.probe(HEALTH_READY)
    document = anonymous.probe(OPENAPI_DOCUMENT)
    missing = anonymous.probe(DEAD_LETTER_ENDPOINT).status_code
    configured = system.authenticated_probe(DEAD_LETTER_ENDPOINT).status_code

    served_revision = live.body.get("revision") if isinstance(live.body, dict) else None
    probes = [
        {
            "name": name,
            "endpoint": path,
            "method": "GET",
            "status_code": probe.status_code,
            "expected_status_min": HEALTHY_MIN,
            "expected_status_max": HEALTHY_MAX,
            "observed_at": observed_at,
        }
        for name, path, probe in (("live", HEALTH_LIVE, live), ("ready", HEALTH_READY, ready))
    ]
    served_paths = (
        document.body.get("paths")
        if document.status_code == AUTHENTICATED_STATUS and isinstance(document.body, dict)
        else None
    )
    routes = [
        {"path": path, "present": isinstance(served_paths, dict) and path in served_paths}
        for path in REQUIRED_ROUTES
    ]
    present = sum(1 for route in routes if route["present"])
    healthy = (
        all(HEALTHY_MIN <= probe["status_code"] <= HEALTHY_MAX for probe in probes)
        and present == len(routes)
        and missing == UNAUTHENTICATED_STATUS
        and configured == AUTHENTICATED_STATUS
    )
    return Production(
        served_revision=served_revision,
        revision_matches=served_revision == release.built_commit,
        healthy=healthy,
        missing_m2m_status=missing,
        probe_summary={"probes": probes},
        route_summary={"routes": routes},
        auth_summary={"missing_m2m_status": missing, "configured_m2m_status": configured},
        # Composed from status codes and counts ONLY. This is the one free-text field of an
        # observation that mints a unit, and ADR-0039's amendment rests on nothing from outside
        # the estate ever reaching it.
        status_summary={
            "status": "healthy" if healthy else "degraded",
            "summary": (
                f"live {live.status_code}, ready {ready.status_code}, "
                f"routes {present}/{len(routes)} present, "
                f"m2m missing {missing} configured {configured}"
            ),
        },
        observed_at=observed_at,
    )


@dataclass(frozen=True)
class _Pass:
    release: Release
    checkout: Path
    system: System
    verifier: Verifier | None
    production: Production
    dry_run: bool


def release_pass(
    release: Release,
    *,
    checkout: Path,
    system: System,
    verifier: Verifier | None,
    anonymous: Anonymous,
    dry_run: bool,
    now: datetime | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """One pass. Never raises for a recoverable failure; always returns what it managed to do."""
    summary: dict[str, Any] = {
        "repository": release.repository,
        "built_commit": release.built_commit,
        "previous_commit": release.previous_commit,
        "digest": release.digest,
        "dry_run": dry_run,
        "unavailable": False,
        "reason": None,
        "refusal": None,
        "production": None,
        "units": [],
    }
    try:
        for label, commit in (
            ("built", release.built_commit),
            ("previous", release.previous_commit),
        ):
            if not holds_commit(checkout, commit):
                raise AncestryError(f"the checkout does not hold the {label} commit")
        rows = system.candidates(release.repository)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        summary["unavailable"] = True
        summary["reason"] = str(error)
        return summary
    if not rows:
        return summary

    production, attempts, reason = probe_until_healthy(
        anonymous, system, release, sleep=sleep or time.sleep, now=now
    )
    if production is None:
        summary["unavailable"] = True
        summary["reason"] = reason
        return summary
    summary["production"] = {
        "attempts": attempts,
        "served_revision": production.served_revision,
        "revision_matches": production.revision_matches,
        "status": production.status_summary["status"],
        "summary": production.status_summary["summary"],
    }
    summary["refusal"] = _production_refusal(production)
    if summary["refusal"] is not None:
        return summary

    state = _Pass(
        release=release,
        checkout=checkout,
        system=system,
        verifier=verifier,
        production=production,
        dry_run=dry_run,
    )
    summary["units"] = [_consider(row, state) for row in rows]
    return summary


def _production_refusal(production: Production) -> dict[str, Any] | None:
    """Why nothing may be written at all, or None. Checked once, before the first binding."""
    if not production.revision_matches:
        return {
            "outcome": REVISION_MISMATCH,
            "served_revision": production.served_revision,
            "reason": "production does not serve the built commit; nothing was written",
        }
    if production.missing_m2m_status != UNAUTHENTICATED_STATUS:
        # The service records only a 401 here, so no observation could be filed -- and a binding
        # written now would be orphaned. It is also the answer a person most needs to see.
        return {
            "outcome": AUTH_POSTURE_UNRECORDABLE,
            "reason": f"an unauthenticated read answered {production.missing_m2m_status}",
        }
    return None


def _consider(row: dict[str, Any], state: _Pass) -> dict[str, Any]:
    try:
        candidate = Candidate.of(row)
    except RECOVERABLE as error:
        return {"binding": {"outcome": UNAVAILABLE, "reason": str(error)}}
    answer: dict[str, Any] = {
        "work_unit_id": candidate.work_unit_id,
        "unit_key": candidate.unit_key,
        "merge_commit": candidate.merge_commit,
    }
    binding, binding_id = _bind_phase(candidate, state)
    answer["binding"] = binding
    if binding["outcome"] not in {BOUND, ALREADY_BOUND}:
        return answer
    observation, post_deploy_unit_id = _observe_phase(candidate, binding_id, state)
    answer["observation"] = observation
    if post_deploy_unit_id is not None:
        answer["verification"] = _verify_phase(post_deploy_unit_id, state)
    return answer


def _bind_phase(candidate: Candidate, state: _Pass) -> tuple[dict[str, Any], str | None]:
    release = state.release
    if candidate.binding_id is not None:
        return _bound_phase(candidate, state)
    try:
        if not holds_commit(state.checkout, candidate.merge_commit):
            return _merge_commit_missing(), None
        if not carries(state.checkout, candidate.merge_commit, release.built_commit):
            return {"outcome": NOT_CARRIED, "reason": "landed after the built commit"}, None
        if carries(state.checkout, candidate.merge_commit, release.previous_commit):
            return {"outcome": SHIPPED_EARLIER, "reason": "the previous image carried it"}, None
    except RECOVERABLE as error:
        return {"outcome": UNAVAILABLE, "reason": str(error)}, None
    payload = binding_payload(candidate, release)
    if state.dry_run:
        return {"outcome": BOUND, "dry_run": True, "record": payload}, None
    try:
        recorded = state.system.bind(candidate.work_unit_id, payload)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        return {"outcome": REFUSED, "reason": str(error)}, None
    binding_id = None if recorded.get("id") is None else str(recorded["id"])
    if binding_id is None:
        return {"outcome": REFUSED, "reason": "the binding carried no id"}, None
    return {"outcome": BOUND, "binding_id": binding_id}, binding_id


def _merge_commit_missing() -> dict[str, Any]:
    """The checkout cannot answer for this unit (shallow or stale), bound or not."""
    return {
        "outcome": MERGE_COMMIT_MISSING,
        "reason": "the merge commit is not in the checkout; fetch and re-run",
    }


def _bound_phase(candidate: Candidate, state: _Pass) -> tuple[dict[str, Any], str | None]:
    """A candidate that already has a container-image binding."""
    if candidate.binding_artifact_digest != state.release.digest:
        if candidate.observed:
            return (
                {
                    "outcome": EARLIER_IMAGE,
                    "binding_artifact_digest": candidate.binding_artifact_digest,
                },
                None,
            )
        # Bound to an image whose deployment nobody observed: nothing will ever verify it, and
        # this image cannot re-bind it. A person has to look.
        return (
            {
                "outcome": ORPHANED_BINDING,
                "binding_artifact_digest": candidate.binding_artifact_digest,
                "reason": "bound to another digest and never observed",
            },
            None,
        )
    # Bound to THIS digest by an earlier pass. Asked again rather than trusted: an observation
    # asserts that this image carries the unit, and a wrong binding must not be built upon.
    try:
        if not holds_commit(state.checkout, candidate.merge_commit):
            return _merge_commit_missing(), None
        carried = carries(state.checkout, candidate.merge_commit, state.release.built_commit)
    except RECOVERABLE as error:
        return {"outcome": UNAVAILABLE, "reason": str(error)}, None
    if not carried:
        return (
            {
                "outcome": REFUSED,
                "reason": "bound to this digest, but the built commit does not carry it",
            },
            None,
        )
    return {"outcome": ALREADY_BOUND, "binding_id": candidate.binding_id}, candidate.binding_id


def _observe_phase(
    candidate: Candidate, binding_id: str | None, state: _Pass
) -> tuple[dict[str, Any], str | None]:
    """File the observation, or find the one already filed. Returns the post-deploy unit's id."""
    production = state.production
    if binding_id is None:
        # Only a dry run reaches here without an id: the binding it would create does not exist.
        return {
            "outcome": OBSERVED,
            "dry_run": True,
            "record": observation_payload("<binding-id>", state.release, production),
        }, None
    try:
        if candidate.observed:
            existing = _existing_observation(binding_id, state)
            if existing is not None:
                return (
                    {"outcome": ALREADY_OBSERVED, "observation_id": existing.get("id")},
                    _post_deploy_unit(existing),
                )
        payload = observation_payload(binding_id, state.release, production)
        if state.dry_run:
            return {"outcome": OBSERVED, "dry_run": True, "record": payload}, None
        recorded = state.system.observe(binding_id, payload)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        return {"outcome": REFUSED, "reason": str(error)}, None
    return {"outcome": OBSERVED, "observation_id": recorded.get("id")}, _post_deploy_unit(recorded)


def _existing_observation(binding_id: str, state: _Pass) -> dict[str, Any] | None:
    for row in state.system.observations(binding_id):
        if row.get("environment") == state.release.environment:
            return row
    return None


def _post_deploy_unit(observation: dict[str, Any]) -> str | None:
    value = observation.get("post_deploy_work_unit_id")
    return None if value is None else str(value)


def _verify_phase(post_deploy_unit_id: str, state: _Pass) -> dict[str, Any]:
    payload = verify_payload(post_deploy_unit_id)
    if state.dry_run or state.verifier is None:
        return {"outcome": DRY_RUN, "post_deploy_work_unit_id": post_deploy_unit_id}
    try:
        result = state.verifier.verify(post_deploy_unit_id, payload)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        return {"outcome": REFUSED, "reason": str(error)}
    outcome = result.get("result")
    return {
        "outcome": outcome
        if outcome in {COMPLETED, REVISION_REQUIRED, AWAITING_REVIEW}
        else REFUSED,
        "post_deploy_work_unit_id": post_deploy_unit_id,
        "state": result.get("state"),
    }


def _outcomes(summary: dict[str, Any]) -> list[str]:
    phases = [summary.get("refusal")] + [
        phase
        for unit in summary["units"]
        for phase in (unit.get("binding"), unit.get("observation"), unit.get("verification"))
    ]
    return [phase["outcome"] for phase in phases if isinstance(phase, dict)]


def has_findings(summary: dict[str, Any]) -> bool:
    return summary["unavailable"] or any(o in FINDING_OUTCOMES for o in _outcomes(summary))


def has_conditions(summary: dict[str, Any]) -> bool:
    return any(o in CONDITION_OUTCOMES for o in _outcomes(summary))
