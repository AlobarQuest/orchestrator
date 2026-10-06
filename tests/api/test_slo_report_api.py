from dataclasses import fields

from fastapi.testclient import TestClient

from orchestrator.api.schemas.reporting import (
    GateLoadMetricResponse,
    MetricValueResponse,
    SloReportResponse,
)
from orchestrator.services.reporting.slo_report import GateLoadMetric, MetricValue, SloReport
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
        "human_adjudication",
        "human_transition",
    }


def test_the_response_models_declare_every_field_the_report_carries() -> None:
    """A response model drops a key it does not declare, without an error."""
    assert set(SloReportResponse.model_fields) == {field.name for field in fields(SloReport)}
    assert set(MetricValueResponse.model_fields) == {field.name for field in fields(MetricValue)}
    assert set(GateLoadMetricResponse.model_fields) == {
        field.name for field in fields(GateLoadMetric)
    }
