"""Reading GitHub. READ-ONLY -- the client refuses anything but GET, structurally.

Three questions per pass, and no more: what commit does a branch name, when was a commit made, and
how does one commit sit relative to another. The third is what separates "production is old" from
"production is serving something that is not on the branch at all", and those want different
responses from a person.
"""

from __future__ import annotations

from typing import Any

import httpx

API = "https://api.github.com"


class GitHubUnreadable(Exception):
    """GitHub could not answer. Per question, so one unreadable subject costs only itself."""


class ForbiddenMethodError(GitHubUnreadable):
    """The reader was asked for something outside its shape."""


class GitHubReader:
    def __init__(
        self,
        *,
        token: str,
        base_url: str = API,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "revision-watcher/1 (+AlobarQuest/orchestrator)",
            },
            timeout=30.0,
            transport=transport,
        )
        self._commits: dict[str, str | None] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def committed_at(self, repository: str, sha: str) -> str | None:
        """A commit's committer date, cached for the life of the reader.

        Cached because it is asked once per subject while the four brains share one repository and
        one served commit -- uncached, a pass would ask GitHub the same question four times for an
        answer that cannot differ. A method rather than a function reaching into the instance: the
        cache belongs to the reader, and a private access with a suppression comment beside it is
        the shape this repository's review exists to catch.
        """
        key = f"{repository}@{sha}"
        if key not in self._commits:
            row = self.get(f"/repos/{repository}/commits/{sha}")
            date = None
            if isinstance(row, dict):
                commit = row.get("commit")
                committer = commit.get("committer") if isinstance(commit, dict) else None
                value = committer.get("date") if isinstance(committer, dict) else None
                date = value if isinstance(value, str) and value else None
            self._commits[key] = date
        return self._commits[key]

    def get(self, path: str) -> Any:
        if not path.startswith("/"):
            raise ForbiddenMethodError(f"the reader may not fetch {path}")
        try:
            response = self._client.request("GET", path)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The type name only. See `estate.py` for why three families and why not the message.
            raise GitHubUnreadable(type(error).__name__) from error
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GitHubUnreadable(f"github rejected GET {path}: {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise GitHubUnreadable(f"github answered GET {path} with a non-JSON body") from error


def branch_tip(reader: GitHubReader, repository: str, branch: str) -> str:
    row = reader.get(f"/repos/{repository}/branches/{branch}")
    if not isinstance(row, dict):
        raise GitHubUnreadable(f"github has no branch {repository}@{branch}")
    commit = row.get("commit")
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not sha:
        raise GitHubUnreadable(f"github named no commit for {repository}@{branch}")
    return sha


def relation(reader: GitHubReader, repository: str, base: str, head: str) -> str | None:
    """How `head` sits relative to `base`: `behind`, `diverged`, `identical` or `ahead`.

    `None` when GitHub will not compare them -- which happens for a commit that is not in the
    repository at all, and that is itself worth reporting rather than collapsing into `behind`.
    """
    row = reader.get(f"/repos/{repository}/compare/{base}...{head}")
    if not isinstance(row, dict):
        return None
    status = row.get("status")
    return status if isinstance(status, str) and status else None
