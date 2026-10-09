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
