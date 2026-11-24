Outcome: done

# Report: ADR-0055 Amendment 32, the lane secrets broker's Phase C (Proposed, docs only)

Worker W-306, branch `hq/broker-phase-c-adr`, base `5ae929a9`. Pull request:
[#810](https://github.com/AlobarQuest/orchestrator/pull/810), open against `main`.

## What I built

`## Amendment 32 — The lane secrets broker's Phase C: the 12 earlier lanes move and their tokens
retire (2026-11-24)` at the end of ADR-0055, status Proposed, plus its line in the ADR's
"Amended" list. It covers each of the handoff's four parts:

1. **Scope** (decision item 1): the 12 token lanes; inert landing's G-79 token and its unmapped
   `INERT_LANDING_BWS_ACCESS_TOKEN`; the unlock tier; the six rows with no adapter (three Coolify,
   two GitHub App, one Anthropic); HQ's unregistered secrets; fetch fairness; and what is out of
   scope.
2. **Per-lane route** (item 4): eight ordered steps from the broker directory to the retirement,
   plus two code changes that must land before batch 1. Item 5 covers the retirement: the
   restated revoke precondition, the one sitting for 13 tokens, the registry removal, the
   leak-revokes-first rule, and HQ's package hold.
3. **Per-lane table** (item 2): all 26 distinct secrets, each with its name, project and whether
   the broker reads it, and a per-lane count. The broker already reads 8: 6 by project and 2 by
   existing single-secret grants. The other 18 need new grants: 15 in `Rotation / Keepers`, 1 in
   `Factory Events` and 2 outside the broker's projects. The lane lists hold 58 entries.
4. **Batches** (section "Proposed batches and order"): four batches of three, with 4, 4, 4 and 6
   new grants (18 in all). Batch 4 holds the three grants that wait on Open item 9. The revoke
   sitting comes once, after batch 4.

The stop points are Open items 6 to 14, and none is settled. I wrote them, with options and
recommendations, to `docs/superpowers/reports/broker-phase-c-adr-question.md` (HQ renamed it to
`broker-phase-c-adr-question-answered.md`). I added three items
the handoff didn't list, because the code read made them necessary:

- **Registration (Open item 13).** `lanes_in_registry` registers a lane only by its token row's
  `<LANE>_BWS_ACCESS_TOKEN` consumer. So the four Phase B broker lanes are already outside
  `prepare`'s lag check and `lanes_to_follow`, and removing the 13 token rows would take the
  remaining lanes out too.
- **The lane machine accounts at the revoke (Open item 14).**
- **The factory-events passwords (Open item 12).** The design places them in Phase C, but the
  handoff doesn't.

## Tests and checks run

- `make check` in the worktree on `orchestrator_test_hq_w_306` (migrated to head) passed on both
  commits:
  14,776 passed, 1 skipped, exit 0.
- Security scan (`security_scan.cli . --category security`): 0 BLOCK. The 2 WARNs were there
  before this change (secret-file gitignore coverage), and this change touches neither.
- I derived the counts with a throwaway script, which I didn't commit. It took UUIDs from
  `scripts/run-<lane>.sh` and `SDS_DEADMAN_PING_URL_UUID` from each compose, projects from
  `.bws-secrets.toml`, and reach from the broker row's `reach_secrets`. I checked the totals by
  hand: 8 reached plus 18 new is 26, and 28 reached plus 30 new is 58.
- `layers.toml` covers both report files (`docs/superpowers/**`) and ADR-0055.
- **The registry cross-check:** each lane's `<lane>-bws-token` `reach_secrets` equals its launcher
  UUIDs plus its ping URL, with no extra and no missing UUID in any of the 12.
- `code-standards risk --repo .`: verdict `normal`, so no vendor review was needed.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: Amendment 32 and
  its "Amended" line.
- `docs/superpowers/reports/broker-phase-c-adr-question-answered.md`: the open items for Devon
  (HQ's rename of `broker-phase-c-adr-question.md`).
- `docs/superpowers/reports/broker-phase-c-adr-report.md`: this report.

## Self-review

- The amendment grants nothing. Each of the 18 grants is named as a gate step for Devon, and its
  "What it grants" section says so.
- I checked each line citation against `5ae929a9` and corrected four after the first draft.
- The projects come from `.bws-secrets.toml`, not from a BWS read, and the amendment says so. If
  the manifest is stale for a secret, that secret's grant row is wrong. Devon's grant sitting would
  show it.
- I didn't run `/code-review`: the change is documentation only. An advisor review of the first
  draft found eight things, and I fixed each one in a second commit:
  - The Phase B context was wrong. The four lanes' pull requests did remove a token mapping, but
    before each lane's first `--add`.
  - The conversion's `--tag` is the follow-up run `finish` prints for a registered lane, so steps
    1, 2 and 5 must close before it.
  - The variable deletion moved from each lane's route to after the revoke sitting. Before the
    sitting, a rollback past the conversion still finds the token.
  - "As the Phase B lanes' rows did" was wrong for #761 and #762. It now says the broker row
    records each grant after Devon's gate.
  - The launcher step now names the child-process `env -u` from #761 and #762.
  - Amendment 25 item 3's account table is now in "Passages it governs" for the append and read
    passwords.
  - A wrong cross-reference (item 10 for item 15) and the "single-secret" wording in item 2 are
    fixed.
  - Question 2's option B named two gaps. Round 1 showed the first was false (see "Round 1").

## Concerns

- **A `--tag` conversion isn't checked for the broker shape.** `add_refusals` runs only for
  `--add`. So a `--tag` whose compose drops the token but lacks the mount passes plan time, and the
  lane then fails its reads. Item 4 makes the fix a prerequisite. I didn't design it.
- **The registration gap affects four live broker lanes today** (estate landing, the bump driver,
  follow-up minting and the change window). In production mode the lag check doesn't cover them,
  and `finish` doesn't move them to the served tag. Development mode skips the lag check, but
  `lanes_to_follow` still leaves them out.
- **Fetch fairness** comes from sds-hq's own list (`state/goal.md`, the broker-first plan
  amendment), which no record defines. Item 15 decides nothing.
- **HQ's six unregistered secrets** appear in no record I could read, so they aren't listed.

## Standards followed

- **Code Brain 3 (logging):** not applicable. The amendment prescribes no logging. It cites the
  broker's existing per-request log only as the place to measure a wait.
- **Code Brain 4 (error handling):** not applicable (docs only).
- **Code Brain 5 (security):** no secret, token, header or request body is logged or written. Step 7
  of the route reads `BWS_ACCESS_TOKEN`'s presence by name, never its value.
- **Infra Brain 591, 592, 593 (`bws.no-token-in-tracked-files`, `bws.no-token-in-git-history`,
  `bws.bootstrap-token-not-inline`):** none of the three files holds a token or a literal example.
  The UUIDs are secret ids, not values, and the scan found 0 BLOCK.
- **Infra Brain 1032 (`cred.exposure-rotate`):** item 5.4 keeps "a leak still revokes first,"
  outside the sitting and the batch order, and leaves the rotation-decomposer token D-48 found to
  D-48 B's pass.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable, because no endpoint is added. The
  amendment's "What it grants" section says so.

## Round 1

HQ's fix round 1 came from the Opus whole-branch review and HQ's own checks at `5ae929a9`. I checked
each finding at the source before fixing it.

### Blocking items

1. **Each open item names what it blocks.** Every Open item now has a **Blocks:** line.
   - Items 8 and 13 block batch 1, and the batches section says so.
   - Items 6, 7 and 11 block Phase C's close, through a new "Phase C is done when" section that
     names each one. That section also names the lanes, the revoke, the design's six adapters, and
     the factory-events passwords only if Devon puts them in.
   - Items 10 and 14 block the revoke sitting.
   - Item 9 blocks batch 4's bump-proposer and work-carrier.
   - Item 12 is out of Phase C pending Devon, and joins "done when" if he puts it in.
2. **The D-48 token.** Item 1's out-of-scope bullet and item 5.4 now say the rotation-decomposer
   token was reissued under D-76 B at G-92 on 2026-11-23 (units `7583f478`, `5df0107f` and
   `de1da609`) and the 2026-11-13 token revoked. They also cite Devon's 2026-11-24 order: the D-48 B
   pass doesn't rotate a lane token Phase C retires.
3. **`helper_target`.** I confirmed every lane row names one (`.cred-consumers.toml` 720 to 1292,
   #793). My first draft took Amendment 31 item 5's "What isn't done" as current, but #793 had
   since made it. Question 2 drops that gap: it now cites G-92's `loaded` check and says the
   broker needs its own target.
4. **The factory-events holders.** Item 3 now says the broker would be one more append holder, not
   a replacement, and cites D-43 B, D-45, D-47 and Amendment 25 item 3 (lines 3655-3657, the
   refusal to identities that serve processes beyond the holders).
   - Open item 9 is now whether the broker may hold the append password at all, plus whether Devon
     accepts the broker as the carrier's read holder.
   - The read password is described as already served to the bump driver.
   - The "serves that lane alone" claim is gone, and the "Passages it governs" row is rewritten.
   - Question 5 is rewritten the same way. Its recommendation is A by a narrow margin, with B's
     cost stated: one lane token stays.
5. **The adapter count.** I counted 11 rows with no `rotated_by_sds`, not 10. The three M2M bearers
   have `orchestrator_bearer.py`, which leaves eight with no adapter. Item 1 now names all eight,
   marks the design's six (spec line 199) as Phase C's, and names the two alert-hub sender rows as
   outside the design's list.
6. **The narrowest credential.** The Coolify read token and the App Brain key are single-secret
   gate steps under the 2026-11-18 standing answer. The batches section says 17 grants can go in one
   sitting, and the append password waits for Open item 9. "What it grants" and Consequences say
   17 gate steps.

### Follow-ups

All six are fixed:

7. The context and item 15 name the shared `MAX_FETCHES = 8` cap (lines 62 and 280).
8. Item 4 step 3 removes the lane row's `helper_target` (`test_lane_token_helper_targets.py`,
   line 108 onward). The lane's entry in the host helper's `/etc/sds-pg-rotate/targets.toml` is
   removed as its own root act after the merge.
9. This report says eight steps.
10. Every sds-hq `goal.md` citation is by section ("Queued", Devon's 2026-11-24 order; the
    broker-first plan amendment), not by line.
11. The deploy.md citation is lines 325-327, the paragraph before "Before a lane token's revoke".
12. Code Brain 4 reads "not applicable (docs only)".

### Checks after round 1

The figures are in "Tests and checks run, round 1", which follows.

### Tests and checks run, round 1

- `make check` on `orchestrator_test_hq_w_306` passed, exit 0: 14,777 tests collected, with
  14,776 passed and 1 skipped.
- The security scan found 0 BLOCK and 2 WARN, the same two WARNs as before this change.
- Round 1 grants nothing, and the changes are docs only: ADR-0055, the question file and this
  report.
