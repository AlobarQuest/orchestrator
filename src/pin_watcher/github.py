"""Reading GitHub. READ-ONLY -- the client refuses any method but GET, structurally.

**THE POPULATION IS SELF-DESCRIBING, and that is the decision here rather than an implementation
detail.** A repository is watched because it CARRIES a caller workflow, never because it appears on
a list in this file. This estate already has four disagreeing answers to "which repositories are
factory targets" (docs/history/claude-md-invariants-archive.md #198, 2026-08-17),
and ADR-0015 ruled that the declaration belongs to the
repository rather than to a list the affected repository cannot see. A fifth hand-maintained list
would be the same defect wearing this lane's name -- and it would go stale in exactly the way this
lane exists to catch.

Reading it from the repositories costs one call per repository per pass. That is the price of not
holding an opinion, and it buys two things a list cannot: a repository that GAINS a caller is
watched from that moment, and one that carries a caller while sitting outside the orchestrator's
dispatch allowlist is visible -- which is precisely the `project-standards` state ADR-0015 had to
reverse by hand.

The population is the member record's `onboarding` and `member` repositories plus the public,
non-archived repositories of each account that owns one (ADR-0059 amendment 4): the accounts come
from the members, never from an owner name in code. It is a SUPERSET of what can actually be
dispatched to, because the grants decide that and this lane only reads callers. A superset
over-reports and never under-reports, which is the direction to be wrong in.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure

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
# A GitHub login: letters, digits and hyphens. An owner goes into a URL path, so anything else in
# the member record is refused rather than sent.
LOGIN = re.compile(r"\A[A-Za-z0-9-]+\Z")

# 2,000 repositories for each account. The account held 75 on 2026-09-04, so the ceiling is
# generous by more than an order of magnitude and exists to bound the loop rather than to express an
# expectation.
PER_PAGE = 100
MAX_PAGES = 20


class PinWatcherError(Exception):
    """Something could not be read. Never used for a fact about a caller."""


class ForbiddenMethodError(PinWatcherError):
    """The reader attempted something other than a read."""


def _permits(method: str, path: str) -> bool:
    return method == "GET" and path.startswith("/")


def _refuse(_method: str, path: str) -> ForbiddenMethodError:
    return ForbiddenMethodError(f"the reader may not fetch {path}")


class GitHubReader:
    def __init__(
        self,
        *,
        token: str,
        base_url: str = API,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        try:
            self._client = ConfinedClient(
                base_url=base_url,
                user_agent="pin-watcher/1 (+AlobarQuest/orchestrator)",
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
            raise PinWatcherError(
                f"the github base URL is unusable: {failure.error_type}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(self, path: str, **params: Any) -> Any:
        try:
            response = self._client.request("GET", path, params=params or None)
        except TransportFailure as failure:
            raise PinWatcherError(
                f"github is unreachable for GET {path}: {failure.error_type}"
            ) from None
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


@dataclass(frozen=True)
class Listing:
    """The population both lanes read, and what the public listing said about the members.

    `public` and `archived` hold names casefolded, and count only entries GitHub returned with
    `private: false`: the Mac's login sees private repositories an installation token cannot, and
    a member the listing did not return publicly takes the same path on both hosts.
    """

    repositories: tuple[str, ...]
    members: tuple[str, ...]
    public: frozenset[str]
    archived: frozenset[str]


def _owner(repository: str) -> str:
    owner, slash, name = repository.partition("/")
    if not (LOGIN.match(owner) and slash and name):
        raise PinWatcherError(f"the member record names {repository!r}, which is not owner/name")
    return owner


def _account_path(reader: GitHubReader, login: str) -> tuple[str, dict[str, str]]:
    """The documented public-repository listing for this account's type."""
    account = reader.get(f"/users/{login}")
    kind = account.get("type") if isinstance(account, dict) else None
    if kind == "User":
        return f"/users/{login}/repos", {"type": "owner"}
    if kind == "Organization":
        return f"/orgs/{login}/repos", {"type": "public"}
    if account is None:
        raise PinWatcherError(f"the account {login} could not be read")
    raise PinWatcherError(f"the account {login} has an unknown type")


