Outcome: done

# Build report: rotation adapters' hand-over entry points (ADR-0055 Amendments 35 and 36)

Worker W-333, branch `hq/rotation-adapter-handover-entry-points`, based on `a8cb546e`. PR
[#830](https://github.com/AlobarQuest/orchestrator/pull/830). The code head is `6444eebc`; this
report's commit follows it. Nothing is merged: HQ reviews and merges. No live call to Anthropic,
GitHub, BWS, Coolify, or Healthchecks was made, and no production act was taken.

## What I built

### 1. The `check_unit` class term (#826 concern 3)

- `src/rotation_worker/adapters/__init__.py:301` `FIXED_PLAN_TYPES` maps each fixed-plan name to
  its type: `orchestrator-distributed` to `orchestrator`, and `change-manager-attested` and
  `change-manager-distributed` to `change-manager`.
- `src/rotation_worker/adapters/__init__.py:308` `runs_on_class(adapter_name, credential_class)`
  passes when the adapter is `CLASS_TYPES[class]` or a fixed-plan name of that type. The canary's
  class (`rotation-canary`, `src/rotation_worker/adapters/fake.py:40`) has no type, and `fake` runs
  on it alone. An unknown or missing class passes no adapter.
- `src/rotation_worker/checks.py:159` `_check_handed_to` holds the `rotated_by_sds`, provider, and
  class terms, called at `checks.py:121`. I extracted it because the new term pushed `check_unit`
  over ruff's complexity limit. The refusal is `the <adapter> adapter doesn't run a '<class>' row:
  it runs its type's class alone`. It names the adapter and the class, never a value.
- `tests/rotation_worker/test_checks.py`: the `World` fixture's rows now carry their real class
  (`_registry(credential_class=...)`), since it wrote `rotation-canary` under the `postgres-role`,
  `orchestrator`, and `github-pat` adapters. A new test,
  `test_an_adapter_of_another_class_sharing_the_row_s_provider_is_refused`, runs `github-pat` on a
  `github-app-private-key` row and the reverse, both with provider `github`, through `check_unit`.

### 2. `rotation-worker probe <credential-id>` (#826 concern 4, #829 concern 2)

- `src/rotation_worker/cli.py:501` `probe`, and `PROBES` at `cli.py:114`, one entry per class.
  The verb:
  1. Reads the row at each registry repository's `main` (`find_credential`, which doesn't check
     `rotated_by_sds`).
  2. Refuses any class but `anthropic-api-key` and `github-app-private-key` before it opens the
     store.
  3. Opens the instance's own store (`open_store`, the root's token) and locates and reads the
     keeper inside one guard. On any failure it prints only `probe: <id>'s keeper is not readable
     by this identity` and exits 2.
  4. Calls the adapter's `probe_current` and prints one scrubbed JSON line to stdout.

  It never opens the orchestrator client or `build_setup`, so it reads no bearer and records
  nothing.
- Anthropic, `src/rotation_worker/adapters/anthropic_key.py`:
  - `confined_client` (line 109) is the client `build_provider` used inline before. It's lifted out
    unchanged: it permits `GET /v1/models` alone.
  - `control_like` (line 125) is the `known_bad` generator, lifted out.
  - `models` (line 132) is the old `_models` request, with the header form as a parameter that
    defaults to `x-api-key`. It refuses any other header name before it reveals the key.
  - `probe_current` (line 146) builds no provider, needs no `workspace_id`, and sends the key and
    a control once in each header form.
  - The provider's `_models` (line 249) now calls `models` with the default form.
- GitHub App, `src/rotation_worker/adapters/github_app_key.py`:
  - `confined_client` (line 136) is lifted out of `build_provider` with the same permitted set.
  - `check_scope` (line 317) keeps its old-key comparison and then calls the new `working_proof`
    (line 324), which holds the rest of its body unchanged. The real route sends the same requests.
  - `probe_current` (line 162) builds a provider whose `old` raises, and calls only
    `working_proof`, because a kept key is its own "old" key and the comparison would refuse it.
- `check_row` and `build_provider`'s refusals are unchanged.
  `test_build_provider_and_check_row_still_refuse_the_row_without_a_workspace` shows both still
  refuse the Anthropic row without `workspace_id`.
