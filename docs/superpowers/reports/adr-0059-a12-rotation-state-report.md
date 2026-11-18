Outcome: done

# Report: adr-0059-a12-rotation-state (worker W-205)

- **PR:** AlobarQuest/orchestrator#735, ready for review, not merged.
- **Base:** `a410af18f17ca50939e1b2589dd682542653e028`.
- **Content commits:** `9383879ea15590038b790e0ca59eae8d1813000e` (the amendment) and
  `898f52a3f82486f9c27661aa992f89354a27e93a` (corrections inside it, after self-review).
- **Head:** the commit that adds this report, on `hq/adr-0059-a12-rotation-state`.
- **Amendment's line range:** lines 3427-3637 of
  `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`.

## What I built

Appended `## Amendment 12 (2026-11-17): the rotation state file moves to the rotation proposer's
lane volume` after Amendment 11. It follows Amendment 11's form: Status, Decided by (D-61 quoted in
full with its recorded answer "A", 2026-11-17T22:22:50Z, and HQ's note that the answer is about this
file only), Relates to, the line-number note, Context, Decision, Verify items, and the table. I
stopped once to ask HQ which signal channel to use (question file in this directory). HQ answered
(A), alert-hub through the `sds-bridge` sender, and the amendment pins HQ's four points.

Decision items:

1. **Home.** The lane volume, through `SECURITY_DRIFT_STATE_DIR`. The list comes through
   `INFRADRIFT_CONFIG_DIR` (`paths.ts` 25-26, 46-47 at `c3359bf`). The Mac copy is copied once
   before the first pass. A missed copy fails closed: `cred-findings` exits on a missing file.
2. **Writers.** Three writers, not two:
   - the proposer's `record-rotation`;
   - a person's `resolve-exposure`;
   - a person's `record-rotation` for a credential not handed over.

   A person runs both commands in the lane container, as the container's user. Neither needs a
   credential.
3. **The Mac scan.** It stops reading the state file and raising the three state-dependent checks,
   through an infraops change. It keeps `cred.invalid-rotate-requested`, `cred.unknown-class` and
   `cred.registry-error`. The order constraint is stated. The Mac copy stays in place until the
   infraops change merges, because a missing file reads as empty.
4. **Signals.**
   - The findings are alerts, not notices, keyed `rotation-finding:<credential>:<status>`.
   - An exposure is `critical`. Age and request are `warning`: the `KeyedAlerts` default
     (`alert_hub_client/__init__.py` line 236), which `alerts.py` 119-125 uses.
   - The amendment says plainly that the change-manager manual checklist is lost for credentials
     not handed over.
5. **Authority.** Grants nothing at merge.

Verify items, each naming its step:

