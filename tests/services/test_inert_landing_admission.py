"""The composed answer for an update-bot pull request in the declared inert population.
ADR-0038 part 2.

Everything runs with no network. Two properties carry most of the weight and neither is visible
from a happy-path test: `satisfied` is a POSITIVE conjunction rather than an empty refusal list, and
the estate term is INVERTED relative to its deploying sibling, which is the whole reason this is a
separate function rather than a branch inside that one.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from orchestrator.persistence.models import EstatePrMerge
from orchestrator.services.estate_landing import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    LANDING_UNKNOWN,
    SOURCE_UNCONFIGURED,
    SOURCE_UNREADABLE,
    EstateAnswer,
)
from orchestrator.services.estate_landing_admission import (
    LANDING_ALREADY_RECORDED,
    LANDING_APP_CREDENTIALS_MISSING,
    LANDING_BASE_NOT_DEFAULT_BRANCH,
    LANDING_CHECKS_AWAITING_VERDICT,
    LANDING_CHECKS_IN_FLIGHT,
    LANDING_CHECKS_NOT_CLEAN,
    LANDING_CHECKS_VERDICT_UNREADABLE,
    LANDING_ECOSYSTEM_EXCLUDED,
    LANDING_ECOSYSTEM_UNREADABLE,
    LANDING_ESTATE_SOURCE_UNCONFIGURED,
    LANDING_ESTATE_SOURCE_UNREADABLE,
    LANDING_ESTATE_UNKNOWN,
    LANDING_FRESHNESS_UNREADABLE,
    LANDING_HEAD_NOT_CURRENT_WITH_BASE,
    LANDING_MERGEABILITY_UNKNOWN,
    LANDING_MERGEABILITY_UNRECOGNISED,
    LANDING_NOT_ENABLED,
    LANDING_PULL_REQUEST_CONFLICTED,
    LANDING_PULL_REQUEST_NOT_OPEN,
    LANDING_PULL_REQUEST_UNREADABLE,
    EstateGatewayError,
)
from orchestrator.services.inert_landing_admission import (
    INERT_LANDING_AUTHOR_NOT_PERMITTED,
    INERT_LANDING_POLICY_SOURCE_UNCONFIGURED,
    INERT_LANDING_POLICY_SOURCE_UNREADABLE,
    INERT_LANDING_REPOSITORY_NOT_DECLARED,
    INERT_LANDING_RULES_UNDECLARED,
    INERT_LANDING_TARGET_NOT_INERT,
    inert_landing_admission,
)
from orchestrator.services.inert_landing_policy import (
    RULES_UNDECLARED,
    InertLandingAnswer,
)
from orchestrator.services.inert_landing_policy import (
    SOURCE_UNCONFIGURED as POLICY_SOURCE_UNCONFIGURED,
)
from orchestrator.services.inert_landing_policy import (
    SOURCE_UNREADABLE as POLICY_SOURCE_UNREADABLE,
)
from tests.services.estate_doubles import FakeEstateLandingSource
from tests.services.estate_landing_doubles import (
    HEAD,
    FakeEstateGateway,
    pull_request,
    run,
)
from tests.services.inert_landing_doubles import (
    EXCLUDED_ECOSYSTEM,
    INERT_POLICY_VERSION,
    INERT_REPOSITORY,
    UPDATE_BOT,
    FakeInertPolicySource,
    rules,
)

PR = 3
DOCKER_BRANCH = f"dependabot/{EXCLUDED_ECOSYSTEM}/python-3.14-slim"
UV_BRANCH = "dependabot/uv/typer-0.21.0"


def _inert_source() -> FakeEstateLandingSource:
    return FakeEstateLandingSource(default=EstateAnswer(LANDING_INERT))


def _answer(
    session: Session,
    *,
    gateway: FakeEstateGateway | None = None,
    landing_source: FakeEstateLandingSource | None = None,
    policy_source: FakeInertPolicySource | None = None,
    enabled: bool = True,
    credentials_configured: bool = True,
    repository: str = INERT_REPOSITORY,
):
    return inert_landing_admission(
        session,
        repository,
        PR,
        landing_source or _inert_source(),
        policy_source or FakeInertPolicySource(),
        gateway or FakeEstateGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH)),
        enabled=enabled,
        credentials_configured=credentials_configured,
    )


def test_a_clean_fresh_update_bot_pull_request_in_the_declared_population_is_admitted(
    migrated_session: Session,
) -> None:
    answer = _answer(migrated_session)

    assert answer.satisfied is True
    assert answer.refusals == ()
    assert answer.repository == INERT_REPOSITORY
    assert answer.pr_number == PR
    assert answer.head_sha == HEAD
    assert answer.policy_version == INERT_POLICY_VERSION
    # Freshness is not an obstacle, so there is nothing to freshen.
    assert answer.branch_update_qualifies is False


def test_the_repository_is_folded_so_the_report_and_the_act_ask_one_question(
    migrated_session: Session,
) -> None:
    """Two surfaces normalising differently is how the deploying lane's equivalent went wrong."""
    landing_source = _inert_source()

    answer = _answer(
        migrated_session, landing_source=landing_source, repository=INERT_REPOSITORY.upper()
    )

    assert answer.satisfied is True
    assert answer.repository == INERT_REPOSITORY
    assert landing_source.asked == [INERT_REPOSITORY]


