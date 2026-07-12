"""The runner's entry point. Operator-invoked; there is no scheduler and no loop."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from reconciliation_runner.client import ReconciliationClient
from reconciliation_runner.facts import check_observation, deploy_observation, pr_observation

app = typer.Typer(no_args_is_help=True)


def _reality(path: Path) -> dict[str, Any]:
    """Reality is read from a fixture file.

    Live GitHub/Coolify wiring is explicitly OUT of scope for WS-P2.1 -- this workstream proves the
    observation contract and the report-only mandate. Wiring the runner to production is a
    separately-approved decision, as is scheduling it.
    """
    return json.loads(path.read_text())


def run_pass(client: ReconciliationClient, reality_path: Path, pass_id: str) -> dict[str, Any]:
    snapshot = client.in_flight_units()
    known_units = {unit["work_unit_id"] for unit in snapshot["units"]}
    known_bindings = {b["binding_id"] for b in snapshot["release_bindings"]}
    reality = _reality(reality_path)

    bodies: list[dict[str, Any]] = []
    for pull in reality.get("pull_requests", []):
        if pull["work_unit_id"] not in known_units:
            continue
        bodies.append(
            pr_observation(
                work_unit_id=pull["work_unit_id"],
                pr_number=pull["pr_number"],
                head_sha=pull["head_sha"],
                state=pull["state"],
                merged=pull["merged"],
                observed_at=datetime.fromisoformat(pull["updated_at"]),
            )
        )
    for check in reality.get("check_runs", []):
        if check["work_unit_id"] not in known_units:
            continue
        bodies.append(
            check_observation(
                work_unit_id=check["work_unit_id"],
                pr_number=check["pr_number"],
                check_name=check["check_name"],
                head_sha=check["head_sha"],
                conclusion=check["conclusion"],
                observed_at=datetime.fromisoformat(check["completed_at"]),
            )
        )
    for deployment in reality.get("deployments", []):
        if deployment["binding_id"] not in known_bindings:
            continue
        bodies.append(
            deploy_observation(
                binding_id=deployment["binding_id"],
                artifact_digest=deployment["artifact_digest"],
                deploy_status=deployment["deploy_status"],
                environment=deployment["environment"],
                observed_at=datetime.fromisoformat(deployment["completed_at"]),
            )
        )

    for body in bodies:
        client.record_observation(body)
    detected = client.detect(f"reconcile-detect:{pass_id}")
    return {"observations_pushed": len(bodies), **detected}


@app.command("run-pass")
def run_pass_command(
    reality: Annotated[Path, typer.Option(help="Fixture reality file (JSON).")],
    pass_id: Annotated[str, typer.Option(help="Unique id for this pass.")],
    orchestrator_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    credential_key_id: Annotated[str, typer.Option()] = "orchestrator-system",
) -> None:
    token = os.environ.get("RECONCILIATION_RUNNER_TOKEN")
    if not token:
        typer.echo("RECONCILIATION_RUNNER_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    client = ReconciliationClient(
        base_url=orchestrator_url, credential_key_id=credential_key_id, token=token
    )
    summary = run_pass(client, reality, pass_id)
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
