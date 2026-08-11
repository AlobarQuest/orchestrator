"""Test doubles for the estate's change record (ADR-0019 Increment 3).

The real source is an HTTP client injected at the route, exactly like the estate answer's reader,
so admission is exercised here without a network and without patching a module-level function.

`asked` is recorded because "did admission consult change-manager at all?" is a real assertion:
the question costs a round-trip inside a transaction holding a row lock, and a term that
short-circuits before the call is the difference between a check that is cheap and one that is
absent. It records the pair, because asking about the right repository and the wrong pull request
is the mistake a repository-keyed lookup invites.
"""

from orchestrator.services.change_record import (
    RECORD_AMBIGUOUS,
    SOURCE_UNCONFIGURED,
    SOURCE_UNREADABLE,
    STATUS_APPROVED,
    ChangeRecord,
    ChangeRecordAnswer,
)

__all__ = [
    "RECORD_AMBIGUOUS",
    "SOURCE_UNCONFIGURED",
    "SOURCE_UNREADABLE",
    "STATUS_APPROVED",
    "ChangeRecord",
    "ChangeRecordAnswer",
    "FakeChangeRecordSource",
    "approved_record_source",
    "no_record_source",
]


class FakeChangeRecordSource:
    def __init__(
        self,
        answers: dict[tuple[str, int], ChangeRecordAnswer] | None = None,
        default: ChangeRecordAnswer = ChangeRecordAnswer(True),
    ) -> None:
        self._answers = dict(answers or {})
        self._default = default
        self.asked: list[tuple[str, int]] = []

    def record_for(self, github_repo: str, pull_request_number: int) -> ChangeRecordAnswer:
        self.asked.append((github_repo, pull_request_number))
        return self._answers.get((github_repo, pull_request_number), self._default)


def no_record_source() -> FakeChangeRecordSource:
    """change-manager was read and holds no record for anything asked of it."""
    return FakeChangeRecordSource()


def approved_record_source(
    github_repo: str, pull_request_number: int, status: str = STATUS_APPROVED
) -> FakeChangeRecordSource:
    """One record, for one pull request, at the given status."""
    record = ChangeRecord(
        status=status,
        target_repository=github_repo,
        pull_request_number=pull_request_number,
    )
    return FakeChangeRecordSource(
        {(github_repo, pull_request_number): ChangeRecordAnswer(True, record=record)}
    )
