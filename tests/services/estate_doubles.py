"""Test doubles for what the estate records about a landing (WS-P2.28).

The real source is an HTTP client. It is injected at the route exactly like the workflow client,
so the admission path is exercised here without a network and without patching a module-level
function -- the shape `intent-packages` got wrong, where a module-level `httpx.get` left its only
outage test passing for an entirely unrelated reason.

`asked` is recorded because "did admission consult the estate at all?" is a real assertion: a term
that short-circuits before the call is the difference between a check that is cheap and one that
is absent.
"""

from orchestrator.services.estate_landing import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    LANDING_UNKNOWN,
    SOURCE_UNCONFIGURED,
    SOURCE_UNREADABLE,
    EstateAnswer,
)

__all__ = [
    "LANDING_INERT",
    "LANDING_REDEPLOYS",
    "LANDING_UNKNOWN",
    "SOURCE_UNCONFIGURED",
    "SOURCE_UNREADABLE",
    "EstateAnswer",
    "FakeEstateLandingSource",
    "inert_source",
    "redeploying_source",
]


class FakeEstateLandingSource:
    def __init__(
        self,
        answers: dict[str, EstateAnswer] | None = None,
        default: EstateAnswer = EstateAnswer(LANDING_INERT),
    ) -> None:
        self._answers = dict(answers or {})
        self._default = default
        self.asked: list[str] = []

    def landing_for(self, github_repo: str) -> EstateAnswer:
        self.asked.append(github_repo)
        return self._answers.get(github_repo, self._default)


def inert_source() -> FakeEstateLandingSource:
    """Every repository reads `inert` -- which is what App Brain records for the repository the
    existing dispatch suite targets (`AlobarQuest/orchestrator`), so this preserves those tests'
    subject rather than neutralising the term underneath them."""
    return FakeEstateLandingSource()


def redeploying_source() -> FakeEstateLandingSource:
    return FakeEstateLandingSource(default=EstateAnswer(LANDING_REDEPLOYS))
