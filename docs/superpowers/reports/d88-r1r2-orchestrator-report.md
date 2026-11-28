Outcome: done

# Report: D-88 A reissue conditions in the orchestrator (ADR-0055 amendment 39, R1 and R2)

Worker W-346, handoff `d88-r1r2-orchestrator`.

- **PR:** AlobarQuest/orchestrator#839, open and not merged. HQ merges it.
- **Branch:** `hq/d88-r1r2-orchestrator`. All code is at `c51d535fa8908bf6a0796df739cb7cc78d2b5fdf`;
  the PR's head adds only this report's commits after it.
- **Base:** `origin/main` `73b77ea761b9fc49abd40fb60c411b80b1bb4f95`, the base the handoff names.

At that base, amendment 39's text matched what the handoff cited: "What refuses a reissue today" at
line 7329, the readings at 7394, and "The plan" at 7453. No other ADR-0055 amendment PR was open.
I asked one question (`d88-r1r2-orchestrator-question.md`), and HQ answered (B), in this PR, failing
closed. That work is in the fifth commit.

## What I built

Six commits:

1. `844e434b`: R1's reissue conditions and the command's form.
2. `b0784e57`: R2's carrier and gate, plus the docs and the amendment correction.
3. `b6c9ddc1`: changes so the code passes the sibling-import and producer guards, plus two
   exclusion tests.
4. `3724916a`: fixes from the first code review. The gate checks the record's form, and the carrier
   matches the approval by revision.
5. `4fc556cc`: HQ's (B). Revision 1's approval and the last earlier approval are backed by
   verified chain events.
6. `c51d535f`: fixes from the second code review. `earlier_approvals` is scoped to rotation
   packages, and the observation records the events it relied on.

### The conditions as built (file:line at `c51d535f`)

The `standing-approval` command chooses the form by revision (`src/rotation_proposer/cli.py:975`,
`_conditions`). Revision 1 runs amendment 22's `evaluate`, unchanged. Revision 2 and later run
`reissue`, report the `rotated_before` fact, and read the package at the last approval's commit. The
command reads that package only when the commit is a full hash on intent-packages `main`.

| Condition | Where | What refuses |
| --- | --- | --- |
| `rotated_by_sds` | `standing_approval.py:324` | The row lacks `rotated_by_sds = true` (HQ change 1). |
| `reissue_type` | `:331` | The class isn't in `REISSUE_TYPES` (`:71`, `change-manager` and `orchestrator`), or the package doesn't name that provider. |
| `not_hand_rotated` | `:352` | The id is in `HAND_ROTATED_IDS` (`:74`), or a `coolify-env-hash` entry in the row or the package starts with one of `HAND_ROTATED_ENTRY_PREFIXES` (`:78`). |
| `machine_plan` | `:367` | The stated plan has a `HumanAct`, or isn't the rotation plan `plan_for` derives. |
| `reissue_lineage` | `:468` (helpers `:424`) | The rules are listed after this table. |
| `proposer_revised` | `:521` | No approval to compare with; the commit isn't on `main`; the package there doesn't hash to `approved_hash` or isn't that revision; or the tip differs outside `revision`, `status`, `occurrence` and `destinations`. |
| `no_new_copy` | `:568` | The tip lists a destination of any kind but `runtime-fetch` (`NO_COPY_KINDS`, `:88`) that the approved revision didn't list. Destinations are compared as whole strings, not by kind. |
| `destinations_equal` | `:586` | `same(entry.destinations, listed)` fails. |
| `standard_authority` | `:272`, with `first_issue=False` | The authority isn't `generate.authority` for the rotation plan. |
| `reach_unchanged` | `:595` | `reach` isn't `generate.reach` for the type and the row. |
| `rotated_before`, a fact | `:603` | Never refuses: it reports `last_rotated`, the day revision 1 was approved, and whether the first is on or after the second (HQ change 2). |

`reissue_lineage` refuses when any of the following hold:

- The revision isn't 2 or later.
- The lineage names another package, or doesn't list revisions 1 to N.
- An approval names a revision the lineage doesn't list, isn't on its listed hash, or is a repeat.
- An earlier approver is neither a named person nor the policy.
- Revision 1 is unapproved, or its approver isn't in `NAMED_APPROVERS` (`:85`, `devon`, HQ change 3).
- The tip is approved, or it or the lineage isn't `ready_for_review`.

