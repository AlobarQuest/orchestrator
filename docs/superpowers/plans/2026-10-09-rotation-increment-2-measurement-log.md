# Rotation increment 2 measurement log (working notes for ADR-0055 amendment 1)

## Question 7: root on an isolated OrbStack machine (measured 2026-10-09T13:12:39Z)

- OrbStack 2.2.3. Machine `probe-inc2-root` created with `orb create --isolated --isolate-network ubuntu:noble`.
- `orb -m probe-inc2-root -u root id -u`, stdin closed, 30 s timeout: rc 0, prints `0`; `whoami` prints `root`. No prompt.
- Control, same command as the default user: rc 0, prints `501`.
- Isolation holds the other way: `orb -m probe-inc2-root mac uname -s` fails (`dial: no such file or directory`), and `/Users/devon/Projects` doesn't exist inside the machine.
- Answer: **yes, Devon's user gets root in an isolated machine with no password.** Decision 2's residual-risk statement stands as written.
- Machine deleted; `orb list` shows no `probe-inc2` row.

## Question 1: Coolify versions and source (read 2026-10-09)

- Production `4.0.0-beta.473`; dev `4.0.0-beta.470` (`coolify_version`). They differ, so the dev measurement is a hypothesis for production.
- Between the two tags, `routes/api.php`, `app/Http/Middleware/ApiAbility.php` and `ApiSensitiveData.php` are byte-identical. `ApplicationsController.php` changed in 74 lines, none touching abilities, sensitive data, envs or restart.
- Source at beta.473: env GET needs `read`; env POST, PATCH, bulk PATCH and DELETE need `write`; start, restart and stop need `deploy`. Abilities don't imply each other (`root` bypasses all). A missing ability answers 403 `Missing required permissions`. Without sensitive access, the envs response omits `value` and `real_value` entirely (`removeSensitiveData`).
- Source reading is the expectation, not the answer; the probe follows.

## Question 6: throwaway repository

- `AlobarQuest/probe-inc2-secrets` created private with `gh repo create`. The session's `gh` login lacks `delete_repo`, so Devon deletes it in the UI at cleanup.

## Question 1: Coolify token scope (measured 2026-10-09T15:22:40Z, dev beta.470)

- Three dev tokens: candidate `read`+`write`+`deploy`; negative control `read`; positive control
  `read`+`read:sensitive`. All three authenticate (`GET /version` 200); a bogus token gets 401
  `Unauthenticated.` (The first save of the positive-control token was wrong and got 401; re-saved,
  then 200.)
- Throwaway project `probe-inc2` (`v6t7n4ymf2wfmde423r7wtk7`) and dockerimage app `probe-inc2-app`
  (`ctrwss4senh28cpoyzug20ny`, `traefik/whoami`) created and started with the candidate token.
  `write` was enough to create both.
- **Env write.** `POST /applications/{uuid}/envs`: candidate 201; `read` and `read`+`read:sensitive`
  both 403 `Missing required permissions: write`. `PATCH /envs`: candidate 201 (no 500 in this run);
  both controls 403. The fallback was not needed and so was not measured.
- **Two rows per key.** A POST creates the variable twice, `is_preview` false and true. A PATCH
  without `is_preview` changes only the non-preview row; the preview row kept the old value until a
  second PATCH with `is_preview: true` (201). A rotation that patches once leaves the retired value
  stored in the preview row.
- **Readback.** With the positive-control token, both `value` and `real_value` hash to the written
  value. The candidate token can't read it back (next item), so verifying a write through the API
  needs a second token with `read:sensitive`.
- **Masking.** With the candidate and with `read` only, the env rows carry no `value` and no
  `real_value` field at all (field list otherwise identical). With the positive control both fields
  are present and unmasked. Matches the source reading.
- **Restart.** `POST /applications/{uuid}/restart`: candidate 200 `Restart request queued.`, the
  deployment `finished`; both controls 403 `Missing required permissions: deploy`. The new container
  (started 15:22:45Z) holds the patched value (sha256 prefix `c08e4dda` inside the container, read
  through `ssh orb sudo -n docker inspect`, hashed on the VM). The variable was created after the
  first start, so only the restart can have put it there.
- Answer: **`read`+`write`+`deploy` without `read:sensitive` can write, restart and apply a value it
  can never read.** Production runs beta.473 with byte-identical permission code; confirm on
  production in the increment that first uses the token.

