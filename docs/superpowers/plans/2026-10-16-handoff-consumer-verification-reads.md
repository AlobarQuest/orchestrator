# Handoff: the executor confirms scheduled readers switched (ADR-0055 increment 6)

The rotation executor's `verify_consumers` step must confirm that every scheduled reader of a
rotated bearer ran with the new value before the old one is retired. The operator bearer had no
scheduled readers, so this was never built. The observer and drift-reporter bearers have sixteen.
This handoff builds the two reads ADR-0055 amendment 9 chose for them and installs them on the
executor VM. It doesn't move keepers, grant access, split the value or rotate anything.

HQ is the session that drove the operator bearer's rotation. Report to Devon; questions marked
**Stop and ask** go to him before you build past them.

## Read first

- `docs/superpowers/plans/2026-10-15-handoff-common-rules.md`: its **How to work** and **Closing
  steps** sections apply in full. Its **Don't touch** section is spent: that rotation finished on
  2026-10-16.
- ADR-0055's verification table, and amendment 9 in full, especially decision 5 and its section
  "Decision 5 for the drift-reporter's readers" (`docs/decisions/0055-*.md`).
- `docs/operations/rotation-executor.md`.
- `src/rotation_worker/contract.py` (how `verify_consumers` files evidence, and
  `unverified()`), `destinations.py`, `orchestrator_client.py`, `cli.py`, and
  `adapters/orchestrator_bearer.py`.
- `.cred-consumers.toml`: the `orchestrator-observer-bearer` and
  `orchestrator-drift-reporter-bearer` entries as #430 left them. Each scheduled reader is one
  `runtime-fetch` row. Its schedule, Keychain item and Healthchecks check are in free-text `note`.
- #430's description and its compare-destinations comment: the inventory's reader table.

## What to build

Two reads, chosen per reader:

- **Observer readers (eight, `orchestrator/scripts/run-*.sh`): Healthchecks.** A reader counts as
  switched once its check shows a successful ping for a run that started after the store step.
  Each launcher exits non-zero on an empty bearer, and `sds_deadman_finish` then pings `/fail`.
- **Drift-reporter readers (eight, in infraops, security-standards and vps-backup): the
  orchestrator's observations.** A reader counts as switched once it posts an observation for a
  run that started after the store step. Its Healthchecks success proves nothing here: its helper
  swallows a failed fetch or post. Read the subject each reader posts from its `observe_init` call
  and vps-backup's `observe-run.sh`. The route is `GET /api/v1/observations`
  (`api/routes/release.py`), filtered by `subject_reference` and `observed_from`.

Neither read tells the old value from the new, because the orchestrator records neither. The
verification rests on one assumption: **every reader fetches the keeper on every run and holds no
copy of the value.** Confirm it in each of the sixteen scripts and the helpers they call. `bws`'s
state file caches a login session, not a secret's value, but verify that, don't assume it.
**Stop and ask** if any reader keeps a copy.

## What to do

1. **Confirm the step can wait first.** These readers run nightly, and two run on Sundays only.
   `verify_consumers` can't finish inside one pass or one lease. Find how a step that isn't
   satisfied yet waits across passes without filing a failure: `contract.py`'s step filing, the
   lapse rule (`release_expired_claim`), and continuation with the holder's evidence (the
   CLAUDE.md invariant on rotation claims). Then compute the worst case against the overlap
   (`m2m_setting.py`'s `OVERLAP`, read from the code): a store just after a Sunday reader's run
   waits about a week for its next run. **Stop and ask** if the worst case can reach `until`.
   Whether weekly readers gate retire, or the overlap lengthens for this rotation, is Devon's.
   Everything else in this handoff depends on this answer, so do it before building.
2. **Carry each reader's check or subject as data.** The executor can't read free-text `note`.
   Weigh two routes: a parsed field beside `note`, outside the destination string, so
   intent-packages' pin (`tests/fixtures/rotation_destination_format.json`) is unchanged; or
   parsing `note`. #430 proved infraops refuses a row with no `kind`, not that it accepts an extra
   field. Prove infraops still parses the registry the way #430 did, with a scratch
   `INFRADRIFT_CONFIG_DIR`.
3. **Match Healthchecks checks by the identifier the registry records.** Ten of the twelve `sds-`
   checks have no slug, so that's the name. Confirm each name is unique in its Healthchecks
   project. Don't add slugs: that changes Healthchecks.
4. **Permit only the reads.** Add `GET /api/v1/observations` to the orchestrator client's confined
   permits, and check that the executor's role may call it (`api/dependencies.py`); the identities
   route is executor-only, and this one may not admit the executor. Add the Healthchecks host to
   `OUTBOUND_ALLOWLIST` with a reason. Read the Healthchecks key from BWS the way
   `coolify_token_id` is read, only when the step runs.
5. **The Healthchecks key.** Decision 5 names `Rotation / Healthchecks`. Check whether that secret
   exists. If it doesn't: a read-only Healthchecks API key is enough, Devon creates it in the
   Healthchecks UI for the project that holds the `sds-` checks, and the root stores it in the VM.
   Ask Devon for that act; never handle the value outside the VM.
6. **Prove each read discriminates.** A read that answers "switched" must be shown answering "not
   yet" on a known-bad input: a check whose last success predates the store time, and a subject
   with no observation since it. Mutation-test both, and report the count and every survivor.
7. **Install** on the VM per `docs/operations/rotation-executor.md`, and run each read once,
   read-only, against production for all sixteen readers with a store time of a day ago. Report
   what each answers.

## Out of scope

Moving the keepers, the two grants #430 listed, the split and its package, multi-keeper support in
the executor, and the rotation itself. Each comes after this.

## Closing steps

1. Merge each pull request yourself when its checks pass.
2. Deploy the orchestrator only if its code changed; reinstall the VM at every executor change.
3. Remove your worktree, test database and merged branch, and check the main tree's `git status`.
4. Report to Devon: the answer to step 1, how the rows carry their data, the read results for all
   sixteen readers, and the mutation count.
