Outcome: done

# Report: ADR-0055 Amendment 34, the broker token's rotation route and the unlock tier

Worker W-316, 2026-11-26. Branch `hq/broker-token-rotation-adr`, based on orchestrator `8b326f2d`.
Docs only. I made no live call, changed no code, and took no production action.

## Concerns, first

1. **The root already reaches the SYSTEM and VERIFIER bearers through the broker's keeper.** The
   broker's token reads project `SDS Operator` by a project grant. That project holds
   `orchestrator-system-bearer` (`221a48d5-…`) and `orchestrator-verifier-bearer` (`660d5846-…`)
   (`.bws-secrets.toml` lines 28-30 and 66-68). The broker's keeper is in `Rotation / Keepers`,
   which the executor's root writes and reads (ADR-0055 decision 4, lines 215-217). So whoever holds
   the root can read the broker's token, and with it both bearers. This has been true since Devon
   stored the keeper on 2026-11-20. Four passages say those bearers are outside the root's reach,
   and they hold only for the root's own grants: decision 4 (lines 217 and 223-225), T1 (line 365),
   amendment 29 item 2 (lines 4139-4140), and amendment 33 item 7 (lines 5212-5214). D-81 (2) B's
   option text doesn't name this reach. The route adds no grant, but it does have the executor log
   in with the broker's token on each rotation. That login lists ids and reads no value. I wrote
   this up as Open item 10 for Devon. It blocks the row's `rotated_by_sds`, not this PR. I didn't
   treat it as a stop point, because the adapter can reach the token without any change beyond
   D-81 (2) B. HQ may still want to raise it with Devon ahead of the code PR.
2. **The bind on a project grant is unmeasured, and it may refuse every broker token as the row
   is written.** `_REACH` digests every project and every secret the token lists. The row's
   `reach_secrets` names only its 17 single-secret grants. If the SDK's listing includes a granted
   project's secrets, the digests can't match. This row is the only lane-token row with a
   non-empty `reach_projects`. I made this item 5, "Open, for HQ", because it concerns the form of
   a check and grants no authority. My recommendation is to measure first.
3. **The first rotation would take the issue plan as the row stands.** The row has no `created`,
   so infraops serves a null `last_rotated` and the proposer picks the issue plan. The issue would
   stall at `store`, because the keeper holds Devon's real token and not the empty marker. Item 3
   plans `created = "2026-11-20"`. With that, the rotation plan runs and each revision is Devon's
   to approve by name, because the standing approval covers only the issue plan.
4. **Container-name stability is still unverified.** No record at `8b326f2d` closes amendment 29's
   verify item, and I made no live call. Exit checks 1 and 2 in item 9 close it for the broker.
5. **Two concerns fall outside the standards in the handoff.** Neither Code Brain nor Infra Brain
   has a rule on transitive reach through a stored token (concern 1). Neither has one on rollback
   for a shared service's credential (Open item 12). Both rest on this ADR's threat model.
6. **The header list in ADR-0055 is still stale.** I left it unchanged, as amendments 32 and 33 did.

## What I built

ADR-0055 Amendment 34 (Proposed), appended to
`docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`. It follows the shape
of Amendments 32 and 33: Context, Decision, "What it grants, and nothing more", Consequences, and
"Passages it governs".

### The amendment's items

1. **The unlock tier (D-81 (1) A).** The tier is a name with no mechanism. Its test is whether
   anything holding the credential can read for others, which is amendment 30 item 8's reason. Only
   the broker's token is named in it today. The item spells out what each use being Devon's
   browser act means for each act:
   - **Mint:** in the BWS web app, pasted into the inbox.
   - **Grant:** every grant is a web-app gate step.
   - **Revoke:** in the web app, after the revoke alert, or first after a leak.
   - **Approval:** every package revision, by name.

   The item also lists what becomes the executor's job.
2. **The route (D-81 (2) B).** It uses the rotation plan derived in-process at `8b326f2d`, with
   these units and acts:
   1. The mint (Devon).
   2. `store`: `verify_new`, then `store`, then `distribute` (one service `PATCH`, no restart).
   3. The session's restart.
   4. `verify`: the `loaded` match.
   5. The revoke (Devon).
   6. `confirm`.
3. **The row's anchor, and who approves.** The row gets `created = "2026-11-20"`, so the proposer
   picks the rotation plan. The standing approval refuses it, so each revision is Devon's to
   approve by name.
4. **The loaded-fingerprint proof.**
   - **Target:** `[target.secrets-broker]` with `env = "BWS_ACCESS_TOKEN"`.
   - **Container:** `secrets-broker-wogvks6obmvv9fdotlgnw7r3`, read from the repository.
   - **Stability:** unverified, and it fails safe if the name changes.
   - **Row field:** `helper_target = "secrets-broker"`. The existing derivation test holds without
     change.
5. **Open, for HQ: the bind on a project grant.** Options are A (no change), B (list every
   project secret) and C (an exact project set plus a subset of secrets). My recommendation is to
   measure first, then A, otherwise C.
6. **The restart.** The session runs it through `coolify_deploy` at the running tag, and never
   through `restart`. It holds the restart while any broker lane's check shows `started`, and
   avoids the 04:00 change-window pass. The cost is one possible missed read per broker lane.
7. **A leak revokes first.** If Devon revokes first, every broker lane's reads fail until `store`
   completes and the session restarts the broker.
8. **Logging and failure.** Evidence holds fingerprints only, and every step fails closed. No
   revoke alert is sent before `verify`.
9. **The code pull request.** It lists the file changes and one root act on the host, then four
   exit checks:
   1. The target answers with no mint.
   2. The container name survives a redeploy.
   3. Item 5's measurement is recorded.
   4. One real rotation completes. A canary isn't possible.
