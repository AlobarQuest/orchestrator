# Stop point 1: design inventory (W-272, lanes-inc-7-loaded-fingerprint)

Base commit: `883cd863`. No code is written yet. Each answer cites file and line at that commit.
Two design choices need HQ's yes or a correction; they're marked **Decide** in item 3.

## 1. How each live instance gets orchestrator code

- **VM `/opt/rotation-worker`.** `scripts/rotation-worker-vm/provision.sh` takes a full 40-character
  commit (lines 16-19), fetches and checks out exactly that commit into `/opt/rotation-worker/src`
  (lines 63-69), and builds `/opt/rotation-worker/venv` with `uv sync --frozen --no-dev` from it
  (lines 73-74). It's a pinned, non-editable checkout. A merge to `main` changes nothing it runs
  until someone runs `provision.sh <new commit>`. The executor only reports the lag, as
  `rotation-instance-behind` after 7 days (`docs/operations/rotation-executor.md`, the Alerts
  table, line 132).
- **Mac `com.devon.rotation-worker`.** `scripts/com.devon.rotation-worker.plist` line 24 runs
  `__BASE__/venv/bin/rotation-worker`. `scripts/install-rotation-worker-launchd.sh` sets
  `BASE="$HOME/Library/Application Support/rotation-worker"` (line 47), checks the given commit
  out into `$BASE/src` (lines 57-63), and builds `$BASE/venv` from it with
  `uv sync --frozen --no-dev` (lines 64-65). It isn't built from the main tree, and it isn't
  editable (lines 4-8 say so). A merge changes it only when the installer runs again with a newer
  commit.
- **Host helper `sds-pg-rotate`.** Installed by hand as root:
  `install -o root -g root -m 0750 deploy/vps/sds-pg-rotate /usr/local/sbin/sds-pg-rotate`
  from a checkout at the VM's commit (`docs/operations/rotation-executor.md`, lines 1104-1107),
  run by the SSH forced command (line 1141). A merge changes nothing on the host until someone
  reinstalls the file.

So the merge is inert on all three until each is reinstalled. On top of that, the transition rule
in item 3 keeps today's behavior on every row, because no row carries `helper_target`.

## 2. Which instance registers `bws-lane-token`

Both builds contain the adapter (`src/rotation_worker/adapters/__init__.py`, lines 228-236), but
only the VM instance takes its units. `worker._ours` (`src/rotation_worker/worker.py`, lines
526-535) gives the Keychain instance exactly the adapters in `envelope.KEYCHAIN_ADAPTERS`, which
is `{"bws-machine-token"}` (`src/rotation_worker/envelope.py`, line 13). The lane token's
`store_kind` is the default `bws-secret` (`adapters/__init__.py`, line 84; `types.py`, line 25).
The Mac instance never claims or waits on a lane-token unit, so this change doesn't reach it. A
test can pin that: a Keychain-store setup's `_ours` is false for a `bws-lane-token` unit. I'll
add that test only if none exists.

## 3. Planned changes, the transition rule's code shape, and tests

### `deploy/vps/sds-pg-rotate`

- `_target(name, verb)`: for `set` and `probe`, refuse unless `container`, `role` and `database`
  are all valid identifiers (`bad_target`, as today). For `loaded`, require none of them. This
  makes a consumers-only target valid for `loaded` only. A full Postgres target keeps working for
  all three verbs, because `postgres_role` reads `loaded` from its own targets
  (`adapters/postgres_role.py`, line 499).
- `_loaded` is unchanged: it already refuses a missing or empty `consumer` list, a non-table
  consumer, a bad container name and a malformed env name, each as `bad_target` (lines 166-177).
- The docstring describes the consumers-only target.

### `src/rotation_worker/adapters/bws_lane_token.py`

- `helper_target(credential) -> str | None`: `None` when the row has no `helper_target`.
  Otherwise the row must hold a non-empty identifier string, or it raises `ContractError`.
  `check_row` calls it, so a malformed value is refused before a claim. An absent value isn't an
  error, so nothing becomes required.
- `build_provider` takes `pg_helper` and `issue`. It refuses (`ContractError`, as
  `postgres_role.build_provider` does at line 211) only when the row has a `helper_target` and no
  helper is configured. A row without one never touches `pg_helper`.
