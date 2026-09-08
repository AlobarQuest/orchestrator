"""Reading the estate's own surfaces: what an application serves, and what the platform runs.

READ-ONLY, and bounded by construction. The application reader performs GET against a URL this
package declares; the platform reader performs GET against one listing path. Neither can write.

**A NON-2xx IS STILL AN ANSWER, and discarding it would report the wrong thing.** A stamped
application whose database is unreachable answers 503 and its body still names the revision it is
serving, which is exactly the fact this lane wants -- the application is current and unwell, and
those are different findings owned by different lanes. So the body is read whatever the status,
and only a request that produced no body at all is unreadable.
"""

from __future__ import annotations

from typing import Any

import httpx

COOLIFY_APPLICATIONS = "/api/v1/applications"


class UnreadableSubject(Exception):
    """One application could not be asked. Per subject, never fatal to the pass."""


class UnreadablePlatform(Exception):
    """The platform could not be listed, so the coverage question has no answer this pass."""


def _agent() -> str:
    return "revision-watcher/1 (+AlobarQuest/orchestrator)"


class ApplicationReader:
    """One GET per subject, against the URL the subject declares."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": _agent()}, timeout=15.0, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ApplicationReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def health(self, url: str) -> Any:
        try:
            response = self._client.get(url)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # Three families rather than one: a malformed host reaches IDNA encoding and raises
            # UnicodeError, a ValueError that is neither an HTTPError nor an InvalidURL. The type
            # name only -- an exception from a client carries the request, and a diagnostic that
            # prints what it was given is how a value that should not be in a log gets into one.
            raise UnreadableSubject(type(error).__name__) from error
        try:
            return response.json()
        except ValueError as error:
            raise UnreadableSubject(f"HTTP {response.status_code}, not JSON") from error


class PlatformReader:
    """The hosting platform's application list, used ONLY to police the declared table.

    Nothing here decides whether a subject is current. If this reader fails the pass still answers
    for every declared subject and reports the coverage question as unmeasured -- the platform
    being unreachable must not cost the measurement that does not depend on it.
    """

    def __init__(
        self, *, base_url: str, token: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "User-Agent": _agent()},
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PlatformReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def applications(self) -> list[dict[str, Any]]:
        try:
            response = self._client.get(COOLIFY_APPLICATIONS)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise UnreadablePlatform(type(error).__name__) from error
        if not isinstance(body, list):
            raise UnreadablePlatform("the application listing is not a list")
        return [row for row in body if isinstance(row, dict)]


def candidate_urls(applications: list[dict[str, Any]]) -> dict[str, str]:
    """Every running application's health URL, keyed by name, as the platform reports it.

    A stopped application is excluded: it serves nothing, so it cannot be behind. The path is the
    platform's own recorded health path, which is right for every application whose platform health
    check is enabled and wrong for one whose is not -- that application is declared in the table,
    so being wrong here costs a probe rather than an answer.
    """
    urls: dict[str, str] = {}
    for row in applications:
        fqdn = str(row.get("fqdn") or "").split(",")[0].strip()
        status = str(row.get("status") or "")
        if not fqdn or status.startswith("exited"):
            continue
        path = str(row.get("health_check_path") or "/api/health")
        urls[str(row.get("name") or fqdn)] = fqdn.rstrip("/") + path
    return urls
