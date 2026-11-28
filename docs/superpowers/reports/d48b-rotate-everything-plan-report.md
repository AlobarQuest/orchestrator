Outcome: done

# D-48 B pass plan: build report (W-336)

Handoff: sds-hq `state/handoffs/d48b-rotate-everything-plan.md`. Branch
`hq/d48b-rotate-everything-plan`, based on `bd3fb190`. Docs only.

Pull request: https://github.com/AlobarQuest/orchestrator/pull/832 (open, not merged).

## What I built

- **ADR-0055 amendment 38 (Proposed)**, appended to
  `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`, with one `Amended:`
  line. It contains the source quote, what "the move" means and what was read, D-48's exposure
  closure, the scope lines with their sources, how the standing answers apply, the inventory, the
  rows already rotatable, the proving order, code tasks T1 to T6 with exit checks, and ten open
  items. It changes nothing that runs.
- **The question file**, `docs/superpowers/reports/d48b-rotate-everything-plan-question.md`: each
  of the ten open items has options A, B and C, a recommendation with its reason, and the reply
  line `D-n: C, C, A, B, B, B, A, B, A, A`. Round 1 split item 3 and changed this line; see
  "Round 1".

## What I read

Registries, by `git show origin/main:.cred-consumers.toml` after `git fetch`:

| Registry | Commit | Rows | `true` | Not `true` |
|---|---|---|---|---|
| orchestrator | `bd3fb190` | 39 | 24 | 15 (9 no key, 6 `false`) |
| change-manager | `ebd43ee4` | 5 | 4 | 1 |
| factory-runner | `5aadb110` | 2 | 1 | 1 |
| infraops-mcp-server | `35b82caf` | 9 | 7 | 2 |
| localops | `34319282` | 1 | 0 | 1 |
| security-standards | `568376e7` | 1 | 0 | 1 |
| sds-hq | `aeb71e91` | 8 | 0 | 8 |

Also read:

- **orchestrator at `bd3fb190`:** `src/rotation_worker/` (`types.py`, `adapters/`,
  `registry.py`, `grants.py`, `config.py`, `readers.py`, `envelope.py`), `test_rotation_agreement.py`,
  `.bws-secrets.toml`, ADR-0055 decision 4 and amendments 24, 32 and 36, ADR-0059's header, and
  `docs/operations/rotation-executor.md`.
- **sds-hq:** `.bws-secrets.toml` at `aeb71e91`, plus the local working tree's `state/goal.md`,
  `state/decisions.md`, `standing-answers.md`, `state/history/progress-2026-11.md` and
  `state/gates/keepers-move.md`.
- **intent-packages at `cdf68a92`:** every `packages/rotation-*/lineage.yaml`.
- **alert-hub:** `src/admin/routes.ts` at the local clone's `HEAD`, `35daeb47`. The clone has no
  remote, so the amendment marks this source unverified.
- **launchd:** `launchctl list`, a read-only check that `com.devon.rotation-worker` is loaded.

## Counts

- 65 registry rows, of which 36 are `true` and 29 aren't. One of the 29 is out of scope (sds-hq's
  ping URL), so 28 are inventoried in scope.
- 10 credentials with no registry row are inventoried, and one more (`rotation-inbox`) is excluded
  with its reason.
- Of the 29: 7 record a `created` before 2026-11-06, 8 on or after it, and 14 record none.
- Of the `true` rows outside the ping URLs, only `secrets-broker-bws-token` has a revision approved
  since the move, and its rotation completed (G-114, record 258).

## Decisions I made, and why

