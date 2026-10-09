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
