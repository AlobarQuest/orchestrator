Outcome: done

# Report: adr-0059-amendment-3-forks-stay

Worker W-67, 2026-11-10. Branch `hq/adr-0059-amendment-3-forks-stay`, based on orchestrator
`531d42c2` (`origin/main` at the start). Draft PR: #648 (https://github.com/AlobarQuest/orchestrator/pull/648), amendment commit `96d696a7`; the report's own commit follows it on the branch.

## What I built

ADR-0059 **Amendment 3** (2026-11-10): the fork-sync watch and fork hardening stay on the Mac.
`origin/main` at `531d42c2` had Amendments 1 and 2 only, so 3 is the next free number. It's
appended to the end of
`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`, with Status
**Proposed**, quoting Devon's D-35 answer, and the shape Status, Context, Decision, Consequences,
"Open before building", plus a "Passages this amendment changes" list like Amendments 1 and 2.

The amendment appends only. Amendments 1 and 2 rewrote body passages in place, but this one is
proposed and not accepted, so the body is unedited and every line the amendment cites still holds
its text at `531d42c2`. The amendment says it governs the passages it lists once accepted. If Devon
wants the body rewritten on acceptance, that is a follow-up change.

During the work I asked HQ one question (`adr-0059-amendment-3-forks-stay-question-answered.md`): the forks get no
Dependabot pull requests, which contradicted item 4's wording. HQ answered "Yes, take the proposed
answer". The amendment's Context quotes D-35 unchanged and states the correction, and decision item
4 gives the corrected form.

### The moving-lane count

Seventeen moving lanes become **fifteen**; three staying agents become **five**. The 15 are the 19
`run-*.sh` launchers less `activation-sweep`, `tool-installer`, `fork-harden` and
`fork-sync-watch`. Derived counts restated in the amendment:

| Count | Before | After |
|---|---|---|
| Moving lanes / staying agents | 17 / 3 | 15 / 5 |
| Stateful moving lanes | 11 | 11 |
| Stateless moving lanes (line 351) | 6 | 4 |
| Read-only set (line 599) | 12 | 11 |
| API-variable set (line 523) | 12 | 12 |
| Pushing lanes (lines 535, 637) | 3 | 2 |
| "SDS lanes" App key readers | 3 | 2 |
| Read-only App key readers | 13 | 12 |
| Identities reading one or both keys | 15 | 13 |
| Week bar: daily lanes with a `/fail` baseline / more frequent | 7 / 10 | 6 / 9 |
| Increment 3 lanes / increment 5 lanes | 7 / 4 | 6 / 3 |

### ADR lines the amendment changes or withdraws (at `531d42c2`)

Fork hardening:

