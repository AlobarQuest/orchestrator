"""The two detectors: each shown to FIRE on a constructed condition, and not to on a healthy one.

Every landing fixture below is shaped like the rows the production ledger actually holds -- the
six rule-permitted landings of 2026-08-07, whose facts were read back through
`GET /api/v1/observations?observation_type=landing` while this was written.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from landing_ledger.audit import (
    CAVEAT_NO_RULE_INSTALLED,
    CAVEAT_RULE_SELF_MODIFIED,
    DRIFT_CHECK_NOT_GREEN,
    DRIFT_METADATA_MISSING,
    DRIFT_NOT_SATISFIED,
    DRIFT_RULE_DID_NOT_SUCCEED,
    DRIFT_RULE_MISSING,
    DRIFT_RULE_UNKNOWN,
    STALL_ARMED_NOT_LANDED,
    STALL_ELIGIBLE_NOT_ARMED,
    STALL_METADATA_UNREADABLE,
    STALL_RULE_UNKNOWN,
    audit_landing,
    audit_observation,
    audit_pending,
    audit_repository,
    is_green,
)
from landing_ledger.model import Check, PendingUpdate, UpdateMetadata
from landing_ledger.rules import GATE_PATH, REGISTRY

REPO = "AlobarQuest/factory-runner"
PATCH_AND_MINOR = "77ab867d1080d18baea3a2b230655c2729716970"
HYPHENATED = "4d87d9b7465e3b59bd9bdee2086de18eb1cab1dd"
UNDERSCORED = "12880ce77ab97c3f4d9281195041eed8c5d52609"
NEWER_METADATA = "43e37ed97823aec25cc5bac63f636914637e219c"

MAJOR = "version-update:semver-major"
MINOR = "version-update:semver-minor"

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def landing(
    *,
    basis: str = "auto_merge_rule",
    revision: str | None = UNDERSCORED,
    outcome: str = "success",
    update_type: str | None = MINOR,
    ecosystem: str | None = "uv",
    checks: list[dict[str, Any]] | None = None,
    files: list[str] | None = None,
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
        if update_type is None:
            del permitted["update_type"]
    return {
        "what_changed": {
            "repository": REPO,
            "base_ref": "main",
            "commit": "d5e31dc1164f9d0a" + "0" * 24,
            "files": files if files is not None else ["uv.lock"],
            "files_changed": 1,
            "pull_request": 30,
        },
        "permitted_by": permitted,
    }


def kinds(findings: Any) -> list[str]:
    return [finding.kind for finding in findings]


# ---------------------------------------------------------------------------------------------
# Detector A -- permissive drift.
# ---------------------------------------------------------------------------------------------


def test_a_landing_within_its_own_pinned_rule_is_not_a_finding() -> None:
    findings, caveats = audit_landing(landing())

    assert (findings, caveats) == ((), ())


def test_a_major_bump_under_the_patch_and_minor_rule_FIRES() -> None:
    """The plain permissive drift: the gate landed something its own rule excluded."""
    findings, _ = audit_landing(landing(revision=PATCH_AND_MINOR, update_type=MAJOR))

    assert kinds(findings) == [DRIFT_NOT_SATISFIED]


def test_the_SAME_facts_pass_or_fail_on_the_pinned_revision_alone() -> None:
    """The discriminator that makes the whole design worth having.

    An Actions major is inside the corrected rule and outside the hyphenated one, and NOTHING
    about the landing distinguishes the two cases except which revision was pinned to it. A
    detector that judged every landing against today's rule would report the second as fine.
    """
    within, _ = audit_landing(
        landing(revision=UNDERSCORED, update_type=MAJOR, ecosystem="github_actions")
    )
    outside, _ = audit_landing(
        landing(revision=HYPHENATED, update_type=MAJOR, ecosystem="github_actions")
    )

    assert kinds(within) == []
    assert kinds(outside) == [DRIFT_NOT_SATISFIED]


def test_an_untranscribed_rule_revision_FIRES_rather_than_passing() -> None:
    findings, _ = audit_landing(landing(revision="f" * 40))

    assert kinds(findings) == [DRIFT_RULE_UNKNOWN]


def test_a_rule_basis_with_no_rule_pinned_FIRES() -> None:
    findings, _ = audit_landing(landing(revision=None))

    assert kinds(findings) == [DRIFT_RULE_MISSING]


def test_absent_update_metadata_FIRES_rather_than_being_read_as_ineligible() -> None:
    """The rule's own job-level condition is "the update bot raised this", and the trailer is the
    only proxy the ledger holds for it. Absent, the condition cannot be re-read -- which is a
    finding, not a quiet pass and not a rule violation."""
    findings, _ = audit_landing(landing(update_type=None))

    assert kinds(findings) == [DRIFT_METADATA_MISSING]


def test_a_recorded_failing_check_FIRES() -> None:
    findings, _ = audit_landing(landing(checks=[{"name": "Quality", "conclusion": "failure"}]))

    assert kinds(findings) == [DRIFT_CHECK_NOT_GREEN]


def test_a_skipped_check_is_not_a_failing_check() -> None:
    """A conditional job that did not run is neither pass nor failure. Counting it as red would
    make every repository with a conditional job a permanent finding -- and a permanently red
    signal is one nobody reads."""
    findings, _ = audit_landing(
        landing(
            checks=[
                {"name": "deploy", "conclusion": "skipped"},
                {"name": "q", "conclusion": "neutral"},
            ]
        )
    )

    assert kinds(findings) == []


def test_a_rule_run_that_did_not_succeed_FIRES() -> None:
    findings, _ = audit_landing(landing(outcome="failure"))

    assert kinds(findings) == [DRIFT_RULE_DID_NOT_SUCCEED]


def test_a_landing_a_person_decided_is_not_this_detectors_subject() -> None:
    findings, caveats = audit_landing(landing(basis="human"))

    assert (findings, caveats) == ((), ())


def test_a_row_with_no_permission_record_at_all_is_skipped_rather_than_crashing() -> None:
    """The production ledger holds one such row -- an acceptance probe from the 0022 migration --
    and observations are append-only, so a detector that cannot read it is a detector that cannot
    run."""
    assert audit_landing({"probe": "landing-type-acceptance"}) == ((), ())
    assert audit_landing(None) == ((), ())
    assert audit_landing({"permitted_by": "not a mapping"}) == ((), ())


def test_a_landing_that_changed_the_gate_is_flagged_as_judged_by_its_own_change() -> None:
    """The ledger reads the gate at the LANDING commit, so a pull request that edits the gate is
    pinned to the rule it installed rather than the one that armed it. That is a caveat on the
    audit's own evidence, not a violation -- and it is real: factory-runner#42 is exactly this.
    """
    findings, caveats = audit_landing(landing(revision=NEWER_METADATA, files=[GATE_PATH]))

    assert kinds(findings) == []
    assert kinds(caveats) == [CAVEAT_RULE_SELF_MODIFIED]


# ---------------------------------------------------------------------------------------------
# Detector B -- the quiet gate.
# ---------------------------------------------------------------------------------------------


def pending(
    *,
    number: int = 31,
    armed: bool = False,
    update_type: str | None = MINOR,
    ecosystem: str = "uv",
    conclusions: tuple[str, ...] = ("success",),
    concluded_at: datetime | None = NOW - timedelta(days=1),
) -> PendingUpdate:
    return PendingUpdate(
        repository=REPO,
        number=number,
        head_commit="a" * 40,
        opened_at=NOW - timedelta(days=8),
        armed=armed,
        title="chore(deps): bump ruff",
        checks=tuple(
            Check(name=f"job{index}", conclusion=value, run=index)
            for index, value in enumerate(conclusions)
        ),
        update=(
            None
            if update_type is None
            else UpdateMetadata(dependency="ruff", ecosystem=ecosystem, update_type=update_type)
        ),
        last_concluded_at=concluded_at,
    )


def test_eligible_green_and_unarmed_FIRES() -> None:
    """The quiet gate. It is what a rule that stopped arming looks like, and it is also what a
    sibling disarmed by the one that landed first looks like -- the two known generators present
    identically, which is why one detector covers both."""
    rule = REGISTRY[UNDERSCORED]

    assert kinds(audit_pending(pending(), rule, NOW)) == [STALL_ELIGIBLE_NOT_ARMED]


def test_eligible_but_red_is_the_checks_doing_their_job() -> None:
    rule = REGISTRY[UNDERSCORED]

    assert audit_pending(pending(conclusions=("success", "failure")), rule, NOW) == ()


def test_a_package_major_left_unarmed_is_the_rule_declining_to_act() -> None:
    """The discriminator. infraops-mcp-server#4 and #5 are real instances: npm majors, green,
    unarmed, and correctly so."""
    rule = REGISTRY[UNDERSCORED]

    result = audit_pending(pending(update_type=MAJOR, ecosystem="npm_and_yarn"), rule, NOW)

    assert result == ()


def test_an_actions_major_left_unarmed_FIRES_under_the_rule_that_permits_it() -> None:
    rule = REGISTRY[UNDERSCORED]

    result = audit_pending(pending(update_type=MAJOR, ecosystem="github_actions"), rule, NOW)

    assert kinds(result) == [STALL_ELIGIBLE_NOT_ARMED]


def test_armed_and_green_but_only_just_is_a_landing_about_to_happen() -> None:
    rule = REGISTRY[UNDERSCORED]

    result = audit_pending(pending(armed=True, concluded_at=NOW - timedelta(seconds=5)), rule, NOW)

    assert result == ()


def test_armed_and_green_for_an_hour_and_still_open_FIRES() -> None:
    """The purest form of the question: nothing is stopping it and it is not landing."""
    rule = REGISTRY[UNDERSCORED]

    result = audit_pending(pending(armed=True, concluded_at=NOW - timedelta(hours=6)), rule, NOW)

    assert kinds(result) == [STALL_ARMED_NOT_LANDED]


def test_an_unreadable_update_FIRES_rather_than_being_classified_as_ineligible() -> None:
    rule = REGISTRY[UNDERSCORED]

    assert kinds(audit_pending(pending(update_type=None), rule, NOW)) == [STALL_METADATA_UNREADABLE]


def test_a_head_with_nothing_concluded_is_not_green() -> None:
    assert not is_green(pending(conclusions=()))
    assert audit_pending(pending(conclusions=()), REGISTRY[UNDERSCORED], NOW) == ()


# ---------------------------------------------------------------------------------------------
# One repository, both detectors, and the two repository-level answers.
# ---------------------------------------------------------------------------------------------


def test_a_repository_with_an_untranscribed_installed_rule_FIRES_once_for_the_repository() -> None:
    """Fail closed: if the installed rule is unknown, no open update here can be classified, and
    saying nothing would be a clean answer computed from no knowledge."""
    audit = audit_repository(
        repository=REPO,
        landings=[],
        pending=(pending(),),
        rule_revision="e" * 40,
        now=NOW,
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
        now=NOW,
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
        now=NOW,
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
        now=NOW,
    )

    assert audit.severity == "warning"


# ---------------------------------------------------------------------------------------------
# The record the pass files.
# ---------------------------------------------------------------------------------------------


def test_the_heartbeat_row_is_written_even_when_nothing_was_found() -> None:
    """A detector that writes only on a finding is indistinguishable from one that has stopped
    running. That is the failure this whole increment exists to catch, so the row is the pass's
    own evidence that it ran and the findings are its content."""
    audit = audit_repository(
        repository=REPO, landings=[landing()], pending=(), rule_revision=UNDERSCORED, now=NOW
    )

    body = audit_observation(audit, "20260808T120000Z", NOW)

    assert body["observation_type"] == "landing_audit"
    assert body["facts"]["findings_found"] == 0
    assert body["facts"]["rule_permitted_landings"] == 1
    assert body["severity"] == "info"


def test_one_pass_answering_its_own_id_twice_the_same_way_replays() -> None:
    audit = audit_repository(
        repository=REPO, landings=[landing()], pending=(), rule_revision=UNDERSCORED, now=NOW
    )

    first = audit_observation(audit, "20260808T120000Z", NOW)
    second = audit_observation(audit, "20260808T120000Z", NOW)

    assert first["idempotency_key"] == second["idempotency_key"]


def test_one_pass_answering_its_own_id_DIFFERENTLY_is_loud_rather_than_a_second_row() -> None:
    """Same source reference, different facts, which is the orchestrator's conflict branch. A
    moment cannot have two answers, and the ledger's rule is that facts which drift are loud."""
    clean = audit_repository(
        repository=REPO, landings=[landing()], pending=(), rule_revision=UNDERSCORED, now=NOW
    )
    drifted = audit_repository(
        repository=REPO,
        landings=[landing(revision=PATCH_AND_MINOR, update_type=MAJOR)],
        pending=(),
        rule_revision=UNDERSCORED,
        now=NOW,
    )

    first = audit_observation(clean, "20260808T120000Z", NOW)
    second = audit_observation(drifted, "20260808T120000Z", NOW)

    assert first["source_reference"] == second["source_reference"]
    assert first["idempotency_key"] != second["idempotency_key"]


def test_a_flood_of_findings_is_trimmed_to_fit_with_its_true_count_beside_it() -> None:
    """The orchestrator bounds facts at 4096 encoded bytes and rejects anything larger, so an
    unbounded list would make a bad day the day the detector stops being able to report."""
    audit = audit_repository(
        repository=REPO,
        landings=[landing(revision=PATCH_AND_MINOR, update_type=MAJOR) for _ in range(200)],
        pending=(),
        rule_revision=UNDERSCORED,
        now=NOW,
    )

    body = audit_observation(audit, "20260808T120000Z", NOW)

    assert body["facts"]["findings_found"] == 200
    assert len(body["facts"]["findings"]) < 200
    assert len(str(body["facts"])) < 4096