- **Amendment number 38.** The last heading is amendment 36. Amendment 37 exists only as an
  `Amended:` line (#831), and no "amendment 38" appears anywhere under `docs/`.
- **Orchestrator's counts are 9 and 6, not the handoff's 10 and 7.** A parse of the file at
  `bd3fb190` gives 39 rows, 24 `true`, 9 with no key and 6 `false`. The amendment states the
  difference.
- **"The move" is ADR-0059's lane move**, accepted 2026-11-06. D-48 (2026-11-14) came after the
  October host move and during this one. The amendment states the boundary, and Open item 1 asks
  about everything older.
- **"Rotated since the move" uses package approvals**, the approved revision dates at
  intent-packages `cdf68a92`. An approval isn't proof that a rotation completed, and I didn't read
  infraops' rotation record, so the amendment says both. The broker token's completion comes from
  HQ's G-114 entry.
- **The unregistered check matches row UUIDs.** I matched `bws_uuid` and `bws-secret` consumer
  UUIDs, not a substring. That surfaced `APP_BRAIN_READ_KEY`, which appears only in the broker's
  `reach_secrets`.
- **I kept sds-hq's Slack and sds-report rows in the table under their borrowed class.** Code task
  T3's first exit check makes an alert-hub type refuse any row whose provider isn't `alert-hub`.
  Re-classing them is a registry edit, which is a stop point.
- **The observer bearer is an open item, not a D-84 case.** Its identity files the record that
  admits a policy approval, which is the reach D-84's standing answer excludes.
- **The EXECUTE bearer is HQ's check, not a question.** Its keeper is already in
  `Rotation / Keepers`, so a hand-over adds only the flag, and D-84 condition 2 is a check HQ runs.
- **Item 7 keeps D-54 A's BWS copies** (standing) and plans T6, adding no grant. A first draft
  proposed deleting the copies and revoking the bump-proposer token. That contradicted D-54 A and
  D-81 (6), which kept the bump-proposer account as an append holder.
- **Keeper moves are gates, not questions**, under D-84's condition 1. New reach is an open item:
  items 3, 5 and 6.
- **D-48's exposure is cited as closed by revoke.** The exposed 2026-11-13 token was revoked at G-92
  on 2026-11-23 (its revoke alert opened at 16:58 UTC and resolved at 17:04 UTC), and its successor
  at G-112. #828's row removal isn't the closure
  (rule 1032).

## Tests and checks

- `make check`, committed tree `f8eb7493` (and earlier at `d286a55a`), own database
  `orchestrator_test_hq_w_336`: exit 0 both times,
  **15185 passed, 1 skipped**.
- Security scan from the repository root (`security_scan.cli . --category security`): 0 BLOCK, 2
  WARN (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`) and 1 INFO, all present before
  this change, which touches no manifest or ignore file.
- `code-standards risk --repo .`: `normal`, so no vendor review is needed.
- No `/code-review` run: the diff is docs only. I reviewed it myself (next section).

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (one `Amended:` line,
  and amendment 38 appended).
- `docs/superpowers/reports/d48b-rotate-everything-plan-question.md` (new).
- `docs/superpowers/reports/d48b-rotate-everything-plan-report.md` (new, this file).

## Self-review

- **Line citations:** each ADR line range the amendment cites (decision 4 227-231, FE-4 3538-3543,
  amendment 32 item 1 4657-4670) was checked against `git show bd3fb190:…`.
- **Values:** none appear. UUIDs are shortened to their first group in the table of unregistered
  credentials, and no token shape or access-key prefix is written.
- **Authority:** the amendment grants nothing. Every new reach is an open item, and every Devon act
  (keeper move, grant, mint, revoke) is named as a gate.
- **Unverified claims are marked:** the alert-hub source (no remote), whether the dev Coolify
  measurement and canary for the change-window token exist, whether the EXECUTE scope is within the
  full bearer's, whether decision 4's set names the inbox token, the helper's key and the
  Management key, whether sds-hq and security-standards have member rows, the brain service's key
  handling, and Slack's and Claude's rotation APIs.

## Concerns

1. **The handoff and `goal.md` say 17 orchestrator rows aren't `true`; the file has 15.** HQ may
   want to correct `goal.md`'s line.
2. **The alert-hub clone at `~/Projects/alert-hub` has no `origin`,** so I couldn't read it at
   `origin/main`. T3's amendment has to read the deployed source.
3. **`orchestrator-factory-runner-bearer` is `true` but has no rotation package** at
   intent-packages `cdf68a92`, in `packages/` or `archive/`. Either the SDS has never rotated it,
   or its package has another name. I didn't resolve which.
4. **Two infraops findings are outside this task.** The Healthchecks Management key and
   `prod-coolify-api-token` are named in orchestrator's manifest, and no registry row covers them.
   Open item 10 covers both.
5. **Planning docs have no Code Brain road,** as the handoff notes, so no road applied.

## Standards followed

- **Code Brain BLOCK 3 (structured JSON logs) and 4 (never swallow an exception):** not
  applicable, because no code changed.
- **Code Brain BLOCK 5 (never log secrets):** no value, header or token appears in any file. The
  only commands that touched credentials were `git show` of registries and manifests, which hold
  UUIDs and fingerprints only.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files, history or inline):** I wrote no
  token-shaped string, and the scan found 0 BLOCK.
- **Infra Brain 1032 (`cred.exposure-rotate`):** the amendment says D-48's exposure closed by the
  G-92 and G-112 revokes, and says outright that #828's row deletion didn't close it.
- **Infra Brain 212 (HTTPS):** not applicable, because no app or domain changed. The amendment
  says so.
- **Construction-mode ruling:** cited by path in the amendment's header and not restated.
- **sds-hq standing answers:** amendment 38, Decision section 2, says how each applies.
  - 2026-11-06 limit 2: nothing is carried.
  - 2026-11-10: ping URLs are out.
  - 2026-11-18: narrowest credential.
  - D-82: proof setup.
  - D-83 and D-84: root reach, with D-84's exclusion applied to the observer bearer.

## Round 1

Fix round 1 of 2, by W-337 (sds-hq `state/handoffs/d48b-rotate-everything-plan-fix-1.md`), after
HQ's Opus whole-branch review of `5e6a0274`. `git log 5e6a0274..origin/hq/d48b-rotate-everything-plan`
was empty before the work began. Head before this round: `5e6a0274`; new head: the commit that
adds this section (see the pull request's commit list).

### Edits

1. **sds-hq needs a reach T1 didn't name (blocking).** `git_mirror.py` lines 1-7 at `bd3fb190`
   say every repository the executor reads is public and no credential is passed; `registry.py`
   lines 4-6 read registries only through `RepoMirror`. `gh repo view` (2026-11-28): sds-hq is
   PRIVATE, security-standards PUBLIC. T1 now adds security-standards only, states the fact with
   both citations, and leaves sds-hq to Open item 3. Item 3 is now two choices in both files: A, B
   or C for security-standards and localops, and (i) a read-only GitHub credential for the mirror
   (new reach, then sds-hq joins `REGISTRY_REPOSITORIES` with its grant), (ii) the rows move into
   orchestrator's registry, under orchestrator's existing grant (`grants.py` gates a row by its
   registry's repository), or (iii) hand-rotated, for sds-hq. The question file recommends A and
   (ii), with reasons, and says (ii) puts seven HQ credentials under orchestrator's grant. The amendment's "every grant, reach or
   path is an open item or a gate" is true for T1: the security-standards grant is Devon's act
   before the first flag PR, and sds-hq's reach is item 3 (i).
2. **Proof setup** restates D-82 exactly: one canary application holding a single non-secret
   variable and up to three throwaway dev Coolify tokens Devon mints and revokes (gates), for the
   same kinds of proof only; production probe tokens stay Devon's decision.
3. **`UNTYPED`:** "Seven classes have none (`UNTYPED`); among them are …".
4. **Item 5:** B now says a person creates a new sender, pastes its token into the inbox, and
   revokes the old sender after the executor's proof and reader wait. A said "reissues by machine",
   the same unsupported claim, so it now says "creates the new sender and revokes the old one by
   machine". Both files agree.

New reply line: `D-n: C, C, A (ii), B, B, B, A, B, A, A` (item 3 takes a letter and a numeral).
The round-0 line earlier in this report is the record of the first draft.

### Checks

- Token-shape grep of the added lines of `git diff bd3fb190..HEAD` (the BWS token shape, and an
  inline assignment of the bootstrap token variable): 0 matches in 787 added lines.
- Docs only, so `make check` wasn't rerun; no code, manifest or ignore file changed.

### Concerns

- Follow-ups 5 to 12 from the fix handoff are written into #832's body, not edited here.
- Item 5 A was edited though the handoff named only B: A said "reissues by machine", the same
  claim alert-hub's routes don't support. HQ may revert it if it wants B alone changed.
- The worker report for this round is `d48b-rotate-everything-plan-fix-1-report.md`, left
  uncommitted so the pull request touches only the three named files.

### Standards followed, round 1

The section `Standards followed` still holds. Round 1 adds: Code Brain 5 and Infra Brain 591-593,
no value and no token-shaped string written (grep, 0 matches); the 2026-11-06 limit 2, sds-hq's
new reach is an open item and not carried into T1; the 2026-11-18 narrowest-credential answer,
the reason item 3 recommends (ii) over (i); D-82, restated as given.