- 107 (`sds_scope.py` in the image for `run-fork-harden.sh`): withdrawn for fork hardening.
- 309-310, 323, 326-327 (per-pass fork clones, cache, increment 5's cache volume): withdrawn.
- 399-401 (no kill switch): still true on the Mac.
- 535-538 (three lanes push through git; `gh auth git-credential`): two moving lanes push; fork
  hardening keeps `gh auth git-credential`.
- 559-561, 567-571 (helper identity note; increment 5 mints the "SDS lanes" token and rewrites
  header and docstring): withdrawn.
- 637-641 (three pushing lanes; `workflows: write` verify): two lanes; verify withdrawn.
- 646-649 (key reader counts): 2, 12 and 13.
- 723-724, 733-734 (private upsert replaced; provisioning through `sds_upsert_check`): withdrawn
  for fork hardening.
- 741-754, 773, 787 (decision 4 and 5 rows and counts): replaced.
- 811-812 ("the exceptions are decision 4's three"): five.
- 1009 (App install): already done 2026-11-09; the forks' place is Open item 1.
- 1029-1032, 1035-1039 (increment 5's fork hardening and its verify): withdrawn.
- 1135-1136 (cut-over comparison run): withdrawn.
- 1189-1190 (no switch in rollback): still true.
- 1262-1264, 1271-1275 (week bar daily list and exclusions): six daily; five agents excluded.
- 1319-1320 (Consequences key readers): two and twelve.
- 1394-1396 (`hardened` branch protection; `workflows: write`): protection sentence holds; verify
  withdrawn.
- 1428-1429 (Q1's "once they leave his `gh` login"): doesn't arise for fork hardening.

The fork-sync watch:

- 532-534 (mint and `GH_TOKEN` export): withdrawn for the watch, which keeps `gh api` on the
  stored login. The membership check's `GH_TOKEN` export (it also reads through `gh api`) still
  stands.
- 599-603 (read-only set): eleven.
- 610-613, 623-626 (factory-runner and fork reads for the watch; upstream-releases verify): verify
  withdrawn; the read-only App's install on factory-runner and the forks stands, because all six
  are members that the landing ledger and the membership check read.
- 628-629 (Issues read for the watch): no moving lane needs it; Open item 2.
- 1004-1006, 1011-1016 (increment 3's list and the watch's launcher and docstring rewrites):
  withdrawn; increment 3 is six lanes.
- 1133-1135 (no reporting mode): withdrawn for the watch; still applies to the membership check.
- 1496-1560 (Q6, Q9): stand for the eleven.
- `lanes-inc-3-verify-report.md` line 429 (probe before the watch moves): withdrawn.

### Credentials

- `fork-harden` keeps Devon's `gh` login (`gh auth git-credential`) and the Keychain
  `BWS_ACCESS_TOKEN_SDS` read. Q1 gave it the App only "once they leave his `gh` login".
- `fork-sync-watch` keeps `gh api` on Devon's login, not a Q6 mint.
- Neither gets nor loses a credential. A narrower Mac credential is Open item 3, not decided.
- App scope: nothing changes by this amendment. The push App's fork installs (Open item 1) and the
  read-only App's Issues permission (Open item 2) are proposed for Devon to decide, since each
  narrows a credential the ADR decided.

### Open items (the amendment's "Open before building")

1. Whether the forks leave the "SDS lanes" App's installation (installation `180257673`).
2. Whether the read-only App drops Issues read.
3. Whether either fork lane later gets a narrower Mac credential.
4. Increment 6 must keep `activate-checkout.sh`, the Keychain items and `sds-deadman.sh`'s
   name-lookup path for the four staying `run-*.sh` launchers. The gap predates this amendment
   (the tool installer and activation sweep already needed it); this amendment adds two launchers.

## Tests and checks run

- `make check`, with `TEST_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` at
  `orchestrator_test_hq_w_67`, `SECURITY_STANDARDS_DIR` at the fixture tree, `PGTZ=UTC`: exit 0. Ruff "All checks passed!", 1036 files already formatted, pyright 0 errors, pytest 14207 passed, 1 skipped, 11 warnings (5:00). `tests/architecture/test_layer_manifest.py` rerun after the report and question files were committed: 3 passed. The full run predates the last text edits to the amendment and this report, which are prose only; the PR's `Quality` job runs on the final head.
- Security scan from the repo root (`security_scan.cli . --category security`), rerun on the final
  tree with the report committed: no BLOCK finding.
  Two WARNs (`.gitignore` glob `*-migration/`, an undeclared UUID `aaff45ca-…` in code) and one
  INFO, none in a file this change touches; they predate it.
- Every line number the amendment cites in the ADR, in `scripts/deploy_orchestrator.py`, the two
  launchers, `.cred-consumers.toml`, `membership-seed.toml` and the increment 3 verify report was
  read back at `531d42c2` and corrected where it was off.
- Read-only GitHub checks: `gh api repos/AlobarQuest/<fork>/contents/.github/dependabot.yml`
  answered 404 for all five forks.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (Amendment 3
  appended; nothing above it changed).
- `docs/superpowers/reports/adr-0059-amendment-3-forks-stay-question-answered.md` (the question HQ answered; HQ renamed it from `-question.md`).
- `docs/superpowers/reports/adr-0059-amendment-3-forks-stay-report.md` (this report).

## Self-review

- Each count was re-derived from the ADR's own lists, not from the handoff's summary, and the base
  was grepped for count words to find sentences that don't name a fork (lines 811-812 was one).
- An advisor review before handover corrected five points: decision item 3 no longer says the tool
  installer acts on a member; the last Consequences bullet rests on decision 4's existing ground;
  the `gh` commands withdrawal is the watch's alone; one `consistency.py` citation; and membership
  is cited to the live record, with the seed's grant lines as evidence.
- The amendment decides no credential change. Every narrowing is an open item for Devon.
- It states the Dependabot correction once, in Context and decision item 4, and quotes D-35
  unchanged.
- The code impact is limited to `LANE_CHECKS` (`scripts/deploy_orchestrator.py`, 951-974) and
  `STAYS_ON_THE_MAC` (`tests/scripts/test_deploy_lanes.py`, 821), which change together. The
  launcher headers, docstrings, `.bws-secrets.toml`, the registry rows and `deploy.md` stay true as
  written, and the amendment says so. No file outside `docs/` was edited.

## Concerns

- The ADR body still describes both lanes as moving until a follow-up rewrites it. The amendment
  says it governs, but a reader of decision 5's table alone sees Moves.
- While the Mac is off for the week bar, the fork-sync stuck-lane alarm is silent while inert
  landing, having moved, keeps landing the forks' sync pull requests. The amendment states it as a
  consequence. The tool installer isn't running either, so nothing reaches the machine in that
  week.
- The year plan's Phase 1 wording (sds-hq `plan/year-plan.md`, lines 32 and 54) names only the tool
  installer and the Keychain rotation instance. Devon changes it separately; not edited here.
- `rtk` was missing from the shell's `PATH` in the first background `make check` (exit 127 before
  anything ran); I reran it without `rtk proxy`.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply. The change is docs only and
  adds no code that logs.
- **Code Brain rule 4 (never swallow an exception silently):** doesn't apply to docs. The one check
  the amendment relies on, `sds-deadman.sh`'s name lookup for the staying lanes, reports and never
  gates: an unreadable URL logs one reason and the check goes down after its grace, so the failure
  is heard (decision item 5).
- **Code Brain rule 5 (never log secrets):** the amendment and report name secret ids and
  identifiers only (BWS `f793576f-…`, `51509d5e-…`, the App's installation id `180257673`), never a
  value.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN` value; examples are obvious placeholders):** the diff holds no token and no
  `BWS_ACCESS_TOKEN` value; it names the variable only. The security scan reports no BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value in any file or command output.
  Nothing to report.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The change adds no endpoint or FQDN.
- **Infra Brain 2890 (never commit a ping URL):** the diff names check names
  (`sds-fork-hardening`, `sds-fork-sync-watch`) and no Healthchecks ping URL.
