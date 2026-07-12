"""The runner's observation contract.

TWO HALVES THAT MUST SHIP TOGETHER, or the runner wedges on its second pass.

The orchestrator's observation dedup is keyed on
`(source_system, source_reference, normalized_fact_hash)`, and `observed_at` is INSIDE the fact
hash. So:

* `source_reference` is CONTENT-ADDRESSED -- it embeds a digest of the normalized facts. Unchanged
  reality re-pulled next pass therefore produces the identical (reference, fact_hash) and simply
  dedups. Changed reality produces a new reference and a new append-only row.
* `observed_at` comes from UPSTREAM (`PR.updated_at`, `check_run.completed_at`, deployment
  completion time) -- NEVER the runner's pull time. With a wall-clock timestamp, a
  content-addressed reference would produce the same reference but a DIFFERENT fact hash on the
  second unchanged pull -- which is precisely the same-source/different-facts branch, i.e.
  `observation_conflict`. Every pass. Forever.

Get one half without the other and the runner either conflicts or grows unboundedly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

SOURCE_SYSTEM_GITHUB = "github"
SOURCE_SYSTEM_DEPLOY = "deployment_observation"
TRUST_CLASSIFICATION = "delivery_system"


class NormalizedFacts(BaseModel):
    """The runner's ONLY fact shape. Raw provider payloads are never forwarded.

    Not merely tidiness: the orchestrator's secret scanner rejects any fact key containing "log",
    so GitHub's standard `logs_url` field would be refused outright. Forwarding a raw check-run
    payload would fail ingest every single time.
    """

    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    pr_number: int | None = None
    head_sha: str | None = None
    state: str | None = None
    merged: bool | None = None
    check_name: str | None = None
    conclusion: str | None = None
    deploy_status: str | None = None
    artifact_digest: str | None = None

    def as_facts(self) -> dict[str, Any]:
        payload: dict[str, Any] = self.model_dump(exclude_none=True)
        payload["observed_at"] = self.observed_at.isoformat()
        return payload


def fact_digest(facts: dict[str, Any]) -> str:
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _observation(
    *,
    source_system: str,
    source_reference: str,
    subject_type: str,
    subject_reference: str,
    observation_type: str,
    status: str,
    environment: str | None,
    summary: str,
    facts: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        # Content-addressed, so an exact re-delivery dedups on the key and an unchanged re-pull
        # dedups on (source_reference, fact_hash).
        "idempotency_key": f"reconcile:{source_reference}",
        "expected_version": 0,
        "source_system": source_system,
        "source_reference": source_reference,
        "source_url": None,
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": subject_type,
        "subject_reference": subject_reference,
        "environment": environment,
        "observation_type": observation_type,
        "status": status,
        "severity": "info",
        "observed_at": observed_at.isoformat(),
        "summary": summary,
        "facts": facts,
        "payload_digest": None,
    }


def pr_observation(
    *,
    work_unit_id: str,
    pr_number: int,
    head_sha: str,
    state: str,
    merged: bool,
    observed_at: datetime,  # PR.updated_at -- NOT the pull time
) -> dict[str, Any]:
    facts = NormalizedFacts(
        pr_number=pr_number,
        head_sha=head_sha,
        state=state,
        merged=merged,
        observed_at=observed_at,
    ).as_facts()
    return _observation(
        source_system=SOURCE_SYSTEM_GITHUB,
        source_reference=f"pr:{pr_number}@{head_sha}:{fact_digest(facts)}",
        subject_type="work_unit",
        subject_reference=work_unit_id,
        observation_type="github_pr",
        status="observed",
        environment=None,
        summary=f"pull request {pr_number} is {state}",
        facts=facts,
        observed_at=observed_at,
    )


def check_observation(
    *,
    work_unit_id: str,
    pr_number: int,
    check_name: str,
    head_sha: str,
    conclusion: str,
    observed_at: datetime,  # check_run.completed_at -- NOT the pull time
) -> dict[str, Any]:
    facts = NormalizedFacts(
        pr_number=pr_number,
        check_name=check_name,
        head_sha=head_sha,
        conclusion=conclusion,
        observed_at=observed_at,
    ).as_facts()
    return _observation(
        source_system=SOURCE_SYSTEM_GITHUB,
        source_reference=f"check:{check_name}@{head_sha}:{fact_digest(facts)}",
        subject_type="work_unit",
        subject_reference=work_unit_id,
        observation_type="github_check",
        status="passed" if conclusion == "success" else "failed",
        environment=None,
        summary=f"check {check_name} concluded {conclusion}",
        facts=facts,
        observed_at=observed_at,
    )


def deploy_observation(
    *,
    binding_id: str,
    artifact_digest: str,
    deploy_status: str,
    environment: str,
    observed_at: datetime,  # deployment completion time -- NOT the pull time
) -> dict[str, Any]:
    facts = NormalizedFacts(
        deploy_status=deploy_status,
        artifact_digest=artifact_digest,
        observed_at=observed_at,
    ).as_facts()
    return _observation(
        source_system=SOURCE_SYSTEM_DEPLOY,
        source_reference=f"deploy:{binding_id}@{artifact_digest}:{fact_digest(facts)}",
        subject_type="release_binding",
        subject_reference=binding_id,
        observation_type="deployment",
        status="passed" if deploy_status == "succeeded" else "failed",
        environment=environment,
        summary=f"deployment of {artifact_digest} {deploy_status}",
        facts=facts,
        observed_at=observed_at,
    )