# ---------------------------------------------------------------------------------------------
# The estate term, INVERTED. Only an explicit `inert` passes.
# ---------------------------------------------------------------------------------------------


def test_a_repository_the_estate_says_redeploys_is_refused_by_its_own_name(
    migrated_session: Session,
) -> None:
    """It is not a failure -- it says the OTHER lane is the right one -- so reporting it as "the
    estate has not looked" would send somebody to assess a repository already assessed."""
    answer = _answer(
        migrated_session,
        landing_source=FakeEstateLandingSource(default=EstateAnswer(LANDING_REDEPLOYS)),
    )

    assert answer.satisfied is False
    assert INERT_LANDING_TARGET_NOT_INERT in answer.refusals
    assert LANDING_ESTATE_UNKNOWN not in answer.refusals


@pytest.mark.parametrize(
    ("answer_value", "reason", "expected"),
    [
        (LANDING_UNKNOWN, "no_app_record", LANDING_ESTATE_UNKNOWN),
        (LANDING_UNKNOWN, "not_assessed", LANDING_ESTATE_UNKNOWN),
        # A FOURTH value shipped on the authoring side. `landing` is set, so it is not a source
        # failure -- and it must not arrive here as permission.
        ("quiesced", None, LANDING_ESTATE_UNKNOWN),
        (None, SOURCE_UNCONFIGURED, LANDING_ESTATE_SOURCE_UNCONFIGURED),
        (None, SOURCE_UNREADABLE, LANDING_ESTATE_SOURCE_UNREADABLE),
        (None, None, LANDING_ESTATE_SOURCE_UNREADABLE),
    ],
)
def test_every_estate_answer_but_inert_refuses(
    migrated_session: Session, answer_value: str | None, reason: str | None, expected: str
) -> None:
    answer = _answer(
        migrated_session,
        landing_source=FakeEstateLandingSource(default=EstateAnswer(answer_value, reason)),
    )

    assert answer.satisfied is False
    assert expected in answer.refusals


# ---------------------------------------------------------------------------------------------
# The policy term.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (POLICY_SOURCE_UNCONFIGURED, INERT_LANDING_POLICY_SOURCE_UNCONFIGURED),
        (RULES_UNDECLARED, INERT_LANDING_RULES_UNDECLARED),
        (POLICY_SOURCE_UNREADABLE, INERT_LANDING_POLICY_SOURCE_UNREADABLE),
        (None, INERT_LANDING_POLICY_SOURCE_UNREADABLE),
    ],
)
def test_a_policy_that_could_not_be_read_refuses_by_its_own_cause(
    migrated_session: Session, reason: str | None, expected: str
) -> None:
    """Three causes, three different people: a variable nobody set, a service refusing, and a
    document that loaded and declared no such population."""
    answer = _answer(
        migrated_session, policy_source=FakeInertPolicySource(InertLandingAnswer(None, reason))
    )

    assert answer.satisfied is False
    assert expected in answer.refusals
    assert answer.policy_version is None


