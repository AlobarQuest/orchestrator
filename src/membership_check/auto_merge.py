"""Can anything arm GitHub auto-merge in a member repository?

Since ADR-0038 nothing in the SDS arms auto-merge: the orchestrator merges directly. A repository
that still allows it lets any writer arm a merge the landing lanes never judged, and a workflow
that arms it is exactly such a writer. So each member repository is read for two things: its
auto-merge setting, and every file under `.github/workflows/` on its default branch that carries
an arming marker.

This is an ARMING detector, and deliberately narrower than "nothing but the SDS merges": it
reports the setting and a workflow that arms auto-merge, not a workflow that merges directly
(the `gh` CLI's merge command without `--auto`, or the REST pull-request merge endpoint). That
wider check is out of scope here.

`None` means the read failed, and it is never a pass.

THE SETTING IS READ OVER GRAPHQL, on every host. GitHub's "Get a repository" page says: "To view
merge-related settings, you must have the contents:read and contents:write permissions", so the
REST `allow_auto_merge` is withheld from the read-only lane App's token. The GraphQL field
`Repository.autoMergeAllowed` names no permission in its documentation; whether a read-only
installation token is served it is proven only by a container pass. Only a literal boolean at
`data.repository.autoMergeAllowed` of a 200 counts: any other status, any `errors` key, a null
repository or any other shape is `None`, and `detail` names the route, the status and each error's
`type`, never a message or a body.

READ-ONLY by shape, in the manner of `declaration.GitHubDeclarationSource`: three GET routes (the
repository, which proves it exists before a 404 on its workflows directory is read as "absent",
the workflows listing and each workflow file) and one GraphQL query, each judged on the request the
transport will send. A GET is held to `is_allowed`; the one POST is held to `is_allowed_query`,
which admits `POST /graphql` carrying exactly `AUTO_MERGE_QUERY` and the variables `owner` and
`name`, each one path segment. Anything else is refused before it leaves.
"""

from __future__ import annotations

import json
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
_NAME = re.compile(_SEGMENT)
GRAPHQL: Final = "/graphql"
AUTO_MERGE_QUERY: Final = (
    "query($owner: String!, $name: String!) "
    "{ repository(owner: $owner, name: $name) { autoMergeAllowed } }"
)
# A GraphQL error's `type` is an upper-case code (`NOT_FOUND`, `FORBIDDEN`); anything else is not
# repeated, since an error object's other fields may echo the request.
_ERROR_TYPE = re.compile(r"[A-Z][A-Z_]{0,63}")


def is_allowed(path: str) -> bool:
    """A GET route this reader may ask."""
    return bool(_ALLOWED.fullmatch(path))


def is_allowed_query(path: str, body: object) -> bool:
    """The one POST this reader may send: `AUTO_MERGE_QUERY` with an owner and a name."""
    if path != GRAPHQL or not isinstance(body, dict) or set(body) != {"query", "variables"}:
        return False
    variables = body["variables"]
    return (
        body["query"] == AUTO_MERGE_QUERY
        and isinstance(variables, dict)
        and set(variables) == {"owner", "name"}
        and all(isinstance(v, str) and _NAME.fullmatch(v) for v in variables.values())
    )


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
    """The repository's auto-merge setting (GraphQL) and its workflows' text (GET), from GitHub."""

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

        The repository is read first and the rest only after it answered, so a 404 on the
        workflows directory means the directory is absent, not that the repository is. Its REST
        answer's `allow_auto_merge` is not read: the setting comes from GraphQL on every host.
        """
        try:
            self._get(f"/repos/{repository}", JSON_ACCEPT)
        except _Unread as error:
            return AutoMergeState(None, None, f"{repository}: {error}")
        try:
            allowed: bool | None = self._auto_merge_allowed(repository)
            detail = ""
        except _Unread as error:
            allowed, detail = None, f"{repository}: autoMergeAllowed was not served ({error})"
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

    def _auto_merge_allowed(self, repository: str) -> bool:
        owner, _, name = repository.partition("/")
        payload = {"query": AUTO_MERGE_QUERY, "variables": {"owner": owner, "name": name}}
        asked = f"POST {GRAPHQL}"
        try:
            request = self._client.build_request(
                "POST", GRAPHQL, json=payload, headers={"Accept": JSON_ACCEPT}
            )
            sent = json.loads(request.content)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise _Unread(f"{asked} could not be built ({type(error).__name__})") from None
        if request.url.query or not is_allowed_query(request.url.path, sent):
            raise _Unread(f"{asked} for {repository!r} is not a query this reader may send")
        response = self._send(request)
        if response.status_code != 200:
            raise _Unread(f"{asked} answered {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise _Unread(f"{asked} answered {response.status_code} with no JSON") from None
        if not isinstance(body, dict):
            raise _Unread(f"{asked} answered {response.status_code} with no JSON object")
        if "errors" in body:
            raise _Unread(
                f"{asked} answered {response.status_code} with errors: "
                f"{_error_types(body['errors'])}"
            )
        data = body.get("data")
        repo = data.get("repository") if isinstance(data, dict) else None
        setting = repo.get("autoMergeAllowed") if isinstance(repo, dict) else None
        if not isinstance(setting, bool):
            raise _Unread(
                f"{asked} answered {response.status_code} without data.repository.autoMergeAllowed"
            )
        return setting

    def _send(self, request: httpx.Request) -> httpx.Response:
        try:
            return self._client.send(request)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The type name only: an exception from a client carries the request.
            raise _Unread(f"github is unreachable ({type(error).__name__})") from None

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
        response = self._send(request)
        if absent_ok and response.status_code == 404:
            return response
        if not 200 <= response.status_code < 300:
            raise _Unread(f"GET {request.url.path} answered {response.status_code}")
        return response


def _error_types(errors: object) -> str:
    """Each GraphQL error's `type`, comma-separated; never its message, path or locations."""
    if not isinstance(errors, list) or not errors:
        return "a malformed errors list"
    types = [e.get("type") if isinstance(e, dict) else None for e in errors]
    return ", ".join(
        t if isinstance(t, str) and _ERROR_TYPE.fullmatch(t) else "an error with no type"
        for t in types
    )
