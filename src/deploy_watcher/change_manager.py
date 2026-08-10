"""The confined change-manager surface. Three paths, checked before the transport.

The watcher lists the deploying-merge changes, appends an observation to one, and reads a
change's observations back so a later pass can re-derive them. It can reach nothing else --
notably not `claim`, `outcome` or `handoff`, which increment 1 closed to proposed sources and
which this program has no business calling even if they were open, and not the decision routes,
which are how a change is finished and are a human's to press.

Bounding it here rather than trusting the caller is the point: `is_allowed_*` runs before any
request is built, so a path this program was never meant to reach never leaves the process.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx

from deploy_watcher.model import ChangeRecord

DEFAULT_BASE_URL = "https://change-mgr.alobar.net"
USER_AGENT = "deploy-watcher/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS = 30.0

# The whole write surface: append an observation to one change. Anchored, with the id shape
# spelled out, so the bound stays a bound -- `…/{id}/outcome` does not match, and neither does a
# prefix, a trailing slash, or a traversal.
_OBSERVE = re.compile(r"^/api/items/[0-9]{1,9}/deploy-observation$")
# The two reads. The listing is scoped to the proposed source by its query string, but the path
# allowlist cannot see a query string, so the scoping is asserted in `deploy_changes` below.
_ITEMS = "/api/items"
_OBSERVATIONS = re.compile(r"^/api/items/[0-9]{1,9}/deploy-observations$")


class ChangeManagerError(Exception):
    """change-manager could not be asked, or refused in a way the pass cannot interpret."""


class RefusedError(ChangeManagerError):
    """change-manager refused the observation. A finding, not a broken tool."""


class ForbiddenEndpointError(ChangeManagerError):
    """This program tried to reach a path it is not allowed to reach."""


def is_allowed_write(path: str) -> bool:
    return _OBSERVE.match(path) is not None


def is_allowed_read(path: str) -> bool:
    return path == _ITEMS or _OBSERVATIONS.match(path) is not None


def _iso(value: datetime) -> str:
    return value.isoformat()


class ChangeManagerClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ChangeManagerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, **params: Any) -> Any:
        if not is_allowed_read(path):
            raise ForbiddenEndpointError(f"the watcher may not read {path}")
        try:
            response = self._client.get(path, params=params or None)
        except httpx.HTTPError as error:
            raise ChangeManagerError(
                f"change-manager is unreachable for GET {path}: {type(error).__name__}"
            ) from error
        if response.status_code >= 400:
            raise ChangeManagerError(f"change-manager rejected GET {path}: {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise ChangeManagerError(
                f"GET {path} answered with something that is not JSON"
            ) from error

    # -- reads -----------------------------------------------------------------------------

    def deploy_changes(self, source: str = "deploy") -> list[ChangeRecord]:
        """Every deploying-merge change change-manager holds.

        The source is NAMED. `GET /api/items` withholds proposed sources when no source is
        given -- that default exists to keep these records away from the 04:00 change-window
        executor -- so a caller that forgets to name it silently receives a clean empty list and
        reports a quiet, wrong "nothing to watch".
        """
        body = self._get(_ITEMS, source=source)
        if not isinstance(body, list):
            raise ChangeManagerError("the item listing is not a list")
        return [_record(item) for item in body if isinstance(item, dict)]

    def observations(self, item_id: int) -> dict[str, Any]:
        """A change's observations, plus the one change-manager reduces them to.

        `current` and `merge_commits_observed` are read from the server rather than computed
        here. Reducing an append-only history to one answer is a rule, and a rule with two
        implementations is the drift this estate has paid for four times over.
        """
        body = self._get(f"/api/items/{item_id}/deploy-observations")
        if not isinstance(body, dict) or not isinstance(body.get("observations"), list):
            raise ChangeManagerError(f"the observations of item {item_id} are unreadable")
        return body

    # -- the one write ---------------------------------------------------------------------

    def observe(self, item_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Append one observation. A 409 is change-manager REFUSING, which is a finding."""
        path = f"/api/items/{item_id}/deploy-observation"
        if not is_allowed_write(path):
            raise ForbiddenEndpointError(f"the watcher may not write {path}")
        try:
            response = self._client.post(path, json=body)
        except httpx.HTTPError as error:
            raise ChangeManagerError(
                f"change-manager is unreachable for POST {path}: {type(error).__name__}"
            ) from error
        if response.status_code == 409:
            raise RefusedError(_detail(response))
        if response.status_code >= 400:
            # A 422 is this program sending a shape the server does not accept -- a defect in
            # the watcher, not in the estate, and it must not be reported as a rollout finding.
            raise ChangeManagerError(
                f"change-manager rejected POST {path}: {response.status_code}: {_detail(response)}"
            )
        try:
            recorded = response.json()
        except ValueError as error:
            raise ChangeManagerError(
                f"POST {path} answered with something that is not JSON"
            ) from error
        if not isinstance(recorded, dict):
            raise ChangeManagerError(f"POST {path} answered with something that is not an object")
        return recorded


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "<non-json>"
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail)[:300] if detail is not None else "<no detail>"


def _record(item: dict[str, Any]) -> ChangeRecord:
    criteria = item.get("acceptance_criteria")
    return ChangeRecord(
        item_id=int(item["id"]),
        identity=str(item.get("identity") or ""),
        target_repository=str(item.get("target_repository") or ""),
        pull_request_number=int(item.get("pull_request_number") or 0),
        acceptance_criteria=tuple(str(c) for c in criteria) if isinstance(criteria, list) else (),
    )


def observation_body(
    *,
    repository: str,
    pull_request_number: int,
    merge_commit_sha: str,
    merged_at: datetime | None,
    workflow_path: str,
    workflow_revision: str | None,
    workflow_attestation: str,
    rollout_job: str | None,
    rollout_job_conclusion: str | None,
    trigger_step: str | None,
    trigger_step_conclusion: str | None,
    concurrent_run_id: int | None,
    run_id: int | None,
    run_attempt: int | None,
    run_url: str | None,
    run_conclusion: str | None,
    run_concluded_at: datetime | None,
    observed_at: datetime,
    actor: str,
) -> dict[str, Any]:
    """The wire body. Facts and coordinates; the server derives the verdict from them."""
    return {
        "target_repository": repository,
        "pull_request_number": pull_request_number,
        "merge_commit_sha": merge_commit_sha,
        "merged_at": _iso(merged_at) if merged_at else None,
        "workflow_path": workflow_path,
        "workflow_revision": workflow_revision,
        "workflow_attestation": workflow_attestation,
        "rollout_job": rollout_job,
        "rollout_job_conclusion": rollout_job_conclusion,
        "trigger_step": trigger_step,
        "trigger_step_conclusion": trigger_step_conclusion,
        "concurrent_run_id": concurrent_run_id,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_url": run_url,
        "run_conclusion": run_conclusion,
        "run_concluded_at": _iso(run_concluded_at) if run_concluded_at else None,
        "observed_at": _iso(observed_at),
        "actor": actor,
    }
