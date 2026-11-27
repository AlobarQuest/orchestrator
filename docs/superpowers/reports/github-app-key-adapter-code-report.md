Outcome: done

# Build report: the GitHub App private-key rotation adapter (ADR-0055 Amendment 35 item 15, D-84)

Worker W-328, branch `hq/github-app-key-adapter-code`, based on orchestrator `origin/main` at
`5c3631b0`. No call to GitHub, BWS, Coolify, or Healthchecks was made, and no production act was
taken. The pull request is open and not merged.

- **PR:** PR_URL
- **Head:** PR_HEAD

## What I built

The `github-app-private-key` class now has a type, `github-app-key`, with Bitbucket's capabilities
(`machine_mint=False`, `machine_revoke=False`). A person generates a key pair and pastes base64 of
its PEM into the inbox. The executor proves the key, stores it, waits for every reader's green
ping, and, after the person deletes the App's other key pairs, proves the old key refused. Both
registry rows stay `rotated_by_sds = false`.

### Each change against item 15's list

| Item 15 entry | What changed |
|---|---|
| `src/rotation_worker/adapters/github_app_key.py`, the provider | New. `NAME` `github-app-key`, `PROVIDER` `github`, `PLAN = derive(...)` (Bitbucket's five segments). The probe is `GET /app` with a JWT the key signs; 200 must name the row's `app_id`, and a 200 naming another App raises a `ContractError` that names both ids. The control is a JWT signed by a fresh in-memory RSA key (`known_bad`). `dead_statuses = {401}`. `same_identity` needs both keys to answer for the row's App and their public halves to differ. `check_scope` (the `ScopeChecked` hook, run by verify-new after the live probe) holds the working proof (item 4) and the public-half refusal (item 3). `revoke_guide` gives the 8-character fingerprint (item 7). `check_row` covers item 11. `mint`, `host`, `consumer_loaded`, and `revoke` raise `ContractError`. |
| `src/estate_clients/github_app_mint.py` | `_jwt` renamed to the public `sign_app_jwt`, with a docstring and a default clock. `mint` calls it with the same arguments; nothing else in `mint` changed. |
| `src/rotation_worker/adapters/__init__.py` | The import and an `ADAPTERS` entry with `capabilities=TYPES[...]`, `check_row`, and `revoke_may_come_first=True`. |
| `src/rotation_worker/types.py` | `TYPES["github-app-key"]` and `CLASS_TYPES["github-app-private-key"]`. |
| `src/rotation_worker/inbox.py` (item 6 B) | The `NamesItsCredential` protocol, `WaitingMint`, and `waiting_mints` (which replaces `other_mints` and also returns each waiting unit's revision id). `bind` takes `others_declare`. The identity checks moved into `_identity_refusal` to keep `bind` under the complexity limit, with no change in behavior. |
| `tests/architecture/test_rotation_agreement.py` | `github-app-private-key` left `UNTYPED`. |
| `tests/architecture/test_rotation_edges.py` | A `PROVIDER_EDGES["github-app-key"]` entry naming the four requests. It also gains the module's client count in `_WORKER_CLIENTS`, the three new paths in `ROUTES`, and `sign_app_jwt`, `decode_stored_key`, and `REPOSITORY` in the lane's borrowed names from `github_app_mint`. |
| `tests/architecture/test_wsp21_invariant_scan.py` | The module in `OUTBOUND_ALLOWLIST`, with its reason, beside the Coolify adapter. |
| `layers.toml` | No change. `src/rotation_worker/**` and `tests/rotation_worker/**` already name every new file. |
| `src/orchestrator/factory-policy.toml` | `github-app-key` in both `rotation_adapters` lists, and a rationale line in both rows' text. |
| `.cred-consumers.toml` (item 11) | Each row gains `app_id`, `installation_id`, `proof_repository`, and an explicit `rotated_by_sds = false`. The preconditions are reworded to "mint it with … in one sitting" and "after its revoke alert delete the App's other key pairs", and the block comment states the route. There is no `rotate_requested`, no `[[credential.exposure]]`, and no change to `disposition`, the consumers, `created`, the `secrets-broker` row, or any lane row. |
| `docs/operations/rotation-executor.md` | A "GitHub App private keys" section covering generating a key pair (item 1's mint note, including deleting the download), what verify-new proves, deleting the old key pairs (item 7's note and the stray-pair gap), and the registry rows. The inbox section names the item 6 B exception. |
| One test per reader | `tests/rotation_worker/test_github_app_key_readers.py`, covered in the following section. |

Item 15 doesn't list the following changes, but items 6 and 7 need them:

- **`src/rotation_worker/worker.py`, two call sites.** `_bind_inbox` passes `others_declare` built
  from `_declared_by_waiting`, which goes from unit to intake to `standing_credential` to the
  registry row. The revoke `Act` body comes from `_revoke_guide`, which asks a provider that
  `GuidesTheRevoke` and otherwise sends the earlier text unchanged.
- **`src/rotation_worker/CLAUDE.md`.** The inbox rule now states the item 6 B exception.
- **Guard lists that name every adapter:** `tests/rotation_worker/test_client_surface.py`
  (`HTTP_CLIENTS`) and `tests/rotation_worker/test_postgres_role.py` (`EXISTING_PLANS`).

### Values the rows use, and where the repository states them

- **`app_id` and `installation_id`** come from each row's `provider_identity`: 5684758 and
  180257215 for the read App, 5684778 and 180257673 for the push App. A test holds each pair to
  its `provider_identity` text.
- **`proof_repository`** is `AlobarQuest/intent-packages` for both rows:
  - The push App is installed only on intent-packages and the forks (`.bws-secrets.toml` 381-382,
    and both launchers' `--repository AlobarQuest/intent-packages`).
  - The read App is installed on intent-packages by HQ D-17 (ADR-0059, lines 612-619 and
    1572-1573 at `5c3631b0`).

  Nothing was read from GitHub or BWS. Exit check 2 proves each value live.

### How D-84's answers and item 6 B show in the code

- **8 A.** Nothing moves. `.bws-secrets.toml`, the broker row's `reach_secrets`, and its
  `reach_projects` are unchanged.
- **9 A.** Nothing in the code depends on it. The rows stay out of the SDS's hands.
- **13 A.** Both rows are typed, and the executor runs items 1 to 7 for each. The precondition
  reads "mint it with … in one sitting".
- **14 A.** No throwaway App. The contract's `run.probe` already fails a control that isn't in
  `dead_statuses`, naming its status. verify-new files the control's status in its probes before
  `store`, and `confirm_dead` files the old key's. The following tests pin it:
  - `test_a_rotation_proves_stores_and_waits_for_the_person_s_deletion` records 401 for both.
  - `test_a_control_github_doesn_t_refuse_with_401_fails_verify_new_by_its_status` covers 403,
    404, and 422, with the keeper unchanged.
  - `test_a_deleted_key_github_doesn_t_refuse_with_401_fails_confirm_by_its_status` covers 403 and
    404, with the quarantine kept, so nothing is deleted by machine.
- **6 B.** `bind` skips the "another rotation waits" refusal only for a provider that
  `NamesItsCredential`, and only when all of the following hold:
  - the value's `GET /app` id equals this row's `app_id`;
  - no waiting rotation's row declares the same id;
  - every waiting rotation's row can be resolved.

  The other checks still run: `accepts`, `same_identity`, and `other_keepers`. Two tests in
  `test_github_app_key.py` cover the real provider: binding beside another App's waiting rotation,
  and the refusal when two waiting rotations declare the same App id. Five tests in
  `test_inbox.py` drive the worker and `bind`: a waiting rotation of another type, the same
  declared identity, a waiting rotation that can't be resolved, a value naming another identity,
  and a provider without the protocol.

### Request reach (item 10)

The adapter's confined client permits exactly `GET /app`, `GET /repos/<proof>/installation`,
`GET /installation/repositories`, and `DELETE /installation/token`. The fifth request in item 10,
`POST /app/installations/<id>/access_tokens`, is sent by `github_app_mint.mint` through its own
confined client. That client permits a POST to any numeric installation. "The declared id" is
therefore held by the adapter's own `GET /repos/<proof>/installation` check, which runs before the
mint, not by a permit. A mismatch refuses before anything is minted
(`test_an_installation_not_the_row_s_is_refused_before_anything_is_minted`). The working proof
runs in that order: the installation, then the mint, then the reach check. Item 4 lists the mint
first. The token is revoked in a `finally`, whatever the reach check found.

## The no-person approval routes (D-84 condition 2)

None of the routes in the following table would approve a `rotation-<row id>` package for a
`github-app-private-key` row. Line numbers are at `5c3631b0`, except the sds-hq file (sds-hq
`256bb8e0`).

| Route | Where it approves | What refuses a GitHub App key package | Test pinning the refusal |
|---|---|---|---|
| Standing approval, carry form (the proposer's CLI and the work carrier) | `src/rotation_proposer/standing_approval.py` `carry` (253), run by `src/work_carrier/standing.py` 128 | `lane_type` (187-191: the class isn't in `LANE_KINDS`, 38), `lane_destinations` (199-213), and `stated_issue_plan` (166-170: the type has no issue plan) | New: `tests/rotation_proposer/test_standing_approval.py::test_a_github_app_key_package_refuses_as_no_lane_type` (both rows) and `tests/work_carrier/test_standing.py::test_a_github_app_key_revision_the_policy_approved_is_refused` |
| Standing approval, decomposition gate | `src/orchestrator/services/intake/decomposition.py` 374-379 | `standing_approval.py` 70-71 (`_names_one_lane_type`, 94): the snapshot names neither lane provider. Also 69 (`not_revision_1`) for any later revision. | New case `github-app-key` (destinations `KEEPER, 'provider name="github"'`, with a carrier record present) in `tests/services/test_standing_approval_gate.py::test_a_package_naming_no_one_lane_type_refuses`. `not_revision_1` is pinned by `test_a_revision_other_than_1_refuses`. |
| The lane-credential policy actor (sds-hq `standing_approve.py`, approver `policy:non-software-operational@v<N>`) | Writes the same approver string, so in this repository it's the first two rows | The same two refusals. In sds-hq, `_LANE_PACKAGE` (87) and `lane_of` (863-869) also accept only `rotation-<lane>-bws-token` or `rotation-<lane>-ping-url` | The two rows' pins |
| The D-76 canary (sds-hq `standing-answers.md`, "one canary reissue per new executor home") | HQ may start one canary reissue: revision 2 or later of a `bws-lane-token` row with a `helper_target`. No code route in this repository carries that name. | A canary is a lane-token reissue, never this class. Any policy-approved revision 2 is refused at `standing_approval.py` 69. | `test_a_revision_other_than_1_refuses` |
| Any other `policy:` approver on the rotation branch | `decomposition.py` 375-376 | `revision_approved_by_policy` | `tests/services/test_rotation_decomposition_policy.py::test_a_revision_an_approval_policy_approved_waits_for_a_person` |
| The bump branch | `decomposition.py` 345, `bump_policy.py` 31 | `bump_policy.py` 40 `revision_not_dependency_update`: a rotation revision's profile is `non-software-operational` | `tests/services/test_bump_decomposition_policy.py::test_a_revision_of_another_profile_waits_for_a_person` |

None needed a fix, so I added only the pins.

**What these routes don't cover (concern 1).** `decomposition.py` 374 treats any approver without
the `policy:` prefix as a named person. That person is proved outside this repository: the
carrier matches the lineage's approval to a verified factory-events chain event
(`src/work_carrier/standing.py`, `_verified_approval`), and the chain's event actor is
self-declared (ADR-0055 amendment 24, "Threat model"). The broker's account holds a single-secret
read grant on the chain's append password (`.cred-consumers.toml`, `factory-events-append-password`,
the bump proposer's note, HQ G-109). The root reads the broker's token (amendment 34). So whoever
holds the root can reach the append password, and under 9 A the push App's `contents: write` on
intent-packages. That could produce a person-named approval without a person. This isn't a
no-person route, and the fix would be more than a refusal in a route. It's the reach that
amendment 34's Open item 10 and D-84's 9 A ("reopened in the security phase") already put to
Devon, so I didn't stop.

## Exit checks 1 to 3: what each still needs and who runs it

1. **The control's status.** D-84 14 A makes the first real rotation the measurement, so no status
   is recorded yet. That rotation's verify-new evidence files it (the `known_bad` probe's
   `http_status`), and a status other than 401 fails the step. The executor runs it; HQ reads the
   evidence.
2. **The working proof on the current keys, without a mint.** This needs a one-off run of
   `check_scope` against each keeper's current value, by the executor's process on `prod-vps`. That
   run proves the App ids, installation ids, proof repository, and the 204. No CLI runs it yet: an
   HQ-led session on the VPS host needs an entry point, or a one-line `python -c` against the
   adapter, with the root's store. The root can't read `SDS / Lane Apps` by its own grants, so this
   also waits on the keepers' move (8 A) or a read through the broker's keeper. HQ schedules it.
3. **One real rotation of each row.** This needs the keepers' move (8 A, Devon's act after Phase
   C's revoke sitting), then each row's `rotated_by_sds = true` with a `rotate_requested` date,
   which HQ writes. Devon approves each revision by name, generates both keys in one sitting, and
   deletes the old key pairs after each revoke alert. The executor does the rest.

## Tests and checks run

- **`make check`** on `orchestrator_test_hq_w_328`: MAKE_CHECK_RESULT.
- **Targeted runs**, all passing:
  - `tests/rotation_worker/test_github_app_key.py`: 35 tests.
  - `tests/rotation_worker/test_github_app_key_readers.py`: 45 tests.
  - `tests/rotation_worker/test_inbox.py`: 67 tests.
  - The three approval pins: 4 tests.
  - `tests/rotation_worker`, `tests/rotation_proposer`, `tests/estate_clients`, and the three
    rotation architecture files: 3,547 passed before the new files.
- **Mutation checks:** MUTATION_RESULT.
- **Token-shape grep of the diff:** I ran `git diff 5c3631b0..HEAD`, filtered to added lines, and
  grepped for BWS access tokens, PEM headers, base64-of-PEM headers (`LS0tLS1CRUdJTi`), `ghs_`,
  `ghp_`, and `github_pat_` tokens, and JWT headers (`eyJ`). There were no matches. Test keys are
  generated at run time (`_pem`, `_new_rsa_pem`), and no PEM is committed.
- **`code-standards risk --repo .`:** `normal`, so the vendor review isn't required.
- **`/code-review`:** CODE_REVIEW_RESULT.

## What a reader's green ping proves (item 5's carried check)

`tests/rotation_worker/test_github_app_key_readers.py` pins each of the 14 readers from its
launcher's text:

- the exit codes that ping success;
- each `exit` between arming the ping and the key's read;
- the line opening the block the read sits in.

It checks three properties:

- No literal early exit pings success. For example, the inert lander's empty-list `exit 2` pings
  `/fail`, because its finding code is 3.
- Every scheduled pass meets its read's guard.
- The unit verifier's tokenless pass reports 3.

A control test proves the derivation sees an `exit 0` placed before the read. Two paths give a
green ping without the read (concern 2).

## Files changed

- `src/rotation_worker/adapters/github_app_key.py` (new)
- `src/rotation_worker/adapters/__init__.py`, `src/rotation_worker/types.py`,
  `src/rotation_worker/inbox.py`, `src/rotation_worker/worker.py`, `src/rotation_worker/CLAUDE.md`
- `src/estate_clients/github_app_mint.py`
- `src/orchestrator/factory-policy.toml`
- `.cred-consumers.toml`
- `docs/operations/rotation-executor.md`
- `tests/rotation_worker/test_github_app_key.py` (new),
  `tests/rotation_worker/test_github_app_key_readers.py` (new),
  `tests/rotation_worker/test_inbox.py`, `tests/rotation_worker/test_client_surface.py`,
  `tests/rotation_worker/test_postgres_role.py`
- `tests/architecture/test_rotation_agreement.py`, `tests/architecture/test_rotation_edges.py`,
  `tests/architecture/test_wsp21_invariant_scan.py`
- `tests/rotation_proposer/test_standing_approval.py`, `tests/work_carrier/test_standing.py`,
  `tests/services/test_standing_approval_gate.py`
- `docs/superpowers/reports/github-app-key-adapter-code-report.md` (this report)

## Self-review

- **The public-half refusal reads the old key itself.** The `ScopeChecked` hook passes only the new
  value, so the provider reads the quarantine if there is one, else the keeper, as `UnitRun.old`
  does for a rotation. The alternative was changing the contract's hook, which item 15 doesn't
  list.
- **A JWT and a token never reach a message.** Every refusal names a request, a status, or an
  exception's type. A test collects every bearer GitHub received, every key form, every evidence
  `repr`, every refusal, and every log record, and checks that none contains another.
- **`probe` raises on a 200 for another App.** It doesn't return a status the contract would file
  as indeterminate. In the inbox, the worker catches that as "it couldn't be checked: …".
- **A JWT signed for this row's `app_id` by another App's key answers 401 in the simulated GitHub,**
  because GitHub checks the signature against the named App's keys. So a key pasted for the wrong
  App is refused as dead before the identity check runs.

## Concerns

1. **A person-named approval the root could write.** See "What these routes don't cover". It
   stands under 9 A and amendment 34's Open item 10.
2. **Two ways a reader's green ping proves less than a mint with the new key:**
   - Three launcher patterns pass a program's exit code through before the read: `"$scope_rc"`
     (landing ledger, revision watcher, pin watcher), `"$derive_rc"` (deploy watcher, inert and
     estate landing), and `"$registries_rc"` (rotation proposer). Such an exit pings success if it
     equals the lane's finding code, for example an argparse usage error's 2 from `sds_scope` on a
     finding-2 lane.
   - The rotation proposer pings every pass and reads the push key only on `--submit`, so a bare
     hand run pings success without reading it.

   No reader keeps a value, so neither path leaves a reader on the old key; each only weakens the
   revoke alert's timing. Both are pinned and documented in the runbook.
3. **The provider name is shared with the GitHub PAT adapter.** `check_unit` compares only the
   row's `provider` with the adapter's `provider_name`, which is `github` for both. Nothing ties an
   envelope's `rotation_adapter` to `CLASS_TYPES[row class]`, so an envelope naming `github-pat` on
   a key row would pass that check, and its probe would send the stored key to `GET /user` as a
   bearer. Reaching that needs a hand-made envelope in an approved package: the decomposer writes
   the adapter from the type. The rows aren't `rotated_by_sds` yet, so the path isn't open. I
   recommend a `check_unit` term (the adapter must be the row's class type, or a fixed-plan name of
   that type) before either row is handed over.
4. **Exit check 2 has no entry point.** See the exit checks section.
5. **Standards the handoff doesn't cover.** The inbox change (item 6 B) is a check's form, settled
   by HQ, and no Code Brain road covers it. I kept it to the existing protocol pattern
   (`BindsByItself`).

## Standards followed

- **Code Brain 3 (logging).** The adapter adds no logger. `worker._revoke_guide` logs one
  structured line (`revoke_guide_unread`) through the executor's existing `LOG`, with the reason
  scrubbed. There is no file logging.
- **Code Brain 4 (error handling).** Nothing is swallowed:
  - A transport failure returns `None` from the probe (indeterminate, which fails the step), or
    raises a `ContractError` naming the failure's type in the working proof. Either fails the unit.
  - An unexpected status, a mismatched App or installation id, a reach that isn't the proof
    repository, and a token not revoked each fail the step, naming what happened.
  - `_reach_problem` and `_revoke_token` return their problem rather than raise, so the token is
    always revoked, and both problems are raised together.
  - The one fallback (`_revoke_guide`) logs and sends the generic alert text with the reason.
- **Code Brain 5 (security).** The PEM, the JWT, and the installation token appear in no log line,
  exception message, or evidence field:
  - `MintRefused` text is replaced, not echoed.
  - The installation token is a `Value`, so `scrub` knows it.
  - Evidence carries fingerprints and statuses only.
  - The revoke alert carries only 8 characters of a public-key hash.
  - No response body is kept.
  - The no-leak test checks every form.
- **Infra Brain 591, 592, 593.** No token, key, or token-shaped literal is in the code, tests,
  fixtures, or docs. Test keys are generated at run time, and the token-shape grep of the diff is
  clean.
- **Infra Brain 1032 (`cred.exposure-rotate`).** Each row's exposure and consumer fields stay
  valid: no `[[credential.exposure]]` was added, and the consumers are unchanged. The adapter sets
  `revoke_may_come_first`. The revoke alert waits on every reader's machine evidence.
- **Infra Brain 212 (HTTPS for public FQDNs).** Not applicable: the adapter adds no endpoint. It
  calls only `https://api.github.com`, through a confined client that permits four requests, and
  the mint's POST goes through `github_app_mint`'s confined client.
