Outcome: done

# Report: lanes-inc-2-adr-0055-amendment

Worker W-36. PR: https://github.com/AlobarQuest/orchestrator/pull/626 (branch
`hq/lanes-inc-2-adr-0055-amendment`, docs only, status Proposed). The amendment is **number 19**:
amendment 18 is the last at `origin/main` `8875e1bf`, which I checked on 2026-11-07.

## What I built

I appended "Amendment 19 — The VM executor issues lane tokens and ping URLs from its inbox
(2026-11-07)" to `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`, and
added it to the front-matter list. The amendment covers the handoff's five goals:

1. **Decision 4's lift.** It lifts decision 4 for lane BWS tokens and lane ping URLs, on the VM
   instance only, from increment 2 (decision item 1).
2. **The executor extension.** The amendment adds these parts:
   - Two classes and types, `bws-lane-token` and `healthchecks-ping-url`, with store kind
     `bws-secret`, so the VM instance claims them (item 2).
   - An issue plan with no `retire_old` or `confirm_dead`, validated by its own rule. It runs only
     when the keeper holds the inbox's `EMPTY` marker, which a sibling of `adopt-keeper` writes
     (item 3).
   - A lane-token bind by declared BWS reach (the `_REACH` digest against the lane's
     `.bws-secrets.toml` UUIDs), in place of `same_identity`. The same bind serves a reissue after
     a leak's early revoke (item 4).
   - A ping-URL bind by shape and check read, never by a ping (item 5).
3. **The order.** The order is a mechanism: the ping URL's row becomes due only after the token's
   `store` unit completes, so `other_mints` never sees both waiting (item 8, with the four-step
   cut-over).
4. **The verify items.** Each code-read verify item is settled with citations ("What the code
   settles", and the following section of this report).
5. **No mint, no revoke.** The executor still can't mint or revoke. Lane tokens are
   `rotated_by_sds` from their first issue, but no scheduled rotation runs before increment 7
   (item 9).

## Verify items: findings and citations

Citations are at orchestrator `8875e1bf`, Coolify `v4.0.0-beta.473` (`57ea0764`, read from GitHub),
and infraops-mcp-server `main` `9cdaab1f` (read from GitHub; not edited).

1. **The Coolify client only PATCHes, and who creates the `<LANE>_BWS_ACCESS_TOKEN` row.**
   - The executor's client allows only `PATCH /api/v1/services/{uuid}/envs` and
     `POST /api/v1/services/{uuid}/restart` (`src/rotation_worker/coolify.py`, lines 63-65 and
     82-83). A service write is one PATCH (lines 85-91).
   - Coolify's PATCH answers 404 for a missing key (`app/Http/Controllers/Api/ServicesController.php`,
     lines 1238-1241), which the client raises as `CoolifyRefused` (`coolify.py`, lines 104-105).
   - **The compose parse at create creates the row.** The create saves the raw compose and calls
     `parse(isNew: true)` (`ServicesController.php`, lines 616 and 627). `serviceParser` starts at
     `bootstrap/helpers/parsers.php`, line 1504, and for a `${VAR}` with no default it runs
     `firstOrCreate`, making the row with no value (lines 2447-2459). `firstOrCreate` never
     overwrites a value the executor later writes.
   - I didn't list any lane resource's variables. The production confirmation is the first lane's
     create, because the placeholder's compose maps no token
     (`lanes/placeholder/compose.yaml`). The amendment lists it under "Open before building". A 404
     there fails the unit; it doesn't skip.
2. **`postgres_role` requires `redeploy` true and restarts.**
   - `hosted_destination` refuses unless `redeploy is True`
     (`src/rotation_worker/adapters/postgres_role.py`, lines 169-182).
   - `host` calls `service.restart()` at line 316 (and again at 377 in `_restore`), which is
     `POST /services/{uuid}/restart` (`coolify.py`, lines 65 and 96-97).
   - `redeploy` is identity in the destination string (`src/rotation_worker/destinations.py`, lines
     17-19, 38 and 50), so a lane row's `false` is approved text.
   - The amendment's item 6 refuses `redeploy` true for a lane destination and never calls
     `restart()`.
3. **infraops has no class for a ping URL.**
   - `CLASS_POLICY` (`src/security-drift/cred-rotation.ts`, line 31) has `bws-machine-token` (lines
     88-93) and no Healthchecks class. An unknown class raises `cred.unknown-class` (lines
     242-253).
   - The amendment names two infraops classes: `healthchecks-ping-url` (`maxAgeDays` infinite, with
     a "recreate the check, not rotate" landmine) and `bws-lane-token` (365 days).
   - It also needs `bws-lane-token`, because `CLASS_TYPES` maps `bws-machine-token` to the
     Keychain-only type (`src/rotation_worker/types.py`, lines 49-57;
     `src/rotation_worker/envelope.py`, line 13).
   - The infraops change must land first: `tests/architecture/test_rotation_agreement.py`, lines
     55-56, holds `CLASS_TYPES` to infraops's classes.
