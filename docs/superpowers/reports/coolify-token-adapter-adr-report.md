Outcome: done

# Report: ADR-0055 Amendment 33, a rotation adapter for `coolify-api-token` (W-314)

## What I built

I wrote `## Amendment 33 — A rotation adapter for Coolify API tokens: a person mints and revokes,
the executor proves (2026-11-24)`, status Proposed. It's at the end of
`docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`, after Amendment 32. I
checked `origin/main` (`7af40971`) first: Amendment 32 is the last heading, and no open pull request
adds an amendment to ADR-0055 (#814, batch 1, doesn't touch the ADR). So the number is 33. The ADR
header's amendment index ends at 28 and doesn't list 29 to 32, so I didn't add 33 to it.

The amendment covers the handoff's five tasks:

1. **The three rows**, read from `.cred-consumers.toml` at `7af40971`. The amendment tabulates each
   row's scopes, keeper project, consumers and fingerprint. Only the estate lander's row has a
   fingerprint, and it's an 8-hex `fingerprint_sha256_8`, not the executor's 16-character form.
2. **The provider's API.** The primary source is Coolify's source at tag `v4.0.0-beta.473`
   (`57ea0764`), the version `coolify_version` reports for production. `routes/api.php` has no
   token route (`/security/keys` is SSH keys, `/cloud-tokens` is cloud-provider tokens), and no API
   controller handles tokens. Tokens are created and revoked only in the Livewire component
   `app/Livewire/Security/ApiTokens.php`. The docs at the docs repository's head describe a later
   version, with roll, pause, expiry and resource-limited access. The amendment says so and relies
   on none of it. `coolify_search_docs` returned nothing for two queries, so I read the docs
   repository through `gh`. The shape is Bitbucket's: `Capabilities(machine_mint=False,
   machine_revoke=False)`, and `plans.derive` gives mint (person), store, verify, revoke (person),
   confirm.
3. **The adapter's proof.**
   - **Probe:** `GET /api/v1/version`, with a random token of the same shape as the known-bad
     control (401).
   - **Identity:** the team id from `GET /api/v1/teams/current`.
   - **Scope:** non-mutating requests that the ability middleware refuses with 403 before a handler
     that refuses without writing: `PATCH /projects/<random>` with an illegal field (422), and
     `GET /deploy` with no parameters (400).
   - **R10:** every consumer fetches the keeper fresh on each pass. The broker caches nothing
     (amendment 30 item 4, `secrets_broker.py` line 18). So the existing scheduled `runtime-fetch`
     proof meets R10: the keeper readback plus each row's Healthchecks run after the store.
     Amendment 29's loaded fingerprint doesn't apply.
   - **Revocation:** confirm-dead probes the old value itself, so no extra credential is needed.
   - **Carried gaps:** the estate lander's non-fatal read, and the two Mac paths in the rows' notes.
     The amendment names who verifies each.
4. **The executor's reach**, tabulated from `docs/operations/rotation-executor.md` line 677, decision
   4 and amendment 23. It writes `Rotation / Keepers`. It has nothing on `SDS Operator`. On
   `Ops / Platform` it has only the direct read of the Healthchecks Management key.
5. **What the code pull request changes** (Decision item 10): the adapter module, `ADAPTERS`,
   `types.py` (`TYPES` and `CLASS_TYPES`), `test_rotation_agreement.py`'s `UNTYPED`,
   `test_rotation_edges.py`, `OUTBOUND_ALLOWLIST`, `layers.toml`, `factory-policy.toml`'s two
   `rotation_adapters` lists, the rows (a declared-abilities field, the Mac notes and
   `rotated_by_sds`), intent-packages' `approval-policy.toml` and packages, and
   `rotation-executor.md`. I found these by grepping the sites that name `bitbucket` and
   `healthchecks-ping-url`.

The stop points are Decision items 11 to 15, each marked "Open, for Devon" in the ADR and in
`docs/superpowers/reports/coolify-token-adapter-adr-question.md`, each with options and a
recommendation:

- **11:** the keeper moves or grants for two rows. I recommend moving both keepers to
  `Rotation / Keepers`.
- **12:** the executor minting or revoking tokens itself. The API can't do it, and I recommend no.
- **13:** proving a new token lacks `read:sensitive`. I recommend a canary application.
- **14:** three throwaway measurement tokens on `dev-coolify`. I recommend yes.
- **15:** proving the estate lander's read. I recommend carrying the gap for now.

None of them blocks this docs pull request.

## Tests and checks

- `make check` in the worktree against `orchestrator_test_hq_w_314`: green, exit 0. Ruff check and format, and pyright, report no errors. Pytest: 14,788 collected, 14,787 passed and 1 skipped, in 399 seconds.
- Every line number the amendment cites at `7af40971` was read in this session. The Coolify
  citations were read from files fetched at the tag into a scratch directory.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: Amendment 33 appended
  (docs only).
- `docs/superpowers/reports/coolify-token-adapter-adr-report.md`: this report.
- `docs/superpowers/reports/coolify-token-adapter-adr-question.md`: the five open items.

Pull request: #815 (https://github.com/AlobarQuest/orchestrator/pull/815), opened against `main` and not merged.

## Self-review

- I made no mutating call. The calls I made were `coolify_version`, two `coolify_search_docs`
  queries, `gh api` reads of public Coolify repositories, local file reads, and `launchctl list`.
  I read no secret value, made no BWS call, and probed no live token.
- The scope probes are source readings, not measurements. The amendment labels them unmeasured and
  makes measuring them Open item 14.
- The `PATCH` and `GET /deploy` probes go to write-guarded and deploy-guarded routes. I read each
  handler to confirm that it refuses before it writes or deploys: `update_project` validates
  before its lookup, and `deploy` returns 400 with no `uuid` or `tag`. A Coolify upgrade could
  change that order, so the code pull request's measurement must repeat at the deployed version.

## Concerns

- **The Mac paths in two rows' notes.** The estate lander's note says "on the Mac with the narrow
  sds-operator identity", and the revision watcher's says "on the Mac with the broad identity".
  `launchctl list` shows no such job. Either the notes are stale, or something runs these
  launchers by hand. The code pull request has to settle this before either row becomes
  `rotated_by_sds`.
- **The executor's own Coolify token** (`Rotation / Coolify`) has no `.cred-consumers.toml` row.
  It's in the hand-rotated set by decision 4. I didn't change that, and the handoff didn't ask me
  to.
- **Moving a keeper with single-secret grants.** Amendment 1 question 5 measured that a project
  grant loses read on a move. Whether a direct secret grant survives a move isn't measured.
  Open item 11 A therefore proves each consumer's read after the grant, as decision 4 requires.
- **A concern the Standards section doesn't cover:** what makes a probe request non-mutating. No
  Code Brain or Infra Brain rule covers sending a request to a write-guarded route as a scope
  probe. The amendment rests that on the handlers' source.

## Standards followed

- **Code Brain 3 (logging):** the ADR prescribes no log lines. Decision item 9 says the adapter
  logs nothing of a value and files evidence through `contract.py`.
- **Code Brain 4 (error handling):** Decision item 9. A transport failure is indeterminate and fails
  the unit. An unexpected status fails `verify_new` with the status named, and `mint`, `host`,
  `consumer_loaded` and `revoke` raise `ContractError`. Nothing is swallowed.
- **Code Brain 5 (security):** evidence carries only fingerprints (the sha256 prefix and length).
  No token, header or body is logged. The amendment and this report contain no token value or
  literal example. The probes' shapes are described, not written out.
- **Infra Brain 591, 592 and 593 (BWS token handling):** no BWS or Coolify token appears in any file
  or commit. The token shape is described in words ("a numeric id, `|` and a random secret"). BWS
  UUIDs appear only as the registry already lists them, truncated.
- **Infra Brain 1032 (`cred.exposure-rotate`):** Decision item 5 makes the revoke wait for machine
  evidence that every scheduled reader ran after the store. Item 6 keeps "a leak revokes first"
  with `revoke_may_come_first`. Readers with no machine proof (the Mac paths) keep their rows out
  of the type until they're settled.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. No endpoint is added, and the
  adapter calls Coolify only at the executor's existing tailnet address. The amendment's "What it
  grants" section says so.
