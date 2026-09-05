"""Doubles for ADR-0038 part 2: the policy block declaring the inert population, and the acting
gateway.

**The served body below is what change-manager ACTUALLY SENDS, measured against production on
2026-08-31 at policy version 6, rather than built from anything in this source tree.** That is the
same discipline the deploying lane's doubles state and for the same reason: a fixture derived from
the reader proves the reader agrees with itself. This one states the wire, so a change to the parse
shows up as a failing test rather than as a green suite over a body nobody read.
"""

from __future__ import annotations

from typing import Any

from orchestrator.services.estate_landing_admission import EstateGatewayError
from orchestrator.services.estate_pr_merge import MergeOutcome
from orchestrator.services.inert_landing_policy import (
    InertLandingAnswer,
    InertLandingRules,
)
from tests.services.estate_landing_doubles import FakeEstateGateway

# One of the six repositories deploy policy version 6 declares. Spelled lower case, as the policy
# serves it and as the admission cascade folds it.
INERT_REPOSITORY = "alobarquest/orchestrator"

# The document's version, not the block's -- one number covers both populations, and it is what a
# landing here is attributed to.
INERT_POLICY_VERSION = 6

# The identity the policy names, in the spelling the REST pull-request object uses. The
# command-line client answers `app/dependabot` for the same pull request; this is the other one,
# and it is the one the workflow this lane replaces keyed on.
UPDATE_BOT = "dependabot[bot]"

# The one ecosystem version 6 excludes for this population, and it is NOT the one the deploying
# half excludes -- same principle, different ecosystem, because what the required checks leave
# unexercised differs. A test that assumes the two lanes exclude the same thing is testing the
# wrong document.
EXCLUDED_ECOSYSTEM = "docker"

DECLARED_REPOSITORIES = (
    "alobarquest/factory-runner",
    "alobarquest/infraops-mcp-server",
    "alobarquest/intent-packages",
    "alobarquest/orchestrator",
    "alobarquest/project-standards",
    "alobarquest/security-standards",
)


def served_body(**overrides: Any) -> dict[str, Any]:
    """The policy document as change-manager serves it, projected to what this reader looks at.

    Keyword overrides replace TOP-LEVEL keys; pass `inert_landing=...` to reshape the block.
    """
    body: dict[str, Any] = {
        "version": INERT_POLICY_VERSION,
        "decided": "2026-08-31",
        # The deploying half of the same document. Present so the reader is exercised against a
        # body carrying keys it must ignore rather than against a body shaped for it.
        "repositories": ["alobarquest/brain", "alobarquest/change-manager"],
        "change_classes": ["dependency-update", "factory-delivery"],
        "risks": ["caution"],
        "landing": {"update_types": [], "require_head_current_with_base": True},
        "inert_landing": {
            "repositories": list(DECLARED_REPOSITORIES),
            "permitted_authors": [UPDATE_BOT],
            "excluded_ecosystems": [EXCLUDED_ECOSYSTEM],
            "require_head_current_with_base": True,
            "rationale": "WHERE LANDING ON THE DEFAULT BRANCH CHANGES NOTHING ALREADY SERVING.",
        },
    }
    body.update(overrides)
    return body


def rules(
    *,
    version: int = INERT_POLICY_VERSION,
    repositories: frozenset[str] | None = None,
    permitted_authors: frozenset[str] | None = None,
    excluded_ecosystems: frozenset[str] | None = None,
    require_fresh: bool = True,
    non_ecosystem_authors: frozenset[str] = frozenset(),
) -> InertLandingRules:
    return InertLandingRules(
        version=version,
        repositories=(
            frozenset(name.lower() for name in DECLARED_REPOSITORIES)
            if repositories is None
            else repositories
        ),
        non_ecosystem_authors=non_ecosystem_authors,
        permitted_authors=(
            frozenset({UPDATE_BOT}) if permitted_authors is None else permitted_authors
        ),
        excluded_ecosystems=(
            frozenset({EXCLUDED_ECOSYSTEM}) if excluded_ecosystems is None else excluded_ecosystems
        ),
        require_head_current_with_base=require_fresh,
    )


class FakeInertPolicySource:
    """Records that it was asked, so a test can assert on a question NOT put."""

    def __init__(self, answer: InertLandingAnswer | None = None) -> None:
        self._answer = answer if answer is not None else InertLandingAnswer(rules())
        self.asked = 0

    def inert_landing_rules(self) -> InertLandingAnswer:
        self.asked += 1
        return self._answer


class ActingInertGateway(FakeEstateGateway):
    """The reading gateway plus the one call that lands anything, recorded.

    Named `merge` because that is the name the lane's protocol declares, which is what makes the
    repository's merge guard able to see the act at all.
    """

    def __init__(
        self,
        *,
        outcome: MergeOutcome | None = None,
        merge_error: EstateGatewayError | None = None,
        landed_after_refusal: bool | None = None,
        reconcile_error: EstateGatewayError | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._outcome = outcome or MergeOutcome(
            landed=True, commit_sha=LANDED_COMMIT, status_code=200
        )
        self._merge_error = merge_error
        self._landed_after_refusal = landed_after_refusal
        self._reconcile_error = reconcile_error
        # THE LIST THAT CARRIES EVERY REFUSAL ASSERTION: a test proving this lane declined is a
        # test proving this stayed empty. Reading only the raised error would pass against an
        # implementation that acted first and complained afterwards.
        self.merges: list[tuple[str, int, str, str]] = []

    def merge(
        self, *, repository: str, number: int, head_sha: str, commit_message: str
    ) -> MergeOutcome:
        self.merges.append((repository, number, head_sha, commit_message))
        if self._merge_error is not None:
            raise self._merge_error
        return self._outcome

    def read_pull_request(self, *, repository: str, number: int):
        # The reconciling re-read after a refusal is the SECOND read of the same pull request, and
        # it is the one whose failure means "we cannot rule out that it landed".
        if self.merges and self._reconcile_error is not None:
            self.reads.append((repository, number))
            raise self._reconcile_error
        pull = super().read_pull_request(repository=repository, number=number)
        if self.merges and self._landed_after_refusal is not None:
            from dataclasses import replace

            return replace(pull, landed=self._landed_after_refusal)
        return pull


LANDED_COMMIT = "cccccccccccccccccccccccccccccccccccccccc"
