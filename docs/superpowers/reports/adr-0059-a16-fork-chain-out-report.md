Outcome: done

# Build report: ADR-0059 Amendment 16, the fork chain leaves SDS for localops

Worker W-235, branch `hq/adr-0059-a16-fork-chain-out`, from orchestrator `4046d953`. Docs only.
Step 3k (`fork-chain-out`), task (1) of five. The amendment is written on the basis of D-71 A,
which answered this session's question (`adr-0059-a16-fork-chain-out-question.md`, which HQ renamed
to `-question-answered.md`; this change doesn't commit it).

## What I built

- **ADR-0059 Amendment 16**, at
  `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` lines 4495-4800.
  The amendment covers the following:
  - It quotes D-57's question and answer verbatim, cites goal step 3k as the session's record, and
    quotes D-71's recorded answer verbatim.
  - It supersedes Amendment 3 item by item and gives the counts: 15 move, 2 stay, 3 leave SDS.
  - It describes localops, the five tasks with the file list task (2) copies, and the three D-71
    cuts.
  - It covers membership (six forks, ADR-0058's `/review` path, at task (4)), the SDS lanes that
    stop covering the forks, and the alerting interim.
  - It states the authority exactly, with the interim reads and the task that ends each.
  - It lists eight open items, each with a task, and a passages table cited by line at `4046d953`.
- **ADR-0042 Amendment 6**, lines 300-307: the tool installer moves to localops under ADR-0059
  Amendment 16. The ADR keeps governing it there, except where D-71's cuts change "The record" and
  the window.
- **ADR-0057 Amendment 2**, lines 157-162: fork hardening moves to localops, with its scope from
  localops' own list. The ADR keeps governing it there.

Both short amendments say "same function" rather than the handoff's "moves unchanged". D-71 A
("Same function, not same code") replaced "unchanged".

## Files task (2) copies

These are taken from `layers.toml`'s `fork` layer (lines 707-733) and from what those files load:

- `src/fork_sync/`: `__init__.py`, `acknowledge.py`, `bws.py`, `checks.py`, `CLAUDE.md`, `cli.py`,
  `estate.py`, `fork_sync_review.py`, `gh.py`, `onboard.py`, `onboarding.py`, `watch.py`, and
  `templates/` (`audit-cargo.yml`, `audit-npm-no-deps.yml`, `audit-npm.yml`, `ci-cargo.yml`,
  `ci-npm-no-deps.yml`, `ci-npm.yml`, `upstream-sync.yml`).
- `src/fork_harden/`: `__init__.py`, `CLAUDE.md`, `cli.py`, `git.py`, `harden.py`, `mwg.py`,
  `n8n.py`, `octo.py`, `read_lines.py`, `rules.py`.
- `src/tool_installer/`: `__init__.py`, `alerts.py`, `CLAUDE.md`, `cli.py`, `github.py`,
  `install.py`, `npm.py`, `orchestrator_client.py` (cut), `plugin.py`, `policy_client.py` (cut),
  `record.py`, `registry.py`, `toolchain.py`, `tools.py`, `window.py`.
- `tests/fork_sync/`: `__init__.py`, `test_ack.py`, `test_alerts.py`, `test_cli.py`,
  `test_onboard_reads.py`, `test_onboard.py`, `test_review_script.py`, `test_sync_workflow.py`,
  `test_watch.py`, and `fixtures/` (`cargo-audit-0.22.2-rtk-v1.0.1.json`,
  `npm-11-audit-package-lock.json`, `npm-11-audit.json`).
- `tests/fork_harden/`: `__init__.py`, `test_cli.py`, `test_harden.py`, `test_mwg.py`,
  `test_n8n.py`, `test_octo.py`.
- `tests/tool_installer/`: `__init__.py`, `test_cli.py`, `test_client_surface.py`,
  `test_install.py`, `test_npm_workspace.py`, `test_npm.py`, `test_plugin.py`, `test_record.py`,
  `test_registry.py`, `test_toolchain.py`, `test_window.py`.
- Scripts: `run-fork-harden.sh`, `run-fork-sync-watch.sh` and `run-tool-installer.sh`; the three
  `install-*-launchd.sh` installers; the three `com.devon.*.plist` files; and
  `tests/scripts/test_run_fork_sync_watch.py` and `tests/scripts/test_run_tool_installer.py`.
- Launcher helpers: `scripts/sds-deadman.sh`, `scripts/sds-bws.sh`, `scripts/sds-token.sh` and
  `scripts/sds-install.sh`.
- Shared packages, copied (D-71): `src/alert_hub_client/`, `src/estate_clients/confined.py` and
  `src/revision_watcher/estate.py` (`LandingReader`). Also
  `tests/architecture/import_scan.py`, which `test_client_surface.py` line 14 imports.
- Docs: `docs/operations/fork-hardening.md` and `docs/operations/onboarding-a-fork.md`.
- `pyproject.toml`: the three console scripts (lines 86-103) and the `templates` package data
  (line 127).

Every path exists at `4046d953`.

## Lanes that stop covering the forks (decision item 5)

- **Inert landing (fork sync pull requests).** Source: change-manager `app/deploy_policy.py` lines
  611-630 at `4ed5da6`. Its refusal is in `src/inert_lander/cli.py` line 169 and
  `src/lander/core.py` line 162. localops lands each sync pull request (open item 1).
- **The orchestrator's fork branches in inert landing.** Sources: `terms.py` lines 68-69,
  `inert_landing_admission.py` lines 255-262 and 500-504, `inert_pr_merge.py` lines 46-51, and
  `revert_act.py` lines 14-18. The merge-commit property survives in localops. Task (4) decides
  whether the code goes.
- **`fork-sync onboard` step 10.** Source: `onboarding.py` lines 683-690 and 839-849. It ends at
  task (3).
- **The landing ledger.** Sources: `scripts/sds_scope.py` lines 59-63 and
  `run-landing-ledger.sh` line 130. It ends. `record.py` lines 206-207 go at task (4).
- **The membership check.** Sources: `consistency.py` lines 127 and 145-148, and `cli.py` lines
  19-21, 36 and 174-192. It ends at task (4).
- **The read-only App's installation on the forks.** Sources: D-14 and D-58. Devon removes it at
  task (4).
- **The pin watcher and membership check public listing.** Source: Amendment 4. The forks become
  public non-members, and nothing replaces this coverage.
- **The credential registry.** Sources: `.cred-consumers.toml` lines 104-106, 160-162, 216,
  411-413 and 453-455, and `rotation_destination_format.json` lines 153-155. Task (4) re-points or
  removes the rows.
- **`tool-installer`'s observation write and policy read, and `fork-harden`'s scope.** Task (2)
  cuts them.

I also checked the following, and none covers the forks as members:
- `test_hold_paths.py`: holds apply to bumps, and a fork's sync isn't one.
- `test_revert_act.py` and `test_revert_landing.py`: these cover the revert watch, which never
  watches a fork's sync.
- `work_carrier/prepare.py` and `workability.py`: "fork" there means any other owner's fork.
- `verifier/evidence.py`: "fork" there means forking a head.

## Tests and checks

- `make check` with `TEST_DATABASE_URL` pointing at `orchestrator_test_hq_w_235`: ruff "All
  checks passed!", pyright "0 errors", and pytest "15832 passed, 1 skipped". This ran on the first
  version of the amendment. After the review edits I reran it; that result is in the following
  section.
- Security scanner from the repo root: 0 BLOCK, 2 WARN and 1 INFO.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (appended)
- `docs/decisions/0042-the-last-step-of-the-fork-sync-lane-is-a-scheduled-local-install.md`
  (appended)
- `docs/decisions/0057-fork-hardening-is-a-separate-process-that-writes-a-hardened-branch.md`
  (appended)
- `docs/superpowers/reports/adr-0059-a16-fork-chain-out-report.md` (this file)

## Self-review

An advisor review of the first draft found six issues, and I fixed each one:
- A wrong seed claim: `n8n-manager` has no `rollouts` grant.
- An "until" against "after" contradiction on Amendment 3 item 2.
- A weak `sds_scope` cite.
- An incomplete lane sweep. I added the fork branches in inert landing, the public listing and the
  credential registry.
- Missing passages from Amendments 3, 5 and 13 and from decision 2 and Q9.
- One end task for every interim read. Each read now names its own task.

## Sources cited, with commits

- sds-hq `state/decisions.md` at `27984a2`, line 26 (D-57). At `beda28c`: line 13 (D-71), line 26
  (D-58) and line 69 (D-14).
- sds-hq `state/goal.md` at `27984a2`, lines 15 and 42 (step 3k).
- sds-hq `plan/year-plan.md` at `27984a2`, lines 32, 55 and 57.
- change-manager `app/deploy_policy.py` at `4ed5da6`, lines 611-630 (line 624 is `sds-canary`).
- factory-runner `.cred-consumers.toml` at `08dc5aa`, line 108 (`fork-sync-app-private-key`).
- Every orchestrator line cited is at `4046d953`.

## Concerns and what I couldn't confirm

1. **The narrow identity in localops.** Until the `sds-bridge` read ends (task (3), or task (5) if
   localops' own source isn't granted by then), localops holds the `sds-operator` identity. That
   identity can read the SYSTEM and OBSERVER bearers (`run-tool-installer.sh` lines 89-92).
   localops never fetches them, but D-71's "no SYSTEM or OBSERVER bearer in localops" holds only
   for what localops fetches, not for what its identity can reach. The amendment says this in
   decision item 7. If Devon meant reach, the `sds-bridge` read has to move to another identity at
   task (2).
2. **`modern-web-guidance` isn't in the seed.** The handoff says the seed lists it, but
   `membership-seed.toml` lists only five forks. I relied on D-58 for the live record and didn't
   read `GET /api/v1/members` (no live call). Task (4) reads the live record first.
3. **ADR-0056 gets no amendment.** ADR-0056 governs the fork-sync lane, and the handoff allowed
   edits only to ADR-0042 and ADR-0057. ADR-0059 Amendment 16 names it under "Relates to". HQ may
   want a one-line ADR-0056 amendment at task (2).
4. **Ending task (3) reads depends on Devon's gates.** Decision item 7 names task (3) for the
   `sds-bridge`, Healthchecks and change-manager reads. Each new credential is a later gate, so
   any read whose replacement isn't granted by task (3) carries to task (5).
5. **Fork code left in SDS.** The inert landing path's fork branches and change-manager's
   `fork-secure-upstream-sync[bot]` author become unreachable after task (4). Removing them changes
   a landing path, so task (4) decides it (open item 6).

## Standards followed

- **Code Brain 3 (structured JSON logs), 4 (no swallowed exception) and Infra Brain 212 (HTTPS
  for public FQDNs):** they don't apply. The change is docs only, with no code and no app domain.
- **Code Brain 5 (never log secrets) and Infra Brain 591, 592 and 593 (no token in tracked files,
  in history, or inline):** the amendment names no secret value and no token-shaped example. It
  names secrets only by UUID prefix with `…` (`221a48d5-…`, `f793576f-…`, `b415d03b-…`,
  `51509d5e-…`, `998b817e-…`, `b74bf8b3-…`). The scanner reports 0 BLOCK.
- **Infra Brain 1032 (rotate an exposed credential):** it doesn't apply, because nothing was
  exposed.
