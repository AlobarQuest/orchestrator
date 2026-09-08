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
LANDING_ROUTE = "/api/apps/default-branch-landing"

# App Brain's answer vocabulary, mirrored. The source of truth is `LANDING_*` in
# AlobarQuest/brain `src/brains/app/models.py`; the orchestrator mirrors it too, in
# `services/estate_landing.py`. This is a THIRD copy and it is pinned rather than trusted --
# `tests/revision_watcher/test_landing_vocabulary.py` holds it to the orchestrator's, which is
# importable from a test even though this package may not import it. Nothing is invented here: a
# fourth value on this side would be a second copy of a vocabulary that already lives somewhere,
# which this repository has paid for three times.
LANDING_REDEPLOYS = "redeploys"
LANDING_INERT = "inert"
LANDING_UNKNOWN = "unknown"


class UnreadableSubject(Exception):
    """One application could not be asked. Per subject, never fatal to the pass."""


class UnreadablePlatform(Exception):
    """The platform could not be listed, so the coverage question has no answer this pass."""


class LandingReader:
    """How landing on a repository's default branch behaves, per the estate's own record.

    THIS DOES NOT DECIDE WHETHER AN APPLICATION IS BEHIND. It decides what BEING behind MEANS,
    which is a different question and the reason this reader exists at all:

      `redeploys`  merging IS deploying, so a gap means something went wrong -- the rollout
                   failed, the webhook did not fire, the image never built.
      `inert`      merge and deploy are separate tracks, so a gap is the QUEUE rather than a
                   defect. Every merge creates one by design.

    A repository whose answer cannot be obtained is treated as `redeploys` by the caller, which is
    the strict reading: reporting a gap that turns out to be an expected queue costs a look, and
    staying quiet about one that is a failed rollout costs what 2026-09-06 cost.

    The credential is App Brain's READ-ONLY key, which authenticates GET on this route and answers
    401 everywhere else -- measured 2026-09-08 against `/api/apps`, `/api/apps/{slug}` and
    `/api/repositories`.

    SELF-REFERENCE, NAMED: App Brain is itself one of this lane's subjects. It is asked about
    every repository including its own, and a deploy of it makes this reader unavailable for one
    pass -- which reaches the strict reading above rather than a wrong answer.
    """

    def __init__(
        self, *, base_url: str, key: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"x-brain-key": key, "User-Agent": _agent()},
            timeout=15.0,
            transport=transport,
        )
        self._answers: dict[str, str] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LandingReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def landing(self, repository: str) -> str:
        """`redeploys`, `inert`, or `unknown` -- never a raised exception.

        Cached per repository: four of the six subjects share one, so an uncached read would ask
        the same question four times a pass for an answer that cannot differ within it.

        An unreadable App Brain answers `unknown` rather than raising, because this lane can still
        measure whether an application is behind without knowing what that means -- losing the
        interpretation must not lose the measurement.
        """
        if repository not in self._answers:
            answer = LANDING_UNKNOWN
            try:
                response = self._client.get(LANDING_ROUTE, params={"github_repo": repository})
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, httpx.InvalidURL, ValueError):
                body = None
            if isinstance(body, dict):
                value = body.get("landing")
                if value in (LANDING_REDEPLOYS, LANDING_INERT, LANDING_UNKNOWN):
                    answer = value
            self._answers[repository] = answer
        return self._answers[repository]


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