- `BwsLaneTokenProvider` keeps `_target: str | None` and `_helper`.
  - **The transition rule's exact shape**, in `consumer_loaded`:

    ```python
    if self._target is None or self._issue:
        # today's proof, byte for byte: the PATCH answering 2xx
        if self._written != new.fingerprint:
            self.write_variable(consumer, new)
        return True
    # A reissue's verify unit with a helper target: one loaded read, matched or raised.
    ```

    The read raises `ContractError` on a mismatch or an unreadable answer. It never returns
    `False`. The reason: `contract._verify_consumers` (lines 753-764) fails the unit only for a
    stored or held consumer that's missing. A hosted `coolify-env` it would only count as
    "N-1 of N" and pass. A raise is the only way the claimed step fails the unit.
  - `loaded_wait(unit, keeper: Fingerprint) -> str | None`: `None` when there's no target, the
    plan is an issue, or the unit has no `verify_consumers` step. Otherwise it reads `loaded` and
    returns a reason unless every `<container>/<env>` fingerprint equals
    `f"{keeper.sha256_prefix}:{keeper.length}"` and the map is non-empty. An empty print (the
    container is missing or the variable is unset), any other fingerprint, a `HelperRefused`, or a
    `ContractError` from the client (no answer, a malformed answer) all return a reason, never a
    pass.
  - The match is exact string equality on 16 hex characters plus the length. It isn't
    `Fingerprint.matches`, which accepts an 8-character prefix that a person typed.
- The module docstring's "until ADR-0059 increment 7" bullet describes both paths.

### `src/rotation_worker/adapters/__init__.py`

- `AdapterContext` gains `issue: bool = False`. `_bws_lane_token` passes `context.pg_helper` and
  `context.issue`.

### `src/rotation_worker/worker.py`

- `_provider` passes `issue=approved.plan.issue`.
- A new `_loaded_waits(setup, approved, provider, unit_id, found)` joins `_take`'s pre-claim chain
  beside `_adapter_waits`. It runs only for a provider that is `isinstance(..., WaitsForLoad)`, a
  small `runtime_checkable` protocol modelled on `ReadsItsMint` (lines 678-690). It reads the
  keeper's fingerprint with `setup.store.read(rotation.keeper_id).fingerprint`, as
  `consumer_reads` does (line 944). A `StoreError` waits too. A reason becomes
  `found.waits[unit_id] = Wait(reason)`: the ordinary 48-hour `rotation-unit-waiting` clock, not
  the reader clock. It logs `unit_waiting` with the reason, which holds fingerprints and refusal
  words only.

**Decide (a): the hook.** `Adapter.wait` has the signature `(unit, history, now)`
(`adapters/__init__.py`, line 74). It can't reach the helper, the row or the keeper, and it's a
module-level function shared by type. My plan leaves `Adapter.wait` alone and adds the
provider-level protocol in the `_take` chain. That's the same place `_adapter_waits` sits and the
same pattern `_read_mint_waits` uses. The alternative is widening `WaitReason` to take the
provider and the store, which changes `bws_machine_token.wait_reason`'s signature too. I
recommend the protocol.

**Decide (b): telling an issue from a reissue.** The provider can't see which plan runs. Using
`self._written` ("this unit wrote the variable") would misfire on an issue's retry. On a retry
the store unit skips the `distribute` an earlier attempt filed, so the provider would read
`loaded` before the lane restarted and fail every retry. I plan to pass `approved.plan.issue`
through `AdapterContext.issue`, one boolean that an issue's provider uses to keep the `PATCH`
proof. I recommend this.

### Tests

- `tests/deploy/test_sds_pg_rotate.py`: a consumers-only target answers `loaded`. `set` and
  `probe` on it answer `bad_target`. `loaded` refuses a missing consumer list and a malformed env
  name. A full target still answers all three.
- `tests/rotation_worker/` (the lane-token tests, `test_lane_credentials.py` or a new
  `test_lane_token_loaded.py`):
  - With `helper_target`, a reissue's `verify` unit passes only on an equal fingerprint. A
    mismatch, an empty print, and a helper refusal or no answer each leave the unit unclaimed with
    a `Wait` on the ordinary clock. A claimed read that mismatches fails the unit.
  - An issue's `store` unit with `helper_target` keeps the `PATCH` 2xx proof and never calls
    `loaded`.
  - A row without `helper_target`: `loaded_wait` is `None`, `consumer_loaded` makes the same
    `PATCH` and returns `True`, and no helper is ever built. This pins today's behavior.
  - A malformed `helper_target` is refused by `check_row`. A target with no helper configured is
    refused at build.
  - Captured logs and exception texts never contain the fake token text. Fixtures use obviously
    fake values that don't match the token shape.
