"""The adapter entry point. Operator-invoked; there is no scheduler and no loop."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol

import httpx
import typer

from tracker_projection_adapter.orchestrator_client import OrchestratorClient
from tracker_projection_adapter.projection import (
    Action,
    BindingView,
    UnitView,
    binding_view,
    plan_actions,
    unit_view,
)
from tracker_projection_adapter.tracker import ItemRef, TodoistProjector, TrackerProjector

app = typer.Typer(no_args_is_help=True)


@app.callback()
def _cli() -> None:
    """Outbound tracker projection adapter (WS-P2.7).

    Defining a callback keeps the single ``project`` command named at the CLI — Typer
    otherwise collapses a lone command into the top level, which would make the
    ``tracker-projection-adapter project ...`` invocation used by
    ``scripts/run-tracker-projection.sh`` fail. It also mirrors the reconciliation-runner
    convention and leaves room for a second command (inbound, Increment 2).
    """


class OrchestratorReader(Protocol):
    """The read/write surface `project()` needs from an orchestrator client.

    Structural (like `TrackerProjector`) so tests can pass a hermetic fake without
    subclassing the real `OrchestratorClient`.
    """

    def status_ledger(self) -> list[dict[str, Any]]: ...
    def tracker_bindings(self) -> list[dict[str, Any]]: ...
    def upsert_tracker_binding(
        self,
        *,
        work_unit_id: str,
        tracker_system: str,
        external_item_id: str,
        external_url: str | None,
        projected_state: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...
    def report_tracker_reconciliation(
        self, *, observed_states: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]: ...


class _NullProjector:
    """A dry-run projector: any use is a bug, so it fails loudly.

    Fully annotated (implements the TrackerProjector protocol) so no type suppression is needed.
    """

    def create_item(self, unit: UnitView) -> ItemRef:
        raise AssertionError("dry run must not create tracker items")

    def update_item(self, item_ref: ItemRef, unit: UnitView) -> ItemRef:
        raise AssertionError("dry run must not update tracker items")

    def complete_item(self, item_ref: ItemRef) -> None:
        raise AssertionError("dry run must not complete tracker items")

    def item_completed(self, item_ref: ItemRef) -> bool:
        raise AssertionError("dry run must not read tracker item state")


def _apply(
    client: OrchestratorReader,
    projector: TrackerProjector,
    action: Action,
    binding_by_unit: dict[str, BindingView],
) -> None:
    unit = action.unit
    if action.kind == "create":
        ref = projector.create_item(unit)
    elif action.kind == "update":
        existing = binding_by_unit[unit.work_unit_id]
        ref = projector.update_item(ItemRef(existing.external_item_id, existing.external_url), unit)
    elif action.kind == "complete":
        existing = binding_by_unit[unit.work_unit_id]
        ref = ItemRef(existing.external_item_id, existing.external_url)
        projector.complete_item(ref)
    else:
        return
    client.upsert_tracker_binding(
        work_unit_id=unit.work_unit_id,
        tracker_system="todoist",
        external_item_id=ref.external_item_id,
        external_url=ref.external_url,
        projected_state=unit.unit_state,
        idempotency_key=f"tracker-binding:{unit.work_unit_id}:{unit.unit_state}",
    )


def project(
    client: OrchestratorReader,
    projector: TrackerProjector,
    *,
    dry_run: bool,
) -> dict[str, int]:
    units = [unit_view(row) for row in client.status_ledger()]
    bindings = [binding_view(row) for row in client.tracker_bindings()]
    binding_by_unit = {b.work_unit_id: b for b in bindings}
    counts = {"create": 0, "update": 0, "complete": 0, "skip": 0}
    for action in plan_actions(units, bindings):
        counts[action.kind] += 1
        if dry_run or action.kind == "skip":
            continue
        _apply(client, projector, action, binding_by_unit)
    return counts


def reconcile(
    client: OrchestratorReader,
    projector: TrackerProjector,
    *,
    dry_run: bool,
    pass_id: str,
) -> dict[str, int]:
    """Report each Todoist-bound item's observed completion. The orchestrator owns the divergence
    rule; this only observes and reports (dumb adapter). Reading Todoist is non-mutating, so a dry
    run still reads -- it only withholds the orchestrator report.

    Per-item fail-open with a counted skip. The report is ONE post at the end, so letting a single
    unreadable item propagate would discard every item already read, not just the failing one --
    a pass that dies on item three reports nothing about items one and two.
    """
    observed_states = []
    skipped = 0
    for row in client.tracker_bindings():
        try:
            binding = binding_view(row)
            if binding.tracker_system != "todoist":
                continue
            completed = projector.item_completed(
                ItemRef(binding.external_item_id, binding.external_url)
            )
        except (RuntimeError, KeyError, TypeError, ValueError, httpx.HTTPError):
            skipped += 1
            continue
        observed_states.append(
            {
                "tracker_system": binding.tracker_system,
                "external_item_id": binding.external_item_id,
                "observed_completed": completed,
            }
        )
    if not dry_run and observed_states:
        # Per pass, not a constant. Every pass previously shared the key "tracker-detect-pass";
        # compare the reconciliation runner's f"reconcile-detect:{pass_id}".
        client.report_tracker_reconciliation(
            observed_states=observed_states, idempotency_key=f"tracker-detect:{pass_id}"
        )
    return {"reported": len(observed_states), "skipped": skipped}


@app.command("project")
def project_command(
    todoist_project_id: Annotated[str, typer.Option(help="Target Todoist project id.")],
    orchestrator_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    review_base_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    credential_key_id: Annotated[str, typer.Option()] = "orchestrator-system",
    dry_run: Annotated[bool, typer.Option(help="Print the plan; make no writes.")] = False,
) -> None:
    token = os.environ.get("TRACKER_PROJECTION_TOKEN")
    if not token:
        typer.echo("TRACKER_PROJECTION_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    client = OrchestratorClient(
        base_url=orchestrator_url, credential_key_id=credential_key_id, token=token
    )
    if dry_run:
        counts = project(client, _NullProjector(), dry_run=True)
    else:
        todoist_token = os.environ.get("TODOIST_API_TOKEN")
        if not todoist_token:
            typer.echo("TODOIST_API_TOKEN is required", err=True)
            raise typer.Exit(code=1)
        projector = TodoistProjector(
            token=todoist_token,
            project_id=todoist_project_id,
            review_base_url=review_base_url,
        )
        counts = project(client, projector, dry_run=False)
    typer.echo(json.dumps(counts, indent=2, sort_keys=True))


@app.command("reconcile")
def reconcile_command(
    todoist_project_id: Annotated[str, typer.Option(help="Target Todoist project id.")],
    orchestrator_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    review_base_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    credential_key_id: Annotated[str, typer.Option()] = "orchestrator-system",
    dry_run: Annotated[bool, typer.Option(help="Read + print the plan; send no report.")] = False,
    pass_id: Annotated[str, typer.Option(help="Unique id for this pass.")] = "",
) -> None:
    token = os.environ.get("TRACKER_PROJECTION_TOKEN")
    if not token:
        typer.echo("TRACKER_PROJECTION_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    todoist_token = os.environ.get("TODOIST_API_TOKEN")
    if not todoist_token:
        typer.echo("TODOIST_API_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    resolved_pass_id = pass_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    client = OrchestratorClient(
        base_url=orchestrator_url, credential_key_id=credential_key_id, token=token
    )
    with TodoistProjector(
        token=todoist_token, project_id=todoist_project_id, review_base_url=review_base_url
    ) as projector:
        counts = reconcile(client, projector, dry_run=dry_run, pass_id=resolved_pass_id)
    typer.echo(json.dumps(counts, indent=2, sort_keys=True))
