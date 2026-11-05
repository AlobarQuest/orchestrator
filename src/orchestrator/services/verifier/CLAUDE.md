# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Verifier services (`src/orchestrator/services/verifier/`): adjudication, named-check and rotation evidence

- Generated post-deploy criteria are verifier-owned: public adjudication rejects their AC IDs
  (`test_generated_post_deploy_unit_rejects_direct_public_adjudication`). Archive #8.
- A verifier adjudication may arise only from `verify_work_unit`
  (`test_verifier_may_not_adjudicate_outside_its_own_evaluation`,
  `test_only_the_verify_command_may_assert_its_own_evaluation`). Reconciliation detects reality
  changing, never reality misreported, so worker-attested evidence has no downstream net. Archive #109.
- Named-check evidence is ingested only for criteria declared `automated_check`
  (`test_named_evidence_is_accepted_only_on_an_automated_check_criterion`), with `check_name` the JOB
  name. A human adjudication does not complete a unit: `AWAITING_REVIEW -> COMPLETED` is a separate
  human gate (`DESIGNED_HUMAN_GATES`). Archive #154.
- A rotation unit's `external_attestation` criterion is decided by the verifier from the current
  `rotation.step` evidence only when the unit is a rotation unit and the evidence was filed by its
  owning executor (`RotationExecutors.owner`, passed in `VerifyCommand.rotation_executors`);
  `rotation_verification.rotation_step_verdict` never fails a criterion, and anything else stays
  with a person. The floor stays human: never add `external_attestation` to a deterministic set.
  The `unit-verifier` lane runs `/verify` as VERIFIER. Detail: ADR-0055 amendment 14,
  `docs/operations/rotation-executor.md`.
- Worker-filed or SYSTEM-recovered evidence on a rotation unit must be a `RotationStepEvidence`
  payload with no `stable_ref`, pass the secret scan, and hold no 32-character unbroken run
  (`rotation_evidence.check_rotation_evidence`); a recovered row also carries the orchestrator's
  `recovery` key. Verifier-owned evidence isn't checked. The schema is a contract with the
  rotation executor, pinned by `tests/fixtures/rotation_evidence_schema.json`. Never extend the
  check to every unit without first running it over stored evidence. Detail:
  `docs/operations/driving-a-unit.md`.