- How the checklist reaches a person (the proposer's findings code, 3f-5-state work 2).
- The alert levels and keys (the proposer's findings code).
- A byte-identical copy at mode 0600, owned by the container's user (cut-over).
- An open exposure survives the move (cut-over).
- `resolve-exposure` in the container clears it (cut-over).
- The stale-window item (cut-over and the infraops change): the state file only clears findings, so
  the window over-reports but never hides one.
- The Mac read is off only after the VPS pass covers the findings (the infraops change).

## Table rows

| Passage (lines at `a410af18`) | Disposition |
|---|---|
| Decision 5, the `cred-findings` inputs and state file (796-803) | Answered for the state file; the list stands |
| Decision 1 "Lane state", `rotation-proposer` row (345) | Stands |
| `infra-drift` row, credential scan reads rotation state (823) | Superseded for the state once the infraops change merges |
| Increment 9, "reads the same list and rotation state" (1084-1085) | Stands as a description of today |
| Done-when, no moved lane's input on the Mac (1299-1301) | Stands, met for the state file |
| Amendment 9 decision item 1, scan's read carried (3008-3009) | Answered |
| Amendment 9 "Verify in increment 4" (3029-3044) | Closed as to the decision; checks carry to Amendment 12's verify items |
| Amendment 9 row on decision 5 (3059) | Answered |
| Amendment 9 row on line 1081 (3065) | Stands, with the shared-file clause answered |

## Tests and checks

- `make check` on `898f52a3` with `TEST_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` pointed at
  `orchestrator_test_hq_w_205` (migrated with `alembic upgrade head`),
  `SECURITY_STANDARDS_DIR=tests/fixtures/security-standards` and `PGTZ=UTC`: exit 0. Lint
  "All checks passed!", pyright 0 errors, **15335 items collected, 15334 passed, 1 skipped**, 11 warnings, in 306.62s. An earlier run on
  `9383879e` gave the same counts.
- Security scan from the repo root (`security_scan.cli . --category security`): 0 BLOCK, 2 WARN,
  1 INFO. Neither WARN comes from this diff:
  - `bws.secret-files-gitignored`: the glob `*-migration/` isn't covered.
  - `bws.manifest-matches-usage`: undeclared UUID `aaff45ca-…` in code.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`: append only (212
  insertions, 0 deletions against the base).
- `docs/superpowers/reports/adr-0059-a12-rotation-state-report.md`: this report.

The question file stays uncommitted.

## Self-review

- `git diff` against the base shows no line removed, so the change is append only.
- I re-read every cited line in the worktree or with `git show c3359bf:`.
- I corrected two citations:
  - HQ's `.bws-secrets.toml` 266-284 became 269-288 (the `sds-bridge` comment and block).
  - My first draft's line 238 became 236.
- An advisor pass caught issues that `898f52a3` fixes:
  - Context had said the work watcher runs `record-rotation`; the proposer's pass runs it after the
    work watcher retires the record.
  - Decision item 2 had named who runs a person's write beyond D-61's "a person ... from an
    interactive session"; it now uses D-61's words.
  - The notice claim now cites alert-hub `src/ingest/validate.ts` line 98 at `2218702`.
  - The lane read of the bridge token now cites decision 2 (lines 486-490).
  - "Don't page Devon per credential" is now a requirement with a verify item, not a claim.
  - The decision 5 row now covers 796-803.
  - The window item now says a write made on the Mac never reaches the volume.
- D-61's "3029-3043" is quoted verbatim, and the line-number note says the item ends at 3044.
- I reviewed the diff myself rather than running `/code-review`, because the change is a docs-only
  append that HQ sends to an Opus whole-branch review before merging.

## Concerns

- **The bridge token's `lanes` list doesn't yet name `rotation-proposer`.** See
  `.bws-secrets.toml` 285-288. The Mac launcher reads the token through `sds-operator`. The
  amendment says the lane identity joins that list at the proposer's cut-over, as the bump
  proposer's did, and treats that as the lane's move under decision 2, not a grant this amendment
  makes. If HQ reads it as a grant, the authority item needs revisiting.
- **`warning` and paging.** The amendment states "must not page Devon per credential" as a
  requirement and carries its proof as a verify item. alert-hub's routing rules, read live on
  2026-11-17, send `critical` to two channels and `warning` only to the catch-all channel. Each
  credential still gets a `warning` message there, as the existing `rotation-finding` alerts do.
- **"The ADR's outcome 1".** ADR-0059 has no numbered outcomes. I read it as ADR-0021 amendment 1,
  "every signal has a machine home".

## Follow-ups outside the ADR

These are listed in the PR body:

- `docs/operations/rotation-proposer.md` goes stale in three places:
  - "What it reads", lines 12-17;
  - `legacy`, lines 179-181;
  - "Alerts", lines 153-170.
- Infra Brain rule 1032's text says the 3 AM scan raises the exposure finding.
- infraops-mcp-server: the `credScan` / `scanFindings` change, and its "clears on the next 3am run"
  comments.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply; docs only.
- **Code Brain rule 4 (never silently swallow an exception):** doesn't apply; docs only.
- **Code Brain rule 5 (never log secrets, credentials, tokens):** the amendment names no secret
  value. It refers to the one secret it mentions by truncated UUID (`b415d03b-…`), as the ADR does.
- **Infra Brain 591, 592, 593:** no BWS token in any tracked file or in history, and no inline
  `BWS_ACCESS_TOKEN` value. The security scan reports 0 BLOCK.
- **Infra Brain 1032 (`cred.exposure-rotate`):**
  - Exposure findings stay reported. They're raised by the proposer's pass as `critical` alerts
     that stay firing until the exposure is revoked or `resolve-exposure` records it.
  - The order constraint and the stale-window item keep them from going dark during the move.
  - The window hides none of the three moved findings. It can hide `cred.unknown-class`, which
    isn't an exposure finding, for a credential whose exposure was resolved in the container; the
    cut-over's window item checks those credentials (fix round 2).
  - I saw no secret value anywhere.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; docs only.

## Fix round 1 (worker W-207)

Fixes HQ's Opus whole-branch review: two blocking findings and three factual corrections. ADR
fix commit: `d5778590` (parent `25cd0ae9`, the PR head before this round; `git fetch` showed
nothing later before the push). All lines are in
`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` at `d5778590`.

### Changes

1. **The scan's other credential findings (blocking), decision item 3, lines 3530-3541.** The
   amendment no longer says those findings don't read the state file. It now says that today two
   of them depend on it:
   - `credScan` loads the state inside the same `try` as the list (`security-drift-cli.ts`
     135-156, comment 272-274), so a state file that fails the check raises `cred.registry-error`.
   - `cred.unknown-class` is reached only after the open-exposure `continue` (`cred-rotation.ts`
     237-262).

   After the infraops change, `cred.registry-error` comes from reading the list and its registry
   files only. A volume copy that fails the check makes the proposer's pass exit 2, so the
   integrity signal keeps a home.

   The infraops verify item (lines 3637-3646) adds that `cred.unknown-class` must not change for
   a credential with an open exposure, or the pull request states what changes and why.
2. **The third writer's check (blocking), cut-over verify item, lines 3615-3627.** The item now
   covers `resolve-exposure` and `record-rotation` in the container. It cites `doRecordRotation`'s
   read of the list and every registry file (`security-drift-cli.ts` line 119). It also says the
   CLI is in the per-pass infraops clone that the launcher deletes at the start and end of each
   pass (`run-rotation-proposer.sh` 230-251 and 284-287 at `9052f7f8`), and it requires the
   cut-over to state where the CLI and the listed registry files are between passes.
3. **The lanes list, decision item 5, lines 3585-3587.** `rotation-proposer` joined the token's
   `lanes` list in #734 (`9052f7f8`, `.bws-secrets.toml` lines 287-290), as `bump-proposer` did in
   #733 (`a410af18`).
4. **D-61's words, decision item 2 (lines 3520-3523) and decision item 5 (lines 3587-3589).** Only
   `resolve-exposure` carries D-61's label. The person's `record-rotation` "runs the same way and
   adds no credential, scope or network path".
5. **Citations, each checked at its cited commit:**
   - `rotation-proposer.md` 122-125 (line 3480);
   - alert-hub `validate.ts` line 99 at `2218702` (line 3561);
   - `scanFindings` at `cred-rotation.ts` lines 310-315 (line 3495).

No other text changed.

### Checks

- `make check` on the committed tree `d5778590`, with database `orchestrator_test_hq_w_207`:
  exit 0, 15335 collected, 15334 passed, 1 skipped.
- Security scan from the repo root: 0 BLOCK, 2 WARN, 1 INFO. These are the standing findings,
  unchanged by a docs-only diff.
- CI on `d5778590`: all three checks pass (run `44205367479`). The Quality job log reads
  `4 workers [15388 items]` and `15365 passed, 23 skipped`. CI's collected count differs from the
  local count because the two environments differ; neither run failed a test.

### Notes on method

- The brief's `git reset --hard origin/hq/adr-0059-a12-rotation-state` was denied by the
  permission layer. I reached the same state with
  `git switch -C hq/adr-0059-a12-rotation-state-fix-1 origin/hq/adr-0059-a12-rotation-state`; the
  branch held no work.
- The brief cites `src/security-drift/security-drift-cli.ts`. At infraops `c3359bf` that file is
  `src/cli/security-drift-cli.ts`, as the ADR already cites it. The content at the brief's line
  numbers matched.

### Concerns

These are for HQ. I didn't act on them because the brief says to change no other text.

- **Where the listed registry files are in the container is undecided.** #734's report
  (`docs/superpowers/reports/lanes-inc-4-rotation-proposer-report.md` at `9052f7f8`, "The list
  file: not built, and why") says the Mac list names four paths. Of those, only
  infraops-mcp-server's file exists in the container before `cred-findings` runs: the mirrors are
  bare and fetched later. That is why the verify item asks the cut-over to state where the files
  are, rather than claiming they're in the per-pass clones as the brief said.
- **A stale copy can now hide one finding.** The Context says "a stale state file can therefore
  only over-report", and the window item says the window "never hides one". Both hold for the
  three moved findings, but not for `cred.unknown-class`: in the window, an exposure resolved in
  the container is still open in the Mac's frozen copy. The Mac scan's `continue` then suppresses
  `cred.unknown-class` for that credential.
- **Decision item 2 is incomplete.** It says "`record-rotation` reads the list and the state
  file". `record-rotation` also reads every registry file the list names (line 119). That isn't
  a credential read, so the item's claim that it needs no new credential still holds.

## Fix round 2 (worker W-208)

Fixes the two false sentences HQ's Opus re-review found, plus two completions. ADR fix commit:
`b57f67c5` (parent `51045612`, the PR head before this round; `git fetch` showed nothing later
before the push). All lines are in
`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` at `b57f67c5`. I
read every cited line at its commit (infraops-mcp-server `c3359bf`, orchestrator `a410af18`)
before writing it.

### Changes

1. **Context, lines 3484-3492 (was false).** "The state file only clears findings" and "A stale
   state file can therefore only over-report" are replaced. The bullet now says the following:
   - A resolved exposure clears `cred.exposure-rotate`.
   - A recorded rotation or revoke clears `cred.rotation-age` and `cred.rotation-requested`.
   - An exposure the state still shows open also suppresses that credential's
     `cred.unknown-class`, `cred.rotation-age` and `cred.rotation-requested` (the `continue` at
     `cred-rotation.ts` line 249).
   - So a stale state file over-reports the three state-dependent findings, and can hide
     `cred.unknown-class` for a credential whose exposure it still shows open.

   The citation widens from 237-238 to `cred-rotation.ts` lines 140-144, 201-211 and 237-262.
2. **Window verify item, lines 3640-3648 (was false).** "Because the state only clears findings,
   the window can over-report but never hides one" is replaced. The window over-reports the three
   moved findings and hides none of them. It can hide `cred.unknown-class` for a credential whose
   exposure was resolved in the container: the Mac's frozen copy still shows it open. The Mac
   scan suppresses the finding whether or not the credential is handed over, because
   `scanFindings` withholds only the three trigger checks (`cred-rotation.ts` lines 310-315,
   `ROTATION_TRIGGER_CHECKS` at 301-305). The proposer's pass doesn't report it either
   (`rotation_proposer/findings.py` line 60 at `a410af18`, `NOT_ROTATIONS`). The cut-over now
   lists each exposure resolved in the window and shows that its credential's class has a
   rotation policy.
3. **Decision item 2, lines 3528-3530.** `record-rotation` reads "the list, the registry files the
   list names and the state file" (`security-drift-cli.ts` line 119, `loadCredConsumerFiles`).
4. **Citation, decision item 3 "Order", lines 3551-3552.** "The scan reads a missing state file as
   an empty one" now cites `loadRotationState` (`cred-rotation.ts` line 152) on
   `loadValidated0600Json`'s missing-file case (`validated-store.ts` line 27). `credScan` calls
   `loadRotationState` with no existence guard (`security-drift-cli.ts` line 137). The old
   citation, lines 167-170, is a comment in `doCredFindings`.

`git diff --word-diff` against `51045612` shows no other word changed. The other changed lines
are reflow only, to keep the paragraphs at the file's width.

In this report, the Infra Brain 1032 bullet under "Standards followed" gains a sub-bullet: the
window hides none of the three moved findings but can hide `cred.unknown-class`, which the
cut-over's window item checks. The summary at lines 53-54 ("the state file only clears findings,
so the window over-reports but never hides one") is superseded by change 2; I left it as written.

### Checks

- `make check` on the committed tree `b57f67c5`, with database `orchestrator_test_hq_w_208`:
  exit 0, `10 workers [15335 items]`, 15334 passed, 1 skipped.
- Security scan from the repo root: 0 BLOCK, 2 WARN, 1 INFO, the standing findings.
- CI on `b57f67c5`: all three checks pass (run `44210855769`). The Quality job log reads
  `4 workers [15434 items]` and `15411 passed, 23 skipped`. CI collects more items than the local
  run because the environments differ, and more than round 1's CI run (15388); neither run failed
  a test.

### Concerns

- **An open exposure also masks age and request.** The new Context bullet says a stale file
  over-reports the three state-dependent findings. For a credential whose exposure the stale file
  still shows open, `cred.rotation-age` and `cred.rotation-requested` are suppressed too. They
  stay behind that credential's own `cred.exposure-rotate` finding, which is reported, so the
  credential isn't dark. I kept the brief's wording and didn't add this.
