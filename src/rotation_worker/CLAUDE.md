# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The rotation executor (`src/rotation_worker/`)

- The rotation executor reads the member record's `credential_rotation` grant (`GET
  /api/v1/members`, `rotation_worker/grants.py`) before it claims a unit, which stays unclaimed
  while the grant is absent, even one the checks refuse whose registry row resolves, and before
  each step, where a withdrawn grant fails the unit as a
  handed-back `rotated_by_sds` flag does and can leave an overlap open. An empty or unreadable
  record is unread, never a withdrawal: before the claim the unit waits, and mid-rotation the step
  proceeds. That protects the grant read only; a failed `renew` still fails the unit. Detail:
  `docs/operations/rotation-executor.md`, "The member grant".
- The rotation executor (`src/rotation_worker/`) acts only on what git confirms: the revision's
  commit on intent-packages' `main` with its hash recomputed, the registry at its repositories'
  `main`, and registry, package and envelope destinations equal in `destinations.py`'s one format
  (pinned with intent-packages by `tests/fixtures/rotation_destination_format.json`). Every
  adapter's steps run through `contract.py`, which holds ADR-0055 decision 3's invariants; never
  re-implement a step in an adapter. A rotation unit has no `target_repository`, so it reads
  `GET /work-units/{id}`, never the runner brief. It reads the package at its `package_id`'s
  path: an intake's `source_path` is the emitting machine's directory, never a git path. Detail:
  `docs/operations/rotation-executor.md`.
- A credential type's plan is `plans.derive` from `Adapter.capabilities` and the registry row's
  destinations, criteria numbered over its machine steps; the approved package pins which plan
  runs. The executor runs the derived plan, or the type's pre-derivation `Adapter.plan`, whichever
  the package's criteria describe step for step (unit too, where named), and refuses otherwise;
  read `Approved.plan`, never `adapter.plan`, for the plan a unit runs. A derived plan never has a
  person attest a consumer. Detail: `docs/operations/rotation-executor.md`, "The
  plan a credential runs".
- The executor's Keychain instance (ADR-0055 amendment 12) keeps state in the login Keychain
  through `/usr/bin/security` (`secret_store.KeychainStore`): values only on stdin and stdout
  (`security -i`), items updated in place with `-U`, never re-created, and an update never
  restates the access list (macOS then prompts a person). Each instance takes only its own store's
  adapters (`Adapter.store_kind`). A store reads back its own kind (`SecretStore.locate`); any
  other `keychain` destination stays person-attested. Detail:
  `docs/operations/rotation-executor.md`.
- A person's mint reaches a rotation only through its instance's `rotation-inbox` BWS secret or a
  staging secret written by hand: the unit after the mint waits, unclaimed, until one is staged
  (`worker._mint_waits`). The inbox binds a value only while no other rotation on either
  instance waits on a mint, and only after the provider accepts it beside a refused control,
  `same_identity` (same account, not scope) holds against the keeper, and no other SDS-rotated
  keeper of its type holds it; a lane token binds by its row's declared reach instead
  (`inbox.BindsByItself`, ADR-0055 amendment 19), never by `same_identity`;
  it writes staging before emptying the inbox. A ping URL never comes through the inbox: its
  mint is the machine's (`Capabilities.creates` false, so no `credential_create`), reading the
  `ping_url` of the check its row's `healthchecks_check` names from the Management key's listing
  (`readers.PingUrls`, which may only `GET /api/v3/checks/`), before the claim in
  `worker._read_mint_waits`: no answer or a `StoreError` waits, anything else that fails is a
  `ReadError` refused before the claim, and the claimed mint reuses that pass's listing (ADR-0055
  amendment 23). The Keychain instance reads its inbox with its
  own narrow BWS identity, which the VM's root can't reach. Detail:
  `docs/operations/rotation-executor.md`, "A person's mint is taken from the inbox".
- A scheduled `runtime-fetch` reader is verified by the read its registry row names
  (`healthchecks_check`, or `observation_source` with `observation_subject`, beside `note`), never
  by a person; a unit that retires waits unclaimed until every such reader has run since the
  keeper's revision date, and a plan with no human act refuses a reader with no read. Weekly
  readers gate retire even past the overlap's `until`. No step can answer "not yet" once its unit
  is claimed: any exception fails the unit and spends an attempt, so a step that must wait waits
  before the claim, in `worker.py::_take`. Detail: `docs/operations/rotation-executor.md`.
- A host with no overlap (`m2m_setting.Change.REPLACE`) is declared only by the mint unit's
  approved envelope (`host_without_overlap: true`), and `build` refuses it unless another served
  entry holds the old hash as current, the one case where boot refuses an overlap. Never widen
  that condition or infer the declaration. Its undo sets the entry's hash to the sharing entry's.
  Detail: `docs/operations/rotation-executor.md`.
- The `change-manager` rotation adapter can't read what change-manager loaded: it writes each
  bearer variable from the old and new values alone, confirms by probing `/api/landing-policy`,
  and refuses a value any other change-manager keeper holds, since a shared `previous` stops
  change-manager booting. All four keepers must be readable by the root before its first
  rotation. Restarts wait while any registered change-manager reader is mid-run. Detail:
  `docs/operations/rotation-executor.md`.
- A value one application holds to call another service is a `coolify-env-held` destination
  (ADR-0055 amendment 13), never unregistered: a rotation that misses one strands its holder on a
  dead value. Distribute writes it and restarts the holder; it counts switched only when the
  holder reports the new fingerprint, and only the orchestrator reports
  (`GET /api/v1/held-credentials`, executor-only, its `HELD_SETTINGS`, which names
  `ORCHESTRATOR_CHANGE_RECORD_TOKEN` and `ORCHESTRATOR_ALERT_HUB_TOKEN`), so the checks refuse a
  held destination anywhere else, or one the report doesn't list, before the claim. Detail:
  `docs/operations/rotation-executor.md`, "Held copies".
- The executor writes an Actions secret only through the rotation App, with a token minted for
  the destination's one repository and exactly `{secrets: write, metadata: read}` (refused if
  GitHub grants more), and counts a copy switched only when that repository's hourly
  `secret-fingerprint.yml` report shows the keeper's fingerprint; no report in three hours
  refuses the unit. A repository gaining a registered copy needs the App installed and the secret
  added to its report. A plan with Actions-secret destinations needs a distribute step. Detail:
  `docs/operations/rotation-executor.md`, "Actions secrets".
- A `postgres-role` host replaces the password with no overlap and restores the old value on its
  own failure, because `/review` sits behind the service it restarts. Its only reach to the
  database is the forced-command helper (`deploy/vps/sds-pg-rotate`), and a password must never be
  sent in plain text to `ALTER ROLE`: the helper sends only a SCRAM-SHA-256 verifier. Detail:
  `docs/operations/rotation-executor.md`, "Postgres role passwords"; ADR-0055 amendment 17.
- A rotation instance alerts only for the units it owns: `rotation_worker.envelope.KEYCHAIN_ADAPTERS`
  is a copy of `rotation_claims.KEYCHAIN_ADAPTERS` (the worker may not import the orchestrator), and
  `test_bws_machine_token.py` holds them equal. Change both together.
- A unit's wait alert runs on two clocks: reader waits (weekly readers, another rotation's overlap)
  on `unit_reader_clock_key` with the 192-hour threshold, every other wait, including a reader
  mid-run and `Revoke.UNKNOWN`, on `unit_waiting_key` with 48. A pass judges overdue by its own
  clock alone; while the unit stays waiting both clocks are kept and its waiting and stuck alerts
  are held. Never fold them into one key. Detail:
  `docs/operations/rotation-executor.md`, "Alerts".
- `rotation-heartbeat` is sent as `resolved` after every finished pass and is never raised; its
  meaning is the absence of the ping, heard by an alert-hub schedule. A pass that raises sends none.
  `rotation-instance-behind` is held when the mirror lacks the running revision.
- An issue (`plans.derive_issue`, ADR-0055 amendment 19) runs only on a keeper holding the inbox's
  empty marker (`contract.EMPTY`), which stands as its old value; `checks._check_issue_fact`
  reads that before the claim, and refuses a rotation plan on the marker. A lane token's variable
  is written at `distribute` with one `PATCH` by a Coolify client that can't restart
  (`restarts=False`); a ping URL is never sent anything: its `verify_new` checks its shape, then
  probes by the read-only key's lookup of its check's `unique_key` beside a random UUID's, which
  must answer 404 (amendment 21). `unique_key` hashes only the UUID's first half, so the lookup
  can't tell a URL wrong in its second half; cut-over step 4's first scheduled ping is the only
  proof of the full URL. Evidence holds only a 12-character `unique_key` prefix: the
  orchestrator's scan refuses a 32-character run. A machine-minted issue (a ping URL, amendment
  23) is one unit, still named `store`, the one unit name the factory policy's ping-URL row
  recognises. Detail: `docs/operations/rotation-executor.md`, "Lane tokens and ping URLs".
