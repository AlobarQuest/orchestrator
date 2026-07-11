import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

import httpx
import typer

from orchestrator.package_sources import (
    PackageSourceError,
    load_package_intake_payload,
    load_protocol_fixture_intake_payload,
)

app = typer.Typer(no_args_is_help=True)
event_publications_app = typer.Typer(no_args_is_help=True)
app.add_typer(event_publications_app, name="event-publications")
HTTP_TRANSPORT: httpx.BaseTransport | None = None
JsonObject = dict[str, Any]
JsonOption = Annotated[bool, typer.Option("--json", help="Write deterministic JSON.")]
DataOption = Annotated[str, typer.Option("--data", help="Request body as a JSON object.")]
OptionalDataOption = Annotated[
    str | None,
    typer.Option("--data", help="Request body as a JSON object."),
]
ContextOption = Annotated[
    str | None,
    typer.Option("--context", help="Standing context as JSON object or @file."),
]


@dataclass(frozen=True)
class CliSettings:
    api_url: str
    api_token: str
    credential_key_id: str | None

    @classmethod
    def from_environment(cls) -> "CliSettings":
        return cls(
            api_url=os.getenv("ORCHESTRATOR_API_URL", "http://127.0.0.1:8000"),
            api_token=os.getenv("ORCHESTRATOR_API_TOKEN", ""),
            credential_key_id=os.getenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID"),
        )


class CliError(Exception):
    def __init__(self, detail: JsonObject) -> None:
        self.detail = detail
        super().__init__(str(detail.get("message", detail.get("code", "API request failed"))))

    @classmethod
    def from_response(cls, response: httpx.Response) -> "CliError":
        try:
            body = response.json()
        except ValueError:
            body = {}
        detail = body.get("error") if isinstance(body, dict) else None
        if not isinstance(detail, dict):
            detail = {
                "code": "http_error",
                "message": f"API request failed with HTTP {response.status_code}",
            }
        return cls(detail)


def request(method: str, path: str, payload: JsonObject | None = None) -> Any:
    settings = CliSettings.from_environment()
    headers = {"Authorization": f"Bearer {settings.api_token}"}
    if settings.credential_key_id:
        headers["X-Credential-Key-Id"] = settings.credential_key_id
    try:
        with httpx.Client(
            base_url=settings.api_url,
            headers=headers,
            timeout=30.0,
            transport=HTTP_TRANSPORT,
        ) as client:
            response = client.request(method, path, json=payload)
    except httpx.TimeoutException:
        raise CliError({"code": "api_timeout", "message": "API request timed out"}) from None
    except httpx.RequestError:
        raise CliError(
            {"code": "api_unavailable", "message": "API request could not be completed"}
        ) from None
    if response.is_error:
        raise CliError.from_response(response)
    try:
        value = response.json()
    except ValueError:
        raise CliError(
            {"code": "invalid_response", "message": "API returned an invalid response"}
        ) from None
    if isinstance(value, (dict, list)):
        return value
    raise CliError({"code": "invalid_response", "message": "API returned an invalid response"})


def _json_file_or_object(
    value: str,
    *,
    param_hint: str = "--data",
    noun: str = "data",
) -> JsonObject:
    if value.startswith("@"):
        try:
            value = Path(value[1:]).read_text(encoding="utf-8")
        except OSError as error:
            raise typer.BadParameter(
                f"{noun} must be a JSON object",
                param_hint=param_hint,
            ) from error
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise typer.BadParameter(
            f"{noun} must be a JSON object",
            param_hint=param_hint,
        ) from error
    if not isinstance(parsed, dict):
        raise typer.BadParameter(f"{noun} must be a JSON object", param_hint=param_hint)
    return parsed


def _json_object(value: str) -> JsonObject:
    return _json_file_or_object(value, param_hint="--data", noun="data")


def _context_object(value: str | None) -> JsonObject | None:
    if value is None:
        return None
    return _json_file_or_object(value, param_hint="--context", noun="context")


