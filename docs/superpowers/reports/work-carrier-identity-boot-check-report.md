Outcome: done

# Work carrier identity: registry pin and boot check (ADR-0055 amendment 22, step 3e-cid-2)

- **PR:** AlobarQuest/orchestrator#666, opened ready (not draft). Not merged, released or deployed.
- **Base:** `origin/main` at `027a7c47`. The handoff's facts held at that base: `main.py` lines
  226-227 and 230, the call at 135-140, and `config.py` line 158.
- **Code head:** `c0f378d0`. This report is the next commit on the branch.

## What I built

1. **The registry pin.** `security-standards.pin.toml` moves to revision
   `31aa7d70ba208d17bfe53f50fed0e035ff451870`, with `artifact_sha256`
   `b11a9e9c0c28d0c77e32c6fd831bab9fd9f83278e66d76c4ff0effdc60a416b9`. No fixture mirrors the
   production bundle:
   - `tests/fixtures/registry-bundle.json` and `tests/fixtures/security-standards/` are two-agent
     fixtures (`devon`, `worker`).
   - The pin's own tests (`test_release_workflow.py`, `test_container.py`) check its shape, not
     its values.

   So the pin file was the only change the bump needed.
2. **The boot check.** I added `WORK_CARRIER_OBSERVER_PROFILE = "work-carrier-observer-v1"` beside
   the executor profiles, and `_require_work_carrier(registry, credentials, agent_id)`, which
   `load_auth_config` calls after `_require_rotation_executors`. The rule is that the set of
   identities whose credentials hold the profile must equal `{setting}`, or the empty set when
   the setting is unset. Boot refuses each of the following:
   - A credential holding the profile with no setting.
   - A credential holding the profile with a setting that names another identity.
   - A second identity holding the profile.
   - A setting with no credential, including one that names an identity holding another profile
     or an unknown identity.

   Several keys for the one identity pass. I wrote a sibling function rather than adding to the
   executors' mapping. The mapping deliberately accepts a setting with no credential (the
   `setting_before_credential` case in `test_rotation_executor_boot.py`), and the handoff requires
   that case to refuse.
3. **Docs.**
   - `docs/operations/authentication.md` lists the setting (now "Four optional settings") and
     states the check. It also gives the add order: three edits, a read-back, then one restart.
   - `docs/operations/credentials.md` #20 points to that paragraph as the exception to restarting
     between variables.
   - The root `CLAUDE.md` amendment-22 bullet names the rule and the test file.

   `git grep -n _require_rotation_executors docs/` had no hits. I chose `authentication.md`
   because it is where the executors' boot refusal is documented.

## Checks

### 1. The recomputed `artifact_sha256`

I used `git archive` against `~/Projects/security-standards` through the script itself, with
output to the scratch directory `/tmp/w94-ss.LSrP`. The working tree there was not touched.

```
.venv/bin/python scripts/shape_registry_context.py --source ~/Projects/security-standards --revision <rev> --output /tmp/w94-ss.LSrP/<rev>
PYTHONPATH=scripts .venv/bin/python -c "...from build_registry_bundle import artifact_digest; print(artifact_digest(Path(dir)))"

64b71869c2af17aae103bbafeff23609a6bd8151 -> f57cef12022a10b3b07b633fbdb40aded0cfb38ceeab1be6b8c026780bb5bea1   (control: equals the old pin)
31aa7d70ba208d17bfe53f50fed0e035ff451870 -> b11a9e9c0c28d0c77e32c6fd831bab9fd9f83278e66d76c4ff0effdc60a416b9
```

The build gate accepts it:
`build_registry_bundle.py --artifact-dir … --source-revision 31aa7d70… --artifact-sha256 b11a9e9c…`
exited 0. It produced 19 actors, including `work-carrier` (`active`, `work-carrier-observer-v1`)
and `security-executor` (`retired`, `security-window-v1`). Between the two revisions, the diff
under the shaped paths touches only `registry/agents/security-executor.yaml` and
`registry/agents/work-carrier.yaml`. `31aa7d70` is on security-standards `origin/main`.

Note: my first `git cat-file` on `31aa7d70` failed, so a fallback ran `git fetch` in the
security-standards main checkout. That updated its remote refs only, with no checkout or working
tree change. `git status --porcelain` there was empty afterward.