def test_a_repository_the_estate_calls_inert_but_nobody_declared_is_refused(
    migrated_session: Session,
) -> None:
    """**Fail-closed in BOTH directions is the design.** The estate agreeing that landing here is
    inert is not the same as somebody having decided this lane may land into it."""
    answer = _answer(
        migrated_session,
        policy_source=FakeInertPolicySource(
            InertLandingAnswer(rules(repositories=frozenset({"alobarquest/somewhere-else"})))
        ),
    )

    assert answer.satisfied is False
    assert INERT_LANDING_REPOSITORY_NOT_DECLARED in answer.refusals
    # The version is still carried, because the document read fine; what failed is membership.
    assert answer.policy_version == INERT_POLICY_VERSION


def test_an_undeclared_repository_still_has_its_remaining_terms_evaluated(
    migrated_session: Session,
) -> None:
    """Reporting one condition while silently declining to evaluate the rest leaves an operator
    fixing one thing at a time."""
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, is_open=False), behind=4
    )

    answer = _answer(
        migrated_session,
        gateway=gateway,
        policy_source=FakeInertPolicySource(
            InertLandingAnswer(rules(repositories=frozenset({"alobarquest/somewhere-else"})))
        ),
    )

    assert INERT_LANDING_REPOSITORY_NOT_DECLARED in answer.refusals
    assert LANDING_PULL_REQUEST_NOT_OPEN in answer.refusals
    assert LANDING_HEAD_NOT_CURRENT_WITH_BASE in answer.refusals


# ---------------------------------------------------------------------------------------------
# The author term, which is the only thing bounding which pull requests this lane sees.
# ---------------------------------------------------------------------------------------------


def test_the_permitted_author_is_read_from_the_policy_and_not_written_here(
    migrated_session: Session,
) -> None:
    """A policy naming a different account refuses the update bot -- which is what proves the
    condition is the document's rather than a literal in this source tree."""
    answer = _answer(
        migrated_session,
        policy_source=FakeInertPolicySource(
            InertLandingAnswer(rules(permitted_authors=frozenset({"renovate[bot]"})))
        ),
    )

    assert answer.satisfied is False
    assert INERT_LANDING_AUTHOR_NOT_PERMITTED in answer.refusals


def test_a_policy_naming_another_account_admits_that_account(migrated_session: Session) -> None:
    """The positive half. Without it the test above passes against a term that refuses everyone,
    which is the shape a wrong spelling produces and the direction nobody notices."""
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, author_login="renovate[bot]")
    )

    answer = _answer(
        migrated_session,
        gateway=gateway,
        policy_source=FakeInertPolicySource(
            InertLandingAnswer(rules(permitted_authors=frozenset({"renovate[bot]"})))
        ),
    )

    assert answer.satisfied is True


def test_a_permitted_login_carried_by_an_account_that_is_not_a_machine_is_refused(
    migrated_session: Session,
) -> None:
    """The TYPE is not in the document and is checked anyway: a person may rename a user account
    into any spelling at all, and the platform's answer about what kind of account it is cannot be
    taken by renaming."""
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, author_is_bot=False)
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is False
    assert INERT_LANDING_AUTHOR_NOT_PERMITTED in answer.refusals


