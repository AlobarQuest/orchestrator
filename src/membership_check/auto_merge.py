"""Can anything arm GitHub auto-merge in a member repository?

Since ADR-0038 nothing in the SDS arms auto-merge: the orchestrator merges directly. A repository
that still allows it lets any writer arm a merge the landing lanes never judged, and a workflow
that arms it is exactly such a writer. So each member repository is read for two things: its
`allow_auto_merge` setting, and every file under `.github/workflows/` on its default branch that
carries an arming marker.

`None` means the read failed, and it is never a pass. `allow_auto_merge` is served only to a
credential with admin rights on the repository, so a missing key is `None`, not `False`.

READ-ONLY by shape, in the manner of `declaration.GitHubDeclarationSource`: three GET routes,
each judged on the path the transport will send, so a name that would compose any other request
is refused before it leaves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import httpx

from membership_check.declaration import API, TIMEOUT_SECONDS, USER_AGENT

WORKFLOWS: Final = ".github/workflows"
JSON_ACCEPT: Final = "application/vnd.github+json"
RAW_ACCEPT: Final = "application/vnd.github.raw"

# not-a-vocabulary: substrings searched for in workflow text, never compared with another
# program's values. The `gh` CLI command (arming only beside a whole `--auto` flag, in any order),
# the GraphQL mutation, and the community action. The CLI spelling appears in this module only
# here: the merge guard's detector door is bound to this assignment.
ARMING_MARKERS: Final = (
    "gh pr merge",
    "enablePullRequestAutoMerge",
    "enable-pull-request-automerge",
)
_CLI_MARKER: Final = ARMING_MARKERS[0]
_SUBSTRING_MARKERS: Final = ARMING_MARKERS[1:]
# A whole `--auto` flag: not `--disable-auto`, not `--autodelete`.
_AUTO_FLAG = re.compile(r"(?<![\w-])--auto(?![\w-])")

_SEGMENT: Final = r"[A-Za-z0-9._-]+"
_ALLOWED = re.compile(
    rf"^/repos/{_SEGMENT}/{_SEGMENT}(?:/contents/{re.escape(WORKFLOWS)}(?:/{_SEGMENT})?)?$"
)


def is_allowed(path: str) -> bool:
    return bool(_ALLOWED.fullmatch(path))


def arming(text: str) -> bool:
    """True when the workflow text arms auto-merge.

    A line arms when it holds the CLI command and a whole `--auto` flag, in any order, or either
    of the other markers. Line endings are normalised and shell line continuations joined first,
    so a command split over lines is read as one.
    """
    for line in text.replace("\r\n", "\n").replace("\\\n", " ").splitlines():
        if _CLI_MARKER in line and _AUTO_FLAG.search(line):
            return True
        if any(marker in line for marker in _SUBSTRING_MARKERS):
            return True
    return False


@dataclass(frozen=True)
class AutoMergeState:
    """A repository's auto-merge setting and arming workflows; `None` means the read failed."""

    allowed: bool | None
    arming_workflows: tuple[str, ...] | None
    detail: str = ""


class _Unread(Exception):
    """A read that did not answer; the message names why, never a response body."""


class GitHubAutoMergeSource:
    """The repository's `allow_auto_merge` and its workflows' text, from GitHub. GET only."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = API,
        timeout_seconds: float = TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubAutoMergeSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def state(self, repository: str) -> AutoMergeState:
        """Total: every failure is returned as `None` with the reason in `detail`.

        The repository is read first and the workflows only after it answered, so a 404 on the
        workflows directory means the directory is absent, not that the repository is.
        """
        try:
            metadata = self._get(f"/repos/{repository}", JSON_ACCEPT)
        except _Unread as error:
            return AutoMergeState(None, None, f"{repository}: {error}")
        setting = _field(metadata, "allow_auto_merge")
        allowed = setting if isinstance(setting, bool) else None
        detail = "" if allowed is not None else f"{repository}: allow_auto_merge was not served"
        try:
            workflows = self._arming_workflows(repository)
        except _Unread as error:
            reasons = "; ".join(filter(None, (detail, f"{repository}: {error}")))
            return AutoMergeState(allowed, None, reasons)
        return AutoMergeState(allowed, workflows, detail)

    def _arming_workflows(self, repository: str) -> tuple[str, ...]:
        listing = self._get(
            f"/repos/{repository}/contents/{WORKFLOWS}", JSON_ACCEPT, absent_ok=True
        )
        if listing.status_code == 404:
            return ()
        try:
            entries = listing.json()
        except ValueError as error:
            raise _Unread(f"{WORKFLOWS} listing is not JSON ({type(error).__name__})") from None
        if not isinstance(entries, list):
            raise _Unread(f"{WORKFLOWS} listing is not a list")
        found: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != "file":
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                raise _Unread(f"{WORKFLOWS} lists a file with no name")
            text = self._get(f"/repos/{repository}/contents/{WORKFLOWS}/{name}", RAW_ACCEPT)
            try:
                body = text.text
            except (UnicodeDecodeError, httpx.HTTPError) as error:
                raise _Unread(f"{WORKFLOWS}/{name} is not text ({type(error).__name__})") from None
            if arming(body):
                found.append(f"{WORKFLOWS}/{name}")
        return tuple(found)

    def _get(self, path: str, accept: str, *, absent_ok: bool = False) -> httpx.Response:
        """One GET on an allowed route, answered 2xx (or 404 when `absent_ok`)."""
        try:
            request = self._client.build_request("GET", path, headers={"Accept": accept})
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            reason = f"{path!r} is not a route this reader may ask ({type(error).__name__})"
            raise _Unread(reason) from None
        # The path the transport will send, and no query: `o/r?x=1` builds `/repos/o/r`, which
        # the pattern admits, with the rest in the query.
        if request.url.query or not is_allowed(request.url.path):
            raise _Unread(f"{path!r} is not a route this reader may ask")
        try:
            response = self._client.send(request)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The type name only: an exception from a client carries the request.
            raise _Unread(f"github is unreachable ({type(error).__name__})") from None
        if absent_ok and response.status_code == 404:
            return response
        if not 200 <= response.status_code < 300:
            raise _Unread(f"GET {request.url.path} answered {response.status_code}")
        return response


def _field(response: httpx.Response, name: str) -> object:
    """`name` from a JSON object body, or `None` when the body is not one."""
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get(name) if isinstance(body, dict) else None
