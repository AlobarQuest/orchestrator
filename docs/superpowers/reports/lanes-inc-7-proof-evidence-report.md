Outcome: done

# Report: lanes-inc-7-proof-evidence (W-274)

- **Branch:** `hq/lanes-inc-7-proof-evidence`, base `a3109318`.
- **PR:** #791, opened ready for review. HQ reviews and merges.
- **Commits:** `ce44514e` (the four docs fixes), `2507a474` (the proof in the note), the report,
  and one commit with the `_target` cite fix and the review's test fix.

## What I built

### The consumer check names its proof (#789 follow-up 4, HQ's option A)

The evidence is built in `_verify_consumers`, `src/rotation_worker/contract.py:790` at
`2507a474`. The `StepEvidence` constructor is at line 813, and the issue's deletion record, which
repeats the note, is at line 823. At `a3109318` the function is at line 753 and the record at
line 774.

- `contract.py` gains `ConsumerProof` (`"patch"` or `"loaded"`), `PROOF_PHRASES` (one fixed
  phrase per proof), and an optional `runtime_checkable` protocol, `NamesConsumerProof`, with one
  method, `consumer_proof(consumer)`. It sits beside `ShapeVerified`, in the style of
  `worker.WaitsForLoad`.
- `_proofs(run, missing)` names each distinct proof that passed for a hosted consumer the provider
  verified, once each, in destination order. It names nothing for a scheduled consumer, a consumer
  still missing the new value, or a provider without the hook.
- `_verify_consumers` appends `"; <phrase>"` after today's text: after the coverage count, and
  after `"; the old value is <outcome> before the revoke"` where that appears. The issue's
  `"; deleted staging <id>"` follows, as before.
- `BwsLaneTokenProvider.consumer_proof` returns `"patch"` when `self._loaded is None` (a row
  without `helper_target`, and every issue), else `"loaded"`. `consumer_loaded` branches on the
  same predicate, so the label can't name a proof the decision didn't use. The hook only labels:
  `consumer_loaded` still decides, and a mismatch still raises.
- The phrases:
  - `patch`: `proof: patch (the variable's PATCH answered 2xx)`
  - `loaded`: `proof: loaded (the loaded fingerprint equals the new value's)`

  For `loaded`, the fingerprint the payload carries is the filed `new` fingerprint: prefix and
  length, never a value. The match is equality, so the loaded fingerprint is the `new` one.

A lane token's reissue note on a row with `helper_target` now reads:
`2 of 2 consumers hold or attest the new value; the old value is live before the revoke; proof:
loaded (the loaded fingerprint equals the new value's)`.

### The stop-point search (before code)

The question file records what I searched and what refuses a new key. HQ renamed it to
`lanes-inc-7-proof-evidence-question-answered.md`.

- `RotationStepEvidence` is `extra="forbid"` (`rotation_evidence.py:70-84`, checked at `:114`).
- The verifier re-validates the stored payload (`rotation_verification.py:60`).
- The pinned schema has `additionalProperties: false` (`rotation_evidence_schema.json:96`).

HQ chose option A: the proof goes in the note, with no new key. No schema, fixture, verifier, or
`test_rotation_step_contract.py` change was made, so the merge is inert for the server.

### The four docs fixes (#790 follow-ups 1 to 4), in `ce44514e`

1. **The T1 cites.** Amendment 29 item 2 and the amendment 29 and 31 tables now cite "T1 (line
   363)". The row is at line 354 at `a3109318`. Fix 3 adds nine header lines, so it's at line 363
   in the changed file. Both cites are correct in the file as merged.
2. **The long bullet.** The `src/rotation_worker/CLAUDE.md` bullet #790 added is rewrapped, same
   words. That file's other long lines (1 and 90) predate #790 and aren't touched.
3. **The header list.** The ADR-0055 header's "Amended" list gains amendments 25 to 29, each
   "date, amendment N, proposed (summary)", with the date and status from its own heading.
4. **The helper cites.** Amendment 29 item 3's helper cites now read `deploy/vps/sds-pg-rotate`,
   lines 1-15 (docstring), 103-119 (`_target`), and 179-195 (`_loaded`), as at `a3109318`. They
   were 1-12, 100-106, and 166-182.

## Tests and checks

- **`make check`:** green. 14,681 collected: 14,680 passed, 1 skipped. It ran again after the review fix, with the same counts.
- **Targeted:** `tests/rotation_worker/test_lane_token_loaded.py`,
  `tests/contract/test_consumer_proof_note.py`, and `tests/contract/test_rotation_step_contract.py`
  pass.
- **Unmodified:** `/usr/bin/git diff a3109318 -- tests/contract/test_rotation_step_contract.py
  tests/fixtures/rotation_evidence_schema.json` is empty.

### New tests

In `tests/rotation_worker/test_lane_token_loaded.py`, driven through `worker._take` with the real
adapter and contract:

- `test_a_reissue_on_a_helper_target_names_the_loaded_proof_and_only_fingerprints`: the note ends
  with the `loaded` phrase, names it once, and doesn't name `patch`. The filed `new` fingerprint is
  exactly the token's prefix and length.