`carry_reissue` (`:650`, `reissue` is `:627`) runs every condition except `proposer_revised` and
`no_new_copy`. It uses `policy_approved_reissue` (`:484`) in place of `reissue_lineage`. That
condition requires exactly one approval of the tip, by the policy, on the package's hash, equal to
the emitter's verified event in approver, hash, revision, commit and event id. It also checks the
same earlier-approval rules.

The carrier (`src/work_carrier/standing.py`) works in five steps:

1. `_check` (`:140`) runs `carry` at revision 1 and `carry_reissue` above it.
2. `_verified_approval` (`:189`) takes the lineage's one approval on the payload's revision,
   refuses an approval of a later revision, and compares the approval with the verified event.
3. After the carry form, `_earlier_backed` (`:216`) requires the payload's `earlier_approvals` to
   hold one entry for each role, `revision_1` and `last_earlier`. Each entry must be `verified` and
   equal the lineage's approval for that role, field for field. A refusal names the role.
4. The observation (`:283`) adds `form`, which is also part of the reference and the key.
5. At a reissue, the observation also adds the event id and commit for each earlier role.

The emitter (`src/orchestrator/package_sources.py`) works in three steps:

1. `_ChainRead` (`:449`) reads the chain once per payload. `_verify_current_approval` (`:414`)
   takes it as an optional reader, defaulting to the real one, resolved at call time.
2. `_earlier_approvals` (`:462`) and `_earlier_entry` (`:489`) match revision 1's approval and the
   last earlier approval by the tip's rules, using `_verified_approval_from_events` (`:389`).
3. The payload carries the result as `earlier_approvals` (`:759`). It does so only for a
   `non-software-operational` package's later revision that a policy approved, and refuses nothing
   itself.

The gate (`src/orchestrator/services/intake/standing_approval.py:78`) keeps the lane check at
revision 1. Above revision 1 it refuses three ways:

- `not_a_reissue_type`: the snapshot doesn't name exactly one of `REISSUE_PROVIDERS` (`_names_one`, `:124`).
- `no_credential_id`: the snapshot has no string `credential_id` (`:133`).
- `hand_rotated_identity`: the id is in `HAND_ROTATED_IDS` (`:63`).

`_binds` also requires the record's `form` to fit the revision (`_form_fits`, `:157`): `reissue`
above revision 1, and `issue` or no form at revision 1.

On real data, I ran `reissue` offline against the seven covered packages. I used intent-packages
and change-manager `origin/main` from local clones, through git only. All seven refuse nothing:
operator, observer, drift-reporter, full, read, propose and observe.

## Tests and checks

### `make check`

- At `c51d535f`, the final head: **15,347 passed, 1 skipped**, lint and type-check green, exit 0.
- Database: `orchestrator_test_hq_w_346`. Environment: `SECURITY_STANDARDS_DIR` set to the
  fixture, and `PGTZ=UTC`.
- At `3724916a`, before HQ's (B): 15,301 passed, 1 skipped.

### Tests per exclusion

I proved each exclusion's test discriminates with a mutation script (`/tmp/w346/mutate.py`). For
each check, the script:

1. Replaces the check in the source with a no-op, asserting the site matches exactly once.
2. Clears `__pycache__`.
3. Runs the named test files with `-x`.
4. Restores the file by rewriting its original text.

**64 mutants, 64 killed, 0 survivors.** The tree was clean after each run (`git status` empty).

| Group | Mutants | Killing tests |
| --- | --- | --- |
| R1 conditions | 35: `rotated_by_sds`; both `reissue_type` checks; hand-rotated id and prefix; person's act; stated is derived; revision ≥ 2; lineage package id; revisions 1 to N; unlisted, wrongly hashed and repeated approvals; earlier approver named or policy; revision 1 approved; revision 1 named approver; tip unapproved; tip and lineage in review; the four carry-lineage checks; the five `proposer_revised` checks; the four `no_new_copy` checks; `destinations_equal`; `standard_authority`; `reach_unchanged` | `tests/rotation_proposer/test_reissue_approval.py` |
| Command | 3: approval commit on `main`, full-hash commit, revision chooses the form | `test_reissue_approval.py` (rig tests through the real mirror) |
| Carrier | 13: verified approval, one approval of the revision, later approval, form choice, the earlier-approval check and its entries, roles, `verified` flag, field equality, the "before the tip" bound, the observation's earlier events | `tests/work_carrier/test_standing.py` |
| Gate | 5: one reissue provider, credential id present, hand-rotated id, reissue form, issue form | `tests/services/test_standing_approval_gate.py` |
| Emitter | 11: one approval per role, approver recognised, hash, commit and event id present, event must match, one chain read, "before the tip" bound, policy only, rotation profile only | `tests/cli/test_earlier_approvals.py` |

