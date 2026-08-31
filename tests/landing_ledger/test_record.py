"""What a landing record may assert, per route -- and what it must refuse to assert."""

import json
from datetime import UTC, datetime

from landing_ledger.model import (
    Check,
    FactoryClaim,
    InertLandingPermission,
    Landing,
    PolicyPermission,
    RuleApplication,
    UpdateMetadata,
)
from landing_ledger.record import (
    BASES,
    BASIS_CHANGE_RECORD,
    BASIS_FACTORY,
    BASIS_HUMAN,
    BASIS_INERT_POLICY,
    BASIS_NONE,
    BASIS_RULE,
    BASIS_UNATTRIBUTED,
    MAX_FACT_BYTES,
    arm_question_answered,
    basis_of,
    gate_armed,
    landing_observation,
)
from landing_ledger.rules import rule_for

LANDED = datetime(2026, 8, 7, 12, 42, 4, tzinfo=UTC)
REPO = "AlobarQuest/intent-packages"
GATE = ".github/workflows/dependabot-auto-merge.yml"

# THE FULL BLOB SHA, AND SINCE ADR-0037 IT DECIDES WHICH BRANCH RUNS. `basis_of` asks the registry
# whether the revision is transcribed: a transcribed one is judged on whether the gate ARMED the
# landing, and an unknown one falls back to the login heuristic. This fixture used the abbreviated
# `77ab867d`, which the registry does not hold -- so every case in this file would have exercised
# the fallback and nothing would have tested the rule the change is about.
REVISION = "77ab867d1080d18baea3a2b230655c2729716970"
# A revision no fixture pins and the registry has never held.
UNTRANSCRIBED = "0" * 40


def _rule(outcome: str = "success", arm_outcome: str | None = "success") -> RuleApplication:
    return RuleApplication(
        path=GATE,
        revision=REVISION,
        run=31179223805,
        outcome=outcome,
        arm_outcome=arm_outcome,
    )


def gate_landing(**overrides: object) -> Landing:
    """The auto-merged shape, modelled on intent-packages@e931db8d (2026-08-07)."""
    base = {
        "repository": REPO,
        "base_ref": "main",
        "commit": "e931db8d31debfb08fd8f8410a4778f33c437fc1",
        "landed_at": LANDED,
        "title": "chore(deps-dev): bump ruff from 0.15.22 to 0.16.1 (#50)",
        "files": ("pyproject.toml", "uv.lock"),
        "files_changed": 2,
        "pull_request": 50,
        "head_commit": "4437bc985a55c1aa5ad8488067df594c5c1c676c",
        "landed_by": "github-actions[bot]",
        "checks": (Check(name="Lint, type-check, and test", conclusion="success", run=1),),
        "rule": _rule(),
        "update": UpdateMetadata(
            dependency="ruff", ecosystem="uv", update_type="version-update:semver-minor"
        ),
    }
    return Landing(**{**base, **overrides})  # type: ignore[arg-type]


def human_landing(**overrides: object) -> Landing:
    return gate_landing(landed_by="AlobarQuest", rule=None, update=None, **overrides)


def push_landing(**overrides: object) -> Landing:
    """A commit pushed straight at the branch -- intent-packages@a0563643, which edited the gate."""
    base = {
        "repository": REPO,
        "base_ref": "main",
        "commit": "a0563643d1f92d9c9ce5f5806aaa11c53dca1437",
        "landed_at": LANDED,
        "title": "ci: auto-merge GitHub Actions majors, not just patch and minor",
        "files": (GATE,),
        "files_changed": 1,
    }
    return Landing(**{**base, **overrides})  # type: ignore[arg-type]


UNIT = "0c0002c6-9869-59bc-84c6-654e6fc57d9e"


