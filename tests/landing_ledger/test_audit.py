"""The three detectors: each shown to FIRE on a constructed condition, and not to on a healthy one.

Every landing fixture below is shaped like the rows the production ledger actually holds -- the
six rule-permitted landings of 2026-08-07, whose facts were read back through
`GET /api/v1/observations?observation_type=landing` while this was written.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from landing_ledger.audit import (
    BRANCH_NOT_GREEN,
    CAVEAT_NO_RULE_INSTALLED,
    CAVEAT_RULE_SELF_MODIFIED,
    DRIFT_CHECK_NOT_GREEN,
    DRIFT_METADATA_MISSING,
    DRIFT_NOT_SATISFIED,
    DRIFT_RULE_DID_NOT_SUCCEED,
    DRIFT_RULE_MISSING,
    DRIFT_RULE_UNKNOWN,
    EXCEPTION_METADATA_UNREADABLE_AT_RECORDING,
    EXCEPTION_UPDATE_TYPE_UNPARSEABLE,
    FACTORY_CLAIM_UNREADABLE,
    FACTORY_FINGERPRINT_MISMATCH,
    FACTORY_HUMAN_ADJUDICATION,
    FACTORY_LANDING_UNBOUND,
    FACTORY_LANDING_UNCLAIMED,
    FACTORY_NOT_VERIFIER_DECIDED,
    FACTORY_UNIT_NOT_COMPLETED,
    FACTORY_UNIT_UNKNOWN,
    MAX_LIST,
    STALL_ARMED_NOT_LANDED,
    STALL_ELIGIBLE_NOT_ARMED,
    STALL_METADATA_UNREADABLE,
    STALL_RULE_UNKNOWN,
    audit_branch,
    audit_factory_landing,
    audit_landing,
    audit_observation,
    audit_pending,
    audit_repository,
    branch_status,
    is_green,
)
from landing_ledger.model import (
    BRANCH_FAILING,
    BRANCH_IN_FLIGHT,
    BRANCH_PASSING,
    BRANCH_UNVERIFIED,
    BranchStatus,
    Check,
    PendingUpdate,
    UpdateMetadata,
    WorkflowRun,
)
from landing_ledger.orchestrator_client import LedgerWriteError
from landing_ledger.record import (
    BASIS_FACTORY,
    KNOWN_DEFECTIVE_METADATA_LANDINGS,
    is_known_defective_metadata_landing,
)
from landing_ledger.rules import GATE_PATH, REGISTRY, rule_for

REPO = "AlobarQuest/factory-runner"
PATCH_AND_MINOR = "77ab867d1080d18baea3a2b230655c2729716970"
HYPHENATED = "4d87d9b7465e3b59bd9bdee2086de18eb1cab1dd"
UNDERSCORED = "12880ce77ab97c3f4d9281195041eed8c5d52609"
NEWER_METADATA = "43e37ed97823aec25cc5bac63f636914637e219c"

MAJOR = "version-update:semver-major"
MINOR = "version-update:semver-minor"

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)

# A green default branch, so every detector-A and detector-B test below states that it is not about
# detector C. `audit_repository` takes no default for `branch` precisely so this has to be said.
GREEN = BranchStatus(commit="f" * 40, state=BRANCH_PASSING, passing=(".github/workflows/ci.yml",))


def landing(
    *,
    basis: str = "auto_merge_rule",
    revision: str | None = UNDERSCORED,
    outcome: str = "success",
    update_type: str | None = MINOR,
    ecosystem: str | None = "uv",
    metadata: bool = True,
    checks: list[dict[str, Any]] | None = None,
    files: list[str] | None = None,
    repository: str = REPO,
    commit: str = "d5e31dc1164f9d0a" + "0" * 24,
) -> dict[str, Any]:
    permitted: dict[str, Any] = {
        "basis": basis,
        "landed_by": "github-actions[bot]",
        "checks_observed": 1,
        "checks": checks if checks is not None else [{"name": "Quality", "conclusion": "success"}],
    }
    if basis == "auto_merge_rule":
        permitted |= {
            "rule_path": GATE_PATH,
            "rule_revision": revision,
            "rule_run": 1,
            "rule_outcome": outcome,
            "dependency": "ruff",
            "ecosystem": ecosystem,
            "update_type": update_type,
        }
        if revision is None:
            del permitted["rule_revision"]
        if not metadata:
            # `permitted_by` writes the three update keys together or not at all, so a landing
            # whose trailer could not be read drops all three. That is a DIFFERENT record from
            # one whose `update_type` is present and null, which says the bot declared no delta
            # -- and `audit_landing` gives the two different answers.
            for key in ("dependency", "ecosystem", "update_type"):
                del permitted[key]
    return {
        "what_changed": {
            "repository": repository,
            "base_ref": "main",
            "commit": commit,
            "files": files if files is not None else ["uv.lock"],
            "files_changed": 1,
            "pull_request": 30,
        },
        "permitted_by": permitted,
    }


def kinds(findings: Any) -> list[str]:
    return [finding.kind for finding in findings]


# ---------------------------------------------------------------------------------------------
# The factory half of detector A. Every fixture below is shaped like the FIRST landing the factory
# ever made -- `AlobarQuest/intent-packages` #66, 2026-08-10 -- whose evidence pack and unit
# history were read from production while this was written.
# ---------------------------------------------------------------------------------------------

FACTORY_REPO = "AlobarQuest/intent-packages"
UNIT = "0c0002c6-9869-59bc-84c6-654e6fc57d9e"
FINGERPRINT = "40f1b2eaee9e5af976292664182ec977ab1c01578e33e155f92c192d5195c3d1"
MERGE_COMMIT = "b3f1522f8630a7026da7dbaa1a120971fc024f73"
HEAD = "0632151f54360676da1fd72b1f0a3c90a10668d5"


class FakeUnits:
    """The orchestrator as the factory detector sees it: two reads, and 404 answers None."""

    def __init__(
        self,
        packs: dict[str, dict[str, Any]] | None = None,
        histories: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.packs = packs or {}
        self.histories = histories or {}

    def read_evidence_pack(self, work_unit_id: str) -> dict[str, Any] | None:
        return self.packs.get(work_unit_id)

    def read_unit_history(self, work_unit_id: str) -> list[dict[str, Any]] | None:
        return self.histories.get(work_unit_id)


class UnreachableUnits:
    """An orchestrator that could not be asked. It RAISES, and the difference is the whole point:
    a unit that does not exist is a finding, and a question nobody managed to ask is not."""

    def read_evidence_pack(self, work_unit_id: str) -> dict[str, Any] | None:
        raise LedgerWriteError("orchestrator is unreachable for GET: ConnectError")

    def read_unit_history(self, work_unit_id: str) -> list[dict[str, Any]] | None:
        raise LedgerWriteError("orchestrator is unreachable for GET: ConnectError")


NO_UNITS = FakeUnits()


def factory_landing(
    *,
    work_unit: Any = UNIT,
    repository: str = FACTORY_REPO,
    commit: str = MERGE_COMMIT,
    pull_request: int = 66,
) -> dict[str, Any]:
    permitted: dict[str, Any] = {
        "basis": BASIS_FACTORY,
        "landed_by": "alobar-sds-dispatch[bot]",
        "checks_observed": 3,
        "checks": [{"name": "validate", "conclusion": "success", "run": 1}],
        "reason": "landed by the factory",
        "package_revision": 1,
    }
    if work_unit is not None:
        permitted["work_unit"] = work_unit
    return {
        "what_changed": {
            "repository": repository,
            "base_ref": "main",
            "commit": commit,
            "head_commit": HEAD,
            "pull_request": pull_request,
            "files": ["uv.lock"],
            "files_changed": 1,
        },
        "permitted_by": permitted,
    }


def pack(
    *,
    state: str = "completed",
    decided_by_verifier: bool = True,
    evidence_observed: bool = True,
    refusals: list[dict[str, Any]] | None = None,
    decided_by_role: str | None = "verifier",
    fingerprint: str = FINGERPRINT,
) -> dict[str, Any]:
    return {
        "work_unit": {"id": UNIT, "state": state, "authority_fingerprint": fingerprint},
        "verifier_decided_completion": {
            "satisfied": decided_by_verifier and evidence_observed,
            "decided_by_verifier": decided_by_verifier,
            "evidence_observed": evidence_observed,
            "refusals": refusals or [],
        },
        "adjudications": [
            {
                "ac_id": "AC-001",
                "outcome": "passed",
                "current": True,
                "decided_by_role": decided_by_role,
            }
        ],
    }


def history(
    *,
    status: str = "merged",
    repository: str = FACTORY_REPO,
    pr_number: int = 66,
    merge_commit: str | None = MERGE_COMMIT,
    head_sha: str = HEAD,
    fingerprint: str = FINGERPRINT,
) -> list[dict[str, Any]]:
    return [
        {"action": "work_unit.transitioned", "actor_id": "factory-runner", "payload": {}},
        {
            "action": f"pr_merge.{status}",
            "actor_id": "orchestrator-system",
            "payload": {
                "status": status,
                "repository": repository,
                "pr_number": pr_number,
                "head_sha": head_sha,
                "merge_commit_sha": merge_commit,
                "authority_fingerprint": fingerprint,
            },
        },
    ]


def units(
    unit_pack: dict[str, Any] | None = None, unit_history: list[dict[str, Any]] | None = None
) -> FakeUnits:
    return FakeUnits(
        {UNIT: unit_pack if unit_pack is not None else pack()},
        {UNIT: unit_history if unit_history is not None else history()},
    )


# ---------------------------------------------------------------------------------------------
# Detector A -- permissive drift.
# ---------------------------------------------------------------------------------------------


def test_a_landing_within_its_own_pinned_rule_is_not_a_finding() -> None:
    findings, caveats, _ = audit_landing(landing())

    assert (findings, caveats) == ((), ())


def test_a_major_bump_under_the_patch_and_minor_rule_FIRES() -> None:
    """The plain permissive drift: the gate landed something its own rule excluded."""
    findings, _, _ = audit_landing(landing(revision=PATCH_AND_MINOR, update_type=MAJOR))

    assert kinds(findings) == [DRIFT_NOT_SATISFIED]


def test_the_SAME_facts_pass_or_fail_on_the_pinned_revision_alone() -> None:
    """The discriminator that makes the whole design worth having.

    An Actions major is inside the corrected rule and outside the hyphenated one, and NOTHING
    about the landing distinguishes the two cases except which revision was pinned to it. A
    detector that judged every landing against today's rule would report the second as fine.
    """
    within, _, _ = audit_landing(
        landing(revision=UNDERSCORED, update_type=MAJOR, ecosystem="github_actions")
    )
    outside, _, _ = audit_landing(
        landing(revision=HYPHENATED, update_type=MAJOR, ecosystem="github_actions")
    )

    assert kinds(within) == []
    assert kinds(outside) == [DRIFT_NOT_SATISFIED]


def test_an_untranscribed_rule_revision_FIRES_rather_than_passing() -> None:
    findings, _, _ = audit_landing(landing(revision="f" * 40))

    assert kinds(findings) == [DRIFT_RULE_UNKNOWN]


def test_a_rule_basis_with_no_rule_pinned_FIRES() -> None:
    findings, _, _ = audit_landing(landing(revision=None))

    assert kinds(findings) == [DRIFT_RULE_MISSING]


def test_absent_update_metadata_FIRES_rather_than_being_read_as_ineligible() -> None:
    """The rule's own job-level condition is "the update bot raised this", and the trailer is the
    only proxy the ledger holds for it. Absent, the condition cannot be re-read -- which is a
    finding, not a quiet pass and not a rule violation."""
    findings, _, _ = audit_landing(landing(metadata=False))

    assert kinds(findings) == [DRIFT_METADATA_MISSING]


# ---------------------------------------------------------------------------------------------
# The six rows recorded while the reader could not read a requirement range's trailer.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("repository", "commit"), sorted(KNOWN_DEFECTIVE_METADATA_LANDINGS)
)
def test_a_known_defective_row_is_an_exception_rather_than_a_finding(
    repository: str, commit: str
) -> None:
    """Every one of the six, named individually rather than as a count.

    They are immutable and content-addressed with no supersession path, so the metadata they lack
    can never arrive and the finding would never clear. That is a certainty about the SUBJECT,
    which is what makes it an exception rather than a caveat -- and exceptions do not drive the
    exit status, so the control stops being permanently non-zero on something nobody can fix.
    """
    findings, _, exceptions = audit_landing(
        landing(metadata=False, repository=repository, commit=commit)
    )

    assert kinds(findings) == []
    assert kinds(exceptions) == [EXCEPTION_METADATA_UNREADABLE_AT_RECORDING]


def test_the_exemption_silences_one_row_and_not_the_finding_class() -> None:
    """AN EXEMPTION THAT SILENCED THE CLASS WOULD BE WORSE THAN THE DEFECT. Three controls, and
    the first two are the ones that discriminate: the same repository at a different commit, and
    the same commit in a different repository, both still FIRE."""
    same_repository = landing(
        metadata=False,
        repository="AlobarQuest/orchestrator",
        commit="0" * 40,
    )
    same_commit = landing(
        metadata=False,
        repository="AlobarQuest/change-manager",
        commit="b0150834bd6d42950b4fe3ca65582e05af2aae3f",
    )

    assert kinds(audit_landing(same_repository)[0]) == [DRIFT_METADATA_MISSING]
    assert kinds(audit_landing(same_commit)[0]) == [DRIFT_METADATA_MISSING]
    assert kinds(audit_landing(landing(metadata=False))[0]) == [DRIFT_METADATA_MISSING]


def test_an_exempt_row_is_still_audited_for_everything_else() -> None:
    """The exemption withholds ONE finding, not the audit. A landing whose gate run did not
    succeed, or whose checks did not pass, is still reported however it was recorded."""
    exempt = dict(
        repository="AlobarQuest/orchestrator",
        commit="b0150834bd6d42950b4fe3ca65582e05af2aae3f",
    )

    findings, _, exceptions = audit_landing(
        landing(
            metadata=False,
            outcome="failure",
            checks=[{"name": "Quality", "conclusion": "failure"}],
            **exempt,
        )
    )

    assert kinds(findings) == [DRIFT_RULE_DID_NOT_SUCCEED, DRIFT_CHECK_NOT_GREEN]
    assert kinds(exceptions) == [EXCEPTION_METADATA_UNREADABLE_AT_RECORDING]


def test_the_exempt_population_is_exactly_six_named_landings() -> None:
    """A LITERAL ASSERTION, because the list IS the judgment. Every fixture above derives itself
    from the constant, so a member silently added to it would be exempted by tests that grew to
    match. Six rows, verified against production on 2026-08-29."""
    assert len(KNOWN_DEFECTIVE_METADATA_LANDINGS) == 6
    assert KNOWN_DEFECTIVE_METADATA_LANDINGS == frozenset(
        {
            ("AlobarQuest/orchestrator", "b0150834bd6d42950b4fe3ca65582e05af2aae3f"),
            ("AlobarQuest/orchestrator", "b58bc4e2d8d2a56ff37cb70950a9ee87a29320d9"),
            ("AlobarQuest/orchestrator", "a9a85bf6a350a09d931306b522ffc89234b3eb40"),
            ("AlobarQuest/intent-packages", "870e5c718ba68dd5057b7e8e7bc72f1fba885a3e"),
            ("AlobarQuest/factory-runner", "c70bea752c93c11dbe698bd592958f7ee16697da"),
            ("AlobarQuest/security-standards", "3a60adc6af3d42b0045bd6fb2b5222719bfb1f31"),
        }
    )


def test_a_non_string_subject_matches_nothing_rather_than_raising() -> None:
    """The audit's own convention: an unexpected shape must reach a finding, never an exception
    the pass cannot survive."""
    assert not is_known_defective_metadata_landing(None, None)
    assert not is_known_defective_metadata_landing(["a"], {"b": 1})


def test_a_recorded_failing_check_FIRES() -> None:
    findings, _, _ = audit_landing(landing(checks=[{"name": "Quality", "conclusion": "failure"}]))

    assert kinds(findings) == [DRIFT_CHECK_NOT_GREEN]


def test_a_skipped_check_is_not_a_failing_check() -> None:
    """A conditional job that did not run is neither pass nor failure. Counting it as red would
    make every repository with a conditional job a permanent finding -- and a permanently red
    signal is one nobody reads."""
    findings, _, _ = audit_landing(
        landing(
            checks=[
                {"name": "deploy", "conclusion": "skipped"},
                {"name": "q", "conclusion": "neutral"},
            ]
        )
    )

    assert kinds(findings) == []


def test_a_rule_run_that_did_not_succeed_FIRES() -> None:
    findings, _, _ = audit_landing(landing(outcome="failure"))

    assert kinds(findings) == [DRIFT_RULE_DID_NOT_SUCCEED]


def test_a_landing_a_person_decided_is_not_this_detectors_subject() -> None:
    findings, caveats, _ = audit_landing(landing(basis="human"))

    assert (findings, caveats) == ((), ())


def test_a_row_with_no_permission_record_at_all_is_skipped_rather_than_crashing() -> None:
    """The production ledger holds one such row -- an acceptance probe from the 0022 migration --
    and observations are append-only, so a detector that cannot read it is a detector that cannot
    run."""
    assert audit_landing({"probe": "landing-type-acceptance"}) == ((), (), ())
    assert audit_landing(None) == ((), (), ())
    assert audit_landing({"permitted_by": "not a mapping"}) == ((), (), ())


def test_a_landing_that_changed_the_gate_is_flagged_as_judged_by_its_own_change() -> None:
    """The ledger reads the gate at the LANDING commit, so a pull request that edits the gate is
    pinned to the rule it installed rather than the one that armed it. That is a caveat on the
    audit's own evidence, not a violation -- and it is real: factory-runner#42 is exactly this.
    """
    findings, caveats, _ = audit_landing(landing(revision=NEWER_METADATA, files=[GATE_PATH]))

    assert kinds(findings) == []
    assert kinds(caveats) == [CAVEAT_RULE_SELF_MODIFIED]


# ---------------------------------------------------------------------------------------------
# Detector B -- the quiet gate.
# ---------------------------------------------------------------------------------------------


# The default title STATES A SINGLE DELTA, and that is load-bearing rather than decoration. From
# 2026-08-23 the title is what separates an open update nothing can classify from one the gate
# should have been able to decide, so a fixture whose title stated no delta would make every
# metadata-unreadable case an exception and leave the finding untested.
CLASSIFIABLE_TITLE = "chore(deps): bump ruff from 0.15.20 to 0.15.21"

# The two shapes that state no single delta, both measured on live subjects: a requirement range
# (orchestrator#174, and the same setuptools bump open on three more repositories) and a docker
# tag that is not semver (orchestrator#3). A grouped bump is the third and is covered in the
# parser's own corpus.
RANGE_TITLE = "chore(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0"
DOCKER_TITLE = "chore(deps): bump python from 3.12-slim to 3.14-slim"


def pending(
    *,
    number: int = 31,
    armed: bool = False,
    update_type: str | None = MINOR,
    ecosystem: str = "uv",
    conclusions: tuple[str, ...] = ("success",),
    concluded_at: datetime | None = NOW - timedelta(days=1),
    title: str = CLASSIFIABLE_TITLE,
    metadata: bool = True,
) -> PendingUpdate:
    return PendingUpdate(
        repository=REPO,
        number=number,
        head_commit="a" * 40,
        opened_at=NOW - timedelta(days=8),
        armed=armed,
        title=title,
        checks=tuple(
            Check(name=f"job{index}", conclusion=value, run=index)
            for index, value in enumerate(conclusions)
        ),
        # `metadata` and `update_type` are two switches because the reader draws two lines. A
        # requirement range carries a dependency name and no update type, so it arrives as
        # metadata whose `update_type` is None; only a head commit with no readable trailer at
        # all arrives as no metadata. Before 2026-08-28 the reader collapsed them.
        update=(
            UpdateMetadata(dependency="ruff", ecosystem=ecosystem, update_type=update_type)
            if metadata
            else None
        ),
        last_concluded_at=concluded_at,
    )


def test_eligible_green_and_unarmed_FIRES() -> None:
    """The quiet gate. It is what a rule that stopped arming looks like, and it is also what a
    sibling disarmed by the one that landed first looks like -- the two known generators present
    identically, which is why one detector covers both."""
    rule = REGISTRY[UNDERSCORED]

    findings, exceptions = audit_pending(pending(), rule, NOW)

    assert kinds(findings) == [STALL_ELIGIBLE_NOT_ARMED]
    assert exceptions == ()


def test_eligible_but_red_is_the_checks_doing_their_job() -> None:
    rule = REGISTRY[UNDERSCORED]

    assert audit_pending(pending(conclusions=("success", "failure")), rule, NOW) == ((), ())


def test_a_package_major_left_unarmed_is_the_rule_declining_to_act() -> None:
    """The discriminator. infraops-mcp-server#4 and #5 are real instances: npm majors, green,
    unarmed, and correctly so."""
    rule = REGISTRY[UNDERSCORED]

    result = audit_pending(pending(update_type=MAJOR, ecosystem="npm_and_yarn"), rule, NOW)

    assert result == ((), ())


def test_an_actions_major_left_unarmed_FIRES_under_the_rule_that_permits_it() -> None:
    rule = REGISTRY[UNDERSCORED]

    findings, exceptions = audit_pending(
        pending(update_type=MAJOR, ecosystem="github_actions"), rule, NOW
    )

    assert kinds(findings) == [STALL_ELIGIBLE_NOT_ARMED]
    assert exceptions == ()


def test_armed_and_green_but_only_just_is_a_landing_about_to_happen() -> None:
    rule = REGISTRY[UNDERSCORED]

    result = audit_pending(pending(armed=True, concluded_at=NOW - timedelta(seconds=5)), rule, NOW)

    assert result == ((), ())


def test_armed_and_green_for_an_hour_and_still_open_FIRES() -> None:
    """The purest form of the question: nothing is stopping it and it is not landing."""
    rule = REGISTRY[UNDERSCORED]

    findings, exceptions = audit_pending(
        pending(armed=True, concluded_at=NOW - timedelta(hours=6)), rule, NOW
    )

    assert kinds(findings) == [STALL_ARMED_NOT_LANDED]
    assert exceptions == ()


def test_an_unreadable_update_whose_title_STATES_a_delta_FIRES() -> None:
    """THE DISCRIMINATOR for the exception below, and without it that change is a suppression.

    The bot names a version delta this gate could have decided, and its metadata trailer is
    missing anyway -- so the audit genuinely cannot say whether the gate should have armed it,
    and that is a finding a person can act on. It is exactly the condition
    `update_metadata_unreadable` was named for.
    """
    rule = REGISTRY[UNDERSCORED]

    findings, exceptions = audit_pending(pending(metadata=False), rule, NOW)

    assert kinds(findings) == [STALL_METADATA_UNREADABLE]
    assert exceptions == ()


@pytest.mark.parametrize("title", [RANGE_TITLE, DOCKER_TITLE])
def test_a_title_stating_no_single_delta_is_an_EXCEPTION_rather_than_a_finding(title) -> None:
    """The seven subjects that made this detector exit non-zero every night.

    A requirement range states two ranges rather than a delta; a docker tag is not semver at all.
    Neither states something any rule about update types could be applied to, which is a property
    of the subject and not a defect anywhere -- the landing lane already classifies the identical
    condition as an exception, and this is the second control looking at the same pull requests.
    """
    rule = REGISTRY[UNDERSCORED]

    findings, exceptions = audit_pending(pending(metadata=False, title=title), rule, NOW)

    assert findings == ()
    assert kinds(exceptions) == [EXCEPTION_UPDATE_TYPE_UNPARSEABLE]
    assert exceptions[0].subject == f"{REPO}#31"


def test_the_title_is_not_consulted_when_the_METADATA_is_readable() -> None:
    """The bound on the new question, and it is the one that keeps this detector about the GATE.

    The gate reads the metadata trailer and never the title. So an update whose trailer says
    `semver-minor` is decided in the gate's own terms however its title reads -- here it is
    permitted, green and unarmed, which is a stall and stays one.
    """
    rule = REGISTRY[UNDERSCORED]

    findings, exceptions = audit_pending(pending(title=RANGE_TITLE), rule, NOW)

    assert kinds(findings) == [STALL_ELIGIBLE_NOT_ARMED]
    assert exceptions == ()


def test_a_head_with_nothing_concluded_is_not_green() -> None:
    assert not is_green(pending(conclusions=()))
    assert audit_pending(pending(conclusions=()), REGISTRY[UNDERSCORED], NOW) == ((), ())


# ---------------------------------------------------------------------------------------------
# Detector A, factory half -- ADR-0020.
# ---------------------------------------------------------------------------------------------


def test_the_landing_the_factory_actually_made_is_not_a_finding() -> None:
    """The real 2026-08-10 landing, as production holds it. If this ever fires, the estate's one
    autonomous merge has stopped reconciling with the orchestrator's own record of making it."""
    assert audit_factory_landing(factory_landing(), units()) == ((), ())


