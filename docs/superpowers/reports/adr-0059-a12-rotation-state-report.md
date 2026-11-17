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
  - I saw no secret value anywhere.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; docs only.
