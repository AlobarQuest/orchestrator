"""The confined GitHub surface for the staleness report. ONE path, and it is a READ.

Ruling B1 (Devon, 2026-09-27): a work record left pending or approved after its Dependabot pull
request was closed or merged by hand is REPORTED, and a person retires it. Reporting it needs the
pull request's state, and GitHub is the only party that holds it -- change-manager stores no pull
request field, and the orchestrator has no egress.

**THE CLAIM IN THE FIRST LINE IS BEHAVIOURAL.** `is_allowed_read` answers True for exactly one
anchored template -- a single pull request -- and there is no write path in this module at all.

**THE CREDENTIAL IS THE CARRY'S, NOT A NEW ONE.** `WORK_CARRIER_GITHUB_TOKEN` is `gh auth token`,
set by `run-work-carrier.sh` for the carry's declaration reads; this program runs in that same
launcher, so it reads the same variable rather than a second name for one credential. It is
Devon's PAT, as every out-of-process lane's GitHub read is, and here it is only ever used to GET.
"""

from __future__ import annotations

import re
from typing import Final

import httpx

from work_carrier.declaration import TOKEN_ENV

API: Final = "https://api.github.com"
USER_AGENT: Final = "work-watcher/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0

# Re-exported so the CLI names the variable it reads without spelling it a second time.
GITHUB_TOKEN_ENV: Final = TOKEN_ENV

OPEN: Final = "open"
CLOSED: Final = "closed"
MERGED: Final = "merged"

_PULL = re.compile(r"^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pulls/[0-9]{1,9}$")


class GitHubError(Exception):
    """GitHub could not be asked, or answered in a way this pass cannot interpret."""


class ForbiddenEndpointError(GitHubError):
    """This program tried to reach a path it is not allowed to reach."""


def is_allowed_read(path: str) -> bool:
    return _PULL.match(path) is not None


class PullRequestReader:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = API,
        timeout_seconds: float = TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self._injected = client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }

    def state(self, repository: str, number: int) -> str:
        """`open`, `closed` (without merging) or `merged`. Anything else is an error, not a guess.

        `merged` is read from `merged_at` and never inferred from `state` alone: a closed pull
        request may or may not have merged, and which one happened is exactly what a person
        retiring the record needs to know.
        """
        path = f"/repos/{repository}/pulls/{number}"
        if not is_allowed_read(path):
            raise ForbiddenEndpointError(f"this program may not GET {path}")
        try:
            client = self._injected or httpx.Client(
                base_url=self._base_url, timeout=self._timeout, headers=self._headers
            )
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise GitHubError(f"the GitHub base URL is unusable: {type(error).__name__}") from None
        try:
            response = client.get(path)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The exception TYPE only: an httpx error carries the request, bearer included.
            raise GitHubError(
                f"GitHub is unreachable for GET {path}: {type(error).__name__}"
            ) from None
        finally:
            if self._injected is None:
                client.close()
        if not 200 <= response.status_code < 300:
            raise GitHubError(f"GitHub answered {response.status_code} for GET {path}")
        try:
            payload = response.json()
        except ValueError as error:
            raise GitHubError("the GitHub response was not JSON") from error
        if not isinstance(payload, dict):
            raise GitHubError("the GitHub response was not an object")
        state = payload.get("state")
        if state == OPEN:
            return OPEN
        if state == CLOSED:
            return MERGED if payload.get("merged_at") else CLOSED
        raise GitHubError(f"GitHub reported an unrecognised state for {repository}#{number}")