## Questions 2 and 3: OpenRouter keys (measured 2026-10-09T15:35:31Z to 15:36:50Z)

- Devon created management key `probe-inc2-mgmt` in the console, which gave it a **one-hour expiry**.
  So a management key can carry an expiry (console observation).
- The probe refused any PATCH or DELETE on a hash other than its own create response's.
- Control: a malformed key (`sk-or-v1-` + 64 zeros) gets 401 `User not found.` on `/key`,
  `/auth/key` and a chat completion, at the start and at the end.
- **Expiry on a minted key.** `POST /api/v1/keys` with `expires_at` as `…+00:00` returns 400
  `Invalid request field: expires_at`. The same field as `2026-10-09T17:36:18Z` returns 201 and the
  stored `expires_at` matches. Without it, `expires_at` is null. `limit: 0.01` is accepted.
- **Key-info identifier.** `GET /api/v1/key` with the minted key's value returns 200 with 23 fields
  and **no `hash`**; no field equals the create response's hash. But the hash is **sha256 of the key
  value** (checked on a second throwaway key), so revoke needs only the value. A list of keys
  matched on `label` also finds exactly that hash.
- **`/auth/key` vs `/key`:** same status, same fields, same values for the same key.
- **Disabled** (`PATCH disabled: true`, 200): 5 s later `/key` and `/auth/key` still answer **200**;
  a chat completion on a `:free` model answers **401** `User not found.`
- **Deleted** (`DELETE /keys/{hash}` 200, then `GET /keys/{hash}` 404 `API key not found`): 5 s later
  `/key` and `/auth/key` still **200**, chat **401**. At +66 s all three are 401.
- **Disabled, followed further** (third throwaway, 16:18:35Z): `/key` and `/auth/key` 200 at +0 s,
  401 at +60 s. So disable flips the key-info endpoint on the same timescale as delete.
- Answer: **the key-info endpoint lags a disable or delete by up to about a minute** (200 at +5 s,
  401 by +60 to +66 s); inference refuses at once. Verify a revoke with a zero-cost inference call, or
  poll `/key` until 401 with a deadline of a few minutes.
- The hash-equals-sha256(value) relation is undocumented and was measured on two keys in one
  account: store the hash from the create response, and use derivation (or a `label` match on the
  key list) only for keys created before the executor existed.
- All three throwaway keys deleted (404 on readback for the first and third; 200 DELETE for the second). The
  management key expires on its own an hour after creation.

## Question 5: moving a BWS secret between projects (measured 2026-10-09T15:47:59Z)

- Throwaway projects `probe-inc2-a` (`ed169638-…`) and `probe-inc2-b` (`1940ab55-…`). Machine
  accounts `probe-inc2-writer` (read/write on both) and `probe-inc2-reader` (read on `a` only;
  `project list` shows only `a`).
- Writer creates `PROBE_INC2_MOVE` (a random placeholder, not a credential) in `a`: id `955b7434-…`.
  Reader reads it by id: rc 0, value matches. Reader reads a random UUID: rc 1, `[404 Not Found]
  Resource not found.`
- `bws secret edit --project-id <b> <id>` as the writer: rc 0, **same id**, `projectId` is `b`.
  `secret list a` no longer contains it; `secret list b` does. The writer reads it by id: value intact.
- Reader reads it by id after the move: rc 1 with the same 404 message as the nonexistent-UUID control,
  which differs from its rc 0 before the move.
- Answer: **a move keeps the secret's UUID, and access follows the project**: an account without the
  target project gets the same 404 as for a secret that doesn't exist.

## Question 4: revoked BWS machine token (revoke 2026-10-09T15:51:22Z)

- `bws` 2.0.0 **caches a session per access-token id** in `~/.config/bws/state/`. With the real `HOME`, a
  token whose secret part was altered in-process still succeeded (rc 0): the cached session was used
  and the altered secret never reached the server. With an empty `HOME` per call, the same altered
  token gets rc 1 `[400 Bad Request] {"error":"invalid_client"}`. Every probe therefore runs cached and
  fresh.
- The Bitwarden Python SDK (`bitwarden-sdk` from PyPI, Python 3.12) installs and works. Probes run
  through it too: one client logged in before the revoke and reused (`sdk-held`), and a new client
  each round (`sdk-fresh`). `projects().list` takes the organization id.
- Baseline 15:49:08Z: cli-cached, cli-fresh, sdk-held and sdk-fresh all read; both controls 400
  `invalid_client`.