- `cli.py` imports no HTTP client. The executor's client-surface guard
  (`tests/rotation_worker/test_client_surface.py`) and `test_rotation_edges.py`'s network table
  forbid it there. So the verb takes no transport, and its tests answer at
  `httpx.HTTPTransport.handle_request`, behind the adapters' real clients.

### 3. The Anthropic header (#829 concern 1)

- The rotation sends the key as `x-api-key` with `anthropic-version`. `probe_target` reads `with
  the key as x-api-key`, and the module docstring states the form and the reason.
- `tests/rotation_worker/test_anthropic_key.py`: the simulated Anthropic takes `x-api-key`, and
  takes a bearer only with `takes_bearer=True`. The pinning test is
  `test_the_probe_sends_the_key_as_x_api_key_with_a_version_and_nothing_else`, which also asserts
  no `Authorization` header.
- ADR-0055 Amendment 36 gains one line after its exit checks (line 6589): the change, its source,
  and that exit check 1 measures both forms. No other ADR text changed.
- `tests/architecture/test_wsp21_invariant_scan.py`: the Anthropic adapter's `OUTBOUND_ALLOWLIST`
  reason now says `x-api-key` and names the probe.
- `docs/operations/rotation-executor.md`: the Anthropic section's request and hand-over text, and
  a section, "Probe a kept key once" (line 1310): the host command, exit codes, and what each exit
  check reads from its line.

## The regression test's rows and envelope shapes

`tests/rotation_worker/test_adapter_class.py` runs the real `rotation_decomposer.decomposition` and
feeds every envelope's `rotation_adapter` to `runs_on_class`.

- **Every `rotated_by_sds = true` row in this repository's `.cred-consumers.toml` at `a8cb546e`**,
  with issue and reissue variants where the type has both:
  - `bws-sds-operator-token` (`bws-machine-token`).
  - `orchestrator-operator-bearer`, `orchestrator-observer-bearer`,
    `orchestrator-drift-reporter-bearer`, `orchestrator-factory-runner-bearer`
    (`orchestrator-m2m-bearer`).
  - `secrets-broker-bws-token` (`bws-lane-token`), issue and reissue.
  - The 17 ping URLs (`healthchecks-ping-url`): `execution-watch`, `landing-ledger`,
    `deploy-watcher`, `revision-watcher`, `unit-verifier`, `pin-watcher`, `membership-check`,
    `change-proposer`, `rotation-decomposer`, `work-carrier`, `bump-proposer`,
    `rotation-proposer`, `inert-landing`, `estate-landing`, `bump-driver`, `follow-up-mint`, and
    `change-window`, each `-ping-url`.
  - `rotation-canary` (`rotation-canary`): the one row with no type. The decomposer refuses it, so
    the test asserts it's the only untyped row and that `fake` runs its class.
- **A row of every class in `CLASS_TYPES`** (`test_decompose.py`'s `CASES`, both variants where a
  type has them). This covers the other three registries, whose files aren't in this repository.
  I read their `rotated_by_sds = true` rows read-only through the GitHub API at `main`, and every
  class is in `CLASS_TYPES`:
  - `infraops-mcp-server@0fe5b92953c1`: `github-finegrained-mirror`, `bitbucket-mirror-token`,
    `openrouter-generic`, `bws-tok-ops-mini-20260730`, `bws-tok-content-mini`,
    `bws-tok-brain-ops`, and `alobar-id-postgres`.
  - `change-manager@ebd43ee42ec5`: the four `change-manager-*-bearer` rows.
  - `factory-runner@5aadb110b37f`: `factory-pr-token`.
- **The fixed-plan names**: each runs on its type's class and on no other class.
- **The open unit**: `secrets-broker-bws-token`'s reissue `confirm` envelope passes
  (`test_the_unit_open_on_the_host_passes`).
- **Guards against silent drift**: `ADAPTERS` equals the types, the fixed-plan names, and `fake`.
  No adapter but `fake` runs an untyped class, `None`, or an unknown class.

`postgres-role` and `bws-lane-token`, which the handoff names as fixed-plan names, are type names:
the decomposer writes them as the type, and the walk covers them.