The handoff asked for specific tests. They exist as follows:

- **R1's list.** Every exclusion has a test that asserts its named condition refuses, usually
  alone. Where two conditions refuse together, the test asserts the named condition and its
  reason; for example, a lane token also fails `machine_plan`, because its mint is a person's act.
  The test list's two `last_rotated` refusals became passes that report the fact (HQ change 2).
- **R2's list.** These are in the gate and carrier tests.
- **D-84 condition 2, on the tip.**
  - In `carry_reissue`: `test_a_tip_approval_the_verified_chain_event_doesn_t_back_refuses`, one
    case per field.
  - In the carrier: `test_a_reissue_approval_the_emitter_didn_t_verify_refuses`.
- **HQ's (B), at the emitter.** `test_a_forged_approval_with_no_event_is_unbacked` and
  `test_an_event_naming_another_approval_doesn_t_back_it` cover hash, revision, approver and
  commit, for both roles. Also: `test_an_event_with_another_id_or_package_doesn_t_back_it` and
  `test_a_forged_revision_1_reaches_the_payload_unbacked`.
- **HQ's (B), at the carrier.** `test_a_forged_earlier_approval_the_chain_doesn_t_back_refuses`
  covers both roles, and the refusal names which approval. `test_an_earlier_approval_other_than_the_one_verified_refuses`
  covers each field and role. `test_the_emitter_s_entries_are_what_the_carrier_reads` wires the
  emitter's real output into the carrier.

### Other checks

- **`code-standards risk`:** `normal` at the final head, so `vendor-review` wasn't required.
- **`/code-review`, two passes at high effort.**
  - First pass, on the original branch: one important finding and five nits. I fixed four nits.
    I kept exit 2 on a failed infraops read (concern 4). The important finding was the
    reading-8 gap, now recorded as residual (b).
  - Second pass, on HQ's (B) commit: seven findings, all fixed. They covered the scope to rotation
    packages, a pinning test for the shared roles and fields, carry form first, earlier events in
    the observation, the `None` default for the chain reader, layer order, and the nested
    CLAUDE.md clause.

## Files changed

- **Source:**
  - `src/rotation_proposer/standing_approval.py`
  - `src/rotation_proposer/cli.py`
  - `src/work_carrier/standing.py`
  - `src/orchestrator/services/intake/standing_approval.py`
  - `src/orchestrator/services/intake/decomposition.py` (docstring only)
  - `src/orchestrator/package_sources.py`