def test_a_factory_opened_pull_request_is_refused(migrated_session: Session) -> None:
    """Four of the declared six carry a factory caller workflow, so this is a real subject rather
    than a hypothetical one -- and this lane asks NONE of the questions a factory landing rests
    on."""
    gateway = FakeEstateGateway(
        pull=pull_request(
            number=PR,
            head_ref=UV_BRANCH,
            title="SDS 0e4f: Bump typer from 0.20.0 to 0.21.0",
            author_login="AlobarQuest",
            author_is_bot=False,
        )
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is False
    assert INERT_LANDING_AUTHOR_NOT_PERMITTED in answer.refusals


# ---------------------------------------------------------------------------------------------
# The remote terms, shared with the deploying lane wherever the condition is the same.
# ---------------------------------------------------------------------------------------------


def test_an_unreadable_pull_request_refuses_and_asks_nothing_further(
    migrated_session: Session,
) -> None:
    gateway = FakeEstateGateway(read_error=EstateGatewayError("read_status", 502))

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is False
    assert LANDING_PULL_REQUEST_UNREADABLE in answer.refusals
    assert answer.head_sha is None
    assert gateway.compares == []
    assert gateway.blobs == []


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"is_open": False}, LANDING_PULL_REQUEST_NOT_OPEN),
        ({"landed": True}, LANDING_PULL_REQUEST_NOT_OPEN),
        ({"base_ref": "release/2.0"}, LANDING_BASE_NOT_DEFAULT_BRANCH),
        ({"mergeable_state": "unknown"}, LANDING_MERGEABILITY_UNKNOWN),
        # THE SIBLING PIN OF THE SAME DEFECT, and this test's own name is the indictment: a
        # conflicted branch was refusing by SOMEBODY ELSE'S name, in a parametrization asserting
        # that each fact refuses by its own. Corrected 2026-09-05 with the lane it mirrors.
        ({"mergeable_state": "dirty"}, LANDING_PULL_REQUEST_CONFLICTED),
        ({"mergeable_state": "draft"}, LANDING_MERGEABILITY_UNRECOGNISED),
    ],
)
def test_the_remote_facts_each_refuse_by_their_own_name(
    migrated_session: Session, kwargs: dict[str, Any], expected: str
) -> None:
    gateway = FakeEstateGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH, **kwargs))

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is False
    assert expected in answer.refusals


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ((run(conclusion="failure"),), LANDING_CHECKS_NOT_CLEAN),
        ((run(status="in_progress", conclusion=None),), LANDING_CHECKS_IN_FLIGHT),
        ((run(conclusion="cancelled"),), LANDING_CHECKS_AWAITING_VERDICT),
        ((), LANDING_CHECKS_AWAITING_VERDICT),
        # The order is the safety: a head carrying one red run and one still going has said no.
        (
            (run(conclusion="failure"), run(status="in_progress", conclusion=None)),
            LANDING_CHECKS_NOT_CLEAN,
        ),
    ],
)
def test_a_blocked_head_is_told_apart_by_the_runs_at_it(
    migrated_session: Session, runs: tuple, expected: str
) -> None:
    """One word, four causes -- and this lane shares the deploying lane's classification rather
    than carrying a second copy of it."""
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, mergeable_state="blocked"), runs=runs
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is False
    assert expected in answer.refusals


def test_runs_that_cannot_be_read_refuse_rather_than_being_treated_as_absent(
    migrated_session: Session,
) -> None:
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, mergeable_state="blocked"),
        runs_error=EstateGatewayError("read_status", 500),
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert LANDING_CHECKS_VERDICT_UNREADABLE in answer.refusals


def test_a_clean_head_is_never_asked_about_its_runs(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    _answer(migrated_session, gateway=gateway)

    assert gateway.run_reads == []


# ---------------------------------------------------------------------------------------------
# Freshness -- required, and the condition this lane creates for itself.
# ---------------------------------------------------------------------------------------------


def test_a_head_behind_its_base_is_refused_and_qualifies_for_a_branch_update(
    migrated_session: Session,
) -> None:
    gateway = FakeEstateGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH), behind=2)

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is False
    assert answer.refusals == (LANDING_HEAD_NOT_CURRENT_WITH_BASE,)
    assert answer.branch_update_qualifies is True


def test_a_policy_that_does_not_require_freshness_never_asks_how_far_behind(
    migrated_session: Session,
) -> None:
    """The condition is the document's, so a version that waives it waives it here."""
    gateway = FakeEstateGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH), behind=9)

    answer = _answer(
        migrated_session,
        gateway=gateway,
        policy_source=FakeInertPolicySource(InertLandingAnswer(rules(require_fresh=False))),
    )

    assert answer.satisfied is True
    assert gateway.compares == []


def test_an_unreadable_comparison_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH),
        compare_error=EstateGatewayError("read_status", 500),
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert LANDING_FRESHNESS_UNREADABLE in answer.refusals
    assert answer.branch_update_qualifies is False


@pytest.mark.parametrize(
    "beside",
    [
        {"mergeable_state": "dirty"},
        {"is_open": False},
        {"author_is_bot": False},
    ],
)
def test_a_second_obstacle_disqualifies_the_branch_update(
    migrated_session: Session, beside: dict[str, Any]
) -> None:
    """The rule is that freshness is the SOLE remaining obstacle. Anything else means the branch
    could not land whatever is done to it, so freshening spends a real build to learn nothing --
    and a build running is indistinguishable from progress to whoever reads the report."""
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, **beside), behind=2
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert LANDING_HEAD_NOT_CURRENT_WITH_BASE in answer.refusals
    assert answer.branch_update_qualifies is False


