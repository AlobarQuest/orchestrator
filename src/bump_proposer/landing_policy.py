"""What change-manager declares will land unattended, read at the source rather than transcribed.

ADR-0038. Until now this producer learned which bumps the estate lands by itself by importing
`landing_ledger.rules` -- a hand transcription of a GitHub Actions workflow, keyed by that
workflow's git blob sha. The workflow is being removed and the orchestrator becomes the merger,
which leaves that rule with three readers and no holder: the party that lands, this producer, and
a person. change-manager's deploy policy is where it now lives, for the reason its own module
header gives for everything else in it -- one holder, and the readers ask.

**IT IS READ, NOT COMPUTED.** This module parses a declaration and answers questions about it. It
does not evaluate any condition that lives in GitHub -- who opened a pull request, which ecosystem
its branch names, what its required checks concluded -- because change-manager does not evaluate
those either, and a second interpreter of them is the thing the transcription already was.

**WHY `/api/landing-policy` AND NOT `/api/deploy-policy`.** They are two projections of ONE
document and were byte-identical when measured, so either would answer today. This one is chosen
for two reasons that are about tomorrow. It is the path the LANDING PARTY reads -- the
orchestrator's own architecture guards forbid the bare token the other path is spelled with
anywhere under its source tree -- so the lane and this producer read the same projection, and a
divergence between projections cannot make them disagree about the same bump. And it is the path
that survives: ADR-0038 records that at the rename Devon deferred, the second path becomes the
primary and the first retires.

**ONE PATH BY CONSTRUCTION, NOT BY ALLOWLIST.** `change_manager.py` asserts its two paths against
a pattern before the transport, because it has a write and a read and could name others. This
module has one request and takes no path argument at all, so there is nothing for a mistake here
to point somewhere else. The credential it is given is READ-scoped, which is the other half: a
dry run reads this document and still cannot reach the route that writes.

**AN ABSENT BLOCK IS A REFUSAL, NOT A WAIVER.** A policy version that declares no inert population
is not one that opened this lane to everybody; it is one that did not decide the question. Every
failure below raises, and the pass reports that it could not use its inputs -- which is the same
fail-closed reading an untranscribed gate blob got, arrived at from the other side.

**BOTH POPULATIONS, EACH ON ITS OWN TERMS (ADR-0028 amendment 3).** The document governs two
populations: the DEPLOYING one, the top-level `repositories` with its terms in the `landing` block,
where landing redeploys production; and the INERT one, the `inert_landing` block. The bump
proposer's lane covers both, and asks each repository's own block which ecosystems the landing
party excludes (ADR-0028 amendment 3). An empty or absent deploying population is a refusal
exactly as an inert one is, so a document that stopped declaring it can't shrink the lane to inert
in silence.
Of the `landing` block only `excluded_ecosystems` is read, and it is required: a version without
it (1 to 4) decides by update type, which this producer does not evaluate. The `landing` block
names no authors -- who may land a deploying pull request is the orchestrator's admission -- so
`covers_author` asks the inert block alone, as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import httpx

from bump_proposer.change_manager import DEFAULT_BASE_URL, TIMEOUT_SECONDS, USER_AGENT
from estate_clients.confined import ConfinedClient, TransportFailure

# The one path this module reaches. See the docstring: there is no path parameter, so this
# constant and the request that uses it cannot drift apart.
_PATH: Final = "/api/landing-policy"

# The key the document carries the inert population under.
_BLOCK: Final = "inert_landing"
# The key carrying the deploying population's landing terms; the population itself is the
# document's top-level `repositories`.
_DEPLOYING_BLOCK: Final = "landing"

# The keys that block must carry. THE FOURTH IS REQUIRED AND DELIBERATELY NOT STORED --
# `require_head_current_with_base` is a condition on the ACT, and this producer does not act. It
# is a condition the LANDING PARTY clears by itself, by bringing the branch up to date, so a
# producer that read it as a refusal would turn every sibling a landing staled into factory work.
# Requiring the key without keeping the value is the honest middle: a document that dropped it is
# malformed and refused, and a document that carries it cannot be silently half-read here.
_REQUIRED: Final = (
    "repositories",
    "permitted_authors",
    "excluded_ecosystems",
    "require_head_current_with_base",
)


class LandingPolicyError(Exception):
    """The declaration could not be read, or does not decide this question. Never a guess."""


@dataclass(frozen=True)
class LandingPolicy:
    """Both populations change-manager declares, and the terms each lands on.

    `repositories`, `permitted_authors` and `excluded_ecosystems` are the INERT block's, kept
    under the names the inert lander reads; `deploying` and `deploying_excluded_ecosystems` are the
    deploying population and its `landing` block's exclusions. All names are stored lowercased.

    `version` is the DOCUMENT's version and not either block's -- one version covers both
    populations, so a later version moving only a rollout pin in the deploying half re-stamps the
    inert half too. Carried because the pass reports it, and reported because a reader of a
    proposal wants to know which declaration was in force.
    """

    version: int
    repositories: frozenset[str]
    permitted_authors: frozenset[str]
    excluded_ecosystems: frozenset[str]
    deploying: frozenset[str]
    deploying_excluded_ecosystems: frozenset[str]

    def declares(self, repository: str) -> bool:
        """Whether a human has admitted this repository to either population.

        CASE-FOLDED ON BOTH SIDES. change-manager serves the populations lowercased and a standing
        package names its target the way GitHub spells it (`AlobarQuest/...`), so comparing the
        two as given would answer False for every repository in the lane.
        """
        name = repository.lower()
        return name in self.repositories or name in self.deploying

    def deploys(self, repository: str) -> bool:
        """Whether this repository is in the deploying population, where landing redeploys.

        A repository in both populations deploys: change-manager holds the two disjoint, and if
        that ever fails the more restrained answer wins (Review focus 2).
        """
        return repository.lower() in self.deploying

    def permits(self, repository: str, ecosystem: str | None) -> bool:
        """Whether the declaration lets a bump in this repository and ecosystem land unattended.

        EACH REPOSITORY'S OWN BLOCK DECIDES (ADR-0028 amendment 3): the `landing` block's
        exclusions for a deploying repository, the inert block's for an inert one. A repository in
        neither is permitted nothing -- nobody has said anything lands there.

        IT ASKS NOTHING ABOUT THE VERSION DELTA, and that is the declaration's own shape rather
        than a simplification here: what decides is whether the required checks pass, except in
        the ecosystems those checks do not exercise. The caller asks what the checks concluded.

        AN ABSENT ECOSYSTEM IS FAIL-CLOSED RATHER THAN FAITHFUL, deliberately, and the reasoning
        is carried over unchanged from the transcription this replaces. The landing party always
        has one -- it is the second segment of the branch the update bot opens -- so `None` here
        never means "the party that lands saw nothing". It means THIS PROGRAM could not read what
        that party reads, and permitting on that would leave a bump to a lane whose exclusion
        nobody can re-check.
        """
        if ecosystem is None:
            return False
        if self.deploys(repository):
            return ecosystem not in self.deploying_excluded_ecosystems
        if repository.lower() in self.repositories:
            return ecosystem not in self.excluded_ecosystems
        return False

    def covers_author(self, author: str) -> bool:
        """Whether pull requests by this author are ones the declaration lets land at all.

        The one thing this producer must check about the declaration rather than about a bump.
        Its subject is what the lane REFUSES, so a declaration that stopped permitting the update
        bot would make every open bump in the estate this lane's subject at once -- a flood of
        package revisions that cannot be unminted, on a decision nobody took. The caller refuses
        instead. It fails closed in the expensive direction; the cheap direction, a declaration
        naming an author this producer's reader cannot see, costs a quiet lane and no writes.
        """
        return author in self.permitted_authors


def _block(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise LandingPolicyError("the landing policy did not answer an object")
    version = document.get("version")
    if not isinstance(version, int):
        raise LandingPolicyError("the landing policy names no version")
    block = document.get(_BLOCK)
    if block is None:
        raise LandingPolicyError(
            f"landing policy version {version} declares no inert population, so which bumps "
            "land unattended is not a question with an answer; an absent declaration is a "
            "refusal rather than a waiver"
        )
    if not isinstance(block, dict):
        raise LandingPolicyError(f"the landing policy's {_BLOCK} is not an object")
    missing = [key for key in _REQUIRED if key not in block]
    if missing:
        raise LandingPolicyError(
            f"the landing policy's {_BLOCK} is missing {', '.join(sorted(missing))}"
        )
    return block


def _names(block: dict[str, Any], key: str) -> frozenset[str]:
    value = block.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise LandingPolicyError(f"the landing policy's {key} is not a list of names")
    return frozenset(value)


def _deploying(document: dict[str, Any], version: int) -> tuple[frozenset[str], frozenset[str]]:
    """The deploying population and its `landing` block's exclusions, or a refusal."""
    if "repositories" not in document:
        raise LandingPolicyError(
            f"landing policy version {version} declares no deploying population; an absent "
            "declaration is a refusal rather than a waiver"
        )
    deploying = _names(document, "repositories")
    if not deploying:
        raise LandingPolicyError(
            f"landing policy version {version} declares an EMPTY deploying population; a "
            "declaration naming nobody is not the same as one naming everybody"
        )
    landing = document.get(_DEPLOYING_BLOCK)
    if not isinstance(landing, dict):
        raise LandingPolicyError(f"the landing policy's {_DEPLOYING_BLOCK} is not an object")
    if "excluded_ecosystems" not in landing:
        raise LandingPolicyError(
            f"landing policy version {version} decides deploying landings by update type, which "
            "this producer does not evaluate"
        )
    return (
        frozenset(name.lower() for name in deploying),
        _names(landing, "excluded_ecosystems"),
    )


