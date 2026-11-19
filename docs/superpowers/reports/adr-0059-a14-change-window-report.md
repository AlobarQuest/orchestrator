Outcome: done

# Build report: ADR-0059 Amendment 14, change-window's move (increment 8)

Worker W-232, branch `hq/adr-0059-a14-change-window`, base `3585b4f9` (#749's merge). The change is
docs only. I made no live call and no production act, and edited no other repository.

- **PR:** #750 (ready for review, not draft). Not merged.
- **Content commits:** `2467c8bc` (the amendment), `f3fb27df` (self-review corrections) and
  `68c395ccbb9acf910c7692e5fb9d4edc38f864c4` (HQ's answer on the `execute` bearer, the last
  content commit).
- **Head:** PR #750's head. After `68c395cc` come only docs commits under `docs/superpowers/reports/`: this report and HQ's answered question.
- **Amendment's line range:** lines 4003-4362 of
  `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`. Line 4002 is the
  blank separator after Amendment 13. Against the base `3585b4f9`, the diff adds 361 lines and
  removes none.

## What I built

Amendment 14 follows Amendments 12 and 13's form: Status, Decided by, Relates to, a line-number
note, Context, Decision (seven items), Verify items, then the table.

- **Decided by** quotes D-66 to D-69 in full, question and recorded answer, as blockquotes. A
  script spliced the text in from `git show 68466c01:state/decisions.md` and then asserted that
  each quote is a whitespace-normalized substring of the amendment. It also quotes the standing
  answer's heading and its whole "Rule:" paragraph. It says D-66 A carries "I will revisit this
  later", that D-68 was made standing, and that D-66, D-67 and D-69 weren't, with no general rule
  for them.
- **Decision items:**
  1. Code (D-66 A), with the unpinned-`main` residual stated plainly.
  2. Prod only (D-67 C), stated as a behavior change.
  3. The four credentials (D-68 B, B and the standing answer). The Anthropic key and the
     `report-change-window` sender are marked as HQ's application of the standing answer.
  4. The check (D-69 A).
  5. HQ's configuration, each item verified in #749's code.
  6. Where each change lands.
  7. Authority, exactly.
- **Verify items:** all five the handoff names, plus a sixth for the Coolify window token's
  permissions at its mint gate. Each names its step: increment 8's cut-over, its
  mint gate, or the quiet slot. D-66's revisit is named as Devon's, with no step and the reason.

### The table's rows (16)

1. Decision 1's clone bullet (306-311): extended.
2. Decision 2's identity (487-495): stands.
3. The `change-window` row (822): stands as a description of the Mac job today. The lane differs.
4. Increment 8, "with the prod and dev Coolify tokens" (1075-1076): superseded for the lane.
5. Increment 8, the identity question (1076-1077): answered.
6. Increment 8, dev Coolify reach (1077-1078): answered.
7. Line 1087, "Each increment's plan records which repositories it changes and which identities
   it creates": met.
8. Cut-over step 2's reporting mode (1133-1135): extended.
9. Cut-over step 4's three-part comparison (1168-1174): stands. The third part is uniform.
10. Q7 (1522-1526): stands.
11. Amendment 6 decision items 1 and 4 (2679-2682, 2694-2696): stand.
12. Amendment 9 decision item 3 (3016-3018) and its row on the done-when paragraph (3067):
    stand.
13. Amendment 9's rows on lines 3060 and 3068: stand.
14. Amendment 9's rows on lines 3061 and 3063: answered.
15. Amendment 13's Context "Five entries" (3751-3754) and wiring bound (3933-3937): stand as of
    their base.
16. Amendment 13's increment 8 verify item (3953-3957): answered by D-69 A, with its plist timing
    corrected (a hollow pass until infraops' removal deletes the plist).

### `deploy.md`

Unchanged. Its change-window section doesn't contradict the amendment, which is the handoff's
condition for editing it. One gap goes to the PR body as follow-up 1: step 9 doesn't state D-69's
order and doesn't name the `WIRING` drop.

## Tests and checks run

- `make check` against `orchestrator_test_hq_w_232`, run three times on the clean committed tree
  (at `2467c8bc`, `f3fb27df` and `68c395cc`), each with
  `TEST_DATABASE_URL`, `ORCHESTRATOR_DATABASE_URL`, `SECURITY_STANDARDS_DIR` (the fixture) and
  `PGTZ=UTC`, after `alembic upgrade head`. Each exited 0. Ruff reported "All checks passed!" and pyright
  reported 0 errors. Tests on the last run: **15,832 passed, 1 skipped (15,833 collected)**, 11
  warnings, in 309 seconds.
- Security scan from the repo root (`security_scan.cli . --category security`), on the last
  head: **0 BLOCK**, 2 WARN. Both WARNs are the gitignore-coverage pattern check, and this diff touches no
  secret file.
- Quote check: the splice script asserted each D-66 to D-69 question and answer, and the standing
  rule, as normalized substrings of the amendment.
- Line check: a script printed the first and last line of every range the amendment cites, at
  `3585b4f9`, and I compared each to the text claimed. Corrections made along the way:
  - the quiet slot is 221-236, not 220-236;
  - decision 2's bullet is 487-494, not 487-495;
  - cut-over step 2's "confirm no pass is running" is 1148-1151;
  - the wiring bound is 3933-3937;
  - `revert_stale_handoffs` is `app/watchdog.py` 11-30.
- ADR scan: I grepped the whole ADR for `change-window`, `increment 8` and `increments 8` to find
  every passage the amendment might govern. The only one missing was row 3067, which joined
  Amendment 9's decision-item-3 row.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`: Amendment 14,
  appended.
- `docs/superpowers/reports/adr-0059-a14-change-window-report.md`: this report.

## The question and HQ's answer

After opening the PR I found that no source named who writes the `execute` bearer's hash into
change-manager's `M2M_TOKEN_EXECUTE` and restarts change-manager. I asked HQ in
`adr-0059-a14-change-window-question.md` and withdrew this report from the branch until it
answered. HQ answered B, with the actor corrected: an interactive session, as the session's part of
the lane's mint gate, not an unattended cycle and not Devon. HQ cited the cycle's credential rule
(Devon, 2026-11-14, after G-57) and the 2026-11-18 standing answer. Decision item 7 now says:

1. Devon mints the bearer into BWS and grants its read.
2. A session reads it by UUID in one process and computes the sha256 hash in-process, never
   echoing it.
3. The session writes the JSON entry to both `is_preview` rows through infraops, citing
   `bearer-settings.md` at `e57076f2`.
4. It restarts change-manager as an announced production act with no lane.
5. It verifies the restart three ways: `/api/health`'s `revision` (`app/main.py` 21-32), one
   `execute`-scoped read, and a refused known-bad control.

All of it happens before the paired pass. Later rotation is left to the bearer's keeper row, which
is added at that gate. The amendment credits the rule to HQ's answer. I didn't find the rule's text
in sds-hq to re-read; I found only references to G-57.

## Sources verified, at their commits

- sds-hq `68466c01`: `state/decisions.md` (D-66 to D-69), `standing-answers.md`, and
  `state/goal.md` line 40 (G-73). The ADR has no G-73 mention, so the amendment cites goal.md.
- W-223's report at `54afa8ba`, sections 1 to 7.
- infraops-mcp-server `fdf3e91b` (on `origin/main`):
  - `dist/bundle/change-mgr-cli.js` exists in the tree.
  - `selectItems` (`run-window.ts` 32-46) filters by instance before the cap.
  - The dry run's JSON line is `change-mgr-cli.ts` 107-117.
  - `getApproved` is `GET /api/items?status=approved` (`api-client.ts` 69).
- change-manager `bbb08394` and `e57076f2` (both on `origin/main`):
  - `EXECUTE` is in `scopes.py`, and `M2M_TOKEN_EXECUTE` is in `bearers.py`.
  - The outcome guard is `api.py` 529.
  - The watchdog reverts only `handed_off`, and reconcile leaves `in_progress` (155).
  - `PROJECT.md` on `origin/main` still holds the entry.
- security-standards `7940572` (`origin/main`): `change-window-agent.yaml` holds `environment: mini`, and
  `governance-map.toml` 178 lists `change-window.sh`. PR #101 (open, head `42bc2097`) has a fixed
  `WIRING` tuple that names `launchagent:com.devon.change-window`.
- project-standards `4673d726`: `wiring.py` 132-141, `_verify_launchagent`.
- infraops-mcp-server `fdf3e91b`: `docs/secrets-inventory.md` line 68 ("Anthropic doesn't
  support per-key scoping"); `scripts/change-window.sh` lines 8 and 13 (Healthchecks.io), 65 and
  67 (the public prod and OrbStack dev URLs).
- alert-hub `2218702` (local `main`): the sender guide's "Route reports to email only" (lines
  242-256), which documents adding a `report-*` rule. That the rule already exists in the hub is
  W-231's claim, and the amendment credits it to W-231.
- Orchestrator at the base:
  - The launcher's lines as cited.
  - `LANE_CHECKS` (`deploy_orchestrator.py` 956).
  - `ENV TZ=America/New_York` (`lanes/Dockerfile` 86).
  - The estate lander's token shape (`.cred-consumers.toml` 665).
  - `.bws-secrets.toml` rows for `bbd71f41-…` (156) and `b74bf8b3-…` (283). `af0e4192-…` and
    `4ed74de3-…` are W-223's reads of other repositories' manifests.
  - `sds_outcome_begin` (`scripts/sds-deadman.sh` 200-215) resolves the ping URL on an acting
    pass, so on `--submit` the ping-URL secret is read before the clone. The amendment says so.

## Self-review

- **Append only:** `git diff` shows 0 removed lines. The heading is the handoff's, verbatim,
  dated 2026-11-19.
- **No full UUID:** existing secrets appear only truncated (`bbd71f41-…`, `af0e4192-…`,
  `b74bf8b3-…`, `4ed74de3-…`). The four new secrets are named by role.
- **Attribution:** HQ's goal.md phrase "revisit candidate B, build nothing that forecloses it" and
  the standing answer's index number aren't attributed to Devon. The amendment says HQ's plan
  records option B as the revisit's candidate (goal.md line 45). It makes no claim of its own about
  foreclosure.
- **Authority:** every grant falls into one of three groups:
  - Devon's later act: the token, the bearer's value, the key, the sender token, and the lane
    account's grants.
  - The mint gate's interactive session, per HQ's answer: the `M2M_TOKEN_EXECUTE` write and the
    change-manager restart.
  - HQ's configuration: the crontab, the dry run, the tailnet address, the check name, the
    digest's form, and the sender's name.

  Item 7 ends with "No other account, BWS grant, Coolify token, change-manager scope, network path,
  executor kind or production act."

## Concerns

1. **"Lists prod items only" versus the code.** The handoff says the lane "never lists or claims
   a dev item", and D-67's question says "the lane lists prod items only". At `fdf3e91b` the list
   call is unfiltered (`GET /api/items?status=approved`), and `selectItems` drops dev items before
   the cap and before any claim. So the lane receives dev items in the list response but never
   selects, claims or executes one. The amendment says this exactly (decision item 2). The intent
   of D-67 C holds, so I didn't treat it as a contradiction that stops the PR. If HQ wants the list
   itself filtered, change-manager's list route takes an `instance` filter (W-223 section 3), and
   that's an infraops change.
2. **The `execute` bearer's hash write: resolved** by HQ's answer (preceding section). A restart
   doesn't change `GIT_SHA`, so `/api/health`'s `revision` shows the service came back on the
   same image, not that the setting loaded. The `execute`-scoped read and the refused control are
   what prove the setting.
3. **The `WIRING` drop is beyond the handoff's security-standards bullet.** If security-standards
   #101 merges as its head holds it, its fixed `WIRING` tuple names the change-window label. After
   D-69 A, that breaks Amendment 13's "names nothing no member declares" bound. The amendment adds
   that edit under decision item 6, conditional on #101, and orders it after change-manager's PR
   and before the plist's deletion (W-223 section 7 item 3 names it). This is a consequence of
   Amendment 13's existing bound, not a new grant.
4. **The Coolify token's terms.** D-68's words are "application read, PATCH, and deploy only, no
   read:sensitive". I couldn't confirm Coolify's permission list from a primary source: the
   infraops Coolify docs search returned nothing. So the amendment keeps D-68's words and moves the
   mapping to a mint-gate verify item. The estate lander's token is `read`, `write` and `deploy`,
   and if Coolify has no application-only write, the gate records what the token reaches beyond
   applications.
5. **The PR opened before the question.** I found the bearer gap during self-review, after #750
   was open, so the question came while the PR was open rather than before it.
6. **Standards coverage.** The handoff's Standards section covers everything this docs change
   touches.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** doesn't apply. The change is
  docs only and adds no logging.
- **Code Brain rule 4 (never silently swallow an exception):** doesn't apply. The change is docs
  only and adds no code.
- **Code Brain rule 5 (never log secrets, credentials, tokens, or full request bodies):** the
  amendment names no secret value. It refers to existing secrets by truncated UUID only
  (`bbd71f41-…`, `af0e4192-…`, `b74bf8b3-…`, `4ed74de3-…`), and to the four new ones by role.
- **Infra Brain 591 and 592 (no BWS token in tracked files or history):** the diff holds no token
  and describes none by example. The security scan reported 0 BLOCK.
- **Infra Brain 593 (no inline `BWS_ACCESS_TOKEN` value):** the amendment names the lane's token
  variable only, never a value.
- **Infra Brain 1032 (`cred.exposure-rotate`):** unchanged by this amendment. I saw no secret value
  in any source I read.
- **Infra Brain 212 (HTTPS for public FQDNs):** the lane serves nothing and has no FQDN.
  change-manager, alert-hub and the Anthropic API are HTTPS. Production Coolify is reached over
  G-73's tailnet address `http://100.92.138.19:8000`: a private tailnet path that the tailnet
  encrypts, not a public FQDN, and the one estate landing uses. That replaces the Mac job's plain
  HTTP to a public hostname.
