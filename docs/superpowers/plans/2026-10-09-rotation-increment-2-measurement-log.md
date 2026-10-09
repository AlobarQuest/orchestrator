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
- Answer: a revoke verified through the key-info endpoint can report a dead key as live (indefinitely
  for a disabled key as far as measured; about a minute for a deleted one). **Verify a revoke with a
  zero-cost inference call, or wait for `/key` to 401 after a delete.** Disable alone was not followed
  past 5 s on `/key`, since the inference probe had already flipped.
- Both throwaway keys deleted (404 on readback for the first; 200 DELETE for the second). The
  management key expires on its own an hour after creation.
