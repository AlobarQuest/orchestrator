# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The orchestrator kernel (`src/orchestrator/kernel/`): authority envelopes, standing context and the runner-authority predicate

- Generic authority approvals satisfy work-unit readiness only: an authority-expanding
  standing-context update needs a named human approval bound to the exact standing-context
  fingerprint (`kernel/context.py::classify_context_update`, via `services/lifecycle/context.py`;
  `test_a_generic_authority_approval_does_not_satisfy_an_expanding_context`).
- `AuthorityEnvelope.normalized()` is what an authority approval attests: fields outside
  `KNOWN_FIELDS` contribute only their names. Authority-bearing fields must be known; adding one
  rewrites every authority fingerprint. History: `docs/history/claude-md-invariants-archive.md` #15.
- Key runner-shape gates on `RUNNER_ENVELOPE_FIELDS` (pinned by `runner_envelope_contract.json`),
  never `KNOWN_FIELDS`, which adds `unknown_fields`. Store `runner_payload(envelope)`, not
  `normalized()`. History: `docs/history/claude-md-invariants-archive.md` #148.
- `runner_authority_violation` is the one composed predicate, asked at breakdown ingress, authority
  approval (service and form) and admission; `test_authority_asking_points.py` pins the surfaces.
  History: `docs/history/claude-md-invariants-archive.md` #149.
- Unit registration asks only the field and level terms of that predicate, not the command term
  (`test_registration_asks_the_field_and_level_terms_and_not_the_command_term`).