## The probe's output, per adapter

The Anthropic probe sends `GET https://api.anthropic.com/v1/models` four times, in this order: the
key as `x-api-key`, the key as `Authorization: Bearer`, then a control of the key's shape in each
form. Each request carries one header form, never both. The control is `known_bad`'s generator,
in memory only.

```json
{"credential_id": "anthropic-change-window-key", "class": "anthropic-api-key", "keeper": "<uuid>",
 "key_x_api_key": {"status": 200, "anthropic-workspace-id": "wrkspc_..."},
 "key_bearer": {"status": 401, "anthropic-workspace-id": null},
 "control_x_api_key": {"status": 401, "anthropic-workspace-id": null},
 "control_bearer": {"status": 401, "anthropic-workspace-id": null}, "passed": true}
```

A request with no answer reads `{"status": null, "no_answer": "<httpx exception type>"}`. A
workspace header not in the `wrkspc_` shape reads `(a malformed id)`. `passed` holds when the key
answers 200 as `x-api-key` with an `anthropic-workspace-id`, both controls answer 401, and all
four requests answered. That is Amendment 36's exit check 1 verdict: a 429 or 5xx is indeterminate
(item 9), and a 200 with no workspace can't bind.

The GitHub App probe sends `check_scope`'s calls as the rotation sends them:
1. `GET /repos/<proof>/installation`.
2. The mint's own installation read.
3. `POST /app/installations/<id>/access_tokens` with `metadata: read`, for the proof repository.
4. `GET /installation/repositories`.
5. `DELETE /installation/token`, whatever the reach check found.

It doesn't send `GET /app`.

```json
{"credential_id": "lanes-read-app-private-key", "class": "github-app-private-key",
 "keeper": "<uuid>", "app_id": <int>, "installation_id": <int>,
 "proof_repository": "<owner/name>", "status": "ok", "passed": true}
```

A refused proof reads `"status": "refused"`, `"passed": false`, and a `refusal` naming requests,
statuses, or ids.

The exit codes are 0 when `passed` is true, 1 when the line is printed with `passed` false, and 2
when the probe can't run: an unreadable row, another class, a store that can't open, an unreadable
keeper, a row missing its ids, or an unforeseen failure (`probe: <id> unprobed: <class>`). On exit
2, nothing goes to stdout.

## What each exit check's session runs

On `prod-vps`, as root through infraops' `vps_exec`, as the `rotation` user with only `PATH` and
`HOME`, so the store is the root identity's:

```sh
runuser -u rotation -- env -i PATH=/usr/bin:/bin HOME=/var/lib/rotation-worker \
  /opt/rotation-worker/venv/bin/rotation-worker probe <credential-id> \
  --config /etc/rotation-worker/config.toml
```

The host must first run a commit that has the verb ("Install or update it on the VPS host").

- **Amendment 36 exit check 1**: `probe anthropic-change-window-key`. It reads three things:
  - `key_x_api_key.status` 200 beside `control_x_api_key.status` 401: the rotation's form works.
  - `key_bearer` beside `control_bearer`: the sources' disagreement, settled.
  - `key_x_api_key`'s `anthropic-workspace-id`: Devon confirms it's `sds-change-window`'s before a
    PR writes it as `workspace_id`.
- **Amendment 35 exit check 2**: `probe` on each `github-app-private-key` row, reading `status`
  and the ids. Until Devon moves the keepers out of `SDS / Lane Apps` (D-84 item 8 A), the root
  can't read them, and the probe answers exit 2, "not readable by this identity". It never reads
  through the broker's keeper.

## Tests and checks run

- **`make check`** on `orchestrator_test_hq_w_333`, clean tree at `6444eebc`: exit 0. Ruff,
  format (1,059 files), and pyright are clean. Pytest: **15,154 passed, 1 skipped** (5 minutes 17
  seconds). Earlier runs failed and each was fixed:
  - Run 1: `cli.py` imported `httpx`, which the client-surface and rotation-edges guards refuse. I
    removed the import, and the verb takes no transport.
  - Run 3: `test_the_github_probe_sends_the_working_proof_s_calls_alone` depended on test order.
    `scrub` redacts every value the process has wrapped, so another test's `Value("v")` turned
    `private` into `pri[redacted]ate` in the line. The probe tests now get their own touched-value
    set, as `test_postgres_role.py` does. With `Value("v")` touched first, the tests pass with the
    fixture and fail without it.
