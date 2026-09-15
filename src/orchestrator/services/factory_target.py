"""Whether a repository declares that the SDS factory may send work into it (ADR-0015).

The declaration is `factory-target.toml` at the repository's root, owned by the repository itself.
Admission reads it here in place of a hand-maintained list of target repositories, which was the
fourth answer the estate kept to one question -- and the only one a repository could not see.

**IT IS READ FROM GITHUB, NEVER FROM A CHECKOUT.** The orchestrator has no checkout, and a working
copy on some machine is not the repository work would land in. The default branch is whatever
GitHub serves for a `contents` request with no `ref`, which is the branch a change lands on.

**THREE ANSWERS, AND ONLY ONE OF THEM ADMITS.** `True` is a repository that declared itself a
target. `False` is one that declared it is not, or said nothing -- participation is declared, never
assumed. `None` is this process not knowing: a transport fault, a refused credential, a status
that is not an answer, or a file that exists and does not read as a declaration. Admission refuses
on both `False` and `None` and names them differently, because the first needs a repository to
change its mind and the second needs somebody to look at why a read failed.

**ABSENCE IS CONFIRMED, NOT BELIEVED.** GitHub answers 404 for a missing file, for a repository
that does not exist, and for one this credential cannot see. So a 404 is followed by one read of
the repository itself, and only a repository that answers lets the absence stand. The verdict is a
refusal either way; the second read keeps the diagnosis true, which is the whole output of a
refusal. `work_carrier/declaration.py` and project-standards' `factory_target.py` read the same
file under the same rules. They cannot be imported from here, so the parse is restated and
`tests/services/test_factory_target.py` holds it to the carrier's.

**NOTHING HERE RAISES.** Only `DomainError` and `APIAuthenticationError` have registered handlers,
so an escaping exception would be a bare 500 from admission -- a gate that has stopped deciding.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

import httpx

from orchestrator.services.github_app import GITHUB_API_URL, GitHubAppTokenError

FILENAME: Final = "factory-target.toml"
TIMEOUT_SECONDS: Final = 10.0
_USER_AGENT: Final = "orchestrator-admission/1 (+AlobarQuest/orchestrator)"

# `owner/repo`, with neither segment a relative path component. The repository comes from an
# authored envelope, so it is judged before any route is composed from it: a name carrying a `/`,
# a `?` or a `..` would otherwise ask GitHub about somewhere else.
_REPOSITORY: Final = re.compile(r"(?!\.\.?/)[A-Za-z0-9._-]+/(?!\.\.?$)[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class TargetDeclaration:
    """`target` is the repository's answer, or `None` when this process could not obtain one.

    `reason` explains the answer and never causes one.
    """

    target: bool | None
    reason: str


class FactoryTargetSource(Protocol):
    """Asked once per admission decision that reaches the target-repository term."""

    def declaration_for(self, repository: str) -> TargetDeclaration: ...


class GitHubFactoryTargetSource:
    """Reads the declaration with the App installation token the orchestrator already mints.

    READ-ONLY by shape: two GET routes, the declaration and the repository beneath it, and nothing
    else. The transport is injectable so a test can see the request rather than patch a function.
    """

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        base_url: str = GITHUB_API_URL,
        timeout_seconds: float = TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def declaration_for(self, repository: str) -> TargetDeclaration:
        if not _REPOSITORY.fullmatch(repository):
            return TargetDeclaration(
                None, f"{repository!r} is not an owner/repo name this process may ask about"
            )
        try:
            token = self._token_provider()
        except GitHubAppTokenError as error:
            return TargetDeclaration(None, f"the App token could not be minted: {error.code}")
        headers = {
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": _USER_AGENT,
        }
        try:
            with httpx.Client(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = client.get(
                    f"/repos/{repository}/contents/{FILENAME}",
                    # The raw bytes rather than the JSON envelope: no base64 hop to get wrong.
                    headers={"Accept": "application/vnd.github.raw"},
                )
                if response.status_code == 404:
                    probe = client.get(
                        f"/repos/{repository}", headers={"Accept": "application/vnd.github+json"}
                    )
                    return _absence(repository, probe.status_code)
                if response.status_code != 200:
                    # Redirects are not followed, so a renamed repository lands here too.
                    return TargetDeclaration(
                        None, f"{repository}'s {FILENAME} answered {response.status_code}"
                    )
                return _parse_declaration(response.text)
        # Three families, because httpx raises three: `HTTPError`, `InvalidURL` (which is not an
        # `HTTPError`), and `UnicodeError` from IDNA encoding a malformed host, which is a
        # `ValueError`. The type name only -- an exception from a client carries the request, and
        # the request carries the token.
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            return TargetDeclaration(
                None, f"github could not be read for {repository}: {type(error).__name__}"
            )


def _absence(repository: str, probe_status: int) -> TargetDeclaration:
    if probe_status == 200:
        return TargetDeclaration(
            False, f"{repository} has no {FILENAME}, so it has not declared itself a target"
        )
    return TargetDeclaration(
        None,
        f"{repository} has no readable {FILENAME} and the repository itself answered "
        f"{probe_status}, so this is not an absence",
    )


def _parse_declaration(text: str) -> TargetDeclaration:
    """Both keys are required, the reason included -- a bare `factory_target = true` is folklore.

    A file that exists and does not read as a declaration is `None`, never `False`: somebody tried
    to declare something, and reading a typo as a refusal would report the wrong defect.
    """
    try:
        data = tomllib.loads(text)
    # `tomllib` parses recursively, so about 500 nested brackets raise `RecursionError`, which is
    # not a `TOMLDecodeError`. The repository authors these bytes, so a hostile file must not 500.
    except (tomllib.TOMLDecodeError, RecursionError) as error:
        return TargetDeclaration(None, f"{FILENAME} is not valid TOML: {type(error).__name__}")
    target = data.get("factory_target")
    if not isinstance(target, bool):
        return TargetDeclaration(
            None, f"{FILENAME} must declare `factory_target` as a bool, got {target!r}"
        )
    reason = data.get("factory_target_reason")
    if not isinstance(reason, str) or not reason.strip():
        return TargetDeclaration(
            None,
            f"{FILENAME} must declare `factory_target_reason` as a non-empty string, "
            f"got {reason!r}",
        )
    return TargetDeclaration(target, f"{FILENAME} declares factory_target = {str(target).lower()}")