def test_a_landing_on_any_other_basis_is_not_this_detectors_subject() -> None:
    for basis in ("auto_merge_rule", "human", "none", "unattributed"):
        facts = factory_landing()
        facts["permitted_by"]["basis"] = basis
        assert audit_factory_landing(facts, NO_UNITS) == ((), ())


def test_a_factory_landing_naming_a_unit_the_orchestrator_does_not_hold_FIRES() -> None:
    """The claim is read from a commit the runner wrote. A unit that does not exist is the
    cheapest possible way for that claim to be false, and it must not read as a lookup failure."""
    findings, _ = audit_factory_landing(factory_landing(), NO_UNITS)

    assert kinds(findings) == [FACTORY_UNIT_UNKNOWN]
    assert UNIT in findings[0].detail


def test_a_factory_landing_naming_no_unit_at_all_FIRES() -> None:
    findings, _ = audit_factory_landing(factory_landing(work_unit=None), NO_UNITS)

    assert kinds(findings) == [FACTORY_CLAIM_UNREADABLE]


def test_a_unit_that_is_not_completed_FIRES() -> None:
    findings, _ = audit_factory_landing(factory_landing(), units(pack(state="executing")))

    assert kinds(findings) == [FACTORY_UNIT_NOT_COMPLETED]


def test_a_unit_the_verifier_did_not_decide_FIRES_and_carries_the_reason() -> None:
    """ADR-0020's whole condition: the factory may close the loop exactly when it never had to
    ask. A criterion a person decided is one it asked about."""
    findings, _ = audit_factory_landing(
        factory_landing(),
        units(
            pack(
                decided_by_verifier=False,
                refusals=[{"ac_id": "AC-002", "code": "decision_outside_required_criteria"}],
            )
        ),
    )

    assert kinds(findings) == [FACTORY_NOT_VERIFIER_DECIDED]
    assert "decision_outside_required_criteria" in findings[0].detail