- Mutation proof for each rule, as the handoff lists, reported with the count and any survivors.

## 4. The helper's `loaded` verb, and the lane image

- `_loaded` (`deploy/vps/sds-pg-rotate`, lines 166-182) runs
  `docker exec <container> sh -c 'printf %s "$<ENV>"'` per consumer (line 178). `ENV` is
  validated against `[A-Z_][A-Z0-9_]*` first (lines 33 and 176), and the container name against
  `IDENT` (lines 32 and 174). It hashes stdout and returns only `sha256[:16]:<length>`, or `""` on
  a non-zero exit or an empty value (lines 179-181). The value stays inside the helper process;
  only the fingerprint is written to stdout.
- The lane image is `FROM ${ORCHESTRATOR_IMAGE}` (`lanes/Dockerfile`, lines 12 and 44). The
  orchestrator image is `FROM python:3.14-slim` (`Dockerfile`, lines 2 and 22), which is Debian
  13 (the lane Dockerfile pins `deb13u` packages at lines 46-48). Debian slim keeps
  `/bin/sh` (dash, `Essential: yes`), and `printf` is a dash builtin. coreutils, also Essential,
  ships `/usr/bin/printf` as well. The `docker exec` runs as the image's `USER orchestrator`
  (`Dockerfile`, line 48), and reading its own environment needs no privilege. I read this from
  build files only. I pulled and ran nothing.
- **It tells new from old.** The two tokens are different strings, so their sha256 prefixes
  differ. The container reports only the token it loaded at its last start. Before the restart it
  reports the old print (a mismatch, so the unit waits). After the restart it reports the new
  print (a match). The 16 hex characters give 64 bits; a false match between two random tokens
  isn't a practical risk.
- **The name form it matches.** `docker exec` takes the container name exactly, and the helper
  refuses anything outside `IDENT` (`[A-Za-z_][A-Za-z0-9_.-]*`). It does no prefix or pattern
  match. Whether Coolify keeps that name stable across redeploys is HQ's live read, as the handoff
  says.

## 5. Conflicts with Amendments 29 and 30

- **Amendment 29** matches this scope. Item 3 names exactly these three changes. The `targets.toml`
  entries and the rows' `helper_target` are the cut-over's, and I add neither. One point to
  record: amendment 29 says "`_target` refuses it for `set` and `probe`". The existing refusal code
  for a missing `role` or `database` is `bad_target` (`_ident`, line 96), and I keep that code.
- **Amendment 30 (broker lanes).** A broker lane's container holds no `BWS_ACCESS_TOKEN`.
  `lanes/inert-landing/compose.yaml` line 15 says its variable "maps to nothing". Yet it still has
  a lane-token row, `inert-landing-bws-token` (`.cred-consumers.toml`, lines 1355-1378), because
  the account and its Coolify variable exist. The broker's own row,
  `secrets-broker-bws-token` (lines 1497-1516), is a real loaded token
  (`lanes/secrets-broker/compose.yaml`, line 26), but `rotated_by_sds = false`.
  - The adapter treats a broker lane's row by the transition rule. Without `helper_target`, the
    row keeps the `PATCH` proof, as today.
  - If the cut-over ever gave a broker lane's row a `helper_target` naming its own container, the
    helper would answer `""` for the unmapped variable. That's a mismatch, so the unit waits and
    raises `rotation-unit-waiting` at 48 hours. It fails safe and never passes, so the revoke never
    follows. It would also never succeed, though, so the cut-over must not give a broker lane's
    row a `helper_target`. The broker itself could take one, naming its own container.
  - I don't plan code that detects a broker lane. The adapter can't see the compose file, and
    inferring one from the row would be guessing. I'll name this in the report as a cut-over
    constraint for HQ. Tell me if you want a `check_row` refusal instead. That needs a row-level
    marker, which means a `.cred-consumers.toml` change, and that's out of scope.

## What I need from HQ

1. Yes or no on **Decide (a)**: a provider-level `WaitsForLoad` protocol in `_take`'s chain,
   leaving `Adapter.wait`'s signature alone.
2. Yes or no on **Decide (b)**: `AdapterContext.issue` from `approved.plan.issue`.
3. Confirm that the broker-lane case is a cut-over constraint in the report, not code.
