# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Landing services (`src/orchestrator/services/landing/`): the pr-merge policy path, landing rows, branch updates and the revert watch

- `mergeable_state: blocked` has four causes; the landing lane reads workflow runs at the head to
  separate `landing_checks_not_clean`, `landing_checks_awaiting_verdict` and
  `landing_checks_in_flight`. The Checks API is 403 to the App. Detail:
  `docs/operations/landing-lanes.md`.
- `qualifies_for_branch_update` (acting) and `freshness_derived_refusals` (reporting) are separate
  criteria; never fold a refusal into a shared set to excuse it for one consumer. Detail:
  `docs/operations/landing-lanes.md`.
- The post-landing watch selects a factory merge from rows alone: status `merged` or
  `already_merged`, a unit that is a dependency update by both class and profile, `landing_inert`
  on the row's event (the estate's answer when our call was made; an `already_merged` found before
  any call, or at a head other than the row's, never carries it, and an event without it is never
  watched), and no record naming it as a revert. `UnitPrMerge.repository` is the envelope's
  spelling and `landing_reverts` is folded, so every join folds it. Detail:
  `docs/operations/landing-lanes.md`, "Automatic reverts (inert repositories)".
- A landing row whose reason is `merge_refused_by_remote:<code>` is an unknown outcome
  (`interfaces.outcome_is_unknown`), answered `landing_outcome_unknown`; the bare reason is
  terminal. Each lander asks about every such row first, from `GET
  /api/v1/landing-unknown-outcomes`, because no enumeration reaches a landed pull request.
  `lane_act._resettle`, in its own committed step, is the only code that may change or remove a
  landing row (`test_only_lane_act_may_write_a_landing_row_and_only_resettle_changes_one`, which
  tracks rows passed as parameters, built by calling the model, or held in locals assigned from
  `select(Model)` or `session.get(Model, ...)`; a row reached any other way escapes it): landed
  makes it `already_merged`, repoints `event_id` to an event with that action and runs `on_landed`
  (at a head other than the row's, on this or the reconciling read, the event's action is
  `<lane>.already_merged_other_head`, which the revert watch never selects);
  confirmed unlanded removes it, and that stands whatever admission then says. Events are unique
  and append-only, so a removed row's key is spent (`_event_key`). Detail:
  `docs/operations/landing-lanes.md`, "An unknown landing outcome".
- `pr-merge`'s authority term is a bound human approval or the policy path (ADR-0025 amendment 4).
  The bound approval suffices for every change class and is blind to any switch
  (`test_a_bound_approval_never_consults_the_policy`,
  `test_a_human_approved_merge_ignores_the_switch`).
- The policy path needs a `dependency-update` envelope (`test_the_change_class_is_asked_for_itself`).
- The policy path needs a `standing_bump_refusal`-clean revision, the decomposition gate's own
  conditions, reach exactly `source_repository`
  (`test_a_unit_outside_the_policy_path_needs_its_approval`).
- The policy path needs the estate to answer `inert`
  (`test_a_redeploying_repository_stays_with_a_person_even_with_its_record`).
- The policy path needs recognition at merge time, only by dependency-update patterns
  (`test_recognition_is_asked_again_at_merge`).
- The policy path needs the `dependency_updates` grant
  (`test_an_ungranted_repository_is_refused_on_both_terms`).
- The policy path needs `ORCHESTRATOR_BUMP_MERGE_ENABLED` on. Off, a unit the path covers refuses
  `merge_policy_path_off` in place of `authority_approval_not_bound`
  (`test_the_switch_off_refuses_a_unit_the_policy_path_would_admit`); other terms report beside it,
  so it means waiting only as the sole refusal
  (`test_the_switch_off_reports_beside_another_terms_refusal`).
- The admission answer writes nothing (`test_reading_the_answer_records_nothing`).
- The act records `authority.human_gate_not_required` beside a landing its own call made, including
  one reconciled after that call at the head it named
  (`test_landing_after_a_lost_response_is_recorded_as_policy_lifted`), never beside an
  `already_merged` found before it or at another head
  (`test_a_pull_request_somebody_else_landed_records_no_lifted_gate`).
- A row whose reason is `merge_refused_by_remote:<code>` is an unknown outcome: `pr-merge-admission`
  answers `landing_outcome_unknown` (`test_the_answer_names_an_unknown_row_until_it_settles`), and
  the bump driver replays it whatever the pull request's state or the other refusals
  (`test_an_unknown_outcome_row_is_waiting_not_refused`).
- `pr_merge._resettle`, in its own committed step and asking no term, is the only code that may
  change or remove a `UnitPrMerge` row (ADR-0025 amendment 5;
  `test_only_pr_merge_may_write_a_factory_row_and_only_resettle_changes_one`).
- The policy path alone also reads the pull request in `pr_merge._act`, after it is known open and
  before landing: the head is `sds/<unit8>-attempt-<attempt_count>` in the repository itself and is
  the adjudicated head, the body has the whole-line `SDS-Unit:` trailer, and `pulls/{n}/files` reads
  fully (its length equals `changed_files`), neither end of any entry is a workflow or gate file,
  and a tooling manifest changed. Each failure is a `pr_merge_*` refusal with no `UnitPrMerge` row,
  so a person can still land it. Detail: `docs/operations/landing-lanes.md`, ADR-0025 amendment 4.
