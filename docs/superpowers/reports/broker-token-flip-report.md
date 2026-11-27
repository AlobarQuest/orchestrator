Outcome: done

# Report: hand the broker's own token to the SDS

PR: https://github.com/AlobarQuest/orchestrator/pull/827, branch `hq/broker-token-flip`, base
`origin/main` `13cc544d` (still the tip when fetched before the push). Code commit `58d9511d`; this
report is the commit after it. Not merged.

## What I built

- `.cred-consumers.toml`, row `secrets-broker-bws-token`: `rotated_by_sds = true` and
  `rotate_requested = "2026-11-27"`. In the comment block, I rewrote only the three lines saying the
  row stays `false`. They now say the row was handed over once exit checks 1 to 3 passed (G-110,
  G-111) and item 5's bind was settled. They also say check 4 follows the flip, because the proposer
  and the executor act only on a handed-over row. The unlock-tier paragraph is unchanged. No other
  row, consumer, or exposure field changed, and I added no `[[credential.exposure]]`.
- ADR-0055 Amendment 34 item 9: the line `**Exit checks**, each before rotated_by_sds = true:` now
  reads: checks 1 to 3 pass before `rotated_by_sds = true`, and check 4 runs after it, because the
  proposer and the executor act only on a handed-over row (`checks.py`, `cli.py`). It wraps onto
  two lines. No other ADR text changed.
- `docs/operations/secrets-broker.md`, "Rotate the broker's token": the sentence about a `false`
  row now says the row is `true` and that "Run a rotation" follows that commit.
- `docs/operations/rotation-executor.md`, "Lane tokens and ping URLs": the sentence "The row stays
  `rotated_by_sds = false` … until … checks 1 to 3 pass" now says the row became `true` once they
  passed, and check 4 follows.
- `tests/rotation_worker/test_secrets_broker_row.py`:
  - `test_the_row_is_not_handed_to_the_sds` became
    `test_the_row_is_handed_to_the_sds_with_a_requested_date`. It asserts the row is in
    `rotated_by_sds(mirror)` and `rotate_requested == "2026-11-27"`.
  - The new test `test_a_dry_proposer_pass_would_generate_the_row_s_rotation_package` builds the
    `requested` `Due` from the row's own `rotated_by_sds` and `rotate_requested`. It then runs the
    proposer's `_pass` dry (no client, no observer, `granted=None`, `last_rotated` = the row's
    `created`) and expects `would-generate`. It also checks that nothing was written.
  - The module docstring was updated.

No other test or guard broke, so nothing else changed.

## Tests and checks

- `make check` (worktree DB `orchestrator_test_hq_w_330`, `PGTZ=UTC`): rc 0. Lint and pyright
  were clean. **15084 items collected; 15083 passed, 1 skipped.**
- Failing-without proof, with two controls on a scratch edit of the registry, restored afterwards:
  1. `rotated_by_sds = false` and no `rotate_requested` (the base state): both changed tests fail.
     The handover test fails with `assert 'secrets-broker-bws-token' in {...}`. The dry-pass test
     fails with `KeyError: 'rotate_requested'`.
  2. `rotated_by_sds = false` with the date kept: the dry-pass test fails with
     `'manual-request' != 'would-generate'`. So the flag alone decides whether the proposer plans a
     rotation.
- Standing-approval probe (scratch script, deleted afterwards): `standing_approval.stated_issue_plan`
  on this row's rendered package. With `first_issue=False`, which is what the proposer generates
  because infraops serves `created` as `last_rotated`, it returns `passed=False`, "the package's
  criteria state the rotation plan". With `first_issue=True` it passes. The probe tells the two
  cases apart, so a standing approval can't carry this row's revision 1, and a named person approves
  it.
- Reviews:
  - `code-standards risk`: `high-risk`, matching `secrets` on the test's file name.
  - `vendor-review` (`/tmp/vendor-review-mqif5x1g`): Claude and Codex finished, and agy is off by
    configuration. 0 findings, 0 dropped.
  - `/code-review` (low): no findings.
- Token-shape grep of added lines (`git diff origin/main...HEAD`, `+` lines): 0 matches for the BWS
  token shape (`0.` uuid, `.` secret) and 0 for an inline `BWS_ACCESS_TOKEN =`. The only uuids
  added are the existing row's `bws_uuid`, which is a secret id, not a value, and it was already in
  the file.

## Files changed

- `.cred-consumers.toml`
- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- `docs/operations/rotation-executor.md`
- `docs/operations/secrets-broker.md`
- `tests/rotation_worker/test_secrets_broker_row.py`
- `docs/superpowers/reports/broker-token-flip-report.md` (this file)

## Self-review

I checked every reader of `rotated_by_sds` for this row, and none needed a change:

- `rotation_worker/checks.py` 119 and `worker.py` 1123 now admit the row, as intended.
- `worker._other_keepers` and the adapter's `siblings()` now include the broker's keeper when
  another lane token binds. `test_no_other_lane_token_row_declares_the_same_reach` already holds
  that no other row shares its reach.
- `rotation_decomposer` and the work carrier admit it, subject to the member record's
  `credential_rotation` grant on this repository. Other rows in this registry already use that
  grant.
- `membership_check` already sees `true` rows in this file.
- `scripts/deploy_orchestrator.py` `lanes_in_registry` skips the row by id (`BROKER_TOKEN_ID`), so
  the deploy command still never plans the broker as a lane.
- `test_every_lane_holding_its_own_token_names_its_helper_target` is satisfied, because the row
  names `helper_target = "secrets-broker"`.

## Concerns

- **ADR wording left inconsistent, on purpose.** Item 9's first bullet still says
  "`rotated_by_sds = true` and a `rotate_requested` date are set only when every exit check
  passes". The handoff allowed one correction, to the exit-check line, which now states the real
  order. HQ may want that bullet reconciled in a later ADR touch.
- **ADR line numbers moved.** The corrected line wraps to two lines, so every line citation after
  it in ADR-0055 (Amendment 35 and later, which cite Amendment 34 lines 5696-5718) is one line off.
- **The proof's scope.** The test builds the `Due` from the row by hand, so it proves the
  proposer's side only. infraops reading the row as due from `rotate_requested` is infraops'
  behaviour, and this test doesn't exercise it.
- **What happens after merge.** On the proposer's next pass with infraops reporting the row due,
  it generates `rotation-secrets-broker-bws-token` rev 1 (the rotation plan). Devon approves it by
  name, and check 4 runs from that approval.

## Standards followed

- **Code Brain 3 (logging):** no logging added, in code or tests.
- **Code Brain 4 (error handling):** no `except` added. The new test lets every exception
  surface.
- **Code Brain 5 (security):** nothing logs or prints a value. The test reads registry metadata
  only, and the dry pass reads no secret.
- **Infra Brain 591, 592, 593 (no token in tracked files or history, no inline bootstrap
  token):** the token-shape grep of added lines found 0 matches, and no value appears anywhere in
  the diff.
- **Infra Brain 1032 (`cred.exposure-rotate`):** the row's two consumers and its exposure fields
  are unchanged, and I added no `[[credential.exposure]]`. `test_the_row_is_one_the_lane_token_adapter_accepts`
  and `test_the_keeper_consumer_is_the_bws_uuid` still pass.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable; the change adds no endpoint or FQDN.
- **Construction-mode ruling:** I didn't read the ruling file from this session. I applied the
  handoff's scope: no production act. No call went to BWS, Coolify, Healthchecks, the VPS, or
  GitHub beyond `git push` and `gh pr create`.
