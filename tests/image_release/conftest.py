"""A real git repository and hermetic fakes for the three identities the binder speaks as.

A real repository rather than a fake git runner, for the reason the activation sweep's suite
records: a fake would let these tests agree with a model of git rather than with git.

History built here: `shipped` (a landing the previous image already carried) <- `previous` (the
revision production served before the swap) <- `merge` (a unit's landing) <- `built` (the commit
the image was built from) <- `later` (a landing after the build, which the image does not carry).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from image_release.client import Probe
from image_release.release import Release
from tests.activation_sweep.conftest import git

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
UNIT_ID = "eb7c36f7-4f7e-5d00-9709-779c0c1152a4"
SECOND_UNIT_ID = "1d4c6a3e-0000-4000-8000-000000000002"
BINDING_ID = "5c0f3a1e-0000-4000-8000-0000000000b1"
POST_DEPLOY_UNIT_ID = "7a7a7a7a-0000-4000-8000-0000000000d1"


@dataclass(frozen=True)
class History:
    path: Path
    shipped: str
    previous: str
    merge: str
    built: str
    later: str


def _commit(path: Path, name: str) -> str:
    (path / name).write_text(name)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", name)
    return git(path, "rev-parse", "HEAD").strip()


@pytest.fixture
def history(tmp_path: Path) -> History:
    path = tmp_path / "checkout"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    shipped = _commit(path, "shipped")
    previous = _commit(path, "previous")
    merge = _commit(path, "merge")
    built = _commit(path, "built")
    later = _commit(path, "later")
    return History(
        path=path, shipped=shipped, previous=previous, merge=merge, built=built, later=later
    )


def release_for(built: str, *, previous: str, digest: str = DIGEST) -> Release:
    return Release(
        repository="AlobarQuest/orchestrator",
        built_commit=built,
        previous_commit=previous,
        digest=digest,
        registry="ghcr.io",
        image_repository="alobarquest",
        image_name="orchestrator",
        tag=f"{built[:12]}-amd64",
        workflow_run_url="https://github.com/AlobarQuest/orchestrator/actions/runs/1",
        base_url="https://sds.example.invalid",
        deployer="hq-session",
        environment="production",
    )


def candidate_row(
    merge_commit: str,
    *,
    unit_id: str = UNIT_ID,
    binding_id: str | None = None,
    binding_digest: str | None = None,
    observation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "work_unit_id": unit_id,
        "work_package_revision_id": "11111111-2222-3333-4444-555555555555",
        "package_revision_hash": "sha256:package",
        "unit_key": "orchestrator-ac-001",
        "work_unit_version": 3,
        "source_repository": "AlobarQuest/orchestrator",
        "pr_number": 352,
        "source_commit": "f" * 40,
        "merge_commit": merge_commit,
        "binding_id": binding_id,
        "binding_artifact_digest": binding_digest,
        "observation_id": observation_id,
    }


@dataclass
class FakeSystem:
    rows: list[dict[str, Any]]
    existing: list[dict[str, Any]] = field(default_factory=list)
    configured_status: int = 200
    bound: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    observed: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def candidates(self, repository: str) -> list[dict[str, Any]]:
        return list(self.rows)

    def observations(self, binding_id: str) -> list[dict[str, Any]]:
        return list(self.existing)

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.bound.append((work_unit_id, payload))
        return {"id": BINDING_ID}

    def observe(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.observed.append((binding_id, payload))
        return {"id": "observation-1", "post_deploy_work_unit_id": POST_DEPLOY_UNIT_ID}

    def authenticated_probe(self, path: str) -> Probe:
        return Probe(self.configured_status, None)


@dataclass
class FakeVerifier:
    result: str = "completed"
    verified: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def verify(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.verified.append((work_unit_id, payload))
        return {"unit_id": work_unit_id, "state": self.result, "version": 3, "result": self.result}


SERVED_PATHS = (
    "/health/live",
    "/health/ready",
    "/api/v1/dead-letter",
    "/api/v1/machine-activation-candidates",
    "/api/v1/work-units/{unit_id}/release-artifacts",
    "/api/v1/release-artifacts/{binding_id}/deployment-observations",
    "/api/v1/work-units/{unit_id}/verify",
)


@dataclass
class FakeProduction:
    """Production as the probe sees it. `settles_after` makes the first N probe passes degraded,
    the way a swap looks while it is still settling."""

    revision: str | None
    ready_status: int = 200
    missing_status: int = 401
    paths: tuple[str, ...] = SERVED_PATHS
    settles_after: int = 0
    asked: list[str] = field(default_factory=list)

    @property
    def passes(self) -> int:
        return self.asked.count("/health/live")

    def probe(self, path: str) -> Probe:
        self.asked.append(path)
        if path == "/health/live":
            return Probe(200, {"status": "ok", "revision": self.revision})
        if path == "/health/ready":
            settling = self.passes <= self.settles_after
            return Probe(503 if settling else self.ready_status, {"status": "ok"})
        if path == "/openapi.json":
            return Probe(200, {"paths": {p: {} for p in self.paths}})
        if path == "/api/v1/dead-letter":
            return Probe(self.missing_status, None)
        raise AssertionError(path)
