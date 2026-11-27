Outcome: done

# Build report: the Anthropic API-key rotation adapter (ADR-0055 Amendment 36 item 13, D-85)

Branch `hq/anthropic-key-adapter-code`, based on orchestrator `origin/main` at `e7016cb8`.
PR: PR_URL_PLACEHOLDER (head `HEAD_PLACEHOLDER`). Not merged: HQ reviews and merges.

No live call was made to Anthropic, BWS, Coolify or Healthchecks, and no production act was taken.
Every test runs against fakes.

## What I built

The `anthropic-api-key` class's adapter, `anthropic-key`, with Bitbucket's shape: a person creates
a key in the Claude Console and pastes it into the inbox, the executor proves and stores it, waits
for the reader's green ping, and after the person deletes the old key proves it refused.

### Each change against item 13's list

| Item 13 | Change |
|---|---|
| `adapters/anthropic_key.py` | New. `NAME = "anthropic-key"`, `PROVIDER = "anthropic"`, `PLAN = derive(TYPES[NAME].capabilities, (), "", "bws-secret")`. The probe (`GET /v1/models`, key as `Authorization: Bearer`, `anthropic-version: 2023-06-01`, no `anthropic-workspace-id` request header), the control (the regular-key prefix plus random characters of the key alphabet, as long as the old key, built in memory), `dead_statuses = {401}`, the identity check (`same_identity`: both answer 200 with `anthropic-workspace-id` equal to the row's `workspace_id`), `check_row` (item 8), `revoke_guide` (item 5's alert text), and `mint`, `host`, `consumer_loaded`, `revoke` raising `ContractError`. |
| `adapters/__init__.py` | The import and an `ADAPTERS` entry: provider `anthropic`, `capabilities=TYPES["anthropic-key"].capabilities`, `check_row=anthropic_key.check_row`, `revoke_may_come_first=True`. |
| `types.py` | `TYPES["anthropic-key"] = Capabilities(machine_mint=False, machine_revoke=False)` (store `bws-secret`) and `CLASS_TYPES["anthropic-api-key"] = "anthropic-key"`. |
| `test_rotation_agreement.py` | `anthropic-api-key` leaves `UNTYPED`. |
| `test_rotation_edges.py` | A `PROVIDER_EDGES["anthropic-key"]` row: host `https://api.anthropic.com`, the one route `("GET", "/v1/models")`. Also, as #826 needed for its adapter: the import, `_WORKER_CLIENTS` (one client), `/v1/models` in `ROUTES`, and `anthropic` in `NO_ADMIN_SECRET`. |
| `test_wsp21_invariant_scan.py` | `src/rotation_worker/adapters/anthropic_key.py` in `OUTBOUND_ALLOWLIST`, with its reason (D-85 7 A, the one request, pinned by `test_anthropic_key.py`). |
| `layers.toml` | No change: `src/rotation_worker/**` (line 652) and `tests/rotation_worker/**` (653) already name both new files. |
| `factory-policy.toml` | `anthropic-key` in both `rotation_adapters` lists, with one sentence in each pattern's description beside `github-app-key`'s. |
| `.cred-consumers.toml` | Only the `anthropic-change-window-key` row's block comment: "No adapter rotates this class" is replaced by the route and what comes before `rotated_by_sds`. No field changes: no `workspace_id` (exit check 1 reads it), no `rotated_by_sds`, no `rotate_requested`, no `[[credential.exposure]]`. |
| `docs/operations/rotation-executor.md` | A section, "Anthropic API keys": create a key (item 1's mint note: in the workspace, never organization-wide), what verify-new proves, delete the old key (item 5's note: Delete, not Disable, and the stray-key gap), the registry row, and the T1 note. The inbox's "same account" bullet names the workspace check. |
| The threat model's T1 note | In the same section, "What the root reaches through this keeper", in the form `docs/operations/secrets-broker.md` uses for D-83's T1 note. I didn't edit ADR-0055's threat-model table: an ADR's earlier text changes only by amendment. If HQ wants the note in the ADR, it's a one-paragraph amendment. |
| The reader's test | `tests/scripts/test_run_change_window.py::test_a_green_ping_proves_the_anthropic_key_was_read_not_that_anthropic_accepted_it`: a `--submit` success ping follows a pass that read the key from its keeper and handed it to the window; a pass whose items all failed still pings success; an unreadable or empty key pings `/fail` before the window; a bare pass reads no key and pings nothing. |