def parse(document: Any) -> LandingPolicy:
    """The declaration, or a refusal. Separated from the transport so it can be exercised."""
    block = _block(document)
    version = document["version"]
    repositories = _names(block, "repositories")
    if not repositories:
        raise LandingPolicyError(
            f"landing policy version {version} declares an EMPTY inert population; a declaration "
            "naming nobody is not the same as one naming everybody"
        )
    deploying, deploying_excluded = _deploying(document, version)
    return LandingPolicy(
        version=version,
        repositories=frozenset(name.lower() for name in repositories),
        permitted_authors=_names(block, "permitted_authors"),
        excluded_ecosystems=_names(block, "excluded_ecosystems"),
        deploying=deploying,
        deploying_excluded_ecosystems=deploying_excluded,
    )


def read_landing_policy(
    token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    transport: httpx.BaseTransport | None = None,
) -> LandingPolicy:
    """Ask change-manager what it declares. One request, one path, one credential."""
    if not token:
        raise LandingPolicyError("no READ-scoped change-manager credential")
    try:
        client = ConfinedClient(
            base_url=base_url,
            user_agent=USER_AGENT,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            permits=lambda method, path: method == "GET" and path == _PATH,
            refuse=lambda method, path: LandingPolicyError(
                f"the landing-policy reader may not {method} {path}"
            ),
        )
    except TransportFailure as failure:
        # Construction is guarded as well as the request (ADR-0050).
        raise LandingPolicyError(
            f"the change-manager base URL is unusable: {failure.error_type}"
        ) from None
    try:
        try:
            response = client.request("GET", _PATH)
        except TransportFailure as failure:
            raise LandingPolicyError(
                f"change-manager is unreachable for {_PATH}: {failure.error_type}"
            ) from None
        if response.status_code >= 400:
            hint = (
                " -- the credential is not scoped for this route"
                if response.status_code == 403
                else ""
            )
            raise LandingPolicyError(
                f"change-manager answered {response.status_code} for {_PATH}{hint}"
            )
        try:
            document = response.json()
        except ValueError:
            raise LandingPolicyError("the landing policy did not answer JSON") from None
    finally:
        client.close()
    return parse(document)
