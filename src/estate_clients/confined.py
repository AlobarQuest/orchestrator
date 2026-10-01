"""The one HTTP transport the out-of-process programs use to reach a service of this estate.

Every program here used to build its own `httpx.Client` and repeat the same three guards around
it. This module holds those guards once; each program keeps everything that is its own -- the
paths it may reach, its exception classes, the wording of its errors, and what it does with a
status code -- and passes the first two in.

What lives here and nowhere else:

* **The permit check runs before the transport.** A `ConfinedClient` cannot send a request its
  program does not permit: `permits(method, path)` is asked first, and a refusal raises the
  program's own exception without a byte leaving the process.
* **`TRANSPORT_ERRORS` is all three exception families `httpx` raises for a request that never
  got an answer.** `HTTPError` and `InvalidURL` are the obvious two. The third is `ValueError`:
  IDNA encoding of a malformed host -- a doubled dot, or a DNS label over 63 characters --
  raises `UnicodeError` at request time, and it is neither of the other two. The triggers are
  ordinary environment-variable typos.
* **Construction is guarded as well as the request.** `httpx` refuses some malformed URLs at the
  constructor (a control character) and others only at request time, so a guard on one half is
  not a guard.
* **A failure carries the exception's TYPE and nothing else.** An `httpx` error holds the request,
  and a diagnostic that prints what it was given is how a bearer token reaches a transcript.

This package imports nothing from any program, and no program's wording appears in it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

TRANSPORT_ERRORS = (httpx.HTTPError, httpx.InvalidURL, ValueError)

MAX_DNS_LABEL = 63


class TransportFailure(Exception):
    """A request that got no answer, or a client that could not be built.

    `error_type` is the name of the exception `httpx` raised. The exception itself is never
    chained or kept, for the reason the module docstring gives.
    """

    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


def base_url_problem(base_url: str) -> str | None:
    """Why a base URL would only fail once a request was made, or None if it is usable.

    A doubled dot and an over-long label construct cleanly and raise from IDNA encoding at
    request time, where the failure is indistinguishable from the service being down. Asking
    first lets a program report the operator's typo as the typo.
    """
    parsed = urlsplit(base_url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return "must be https with a host"
    for label in parsed.hostname.split("."):
        if not label or len(label) > MAX_DNS_LABEL:
            return "has a malformed host"
    return None


def error_detail(response: httpx.Response, limit: int = 400) -> str:
    """The service's own explanation of a refusal, bounded. Never the whole body, never headers.

    The orchestrator puts a `DomainError` NESTED under `error`, and the framework's own answer for
    a route it does not serve is a top-level `detail`. Both are read, because a route the deployed
    image does not carry answers the framework's shape.
    """
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:limit]
        if payload.get("detail"):
            return str(payload["detail"])[:limit]
    return f"HTTP {response.status_code}"


def error_code(response: httpx.Response) -> str:
    """The refusal's own code, read from where a `DomainError` puts it: nested under `error`.

    An unreadable body yields the empty string, which no classifier recognises, so an answer a
    program cannot parse stays a finding.
    """
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return ""


class ConfinedClient:
    """An `httpx.Client` that sends only what its program permits."""

    def __init__(
        self,
        *,
        base_url: str,
        headers: Mapping[str, str],
        timeout: float,
        permits: Callable[[str, str], bool],
        refuse: Callable[[str, str], Exception],
        transport: httpx.BaseTransport | None = None,
        follow_redirects: bool = False,
    ) -> None:
        self._permits = permits
        self._refuse = refuse
        try:
            self._client = httpx.Client(
                base_url=base_url.rstrip("/"),
                headers=dict(headers),
                timeout=timeout,
                transport=transport,
                follow_redirects=follow_redirects,
            )
        except TRANSPORT_ERRORS as error:
            raise TransportFailure(type(error).__name__) from None

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send one request, permit first. Raises the program's refusal or `TransportFailure`."""
        if not self._permits(method, path):
            raise self._refuse(method, path)
        try:
            return self._client.request(method, path, **kwargs)
        except TRANSPORT_ERRORS as error:
            raise TransportFailure(type(error).__name__) from None

    def close(self) -> None:
        self._client.close()