### 2. `make check`

I ran it on the committed tree at `c0f378d0`, against `orchestrator_test_hq_w_94`, with
`PGTZ=UTC` per `local-development.md`. It exited 0:

- Ruff passed, and the format check found 1,054 files already formatted.
- Pyright found 0 errors.
- pytest collected 14,563 tests: 14,562 passed, 1 was skipped, and 0 failed.

`test_queue_age_median_of_ready_units` passed, because `PGTZ=UTC` removes the daylight-saving
offset.

### 3. Mutations of the boot check

Each mutant was applied to `main.py`, with `__pycache__` cleared, and run against
`tests/identity/test_work_carrier_boot.py`. Afterward I restored the file and confirmed it with
`cmp`.

| Mutant | Killed by |
|---|---|
| M1: remove the `_require_work_carrier` call | All 6 refusal cases |
| M2: an unset setting accepts any holder | `credential_without_setting` |
| M3: a setting with no credential passes | `setting_without_credential`, `setting_names_another_profile`, `setting_names_no_identity` |
| M4: the setting need only be among the holders | `a_second_holder` |

All four mutants were killed, with no survivors. The four accepting cases passed under every
mutant: `neither`, `blank_setting_is_unset`, `both_agree` and `two_keys_one_identity`.

### 4. `security-executor`: grep and retired-agent boot behaviour

`git grep -n security-executor` at the base found four hits, none of them in code or config:

- `docs/decisions/0055-…md:774`, history text.
- `docs/decisions/0055-…md:2196`, which says the identity stays in the registry as `retired`.
- `docs/superpowers/plans/2026-11-01-rotation-part-5-close.md:53` and `:64`, which plan the
  deletion and the retirement.
- `docs/superpowers/specs/2026-07-06-ws34-evidence-events-design.md:122`, a list of actors at
  baseline.

`git grep` for `security_executor` and `security-window` found nothing outside docs history.
Nothing at the base names it as active.

When a configured credential names a retired agent, boot refuses with the generic `RuntimeError`
`invalid runtime authentication configuration`. `_m2m_credentials` calls `registry.resolve`, which
raises `RegistryValidationError("registry identity is not active")` for any status other than
`active` (`identity/registry.py:45`). That error is a `ValueError`, so `load_auth_config` converts
it to the generic refusal.

A boot-level probe confirmed both sides, using a temporary test file that I deleted afterward.
With a `security-executor` credential, `status: active` booted, and `status: retired` refused with
the cause `RegistryValidationError registry identity is not active`. The unit-level pin is
`test_registry_fails_closed_for_inactive_identities` in `tests/identity/test_registry_bundle.py`.
`resolve` has no other caller that reads historical actors: it is called only at boot
(`main.py`) and at authentication (`identity/auth.py`).

### 5. What plan step 3e-cid-3 needs, in order (read only)

**Before this image is released**, because the repository can't verify either of these:

1. Confirm `ORCHESTRATOR_WORK_CARRIER_AGENT_ID` is unset or blank in production, in both rows.
   With this image, a setting with no credential refuses boot.
2. Confirm no `ORCHESTRATOR_M2M_CREDENTIALS` entry names `security-executor`. With this bundle,
   that entry refuses boot.

**Step 3e-cid-3, after Devon's mint:**

1. Make sure no rotation is pending or running against the orchestrator's
   `ORCHESTRATOR_M2M_CREDENTIALS` between the edits and the restart. The executor rewrites the
   variable from what the process loaded, which would drop the unloaded entry and leave a setting
   with no credential.
2. Add `"work-carrier-observer": {"agent_id": "work-carrier", "token_hash": "<sha256>"}` to
   `ORCHESTRATOR_M2M_CREDENTIALS`, both rows.
3. Add `"work-carrier-observer": "observer"` to `ORCHESTRATOR_M2M_ROLES`, both rows.
4. Set `ORCHESTRATOR_WORK_CARRIER_AGENT_ID=work-carrier`, both rows.
5. Read all three variables back from Coolify, both rows, parsed in-process with whitelisted
   fields only. Re-read the stored credentials immediately before the restart.
6. Restart, confirm boot, verify the three variables in the container, and confirm
   `GET /api/v1/m2m-identities` lists `work-carrier-observer`.
