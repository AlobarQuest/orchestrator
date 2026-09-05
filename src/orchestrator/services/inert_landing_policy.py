"""What the estate's policy declares about landing on a default branch where landing changes
nothing already serving. ADR-0038 part 1, read by ADR-0038 part 2.

**change-manager is the authority and it is asked, never second-guessed.** The rule this reads was
a GitHub Actions workflow copied across six repositories until 2026-08-31; ADR-0038 moved it to the
one place its three readers can ask rather than transcribe, and this is the orchestrator's ask.
Nothing here derives the rule from anything else, and nothing here writes.

## Why a second route on one document, and not a second document

The policy object is served at two byte-identical paths. This module names the second, and the
reason is recorded in ADR-0038 rather than being a preference: the first path is spelled with a
bare token this repository's architecture guards forbid in every runtime string literal under
`src/orchestrator`, including docstrings, and a URL owned by another service cannot be reworded
from this side. One holder, one builder, two projections, pinned by a test on the far side.

## Nothing raises

`inert_landing_rules` is total: a timeout, a refusal, a malformed body, a body whose block is
absent and an unconfigured deployment all come back as an answer that says it has none. Only
`DomainError` and `APIAuthenticationError` have registered handlers, so an escaping HTTP exception
would surface as a bare 500 from the admission path -- and a gate that 500s is one that has
stopped deciding. Returning is what keeps the caller able to fail closed rather than fail over.

## Every absent field refuses, and none of them defaults

The sibling that reads the DEPLOYING lane's conditions treats one absent key as a statement --
`excluded_ecosystems` missing means "this version decides by version delta instead", because
versions predating that field really did decide that way and a reader must judge a record by the
rule it was approved under. **There is no such older rule here.** This block arrived whole, in one
version, so an absent field is a document this build cannot read rather than an older document it
can. Defaulting any of them would invent a permission nobody granted: the repositories, the
permitted authors and the excluded ecosystems each BOUND what may land, and a default for a bound
is the fail-open shape this repository keeps finding.

An EMPTY list is a different matter and parses: a block declaring no repositories permits nothing,
and one declaring no excluded ecosystems excludes nothing. Both are things a person can decide.

## The version is the DOCUMENT's, not the block's

One `version` covers both populations, so a revision that moves only the deploying half re-stamps
what a landing here is attributed to. That follows from there being one holder and it is recorded
in ADR-0038 as a consequence rather than a defect -- the estate's ledger reads this number back out
of the landing commit, and a reader would otherwise assume it tracks the rule it names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx

# Why THIS process has no answer -- distinct from the document answering with a block this build
# cannot read, which is a statement about the estate. These need different people: one sets an
# environment variable, the other looks at why a service is refusing.
SOURCE_UNCONFIGURED: Final = "source_unconfigured"
SOURCE_UNREADABLE: Final = "source_unreadable"

# The document loaded and served no block naming this population at all. Its own reason, because
# its cause is different from both of the above and so is who fixes it: every policy version
# before the sixth omits the block, so this is what a reader meets while running ahead of the
# party holding the policy, and after any rollback of it. Refusing is the only safe reading -- a
# version that said nothing about this population did not permit landing into it.
RULES_UNDECLARED: Final = "rules_undeclared"

# The key the policy document carries the block under. A cross-boundary name, mirrored exactly
# from change-manager's `DeployPolicy`; a second spelling invented on this side would read as
# "the block is absent" for a document that carries it, which is the failure this whole module
# fails closed toward and would therefore never announce itself.
_BLOCK: Final = "inert_landing"

_ROUTE: Final = "/api/landing-policy"
_USER_AGENT: Final = "orchestrator-inert-landing-policy/1 (+AlobarQuest/orchestrator)"


@dataclass(frozen=True)
class InertLandingRules:
    """The declared population, and the conditions on landing into it.

    **This process holds no copy of any of it.** Every value arrives with the version that
    declared it, so the rule and the number a landing is attributed to are read together and
    cannot disagree across two calls.

    `repositories` is folded to lower case at the parse, because GitHub names are
    case-insensitive and the admission cascade folds what it asks. `permitted_authors` and
    `excluded_ecosystems` are stored verbatim and folded at the comparison instead: they are not
    identity keys of this repository's own, and folding at one end only is the mistake the
    sibling's exclusion comment records.
    """

    version: int
    repositories: frozenset[str]
    permitted_authors: frozenset[str]
    excluded_ecosystems: frozenset[str]
    require_head_current_with_base: bool
    # ADR-0041. The permitted authors whose pull requests are NOT ecosystem-scoped -- an upstream
    # sync is somebody else's release wholesale, not a bump, so "which package ecosystem" is the
    # wrong question rather than one it failed to answer.
    #
    # **THE ONLY OPTIONAL FIELD IN THIS BLOCK, and the asymmetry is the argument.** Every other
    # field BOUNDS what may land, so an absent one is a permission the document never granted and
    # `_string_set` refuses it. This one EXEMPTS: absent means the empty set, means no author is
    # exempt, means every subject must produce a readable ecosystem -- which is exactly the
    # behaviour before this field existed. An absence here cannot open a hole, so refusing on it
    # would strand the live document for no gain.
    non_ecosystem_authors: frozenset[str] = frozenset()

    def declares(self, repository: str) -> bool:
        """Is this repository one a person pinned into the population?"""
        return repository.lower() in self.repositories

    def permits_author(self, login: str) -> bool:
        """Is this the account whose pull requests the block exists for?

        **A DECLARED CONDITION AND NOT AN ASSUMPTION**, and it sits on the PERMITTING side, so a
        value that does not match under-permits and the lane goes quiet -- the direction nobody
        notices. The identity has two spellings: the REST pull-request object answers
        `dependabot[bot]` while the command-line client answers `app/dependabot` for the same
        pull request. What is declared is the first, because that is what the workflow this
        replaces keyed on and what this lane reads.
        """
        return login.lower() in {name.lower() for name in self.permitted_authors}

    def ecosystem_scoped(self, login: str) -> bool:
        """Does the ecosystem bound apply to this author's pull requests at all? ADR-0041.

        **DECLARED, never inferred from the branch.** Reading it from a `dependabot/` prefix is one
        line and needs no policy version, and it lets ANY branch name switch the bound off -- the
        bound defeated by the thing it constrains naming itself differently, which is ADR-0009's
        R8 and the fail-open shape this repository keeps finding.

        Folded on both sides, like `permits_author`: folding at one end only is the mistake the
        sibling's exclusion comment records.

        The default direction is the safety. An author nobody declared is scoped, so a policy
        version that forgets to name a new one gets today's refusal rather than a hole.
        """
        return login.lower() not in {name.lower() for name in self.non_ecosystem_authors}


@dataclass(frozen=True)
class InertLandingAnswer:
    """The rules, or the reason this process does not have them.

    Deliberately one type with both cases, so every caller must handle both -- a caller that
    forgets gets `None`, which no predicate here or downstream treats as permission.
    """

    rules: InertLandingRules | None
    reason: str | None = None


class InertLandingPolicySource(Protocol):
    """Asked once per admission decision that needs it."""

    def inert_landing_rules(self) -> InertLandingAnswer: ...


class HttpInertLandingPolicySource:
    """Reads change-manager over HTTP, and converts every failure into an answer.

    Injected at the route, the same way the record and estate readers are, so the whole admission
    path runs with no network. It goes through an `httpx.Client` with an INJECTABLE transport
    rather than a module-level call, for the reason its sibling gives: a module-level call can
    only be tested by patching, and the request itself is then unobservable -- which is what lets
    a test prove the credential and the route on the wire are the ones intended.

    **The credential is change-manager's, and it is the one the record reader already holds.**
    Every narrow scope in that service is its read routes plus whatever it may write, and this
    route is one of those read routes, so a bearer that can read a change record can read this.
    Sharing it means activating this lane costs one environment variable -- the switch -- rather
    than a second secret whose absence would be indistinguishable from a service being down.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def inert_landing_rules(self) -> InertLandingAnswer:
        if not self._base_url or not self._token:
            return InertLandingAnswer(None, SOURCE_UNCONFIGURED)
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout_seconds) as client:
                response = client.get(
                    f"{self._base_url}{_ROUTE}",
                    headers={
                        "authorization": f"Bearer {self._token}",
                        "user-agent": _USER_AGENT,
                    },
                )
        # THREE FAMILIES, and the third is not an `httpx` exception at all. `InvalidURL` derives
        # straight from `Exception`, and IDNA encoding of a malformed host raises `UnicodeError`,
        # which is a `ValueError` -- so a base URL with a trailing newline, a doubled dot or an
        # over-long DNS label escapes a tuple that names only `HTTPError`. Every one of those is
        # an ordinary way for an environment variable to be malformed, and every one of them
        # would surface as a bare 500 from the admission path.
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return InertLandingAnswer(None, SOURCE_UNREADABLE)
        if response.status_code != 200:
            return InertLandingAnswer(None, SOURCE_UNREADABLE)
        try:
            body = response.json()
        except ValueError:
            return InertLandingAnswer(None, SOURCE_UNREADABLE)
        return _rules_from_body(body)


