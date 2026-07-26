from datetime import UTC, datetime

from orchestrator.api.schemas import (
    TraceabilityAnchorResponse,
    TraceabilityChainResponse,
    TraceabilityDeploymentHop,
    TraceabilityIntentHop,
    TraceabilityResponse,
    TraceabilityUnitHop,
)


def test_traceability_response_is_json_serializable():
    chain = TraceabilityChainResponse(
        intent=TraceabilityIntentHop(
            revision=1,
            content_hash="sha256:x",
            source_path="intent.md",
            source_commit="a" * 40,
            registered_by="human-1",
        ),
        unit=TraceabilityUnitHop(
            id=__import__("uuid").UUID(int=1),
            unit_key="u-1",
            title="Unit 1",
            state="completed",
            authority_fingerprint="fp",
            authority_approved_by="human-1",
            authority_decision="approved",
        ),
        pr=None,
        commit=[],
        artifact=[],
        deployment=[
            TraceabilityDeploymentHop(
                environment="prod",
                observed_artifact_digest="sha256:d",
                digest_matches=True,
                deployment_ref="ref",
                deployment_url="https://x",
                deployer="deployer-1",
                observed_at=datetime(2026, 7, 25, tzinfo=UTC),
                status_summary={"code": 200},
                probe_summary={},
            )
        ],
        conditions=[],
        observations=[],
    )
    response = TraceabilityResponse(
        anchor=TraceabilityAnchorResponse(matched_on="environment", value="prod"),
        chains=[chain],
    )
    dumped = response.model_dump(mode="json")
    assert dumped["anchor"]["matched_on"] == "environment"
    assert dumped["chains"][0]["deployment"][0]["digest_matches"] is True