- `test_a_row_without_a_helper_target_names_the_patch_proof`: the note ends with the `patch`
  phrase and doesn't name `loaded`.
- `test_an_issue_names_the_patch_proof_in_both_its_verify_records`: both records name `patch`,
  and the deletion record is the note plus `; deleted staging `.
- `test_a_failed_loaded_read_files_no_proof_and_no_token`: a mismatch after the claim fails the
  unit and files no verify_consumers evidence.
- The first four also assert that no token value appears anywhere in the captured output: the
  evidence bodies, the failure reasons, and every log record with its extras.
- `test_a_proof_is_named_once_however_many_consumers_it_proved`: two hosted consumers name the
  proof once, and a missing consumer names none.
- `test_a_provider_that_names_no_proof_adds_nothing_to_the_note`.
- `test_only_the_lane_token_names_its_consumer_proof`: scans every adapter module, and only
  `BwsLaneTokenProvider` has `consumer_proof`. With no hook, `_proofs` adds nothing, so every
  other adapter's note is byte-identical. The existing exact-note tests hold this too:
  `test_change_manager_distributed.py:99` and `test_orchestrator_distributed.py:136` assert `==`.

In `tests/contract/test_consumer_proof_note.py` (registered in `layers.toml` beside
`test_rotation_step_contract.py`):

- `test_each_proof_note_is_accepted_and_judged_as_today_s_note`, for the reissue with `loaded`,
  the reissue with `patch`, and the issue with `patch` plus its deletion record:
  - Each filed payload passes the real `check_rotation_evidence`, the schema and the secret scan.
  - The real `rotation_step_verdict` gives `None` (decided, no person) both for the payload and
    for the same payload with the phrase removed.
- `test_the_longest_proof_note_fits_both_length_limits`: 6-digit counts, the `indeterminate`
  before-revoke clause, both phrases, and a full BWS id in the deletion suffix give 272
  characters, within `MAX_NOTE` (500) and the server's `MAX_TEXT` (500). `StepEvidence` accepts
  it.
- `test_no_proof_phrase_could_turn_on_the_before_revoke_rule_or_form_a_long_run`.

### Mutation proof

The script is in `/private/tmp/w274-mutate/`. Each mutation is applied to the source, then
`__pycache__` is cleared, the two proof test files run, and the file is restored from a backup
copy. The tree was clean afterwards. Each run was without `-x`, so the table lists every test
that failed. All 15 mutations were killed, with no survivors, and every new test kills at least
one.

| # | Mutation | Failing tests |
|---|---|---|
| M1 | The lane always names `patch` | `reissue_on_a_helper_target_names_the_loaded_proof`; `judged_as_today_s_note[reissue-loaded]` |
| M2 | The lane always names `loaded` | `without_a_helper_target_names_the_patch_proof`; `issue_names_the_patch_proof`; `judged_as_today_s_note`, both patch cases |
| M3 | A proof named per consumer (no dedupe) | `proof_is_named_once_however_many_consumers_it_proved` |
| M4 | A missing consumer still names a proof | `proof_is_named_once_however_many_consumers_it_proved` |
| M5 | No proof named at all | All three proof tests; `judged_as_today_s_note`, all three cases |
| M6 | The proof put before the coverage count | All three proof tests; `judged_as_today_s_note`, all three cases |
| M7 | The `patch` phrase contains " before the revoke" | `judged_as_today_s_note[issue-patch-and-deletion]`; `no_proof_phrase_could_turn_on_the_before_revoke_rule` |
| M8 | The note carries the new value after the phrase | All three proof tests |
| M8b | The value before the phrase, so the ending still matches | All three proof tests, by the no-token assertion |
| M9 | A phrase too long for the note | `reissue_s_verify_passes_on_the_keeper_s_fingerprint`; `reissue_on_a_helper_target_names_the_loaded_proof`; `judged_as_today_s_note[reissue-loaded]`; `longest_proof_note_fits_both_length_limits` |
| M10 | Another adapter (`fake`) gains `consumer_proof` | `only_the_lane_token_names_its_consumer_proof` |
| M11 | A phrase forms a 32-character run | `judged_as_today_s_note[reissue-loaded]` (the secret scan); `no_proof_phrase_could_..._form_a_long_run` |
| M12 | The mismatch error carries the new value | `context_built_without_issue_takes_the_loaded_read` |
| M13 | A loaded mismatch no longer raises | `claimed_read_fails_the_unit_on_anything_but_a_match[mismatch]`; `failed_loaded_read_files_no_proof_and_no_token`; `context_built_without_issue_takes_the_loaded_read`; `no_log_line_or_failure_reason_holds_a_token` |
| M14 | `_proofs` drops its `isinstance` guard | `provider_that_names_no_proof_adds_nothing_to_the_note` |

"All three proof tests" means the reissue-`loaded`, reissue-`patch`, and issue tests in
`test_lane_token_loaded.py`. Three of the kills need a note:

- **M6.** In the server-side file, the first assertion to fail was the phrase-placement check, not
  the verifier. I checked the verifier directly: a note that starts with the phrase gets "the
  consumer count is unreadable", and today's note and the suffixed note both get `None`.