7. Then make the later items: `run-work-carrier.sh` fetching the token into
   `WORK_CARRIER_OBSERVATION_TOKEN` with `WORK_CARRIER_OBSERVATION_KEY_ID=work-carrier-observer`,
   the `.bws-secrets.toml` UUID, and the `.cred-consumers.toml` row.

**Rollback:** once the credential exists, an image with the old bundle refuses boot, because
`work-carrier` is unknown there. Remove all three variables in one restart before rolling back
past this image. The same applies to any image older than this check: it accepts the variables,
but the old bundle doesn't resolve the identity.

### Security scan and code review

- **Security scan** from the worktree root (`security_scan.cli . --category security`): no BLOCK
  findings. It reported two WARNs (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`)
  and one INFO judgment (`bws.least-privilege-scope`). This diff touches neither `.gitignore` nor
  `.bws-secrets.toml`. My control scan of a clean copy of `main` was denied by the permission
  layer, so I didn't prove the WARNs predate this change, only that it touches neither input.
- **`/code-review` (low)** on `origin/main...HEAD`: no findings.

## Files changed

- `security-standards.pin.toml`: the revision and digest.
- `src/orchestrator/main.py`: `WORK_CARRIER_OBSERVER_PROFILE`, `_require_work_carrier`, and its
  call.
- `tests/identity/test_work_carrier_boot.py`: new, with 4 accepting and 6 refusing cases.
- `docs/operations/authentication.md`: the setting, the check, and the add order.
- `docs/operations/credentials.md`: a pointer in #20.
- `CLAUDE.md`: the amendment-22 bullet names the boot rule.
- `docs/superpowers/reports/work-carrier-identity-boot-check-report.md`: this report.

## Self-review

- The check reads no secret and logs nothing. Like `_require_rotation_executors`, it raises the
  generic boot error, so neither a key id nor an agent id reaches a log.
- I ran it after `_m2m_credentials`, so every credential's identity has already resolved as
  active, and `resolve` in the comprehension can't raise anything new.
- An unknown or other-profile setting is refused because it isn't in the holder set. The function
  needs no separate `resolve` of the setting.

## Deviations

- **The add order has five steps, not the design note's four, and one restart.** The design note
  item 5 says "verify it in the container" after adding the credential, and root invariant #20
  says to verify each variable in the container before the next restart. Under the rules this
  handoff specifies, neither can hold: the credential alone refuses boot, and so does the setting
  alone. So all three edits share one restart. The only check before that restart is a read-back
  from Coolify, which I added as step 4 in `authentication.md`. Roles go in the same restart, as
  the design note orders them. A separate restart for roles would leave the credential an
  unpromoted WORKER in between. `credentials.md` #20 now names this exception.
- The handoff's "boot passes with both present" is covered by `both_agree` and
  `two_keys_one_identity`.

## Concerns

- If the restart in step 3e-cid-3 refuses boot, the orchestrator is down until the variables are
  fixed. The read-back in step 5 is the guard. The release checks in check 5 cover the same risk
  for this image's own deploy.
- `docs/operations/rotation-executor.md` says "the setting alone boots" for the executors. That
  remains true for them. The carrier differs, and `authentication.md` and `CLAUDE.md` say so.

## Follow-ups

These are already planned for step 3e-cid-3 and are not built here:
- The `run-work-carrier.sh` token fetch.
- The `.bws-secrets.toml` UUID.
- The `.cred-consumers.toml` row.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** the check adds no log line. Like
  `_require_rotation_executors`, it raises the generic boot `RuntimeError`, and the process's
  existing logging reports the failed start. There is no unstructured print.
- **Code Brain rule 4 (never swallow an exception silently):** every refusal raises. Nothing is
  caught and dropped. `load_auth_config`'s existing handler converts a `ValueError` to the generic
  `RuntimeError` with `from error`, which keeps the cause.
- **Code Brain rule 5 (never log secrets):** the refusal message is the fixed generic string. It
  carries no token, hash, key id or agent id. The tests use placeholder hashes.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files or history, no inline bootstrap
  token):** the diff adds no token or token-shaped string. The security scan reported no BLOCK
  finding.
- **Infra Brain 1032 (cred.exposure-rotate):** no credential exists yet, and none was created,
  read or surfaced, so there is no exposure entry.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. There is no domain change.