Files outside the list that the change needed, as #826's did: `tests/rotation_worker/test_client_surface.py`
(`HTTP_CLIENTS`), `tests/rotation_worker/test_postgres_role.py` (`EXISTING_PLANS`), and the three
approval-route pins (following section). New test file: `tests/rotation_worker/test_anthropic_key.py`
(35 tests).

### How D-85's answers show in the code

- **7 A.** `build_provider` builds one `ConfinedClient` on `https://api.anthropic.com` whose
  `permits` is `(method, path) == ("GET", "/v1/models")`.
  `test_the_client_permits_only_get_v1_models` refuses another method on the path, other paths
  (`/v1/messages`, the Admin API's key routes, `/v1/models/…`, a query string), and an absolute
  URL or `//host` form naming another host, with no request sent; then the one permitted request
  reaches `api.anthropic.com` only. `test_rotation_edges.py`'s row holds the same bound from the
  architecture side.
- **10 A.** The type is `machine_mint=False, machine_revoke=False`; `revoke` raises, and the client
  can't reach any Admin API route. The adapter never lists, disables or deletes a key.
- **11 A.** No new key: every test uses a fake Anthropic. `dead_statuses = {401}`: verify-new fails
  with the control's status named if it isn't 401 (`…control_anthropic_doesn_t_refuse_with_401…`,
  400, 403 and 404), and confirm fails with the deleted key's status named if it isn't 401, leaving
  the quarantine in place (`…deleted_key_anthropic_doesn_t_refuse_with_401…`, 403 and 404).

### Design points the amendment left to the code

- **The workspace check is in the probe.** `contract._verify_new` calls only the probe, and
  `same_identity` runs only in the inbox's bind, so a check only in `same_identity` would let a key
  staged by hand (`rotation-worker stage`) through to `store`. The probe refuses a 200 that names
  no workspace or another one, as `github_app_key.probe` refuses another App's 200. Item 1 says
  verify-new covers items 2 and 3, so this is that. Tests: a key from another workspace staged by
  hand fails `store` by name with the keeper unchanged, and a 200 with no header does too.
- **The control's length.** `known_bad()` takes no subject, so the control copies the length of
  the old value (the quarantine once stored, the keeper before), read through the store as
  `github_app_key`'s `old()` reads it. The alphabet is the key body's (`A-Z a-z 0-9 - _`).
- **The revoke alert's date.** The executor can't read a Console creation date, so the alert names
  the day the executor stored the new key (`store.revised(keeper_id)`, UTC) and says to keep the
  key created for this rotation. It names the workspace and says Delete, not Disable.
- **A workspace id in a refusal.** A refusal names the row's `workspace_id`, and the answered one
  only if it has a `wrkspc_` id's shape; otherwise it says "(a malformed id)". Refusals go to the
  unit's fail reason and alerts, never to evidence, so the evidence scan's 32-character rule
  doesn't apply to them; no evidence note carries a workspace id.

## The no-person approval routes (D-84 condition 2)

None would approve a `rotation-anthropic-change-window-key` package. Lines are at `e7016cb8`,
except sds-hq's (`e0a252b7`). No route needed a fix, so I added only the pins.

| Route | Where it approves | What refuses this package | Pin |
|---|---|---|---|
| Standing approval, carry form (the proposer's CLI and the work carrier) | `src/rotation_proposer/standing_approval.py` `carry` (253), run by `src/work_carrier/standing.py` 128 | `lane_type` (187-191: `anthropic-api-key` isn't in `LANE_KINDS`, 38), `lane_destinations` (199-213), `stated_issue_plan` (166: the type has no issue plan) | New: `tests/rotation_proposer/test_standing_approval.py::test_an_anthropic_key_package_refuses_as_no_lane_type` and `tests/work_carrier/test_standing.py::test_an_anthropic_key_revision_the_policy_approved_is_refused`, both on the real row |
| Standing approval, decomposition gate | `src/orchestrator/services/intake/decomposition.py` 374-379 | `services/intake/standing_approval.py` 70-71 (`_names_one_lane_type`, 94): the snapshot names neither lane provider; 69 (`not_revision_1`) for any later revision | New case `anthropic-key` (`KEEPER, 'provider name="anthropic"'`, with a carrier record present) in `tests/services/test_standing_approval_gate.py::test_a_package_naming_no_one_lane_type_refuses` |
| The lane-credential policy actor (sds-hq `src/sds_hq/standing_approve.py`) | Writes the standing approver string, so here it is the first two rows | It runs `rotation-proposer standing-approval <credential>` and passes only on exit 0 (631-633), the CLI the first pin holds refusing; it refuses any revision but 1 (416-417) and any reissue history (`check_first_issue`, 433) | The first two rows' pins |
| The D-76 canary (sds-hq `standing-answers.md` 222) | No code implements it; it's HQ's act for one `bws-lane-token` reissue. With a `policy:` approver, `standing_approval.py` 69 and the carry form refuse a revision 2 and this class alike | Its scope is the rule's text only, so nothing here can pin it | `test_a_revision_other_than_1_refuses` (existing) |
| Any other `policy:` approver | `decomposition.py` 375-376 | `revision_approved_by_policy` | Existing: `tests/services/test_rotation_decomposition_policy.py::test_a_revision_an_approval_policy_approved_waits_for_a_person` |
| The bump branch | `decomposition.py` 345, `bump_policy.py` 40 | `revision_not_dependency_update`: a rotation revision's profile isn't a bump's | Existing: `tests/services/test_bump_decomposition_policy.py::test_a_revision_of_another_profile_waits_for_a_person` |

D-84's condition 2 doesn't arise: the key writes nothing the executor trusts. #826's concern 1 (an
approval written with a non-`policy:` approver reads as a person's) applies here unchanged.

## What exit checks 1 and 2 still need, and who runs each

1. **Exit check 1, the probe on the current key, without a mint.** A one-off run, by the executor
   on the VPS host (as its root identity, which reads `Rotation / Keepers`), of the probe and the
   control against the keeper's current value. It records only the statuses (the key's 200, the
   control's 401) and the `anthropic-workspace-id` header's value. Devon then confirms in the
   Console that the id is `sds-change-window`'s, and a PR adds it to the row as `workspace_id`.
   - **No entry point exists for it.** `rotation-worker`'s CLI builds providers only for `fake`
     and `postgres-role`, and `build_provider` refuses a row without `workspace_id`, so the probe
     can't run on today's row through any command. It would take a small `rotation-worker`
     subcommand (or a one-off script run in the executor's venv) that reads the keeper by UUID
     through the executor's `SecretStore`, builds the same `ConfinedClient` (the module's `API`,
     `MODELS_PATH`, `HEADERS`), sends the key and a `known_bad`-shaped control, and prints the two
     statuses and the header only. The request and header read are `AnthropicKeyProvider._models`,
     which needs no declared workspace, so such a command reuses it. I didn't build one (the
     handoff says not to).
   - If the header is absent on `GET /v1/models`, the identity check comes back as its own
     amendment (amendment 36 item 13's exit check 1), and the row stays by hand.
2. **Exit check 2, one real rotation.** After exit check 1's PR, a PR sets
   `rotated_by_sds = true` and a `rotate_requested` date; the rotation proposer generates
   `rotation-anthropic-change-window-key`, and Devon approves its revision by name. Then Devon
   creates the key in the Console and pastes it; the executor binds, proves and stores it; the
   lane's next `--submit` pass pings green; Devon deletes the old key on the revoke alert; and
   `confirm` records the deleted key's status. If it isn't 401, `confirm` fails with the status
   named and the unit is a person's.

## Tests and checks

- `make check` at commit `d8c8b110`, on a clean tree, against
  `orchestrator_test_hq_w_332`: exit 0. Ruff and the formatter clean (1,057 files), pyright 0
  errors, and **15,069 passed, 1 skipped** collected in one run.
- Before it, the targeted run (`tests/architecture`, `tests/rotation_worker`,
  `tests/rotation_proposer`, `tests/work_carrier`, the gate tests and the launcher tests): 4,538
  passed.
- `ruff check` and `ruff format --check` on every changed Python file: clean.
- **Token-shape grep of the diff** (`git diff e7016cb8`), for the scanner's Anthropic and OpenAI
  pattern `sk-(ant-)?[A-Za-z0-9_-]{20,}` and the BWS token shape: no match. The only `sk-ant`
  string is the prefix constant `KEY_PREFIX = "sk-ant-api03-"`, which has no body. Test keys,
  controls and workspace ids are built at run time from `secrets`.

## Files changed

- `src/rotation_worker/adapters/anthropic_key.py` (new)
- `src/rotation_worker/adapters/__init__.py`, `src/rotation_worker/types.py`
- `src/orchestrator/factory-policy.toml`
- `.cred-consumers.toml` (the row's comment only)
- `docs/operations/rotation-executor.md`
- `tests/rotation_worker/test_anthropic_key.py` (new), `test_client_surface.py`,
  `test_postgres_role.py`
- `tests/architecture/test_rotation_agreement.py`, `test_rotation_edges.py`,
  `test_wsp21_invariant_scan.py`
- `tests/scripts/test_run_change_window.py`
- `tests/rotation_proposer/test_standing_approval.py`, `tests/work_carrier/test_standing.py`,
  `tests/services/test_standing_approval_gate.py`
- This report.

## Self-review

- Every step runs through `contract.py`; the adapter re-implements none.
- The workspace refusal raises from `probe`, so it fails `verify_new`, `confirm_dead` (when the new
  key is probed) and the inbox's bind alike. The inbox catches it as a refusal and leaves the value
  in the inbox (`worker._bind_inbox` catches `REFUSALS`).
- `test_no_evidence_refusal_or_log_line_carries_a_key_or_a_control` runs a rotation and three
  failing confirms (403, no header, no answer) and checks that no evidence row, refusal or log line
  carries the old key, the new key, any control sent, or a 16-character slice of any of them.

- **Mutations**, each run against `tests/rotation_worker/test_anthropic_key.py` with
  `__pycache__` cleared and the source restored after: the client permitting any `GET` (1 test
  failed), the probe's workspace check removed (5 failed), and `dead_statuses` widened to
  `{401, 403}` (2 failed). None survived.
- **`code-standards risk`**: `normal`. **`/code-review`** (medium): six findings, none changed the
  code. Two it rated important are the amendment's own unmeasured points: Bearer rather than
  `x-api-key` (concern 1), and whether `GET /v1/models` sends `anthropic-workspace-id` at all.
  Exit check 1 measures both before the row declares a workspace, and the adapter fails closed on
  either (a 401 for a live key, or a 200 with no header, stores nothing). The nits: a wrong-workspace
  200 on the old key raises rather than reading as live (the old key was bound in the row's
  workspace, so that answer means something else is wrong, and failing is right); the typed row
  refused by `check_row` (refused only before a claim, and the row isn't `rotated_by_sds`, so no
  package is generated; the full suite has no alert or intake test that trips); the alert's date
  moving if the keeper is edited after `store` (nothing writes the keeper between `store` and the
  revoke); and the control's extra secret read (the amendment asks for the subject's length).

## Concerns

1. **Bearer or `x-api-key`.** The amendment's probe sends the key as `Authorization: Bearer`, from
   the List Models page's example, and I built it that way. Anthropic's API reference generally
   shows a regular API key in the `x-api-key` header, with `Authorization: Bearer` for OAuth
   tokens. If a regular key isn't accepted as a bearer on `GET /v1/models`, exit check 1 records a
   non-200 for the current key and nothing rotates: the probe fails closed, and switching the
   header is a one-line change plus the tests that pin it. Exit check 1 measures it.
2. **Exit check 1 has no entry point** (preceding section): a small CLI or script is needed before
   it can run.
3. **The T1 note's home.** I put it in the operations page, as `secrets-broker.md` holds D-83's,
   not in ADR-0055's threat-model table.
4. **The registry's first line number moved.** The handoff cited the row at line 1491 and the
   amendment the comment at 1831-1832; at `e7016cb8` the row's comment is at 1485-1489, directly
   above the row, and that's the one I changed.

## Standards followed

- **Code Brain 3 (logging).** The adapter adds no logger and no file logging; the executor's
  existing structured logging is the only output path.
- **Code Brain 4 (error handling).** No exception is swallowed. A transport failure returns `None`
  from the probe, which the contract files as indeterminate and fails the unit. An unexpected
  status fails the step with it named; a missing, malformed or mismatched workspace header raises
  `ContractError` naming it. There is no bare `except …: pass`; the one `except TransportFailure`
  in `build_provider` re-raises as a `ContractError` naming the failure's type.
- **Code Brain 5 (security).** The key, the control and the `Authorization` header appear in no
  log line, exception message or evidence field; evidence carries fingerprints only, through
  `contract.py`; no response body is read. Pinned by the leak test (Self-review). The workspace id
  may appear in a refusal, as item 9 allows, and only in a `wrkspc_` id's shape.
- **Infra Brain 591, 592, 593 (no token in tracked files or history, no inline bootstrap token).**
  No key, token or token-shaped literal is committed (Tests and checks, the grep). Test keys are
  built at run time.
- **Infra Brain 1032 (exposure, then rotate).** The row's consumer fields are unchanged and still
  valid for `check_row` once `workspace_id` is declared (pinned by
  `test_the_row_declares_no_workspace_yet_and_stays_out_of_the_sds_s_hands`); no
  `[[credential.exposure]]` was added. The revoke alert is sent only after the reader's green ping.
- **Infra Brain 212 (HTTPS for public FQDNs).** Not applicable: the adapter adds no endpoint, and
  its one outbound request goes to `https://api.anthropic.com` through a confined client.
