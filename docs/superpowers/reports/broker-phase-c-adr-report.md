Outcome: done

# Report: ADR-0055 Amendment 32, the lane secrets broker's Phase C (Proposed, docs only)

Worker W-306, branch `hq/broker-phase-c-adr`, base `5ae929a9`.

## What I built

`## Amendment 32 — The lane secrets broker's Phase C: the 12 earlier lanes move and their tokens
retire (2026-11-24)` at the end of ADR-0055, status Proposed, plus its line in the ADR's
"Amended" list. It covers each of the handoff's four parts:

1. **Scope** (decision item 1): the 12 token lanes; inert landing's G-79 token and its unmapped
   `INERT_LANDING_BWS_ACCESS_TOKEN`; the unlock tier; the six rows with no adapter (three Coolify,
   two GitHub App, one Anthropic); HQ's unregistered secrets; fetch fairness; and what is out of
   scope.
2. **Per-lane route** (item 4): nine ordered steps from the broker directory to the retirement,
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
recommendations, to `docs/superpowers/reports/broker-phase-c-adr-question.md`. I added three items
the handoff didn't list, because the code read made them necessary:

- **Registration (Open item 13).** `lanes_in_registry` registers a lane only by its token row's
  `<LANE>_BWS_ACCESS_TOKEN` consumer. So the four Phase B broker lanes are already outside
  `prepare`'s lag check and `lanes_to_follow`, and removing the 13 token rows would take the
  remaining lanes out too.
- **The lane machine accounts at the revoke (Open item 14).**
- **The factory-events passwords (Open item 12).** The design places them in Phase C, but the
  handoff doesn't.

## Tests and checks run

- `make check` in the worktree on `orchestrator_test_hq_w_306` (migrated to head) passed:
  14,776 passed, 1 skipped, exit 0.
- Security scan (`security_scan.cli . --category security`): 0 BLOCK. The 2 WARNs were there
  before this change (secret-file gitignore coverage), and this change touches neither.
- I derived the counts with a throwaway script, which I didn't commit. It took UUIDs from
  `scripts/run-<lane>.sh` and `SDS_DEADMAN_PING_URL_UUID` from each compose, projects from
  `.bws-secrets.toml`, and reach from the broker row's `reach_secrets`. I checked the totals by
  hand: 8 reached plus 18 new is 26, and 28 reached plus 30 new is 58.
- `layers.toml` covers both report files (`docs/superpowers/**`) and ADR-0055.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: Amendment 32 and
  its "Amended" line.
- `docs/superpowers/reports/broker-phase-c-adr-question.md`: the open items for Devon.
- `docs/superpowers/reports/broker-phase-c-adr-report.md`: this report.

## Self-review

- The amendment grants nothing. Each of the 18 grants is named as a gate step for Devon, and its
  "What it grants" section says so.
- I checked each line citation against `5ae929a9` and corrected four after the first draft.
- The projects come from `.bws-secrets.toml`, not from a BWS read, and the amendment says so. If
  the manifest is stale for a secret, that secret's grant row is wrong. Devon's grant sitting would
  show it.
- I didn't run `/code-review`: the change is documentation only.

## Concerns

- **A `--tag` conversion isn't checked for the broker shape.** `add_refusals` runs only for
  `--add`. So a `--tag` whose compose drops the token but lacks the mount passes plan time, and the
  lane then fails its reads. Item 4 makes the fix a prerequisite. I didn't design it.
- **The registration gap affects four live broker lanes today** (estate landing, the bump driver,
  follow-up minting and the change window). In production mode the lag check doesn't cover them,
  and `finish` doesn't move them to the served tag. Development mode skips the lag check, but
  `lanes_to_follow` still leaves them out.
- **Fetch fairness** comes from sds-hq's own list (`state/goal.md`, line 58), which no record
  defines. Item 15 decides nothing.
- **HQ's six unregistered secrets** appear in no record I could read, so they aren't listed.

## Standards followed

- **Code Brain 3 (logging):** not applicable. The amendment prescribes no logging. It cites the
  broker's existing per-request log only as the place to measure a wait.
- **Code Brain 4 (error handling):** the amendment adds no code. The conversion-check prerequisite
  turns a silent mis-conversion into a refusal at plan time, so the error isn't swallowed.
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
