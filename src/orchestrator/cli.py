import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import httpx
import typer

app = typer.Typer(no_args_is_help=True)
HTTP_TRANSPORT: httpx.BaseTransport | None = None
JsonObject = dict[str, Any]
JsonOption = Annotated[bool, typer.Option("--json", help="Write deterministic JSON.")]
DataOption = Annotated[str, typer.Option("--data", help="Request body as a JSON object.")]


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


def _json_object(value: str) -> JsonObject:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise typer.BadParameter("data must be a JSON object", param_hint="--data") from error
    if not isinstance(parsed, dict):
        raise typer.BadParameter("data must be a JSON object", param_hint="--data")
    return parsed


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


@app.command("register-revision")
def register_revision(data: DataOption, json_output: JsonOption = False) -> None:
    _post_data("/api/v1/revisions", data, json_output)


@app.command("register-unit")
def register_unit(revision_id: str, data: DataOption, json_output: JsonOption = False) -> None:
    _post_data(f"/api/v1/revisions/{revision_id}/work-units", data, json_output)


@app.command()
def readiness(unit_id: str, json_output: JsonOption = False) -> None:
    _run(lambda: request("GET", f"/api/v1/work-units/{unit_id}/readiness"), json_output)


@app.command()
def claim(unit_id: str, data: DataOption, json_output: JsonOption = False) -> None:
    _post_data(f"/api/v1/work-units/{unit_id}/claim", data, json_output)


@app.command()
def renew(unit_id: str, data: DataOption, json_output: JsonOption = False) -> None:
    _post_data(f"/api/v1/work-units/{unit_id}/renew", data, json_output)


def _lifecycle(
    command: str,
    unit_id: str,
    idempotency_key: str,
    expected_version: int,
    attempt: int | None,
    lease_token: str | None,
    json_output: bool,
) -> None:
    payload = {
        "idempotency_key": idempotency_key,
        "expected_version": expected_version,
        "attempt": attempt,
        "lease_token": lease_token,
    }
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
    json_output: bool,
) -> None:
    _lifecycle(
        command,
        unit_id,
        idempotency_key,
        expected_version,
        attempt,
        lease_token,
        json_output,
    )


def _register_lifecycle_command(name: str) -> None:
    def lifecycle_command(
        unit_id: str,
        idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
        expected_version: Annotated[int, typer.Option("--expected-version", min=0)],
        attempt: Annotated[int | None, typer.Option("--attempt", min=1)] = None,
        lease_token: Annotated[str | None, typer.Option("--lease-token")] = None,
        json_output: JsonOption = False,
    ) -> None:
        _lifecycle_options(
            name,
            unit_id,
            idempotency_key,
            expected_version,
            attempt,
            lease_token,
            json_output,
        )

    lifecycle_command.__name__ = name.replace("-", "_")
    app.command(name)(lifecycle_command)


for _command_name in (
    "ready",
    "start",
    "block",
    "request-approval",
    "approve",
    "submit",
    "verify",
    "review",
    "complete",
    "fail",
    "retry",
    "cancel",
):
    _register_lifecycle_command(_command_name)


@app.command("record-approval")
def record_approval(
    unit_id: str,
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    expected_version: Annotated[int, typer.Option("--expected-version", min=0)],
    subject_type: Annotated[Literal["authority", "action"], typer.Option("--subject-type")],
    reason: Annotated[str, typer.Option("--reason", min=1)],
    json_output: JsonOption = False,
) -> None:
    _run(
        lambda: request(
            "POST",
            f"/api/v1/work-units/{unit_id}/approvals",
            {
                "idempotency_key": idempotency_key,
                "expected_version": expected_version,
                "subject_type": subject_type,
                "reason": reason,
            },
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
