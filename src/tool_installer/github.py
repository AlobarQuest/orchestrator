"""Reading the fork: what revision `main` is at, and when a revision was committed.

READ-ONLY BY CONSTRUCTION, NOT BY CONVENTION. There is no method parameter to get wrong -- the one
entry point issues `GET` and nothing else, and it refuses a path it did not build, so a value that
happened to be an absolute URL could not send this credential to another host.

WHY GITHUB AT ALL, when `cargo install --git --branch main` would fetch the head anyway. Two
answers this lane cannot get from the machine. Whether there is anything to do: cargo's record
names the revision the installed binary was built from, and only the remote knows whether that is
still the head -- without it the lane would rebuild every night to discover it had nothing to do.
And the record's clock: `observed_at` must be a function of the facts rather than a wall clock,
and a commit's committer date is the fact-derived clock this subject has.

A COMMIT THAT IS NOT THERE IS A REAL STATE, NOT AN ERROR. A branch can be force-pushed away from
the revision a binary was built at, and when that happens the installed revision has no date. The
reader returns `None` for a 404 and raises for everything else, so the caller can fall back to the
head's date -- equally fact-derived -- rather than treating a rewritten history as an unreadable
GitHub.
"""

from __future__ import annotations

from typing import Any

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure

GITHUB_API = "https://api.github.com"


class GitHubReadError(RuntimeError):
    """GitHub could not be read. The lane refuses rather than guessing at a revision."""


class ForbiddenMethodError(GitHubReadError):
    """A path this reader did not build, which could leave the intended host."""


def _permits(method: str, path: str) -> bool:
    return method == "GET" and path.startswith("/") and not path.startswith("//")


def _refuse(_method: str, path: str) -> ForbiddenMethodError:
    return ForbiddenMethodError(f"the reader may not fetch {path}")


class GitHubReader:
    def __init__(
        self,
        *,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        try:
            self._client = ConfinedClient(
                base_url=GITHUB_API,
                user_agent="tool-installer/1 (+AlobarQuest/orchestrator)",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30.0,
                transport=transport,
                permits=_permits,
                refuse=_refuse,
            )
        except TransportFailure as failure:
            # Construction is guarded as well as the request (ADR-0050).
            raise GitHubReadError(
                f"the github base URL is unusable: {failure.error_type}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(self, path: str) -> dict[str, Any] | None:
        """The whole surface. `None` means GitHub said the subject is not there."""
        try:
            response = self._client.request("GET", path)
        except TransportFailure as failure:
            raise GitHubReadError(
                f"github is unreachable for GET {path}: {failure.error_type}"
            ) from None
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            # The status only -- a rejection body echoes the request back.
            raise GitHubReadError(f"github rejected GET {path}: {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise GitHubReadError(f"github answered GET {path} with a non-JSON body") from error
        if not isinstance(body, dict):
            raise GitHubReadError(f"github answered GET {path} with a non-object body")
        return body


def commit(reader: GitHubReader, repository: str, ref: str) -> tuple[str, str] | None:
    """`(sha, committer date)` for a ref, or `None` when the ref is not there.

    The COMMITTER date rather than the author date, matching the activation sweep and the pin
    watcher: an author date travels with a rebased patch and is not a fact about this repository's
    history, where the committer date moves whenever the commit does.
    """
    body = reader.get(f"/repos/{repository}/commits/{ref}")
    if body is None:
        return None
    sha = body.get("sha")
    committed_at = (((body.get("commit") or {}).get("committer")) or {}).get("date")
    if not isinstance(sha, str) or not isinstance(committed_at, str):
        raise GitHubReadError(f"github's answer for {repository}@{ref} names no sha and date")
    return sha, committed_at