10. **Open, for Devon: the root's reach to SYSTEM and VERIFIER through the keeper.**
11. **Open, for Devon: whether the unlock tier also names the root token.**
12. **Open, for Devon: rollback after a failed broker start.**

### Each "Open" item, with options and recommendation

- **Item 5 (HQ): the bind on a project grant.**
  - A: no change, if the listing omits project secrets.
  - B: list every secret in both projects in `reach_secrets`. This is brittle: every new project
    secret costs a mint sitting.
  - C: bind on an exact project set, with every declared secret listed. This is weaker than an
    exact match.

  **Recommendation:** measure first, then A if it holds, otherwise C.
- **Item 10 (Devon): the root's transitive reach.**
  - A: accept as found, and have T1 state it. This follows the D-65 precedent.
  - B: move the keeper out of the root's reach. D-81 (2) B can't then be built, and the token
    rotates by hand.
  - C: keep both bearers off the broker. That reverses amendment 32 for three lanes.

  **Recommendation:** A for the development phase, reopened in D-64's security phase.
- **Item 11 (Devon): whether the tier names the root token.**
  - A: the broker's token only.
  - B: the root token too, as a name only.

  **Recommendation:** B. D-81 (1)'s option text says "any other key that mints or reads for others".
- **Item 12 (Devon): rollback after a failed start.**
  - A: no rollback; fix forward.
  - B: a restore verb that re-`PATCH`es the old keeper revision. That is a new executor ability.

  **Recommendation:** A. The bind proves the token works before the `PATCH`.

## Checks run

- **Line-number check.** I printed every cited range at `8b326f2d` with `git show 8b326f2d:<file> |
  sed -n`. This covered the ADR, `.cred-consumers.toml`, `generate.py`, `standing_approval.py`,
  `rotation-executor.md`, `deploy_orchestrator.py`, `sds-pg-rotate`, `factory-policy.toml`, and the
  D-81 question file. I found and fixed seven ranges that were off: decision 4's hand-rotated set,
  `generate.py`'s rotation return, `standing_approval.py`'s condition, the helper's `docker exec`
  line, its empty-fingerprint lines, the D-81 option text, and amendment 33 item 7.
- **Plan derivation.** I ran `plans.candidates` in-process on the row's two destinations. It gave
  the rotation plan with units `store`, `verify` and `confirm`, and the issue plan with one unit,
  `store`, as quoted.
- **Code reads.** I read `bws_lane_token.py` whole, plus `coolify.py`,
  `bws_machine_token.py` (`_REACH`, `reach`, `reach_digest`), the helper's `_target` and `_loaded`,
  `secrets_broker.py`'s token use, `cli.py::_first_issue`, `plan_for`, the standing approval's
  issue condition, `checks._check_issue_fact`, and both row tests.
- **Stop point 2.** `git fetch`, then `git log 8b326f2d..origin/main -- <ADR>`, returned nothing:
  no ADR-0055 change on main while I worked.
- **Token-shape grep of the diff.** I searched `git diff 8b326f2d` for the BWS access-token shape
  and for an inline `BWS_ACCESS_TOKEN=` assignment. No hits. The ADR describes the known-bad
  control in words only.
- **Layer manifest.** `tests/architecture/test_layer_manifest.py`, run after the commit: 3 passed. The
  report path is covered by an existing pattern.
- I didn't run `make check`, because this is a docs-only change and the gate needs Postgres. The
  only check docs affect is the layer manifest's.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: Amendment 34
  appended. No earlier text was edited.
- `docs/superpowers/reports/broker-token-rotation-adr-report.md`: this report.

## Self-review

- **No false claims.** Each code claim cites its file and line at `8b326f2d`, and each range was
  printed. Unmeasured things are labelled unmeasured: the SDK listing, the container name, and the
  helper's behaviour after a rename beyond the code path. No authority is claimed.
- **No unapproved authority.** "What it grants" says nothing is granted. The `targets.toml` entry
  is a root act that D-81 (2)'s option text names, and it reads a fingerprint only. The restart
  uses the existing move route and gate pass.
- **Scope.** I changed only the ADR and this report, as the handoff required.

## Standards followed

- **Code Brain 3 (logging):** item 8. The route adds no logger, and every step logs through the
  executor's structured logging and `contract.py` evidence.
- **Code Brain 4 (error handling):** items 4, 6 and 8. A refused bind, a 4xx `PATCH`
  (`CoolifyRefused`), and a mismatched or unreadable `loaded` each fail closed. A renamed container
  waits and alerts; it never passes.
- **Code Brain 5 (security):** items 4 and 8. Only fingerprints (sha256 prefix and length) appear
  in evidence or the helper's answer, and the reach listing digests ids only. No token value is
  logged.
- **Infra Brain 591, 592 and 593 (no token in tracked files, no token in git history, no inline
  bootstrap token):** the token-shape grep of the diff found nothing, and the known-bad control's
  shape is described in words.
- **Infra Brain 1032 (`cred.exposure-rotate`):** items 2, 7, 8 and 9. No revoke alert is sent
  before `verify`'s `loaded` match shows the broker holds the new value (R10). Revoke-first after a
  leak stays Devon's call. The row's exposure and consumer fields keep their form, and no exposure
  is declared.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. No endpoint is added, as "What it
  grants" says.
- **Construction-mode ruling** (`~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`):
  cited for item 5, which HQ settles under build-mode rule 2.

## PR

https://github.com/AlobarQuest/orchestrator/pull/816, opened against `main` and not merged. Its amendment commit is `c499c90d`; this report line is added in the commit after it, which is the PR head.
