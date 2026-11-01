"""A repository's required status checks on `main`, read from branch protection.

A factory unit's `automated_check` acceptance is "every check the repository requires passed", and
the repository names those itself. This is the one GitHub read the lane makes; the checks themselves
are observed by the orchestrator, not here.
"""

from __future__ import annotations

import re

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem

GITHUB_API = "https://api.github.com"
USER_AGENT = "rotation-verifier/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS = 30.0
_PATH = re.compile(
    r"^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/branches/main/protection/required_status_checks$"
)


class RequiredChecksUnreadable(RuntimeError):
    """GitHub did not say what the repository requires, so a bump cannot be judged."""


class NoRequiredChecks(RequiredChecksUnreadable):
    """A 404: `main` has no required checks, or the token can't see the repository.

    Still unreadable to a bump, which waits for an answer. An app-change unit goes to a person
    either way, and neither reading can pass it.
    """


def _permits(method: str, path: str) -> bool:
    return method == "GET" and _PATH.fullmatch(path) is not None


def required_checks(
    repository: str,
    *,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, ...]:
    """The `contexts` of `main`'s required status checks, sorted.

    Raises `RequiredChecksUnreadable` on anything but a 200 with a readable body, as
    `NoRequiredChecks` for a 404. An empty tuple means a 200 that lists no contexts, which is a
    repository requiring nothing.
    """
    if base_url_problem(GITHUB_API) is not None:  # pragma: no cover - constant
        raise RequiredChecksUnreadable("the GitHub base URL is unusable")
    path = f"/repos/{repository}/branches/main/protection/required_status_checks"
    try:
        client = ConfinedClient(
            base_url=GITHUB_API,
            user_agent=USER_AGENT,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            permits=_permits,
            refuse=lambda method, refused: RequiredChecksUnreadable(
                f"this client may not {method} {refused}"
            ),
        )
        try:
            response = client.request("GET", path)
        finally:
            client.close()
    except TransportFailure as failure:
        raise RequiredChecksUnreadable(f"unreachable: {failure.error_type}") from None
    if response.status_code == 404:
        raise NoRequiredChecks(f"GitHub answered 404 for {repository}")
    if response.status_code != 200:
        raise RequiredChecksUnreadable(f"GitHub answered {response.status_code} for {repository}")
    try:
        contexts = response.json().get("contexts")
    except ValueError, AttributeError:
        raise RequiredChecksUnreadable("GitHub answered with an unreadable body") from None
    if not isinstance(contexts, list) or not all(isinstance(name, str) for name in contexts):
        raise RequiredChecksUnreadable("GitHub's answer lists no contexts field")
    return tuple(sorted(contexts))
