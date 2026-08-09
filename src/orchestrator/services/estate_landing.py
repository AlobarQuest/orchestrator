"""What the estate records about landing a commit on a repository's default branch.

WS-P2.28. A package declares what its work reaches (ADR-0009); this is the estate's own answer
to the one question that declaration can be wrong about in a way nobody notices: **when a commit
lands on this repository's default branch, does something already serving change?**

App Brain is the authority. It is asked, never second-guessed, and its answer is not derived here
from anything else -- R8 holds in both directions. A consumer that computed the answer would be a
consumer asserting a fact about itself, and the specific way that goes wrong is already measured:
reading a repository's CI gets **3 of 10 wrong**, because there are three independent mechanisms
that can advance a running service on a landing (a workflow step, a repository webhook, and the
hosting platform's own git integration) and no single source shows all three.

**Nothing here raises.** `landing_for` is total: a timeout, a refusal, a malformed body and an
absent configuration all come back as an answer that says it has none. Only `DomainError` and
`APIAuthenticationError` have registered handlers, so an escaping HTTP exception would surface as
a bare 500 from the admission path -- and an admission gate that 500s is one that has stopped
deciding. Returning is what keeps the caller able to fail closed rather than fail over.

The credential is READ-ONLY (WS-P2.29 provisioned one; before that only a full-access key existed).
App Brain scopes it to two read paths and refuses it everywhere else, so this module cannot write
to the estate's knowledge even by mistake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx

# App Brain's answer vocabulary, mirrored EXACTLY. This is a cross-boundary vocabulary and it is
# registered as one: the source of truth is `LANDING_*` in AlobarQuest/brain
# `src/brains/app/models.py`, served by GET /api/apps/default-branch-landing. Nothing is added to
# it here -- a fourth value invented on this side would be a second copy of a vocabulary that
# already lives somewhere, which this repository has paid for three times.
LANDING_REDEPLOYS: Final = "redeploys"
LANDING_INERT: Final = "inert"
LANDING_UNKNOWN: Final = "unknown"

# Why THIS process has no answer -- distinct from App Brain answering `unknown`, which is the
# estate saying it has not assessed the repository. These two need different people: one sets an
# environment variable, the other looks at why a service is refusing.
SOURCE_UNCONFIGURED: Final = "source_unconfigured"
SOURCE_UNREADABLE: Final = "source_unreadable"

_ROUTE: Final = "/api/apps/default-branch-landing"
_USER_AGENT: Final = "orchestrator-reach-check/1 (+AlobarQuest/orchestrator)"


@dataclass(frozen=True)
class EstateAnswer:
    """The estate's answer, or the reason this process does not have one.

    `landing` carries App Brain's value when an answer was obtained and `None` when it was not.
    The two cases are deliberately one type: every caller must handle both, and a caller that
    forgets gets `None`, which no predicate here treats as permission.

    `reason` is App Brain's own reason verbatim when `landing` is set (today `no_app_record` or
    `not_assessed`, both under `unknown`), and one of the `SOURCE_*` constants above when it is
    not. It is never load-bearing on its own -- it explains a refusal, it does not cause one.
    """

    landing: str | None
    reason: str | None = None


class EstateLandingSource(Protocol):
    """Asked once per admission decision that needs it."""

    def landing_for(self, github_repo: str) -> EstateAnswer: ...


class HttpEstateLandingSource:
    """Reads App Brain over HTTP, and converts every failure into an answer rather than a raise.

    Injected at the route, the same way the workflow client is, so the admission path can be
    exercised without a network. It goes through an `httpx.Client` with an INJECTABLE transport
    rather than the module-level `httpx.get` used elsewhere in this estate, and the difference is
    not stylistic: a module-level call can only be tested by patching, and `intent-packages`'
    brain client shows what that costs -- its one outage test passes for an unrelated reason and
    has never once exercised the path it names. Here the request itself is observable, which is
    what lets a test prove the read-only credential is the one on the wire.
    """

    def __init__(
        self,
        *,
        base_url: str,
        read_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._read_key = read_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def landing_for(self, github_repo: str) -> EstateAnswer:
        if not self._base_url or not self._read_key:
            return EstateAnswer(None, SOURCE_UNCONFIGURED)
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout_seconds) as client:
                response = client.get(
                    f"{self._base_url}{_ROUTE}",
                    params={"github_repo": github_repo},
                    headers={"x-brain-key": self._read_key, "user-agent": _USER_AGENT},
                )
        # `InvalidURL` is NOT an `HTTPError` -- it derives straight from `Exception` -- so catching
        # the latter alone left the totality this module's docstring promises untrue. The input
        # that reaches it is not exotic: a TRAILING NEWLINE on the configured base URL, which
        # `.rstrip("/")` does not remove, and which is the ordinary way an environment variable
        # gets malformed. Escaping here is an unhandled 500 from every caller, because only
        # `DomainError` and `APIAuthenticationError` have registered handlers.
        except (httpx.HTTPError, httpx.InvalidURL):
            return EstateAnswer(None, SOURCE_UNREADABLE)
        if response.status_code != 200:
            return EstateAnswer(None, SOURCE_UNREADABLE)
        try:
            body = response.json()
        except ValueError:
            return EstateAnswer(None, SOURCE_UNREADABLE)
        return _answer_from_body(body)


def _answer_from_body(body: Any) -> EstateAnswer:
    """App Brain's response body as an answer, or no answer when it does not read as one.

    Defensive about a shape this repository does not own. A body whose `landing` is missing, is
    not a string, or is a value this build does not recognise reads as NO ANSWER -- never as the
    nearest recognisable thing. The likeliest way to meet an unrecognised value is a newer
    authoring side naming a state this build predates, which is exactly the case where guessing
    turns "something I have never heard of" into "nothing happens".
    """
    if not isinstance(body, dict):
        return EstateAnswer(None, SOURCE_UNREADABLE)
    landing = body.get("landing")
    if landing not in (LANDING_REDEPLOYS, LANDING_INERT, LANDING_UNKNOWN):
        return EstateAnswer(None, SOURCE_UNREADABLE)
    reason = body.get("reason")
    return EstateAnswer(landing, reason if isinstance(reason, str) else None)
