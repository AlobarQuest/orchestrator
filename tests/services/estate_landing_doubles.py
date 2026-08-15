"""Doubles for ADR-0019 increment 5b: a change record with its landing conditions, and GitHub.

The conditions are built from the same literals a real change-manager serves, rather than derived
from anything in the source tree. That is deliberate: every test in increment 5a built its payload
FROM the policy, which proves the comparison works and says nothing about whether the two sides
agree. Here the fixture states what change-manager actually sends -- measured 2026-08-12 against
production -- so a change to the reader's parse shows up as a failing test rather than as a
green suite over a wire nobody read.
"""

from __future__ import annotations

from orchestrator.services.change_record import (
    ChangeRecord,
    ChangeRecordAnswer,
    LandingConditions,
    WorkflowPin,
)
from orchestrator.services.estate_landing_admission import (
    EstateGatewayError,
    EstatePullRequest,
)

REPOSITORY = "alobarquest/change-manager"
ROLLOUT_PATH = ".github/workflows/deploy.yml"
ROLLOUT_BLOB = "a47d4b187c93971a5b5915ce87a963bd4ef35e30"
HEAD = "9f7f6ea6b3adde1cfc712f737647bc308cadb59a"
POLICY_VERSION = 2

# A real title from the population this lane serves, measured 2026-08-12.
MINOR_TITLE = "build(deps): bump alembic from 1.18.5 to 1.19.0"


def conditions(
    *,
    version: int = POLICY_VERSION,
    update_types: frozenset[str] = frozenset({"semver-patch", "semver-minor"}),
    require_fresh: bool = True,
    pins: dict[str, WorkflowPin] | None = None,
) -> LandingConditions:
    return LandingConditions(
        version=version,
        update_types=update_types,
        require_head_current_with_base=require_fresh,
        rollout_workflows=(
            {REPOSITORY: WorkflowPin(path=ROLLOUT_PATH, blob_sha=ROLLOUT_BLOB)}
            if pins is None
            else pins
        ),
    )


_DEFAULT = object()


def approved(
    *,
    status: str = "approved",
    policy_version: int | None = POLICY_VERSION,
    objections: tuple[str, ...] = (),
    record_id: int | None = 52,
    landing_conditions: LandingConditions | None | object = _DEFAULT,
) -> ChangeRecordAnswer:
    """A record as change-manager serves one.

    `landing_conditions` distinguishes "not overridden" from an EXPLICIT `None`, because `None` is
    a real value here -- it is what a record service that predates the conditions produces, and it
    is the case the landing must refuse on. A default of `None` would make that case unreachable
    from a test, which is how a fail-closed branch ships untested.
    """
    return ChangeRecordAnswer(
        True,
        record=ChangeRecord(
            status=status,
            target_repository=REPOSITORY,
            pull_request_number=49,
            record_id=record_id,
            policy_version=policy_version,
            policy_objections=objections,
            decided_by="deploy-policy",
            conditions=(
                conditions() if landing_conditions is _DEFAULT else landing_conditions  # type: ignore[arg-type]
            ),
        ),
    )


def pull_request(
    *,
    number: int = 49,
    title: str = MINOR_TITLE,
    head_sha: str = HEAD,
    base_ref: str = "main",
    default_branch: str = "main",
    is_open: bool = True,
    landed: bool = False,
    author_login: str = "dependabot[bot]",
    author_is_bot: bool = True,
    mergeable_state: str = "clean",
) -> EstatePullRequest:
    return EstatePullRequest(
        number=number,
        title=title,
        head_sha=head_sha,
        base_ref=base_ref,
        default_branch=default_branch,
        open=is_open,
        landed=landed,
        author_login=author_login,
        author_is_bot=author_is_bot,
        mergeable_state=mergeable_state,
    )


class FakeEstateGateway:
    """Records every question asked, so a test can assert on what was NOT asked."""

    def __init__(
        self,
        *,
        pull: EstatePullRequest | None = None,
        behind: int = 0,
        blob: str | None = ROLLOUT_BLOB,
        head_blob: str | None | object = _DEFAULT,
        read_error: EstateGatewayError | None = None,
        compare_error: EstateGatewayError | None = None,
        blob_error: EstateGatewayError | None = None,
        update_error: EstateGatewayError | None = None,
    ) -> None:
        self._pull = pull or pull_request()
        self._behind = behind
        self._blob = blob
        # What the pinned path reads as at the pull request's OWN head, when that differs from the
        # base -- which is the case a pull request editing the rollout workflow produces, and the
        # only one that can tell a base-only pin from a complete one.
        self._head_blob = blob if head_blob is _DEFAULT else head_blob
        self._read_error = read_error
        self._compare_error = compare_error
        self._blob_error = blob_error
        self._update_error = update_error
        self.reads: list[tuple[str, int]] = []
        self.compares: list[tuple[str, str, str]] = []
        self.blobs: list[tuple[str, str, str]] = []
        # ADR-0019 Increment 6. THE ONE LIST THAT MATTERS FOR A REFUSAL TEST: every assertion that
        # this lane declined to touch a branch is an assertion that this stayed empty. A test that
        # only checks the raised error would pass against an implementation that acted first and
        # complained afterwards.
        self.branch_updates: list[tuple[str, int, str]] = []

    def read_pull_request(self, *, repository: str, number: int) -> EstatePullRequest:
        self.reads.append((repository, number))
        if self._read_error is not None:
            raise self._read_error
        return self._pull

    def commits_behind_base(self, *, repository: str, base_ref: str, head_sha: str) -> int:
        self.compares.append((repository, base_ref, head_sha))
        if self._compare_error is not None:
            raise self._compare_error
        return self._behind

    def blob_sha(self, *, repository: str, path: str, ref: str) -> str | None:
        self.blobs.append((repository, path, ref))
        if self._blob_error is not None:
            raise self._blob_error
        base = self._pull.base_ref
        return self._blob if ref == base else self._head_blob  # type: ignore[return-value]

    def update_branch(self, *, repository: str, number: int, expected_head_sha: str) -> None:
        self.branch_updates.append((repository, number, expected_head_sha))
        if self._update_error is not None:
            raise self._update_error