- After the revoke (first round 15:51:24Z, then every minute to +5 min, then every 5 min):
  **a new login fails at once** (cli-fresh and sdk-fresh: 400 `invalid_client`, identical to the
  malformed control, so revoked and malformed can't be told apart). **An existing session keeps
  reading**: cli-cached and sdk-held still rc 0 / ok at +20 min.
- Last rounds: at +50 min (16:41:42Z) cli-cached rc 0 and sdk-held ok; at +55 min (16:46:43Z)
  cli-cached rc 1 `[400 Bad Request] invalid_client` and sdk-held `[401 Unauthorized]`. The poller
  stopped there with nothing still working. The cached state dated from about 15:45 to 15:48Z and
  the held SDK login from 15:49Z, so each session lasted about an hour from its login.
- Answer: **a revoke stops new logins at once (exit 1, `invalid_client`, indistinguishable from a
  malformed token); sessions opened before it keep reading for the rest of their hour.**

## Question 6: GitHub App installation token writes an Actions secret (measured 2026-10-09T16:14:35Z)

- **Dispatch App, recorded not re-measured:** at 2026-09-02, from a mint response in the running
  container, installation `145535298` of App `4259746` held `actions:write, contents:write,
  metadata:read, pull_requests:write, workflows:write` and **no `secrets`** (`credentials.md` #274).
- Throwaway App `probe-inc2-app` (App ID `5252714`), repository permission Secrets: read and write
  (Metadata read added by GitHub), webhook off, owner-only. Private key generated under the App's
  **Credentials → Key pairs** tab (GitHub moved it off the General page), stored at
  `~/.probe-inc2/app.pem` mode 600. Installed as installation `169664325` on **only**
  `probe-inc2-secrets` (the install screen defaults to *All repositories*; changed before installing).
- Mint with `{"secrets": "write", "metadata": "read"}` for that repo: 201, response `permissions`
  `{metadata: read, secrets: write}`, `repository_selection: selected`, one-hour expiry.
- With it: `GET …/actions/secrets/public-key` 200; PyNaCl sealed box; `PUT …/actions/secrets/
  PROBE_INC2_PLACEHOLDER` **201**; list shows the name with `updated_at` 16:14:36Z (fresh).
- **Control:** a token minted with `{"metadata": "read"}` (response `permissions` `{metadata: read}`)
  gets 403 `Resource not accessible by integration` on the public key and on a PUT carrying a validly
  sealed payload; the secret's `updated_at` is unchanged.
- The full token on another repository (`orchestrator`) gets 403 on the public key.
- Both tokens revoked (`DELETE /installation/token` 204).
- Answer: **yes: an App with `secrets: write` writes Actions secrets through an installation token**,
  and narrowing `permissions` at mint removes it. Increment 7's dedicated rotation App holds.

## Cleanup (confirmed)

- OrbStack machine `probe-inc2-root`: deleted; `orb list` has no row (13:12Z).
- OpenRouter throwaway keys (three): deleted; `GET /keys/{hash}` 404 for the first and third, DELETE
  200 for the second. The management key `probe-inc2-mgmt` was created with a one-hour expiry; its
  Keychain item was deleted at 16:24Z before a post-expiry probe ran, so its expiry is **not
  probe-confirmed**. Devon can confirm in the console that it shows as expired.
- Coolify app `probe-inc2-app` and project `probe-inc2`: deleted; app `GET` 404, project gone from the
  list, no container on the VM (16:17Z). The three dev tokens: deleted by Devon; each gets 401
  `Unauthenticated.` on `GET /version` (16:23Z). Their Keychain items deleted and confirmed absent.
- BWS secret `PROBE_INC2_MOVE`: deleted; readback rc 1.
- GitHub App `probe-inc2-app`: deleted by the session in Chrome at Devon's go-ahead (16:25Z).
  `GET /app` with a JWT signed by its key: 404 `Integration not found` (the same key minted tokens at
  16:14Z). Key file and `~/.probe-inc2/` removed.
- Observed on GitHub's App list page (not measured): a banner says installation tokens will move to
  a stateless format (`ghs_…`, up to about 520 characters). Anything that stores or validates
  installation-token length needs to allow for it.
- Repository `AlobarQuest/probe-inc2-secrets`: deleted by the session in Chrome at Devon's go-ahead; `gh api repos/AlobarQuest/probe-inc2-secrets` 404 (it answered before).
