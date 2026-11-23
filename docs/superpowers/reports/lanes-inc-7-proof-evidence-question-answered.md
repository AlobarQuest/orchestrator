# Question: lanes-inc-7-proof-evidence (W-274)

The handoff's stop point is met: the orchestrator rejects any new key in the evidence payload.
Which option do you want for naming each consumer's proof?

## What rejects a new key (at `a3109318`)

- **Ingest.** `src/orchestrator/services/verifier/rotation_evidence.py:70-84`: `RotationStepEvidence`
  has `model_config = ConfigDict(extra="forbid", strict=True)`. `check_rotation_evidence` validates
  every rotation evidence row with it at line 114 and refuses the row as
  `rotation_evidence_invalid`. A worker filing a `proof` key fails the step, and so the unit.
- **Verifier.** `src/orchestrator/services/verifier/rotation_verification.py:60` re-validates the
  stored payload with the same model. An unknown key hands the criterion back to a person.
- **Pin.** `tests/fixtures/rotation_evidence_schema.json:96` has `"additionalProperties": false`.
  `tests/services/test_rotation_evidence.py:29`, `tests/rotation_worker/test_contract.py:39`,
  `test_bws_machine_token.py:26`, and `test_lane_credentials.py:431` read it.
- **Other repositories.** The intent-packages hits for `rotation-step/1` are criterion text
  (for example `packages/rotation-rotation-canary/package.yaml`), not key validation. No other
  repository pins the payload's keys.

## What reads the note

- `rotation_verification.py:34`: `_COVERAGE` is a prefix match on
  `"<n> of <m> consumers hold or attest the new value"`. The count must stay first.
- `rotation_verification.py:38` and `:123`: the substring `" before the revoke"` turns on the
  n-equals-m rule. Proof text must not contain it.
- `rotation_evidence.py:131` and `:37`: the secret scan, and `LONG_RUN` (`[A-Za-z0-9_+/=]{32,}`)
  over every string. A rendered destination in the note must not form such a run.
- `src/rotation_worker/evidence.py:56`: `StepEvidence` raises on a note over 500 characters. In a
  claimed step that fails the unit. The issue path appends `"; deleted staging <id>"`
  (`contract.py:784`).
- `tests/contract/test_rotation_step_contract.py:19-20`: pins the coverage and before-revoke text
  in `contract.py`'s source.

## Options

- **A. Name the proof in the note (recommended; inert).** For example:
  `"2 of 2 consumers hold or attest the new value; the lane variable's proof is loaded"`. No
  server change, no new key, and the deployed orchestrator accepts it today. The `loaded`
  fingerprint is already the `new` entry in `fingerprints` (the match is equality), so the note
  needs only the proof's name. Doing this needs a small optional provider hook, because
  `contract.py:593` gets only a bool from `consumer_loaded`. Only `bws_lane_token` implements the
  hook, so every other adapter's evidence stays byte-identical. Cost: the proof sits in free text,
  so a machine reader must parse a string. I'd keep it one fixed phrase per proof.
- **B. Add a `proof` key.** This changes `RotationStepEvidence`, the pinned fixture, and the
  verifier. The orchestrator must deploy before any VM reinstall picks up the worker, so the
  merge isn't inert. It also bumps or widens the `rotation-step/1` contract.
- Probes aren't a third option: `_probes` (`rotation_verification.py:83`) requires a refused
  known-bad control on every probed target, and there's none for a fingerprint read.

## State of the branch

The four docs fixes (#790 follow-ups 1 to 4) are committed locally on
`hq/lanes-inc-7-proof-evidence` as `ce44514e`, not pushed. The T1 row is at line 354 at
`a3109318`. My header additions shift it by nine lines, so the cites read line 363, which is
correct in the changed file. No code is written yet.