def test_a_unit_whose_evidence_was_attested_rather_than_observed_FIRES() -> None:
    """The second clause is separate from the first and is checked separately: a criterion the
    verifier decided off evidence the WORKER attested to is decided by the verifier and rests on
    the runner's own word."""
    findings, _ = audit_factory_landing(factory_landing(), units(pack(evidence_observed=False)))

    assert kinds(findings) == [FACTORY_NOT_VERIFIER_DECIDED]


def test_a_current_adjudication_decided_by_anyone_but_the_verifier_FIRES() -> None:
    """The independent reading. It walks the primary rows rather than re-reading the composed
    answer above, so a composed answer that is wrong about something visible is still caught."""
    findings, _ = audit_factory_landing(
        factory_landing(),
        units(pack(decided_by_verifier=True, decided_by_role="human")),
    )

    assert kinds(findings) == [FACTORY_HUMAN_ADJUDICATION]
    assert "human" in findings[0].detail


def test_an_unrecorded_decider_is_refused_rather_than_read_as_consent() -> None:
    """NULL is the historical rows' value and is never evidence that a machine decided. It is
    reported as `unrecorded` -- the word the evidence pack's own markdown uses for it -- so a
    reader does not meet two spellings of one absence, and never as a bare `None`, which reads
    like a role somebody chose."""
    findings, _ = audit_factory_landing(factory_landing(), units(pack(decided_by_role=None)))

    assert kinds(findings) == [FACTORY_HUMAN_ADJUDICATION]
    assert findings[0].detail.endswith("unrecorded")


