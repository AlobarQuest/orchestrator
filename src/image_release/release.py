"""One pass: bind what the image carries, observe the deployment once, verify what that minted.

WHAT IS NOT A FINDING. A candidate the built commit does not carry is NOT CARRIED -- it landed
after this image was built, and the next deploy binds it. A candidate bound to ANOTHER digest was
released by an earlier image: bindings are write-once per source tuple, so this image cannot
re-bind it and must not try. Neither makes the pass incomplete.

A CONDITION (exit 2) is production not being what was deployed or not passing what a post-deploy
verification judges: the served revision is not the built commit, the auth posture is one the
orchestrator cannot record, or verification asks for revision or review.

A FINDING (exit 3) is an answer that is missing: a read or write the orchestrator refused or could
not be asked, or a checkout that could not answer.

THE PROBE RUNS ONCE PER PASS, before anything is observed, and every observation in the pass
carries the same summaries. A served revision that is not the built commit refuses EVERY
observation, because each one would assert that the bound digest is what production runs. The
binding is still written: it is a fact about the pushed image, true whether or not it is running.
"""

from __future__ import annotations

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

# Per-phase outcomes.
BOUND = "bound"  # bound by this pass
ALREADY_BOUND = "already_bound"  # bound to this digest by an earlier pass
EARLIER_IMAGE = "released_by_earlier_image"
NOT_CARRIED = "not_carried"
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
    {REVISION_MISMATCH, AUTH_POSTURE_UNRECORDABLE, REVISION_REQUIRED, AWAITING_REVIEW}
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
    production: Production | None
    production_reason: str | None
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
) -> dict[str, Any]:
    """One pass. Never raises for a recoverable failure; always returns what it managed to do."""
    summary: dict[str, Any] = {
        "repository": release.repository,
        "built_commit": release.built_commit,
        "digest": release.digest,
        "dry_run": dry_run,
        "unavailable": False,
        "reason": None,
        "production": None,
        "units": [],
    }
    try:
        if not holds_commit(checkout, release.built_commit):
            raise AncestryError("the checkout does not hold the built commit")
        rows = system.candidates(release.repository)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        summary["unavailable"] = True
        summary["reason"] = str(error)
        return summary
    if not rows:
        return summary

    production: Production | None = None
    production_reason: str | None = None
    try:
        production = measure_production(anonymous, system, release, now=now)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        production_reason = f"production could not be probed: {error}"
    if production is not None:
        summary["production"] = {
            "served_revision": production.served_revision,
            "revision_matches": production.revision_matches,
            "status": production.status_summary["status"],
            "summary": production.status_summary["summary"],
        }

    state = _Pass(
        release=release,
        checkout=checkout,
        system=system,
        verifier=verifier,
        production=production,
        production_reason=production_reason,
        dry_run=dry_run,
    )
    summary["units"] = [_consider(row, state) for row in rows]
    return summary


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
        if candidate.binding_artifact_digest != release.digest:
            return (
                {
                    "outcome": EARLIER_IMAGE,
                    "binding_artifact_digest": candidate.binding_artifact_digest,
                },
                None,
            )
        return {"outcome": ALREADY_BOUND, "binding_id": candidate.binding_id}, candidate.binding_id
    try:
        if not holds_commit(state.checkout, candidate.merge_commit):
            return (
                {"outcome": NOT_CARRIED, "reason": "the merge commit is not in the checkout"},
                None,
            )
        if not carries(state.checkout, candidate.merge_commit, release.built_commit):
            return {"outcome": NOT_CARRIED, "reason": "landed after the built commit"}, None
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


def _observe_phase(
    candidate: Candidate, binding_id: str | None, state: _Pass
) -> tuple[dict[str, Any], str | None]:
    """File the observation, or find the one already filed. Returns the post-deploy unit's id."""
    production = state.production
    if production is None:
        return {"outcome": UNAVAILABLE, "reason": state.production_reason}, None
    if not production.revision_matches:
        return (
            {
                "outcome": REVISION_MISMATCH,
                "served_revision": production.served_revision,
                "reason": "production does not serve the built commit",
            },
            None,
        )
    if production.missing_m2m_status != UNAUTHENTICATED_STATUS:
        # The service records only a 401 here, so this answer cannot be filed -- and it is the
        # one a person most needs to see: an unauthenticated read was not refused.
        return (
            {
                "outcome": AUTH_POSTURE_UNRECORDABLE,
                "reason": f"an unauthenticated read answered {production.missing_m2m_status}",
            },
            None,
        )
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
    return [
        phase["outcome"]
        for unit in summary["units"]
        for phase in (unit.get("binding"), unit.get("observation"), unit.get("verification"))
        if isinstance(phase, dict)
    ]


def has_findings(summary: dict[str, Any]) -> bool:
    return summary["unavailable"] or any(o in FINDING_OUTCOMES for o in _outcomes(summary))


def has_conditions(summary: dict[str, Any]) -> bool:
    return any(o in CONDITION_OUTCOMES for o in _outcomes(summary))
