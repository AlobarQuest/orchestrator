# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Intake services (`src/orchestrator/services/intake/`): decomposition policy, the runner brief, approvals, follow-ups and the authority gate

- Change the runner brief in both repositories together (`test_runner_brief_contract.py`); `RunnerBrief`
  stays `extra="allow"`. Archive #49.
- Serve a new brief field only after factory-runner declares it and the pin advances
  (`test_brief_consumer_compatibility.py`); renaming the required check moves the protected context too.
- `record_approval` enforces no lifecycle state for either subject type; an approval's reach is
  bounded by its consumers (the `AWAITING_APPROVAL -> READY` guard and admission), not by refusal.
  History: `docs/history/claude-md-invariants-archive.md` #74.
- Minted follow-up units are created in `AWAITING_REVIEW`, and minting refuses rather than inherit
  an unknown reach. History: `docs/history/claude-md-invariants-archive.md` #88.
- A rotation unit needs no per-unit authority approval while `factory-policy.toml`'s
  `rotation_known_good` pattern recognises its envelope under every reach member. Rotation patterns
  apply only when `is_rotation_unit` holds for the unit and its revision, never on envelope shape
  alone, and never recognise `host_without_overlap`. Rotation units are never dispatched, so the
  claim re-asks policy for a unit nobody approved, refusing `authority_approval_missing` or
  recording `authority.human_gate_not_required` (`services/intake/authority_gate.py`). Detail:
  `docs/operations/rotation-executor.md`.
- `decomposition._policy_approval` approves a decomposition SYSTEM proposes, in the proposal's own
  transaction, on one of two branches, rotation or bump. A human's or WORKER's proposal, and every
  other decomposition, waits for a person on `/review`
  (`test_a_person_s_proposal_waits_for_a_person`, `test_a_worker_s_proposal_waits_for_a_person`).
- A rotation's one click is its package revision approval (ADR-0055 amendment 16): change-manager
  approves its `work` record by policy.
- The rotation branch approves only when the revision is a standing rotation package with a
  recorded approval (`test_a_revision_an_approval_policy_approved_waits_for_a_person`).
- The rotation branch needs every dependency unit-on-unit `completed`
  (`test_a_dependency_on_anything_but_a_unit_completing_waits_for_a_person`).
- The rotation branch needs every unit a rotation unit `human_authority_gate` finds recognised
  (`test_a_unit_no_rotation_pattern_recognises_waits_for_a_person`).
- A standing bump needs no click (ADR-0006 amendment 2): the bump branch takes any revision with the
  `dependency-update` profile or a `policy:dependency-update@` approver.
- The bump branch needs that approver and that profile both to hold
  (`test_a_revision_the_bump_policy_did_not_approve_waits_for_a_person`,
  `test_a_revision_of_another_profile_waits_for_a_person`).
- The bump branch needs `standing` true (`test_a_revision_that_isn_t_standing_waits_for_a_person`).
- The bump branch needs reach exactly `source_repository`
  (`test_a_reach_past_the_repository_waits_for_a_person`).
- The bump branch needs one unit (`test_two_units_wait_for_a_person`) with no dependencies
  (`test_a_dependency_waits_for_a_person`).
- The bump branch needs every criterion mapped to the unit and none retained
  (`test_a_retained_criterion_waits_for_a_person`).
- The bump branch needs attempts within the unit's budget
  (`test_more_attempts_than_the_envelope_budgets_waits_for_a_person`).
- The bump branch needs only dependency-update patterns to recognise the unit
  (`test_recognition_by_anything_but_a_bump_pattern_waits_for_a_person`).
- The bump branch needs `bump_binding_violations` empty
  (`test_a_mutation_of_something_else_waits_for_a_person`).
- The binding is a whole-command allowlist of the generator's forms, and covers `allowed_commands`
  and `verify_commands` too; a generator change means regenerating
  `dependency_update_proposals.json`.
- The bump branch needs the unit's `title` and `outcome` to be the generator's (`bump_text_differs`,
  `test_text_the_generator_didn_t_write_waits_for_a_person`).
- A bump's `dependency_updates` grant is asked before either branch: the proposal itself is refused
  without it (`test_a_grant_of_another_service_is_refused_at_the_proposal`).
- Each refusal of a SYSTEM proposal on either branch, every bump refusal, and an approval that fails
  after policy said yes, records one `decomposition.policy_not_applied` per proposal, served as
  `policy_not_applied` on the proposal routes (`test_a_replayed_refusal_records_one_event`,
  `test_an_approval_that_cannot_complete_says_so`).
- `register_approved_unit` admits SYSTEM only with `approved_by_policy`
  (`test_system_may_register_a_unit_only_for_a_decomposition_policy_approved`).
