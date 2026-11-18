Outcome: done

# Report: adr-0055-a29-executor-on-vps

- **Worker:** W-217, for SDS HQ. Handoff `state/handoffs/adr-0055-a29-executor-on-vps.md`.
- **PR:** #742 (ready for review, not merged).
- **Base:** `ad721fc0509de725f808059f6ce2878b9f613e26`.
- **Content commits:** `4737d5f859c37f22bf795da9a772a1b68ef867fe` (the amendment), then
  `9d8a40a8f110b97fce79877ebab9ec53e6cc75e1` (review fixes, and the answered question file removed).
  The head is this report's commit, which follows them.
- **Amendment's line range:** lines 3999-4288 of
  `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`. The diff from the base
  is 291 insertions and no deletions.

## What I built

ADR-0055 Amendment 29, appended after Amendment 28. It quotes D-63, D-64 and D-65 in full, with
their sds-hq commits (`902da11` for D-63 and D-64, `7e43bc8` for D-65), and notes that D-64 B is
the development-phase choice. Its sections:

1. **Placement (D-64 B):** a systemd service and timer under host user `rotation` on the VPS host.
   The user has no sudo and no `docker` group. The amendment lists what moves and what doesn't. It
   reaches the helper through the same key and forced command, at the host's own tailnet address.
2. **Decision 2 and T1:** rewritten for the VPS host. The amendment names who reaches the root
   token's file:
   - Coolify, whose `localhost` server logs in as `root` with its terminal enabled (read with
     `coolify_get_server`).
   - Every holder of infraops `vps_exec` (`ssh-client.ts` lines 30-33).
   - Any other root login.

   Devon accepts that residual (D-64 B, D-65 option 2). Narrowing each root path is a cut-over
   prerequisite and a later decision of Devon's. The amendment also says that no brain rule covers
   host-user isolation.
3. **Lane-token restart and R10 (D-63 C):**
   - The session keeps `--token`, and the executor still never redeploys a lane.
   - A reissue's `verify` unit waits unclaimed until the helper's `loaded` fingerprint of the lane
     container's `BWS_ACCESS_TOKEN` equals the new keeper's. The claimed step reads it again.
   - An issue keeps the `PATCH` proof.
   - The amendment names three code changes without making them: a consumers-only helper target,
     one target per lane, and the lane-token consumer check calling the helper.
   - The narrowed 02:00-06:00 window only delays the claim to the next night. No wait sits inside
     a claim.
   - This restores R10 for lane tokens and ends D-27's departure at the cut-over.
4. **The cut-over:** W-215 Q6 steps 1-9. The root token at the move is left open for Devon's mint
   gate.
5. **The exit:** Q6 step 10's checks under ADR-0059 Amendment 6 item 4, including one lane token's
   `loaded` fingerprint equal to its keeper's.
6. **Verify items, authority, consequences, and the governed-passages table.** The handoff's five
   verify items each name a step. "What it grants" names no new grant.

## The table's rows (line numbers at `ad721fc0`)

| Passage | Disposition |
|---|---|
| ADR-0055 decision 2 (133-143) | Amended |
| ADR-0055 decision 2 accepted residual (145-149) | Superseded by the new T1 |
| ADR-0055 decision 3 R10 (160-161) | Stands, restored for lane tokens |
| ADR-0055 decision 9 (298-302) | Stands |
| ADR-0055 asset table, root row (338) | Amended |
| ADR-0055 T1 (350) | Amended |
| Amendment 1 T1 widening (627-629) | Stands |
| Amendment 2 destination-list residual (721) | Stands |
| Amendment 12 item 7 (1495-1500) | Stands, with the VM's isolation premise ending at the cut-over |
| Amendment 17 item 2 (2117-2127) | Stands, plus a consumers-only target |
| Amendment 17 reach and key (2141-2142, 2152-2155) | Stand |
| Amendment 19 item 1 (2240), and "the VM instance" generally | Read as the BWS-store instance |
| Amendment 19 item 3 departure (2284-2285) | Ends at the cut-over |
| Amendment 19 item 6 (2324-2352) | Stands |
| Amendment 19 item 7 (2353-2376) | Proof becomes `loaded`; departure ends |
| Amendment 19 item 10 (2403-2422) | The leak order stands; otherwise the revoke follows `verify` |
| ADR-0059 decision 4 (763-766) | Governed |
| ADR-0059 increment 7 (1050-1070) | Governed |
| ADR-0059 Dependencies (1420-1426) | Governed |
| ADR-0059 Amendment 2 (1782-1787) | Governed |
| ADR-0059 Amendment 6 item 4 (2694-2696) | Governed |
| ADR-0059 Amendment 6 item 6 (2709-2710) | Stands |

## Tests and checks run

- `make check` with `orchestrator_test_hq_w_217`, migrated first, on the final content: exit 0.
  15607 passed and 1 skipped, so 15608 tests were collected. Lint and type-check reported 0 errors.