def _emit(value: Any, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return
    if isinstance(value, dict) and {"unit_id", "state", "version", "event_id"} <= value.keys():
        typer.echo(
            f"unit={value['unit_id']} state={value['state']} "
            f"version={value['version']} event={value['event_id']}"
        )
        return
    typer.echo(json.dumps(value, sort_keys=True))


def _run(operation: Callable[[], Any], json_output: bool) -> None:
    try:
        _emit(operation(), json_output)
    except CliError as error:
        payload = {"error": error.detail}
        if json_output:
            typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        else:
            fields = [f"code={error.detail.get('code')}", f"message={error.detail.get('message')}"]
            for name in ("current_version", "recovery"):
                if error.detail.get(name) is not None:
                    fields.append(f"{name}={error.detail[name]}")
            typer.echo(" ".join(fields), err=True)
        raise typer.Exit(1) from error


def _post_data(path: str, data: str, json_output: bool) -> None:
    payload = _json_object(data)
    _run(lambda: request("POST", path, payload), json_output)


def _reason_option(value: str) -> str:
    if not value.strip():
        raise typer.BadParameter("reason must not be empty", param_hint="--reason")
    return value


def _load_intake_payload(path: Path, source_repository: str) -> JsonObject:
    try:
        return load_package_intake_payload(path, source_repository=source_repository)
    except PackageSourceError as error:
        raise CliError({"code": "package_source_error", "message": str(error)}) from error


def _build_intake_payload(path: Path, source_repository: str, idempotency_key: str) -> JsonObject:
    return {
        **_load_intake_payload(path, source_repository),
        "idempotency_key": idempotency_key,
        "expected_version": 0,
    }


def _load_protocol_fixture_payload(path: Path, source_repository: str) -> JsonObject:
    try:
        return load_protocol_fixture_intake_payload(path, source_repository=source_repository)
    except PackageSourceError as error:
        raise CliError({"code": "package_source_error", "message": str(error)}) from error


def _decomposition_decision(
    endpoint: str,
    proposal_id: str,
    idempotency_key: str,
    reason: str,
    json_output: bool,
) -> None:
    _run(
        lambda: request(
            "POST",
            f"/api/v1/decomposition-proposals/{proposal_id}/{endpoint}",
            {
                "idempotency_key": idempotency_key,
                "expected_version": 0,
                "reason": reason,
            },
        ),
        json_output,
    )


@app.command("register-revision")
def register_revision(data: DataOption, json_output: JsonOption = False) -> None:
    _post_data("/api/v1/revisions", data, json_output)


@app.command("register-unit")
def register_unit(revision_id: str, data: DataOption, json_output: JsonOption = False) -> None:
    _post_data(f"/api/v1/revisions/{revision_id}/work-units", data, json_output)


@app.command("intake-package")
def intake_package(
    path: Path,
    source_repository: Annotated[str, typer.Option("--source-repository")],
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    json_output: JsonOption = False,
) -> None:
    def operation() -> Any:
        payload = _build_intake_payload(Path(path), source_repository, idempotency_key)
        return request("POST", "/api/v1/package-intakes", payload)

    _run(operation, json_output)


@app.command("emit-intake-payload")
def emit_intake_payload(
    path: Path,
    source_repository: Annotated[str, typer.Option("--source-repository")],
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write the payload to a file instead of stdout."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    def operation() -> Any:
        payload = _build_intake_payload(Path(path), source_repository, idempotency_key)
        if out is None:
            return payload
        try:
            out.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            raise CliError(
                {"code": "output_write_failed", "message": f"could not write {out}"}
            ) from error
        return {"written": str(out)}

    _run(operation, json_output)


@app.command("intake-protocol-fixture")
def intake_protocol_fixture(
    path: Path,
    source_repository: Annotated[str, typer.Option("--source-repository")],
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    json_output: JsonOption = False,
) -> None:
    def operation() -> Any:
        payload = {
            **_load_protocol_fixture_payload(Path(path), source_repository),
            "idempotency_key": idempotency_key,
            "expected_version": 0,
        }
        return request("POST", "/api/v1/package-intakes", payload)

    _run(operation, json_output)


@app.command("show-package-intake")
def show_package_intake(revision_id: str, json_output: JsonOption = False) -> None:
    _run(lambda: request("GET", f"/api/v1/package-intakes/{revision_id}"), json_output)


@app.command("propose-decomposition")
def propose_decomposition(
    revision_id: str, data: DataOption, json_output: JsonOption = False
) -> None:
    _post_data(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        data,
        json_output,
    )


@app.command("list-decomposition-proposals")
def list_decomposition_proposals(revision_id: str, json_output: JsonOption = False) -> None:
    _run(
        lambda: request(
            "GET",
            f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        ),
        json_output,
    )


@app.command("show-decomposition-proposal")
def show_decomposition_proposal(proposal_id: str, json_output: JsonOption = False) -> None:
    _run(lambda: request("GET", f"/api/v1/decomposition-proposals/{proposal_id}"), json_output)


@app.command()
def readiness(unit_id: str, json_output: JsonOption = False) -> None:
    _run(lambda: request("GET", f"/api/v1/work-units/{unit_id}/readiness"), json_output)


@app.command("status-ledger")
def status_ledger(
    actor_id: Annotated[str | None, typer.Option("--actor-id")] = None,
    work_unit_id: Annotated[str | None, typer.Option("--work-unit-id")] = None,
    state: Annotated[str | None, typer.Option("--state")] = None,
    include_inactive: Annotated[bool, typer.Option("--include-inactive")] = False,
    json_output: JsonOption = False,
) -> None:
    params: dict[str, str] = {}
    if actor_id is not None:
        params["actor_id"] = actor_id
    if work_unit_id is not None:
        params["work_unit_id"] = work_unit_id
    if state is not None:
        params["state"] = state
    if include_inactive:
        params["include_inactive"] = "true"
    query = f"?{urlencode(params)}" if params else ""
    _run(lambda: request("GET", f"/api/v1/status-ledger{query}"), json_output)


@event_publications_app.command("list")
def event_publications_list(
    source_kind: Annotated[str | None, typer.Option("--source-kind")] = None,
    source_id: Annotated[str | None, typer.Option("--source-id")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    json_output: JsonOption = False,
) -> None:
    params: dict[str, str] = {}
    if source_kind is not None:
        params["source_kind"] = source_kind
    if source_id is not None:
        params["source_id"] = source_id
    if status is not None:
        params["status"] = status
    query = f"?{urlencode(params)}" if params else ""
    _run(lambda: request("GET", f"/api/v1/event-publications{query}"), json_output)


@event_publications_app.command("queue")
def event_publications_queue(
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    expected_version: Annotated[int, typer.Option("--expected-version", min=0)],
    source_kind: Annotated[str | None, typer.Option("--source-kind")] = None,
    source_id: Annotated[str | None, typer.Option("--source-id")] = None,
    json_output: JsonOption = False,
) -> None:
    payload = {
        "idempotency_key": idempotency_key,
        "expected_version": expected_version,
        "source_kind": source_kind,
        "source_id": source_id,
    }
    _run(lambda: request("POST", "/api/v1/event-publications/queue", payload), json_output)


@event_publications_app.command("export")
def event_publications_export(
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    expected_version: Annotated[int, typer.Option("--expected-version", min=0)],
    output_path: Annotated[str, typer.Option("--output-path", min=1)],
    json_output: JsonOption = False,
) -> None:
    payload = {
        "idempotency_key": idempotency_key,
        "expected_version": expected_version,
        "output_path": output_path,
    }
    _run(lambda: request("POST", "/api/v1/event-publications/export", payload), json_output)


@event_publications_app.command("retry")
def event_publications_retry(
    publication_id: str,
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    expected_version: Annotated[int, typer.Option("--expected-version", min=0)],
    json_output: JsonOption = False,
) -> None:
    payload = {"idempotency_key": idempotency_key, "expected_version": expected_version}
    _run(
        lambda: request("POST", f"/api/v1/event-publications/{publication_id}/retry", payload),
        json_output,
    )


@app.command()
def claim(
    unit_id: str,
    data: OptionalDataOption = None,
    idempotency_key: Annotated[str | None, typer.Option("--idempotency-key")] = None,
    expected_version: Annotated[int | None, typer.Option("--expected-version", min=0)] = None,
    context: ContextOption = None,
    json_output: JsonOption = False,
) -> None:
    if data is not None:
        _post_data(f"/api/v1/work-units/{unit_id}/claim", data, json_output)
        return
    if idempotency_key is None:
        raise typer.BadParameter(
            "idempotency key is required unless --data is provided",
            param_hint="--idempotency-key",
        )
    if expected_version is None:
        raise typer.BadParameter(
            "expected version is required unless --data is provided",
            param_hint="--expected-version",
        )
    payload = {
        "idempotency_key": idempotency_key,
        "expected_version": expected_version,
        "standing_context": _context_object(context),
    }
    _run(lambda: request("POST", f"/api/v1/work-units/{unit_id}/claim", payload), json_output)


@app.command()
def renew(unit_id: str, data: DataOption, json_output: JsonOption = False) -> None:
    _post_data(f"/api/v1/work-units/{unit_id}/renew", data, json_output)


@app.command("reclaim-expired-claim")
def reclaim_expired_claim(
    unit_id: str,
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    expected_version: Annotated[int, typer.Option("--expected-version", min=0)],
    next_owner_id: Annotated[str, typer.Option("--next-owner-id", min=1)],
    context: ContextOption = None,
    json_output: JsonOption = False,
) -> None:
    _run(
        lambda: request(
            "POST",
            f"/api/v1/work-units/{unit_id}/reclaim-expired-claim",
            {
                "idempotency_key": idempotency_key,
                "expected_version": expected_version,
                "next_owner_id": next_owner_id,
                "standing_context": _context_object(context),
            },
        ),
        json_output,
    )


@app.command()
def preflight(
    unit_id: str,
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    expected_version: Annotated[int, typer.Option("--expected-version", min=0)],
    context: Annotated[str, typer.Option("--context")],
    purpose: Annotated[str, typer.Option("--purpose", min=1)],
    previous_context_snapshot_id: Annotated[
        str | None,
        typer.Option("--previous-context-snapshot-id"),
    ] = None,
    approval_id: Annotated[str | None, typer.Option("--approval-id")] = None,
    attempt: Annotated[int | None, typer.Option("--attempt", min=1)] = None,
    lease_token: Annotated[str | None, typer.Option("--lease-token")] = None,
    json_output: JsonOption = False,
) -> None:
    payload = {
        "idempotency_key": idempotency_key,
        "expected_version": expected_version,
        "standing_context": _context_object(context),
        "previous_context_snapshot_id": previous_context_snapshot_id,
        "approval_id": approval_id,
        "purpose": purpose,
        "attempt": attempt,
        "lease_token": lease_token,
    }
    _run(lambda: request("POST", f"/api/v1/work-units/{unit_id}/preflight", payload), json_output)


@app.command("list-context-snapshots")
def list_context_snapshots(unit_id: str, json_output: JsonOption = False) -> None:
    _run(
        lambda: request("GET", f"/api/v1/work-units/{unit_id}/context-snapshots"),
        json_output,
    )


def _lifecycle(
    command: str,
    unit_id: str,
    idempotency_key: str,
    expected_version: int,
    attempt: int | None,
    lease_token: str | None,
    context: str | None,
    context_snapshot_id: str | None,
    json_output: bool,
) -> None:
    payload = {
        "idempotency_key": idempotency_key,
        "expected_version": expected_version,
        "attempt": attempt,
        "lease_token": lease_token,
    }
    if context is not None:
        payload["standing_context"] = _context_object(context)
        payload["context_snapshot_id"] = context_snapshot_id
    elif context_snapshot_id is not None:
        payload["context_snapshot_id"] = context_snapshot_id
    _run(
        lambda: request("POST", f"/api/v1/work-units/{unit_id}/commands/{command}", payload),
        json_output,
    )


def _lifecycle_options(
    command: str,
    unit_id: str,
    idempotency_key: str,
    expected_version: int,
    attempt: int | None,
    lease_token: str | None,
    context: str | None,
    context_snapshot_id: str | None,
    json_output: bool,
) -> None:
    _lifecycle(
        command,
        unit_id,
        idempotency_key,
        expected_version,
        attempt,
        lease_token,
        context,
        context_snapshot_id,
        json_output,
    )


def _register_lifecycle_command(name: str) -> None:
    def lifecycle_command(
        unit_id: str,
        idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
        expected_version: Annotated[int, typer.Option("--expected-version", min=0)],
        attempt: Annotated[int | None, typer.Option("--attempt", min=1)] = None,
        lease_token: Annotated[str | None, typer.Option("--lease-token")] = None,
        context: ContextOption = None,
        context_snapshot_id: Annotated[
            str | None,
            typer.Option("--context-snapshot-id"),
        ] = None,
        json_output: JsonOption = False,
    ) -> None:
        _lifecycle_options(
            name,
            unit_id,
            idempotency_key,
            expected_version,
            attempt,
            lease_token,
            context,
            context_snapshot_id,
            json_output,
        )

    lifecycle_command.__name__ = name.replace("-", "_")
    app.command(name)(lifecycle_command)


def _register_decomposition_decision_command(name: str, endpoint: str) -> None:
    def decision_command(
        proposal_id: str,
        idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
        reason: Annotated[str, typer.Option("--reason", callback=_reason_option)],
        json_output: JsonOption = False,
    ) -> None:
        _decomposition_decision(endpoint, proposal_id, idempotency_key, reason, json_output)

    decision_command.__name__ = name.replace("-", "_")
    app.command(name)(decision_command)


for _command_name in (
    "ready",
    "start",
    "block",
    "request-approval",
    "approve",
    "submit",
    "verify",
    "review",
    "revision-required",
    "complete",
    "fail",
    "retry",
    "cancel",
):
    _register_lifecycle_command(_command_name)


for _command_name, _endpoint in (
    ("approve-decomposition", "approve"),
    ("reject-decomposition", "reject"),
    ("require-decomposition-revision", "require-revision"),
):
    _register_decomposition_decision_command(_command_name, _endpoint)


@app.command("record-approval")
def record_approval(
    unit_id: str,
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    expected_version: Annotated[int, typer.Option("--expected-version", min=0)],
    subject_type: Annotated[Literal["authority", "action"], typer.Option("--subject-type")],
    reason: Annotated[str, typer.Option("--reason", min=1)],
    context: ContextOption = None,
    json_output: JsonOption = False,
) -> None:
    payload = {
        "idempotency_key": idempotency_key,
        "expected_version": expected_version,
        "subject_type": subject_type,
        "reason": reason,
    }
    if context is not None:
        payload["standing_context"] = _context_object(context)
    _run(
        lambda: request(
            "POST",
            f"/api/v1/work-units/{unit_id}/approvals",
            payload,
        ),
        json_output,
    )


@app.command()
def adjudicate(unit_id: str, data: DataOption, json_output: JsonOption = False) -> None:
    _post_data(f"/api/v1/work-units/{unit_id}/adjudications", data, json_output)


@app.command("authorize-retry")
def authorize_retry(unit_id: str, data: DataOption, json_output: JsonOption = False) -> None:
    _post_data(f"/api/v1/work-units/{unit_id}/retry-authorization", data, json_output)


@app.command("add-dependency")
def add_dependency(unit_id: str, data: DataOption, json_output: JsonOption = False) -> None:
    _post_data(f"/api/v1/work-units/{unit_id}/dependencies", data, json_output)


@app.command("resolve-dependency")
def resolve_dependency(
    dependency_id: str, data: DataOption, json_output: JsonOption = False
) -> None:
    _post_data(f"/api/v1/dependencies/{dependency_id}/resolve", data, json_output)


@app.command("append-evidence")
def append_evidence(unit_id: str, data: DataOption, json_output: JsonOption = False) -> None:
    _post_data(f"/api/v1/work-units/{unit_id}/evidence", data, json_output)


@app.command("list-evidence")
def list_evidence(unit_id: str, json_output: JsonOption = False) -> None:
    _run(lambda: request("GET", f"/api/v1/work-units/{unit_id}/evidence"), json_output)


@app.command()
def history(unit_id: str, json_output: JsonOption = False) -> None:
    _run(lambda: request("GET", f"/api/v1/work-units/{unit_id}/history"), json_output)


@app.command("reconcile-detect")
def reconcile_detect(
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    json_output: JsonOption = False,
) -> None:
    """Run the reconciliation detect-pass. Records conditions; sets no lifecycle state."""
    _run(
        lambda: request(
            "POST",
            "/api/v1/reconciliation/detect",
            {"idempotency_key": idempotency_key, "expected_version": 0},
        ),
        json_output,
    )