- **M12.** The worker scrubs a failure reason, so the new failed-read test didn't see the value.
  The existing exception-text test caught it.
- **M13.** This shows naming the proof loosens nothing: a mismatch that stops raising fails the
  new test and three existing ones (Infra Brain 1032).

### Code-standards risk and review

- `code-standards risk --repo .`: `high-risk` (rule `auth`: `bws_lane_token.py` and
  `test_lane_token_loaded.py`).
- `vendor-review` (`/tmp/vendor-review-vb1oot9x`, exit 0): the review is complete for the seats
  that ran. Claude and Codex each approved with no findings, and agy is off by configuration. No
  findings were kept or dropped, so there was nothing to reproduce.
- `/code-review` (default level, `origin/main...HEAD`): five nits, all handled.
  - Two said `_target` ends at line 120, so `103-119` was wrong. I checked the file: `def _target`
    is at line 103, `return target` at 119, and line 120 is blank, so `103-119` stands. That also
    makes amendment 31's existing `103-119` cite correct, so I dropped my earlier follow-up that
    called it short.
  - One asked that the loaded case set its row and helper explicitly instead of relying on the
    harness default. Fixed, then `make check` reran.
  - One said `_proofs` re-derives which consumers were proved, so the label could drift. This is
    the known limit in the self-review.
  - One said that on an issue, `consumer_loaded` can skip the `PATCH` when distribute already
    wrote the value, yet the note says the `PATCH` answered 2xx. That's by design: `_written` is
    set only by a 2xx write in the same unit, on the same provider instance, so the phrase holds.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: header entries for
  amendments 25 to 29; the T1 and helper cites.
- `src/rotation_worker/CLAUDE.md`: one bullet rewrapped.
- `src/rotation_worker/contract.py`: `ConsumerProof`, `PROOF_PHRASES`, `NamesConsumerProof`,
  `_proofs`, and the note suffix.
- `src/rotation_worker/adapters/bws_lane_token.py`: `consumer_proof`, the import, and one module
  docstring line.
- `tests/rotation_worker/test_lane_token_loaded.py`: the new tests.
- `tests/contract/test_consumer_proof_note.py`: new.
- `layers.toml`: the new test file's entry.
- `docs/superpowers/reports/lanes-inc-7-proof-evidence-question-answered.md`: the question, as
  HQ renamed it.

## Self-review

- **Inert.** No new key and no schema change. The deployed orchestrator's ingest check and
  verifier accept the new note, and the test runs both. No `.cred-consumers.toml`, `targets.toml`,
  `provision.sh`, helper, or registry row change.
- **No check loosened.** `consumer_loaded`, `unverified`, the waits, and the mismatch raise are
  unchanged. A failed read files no evidence, so no proof is named for it.
- **Bounded text.** Two constants, named at most once each, and no destination, container, or id.
  The longest possible note is 272 characters.
- **Known limit.** The label and the decision are two methods sharing one predicate, not one call.
  `test_a_row_without...` and `test_a_reissue_on_a_helper_target...` would catch them diverging
  (M1, M2).

## Concerns and follow-ups

- **Option B, a `proof` key.** It would change the `rotation-step/1` contract, the model, the
  pinned schema, and the verifier, and the orchestrator must deploy before any VM reinstall. Take
  it if a machine reader comes to need the proof as a field rather than a phrase.
- **More stale cites in ADR-0055, not in this handoff's scope.** Amendment 29 item 2 cites
  decision 2 at "lines 133-149" and the root's asset row at "line 338". In the changed file,
  decision 2's section starts at line 146 and the row is at line 351 (137 and 342 at `a3109318`).
  I didn't read the passage to find its exact end.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** no logging added. The note goes into the
  existing evidence row.
- **Code Brain 4 (no silent swallow):** no `try` or `except` added. `consumer_loaded` still raises
  on a mismatch, and the claimed step fails the unit.
- **Code Brain 5 (never log secrets):**
  - The phrases are constants, and the `loaded` proof's fingerprint is only the filed prefix and
    length.
  - Four tests assert that no token value is in the evidence, the failure reasons, or the log
    records. M8, M8b, and M12 prove those assertions bite.
- **Infra Brain 591, 592, 593 (no BWS token in files or config):** test values are the existing
  placeholders (`lane-token-placeholder-new`, a zero UUID), none in the token shape. No env or
  config change.
- **Infra Brain 1032 (no revoke without attested consumers):** naming the proof changes no
  decision. `unverified`, `loaded_wait`, and `consumer_loaded` are unchanged. The verifier gives
  the same verdict with and without the phrase, and no phrase can enable or disable the
  before-revoke rule (M7).
- **Infra Brain 212 (HTTPS):** doesn't apply; there's no domain change.
- **Repo rules:**
  - The new test file is placed in `layers.toml`.
  - The new public names in `rotation_worker` are reachable from production: `_verify_consumers`
    calls them, and `make check`'s guards pass.
  - The evidence contract is unchanged, so no deploy is needed first.
  - `make check` and the risk check ran. The review is reported in the preceding section.
