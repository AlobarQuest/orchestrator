"""The runner-brief cross-repo contract, orchestrator side.

`RunnerBrief` in factory-runner is `extra="forbid"`, so a key added here that the
runner does not know about raises at parse time and kills every run at claim --
not a degraded run, a dead one.

WS-6.4.0 pinned the authority envelope across both repos precisely because the
two ends had silently drifted into mutually unsatisfiable fixtures. It left the
BRIEF unpinned, and the brief is the larger surface. This closes that.

`tests/fixtures/runner_brief.json` is byte-identical to factory-runner's copy
under the same name; CONTRACT_SHA256 is the same constant on both sides, so a
one-sided edit is loud. The hash alone would prove only that a file is unchanged,
which says nothing about the code -- so the second test compares the key set the
SERVICE actually builds.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from orchestrator.api.schemas import RunnerBriefResponse
from orchestrator.services.runner_brief import runner_brief
from tests.contract.test_runner_envelope_contract import _approved_ready_unit

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runner_brief.json"
CONTRACT_SHA256 = "1cf3c51678ad411092816c9543cb15d6d45aeb021f6478c4a4c2541f378f66e4"


def golden_brief() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def test_golden_brief_is_unchanged() -> None:
    """A one-sided edit here means factory-runner's copy has silently drifted."""
    canonical = json.dumps(golden_brief(), sort_keys=True, separators=(",", ":"))

    assert hashlib.sha256(canonical.encode()).hexdigest() == CONTRACT_SHA256


def test_the_served_brief_has_exactly_the_contracted_keys(migrated_session: Session) -> None:
    """The derivation assertion: what the service builds, not what a file contains.

    Adding a top-level brief key without updating both repos' fixtures reds here,
    which is the failure this contract exists to catch. Flipping a fixture byte
    would only red the hash test, and that proves nothing about use.
    """
    unit_id = _approved_ready_unit(migrated_session)

    served = runner_brief(migrated_session, unit_id)

    assert set(served) == set(golden_brief()), (
        "the served brief's key set has drifted from the cross-repo fixture. "
        'factory-runner\'s RunnerBrief is extra="forbid": an unknown key here kills every '
        "run at claim. Change BOTH repos' fixtures and CONTRACT_SHA256 together."
    )


def test_the_http_response_model_declares_every_contracted_key(migrated_session: Session) -> None:
    """The service dict is NOT the wire. `RunnerBriefResponse` drops undeclared keys.

    This caught a real defect during WS-P2.12: `runner_brief()` returned the new
    field, every service-level assertion passed, and the HTTP body did not carry
    it — the response model had not been extended. factory-runner parses the BODY,
    so a contract test that stops at the service has a blind spot exactly where
    the consumer reads. Asserting on the declared response fields closes it
    without needing an HTTP client here.
    """
    declared = set(RunnerBriefResponse.model_fields)

    assert declared == set(golden_brief()), (
        "RunnerBriefResponse does not declare the contracted brief keys: "
        f"missing {sorted(set(golden_brief()) - declared)}, "
        f"undeclared-but-served {sorted(declared - set(golden_brief()))}. "
        "A key the service returns but this model omits is silently dropped on the wire."
    )


def test_a_unit_without_enrichment_serves_null_not_an_empty_document(
    migrated_session: Session,
) -> None:
    """NULL means "predates enrichment". An empty document means "enriched, nothing found".

    Collapsing them would erase the distinction the whole nullable column exists to
    keep, and would make an unenriched unit indistinguishable from a class the brains
    hold no content for.
    """
    unit_id = _approved_ready_unit(migrated_session)

    assert runner_brief(migrated_session, unit_id)["enrichment"] is None
