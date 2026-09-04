"""Reading GitHub. READ-ONLY -- the client refuses any method but GET, structurally.

**THE POPULATION IS SELF-DESCRIBING, and that is the decision here rather than an implementation
detail.** A repository is watched because it CARRIES a caller workflow, never because it appears on
a list in this file. This estate already has four disagreeing answers to "which repositories are
factory targets" (CLAUDE.md, 2026-08-17), and ADR-0015 ruled that the declaration belongs to the
repository rather than to a list the affected repository cannot see. A fifth hand-maintained list
would be the same defect wearing this lane's name -- and it would go stale in exactly the way this
lane exists to catch.

Reading it from the repositories costs one call per repository per pass. That is the price of not
holding an opinion, and it buys two things a list cannot: a repository that GAINS a caller is
watched from that moment, and one that carries a caller while sitting outside the orchestrator's
dispatch allowlist is visible -- which is precisely the `project-standards` state ADR-0015 had to
reverse by hand.

The population is a SUPERSET of what can actually be dispatched to, because the allowlist lives in
the orchestrator's environment and is not served where this lane can read it. A superset
over-reports and never under-reports, which is the direction to be wrong in.
"""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx

API = "https://api.github.com"
RUNNER_REPOSITORY = "AlobarQuest/factory-runner"
RECOMMENDATION_PATH = "RECOMMENDED_CALLER_PIN"
CALLER_PATH = ".github/workflows/factory-runner-pilot.yml"

# The `uses:` line names the reusable workflow and then its revision. The revision is captured
# loosely on purpose -- a branch name must be RECOGNISED so it can be reported as `unpinned`,
# and a pattern that only matched forty hex characters would report a `@main` caller as having no
# caller at all, which is the GAP-4 state reported as the clean one.
CALLER_PIN = re.compile(r"factory-runner\.yml@([^\s\"']+)")
SHA = re.compile(r"\A[0-9a-f]{40}\Z")

# 2,000 repositories. The account held 75 on 2026-09-04, so the ceiling is generous by more than
# an order of magnitude and exists to bound the loop rather than to express an expectation.
PER_PAGE = 100
MAX_PAGES = 20


class PinWatcherError(Exception):
    """Something could not be read. Never used for a fact about a caller."""


class ForbiddenMethodError(PinWatcherError):
    """The reader attempted something other than a read."""


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
                "User-Agent": "pin-watcher/1 (+AlobarQuest/orchestrator)",
            },
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(self, path: str, **params: Any) -> Any:
        if not path.startswith("/"):
            raise ForbiddenMethodError(f"the reader may not fetch {path}")
        try:
            response = self._client.request("GET", path, params=params or None)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # Three families, not one. A malformed host reaches IDNA encoding and raises
            # UnicodeError -- a ValueError, neither an HTTPError nor an InvalidURL -- and only
            # `DomainError` and `APIAuthenticationError` have handlers upstream, so anything else
            # escaping here ends the pass. Type name only: an exception from a client carries the
            # request, and a diagnostic that prints what it was given is how a value that should
            # not be in a transcript gets into one.
            raise PinWatcherError(
                f"github is unreachable for GET {path}: {type(error).__name__}"
            ) from error
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise PinWatcherError(f"github rejected GET {path}: {response.status_code}")
        return response.json()


def _decode(payload: Any) -> str | None:
    """A contents response's body, or None when the path does not exist."""
    if payload is None:
        return None
    content = payload.get("content")
    if content is None:
        return None
    return base64.b64decode(content).decode("utf-8", errors="replace")


def recommendation(reader: GitHubReader) -> str:
    """The revision factory-runner recommends its callers pin.

    Refuses anything that is not a full forty-character SHA. A recommendation this lane could not
    resolve would make every caller `unresolvable` -- one unreadable file reported as six findings
    about six innocent repositories -- so it fails the pass instead.
    """
    body = _decode(reader.get(f"/repos/{RUNNER_REPOSITORY}/contents/{RECOMMENDATION_PATH}"))
    if body is None:
        raise PinWatcherError(f"{RUNNER_REPOSITORY} does not serve {RECOMMENDATION_PATH}")
    value = body.strip()
    if not SHA.match(value):
        raise PinWatcherError(
            f"{RECOMMENDATION_PATH} is not a forty-character sha (it is {len(value)} characters)"
        )
    return value


def repositories(reader: GitHubReader) -> list[str]:
    """Every non-archived repository the credential owns.

    Paginated to a CEILING rather than to exhaustion: an unbounded page walk cannot terminate
    against a server that keeps answering with a full page, and this program runs unattended.

    Archived repositories are excluded: nothing can be dispatched into one, and a caller frozen in
    an archive is a fact about the archive rather than about the factory.
    """
    names: list[str] = []
    for page in range(1, MAX_PAGES + 1):
        batch = reader.get("/user/repos", per_page=PER_PAGE, page=page, affiliation="owner")
        if not batch:
            return names
        names.extend(repo["full_name"] for repo in batch if not repo.get("archived", False))
    # Reaching the ceiling means there are pages we did not read, so the population is a subset of
    # the real one -- and a subset UNDER-reports, which is the direction that lets a drifted
    # caller go unseen. Raising makes the pass incomplete rather than quietly clean.
    raise PinWatcherError(
        f"the account has more than {MAX_PAGES * PER_PAGE} repositories; the sweep is truncated"
    )


def caller_pin(reader: GitHubReader, repository: str) -> str | None:
    """What this repository's caller names as the runner revision, or None if it has no caller.

    None means "no caller workflow", which is not a finding -- most repositories have none. A
    caller whose file exists but names no reusable workflow raises, because a caller that cannot
    be read is a gap in the measurement rather than a clean answer.
    """
    body = _decode(reader.get(f"/repos/{repository}/contents/{CALLER_PATH}"))
    if body is None:
        return None
    match = CALLER_PIN.search(body)
    if match is None:
        raise PinWatcherError(f"{repository} carries a caller that names no reusable workflow")
    return match.group(1)


def comparison(reader: GitHubReader, recommended: str, pin: str) -> dict[str, Any] | None:
    """GitHub's own answer for where `pin` sits relative to `recommended`.

    Read as `recommended...pin`, so the reported `status` is a statement about the PIN -- `behind`
    means the caller is behind, which is the way round a reader expects. None when the SHA does
    not resolve in factory-runner.
    """
    return reader.get(f"/repos/{RUNNER_REPOSITORY}/compare/{recommended}...{pin}")


def committed_at(reader: GitHubReader, sha: str) -> str | None:
    """The committer date of a revision of factory-runner, or None if it does not resolve."""
    payload = reader.get(f"/repos/{RUNNER_REPOSITORY}/commits/{sha}")
    if payload is None:
        return None
    return payload["commit"]["committer"]["date"]