def test_a_landing_the_named_unit_holds_no_record_of_making_FIRES() -> None:
    """The binding, and the finding that matters most: without it the claim selects any completed,
    verifier-decided unit in the estate and the audit reports on that one instead."""
    findings, _ = audit_factory_landing(factory_landing(), units(unit_history=history(pr_number=9)))

    assert kinds(findings) == [FACTORY_LANDING_UNBOUND]
    assert "66" in findings[0].detail


def test_a_record_of_landing_the_same_pull_request_in_another_repository_does_not_bind() -> None:
    findings, _ = audit_factory_landing(
        factory_landing(), units(unit_history=history(repository="AlobarQuest/orchestrator"))
    )

    assert kinds(findings) == [FACTORY_LANDING_UNBOUND]


def test_a_record_naming_a_different_merge_commit_FIRES() -> None:
    findings, _ = audit_factory_landing(
        factory_landing(), units(unit_history=history(merge_commit="c" * 40))
    )

    assert kinds(findings) == [FACTORY_LANDING_UNBOUND]
    assert "cccccccccccc" in findings[0].detail


def test_only_a_MERGED_record_asserts_the_orchestrator_made_this_landing() -> None:
    """`merged` is the one status meaning "we called, the remote said merged, here is the commit".
    Neither other status means what its name suggests, and each has TWO writers in `pr_merge.py`:
    `already_merged` fires for the lost-response retry (our act) AND, before the call is made, for
    a pull request somebody else had already landed -- "somebody else's act, never as ours" in its
    own words; `refused` fires for the genuinely ambiguous outcome AND for a confirmed
    non-landing. Neither can carry authorship, so a landing whose only record is one of them is
    reported rather than excused.
    """
    for status in ("already_merged", "refused"):
        findings, caveats = audit_factory_landing(
            factory_landing(), units(unit_history=history(status=status, merge_commit=None))
        )
        assert kinds(findings) == [FACTORY_LANDING_UNCLAIMED], status
        assert status in findings[0].detail
        assert caveats == (), status