def factory_landing(**overrides: object) -> Landing:
    """The first landing the factory ever made -- intent-packages@b3f1522f, 2026-08-10."""
    base: dict[str, object] = {
        "commit": "b3f1522f8630a7026da7dbaa1a120971fc024f73",
        "title": "feat: implement SDS unit 0c0002c6-9869-59bc-84c6-654e6fc57d9e (#66)",
        "pull_request": 66,
        "landed_by": "alobar-sds-dispatch[bot]",
        "rule": None,
        "update": None,
        "claim": FactoryClaim(work_unit=UNIT, package_revision=1),
    }
    return gate_landing(**{**base, **overrides})


def test_the_factory_landing_its_own_pull_request_records_the_claim_it_will_be_audited_on() -> None:
    permitted = landing_observation(factory_landing())["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_FACTORY
    assert permitted["landed_by"] == "alobar-sds-dispatch[bot]"
    assert permitted["work_unit"] == UNIT
    assert permitted["package_revision"] == 1
    assert "checked against the orchestrator" in permitted["reason"]
    # No rule keys: the gate did not permit this and a record that said so would be false.
    assert not {"rule_path", "rule_revision", "rule_run", "decision"} & set(permitted)


def test_a_PERSON_merging_a_factory_pull_request_is_still_a_person() -> None:
    """The reason no existing row reclassifies. Every factory pull request before 2026-08-10
    carried the same claim in its commit and was merged by Devon; a basis keyed on the claim alone
    would rewrite all of them, and each rewrite is a conflicting row on a landing where nothing
    actually changed.

    What holds this is the `is_machine` conjunct, NOT the cascade order -- swapping the human and
    factory branches is a measured no-op, because the two are mutually exclusive. The order that
    IS load-bearing is rule-before-factory, which
    `test_a_gate_permitted_landing_is_not_reclassified_by_a_claim_it_happens_to_carry` covers.
    """
    permitted = landing_observation(factory_landing(landed_by="AlobarQuest"))["facts"][
        "permitted_by"
    ]

    assert permitted["basis"] == BASIS_HUMAN
    assert "work_unit" not in permitted


def test_a_machine_merge_with_no_claim_is_still_unattributed() -> None:
    """The basis is not a synonym for `a bot did it`. Without a claim there is nothing to audit,
    and inventing a basis for it is what `unattributed` exists to refuse."""
    assert basis_of(factory_landing(claim=None)) == BASIS_UNATTRIBUTED


def test_a_gate_permitted_landing_is_not_reclassified_by_a_claim_it_happens_to_carry() -> None:
    """`auto_merge_rule` is checked first and stays first: a landing the gate actually permitted
    has a rule to be re-evaluated against, which is a stronger answer than a claim."""
    assert basis_of(gate_landing(claim=FactoryClaim(work_unit=UNIT))) == BASIS_RULE


def test_every_basis_the_cascade_can_return_is_named() -> None:
    """The vocabulary and the cascade are two halves of one decision. A branch added without a
    name emits a value no consumer can interpret; a name added without a branch is dead."""
    reachable = {
        basis_of(push_landing()),
        basis_of(gate_landing()),
        basis_of(human_landing()),
        basis_of(factory_landing()),
        basis_of(factory_landing(claim=None)),
        # ADR-0019 increment 5b. The vocabulary grew, so this set had to: a member with no
        # landing here would be a name nothing can produce, which is the half of the property
        # that is easy to lose when a basis is added.
        basis_of(policy_landing()),
        # ADR-0038, and the same obligation one basis later.
        basis_of(inert_landing()),
    }

    assert reachable == set(BASES)
    assert len(BASES) == len(set(BASES))


def test_an_auto_merged_landing_records_the_rule_that_permitted_it() -> None:
    permitted = landing_observation(gate_landing())["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_RULE
    assert permitted["rule_path"] == GATE
    assert permitted["rule_revision"] == REVISION
    assert permitted["rule_run"] == 31179223805
    assert permitted["update_type"] == "version-update:semver-minor"
    assert permitted["ecosystem"] == "uv"
    assert permitted["decision"] == "ADR-0016"
    assert permitted["checks"] == [
        {"name": "Lint, type-check, and test", "conclusion": "success", "run": 1}
    ]


def test_a_human_merge_records_the_person_and_invents_no_rule() -> None:
    """The backfill honesty rule. Every merge before 2026-08-07 is this shape, and a record that
    reconstructed conditions for them would assert something nobody checked."""
    permitted = landing_observation(human_landing())["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_HUMAN
    assert permitted["landed_by"] == "AlobarQuest"
    assert not {"rule_path", "rule_revision", "rule_run", "decision", "update_type"} & set(
        permitted
    )


def test_a_human_merge_of_a_pull_request_the_gate_DECLINED_still_invents_no_rule() -> None:
    """The case the previous test cannot reach, and the one that actually occurs.

    The gate runs on EVERY Dependabot pull request, including the ones it deliberately leaves to a
    person. Such a landing has a successful gate RUN and a human who merged it -- so a record keyed
    on "is there a rule application?" rather than on the basis would stamp `decision: ADR-0016`
    onto a landing the rule declined to permit.

    ADR-0037 changed WHICH FACT says the rule declined. It used to be that a person merged it;
    it is now that the arming step did not run, which is what the gate itself reports when its
    `if:` excludes the update. The scenario is unchanged and the discriminator is the honest one.
    """
    declined = gate_landing(landed_by="AlobarQuest", rule=_rule(arm_outcome="skipped"))
    permitted = landing_observation(declined)["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_HUMAN
    assert not {"rule_path", "rule_revision", "rule_run", "rule_outcome", "decision"} & set(
        permitted
    )
    assert "update_type" not in permitted


def test_a_direct_push_records_that_nothing_permitted_it() -> None:
    permitted = landing_observation(push_landing())["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_NONE
    assert "no pull request" in permitted["reason"]
    # No checks either: a push-triggered run concludes AFTER the push, so it cannot have
    # permitted it. Recording one here would read as a gate that was never there.
    assert "checks" not in permitted
    assert "landed_by" not in permitted


def test_the_rule_basis_needs_both_halves_and_is_never_rounded_down() -> None:
    """Either half alone is satisfiable by something that is not the lane.

    The gate runs on every Dependabot pull request, including the ones it deliberately leaves to a
    person -- so a successful gate RUN does not by itself mean the gate armed the landing. And a
    run that did not conclude cleanly is not a rule that ran, whatever its steps report.
    """
    assert basis_of(gate_landing(rule=None)) == BASIS_UNATTRIBUTED
    assert basis_of(gate_landing(rule=_rule(arm_outcome="skipped"))) == BASIS_UNATTRIBUTED
    # The run itself failed while the arming step had already succeeded. Reachable -- a run is
    # cancelled after its last step concludes -- and the conjunct that refuses it is separate.
    assert basis_of(gate_landing(rule=_rule(outcome="cancelled"))) == BASIS_UNATTRIBUTED


def test_a_person_merging_an_ARMED_pull_request_records_the_rule_that_armed_it() -> None:
    """ADR-0037, and the consequence taken deliberately.

    Under the old rule this recorded `human`, because `merged_by` did not end in `[bot]`. The gate
    had already permitted it and the named checks had already verified it, so the login was never
    the fact the basis was reaching for. It matters because the arming credential is a free choice
    only once the recorded basis stops moving with it.
    """
    permitted = landing_observation(gate_landing(landed_by="AlobarQuest"))["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_RULE
    assert permitted["rule_revision"] == REVISION
    assert permitted["decision"] == "ADR-0016"
    # `landed_by` stops being gated on and keeps being recorded.
    assert permitted["landed_by"] == "AlobarQuest"


def test_an_armed_landing_merged_by_a_machine_is_unchanged() -> None:
    """The regression guard rather than the new behaviour: every cascade landing recorded to date
    is this shape, and the change must not move any of them."""
    assert basis_of(gate_landing()) == BASIS_RULE


def test_a_skipped_arming_step_is_the_cascade_DECLINING_and_an_absent_one_is_not_an_answer() -> (
    None
):
    """The two must not be collapsed, and the basis is where the difference shows.

    `skipped` is the gate's `if:` excluding this update -- a positive observation that the rule
    declined, so the landing loses the rule basis. An ABSENT step is not an observation at all:
    nobody learned what the gate did, so the basis keeps the answer it had before ADR-0037 rather
    than inventing a colder one. Reading absent as "declined" would drop a genuinely armed machine
    landing to `unattributed`, where `audit_landing` returns early and no detector looks again.
    """
    declined = gate_landing(rule=_rule(arm_outcome="skipped"))
    unanswered = gate_landing(rule=_rule(arm_outcome=None))

    assert basis_of(declined) == BASIS_UNATTRIBUTED
    assert basis_of(unanswered) == BASIS_RULE
    assert arm_question_answered(declined)
    assert not arm_question_answered(unanswered)


def test_an_unanswered_arm_question_keeps_a_HUMAN_merge_human() -> None:
    """The fallback is today's answer, not a free pass. It restores the login conjunct, so an
    unanswered question on a person's merge stays `human` exactly as it did before."""
    assert basis_of(gate_landing(rule=_rule(arm_outcome=None), landed_by="AlobarQuest")) == (
        BASIS_HUMAN
    )


def test_a_renamed_arming_step_does_not_quietly_unattribute_an_armed_landing() -> None:
    """The reachable case behind the fallback, spelled as the sequence that produces it.

    The ledger pins the gate AT THE LANDING COMMIT, which need not be the revision that RAN --
    `audit.py` already carries `rule_self_modified` for that mismatch. A pull request opened
    before a revision that RENAMES the arming step and landed after it therefore carries a
    transcription describing a different revision's step, and the run does not contain it. That
    is `arm_outcome is None` on a landing the gate really did arm, and reading it as a decline
    would be wrong AND invisible.
    """
    renamed_away = gate_landing(rule=_rule(arm_outcome=None))

    assert renamed_away.rule is not None
    assert rule_for(renamed_away.rule.revision) is not None
    assert basis_of(renamed_away) == BASIS_RULE


def test_gate_armed_is_success_and_nothing_else() -> None:
    """A conclusion other than `success` is not an arming, whatever it is. Spelled over the
    vocabulary GitHub actually reports rather than over the two cases the cascade produces."""
    assert gate_armed(gate_landing())
    for conclusion in ("skipped", "failure", "cancelled", "neutral", "unknown", ""):
        assert not gate_armed(gate_landing(rule=_rule(arm_outcome=conclusion))), conclusion
    assert not gate_armed(gate_landing(rule=None))


def test_an_untranscribed_revision_keeps_todays_answer_so_the_audit_still_sees_it() -> None:
    """The fail-open this change would otherwise have opened, closed deliberately.

    `audit_landing` returns nothing at all for any basis but the rule, so `DRIFT_RULE_UNKNOWN` --
    the finding that says a rule nobody classified decided a landing -- is reachable ONLY through
    `auto_merge_rule`. Without a registry entry there is no step name to look for, so the arm
    question cannot be asked; letting that drop the basis would DELETE the finding rather than
    raise it. So an unknown revision answers exactly what it answered before ADR-0037, and the
    landing still reaches the detector that reports the revision is unknown.
    """
    # Carries an arm outcome that WOULD refuse the basis, so this pins the transcription clause
    # itself rather than coinciding with the absent-step one: what makes an arm observation
    # interpretable is that a human said which step to read for these exact bytes.
    unknown = RuleApplication(
        path=GATE, revision=UNTRANSCRIBED, run=31179223805, outcome="success", arm_outcome="skipped"
    )

    assert rule_for(UNTRANSCRIBED) is None
    assert basis_of(gate_landing(rule=unknown)) == BASIS_RULE
    assert basis_of(gate_landing(rule=unknown, landed_by="AlobarQuest")) == BASIS_HUMAN


def test_an_unattributed_landing_says_so_rather_than_claiming_a_basis() -> None:
    permitted = landing_observation(gate_landing(rule=None))["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_UNATTRIBUTED
    assert "no gate run" in permitted["reason"]
    assert "rule_path" not in permitted


def test_every_route_records_the_same_observation_type() -> None:
    """One type for one kind of event; the route lives in `permitted_by`.

    Pinned as a set over all three routes rather than three separate equalities, so a change
    that reintroduced a per-route split would fail here even if each individual value were a
    real member of the vocabulary. `github_pr` in particular is NOT available to this adapter:
    it already means "a fact about a pull request bound to a work unit" in the reconciliation
    lane, and the two only fail to collide because their subject namespaces are disjoint.
    """
    types = {
        landing_observation(landing)["observation_type"]
        for landing in (gate_landing(), human_landing(), push_landing())
    }
    assert types == {"landing"}


def test_the_landing_identity_is_the_commit_and_the_key_is_the_facts() -> None:
    """The reference must NOT move with the facts, and the key MUST.

    A commit on a branch is immutable, so one landing is one row forever -- content-addressing the
    reference would let drifted facts open a quiet second row. The key is content-addressed so an
    unchanged re-run replays and a changed fact reaches the same-source/different-facts branch.
    """
    first = landing_observation(gate_landing())
    again = landing_observation(gate_landing())
    drifted = landing_observation(gate_landing(files=("pyproject.toml",), files_changed=1))

    assert first == again
    assert drifted["source_reference"] == first["source_reference"]
    assert drifted["idempotency_key"] != first["idempotency_key"]


def test_observed_at_is_upstreams_clock() -> None:
    """With a wall-clock timestamp every pass would recompute a different fact hash for unchanged
    reality and conflict, forever."""
    assert landing_observation(gate_landing())["observed_at"] == LANDED.isoformat()


def test_a_large_landing_is_trimmed_to_fit_and_still_reports_its_true_size() -> None:
    files = tuple(f"src/orchestrator/services/{'x' * 90}_{index}.py" for index in range(30))
    body = landing_observation(gate_landing(files=files, files_changed=len(files)))
    facts = body["facts"]

    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= MAX_FACT_BYTES
    assert facts["what_changed"]["files_changed"] == 30
    assert len(facts["what_changed"]["files"]) < 30
    # Deterministic, or the same landing would encode differently on the next pass and conflict.
    assert body == landing_observation(gate_landing(files=files, files_changed=len(files)))


def test_a_landing_whose_own_fields_are_maximal_still_fits() -> None:
    body = landing_observation(
        gate_landing(
            title="t" * 4000,
            files=tuple(f"path-{index}" for index in range(30)),
            files_changed=30,
            checks=tuple(
                Check(name=f"check-{index}" * 8, conclusion="success", run=31179223805 + index)
                for index in range(30)
            ),
        )
    )
    encoded = json.dumps(body["facts"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= MAX_FACT_BYTES
    # Trimming the permission evidence must be visible, not silent.
    permitted = body["facts"]["permitted_by"]
    assert permitted["checks_observed"] == 30
    assert len(permitted["checks"]) < 30
    assert body["facts"]["what_changed"]["files"] == []


# ---------------------------------------------------------------------------
# ADR-0019 increment 5b: a landing permitted by a change record and a policy version.
# ---------------------------------------------------------------------------

CHANGE_MANAGER = "AlobarQuest/change-manager"


def policy_landing(**overrides: object) -> Landing:
    """The shape the orchestrator's estate-landing path produces.

    A pull request the UPDATE BOT opened, landed by the estate's App, with the two trailers the
    orchestrator writes into the squash body -- and NO factory claim, because there is no work
    unit, and no gate run, because neither repository where landing changes something already
    serving has a gate workflow at all.
    """
    base: dict[str, object] = {
        "repository": CHANGE_MANAGER,
        "commit": "c" * 40,
        "title": "build(deps): bump alembic from 1.18.5 to 1.19.0 (#50)",
        "pull_request": 50,
        "landed_by": "alobar-sds-dispatch[bot]",
        "rule": None,
        "claim": None,
        "policy": PolicyPermission(change_record=52, policy_version=2),
    }
    return gate_landing(**{**base, **overrides})


def test_a_landing_permitted_by_a_change_record_names_the_record_and_the_version() -> None:
    """Exit criterion 5's ledger half. Without it this landing is indistinguishable from a machine
    landing with no accountable basis, which is a class no detector reads."""
    permitted = landing_observation(policy_landing())["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_CHANGE_RECORD
    assert permitted["change_record"] == 52
    assert permitted["policy_version"] == 2
    assert permitted["landed_by"] == "alobar-sds-dispatch[bot]"
    # No rule keys and no unit: neither is true of this landing.
    assert not {"rule_path", "rule_run", "decision", "work_unit"} & set(permitted)


def test_the_basis_needs_a_MACHINE_as_well_as_a_claim() -> None:
    """A person who landed a pull request carrying these trailers landed it themselves. The
    conjunct is what stops the trailers alone reclassifying a human's act."""
    assert basis_of(policy_landing(landed_by="AlobarQuest")) == BASIS_HUMAN


def test_a_landing_whose_merger_github_did_not_report_is_not_given_this_basis() -> None:
    """The machine conjunct, pinned by the only case that can tell it apart.

    A human merger falls to `human` one branch earlier, so asserting on one proves nothing about
    this conjunct -- a mutation dropping it survived that test. `landed_by: None` reaches this
    branch: the trailers are there, and nothing observed who acted on them. A basis that names a
    permission for an act nobody was reported to have performed is worse than saying so.
    """
    assert basis_of(policy_landing(landed_by=None)) == BASIS_UNATTRIBUTED


def test_a_machine_landing_with_no_claim_of_either_kind_stays_unattributed() -> None:
    """Never fabricate a basis. This is the class the estate records when it cannot say why."""
    assert basis_of(policy_landing(policy=None)) == BASIS_UNATTRIBUTED


def test_a_landing_carrying_BOTH_claims_records_the_stronger_one() -> None:
    """A work-unit claim is re-evaluated against the orchestrator's durable rows by the audit; a
    change-record claim is not re-evaluated here at all. The ordering says which is checked.
    """
    both = policy_landing(claim=FactoryClaim(work_unit=UNIT, package_revision=1))

    assert basis_of(both) == BASIS_FACTORY


def test_the_reason_says_only_what_stays_true() -> None:
    """Every string a landing puts in `facts` is frozen at the first observation of it, so a
    correction afterwards is a conflict on a landing where nothing changed."""
    reason = landing_observation(policy_landing())["facts"]["permitted_by"]["reason"]

    assert "no detector re-evaluates it here" in reason
    for forbidden in ("2026", "today", "verified", "checked against"):
        assert forbidden not in reason


def test_a_landing_stating_no_delta_records_the_key_as_present_and_null() -> None:
    """ADR-0034, and the pin `audit_landing` names when it tests the KEY rather than the value.

    A requirement range carries a dependency name and no update type. Recording that as
    `update_type: null` asserts something -- the update bot declared no delta -- where omitting
    the key asserts that this program could not read the trailer at all. Revision 3457db3c
    permits the first and the audit reports the second, so the two must not encode alike.

    The three keys arriving TOGETHER is the other half, and it is what lets the audit test one
    of them: a landing that records an ecosystem and no update type, or the reverse, would make
    the detector's single-key test wrong without changing what it reads.
    """
    landing = gate_landing(
        update=UpdateMetadata(dependency="setuptools", ecosystem="uv", update_type=None)
    )

    permitted = landing_observation(landing)["facts"]["permitted_by"]

    assert permitted["update_type"] is None
    assert "update_type" in permitted
    assert permitted["dependency"] == "setuptools"
    assert permitted["ecosystem"] == "uv"


def test_a_landing_whose_trailer_could_not_be_read_records_none_of_the_three() -> None:
    """The other side of the same pin. All three or none -- never one without the others."""
    permitted = landing_observation(gate_landing(update=None))["facts"]["permitted_by"]

    assert not {"dependency", "ecosystem", "update_type"} & set(permitted)
    # Still a rule-basis landing: the gate ran and a machine landed it. What is absent is what
    # the update was, not what permitted it.
    assert permitted["basis"] == BASIS_RULE


# ---------------------------------------------------------------------------
# ADR-0038: a landing the orchestrator made into the inert population.
# ---------------------------------------------------------------------------

FACTORY_RUNNER = "AlobarQuest/factory-runner"


def inert_landing(**overrides: object) -> Landing:
    """The shape ADR-0038's lane produces.

    An update-bot pull request landed by the estate's App into a repository where landing on the
    default branch changes nothing already serving, carrying the ONE trailer that lane writes --
    no change record, because the population is declared in the policy rather than per landing;
    no factory claim, because there is no work unit; and NO GATE RUN, because the removal that
    switched this lane on deleted the gate workflow in the same operation.
    """
    base: dict[str, object] = {
        "repository": FACTORY_RUNNER,
        "commit": "1" * 40,
        "title": "build(deps): bump actions/checkout from 4 to 5 (#28)",
        "pull_request": 28,
        "landed_by": "alobar-sds-dispatch[bot]",
        "rule": None,
        "claim": None,
        "policy": None,
        "inert_policy": InertLandingPermission(policy_version=6),
    }
    return gate_landing(**{**base, **overrides})


def test_a_landing_permitted_by_the_inert_policy_names_the_version_it_rests_on() -> None:
    """Without this the whole native-cascade population lands as `unattributed` once the cascade
    is removed -- a class `audit_landing` returns nothing at all for."""
    permitted = landing_observation(inert_landing())["facts"]["permitted_by"]

    assert permitted["basis"] == BASIS_INERT_POLICY
    assert permitted["policy_version"] == 6
    assert permitted["landed_by"] == "alobar-sds-dispatch[bot]"
    # No change record, no rule keys, no unit, and no update metadata: none is true of this
    # landing, and the update keys are deliberately absent because nothing re-reads them here.
    assert not {"change_record", "rule_path", "rule_run", "decision", "work_unit"} & set(permitted)
    assert not {"dependency", "ecosystem", "update_type"} & set(permitted)


def test_the_inert_basis_needs_a_MACHINE_as_well_as_a_trailer() -> None:
    """The `is_machine` conjunct, pinned by the ONLY case that can tell it apart.

    A human merger falls to `human` one branch earlier, so asserting on one proves nothing about
    this conjunct -- exactly as the change-record basis records one branch up. `landed_by: None`
    reaches this branch: the trailer is there, and nothing observed who acted on it.
    """
    assert basis_of(inert_landing(landed_by=None)) == BASIS_UNATTRIBUTED


def test_a_PERSON_merging_an_inert_pull_request_is_still_a_person() -> None:
    """Unreachable in production and asserted anyway, for the same reason its sibling is: the
    trailer lives in the squash body the orchestrator composes, so a landing a person made cannot
    carry it. What this pins is that the ordering below `human` is not accidental."""
    assert basis_of(inert_landing(landed_by="AlobarQuest")) == BASIS_HUMAN


def test_a_machine_landing_with_no_inert_trailer_stays_unattributed() -> None:
    """Never fabricate a basis. A machine merge alone is not a policy landing."""
    assert basis_of(inert_landing(inert_policy=None)) == BASIS_UNATTRIBUTED


def test_the_inert_reason_says_only_what_stays_true() -> None:
    """Every string a landing puts in `facts` is frozen at the first observation of it. In
    particular this one must not assert that the repository IS inert -- that is what the policy
    declared at the version named, and a repository can stop being inert afterwards."""
    reason = landing_observation(inert_landing())["facts"]["permitted_by"]["reason"]

    assert "re-reads only the checks" in reason
    for forbidden in ("2026", "today", "verified", "is inert", "was checked"):
        assert forbidden not in reason
