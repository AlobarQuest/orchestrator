from fastapi.testclient import TestClient

from orchestrator.api.schemas.reporting import GateLoadMetricResponse, SloReportResponse
from tests.api.test_lifecycle_api import HUMAN


def test_slo_report_route_returns_status_typed_metrics(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/slo-report", headers=HUMAN)

    assert response.status_code == 200
    body = response.json()
    assert "since" in body and "until" in body
    assert body["cost_per_unit"]["status"] == "no_data"
    assert body["cost_per_unit"]["value"] is None
    assert body["improvisation"]["status"] in {"no_data", "computed"}
    assert "status" in body["budget_breach"]
    assert body["gate_load"]["status"] == "no_data"
    assert body["gate_load"]["completed_units"] == 0
    assert set(body["gate_load"]["by_kind"]) == {
        "action_approval",
        "authority_approval",
        "retry_approval",
        "decomposition_decision",
        "human_adjudication",
        "human_transition",
    }


def test_the_response_models_declare_exactly_the_fields_the_route_serves() -> None:
    """A literal pin, not a derived one: `response_model` drops an undeclared key silently, and a
    set built from the code would shrink along with it."""
    assert set(SloReportResponse.model_fields) == {
        "since",
        "until",
        "intake_to_first_work",
        "queue_age",
        "claim_expiry_rate",
        "waiver_frequency",
        "revert_rate",
        "evidence_completeness",
        "cost_per_unit",
        "token_consumption",
        "improvisation",
        "budget_breach",
        "gate_load",
    }
    assert set(GateLoadMetricResponse.model_fields) == {
        "status",
        "value",
        "basis",
        "decisions",
        "completed_units",
        "by_kind",
    }