def test_a_head_behind_its_base_whose_checks_reached_no_verdict_still_qualifies(
    migrated_session: Session,
) -> None:
    """The one refusal excused because bringing the branch up to date is what ANSWERS it rather
    than what tolerates it: nothing else in the estate re-runs a check that was abandoned."""
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, mergeable_state="blocked"),
        behind=2,
        runs=(run(conclusion="cancelled"),),
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert LANDING_CHECKS_AWAITING_VERDICT in answer.refusals
    assert answer.branch_update_qualifies is True


# ---------------------------------------------------------------------------------------------
# The ecosystem exclusion.
# ---------------------------------------------------------------------------------------------


def test_a_bump_in_the_excluded_ecosystem_is_refused(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(pull=pull_request(number=PR, head_ref=DOCKER_BRANCH))

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is False
    assert LANDING_ECOSYSTEM_EXCLUDED in answer.refusals


def test_the_excluded_set_is_the_documents_and_not_the_other_lanes(
    migrated_session: Session,
) -> None:
    """Both halves of the estate exclude on one principle and name DIFFERENT ecosystems, because
    what the required checks leave unexercised differs. A reader that carried the literal across
    would refuse the wrong population."""
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref="dependabot/github_actions/checkout-7")
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is True
    assert LANDING_ECOSYSTEM_EXCLUDED not in answer.refusals


