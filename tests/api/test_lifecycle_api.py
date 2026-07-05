from fastapi.testclient import TestClient

from orchestrator.main import app


def test_api_is_versioned() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/api/v1/work-units/{unit_id}/readiness" in paths
    assert "/api/v1/work-units/{unit_id}/commands/{command}" in paths


def test_creation_routes_declare_201_and_response_schemas() -> None:
    document = TestClient(app).get("/openapi.json").json()
    paths = document["paths"]

    revision = paths["/api/v1/revisions"]["post"]
    unit = paths["/api/v1/revisions/{revision_id}/work-units"]["post"]

    assert set(revision["responses"]) >= {"201", "401", "403"}
    assert set(unit["responses"]) >= {"201", "401", "403"}
    assert revision["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
    assert unit["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]


def test_every_api_success_response_has_an_explicit_schema() -> None:
    document = TestClient(app).get("/openapi.json").json()

    for path, operations in document["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for operation in operations.values():
            success = next(
                response
                for status, response in operation["responses"].items()
                if status.startswith("2")
            )
            assert success["content"]["application/json"]["schema"]