- **Tests:**
  - `tests/rotation_proposer/test_reissue_approval.py` (new)
  - `tests/rotation_proposer/test_standing_approval.py`
  - `tests/work_carrier/test_standing.py`
  - `tests/services/test_standing_approval_gate.py`
  - `tests/cli/test_earlier_approvals.py` (new, in `layers.toml`'s `intent` layer)
  - `tests/cli/test_package_intake_cli.py` (one test double takes the chain reader)
  - `tests/architecture/test_rotation_edges.py` (the proposer borrows `canonical.package_hash` and
    `git_mirror.COMMIT_PATTERN`)
- **Docs:**
  - `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
  - `docs/operations/rotation-proposer.md`
  - `CLAUDE.md`
  - `src/rotation_proposer/CLAUDE.md`
  - `src/orchestrator/services/intake/CLAUDE.md`
  - `layers.toml`

## The amendment correction

Amendment 39 ends with "Correction to R1 and exit check 2 (2026-11-28, HQ's call, Opus review of
#838)". Its first paragraph records HQ's three changes:

- `rotated_by_sds` is a condition.
- `rotated_before` is a reported fact. A failed infraops read still exits 2. R1's two `last_rotated`
  refusals become passes.
- Exit check 2's last sentence is rewritten.
- `NAMED_APPROVERS` is `devon`.

Its second paragraph records what building settled:

- Earlier approvals must be on their listed hash, at most one per revision, by a named person or
  the policy.
- The carry condition is named `policy_approved_reissue`, and it takes the verified event.
- `machine_plan` drops a redundant issue-plan check.
- `form` is in the observation's reference and key, so an old row is never replayed into
  `observation_conflict`.
- `REISSUE_TYPES` is sorted, because a tuple headed by `"orchestrator"` reads as an orchestrator
  CLI call to the producer guard.
- The carrier matches the approval by revision.
- The gate checks `form`.

Two more paragraphs record HQ's (B) and its two residuals in plain words:

- **(a)** The rotation root can still append a chain event, through the broker's token and the
  append password. This was accepted earlier, in W-328's #826 concern 1, D-84 (9) A and the D-83
  standing answer item 1.
- **(b)** Nothing ties the command's passing run to the tip's approval (reading 8). That's D-34's
  position for a lane revision 1, which #207's Opus review also raised.

## Self-review

- Every exclusion in R1's and R2's lists, and in HQ's additions, has a test that a mutant of that
  exclusion fails. A check that can't be made is exit 2 at the command, a refusal at the carrier,
  or a `PolicyRefusal` at the gate, never a pass.
- The emitter refuses nothing new. A payload other than a rotation package's policy-approved
  later revision is byte-identical to before. A policy-approved bump's payload is pinned by
  `test_a_policy_approved_later_revision_of_another_profile_states_none`. The intake route ignores
  the new key, because `CommandBase` doesn't forbid extra fields.
- The earlier approvals reuse the tip's chain read, which
  `test_a_policy_approved_reissue_s_payload_states_its_earlier_approvals` asserts as `reads == [1]`.
- No response model changed, and no credential, grant, type, adapter or route was added.

## Concerns

1. **The October approvals must be chain events.** If revision 1's `devon` approval for any of the
   seven packages isn't a `package.approved` event in the database chain, the carrier refuses that
   reissue, naming revision 1. HQ expects them to be there because of FE-5's import, but I couldn't
   read the chain. The first live carry (exit check 4) is where this shows.
2. **The two residuals stay.** Both are in the amendment: (a) the root can append a chain event,
   and (b) the carrier can't see the command's run.
3. **One new test file.** The reissue tests are in
   `tests/rotation_proposer/test_reissue_approval.py`, not in `test_standing_approval.py` as R1
   names, to keep that file's issue-form fixtures separate.
4. **A choice HQ can reverse.** A failed infraops read still exits 2 at a reissue, because
   Code Brain rule 4 says a read that fails is never a pass. The first code review suggested
   reporting it as a null fact instead. Reversing it is one `try` in `_conditions`.
5. **Reads outside the worktree.** I ran `git fetch` (refs only) in the local intent-packages,
   change-manager and orchestrator clones, to read lineages and registry rows for the offline run.
   I made one `gh pr list` call and one `gh pr create` call. The main tree's `git status` is clean.
6. **Teardown.** The worktree and the test database, `orchestrator_test_hq_w_346`, are left for
   HQ to remove after the merge.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout).** The one line I added is the command's
  `standing_approval.fact` line. It goes through the existing `_log`, as one JSON object on
  stdout. Nothing writes to a file.
- **Code Brain rule 4 (never silently swallow an exception).**
  - The command's reads still raise to exit 2.
  - A missing or malformed approval commit is a named refusal.
  - The emitter catches only `PackageSourceError` for an earlier approval, and records it as
    `verified: false` with its reason. The carrier then refuses on that entry.
  - The carrier's `check` turns every exception into a refusal, and the gate returns
    `PolicyRefusal`.
  - No path turns an unread check into a pass. The mutation pass proves each refusal fires.
- **Code Brain rule 5 (never log secrets, credentials, auth headers, tokens or full bodies).**
  - Reasons name conditions, fields, registry ids and destination kinds only.
  - The fact line carries dates.
  - The emitter's entries carry approval metadata (hash, commit, event id): no value, no password.
  - The existing no-value test, `test_no_line_carries_a_value`, still passes.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files, history or inline).** Nothing in
  any file, fixture or commit is token-shaped. Fixture UUIDs are the registry's own UUIDs or
  placeholders, and the session-end scanner gate runs at exit.
- **Infra Brain 1032 (`cred.exposure-rotate`).** A scheduled reissue isn't an exposure. Nothing
  here opens or closes one, and no code reads or writes exposure state.
- **Infra Brain 212 (HTTPS for public FQDNs).** No endpoint was added, so it doesn't apply.