def test_the_exclusion_folds_case_on_both_sides(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(pull=pull_request(number=PR, head_ref="dependabot/Docker/python"))

    answer = _answer(
        migrated_session,
        gateway=gateway,
        policy_source=FakeInertPolicySource(
            InertLandingAnswer(rules(excluded_ecosystems=frozenset({"DOCKER"})))
        ),
    )

    assert LANDING_ECOSYSTEM_EXCLUDED in answer.refusals


@pytest.mark.parametrize(
    "head_ref", ["main", "dependabot/uv", "dependabot//typer", "renovate/uv/typer"]
)
def test_a_branch_whose_ecosystem_cannot_be_read_refuses(
    migrated_session: Session, head_ref: str
) -> None:
    """A name this cannot read is never "the bot named no ecosystem": it is this program failing to
    read what the exclusion is about, and permitting on that lands a change nobody can re-check."""
    gateway = FakeEstateGateway(pull=pull_request(number=PR, head_ref=head_ref))

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is False
    assert LANDING_ECOSYSTEM_UNREADABLE in answer.refusals


# ---------------------------------------------------------------------------------------------
# The terms that are not about the pull request at all.
# ---------------------------------------------------------------------------------------------


def test_an_unenabled_deployment_refuses(migrated_session: Session) -> None:
    answer = _answer(migrated_session, enabled=False)

    assert answer.satisfied is False
    assert LANDING_NOT_ENABLED in answer.refusals


def test_missing_app_credentials_refuse(migrated_session: Session) -> None:
    answer = _answer(migrated_session, credentials_configured=False)

    assert answer.satisfied is False
    assert LANDING_APP_CREDENTIALS_MISSING in answer.refusals


def test_a_pull_request_this_lane_has_already_acted_on_is_refused(
    migrated_session: Session,
) -> None:
    """The SAME table the deploying lane writes, read for the same reason: one row per
    (repository, pull request) ever, so "did we already do this?" is answered here rather than by
    asking GitHub afterwards, where a lost success and a refusal answer alike."""
    migrated_session.add(
        EstatePrMerge(
            repository=INERT_REPOSITORY,
            pr_number=PR,
            head_sha=HEAD,
            status="merged",
            reason_code=None,
            merge_commit_sha="d" * 40,
            github_status=200,
            change_record_id=None,
            policy_version=INERT_POLICY_VERSION,
            idempotency_key="already-here",
        )
    )
    migrated_session.flush()

    answer = _answer(migrated_session)

    assert answer.satisfied is False
    assert LANDING_ALREADY_RECORDED in answer.refusals


# ---------------------------------------------------------------------------------------------
# The shape of the answer itself.
# ---------------------------------------------------------------------------------------------


def test_a_term_that_is_unmet_refuses_even_when_the_remote_half_names_nothing(
    migrated_session: Session,
) -> None:
    """`satisfied` is the conjunction of every term's own affirmative answer, and the refusal list
    is built alongside for the reader rather than being what decides.

    **BE PRECISE ABOUT WHAT THIS DOES NOT PROVE, because the name it first carried claimed more.**
    Mutation-tested: replacing the conjunction with `not refusals` leaves this suite entirely
    green, because no reachable state today has an unmet term that names nothing -- every term
    here either raises its own refusal or sits beside one that does. The two forms are therefore
    equivalent for this lane as it stands, and the conjunction is kept because the NEXT term added
    may not name itself, which is the case the deploying sibling already reaches through its pace
    term (unmet, silent, deliberately). A test cannot discriminate a difference the code cannot
    yet exhibit, and inventing a fixture that could would be testing a shape nothing produces.

    What it does prove is the half that is reachable: a remote half returning unmet with an empty
    refusal list of its own -- which is what a policy that could not be read produces -- still
    refuses, and the answer names the cause rather than staying silent about it.
    """
    gateway = FakeEstateGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    answer = _answer(
        migrated_session,
        gateway=gateway,
        policy_source=FakeInertPolicySource(InertLandingAnswer(None, POLICY_SOURCE_UNREADABLE)),
    )

    assert answer.satisfied is False
    # The only refusal is the policy's own; the remote half raised none and is still unmet.
    assert answer.refusals == (INERT_LANDING_POLICY_SOURCE_UNREADABLE,)


def test_every_unmet_term_is_reported_rather_than_the_first(migrated_session: Session) -> None:
    """An operator asking why nothing landed wants the whole list: the terms are fixed by
    different people at different times."""
    gateway = FakeEstateGateway(
        pull=pull_request(
            number=PR,
            head_ref=DOCKER_BRANCH,
            base_ref="release/2.0",
            author_is_bot=False,
        ),
        behind=3,
    )

    answer = _answer(
        migrated_session,
        gateway=gateway,
        enabled=False,
        credentials_configured=False,
        landing_source=FakeEstateLandingSource(default=EstateAnswer(LANDING_REDEPLOYS)),
    )

    assert set(answer.refusals) >= {
        LANDING_NOT_ENABLED,
        LANDING_APP_CREDENTIALS_MISSING,
        INERT_LANDING_TARGET_NOT_INERT,
        LANDING_BASE_NOT_DEFAULT_BRANCH,
        INERT_LANDING_AUTHOR_NOT_PERMITTED,
        LANDING_HEAD_NOT_CURRENT_WITH_BASE,
        LANDING_ECOSYSTEM_EXCLUDED,
    }


def test_this_lane_never_reads_a_rollout_pin(migrated_session: Session) -> None:
    """The rollout-pin terms belong to the lane whose landings cause a rollout. Here there is no
    rollout, so asking would be asking a question with no subject -- and a pin refusal would hold
    a pull request on a condition nothing in this population can satisfy."""
    gateway = FakeEstateGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is True
    assert gateway.blobs == []


def test_the_policy_is_asked_exactly_once_per_answer(migrated_session: Session) -> None:
    policy_source = FakeInertPolicySource()

    _answer(migrated_session, policy_source=policy_source)

    assert policy_source.asked == 1


def test_the_admitted_author_is_the_rest_spelling_and_not_the_command_line_one(
    migrated_session: Session,
) -> None:
    """One identity, two spellings. The policy declares the REST one because that is what the
    workflow this lane replaces keyed on, and it is what `repos/{r}/pulls/{n}` answers."""
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, author_login="app/dependabot")
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert answer.satisfied is False
    assert INERT_LANDING_AUTHOR_NOT_PERMITTED in answer.refusals
    # And the control: the spelling the policy does declare is admitted.
    assert (
        _answer(
            migrated_session,
            gateway=FakeEstateGateway(
                pull=pull_request(number=PR, head_ref=UV_BRANCH, author_login=UPDATE_BOT)
            ),
        ).satisfied
        is True
    )