def test_nothing_about_a_factory_landing_is_reported_as_a_CAVEAT() -> None:
    """A caveat drives no exit code, so it is where a doubt goes to be ignored. Every doubt about
    an act this estate cannot undo belongs in the lane a person actually reads."""
    for unit_history in (
        history(),
        history(status="already_merged", merge_commit=None),
        history(status="refused"),
        history(merge_commit="c" * 40),
        history(pr_number=9),
    ):
        assert audit_factory_landing(factory_landing(), units(unit_history=unit_history))[1] == ()


def test_a_record_of_a_DIFFERENT_pull_request_does_not_bind_however_it_ended() -> None:
    """Recognising all three statuses is about what the row SAYS, never about which pull request
    it names. All three bind only the repository and pull request actually recorded."""
    for status in ("merged", "already_merged", "refused"):
        findings, _ = audit_factory_landing(
            factory_landing(), units(unit_history=history(status=status, pr_number=9))
        )
        assert kinds(findings) == [FACTORY_LANDING_UNBOUND], status


def test_a_record_naming_a_different_HEAD_FIRES_even_when_it_carries_no_commit() -> None:
    """The head the orchestrator NAMED in its call, which the remote refused anything else for.
    Without it the binding rests on a pull-request NUMBER whenever a status carries no commit --
    and a number says nothing about content, which is exactly the case that needed it.
    """
    findings, _ = audit_factory_landing(
        factory_landing(),
        units(unit_history=history(status="already_merged", merge_commit=None, head_sha="d" * 40)),
    )

    assert set(kinds(findings)) == {FACTORY_LANDING_UNCLAIMED, FACTORY_LANDING_UNBOUND}
    assert "dddddddddddd" in findings[-1].detail


def test_a_landing_made_under_an_authority_the_unit_no_longer_carries_FIRES() -> None:
    findings, _ = audit_factory_landing(
        factory_landing(), units(unit_history=history(fingerprint="f" * 64))
    )

    assert kinds(findings) == [FACTORY_FINGERPRINT_MISMATCH]


def test_an_orchestrator_that_could_not_be_asked_is_NOT_reported_as_a_finding() -> None:
    """It raises through, so the caller reaches the incomplete exit code. Swallowing it here would
    report a landing as audited on the strength of a question nobody managed to ask."""
    with pytest.raises(LedgerWriteError):
        audit_factory_landing(factory_landing(), UnreachableUnits())


def test_the_factory_half_survives_a_stored_shape_it_did_not_expect() -> None:
    """Stored facts are read back from the orchestrator and are not this module's construction."""
    assert audit_factory_landing(None, NO_UNITS) == ((), ())
    assert audit_factory_landing({"permitted_by": "not a mapping"}, NO_UNITS) == ((), ())


def test_a_claim_that_cannot_NAME_a_unit_is_a_finding_rather_than_an_unmeasured_repository() -> (
    None
):
    """The shape is checked here and not left to the client, and the two lanes are the reason.
    The client refuses an unreadable path with an error `audit_pass` catches as UNAVAILABLE, so a
    single malformed stored row would report the WHOLE REPOSITORY as unmeasured -- which is the
    fail-mode inversion of what this landing deserves.
    """
    for value in (12, "", "not-a-uuid", UNIT.upper(), f"{UNIT}/evidence-pack", None):
        findings, _ = audit_factory_landing(factory_landing(work_unit=value), NO_UNITS)
        assert kinds(findings) == [FACTORY_CLAIM_UNREADABLE], value


# ---------------------------------------------------------------------------------------------
# One repository, both detectors, and the two repository-level answers.
# ---------------------------------------------------------------------------------------------


def test_a_factory_landing_is_counted_under_its_own_denominator() -> None:
    """Two subjects, two denominators. Folding factory landings into `rule_permitted_landings`
    would put them behind a key whose name says something else."""
    audit = audit_repository(
        repository=FACTORY_REPO,
        landings=[factory_landing(), landing()],
        pending=(),
        rule_revision=UNDERSCORED,
        units=units(),
        now=NOW,
        branch=GREEN,
    )

    assert audit.findings == ()
    assert (audit.permitted_landings, audit.factory_landings) == (1, 1)
    assert "1 factory landing(s)" in audit_observation(audit, "20260810T120000Z", NOW)["summary"]


