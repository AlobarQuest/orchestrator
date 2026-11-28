Outcome: done

# Keepers move registry (G-116): build report

Worker W-340, branch `hq/keepers-move-registry`, base `origin/main` `d5793c2e`.

- PR: https://github.com/AlobarQuest/orchestrator/pull/836
- Head: `3061eeeb4c9b89c204e77aec979930abd35cb9bf` for the change; this report is the commit after it

## What I built

One PR that records HQ gate G-116 in the registries, the broker row's test, and the operations
pages. It changes no code, no `rotated_by_sds`, `rotate_requested`, class, provider, or consumer
kind, and no consumer set.

- `.cred-consumers.toml`, row `secrets-broker-bws-token`: `reach_secrets` gains `f9f59233-…`,
  `b8f547b9-…` and `314a966c-…`, and `reach_projects` is `SDS Operator` alone. The comment block
  states the reach as it stands: project-level for SDS Operator, with the four moved keepers in
  the `Rotation / Keepers` single-secret list, the `Ops / Platform` line removed, and a paragraph
  naming G-116.
- `.cred-consumers.toml`, the four keeper rows: each comment and keeper `note` says the keeper is
  in `Rotation / Keepers` and the broker reads it by a direct grant. The two Coolify rows say the
  root's read is unproven (no probe class) and comes before the flip. The App keys' block drops
  the "keepers' move" precondition and "whose account reads `SDS / Lane Apps`", and states the lane
  accounts' grants in the past tense.
- `.bws-secrets.toml`: `project = "Rotation / Keepers"` for all four, and each comment states its
  readers (the broker by a direct grant, the root by its project grant). I removed the "stays until
  the lane tokens are revoked" and `bump-proposer` / `rotation-proposer` account clauses. The Mac
  broad-identity sentence on `prod-coolify-read-token` was true only because the secret was in
  `Ops / Platform`. It now reads "Whether the Mac's broad identity reads it since the move is
  unmeasured." I also dropped the estate lander's "with the narrow `sds-operator` identity" clause:
  `run-estate-landing.sh` runs only in its container (it exits on the host, line 108), so no
  `sds-operator` read remains, and that account's project no longer holds the secret.
- `tests/rotation_worker/test_secrets_broker_row.py`: `PROJECTS` is SDS Operator alone, and
  `SECRETS` gains the three G-116 grants.
- `docs/operations/rotation-executor.md`: "The registry rows" for Coolify tokens and for GitHub
  App keys, and exit check 2's paragraph, state the move and drop "until Devon moves them".
- `docs/operations/deploy.md`: the estate-landing and bump-driver cut-over steps and the lane-App
  table header name `Rotation / Keepers` for the moved keepers.

"Move a keeper" (`rotation-executor.md`, line 762) lists no moved keepers and no carried item, so
I left it unchanged. The PR body carries the verify item about the root's read of the two Coolify
keepers.

## Changed line ranges (new file, from `git diff -U0 d5793c2e HEAD`)

- `.bws-secrets.toml`: 176-181, 185, 332-340, 344, 383-386, 390, 396-403, 407.
- `.cred-consumers.toml`: 627-630, 645, 659-663, 677, 1103, 1112-1113, 1133-1137, 1149-1150,
  1168-1172, 1180-1184, 1187, 1198-1200, 1220, 1323.
- `docs/operations/deploy.md`: 1068-1073, 1198-1200, 1450.
- `docs/operations/rotation-executor.md`: 1181-1186, 1259-1262, 1418-1419.
- `tests/rotation_worker/test_secrets_broker_row.py`: 35-36, 75-78.

## Reach counts

- `reach_secrets`: 31 before, 34 after (34 distinct; read with `tomllib` from the committed file).
- `reach_projects` after: `["b09acc9d-87af-4830-9361-b49700d2bc8a"]` (SDS Operator).

## Grep for `b3df5419` and `SDS / Lane Apps`

After the change, `git grep -n -E "b3df5419|SDS / Lane Apps"` matches only the following:

- `.cred-consumers.toml`, the App keys' block (line 1168): "stored each key … in BWS project
  `SDS / Lane Apps`" on 2026-11-09, followed by the G-116 move. It's history inside a live
  comment, kept so the move is legible.