4. **Early and leak reissue actor, and the revoke order: design choices, proposed only.** The
   amendment's item 10 makes these proposals, and the PR body lists them under "Questions for
   Devon":
   - **Actor:** the rotation proposer, from an exposure or `rotate_requested` that HQ records on the
     lane token's registry row. Devon's one click is the revision approval.
   - **Leak order:** Devon revokes first, as decision 6's exposure response. The reissue's
     `retire_old` then finds the token refused, and the reach-based bind needs no hand staging.
   - **Non-leak order:** Devon revokes after `lanes-finish` and `lane-run --read` pass on the
     redeployed container.

Out of this PR, and listed as open in the amendment, are these Healthchecks reads: the flips
endpoint, the ping-log limit, whether `/pings/` needs the Management key, and which read ties a
pasted ping URL to its check.

## Tests and checks run

- `pytest tests/architecture/test_layer_manifest.py tests/architecture/test_rotation_agreement.py`
  against `orchestrator_test_hq_w_36`: 8 passed.
- Security scanner (`security_scan.cli . --category security`) from the repo root: no BLOCK
  findings; two WARN and one INFO, all unrelated to this diff.
- A grep of the diff for the BWS token's prefix shape found nothing.
- I didn't run `make check` or `/code-review`, because the change is docs only and touches no
  logic, interface or configuration (repo CLAUDE.md lets trivial changes skip `/code-review`; the
  full `make check` exercises code this PR doesn't change).

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (amendment 19 and the
  front-matter line).
- `docs/superpowers/reports/lanes-inc-2-adr-0055-amendment-report.md` (this report).

## Self-review

- Every code claim carries a file and line I read at the stated commit. The Coolify and infraops
  reads came from GitHub at the named refs. I made no Coolify, BWS, VPS or Healthchecks call.
- I checked `contract.validate_plan` (`contract.py`, lines 207-252) before proposing an issue plan.
  It requires `retire_old` and `confirm_dead`, so the amendment adds a separate validation rule and
  doesn't loosen the rotation one.
- I checked that a check-ping read since the keeper's revision doesn't prove a lane loaded a new
  token: a pass between the write and the redeploy pings on the old token. The amendment rejects
  that read and keeps the in-container proof with the lane phases.

## Concerns

- **The keeper at rest is a threat-model widening.** It's proposed, and it's PR question 3.
  ADR-0059 accepted transit of lane tokens through the executor. A keeper in `Rotation / Keepers`
  also puts every lane token at rest within the root's reach. A reissue's quarantine and increment
  7's proofs need it, but Devon hasn't decided it.
- **Reach-based binding is new mechanism.** It replaces `same_identity` for one type. It depends
  on each lane's declared secret set being distinct, which the per-lane ping-URL secret provides,
  and on the registry row listing that set. The build has to keep that list equal to
  `.bws-secrets.toml`.
- **Unconfirmed infraops behavior.** I didn't confirm whether infraops raises
  `cred.rotation-requested` for a row with a `rotate_requested` date and no recorded rotation. The
  amendment's first-issue trigger depends on it, and the amendment lists it as open.

## Standards followed

- **Code Brain rule 5 (never log secrets):** amendment item 11 says each bind and refusal logs
  only the unit, the secret UUID, the destination string and the fingerprint, never the value. It
  also notes that the client keeps only the PATCH's status, because Coolify's response returns the
  written row (`coolify.py`, lines 99-108; `ServicesController.php`, line 1258).
- **Code Brain rule 4 (no silent swallow):** item 11 says a value that doesn't bind stays in the
  inbox with a reason, and any failure after the claim fails the unit and is recorded, never
  skipped. The "token row at create" open item says a 404 fails with `CoolifyRefused`.
- **Code Brain rule 3 (structured JSON logs):** item 11 names `rotation_worker/logs.py`'s
  structured JSON on stdout as the bind's log path for the later code.
- **Infra Brain 591, 592 and 593 (no token in tracked files, history or inline):** the amendment
  describes token shapes and names secrets by role or truncated UUID only. The diff grep and the
  scanner found no token.
- **Infra Brain 1032 (`cred.exposure-rotate`):** item 10's leak path ends in Devon's revoke and a
  reissue, not a purge.
- **Infra Brain 212 (HTTPS):** doesn't apply; no endpoint changes.
