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
    HeadCheckRun,
)

REPOSITORY = "alobarquest/change-manager"
ROLLOUT_PATH = ".github/workflows/deploy.yml"
ROLLOUT_BLOB = "a47d4b187c93971a5b5915ce87a963bd4ef35e30"
HEAD = "9f7f6ea6b3adde1cfc712f737647bc308cadb59a"
POLICY_VERSION = 2

# A real title from the population this lane serves, measured 2026-08-12.
MINOR_TITLE = "build(deps): bump alembic from 1.18.5 to 1.19.0"

# A real branch from the same population, measured 2026-08-30 on change-manager #67. The ecosystem
# is its second segment.
HEAD_REF = "dependabot/uv/uvicorn-standard--gte-0.52.4"

# The one ecosystem deploy policy version 5 excludes, spelled as the update bot spells it in a
# branch name. WITH AN UNDERSCORE, which is not a detail: the estate's landing ledger records a
# revision of the other lane's gate that compared the hyphenated form against this value and
# therefore permitted nothing, silently, for weeks.
WORKFLOW_AUTOMATION = "github_actions"


def conditions(
    *,
    version: int = POLICY_VERSION,
    update_types: frozenset[str] = frozenset({"semver-patch", "semver-minor"}),
    require_fresh: bool = True,
    pins: dict[str, WorkflowPin] | None = None,
    excluded_ecosystems: frozenset[str] | None = None,
) -> LandingConditions:
    """The pre-ADR-0036 shape by default, because it is what most of this file's cases are about.

    `excluded_ecosystems=None` is the version-4-and-earlier shape and selects the update-type
    rule; passing a frozenset selects the outcome rule. The default is the older shape so that
    every case written before ADR-0036 keeps asking the question it was written to ask.
    """
    return LandingConditions(
        version=version,
        update_types=update_types,
        require_head_current_with_base=require_fresh,
        rollout_workflows=(
            {REPOSITORY: WorkflowPin(path=ROLLOUT_PATH, blob_sha=ROLLOUT_BLOB)}
            if pins is None
            else pins
        ),
        excluded_ecosystems=excluded_ecosystems,
    )


def outcome_conditions(
    *,
    version: int = 5,
    excluded_ecosystems: frozenset[str] = frozenset({WORKFLOW_AUTOMATION}),
    require_fresh: bool = True,
    pins: dict[str, WorkflowPin] | None = None,
) -> LandingConditions:
    """Deploy policy version 5, as change-manager serves it. ADR-0036.

    `update_types` is EMPTY and that is the served value, not a convenience: version 5 does not
    decide by update type, and a landing party that has not learned the outcome rule must permit
    nothing under it rather than be handed a set it would act on.
    """
    return conditions(
        version=version,
        update_types=frozenset(),
        require_fresh=require_fresh,
        pins=pins,
        excluded_ecosystems=excluded_ecosystems,
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
    head_ref: str = HEAD_REF,
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
        head_ref=head_ref,
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
        runs: tuple[HeadCheckRun, ...] = (),
        runs_error: EstateGatewayError | None = None,
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
        # Empty by default, which is what a `clean` pull request never causes to be read at all --
        # the checks term asks only when the platform says `blocked`, so a fixture that does not
        # set this is asserting the question was not asked.
        self._runs = runs
        self._runs_error = runs_error
        self.reads: list[tuple[str, int]] = []
        self.compares: list[tuple[str, str, str]] = []
        self.blobs: list[tuple[str, str, str]] = []
        self.run_reads: list[tuple[str, str]] = []
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

    def head_check_runs(self, *, repository: str, head_sha: str) -> tuple[HeadCheckRun, ...]:
        self.run_reads.append((repository, head_sha))
        if self._runs_error is not None:
            raise self._runs_error
        return self._runs

    def update_branch(self, *, repository: str, number: int, expected_head_sha: str) -> None:
        self.branch_updates.append((repository, number, expected_head_sha))
        if self._update_error is not None:
            raise self._update_error


def run(status: str = "completed", conclusion: str | None = "success") -> HeadCheckRun:
    """One workflow run at a head. Defaults to the one that permits, so a fixture states the
    interesting half rather than restating the boring one."""
    return HeadCheckRun(status=status, conclusion=conclusion)