- `.bws-secrets.toml`, lines 383 and 400: "Devon moved it from `SDS / Lane Apps`". Same reason.
- `.cred-consumers.toml:1136` (the broker row's G-116 paragraph) and
  `tests/rotation_worker/test_secrets_broker_row.py:35`: they record that G-116 emptied the
  project and removed the grant.
- ADRs (`docs/decisions/0055-…`, `docs/decisions/0059-…`) and reports under `docs/superpowers/reports/`:
  left as they are, as the handoff directs.
- Launcher comments, left because they are outside this PR's scope (registries, docs, registry
  tests). Each says the read App key is "in `SDS / Lane Apps`": `scripts/run-bump-driver.sh:89`,
  `scripts/run-bump-proposer.sh:149`, `scripts/run-estate-landing.sh:98`,
  `scripts/run-inert-landing.sh:100`, `scripts/run-work-carrier.sh:140`. Also
  `scripts/run-estate-landing.sh:93` says the Coolify token is "stored in `SDS Operator`, read with
  the narrow identity". See the follow-ups section.

The rest of `docs/operations/` and `lanes/secrets-broker/allowlist.toml` carry no statement of
these keepers' project or the broker's project reach. I checked with
`git grep -E "0bf066e9|f9f59233|prod-coolify-read-token|COOLIFY_TOKEN_SDS_ESTATE_LANDER|lanes-(read|push)-app-private-key"`
and a `reach_projects|reach_secrets` grep over `docs/operations`. The allowlist comments name
secrets, not projects, and `secrets-broker.md`'s "Add a lane" step 4 and "Rotate the broker's
token" are general.

## Tests and checks

- Registry subset against `orchestrator_test_hq_w_340`, before the commit:
  `pytest -n auto tests/rotation_worker tests/architecture tests/scripts`: 5507 passed.
- `make check` on the committed tree: green: lint and pyright clean, 15187 passed, 1 skipped (collected 15188), in 7:43.
- `code-standards risk --repo .`: `high-risk` (secrets: `.bws-secrets.toml`,
  `tests/rotation_worker/test_secrets_broker_row.py`).
- `vendor-review` (`/tmp/vendor-review-0tjulsvb`): 2 of 2 seats succeeded, Claude and Codex
  approve, 0 findings, 0 dropped; agy off by configuration.
- `/code-review` (low) on `d5793c2e..HEAD`: no findings.
- Token-shape grep of added lines (`git diff d5793c2e HEAD | grep '^+'` for the BWS token shape
  and for 40-plus-character base64 runs): no match. The diff adds UUIDs and names only.

## Self-review

- The new `SECRETS` and `PROJECTS` pins equal the row exactly, through
  `test_the_row_declares_the_granted_projects_and_secrets`, so the test would fail on a missing or
  extra id.
- No consumer set changed: every `[[credential.consumer]]` keeps its kind, UUID and ref; only
  `note` text changed on the four keepers.
- The `lanes` lists in `.bws-secrets.toml` and the allowlist are unchanged; the broker reads the
  same UUIDs it did.

## Concerns

None that block. This change adds no code, so no Code Brain concern beyond the handoff's
standards applies.

## Follow-ups

1. Verify item (also in the PR body): the root's read of the two Coolify keepers (`0bf066e9-…`,
   `f9f59233-…`) in `Rotation / Keepers` is unproven, because no probe class covers a Coolify
   token. That proof comes before either row's `rotated_by_sds` flip.
2. Update the six launcher comments listed earlier in a PR whose scope includes `scripts/`; they
   are comments only, but stale.
3. Whether the Mac's broad identity still reads `prod-coolify-read-token` after the move is
   unmeasured; nothing on the Mac is recorded as reading it.

## Standards followed

- **Code Brain 3 (logging):** not applicable. The change adds no code.
- **Code Brain 4 (error handling):** not applicable. The change adds no code.
- **Code Brain 5 (security):** the diff holds UUIDs and names only, no value or slice of one; the
  token-shape grep of added lines found nothing.
- **Infra Brain 591, 592, 593 (`bws.no-token-in-tracked-files`, `bws.no-token-in-git-history`,
  `bws.bootstrap-token-not-inline`):** no token or token-shaped literal in the diff, by the same
  grep; no env file changed.
- **Infra Brain 1032 (`cred.exposure-rotate`):** this PR adds or closes no `[[credential.exposure]]`
  entry and changes no consumer set.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. No endpoint is added or changed.