def _rules_from_body(body: Any) -> InertLandingAnswer:
    """The served document as rules, or the reason it does not read as any.

    Defensive about a shape this repository does not own, and refusing at every branch. The
    likeliest way to meet an unrecognised one is a policy version this build predates -- which is
    exactly the case where guessing turns "a document I have never seen" into a permission.
    """
    if not isinstance(body, dict):
        return InertLandingAnswer(None, SOURCE_UNREADABLE)
    version = body.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        return InertLandingAnswer(None, SOURCE_UNREADABLE)
    block = body.get(_BLOCK)
    if block is None:
        return InertLandingAnswer(None, RULES_UNDECLARED)
    if not isinstance(block, dict):
        return InertLandingAnswer(None, SOURCE_UNREADABLE)

    repositories = _string_set(block.get("repositories"))
    authors = _string_set(block.get("permitted_authors"))
    ecosystems = _string_set(block.get("excluded_ecosystems"))
    fresh = block.get("require_head_current_with_base")
    if repositories is None or authors is None or ecosystems is None:
        return InertLandingAnswer(None, SOURCE_UNREADABLE)
    if not isinstance(fresh, bool):
        return InertLandingAnswer(None, SOURCE_UNREADABLE)
    # ABSENT is the empty set and PRESENT-BUT-MALFORMED is unreadable. The two are told apart
    # rather than collapsed: absent is a document written before ADR-0041 and is readable, while a
    # field that is there and is not a list of strings is a document this build cannot read, which
    # is what every other field here already says.
    exempt: frozenset[str] | None = frozenset()
    if "non_ecosystem_authors" in block:
        exempt = _string_set(block.get("non_ecosystem_authors"))
        if exempt is None:
            return InertLandingAnswer(None, SOURCE_UNREADABLE)
    return InertLandingAnswer(
        InertLandingRules(
            version=version,
            repositories=frozenset(name.lower() for name in repositories),
            permitted_authors=frozenset(authors),
            non_ecosystem_authors=exempt,
            excluded_ecosystems=frozenset(ecosystems),
            require_head_current_with_base=fresh,
        )
    )


def _string_set(value: Any) -> frozenset[str] | None:
    """A served list of non-empty strings, or `None` for anything else.

    `None` means UNREADABLE and never means empty. The distinction is the whole of this helper:
    an absent or malformed list read as an empty one would make an absent `repositories` say
    "no repository is declared" -- fail-closed and therefore survivable -- while an absent
    `excluded_ecosystems` would say "nothing is excluded", which is a permission the document
    never granted. Two absences, opposite polarities, one reason not to default either.
    """
    if not isinstance(value, list):
        return None
    if any(not isinstance(item, str) or not item for item in value):
        return None
    return frozenset(value)
