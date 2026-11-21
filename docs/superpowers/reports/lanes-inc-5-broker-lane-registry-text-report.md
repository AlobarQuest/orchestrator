Outcome: done

# Report: registry and docs text for the three broker lanes

Worker W-253. Branch `hq/lanes-inc-5-broker-lane-registry-text`, base `origin/main` at `d1ea67f8`
(the handoff's facts were confirmed at that base). PR #768, ready for review, not merged. The head
is the commit that adds this report, one commit after `178656b3`; read it from the PR.

## What I built

The changes follow the handoff's item numbers. Line numbers are on the branch head, taken from
`git diff -U0 origin/main...HEAD`.

1. **The estate token's container reader** (#765 follow-up 3). `.cred-consumers.toml` line 680,
   in `coolify-estate-lander-token`.
   - Preconditions checked first. `scripts/run-estate-landing.sh` reads the token in the container
     inside the `--submit` case (line 234), at lines 241-246, and the read isn't fatal. The id
     `f9f59233-…` is in `lanes/secrets-broker/allowlist.toml` `[lanes.estate-landing]` (line 28),
     at line 34.
   - Deviation from the handoff's shape. The handoff asked for a second `runtime-fetch` consumer
     with the same `repo` and `ref` as the existing Mac reader. That would make this the only row
     in the registry that repeats a destination. I checked every row, and none other does.
     `rotation_worker.readers.scheduled_reads` keys readers by the rendered destination
     (`repo`, `ref`), so a second consumer silently overwrites the first, and
     `destinations.same` counts a repeat. Every other row with both a Mac and a container reader
     keeps them in one consumer, for example `coolify-revision-watcher-read-token`. So I folded
     the container reader into the existing consumer. Its note now carries the handoff's facts:
     `--submit` only, not fatal if unreadable (re-points and restores refused by name),
     `ESTATE_LANDING_COOLIFY_TOKEN`, through the lane secrets broker in the
     `sds-lane-estate-landing` container, on the Mac with `sds-operator`, and pings
     `sds-estate-landing` every pass. The Mac branch (lines 325-330) is also not fatal, so "not
     fatal" covers both readers. If HQ wants the two-consumer shape anyway, it's a revert of
     commit `178656b3`.
   - Test: `test_the_coolify_token_row_lists_the_lane_s_container_reader` in
     `tests/rotation_worker/test_estate_landing_rows.py` (lines 135-155). It requires exactly one
     `runtime-fetch` reader for the launcher, with "secrets broker" and `--submit` in its note and
     `sds-estate-landing` as its check, and it requires the row's `bws_uuid` in the lane's
     allowlist entry.
2. **The inert token block** (#766 follow-up 1). `.cred-consumers.toml` lines 1354-1357. The block
   no longer says grants "are a later act". It says the lane reads its secrets through the broker,
   whose allowlist entry lists them, not with this account's token. This matches the inert ping
   keeper note that #766 fixed.
3. **Ping-URL keeper notes** (#766 follow-up 2, narrowed): no change. No `bump-driver-ping-url` row
   exists. The 12 "the lane reads it with its own token" notes (lines 788-1348) belong to
   execution-watch, landing-ledger, deploy-watcher, revision-watcher, unit-verifier, pin-watcher,
   membership-check, change-proposer, rotation-decomposer, work-carrier, bump-proposer, and
   rotation-proposer. Each of these lanes still maps a token in its compose, so I left them alone.
4. **The bump driver's chain read password reader** (#764 follow-up 3). `.cred-consumers.toml`
   line 1677. The note says the lane reads through the broker. It also says ADR-0059 Amendment 17
   names `lane-secrets-broker` as the holder of the single-secret read grant that Amendment 13
   decision item 7 authorizes, and that the grant is Devon's act at the bump driver's cut-over
   gate. The note doesn't claim the grant is already given.
5. **`.bws-secrets.toml` comments** (#762 follow-up 2, #764 follow-up 3). I found every hit by
   grepping for "own token", "lane account", and
   `(inert-landing|estate-landing|bump-driver)[^.]*account`:
   - Lines 22-24, `ORCHESTRATOR_M2M_CREDENTIALS`: the three lanes read through the broker. The
     rotation decomposer, a token lane, keeps "own token".
   - Lines 192-195, the change-manager bearers: the three lanes read through the broker. The bump
     proposer, membership check, and rotation decomposer keep "own token".
   - Lines 259-261, the Brain contributor keys: the bump driver reads through the broker, and
     Amendment 17's holder is named as Devon's act at the gate.
   - Lines 298-300 and 302, the `sds-bridge` sender: the bump driver, estate lander, and inert
     lander read through the broker. Other lanes are unchanged.
   - Line 324, the estate Coolify token: reads through the broker.
   - Lines 361 and 364-368, the read-only lane App key: the three launchers are listed under the
     broker, and the others keep "own token".
   - Lines 373-374, the same block: the three lane accounts' grant sentences are replaced by "read
     through the broker, whose account `lane-secrets-broker` reads `SDS / Lane Apps`". The broker
     token row's comment (`.cred-consumers.toml`, "Its reach is project-level for SDS Operator and
     SDS / Lane Apps") supports this.
   - Lines 540-541 and 543-544, the inert ping-URL secret: reads through the broker's socket, in
     the same wording as the estate ping-URL block.
   - Lines 587-591, the chain's read password: the bump driver reads through the broker, and
     Amendment 17's holder is named as in item 4.

   I left these hits alone because they name only token lanes: lines 24-26 (follow-up-mint), 58
   (unit-verifier), 75 (work-carrier), 129 (the observer's readers), 168 (revision-watcher), 198
   (the PROPOSE readers), 217, 248, 295-305 (the other lanes' parts), 343-533 (other lanes' ping
   URLs), 377 (the write App key: bump-proposer and rotation-proposer), 565, and 584 (work-carrier).
6. **`docs/operations/credentials.md`**. Line 98 is about follow-up-mint, a token lane, so I left
   it alone.
   - Lines 414-416, inert: reads through the broker's socket, with no token and no Keychain item.
   - Lines 421-424, estate: the same.
   - Line 426 and lines 432-437, the bump driver. The paragraph said it "holds no chain password
     either", but the container reads `factory-events-read-password` every pass
     (`scripts/run-bump-driver.sh` lines 97 and 187-189). It now says the bump driver holds no
     chain password on the Mac, and that the container reads the read password through the broker
     for the `orchestrator.factory_chain_read` walk alone. It also names Amendment 17's holder for
     that password and both contributor keys, as Devon's acts at the gate.
7. **`docs/operations/secrets-broker.md` "Add a lane" step 4** (#764 follow-up 5, optional).
   Lines 69-73. The step adds "or holds a single-secret read grant on it", with Amendment 17's case
   (the bump driver's three secrets in `Factory Events` and `Brains - Contributor`, one
   single-secret grant each to `lane-secrets-broker`, each Devon's act at the gate). It states no
   general rule for other lanes, because Amendment 17 decides only these three.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_253`, run on the final tree: exit 0. 14,850
  collected, 14,849 passed, 1 skipped, and pyright reported 0 errors. I stopped an earlier run
  partway through, because I edited the registry while it ran.
- Mutation proof for the new test, run on scratch edits and then restored, with
  `__pycache__` cleared:
  - Broker reader removed from the note: the test fails with `AssertionError: assert 'secrets
    broker' in ...`.
  - A second consumer added for the same launcher: the test fails with `ValueError: too many
    values to unpack (expected 1, got 2)`.
  - Restored tree: the file's 10 tests pass.
  - An earlier mutation of the two-consumer shape (reader removed) also failed with `ValueError:
    not enough values to unpack`.
- Security scan from the repo root: exit 0, 0 BLOCK, 2 WARN. Neither WARN comes from this diff:
  `bws.secret-files-gitignored` (glob `*-migration/`) and `bws.manifest-matches-usage` (undeclared
  UUID `aaff45ca-…`, which this diff doesn't add).
- `code-standards risk`: `high-risk`, matching `secrets: .bws-secrets.toml`. So I ran the vendor
  review as well:
  - Directory `/tmp/vendor-review-szp0j5_d`, on the final diff.
  - Seats: Claude and Codex both finished. agy is off by configuration.
  - Findings: 0 kept, 0 dropped.
- `/code-review` low on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `.bws-secrets.toml`
- `docs/operations/credentials.md`
- `docs/operations/secrets-broker.md`
- `tests/rotation_worker/test_estate_landing_rows.py`
- `docs/superpowers/reports/lanes-inc-5-broker-lane-registry-text-report.md` (this report)

## Self-review

- Every "through the broker" claim matches the code:
  - `lanes/secrets-broker/allowlist.toml` lists each secret for its lane.
  - None of the three composes maps `BWS_ACCESS_TOKEN`. Inert's compose names its old variable
    only in a comment.
- Amendment 17 calls each grant Devon's act, which he makes or withholds at the gate. Every
  mention says the amendment names the holder, not that the grant exists.
- Wording for other lanes is unchanged, except where a sentence covered both kinds of lane. There
  I split it so each token lane keeps "own token".

## Concerns

- I deviated from the handoff's two-consumer shape for item 1. The reason is in item 1. The
  revert is one commit if HQ disagrees.
- Outside the scope of this handoff: the inert token row (`inert-landing-bws-token`) still has a
  `coolify-env` consumer for `INERT_LANDING_BWS_ACCESS_TOKEN` (line 1382). Its `reach_secrets`
  comment says "What the lane's account sees, exactly". The inert compose maps that variable to
  nothing; its comment calls it a fallback. Whether that row and consumer stay is a decision for
  HQ, and I didn't change them.
- No logging changes. No secret value, ping URL, or token appeared in any output I read.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging changes; the diff is registry
  text, docs, and one test.
- **Code Brain rule 4 (never swallow an exception silently):** the new test catches nothing. Its
  failures surface as `ValueError` or `AssertionError`, as the mutation runs show.
- **Code Brain rule 5 (never log secrets):** the test and its output print only registry notes and
  structure. The test reads `bws_uuid`, a BWS secret id, and no secret value.
- **Infra Brain 591, 592, 593:** no BWS token, inline `BWS_ACCESS_TOKEN` value, or token-shaped
  placeholder anywhere in the diff. The security scan shows 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** no `[[credential.exposure]]` declared, and I saw no
  secret value.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; no domain change.
- **Infra Brain 2890 (never commit a ping URL):** no ping URL in the diff. Ping-URL rows are named
  only by registry id and BWS id.