- Security scan from the repo root: 0 BLOCK and 2 WARN. Both WARNs come from files already at the
  base: `.gitignore` glob coverage, and a UUID in earlier reports. This branch adds no UUID.
- Sources re-read at the base:
  - ADR-0055 at every cited line.
  - ADR-0059 at every cited line.
  - `deploy/vps/sds-pg-rotate`.
  - In `rotation_worker`: `coolify.py`, `adapters/bws_lane_token.py`, `helper_client.py`,
    `values.py`, `plans.py` (`derive` and `derive_issue`), `destinations.py`, and
    `adapters/postgres_role.py`.
  - `rotation_claims.py`.
  - `factory-policy.toml` lines 495-512.
  - `docs/operations/rotation-executor.md` at the cited lines.
  - The infraops `ssh-client.ts`, `vps.ts` and `start.sh`.
  - Coolify's prod server record, read with `coolify_get_server`, a read-only call.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (append only).
- `docs/superpowers/reports/adr-0055-a29-executor-on-vps-question.md`: I added it in `9d005adc`
  and removed it in `9d8a40a8` after HQ answered (D-65).
- This report.

## Self-review

- The ADR diff is pure insertion. It edits no earlier text and leaves the header list alone.
- Every fact is cited at the base or marked as not tested. The one untested statement is that a
  Coolify API token with `write` or `deploy` can probably mount a host path. It follows the ADR's
  own wording at lines 341 and 623-625.
- An Opus advisor review caught three problems, now fixed:
  - The first draft applied the `loaded` check to an issue, which runs `verify_consumers` before
    any restart.
  - The table was missing rows for lines 721 and 1495-1500.
  - D-65 was missing from "What it grants".
- `helper_target` on the lane row follows the `postgres-role` row's precedent. It isn't a
  destination identity field, so it doesn't change an approved destination.

## Concerns

- **HQ's answered question file.** `docs/superpowers/reports/adr-0055-a29-executor-on-vps-question-answered.md`
  is untracked in this worktree. I didn't create it and didn't commit it.
- **T3 on a shared host:** not in scope, and not a verify item. W-215 Q3 noted that T3's
  "churn, not exposure" holds only while the orchestrator container has no route to host root, for
  example no Docker socket mount. Nobody has read the orchestrator's compose for that. HQ may want
  it checked at increment 7's code step.
- **The cut-over waits on D-65's later decisions:** infraops `vps_exec` and Coolify's server user
  off root. Each needs its own question to Devon before increment 7's cut-over.
- **Standards gap:** no Code Brain or Infra Brain rule covers where a root credential rests on a
  host, or host-user isolation. The amendment says so and relies on the ADR's threat model, D-64
  and D-65.

## Follow-ups outside the ADR (listed in the PR body)

- ADR-0055's header "Amended" list stops at amendment 24.
- `docs/operations/rotation-executor.md`'s VM procedures: install, `set-root-token`, the helper key
  and host pin, and the one-pass command.
- ADR-0059's increment 7 text and Amendment 2 still name `lane-run --read` as the revoke's read.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** doesn't apply, because this change is docs
  only.
- **Code Brain 4 (never silently swallow an exception):** doesn't apply, because this change is
  docs only. The design follows it: the revoke wait fails closed. An unreadable `loaded` keeps the
  unit waiting, and an in-claim mismatch fails the unit.
- **Code Brain 5 (never log secrets):** the fingerprint is always described as a sha256 prefix and
  a length, never the value. The amendment names no secret value and no UUID.
- **Infra Brain 2, 591, 592 and 593 (no secret, `.env` value or BWS token in tracked files or
  history; no inline `BWS_ACCESS_TOKEN` value):** the diff holds no token or token-shaped string.
  `BWS_ACCESS_TOKEN` appears only as a variable name. The scan shows 0 BLOCK.
- **Infra Brain 1032 (`cred.exposure-rotate`):** item 3 restores the fail-safe of no revoke without
  attested consumers, using machine evidence: the revoke alert waits for `verify`'s `loaded`
  match. A leak still revokes first and ends in reissue (D-26).
- **Infra Brain 598 (`bws.least-privilege-scope`):** the root identity keeps exactly its scope.
  "What it grants" adds no identity, project, grant or scope.
- **Infra Brain 3035 (`bws.secret-holds-value-only`):** the root token's file holds the token and
  nothing else (item 1).
- **Infra Brain 595 (reference secrets by UUID):** the amendment names secrets by their project
  and role, and adds no new reference.
- **Infra Brain 1031 (rotation age):** unchanged, as "What it grants" states.
- **Infra Brain 1, 3, 4, 5, 6, 211 and 212 (Coolify builds, image tags, sidecars, webhooks,
  migrations, branch names and HTTPS):** don't apply. The executor runs outside Coolify and Docker,
  as the Consequences state. Its Coolify calls go to the tailnet address under the client's
  TLS-or-tailnet rule.