- **CI on PR #830** at `b73dc764`: all three checks pass (Quality, Rollout transcription
  currency, Runner consumer compatibility). The Quality job collected 15,155 tests: 15,141 passed
  and 14 skipped (run 46072358177). The total matches the local run; CI's environment skips more.
- **Targeted runs**: `test_adapter_class.py` (60 passed), `test_probe.py` (23), and
  `test_anthropic_key.py` (35). Also `test_github_app_key.py` (36), `test_checks.py` (63), and the
  client-surface, rotation-edges, and outbound scans (1,329 with `test_probe.py`).
- **Mutation**: 9 mutations, 9 killed, at `6444eebc`. Each was applied, its tests run with `-x`, then reversed by
  inverse edit with `__pycache__` cleared, ending on a clean tree.

  | Mutation | Killed by |
  |---|---|
  | M1: the class term disabled (`if False and not runs_on_class(...)`) | `test_checks.py` (the new refusal test) |
  | M2: the rotation's default header set to Bearer | `test_anthropic_key.py` |
  | M3: the probe sends one header form | `test_probe.py` |
  | M4: the line carries the key, unscrubbed | `test_probe.py` |
  | M5: the unreadable-keeper refusal prints its cause | `test_probe.py` |
  | M6: the probe writes the keeper back | `test_probe.py` (writes nothing) |
  | M7: the probe calls `check_scope` instead of `working_proof` | `test_probe.py` |
  | M8: the verdict ignores whether every request answered | `test_probe.py` (the lost bearer request) |
  | M9: the verdict takes any control refusal, not 401 | `test_probe.py` (the control's 429) |

- **`code-standards risk`**: `normal`, so `/code-review` alone. It ran at medium on `main...HEAD`
  and returned four nits:
  - The keeper's `locate` was outside the guard: fixed, with a test.
  - Exit 0 meant only that every request answered: fixed, with the `passed` verdict and a test.
  - The header changed before the probe measured it: HQ's settled call (item 3), and it fails
    closed.
  - The class term could silently refuse a new name: the walk's drift guards already cover it.

  An advisor review of the fix round found that the first `passed` verdict read only the
  rotation's form, so a lost bearer request, a control's 429, or a 200 naming no workspace exited
  0. It also found that an unforeseen exception escaped with a traceback. Both are fixed and
  pinned (M8, M9, and `test_an_unforeseen_failure_exits_2_naming_only_its_class`).
- **Token-shape grep of the diff** (1,460 added lines against `a8cb546e`, the report included): 0 Anthropic-key
  shapes, 0 PEM blocks, 0 BWS-token shapes, 0 `ghs_` or `ghp_` literals, 0 base64 runs of 60 or
  more characters. The one `PRIVATE KEY` hit is the assertion `"PRIVATE KEY" not in shown`. Test
  keys are built at run time from `KEY_PREFIX` and random characters, and from generated RSA keys.

## Files changed

| File | Change |
|---|---|
| `src/rotation_worker/adapters/__init__.py` | `FIXED_PLAN_TYPES`, `runs_on_class`. |
| `src/rotation_worker/adapters/fake.py` | `CLASS`, the canary's class. |
| `src/rotation_worker/checks.py` | `_check_handed_to` with the class term; docstring bullet. |
| `src/rotation_worker/adapters/anthropic_key.py` | `x-api-key`; `confined_client`, `control_like`, `models`, `probe_current`. |
| `src/rotation_worker/adapters/github_app_key.py` | `confined_client`, `working_proof`, `probe_current`. |
| `src/rotation_worker/cli.py` | The `probe` verb and `PROBES`; module docstring. |
| `tests/rotation_worker/test_adapter_class.py` | The regression walk (new). |
| `tests/rotation_worker/test_probe.py` | The probe's tests (new). |
| `tests/rotation_worker/test_checks.py` | Rows carry their class; the refusal test. |
| `tests/rotation_worker/test_anthropic_key.py` | `x-api-key`; `takes_bearer`; `_sent`. |
| `tests/architecture/test_wsp21_invariant_scan.py` | The Anthropic allowlist reason. |
| `docs/operations/rotation-executor.md` | The Anthropic request text; "Probe a kept key once". |
| `docs/decisions/0055-…md` | One line in Amendment 36. |

`.cred-consumers.toml` and ADR-0059 are unchanged. `layers.toml` needs nothing:
`tests/rotation_worker/**` and `src/rotation_worker/**` cover the new files.

## Self-review

- The probe reuses each adapter's request code rather than copying it. The Anthropic request is
  one function, `models`, which the provider and the probe both call. The GitHub proof is
  `working_proof`, which `check_scope` calls. The real route's requests are byte-for-byte as
  before, except the Anthropic header.
- The term is a closed table, not a rule derived from `provider_name` or a pass for an untyped
  class. Either would reopen the concern.
- Each probe module carries its own pass verdict (`passed`), so `cli.py` holds no
  adapter-specific status logic.

## Concerns

1. **The GitHub probe mints and revokes a proof token.** That is `check_scope`'s own
   `metadata: read` mint for the proof repository and its `DELETE /installation/token`, which the
   handoff names as the calls to send. It stores, mints, and revokes no credential of the row.
2. **The probe syncs the executor's git mirror.** It fetches the registries into
   `config.mirror_root`, as `adopt-keeper` and `read-consumers` do. It writes no evidence, unit
   state, or store secret.
3. **The canary is a named exception.** `fake` runs class `rotation-canary` alone. I treated the
   canary's adapter as that class's one fixed-plan name, since its class has no type by design
   (`of_a_type`).
4. **Choices the handoff left open.** Anthropic sends a control in each form too, so a refused
   bearer can be told from a header Anthropic ignores. Exit 0 requires `passed`, from review.
5. **An unusable value reads as a missing grant.** `store.read` wraps the value, and a `Value`
   refuses empty text or surrounding whitespace. The probe reports that with the one message the
   handoff fixes, "not readable by this identity". The runbook's exit 2 bullet says to check the
   secret before the grant.
6. **The ADR line is one physical line.** I kept the handoff's "one line" literally, at about 280
   characters, rather than wrapping it at the file's 100.
7. **CI is green on `b73dc764`**, the head before this line's commit; a report-only commit reruns
   it.
8. **Teardown is HQ's.** The worktree and `orchestrator_test_hq_w_333` remain for HQ to remove
   after merge.

## Standards followed

- **Code Brain 3 (logging)**: no file logging. The executor's existing JSON logs go to stderr
  (`logs.py`: stdout is reserved for `emit-secret`'s value). The probe's one result line goes to
  stdout, as the handoff states.
- **Code Brain 4 (error handling)**: nothing is swallowed. A transport failure is
  `"no_answer": "<type>"` with exit 1, on any of the four requests. An unreadable keeper, a store that can't open, a refused
  row, or a client that can't be built exits 2 with its cause named. The one deliberate exception
  is the unreadable keeper's cause, withheld because a value read in the process might stand in
  its message, as `emit-secret` does. No bare `except …: pass` was added.
- **Code Brain 5 (security)**: no key, control, JWT, token, or auth header reaches an output
  line, log line, exception message, or test snapshot. The line is `scrub`bed as a net, and no
  response body is read: only statuses, the workspace header (through `_shown`), and ids. Tests
  assert the key and controls, by value and by prefix, are absent from stdout, stderr, and the
  log. GitHub's PEM and tokens are absent too. M4 and M5 show those tests bite.
- **Infra Brain 591, 592, 593**: no token, key, or token-shaped literal in code, tests, or docs.
  The diff grep is clean, and test keys are built at run time.
- **Infra Brain 1032**: no registry change and no `[[credential.exposure]]`.
- **Infra Brain 212**: not applicable. No endpoint is added; the probe calls only the adapters'
  existing HTTPS hosts (`api.anthropic.com`, `api.github.com`) through their confined clients.
- **Not covered by the handoff's standards**: the probe's exit-code contract (0, 1, 2) follows
  the existing verbs' convention in `cli.py`; no Code Brain road covers CLI exit codes.
