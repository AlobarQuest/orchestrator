"""The adapter shares no import path with the orchestrator and calls no canonical surface.

The import confinement this module used to assert -- nothing from the orchestrator, the
orchestrator nothing from here, and the dependency allowlist -- is now a row of
`tests/architecture/test_out_of_process_isolation.py`. This module still runs that row, once, for
the reason given on `test_the_adapter_satisfies_its_isolation_row`.
"""

import httpx
import pytest

from tests.architecture.test_out_of_process_isolation import SRC, TABLE, violations
from tracker_projection_adapter.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    _is_allowed_write,
)


def test_write_surface_allows_only_the_two_report_only_endpoints() -> None:
    assert _is_allowed_write(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/tracker-binding"
    )
    assert _is_allowed_write("/api/v1/reconciliation/tracker-detect")
    assert not _is_allowed_write(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/commands/ready"
    )
    assert not _is_allowed_write("/api/v1/work-units/00000000-0000-0000-0000-000000000000/evidence")
    assert not _is_allowed_write("/api/v1/observations")
    assert not _is_allowed_write(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/adjudications"
    )


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    client = OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-system",
        token="t",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/work-units/00000000-0000-0000-0000-000000000000/commands/ready", {})
    assert seen == []


def test_the_adapter_satisfies_its_isolation_row() -> None:
    """Wave 2 exit clause 3 is attested by `scripts/exit_probe.py tracker-is-a-projection`, which
    runs `pytest tests/tracker_projection_adapter` and records that as proof the adapter imports
    nothing from the orchestrator. So the import ban has to be reachable from THIS directory, or
    the probe attests a property it never executes -- which it did while the ban lived only in
    `tests/architecture/`. The row itself is the table's; this is the same check, run here.
    """
    assert violations(SRC, TABLE, "tracker_projection_adapter") == []