def _entry(repository: Any, login: str) -> tuple[str, bool, bool]:
    """One listed repository's name, and whether it is private and archived, or unreadable."""
    if isinstance(repository, dict):
        name, private, archived = (
            repository.get(key) for key in ("full_name", "private", "archived")
        )
        if isinstance(name, str) and isinstance(private, bool) and isinstance(archived, bool):
            return name, private, archived
    raise PinWatcherError(f"the account {login} listed a repository this lane could not read")


def _account(reader: GitHubReader, login: str) -> list[tuple[str, bool, bool]]:
    """Every repository one account's listing returned, paged to a ceiling."""
    path, kind = _account_path(reader, login)
    entries: list[tuple[str, bool, bool]] = []
    for page in range(1, MAX_PAGES + 1):
        batch = reader.get(path, per_page=PER_PAGE, page=page, **kind)
        # A 404 is an account this lane cannot list, never an account with nothing in it: reading
        # it as the end of the pages would sweep the members alone.
        if not isinstance(batch, list):
            raise PinWatcherError(f"the account {login}'s repositories could not be listed")
        if not batch:
            return entries
        entries.extend(_entry(repository, login) for repository in batch)
    # Reaching the ceiling means there are pages we did not read, so the population is a subset of
    # the real one -- and a subset UNDER-reports, which is the direction that lets a drifted
    # caller go unseen. Raising makes the pass incomplete rather than quietly clean.
    raise PinWatcherError(
        f"the account {login} has more than {MAX_PAGES * PER_PAGE} repositories; "
        "the listing is truncated"
    )


def listing(reader: GitHubReader, members: list[str]) -> Listing:
    """The members' repositories plus each owning account's public, non-archived repositories.

    `members` is the member record's `onboarding` and `member` repositories
    (`sds_members.populations.observed`). Names are compared without case and GitHub's spelling is
    kept; a member the public listing did not return keeps the record's spelling. Any failure
    raises, so a caller never sweeps the members alone.

    Paginated to a CEILING for each account rather than to exhaustion: an unbounded page walk
    cannot terminate against a server that keeps answering with a full page, and this program
    runs unattended.

    A non-member is kept only when it is `private: false` and `archived: false`, tested here
    rather than trusted to the endpoint, so the population is the same whichever token reads it.
    Archived non-members are excluded: nothing can be dispatched into one, and a caller frozen in
    an archive is a fact about the archive rather than about the factory.
    """
    if not members:
        raise PinWatcherError("the member record has no onboarding or member repository")
    logins: dict[str, str] = {}
    for repository in members:
        owner = _owner(repository)
        logins.setdefault(owner.casefold(), owner)
    spelling: dict[str, str] = {}
    public: set[str] = set()
    frozen: set[str] = set()
    for login in logins.values():
        for name, private, is_archived in _account(reader, login):
            if private:
                continue
            key = name.casefold()
            spelling.setdefault(key, name)
            (frozen if is_archived else public).add(key)
    union = {key: spelling[key] for key in sorted(public)}
    for repository in members:
        union.setdefault(repository.casefold(), spelling.get(repository.casefold(), repository))
    return Listing(
        repositories=tuple(union.values()),
        members=tuple(union[key] for key in dict.fromkeys(r.casefold() for r in members)),
        public=frozenset(public),
        archived=frozenset(frozen),
    )


def repositories(reader: GitHubReader, members: list[str]) -> list[str]:
    """`listing`'s union: the members and their accounts' public, non-archived repositories."""
    return list(listing(reader, members).repositories)


def archived(reader: GitHubReader, repository: str) -> bool:
    """Whether a member repository is archived, read from the repository itself.

    For a member the public listing did not return. A repository this lane cannot read raises:
    an unread member is a gap, never a silent drop.
    """
    payload = reader.get(f"/repos/{repository}")
    value = payload.get("archived") if isinstance(payload, dict) else None
    if not isinstance(value, bool):
        raise PinWatcherError(f"{repository} could not be read")
    return value


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