def test_a_repository_with_an_untranscribed_installed_rule_FIRES_once_for_the_repository() -> None:
    """Fail closed: if the installed rule is unknown, no open update here can be classified, and
    saying nothing would be a clean answer computed from no knowledge."""
    audit = audit_repository(
        repository=REPO,
        landings=[],
        pending=(pending(),),
        rule_revision="e" * 40,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    assert kinds(audit.findings) == [STALL_RULE_UNKNOWN]


def test_a_repository_with_no_rule_installed_is_a_caveat_with_its_numbers() -> None:
    """Three repositories deliberately have no gate, and one of them CANNOT have one -- this
    repository's own architecture guards forbid the command it would run. Reporting that as a
    violation would make the detector permanently red about a scope decision somebody made. The
    green-and-unlanded count is still carried, because it is the fact worth reading.
    """
    audit = audit_repository(
        repository="AlobarQuest/orchestrator",
        landings=[],
        pending=(pending(), pending(number=2, conclusions=("failure",))),
        rule_revision=None,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    assert audit.findings == ()
    assert kinds(audit.caveats) == [CAVEAT_NO_RULE_INSTALLED]
    assert "1 of 2 open updates are green" in audit.caveats[0].detail


def test_the_denominators_are_carried_so_nothing_found_is_never_bare() -> None:
    audit = audit_repository(
        repository=REPO,
        landings=[landing(), landing(basis="human"), landing(basis="none")],
        pending=(pending(armed=True, concluded_at=NOW - timedelta(seconds=1)),),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    assert audit.findings == ()
    assert (audit.landings_audited, audit.permitted_landings, audit.pending_audited) == (3, 1, 1)
    assert audit.severity == "info"


def test_a_finding_raises_the_severity_the_row_is_filed_under() -> None:
    audit = audit_repository(
        repository=REPO,
        landings=[landing(revision=PATCH_AND_MINOR, update_type=MAJOR)],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    assert audit.severity == "warning"


def test_an_exception_reaches_the_repositorys_own_list_and_not_its_findings() -> None:
    """The whole change, at the level the caller reads. A repository whose entire open queue is
    unclassifiable is QUIET -- it says so in its own list, not in its severity and not in the
    exit code the caller computes from `findings`.
    """
    audit = audit_repository(
        repository=REPO,
        landings=[],
        pending=(
            pending(metadata=False, title=RANGE_TITLE),
            pending(number=32, metadata=False, title=DOCKER_TITLE),
        ),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    assert audit.findings == ()
    assert kinds(audit.exceptions) == [EXCEPTION_UPDATE_TYPE_UNPARSEABLE] * 2
    assert audit.severity == "info"


def test_an_exception_does_NOT_silence_a_real_finding_beside_it() -> None:
    """The over-general version of this rule would make a repository quiet once one of its open
    updates could not be classified. Both are reported, each under its own category."""
    audit = audit_repository(
        repository=REPO,
        landings=[],
        pending=(pending(metadata=False, title=RANGE_TITLE), pending(number=32)),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    assert kinds(audit.findings) == [STALL_ELIGIBLE_NOT_ARMED]
    assert kinds(audit.exceptions) == [EXCEPTION_UPDATE_TYPE_UNPARSEABLE]
    assert audit.severity == "warning"


# ---------------------------------------------------------------------------------------------
# The record the pass files.
# ---------------------------------------------------------------------------------------------


def test_the_heartbeat_row_is_written_even_when_nothing_was_found() -> None:
    """A detector that writes only on a finding is indistinguishable from one that has stopped
    running. That is the failure this whole increment exists to catch, so the row is the pass's
    own evidence that it ran and the findings are its content."""
    audit = audit_repository(
        repository=REPO,
        landings=[landing()],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    body = audit_observation(audit, "20260808T120000Z", NOW)

    assert body["observation_type"] == "landing_audit"
    assert body["facts"]["findings_found"] == 0
    assert body["facts"]["rule_permitted_landings"] == 1
    assert body["severity"] == "info"


def test_one_pass_answering_its_own_id_twice_the_same_way_replays() -> None:
    audit = audit_repository(
        repository=REPO,
        landings=[landing()],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    first = audit_observation(audit, "20260808T120000Z", NOW)
    second = audit_observation(audit, "20260808T120000Z", NOW)

    assert first["idempotency_key"] == second["idempotency_key"]


def test_one_pass_answering_its_own_id_DIFFERENTLY_is_loud_rather_than_a_second_row() -> None:
    """Same source reference, different facts, which is the orchestrator's conflict branch. A
    moment cannot have two answers, and the ledger's rule is that facts which drift are loud."""
    clean = audit_repository(
        repository=REPO,
        landings=[landing()],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )
    drifted = audit_repository(
        repository=REPO,
        landings=[landing(revision=PATCH_AND_MINOR, update_type=MAJOR)],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    first = audit_observation(clean, "20260808T120000Z", NOW)
    second = audit_observation(drifted, "20260808T120000Z", NOW)

    assert first["source_reference"] == second["source_reference"]
    assert first["idempotency_key"] != second["idempotency_key"]


def test_a_flood_of_findings_is_trimmed_to_fit_with_its_true_count_beside_it() -> None:
    """The orchestrator bounds facts at 4096 ENCODED bytes and rejects anything larger, so an
    unbounded list would make a bad day the day the detector stops being able to report.

    Measured on the encoded bytes, and with findings long enough that the per-list cap alone does
    not save it: a first version asserted only that fewer entries survived than went in, which the
    20-entry cap satisfies on its own -- so the byte trim could be deleted entirely and the test
    still passed.
    """
    audit = audit_repository(
        repository="AlobarQuest/a-repository-with-a-name-long-enough-to-fill-the-record",
        landings=[
            landing(revision=PATCH_AND_MINOR, update_type=MAJOR, ecosystem="e" * 200)
            for _ in range(200)
        ],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    body = audit_observation(audit, "20260808T120000Z", NOW)

    encoded = json.dumps(body["facts"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert body["facts"]["findings_found"] == 200
    assert 0 < len(body["facts"]["findings"]) < MAX_LIST
    assert len(encoded) <= 4096


def test_an_exception_is_recorded_in_the_row_with_its_own_count() -> None:
    """A quiet category still has to be written down, or it is a category that was suppressed."""
    audit = audit_repository(
        repository=REPO,
        landings=[],
        pending=(pending(metadata=False, title=RANGE_TITLE),),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    body = audit_observation(audit, "20260808T120000Z", NOW)

    assert body["facts"]["exceptions_found"] == 1
    assert body["facts"]["exceptions"][0]["kind"] == EXCEPTION_UPDATE_TYPE_UNPARSEABLE
    assert body["facts"]["findings_found"] == 0
    assert body["severity"] == "info"
    assert "0 finding(s) and 1 exception(s)" in body["summary"]


def test_exceptions_are_dropped_before_caveats_when_the_record_will_not_fit() -> None:
    """Least urgent first: an exception is permanent, so it says the same thing tomorrow.

    Enough long-named exceptions to blow the byte bound on their own, beside one caveat that must
    survive them.
    """
    audit = audit_repository(
        repository=REPO,
        landings=[landing(files=[GATE_PATH], ecosystem="e" * 200)],
        pending=tuple(
            pending(number=index, metadata=False, title=RANGE_TITLE) for index in range(60)
        ),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    body = audit_observation(audit, "20260808T120000Z", NOW)

    assert body["facts"]["exceptions_found"] == 60
    assert body["facts"]["caveats"], "the caveat was trimmed before the exceptions"
    assert len(body["facts"]["exceptions"]) < MAX_LIST


def test_caveats_are_dropped_before_findings_when_the_record_will_not_fit() -> None:
    """A caveat qualifies evidence; a finding asserts a violation. The violation must survive."""
    audit = audit_repository(
        repository=REPO,
        landings=[
            landing(revision=PATCH_AND_MINOR, update_type=MAJOR, ecosystem="e" * 200),
            *[landing(files=[GATE_PATH], ecosystem="e" * 200) for _ in range(60)],
        ],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=GREEN,
    )

    body = audit_observation(audit, "20260808T120000Z", NOW)

    assert body["facts"]["findings"], "the finding was trimmed before the caveats"
    assert len(body["facts"]["caveats"]) < len(body["facts"]["findings"]) + MAX_LIST


# ---------------------------------------------------------------------------------------------
# ADR-0034. Two records that used to look identical, and the detector must answer them apart.
# ---------------------------------------------------------------------------------------------

OUTCOME_RULE = "3457db3cee85ffa054dee8b434ac25238a81f425"


def test_a_landing_stating_no_delta_is_not_a_landing_nobody_could_read() -> None:
    """The distinction the reader started drawing on 2026-08-28, read back at the far end.

    `update_type` PRESENT and null says the update bot declared no version delta, which is the
    ordinary shape of a requirement range and exactly what revision 3457db3c permits. Keyed on
    the VALUE rather than the key, this detector would raise `metadata_missing` against every
    landing the new rule exists to make -- a finding about the ledger, aimed at the population
    the change was for, arriving on the first night it ran.
    """
    findings, _, _ = audit_landing(landing(revision=OUTCOME_RULE, update_type=None))

    assert kinds(findings) == []


def test_a_landing_with_no_readable_trailer_still_FIRES_under_the_outcome_rule() -> None:
    """The other side, so the first test is not just the finding being switched off. All three
    update keys absent means nothing here could read what the gate read, and no rule's condition
    can be re-run against it -- whatever that rule turns out to be."""
    findings, _, _ = audit_landing(landing(revision=OUTCOME_RULE, metadata=False))

    assert kinds(findings) == [DRIFT_METADATA_MISSING]


def test_a_docker_landing_under_the_outcome_rule_still_violates_it() -> None:
    """CONTROL 3 at the landing end. The one exclusion survives the widening: a base image is
    refused whatever it declares, so a docker landing recorded as rule-permitted is drift."""
    findings, _, _ = audit_landing(
        landing(revision=OUTCOME_RULE, update_type=None, ecosystem="docker")
    )

    assert kinds(findings) == [DRIFT_NOT_SATISFIED]


def test_a_requirement_range_is_now_CLASSIFIED_rather_than_excepted() -> None:
    """Detector B, and the interim state this change creates deliberately.

    A range is permitted by the installed rule from 2026-08-28, so it stops being a permanent
    exception and becomes an ordinary subject -- and a green one that nothing armed is the
    quiet-gate finding, which is precisely what an already-open pull request is after a gate
    edit that fires no event. The report is right and the remedy is a synchronize event.
    """
    rule = rule_for(OUTCOME_RULE)
    assert rule is not None

    findings, exceptions = audit_pending(
        pending(update_type=None, title=RANGE_TITLE, armed=False), rule, NOW
    )

    assert kinds(findings) == [STALL_ELIGIBLE_NOT_ARMED]
    assert exceptions == ()
    # A person reads this line. An absent intent is said, not interpolated: `None of ruff is
    # permitted` names a value the update bot never wrote.
    assert "no version delta stated" in findings[0].detail
    assert "None" not in findings[0].detail


def test_a_docker_tag_stating_no_delta_stays_quiet_under_the_outcome_rule() -> None:
    """The rule declining to act is the rule working, and produces neither a finding nor an
    exception. `python 3.12-slim -> 3.14-slim` is this case: excluded, not merely undeclared."""
    rule = rule_for(OUTCOME_RULE)
    assert rule is not None

    findings, exceptions = audit_pending(
        pending(update_type=None, ecosystem="docker", title=DOCKER_TITLE), rule, NOW
    )

    assert findings == () and exceptions == ()


def test_the_no_verdict_vocabulary_matches_the_lane_that_owns_it() -> None:
    """A mirror, pinned by importing both -- the arrangement `titles.py` already uses.

    `bump_proposer` and this module may not import the orchestrator, so the set is duplicated
    rather than shared. A duplicate nobody checks is how two vocabularies drift into disagreeing
    about the same word, which this estate has now found four times.
    """
    from landing_ledger.audit import NO_VERDICT_CONCLUSIONS as MIRROR
    from orchestrator.services.estate_landing_admission import (
        NO_VERDICT_CONCLUSIONS as OWNED,
    )

    assert MIRROR == OWNED


def test_a_conclusion_nobody_enumerated_is_read_as_a_verdict() -> None:
    """The polarity that makes the split safe. A word the platform has not yet invented must
    fail toward "the checks refused this", never toward "no answer yet"."""
    from landing_ledger.audit import NO_VERDICT_CONCLUSIONS, REFUSING_CONCLUSIONS

    assert "failure" in REFUSING_CONCLUSIONS
    assert not (REFUSING_CONCLUSIONS & NO_VERDICT_CONCLUSIONS)
    from landing_ledger.audit import FAILING_CONCLUSIONS

    assert REFUSING_CONCLUSIONS == FAILING_CONCLUSIONS - NO_VERDICT_CONCLUSIONS


# ---------------------------------------------------------------------------------------------
# Detector C: is the default branch green NOW? The whole of it is the three-state distinction --
# a red tip is a finding, and a tip nothing has decided on yet is the ordinary state.
# ---------------------------------------------------------------------------------------------

TIP = "d" * 40
QUALITY = ".github/workflows/quality.yml"
RELEASE = ".github/workflows/release.yml"
AT = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)


def run(
    *,
    path: str = QUALITY,
    identifier: int = 1,
    status: str = "completed",
    conclusion: str | None = "success",
    at: datetime = AT,
) -> WorkflowRun:
    return WorkflowRun(
        path=path, run=identifier, status=status, conclusion=conclusion, updated_at=at
    )


def test_a_tip_whose_workflow_decided_against_it_is_FAILING() -> None:
    status = branch_status(TIP, (run(conclusion="failure"),))

    assert (status.state, status.failing) == (BRANCH_FAILING, (QUALITY,))


def test_a_tip_every_workflow_passed_is_PASSING() -> None:
    assert branch_status(TIP, (run(),)).state == BRANCH_PASSING


def test_a_tip_with_a_run_still_going_and_nothing_decided_is_IN_FLIGHT() -> None:
    """NOT a finding. Under the current arming identity a landing fires no `push` run at all, so
    an undecided tip is the ordinary state for hours -- reporting it would red this control
    permanently, which is the failure mode this estate has already paid for twice."""
    status = branch_status(TIP, (run(status="in_progress", conclusion=None),))

    assert (status.state, status.in_flight, status.failing) == (BRANCH_IN_FLIGHT, (QUALITY,), ())


def test_a_DECIDED_failure_outranks_a_run_still_going() -> None:
    """One workflow red while another is still running is a red branch, not an undecided one.

    The states are ordered, and this is the pair that pins the order: a tip that has already been
    decided against does not become quiet because something else has not finished.
    """
    status = branch_status(
        TIP,
        (
            run(path=QUALITY, identifier=1, conclusion="failure"),
            run(path=RELEASE, identifier=2, status="in_progress", conclusion=None),
        ),
    )

    assert status.state == BRANCH_FAILING
    assert (status.failing, status.in_flight) == ((QUALITY,), (RELEASE,))


def test_a_tip_nothing_ran_on_at_all_is_UNVERIFIED() -> None:
    """A repository with no workflows answers this, and so does one whose only runs were
    cancelled. Nothing measured the tip, so nothing can say it is green."""
    assert branch_status(TIP, ()).state == BRANCH_UNVERIFIED
    assert branch_status(TIP, (run(conclusion="cancelled"),)).state == BRANCH_UNVERIFIED


def test_a_newer_GREEN_workflow_cannot_hide_an_older_RED_one() -> None:
    """The reason the reduction is per workflow and then across workflows. "The newest concluded
    run at the tip" reads as one run, and one run is the wrong unit: a repository runs several
    workflows over one commit, so a single-run reading reports a broken branch as healthy."""
    status = branch_status(
        TIP,
        (
            run(path=QUALITY, identifier=1, conclusion="failure", at=AT),
            run(path=RELEASE, identifier=2, conclusion="success", at=AT + timedelta(hours=1)),
        ),
    )

    assert status.state == BRANCH_FAILING
    assert (status.failing, status.passing) == ((QUALITY,), (RELEASE,))


def test_a_workflow_re_run_to_green_is_green() -> None:
    """Is this commit green NOW is the question, and a re-run is the answer to it. A record of
    every attempt would be the answer to a different one."""
    status = branch_status(
        TIP,
        (
            run(identifier=1, conclusion="failure", at=AT),
            run(identifier=2, conclusion="success", at=AT + timedelta(minutes=5)),
        ),
    )

    assert status.state == BRANCH_PASSING


def test_a_CANCELLED_run_cannot_bury_the_verdict_it_superseded() -> None:
    """THE PAIR IS THE CONTROL, and the failure case is the half that discriminates.

    A cancelled run must not become a workflow's current answer. Over a SUCCESS that reads the
    same either way -- a cancelled newest lands in `passing` too -- so a test written only that
    way cannot see the skip being deleted. Over a FAILURE the two answers differ: skipped, the
    branch is still red; counted, the branch reports as fine, which is exactly what held three
    clean bumps for four days when the Actions quota ran out.
    """
    over_success = branch_status(
        TIP,
        (
            run(identifier=1, conclusion="success", at=AT),
            run(identifier=2, conclusion="cancelled", at=AT + timedelta(hours=1)),
        ),
    )
    over_failure = branch_status(
        TIP,
        (
            run(identifier=1, conclusion="failure", at=AT),
            run(identifier=2, conclusion="cancelled", at=AT + timedelta(hours=1)),
        ),
    )

    assert over_success.state == BRANCH_PASSING
    assert over_failure.state == BRANCH_FAILING


def test_a_conclusion_neither_vocabulary_knows_leaves_the_tip_unaccused() -> None:
    """Fail toward quiet, which is the right direction for a finding a person acts on. A word the
    platform has not yet invented must not become an assertion that `main` is broken."""
    status = branch_status(TIP, (run(conclusion="a_word_github_has_not_invented"),))

    assert status.state == BRANCH_PASSING and status.failing == ()


def test_only_a_FAILING_tip_produces_a_finding() -> None:
    red = BranchStatus(commit=TIP, state=BRANCH_FAILING, failing=(QUALITY,))

    assert kinds(audit_branch(REPO, red)) == [BRANCH_NOT_GREEN]
    assert audit_branch(REPO, BranchStatus(commit=TIP, state=BRANCH_PASSING)) == ()
    assert audit_branch(REPO, BranchStatus(commit=TIP, state=BRANCH_IN_FLIGHT)) == ()
    assert audit_branch(REPO, BranchStatus(commit=TIP, state=BRANCH_UNVERIFIED)) == ()


def test_a_red_default_branch_reaches_the_repositorys_findings_and_its_severity() -> None:
    audit = audit_repository(
        repository=REPO,
        landings=[],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=BranchStatus(commit=TIP, state=BRANCH_FAILING, failing=(QUALITY,)),
    )

    assert kinds(audit.findings) == [BRANCH_NOT_GREEN]
    assert audit.severity == "warning"
    assert not audit.unavailable


def test_a_branch_nobody_could_ask_about_is_UNAVAILABLE_and_never_a_pass() -> None:
    """The measurement did not happen, so the repository's answer is missing rather than clean.
    Everything the same pass DID measure survives -- the landings and open updates needed no
    branch read at all, and discarding them would make one unreadable question cost four."""
    audit = audit_repository(
        repository=REPO,
        landings=[landing(revision=PATCH_AND_MINOR, update_type=MAJOR)],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=None,
    )

    assert audit.unavailable
    assert audit.severity == "warning"
    assert kinds(audit.findings) == [DRIFT_NOT_SATISFIED]
    assert audit.branch is None


def test_the_quiet_branch_answers_are_RECORDED_even_though_they_are_not_findings() -> None:
    """A caveat prints a line every night for every repository whose tip nothing has decided on,
    which is a report known to be noise and therefore a report a real finding arrives inside. The
    observation carries the state instead, so a reader can still see which quiet answer it was."""
    audit = audit_repository(
        repository=REPO,
        landings=[],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=BranchStatus(commit=TIP, state=BRANCH_IN_FLIGHT, in_flight=(QUALITY,)),
    )
    facts = audit_observation(audit, "20260808T120000Z", NOW)

    assert audit.findings == () and audit.caveats == ()
    assert facts["facts"]["default_branch"] == {
        "commit": TIP,
        "state": BRANCH_IN_FLIGHT,
        "failing": [],
        "passing": [],
        "in_flight": [QUALITY],
    }
    assert "default branch in_flight" in facts["summary"]


def test_a_huge_branch_block_is_trimmed_BEFORE_the_findings_it_would_otherwise_evict() -> None:
    """`_fit`'s contract, against the block this increment added to its fixed portion.

    A block the trim loop cannot reach evicts findings on its own behalf -- the inversion `_fit`'s
    docstring forbids -- and, once the entry lists empty, falls out of the loop still oversized,
    which the orchestrator refuses. Both halves are asserted: the record fits, and the finding is
    still in it.
    """
    audit = audit_repository(
        repository=REPO,
        landings=[landing(revision=PATCH_AND_MINOR, update_type=MAJOR)],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=BranchStatus(
            commit=TIP,
            state=BRANCH_FAILING,
            failing=tuple(
                f".github/workflows/{'w' * 200}-{index}.yml" for index in range(MAX_LIST)
            ),
            passing=tuple(
                f".github/workflows/{'p' * 200}-{index}.yml" for index in range(MAX_LIST)
            ),
        ),
    )
    facts = audit_observation(audit, "20260808T120000Z", NOW)

    assert len(json.dumps(facts["facts"], sort_keys=True, separators=(",", ":"))) <= 4096
    assert kinds(audit.findings) == [BRANCH_NOT_GREEN, DRIFT_NOT_SATISFIED]
    assert [entry["kind"] for entry in facts["facts"]["findings"]] == [
        BRANCH_NOT_GREEN,
        DRIFT_NOT_SATISFIED,
    ]
    assert facts["facts"]["default_branch"]["state"] == BRANCH_FAILING


def test_an_unread_branch_records_null_rather_than_an_answer() -> None:
    audit = audit_repository(
        repository=REPO,
        landings=[],
        pending=(),
        rule_revision=UNDERSCORED,
        units=NO_UNITS,
        now=NOW,
        branch=None,
    )
    facts = audit_observation(audit, "20260808T120000Z", NOW)

    assert facts["facts"]["default_branch"] is None
    assert "default branch unread" in facts["summary"]
    assert "[UNAVAILABLE]" in facts["summary"]
