"""The home for an unhandled exception: the nested error shape, one log line and one keyed alert.

Decision 1 and calls 1 and 2 of the platform layer design. Any exception no other handler answers
becomes a 500 whose body is `internal_error` with a fresh request id. The same id goes in this
module's log line and in the alert, so a person can join the three; it never goes in the alert's
key, which is the route template and the exception type, so alert-hub keeps one open alert per
cause however often it recurs.

* **Nothing the exception said leaves this module.** Its text can hold a database URL or a token,
  so only its type's name is used. uvicorn logs the traceback when Starlette's
  `ServerErrorMiddleware` re-raises after this response is sent, so this line carries none.
* **The send can't change the response.** It is a `BackgroundTask` that the middleware runs after
  the body is sent, through `alert_hub_client.fire_once`, which never raises. An alert-hub outage
  loses the alert; the log line still holds the fact.
* **No token, no send.** Unset in every test; the 500 is then logged as `alert_not_configured`.
* **One send after one response** is not a concurrent actor: ADR-0002's rule against loops and
  schedulers in the server holds.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from starlette.background import BackgroundTask

from alert_hub_client import Alert, alert_key, fire_once
from orchestrator.api.error_shape import error_body
from orchestrator.config import Settings

LOG = logging.getLogger("orchestrator.server_errors")
USER_AGENT = "orchestrator/1 (+AlobarQuest/orchestrator)"
ALERT_TIMEOUT_SECONDS = 5.0
KEY_PREFIX = "server-error"
UNMATCHED = "unmatched"
MESSAGE = "the server could not complete this request; quote the request id when reporting it"


def log_level(value: str | None) -> int:
    """The `logging` level a `LOG_LEVEL` value names, case-blind, or `INFO`.

    The host injects `LOG_LEVEL=info` (uvicorn's spelling), and `logging.basicConfig` refuses a
    lowercase name at import, which would crash-loop the process.
    """
    level = logging.getLevelNamesMapping().get((value or "").upper())
    return level if level is not None else logging.INFO


@dataclass(frozen=True)
class ServerAlertConfig:
    token: SecretStr | None
    user_agent: str
    timeout: float
    transport: httpx.BaseTransport | None = None


# What an app built with no config holds: every 500 is logged as `alert_not_configured`.
NO_ALERTS = ServerAlertConfig(token=None, user_agent=USER_AGENT, timeout=ALERT_TIMEOUT_SECONDS)


def server_alert_config(settings: Settings) -> ServerAlertConfig:
    return ServerAlertConfig(
        token=settings.alert_hub_token, user_agent=USER_AGENT, timeout=ALERT_TIMEOUT_SECONDS
    )


def _send(config: ServerAlertConfig, token: str, key: str, alert: Alert, facts: str) -> None:
    if fire_once(
        token,
        key,
        alert,
        user_agent=config.user_agent,
        timeout=config.timeout,
        transport=config.transport,
    ):
        LOG.info("notified %s", facts)


async def server_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Answer an exception no other handler took. Never touches the database session."""
    request_id = uuid.uuid4().hex
    route = request.scope.get("route")
    template = getattr(route, "path", None) or UNMATCHED
    error_type = type(error).__name__
    facts = (
        f"request_id={request_id} method={request.method} route={template} error_type={error_type}"
    )
    LOG.error("server_error %s", facts)
    config: ServerAlertConfig = request.app.state.alert_config
    background = None
    if config.token is None:
        LOG.info("alert_not_configured %s", facts)
    else:
        alert = Alert(
            title=f"Orchestrator 500: {request.method} {template} raised {error_type}",
            message=(
                f"An unhandled {error_type} answered {request.method} {template} with a 500 "
                f"(request {request_id}). The traceback is in the orchestrator's container log "
                "beside that request id. Close this alert once the cause is fixed."
            ),
        )
        key = alert_key(KEY_PREFIX, template, error_type)
        background = BackgroundTask(
            _send, config, config.token.get_secret_value(), key, alert, facts
        )
    return JSONResponse(
        status_code=500,
        content=error_body("internal_error", MESSAGE, request_id=request_id),
        background=background,
    )
