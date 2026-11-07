Outcome: done

# Report: lanes-inc-2-executor

This is ADR-0059 increment 2, PR 3 of 4. The rotation executor learns ADR-0055 amendment 19's two
lane credential classes, `bws-lane-token` and `healthchecks-ping-url`.

- **PR:** AlobarQuest/orchestrator#630 (draft), branch `hq/lanes-inc-2-executor`
- **Head:** the commit that adds this report, on top of the last code commit
  `d37a104697cc8142485aeda8f04e9c9b147370db`
- **Base:** `9c835981eb5922aaa30daffe4056f239760f52ca`
- **CI:** see the "Checks" section.

## What I built

Each item matches the handoff's numbering.

1. **The two types.** Both are in `TYPES` and `CLASS_TYPES` (`types.py`), with store kind
   `bws-secret`, so the VM instance claims them and the Keychain instance doesn't
   (`KEYCHAIN_ADAPTERS` is unchanged). Each has its own adapter: `adapters/bws_lane_token.py` and
   `adapters/healthchecks_ping_url.py`. Each provider name is the type's own name, as
   `postgres-role`'s is, for two reasons. The lane token's Coolify token gets its own
   `admin_secrets["bws-lane-token"]` slot. "No other keeper of its type" then reads only that
   type's rows, through `provider_entries`. Registry rows must therefore set
   `provider = "bws-lane-token"` and `provider = "healthchecks-ping-url"`.
   `tests/fixtures/rotation_class_policy.json` is byte-identical to infraops's at `c3359bf1`
   (sha1 `02054fec…` on both), and `test_rotation_agreement.py` passes with both classes typed. I
   checked the other `CLASS_TYPES` users:
   - The decomposer offers the issue plan as a candidate (`plans.candidates`).
   - The proposer's `plan_for` gives a type with no rotation plan its issue plan, and
     `acceptance()` words an issue's criteria.
   - `test_decompose.py` covers both classes through every parametrized test.
   - `test_rotation_edges.py` has updated admin-secret, provider-edge, and borrowed-name tables.
2. **The issue plan.** `plans.derive_issue` builds a person's mint, then one `store` unit:
   `verify_new`, `store`, `distribute` (only when a copy is written), and `verify_consumers`.
   - `Plan.issue` declares it, and `contract.validate_issue_plan` is its own rule. The only change
     to `validate_plan` is that it now refuses a plan declared an issue, so it's unchanged for
     every existing plan.
   - In an issue, `UnitRun.old()` is the marker, so every step after the mint refuses a value
     equal to it.
   - `_store_issue` quarantines nothing and writes only after reading the marker. A keeper
     already holding the staged value counts as done, and any other value fails the unit.
   - `_verify_consumers` deletes staging after it files, and files the deletion.
   - `checks._check_issue_fact` reads the marker before the claim. It refuses a rotation plan on a
     marker keeper, and an issue whose staging holds a different value from the keeper's.
3. **The keeper command.** `rotation-worker create-keeper <id>` (`cli.create_keeper`) creates the
   keeper in `keepers_project_id` holding the marker, keyed by the row id. It refuses an existing
   key and a malformed id, and prints only `{"secret_id": ...}`. It reads no stdin and needs no
   registry row, because ADR item 8 step 1 runs it before the row lands.
4. **The reach bind.** `inbox.BindsByItself` replaces the `accepts` and `same_identity` pair for
   these types. The lane token's `bind_refusal` checks three things:
   - a fresh login accepts the token beside a refused control (`accepts`);
   - `_REACH` equals `reach_digest(reach_projects, reach_secrets)` from the row;
   - no other lane-token row declares the same reach.

   The generic `bind` still refuses the keeper's own value, a value another keeper holds, and any
   bind while `other_mints` lists a unit. A row with no readable reach counts as "same"; it fails
   closed.
5. **The shape bind.** The keeper must hold the marker, and the value must match
   `https://healthcheck.alobar.net/ping/` plus one lowercase UUID, by `fullmatch`. The issue's
   `verify_new` dispatches on the `ShapeVerified` protocol. It repeats the shape check and files
   evidence with no probes. The comment in `contract._verify_new` names it the one exception to
   R14 and cites amendment 19 item 5 and D-30. The provider's `probe`, `known_bad`, and `mint`
   raise, so nothing can send it a ping.
6. **The redeploy refusal.** `bws_lane_token.check_row` runs before the claim. It refuses
   `redeploy` true, a non-production or non-service variable, a key not ending
   `_BWS_ACCESS_TOKEN`, and any destination beyond the provider, the keeper, and one
   `coolify-env`. Distribute sends one `PATCH` through `CoolifyClient(..., restarts=False)`, whose
   `permits` refuses `POST .../restart`. A 404 raises `CoolifyRefused` and fails the unit, as
   tested.
7. **`verify_consumers`.** The provider's `consumer_loaded` counts the `PATCH` answering 2xx: this
   unit's distribute write, or one fresh `PATCH` in a reissue's separate `verify` unit. The keeper
   is checked by `SecretStore.locate` and a read-back. For a reissue,
   `_check_human_retire_verifiable` counts the lane's variable as verified this way. The comment
   there cites amendment 19 item 7 and D-27; R10 stays outside the plan. `retire_old` is the
   person's act, and `confirm_dead` probes the quarantined old token dead and the new one live.
8. **The policy pattern.** I added three `rotation_known_good` rows, each with
   `decided = "2026-11-07"`:
   - "BWS lane-token unit of an approved standing package" under `live_estate`, units `confirm`,
     `store`, and `verify`.
   - The same row under `external_system`.
   - "Healthchecks ping-URL issue unit of an approved standing package" under `external_system`
     only, unit `store` alone.

   For the reach sections, I matched the proposer's `reach()`. A lane token writes a Coolify
   service variable, so it has `live_estate`, plus `external_system` for its Bitwarden login. A
   ping URL writes only a BWS secret, so it has only `external_system`. Each pattern names only
   the units its derived plans have. The issue's `store` is a subset of the reissue's units, so
   the lane token needs no separate issue pattern. No pattern allows anything amendment 19
   doesn't name, and none lists `mint` or `retire`.
9. **Logs.** Binds and refusals go through the existing `inbox_bound` and `unit_waiting` JSON
   events (`logs.py`), with fingerprints only. No refusal text includes the value or any part of
   a ping URL; tests assert this for every refusal. Messages say "the marker" rather than the
   marker's word, `empty`: once a process has wrapped it in a `Value`, `values.scrub` redacts the
   word everywhere in its logs.

The four follow-ups from #628's review:

- `_distribute` gets a `coolify-env` branch for a `WritesVariables` provider. A type that hosts
  still writes its variables at host.
- `derive()` uses a shared `_distributes` helper gated on `Capabilities.env_at_distribute`.
  `test_existing_types_derive_unchanged` still pins every existing type's derived plan.
- The issue's own finish rule is in `UnitRun.finished`: an issue is finished when
  `verify_consumers` was filed and staging is gone.
- A ping URL's `verify_consumers` is the keeper's read-back, and its shape-check `verify_new` is
  item 5.

Also built:

- **The verifier, narrowed.** `rotation_verification._coverage` now treats any note naming the
  old value "before the revoke" as a person's revoke next. Without this, a lane token's `verify`
  after a leak, which files "the old value is dead before the revoke", would have passed as
  partial coverage. It now goes to a person, which only narrows what the verifier decides.

- **The runbook.** `docs/operations/rotation-executor.md` has a new "Lane tokens and ping URLs"
  section with the keeper command, the row shape, and what each unit does. "The plan a credential
  runs" and the inbox section are updated.
- **The package rules.** `src/rotation_worker/CLAUDE.md` has an updated inbox invariant and a new
  issue invariant.

## The `_REACH` measurement and the declared set

I couldn't measure `_REACH` live. A read-only login with the `sds-operator` token, read from
Keychain and printing only counts and project names, was denied by the session's permissions. So
the declared set is designed to hold whichever way the SDK answers.

- **The fake-account test.** `test_reach_lists_a_direct_grant_account_as_its_secrets_alone` runs
  `_REACH`'s own script in-process against a stand-in `bitwarden_sdk`. For an account that lists
  no projects and two directly granted secrets, the digest is
  `sha256("secret <a>\nsecret <b>")`, which equals `reach_digest((), secrets)`. With a project
  listed, it equals `reach_digest((project,), secrets)`. The code path is plain: two listings, one
  sorted join, one hash.
- **The declared set.** It's secrets and projects: `reach_secrets`, required and non-empty, and
  `reach_projects`, optional and empty by default. A lane account with direct secret grants
  declares `reach_projects = []` if the SDK lists no project for it. If the SDK also lists the
  projects of the granted secrets, the row names those projects there. Either way, the digest
  matches what `_REACH` prints.
- **What's still unmeasured.** I don't know what `projects().list` returns for a secret-only
  account. HQ can settle it at the first lane's bind. A wrong `reach_projects` refuses the bind
  with "what it can see isn't exactly the reach its registry row declares", and the value stays
  in the inbox.

## The Healthchecks reads

I read the Healthchecks API docs (`healthchecks.io/docs/api/`) and built nothing that calls
Healthchecks.

- **`/pings/` needs a full read-write key.** `GET /api/v3/checks/<uuid>/pings/` isn't in the
  read-only key's list of allowed endpoints. It also takes the check's `uuid`, which a read-only
  response doesn't include.
- **The flips endpoint works with the read-only key.** `GET /api/v3/checks/<uuid|unique_key>/flips/`
  accepts the `unique_key` that read-only responses carry. It lists status changes, not pings.
- **The ping-log limit is unread for our instance.** The docs say `/pings/` returns 100 pings for
  free accounts and 1,000 for paid ones, a per-account plan setting. I didn't read the self-hosted
  instance's value, and the docs don't state a retention limit apart from that.
- **What a read-only response omits.** It drops `uuid`, `ping_url`, `update_url`, `pause_url`,
  `resume_url`, and `channels`, and adds `unique_key`. This confirms D-28's premise that nothing
  the VM's read-only key reads ties a pasted UUID to a check.

## Tests and checks run

- **`make check`**, run at `87f3303a` (the follow-up commit's suites are in "The rotation suites"
  item). Ruff check, ruff format (1,030 files), and pyright are all clean. pytest
  passed 14,065 tests, skipped 1, and failed 1:
  `tests/services/test_slo_report.py::test_queue_age_median_of_ready_units`, off by exactly
  3,600 s. This diff doesn't touch that test or `services/reporting`. The test's span runs from
  July 5 to now and crosses the November 1 DST change in the local session. I couldn't run the
  clean-clone control locally, because a `git archive` of the base was denied, so CI is the
  control: CI's `Quality` job passed it, so the failure is the local timezone, not the code.
- **The new module.** `tests/rotation_worker/test_lane_credentials.py` has 75 tests. These
  include a test that every issue payload conforms to
  `tests/fixtures/rotation_evidence_schema.json`. Two verifier tests are in
  `tests/services/test_rotation_verification.py`.
- **The rotation suites.** After the last code commit, `rotation_worker`, `rotation_decomposer`,
  `rotation_proposer`, the verifier tests, the rotation-step contract test, and
  `tests/architecture` gave 3,829 passed. Ruff and pyright are clean.
- **Mutation review.** I ran 25 mutations of the new guards: the marker as the old value, the
  issue store's marker read, staging cleanup, the shape check, the quarantine refusal, the issue
  validation's actor checks, the leak probe, the bind dispatch, reach equality, the
  shared-reach and login checks, the redeploy refusal, the single-`PATCH` memory, the
  approved-variable check, the ping marker and `fullmatch`, both issue-fact refusals, the R10
  stand-in, the env fit gate, `restarts=False`, the hosted-write rule, and the keeper command's
  existing-key and id checks. All 25 were killed, two of them after I strengthened tests. The
  tree was restored and `__pycache__` cleared.
- **`/code-review` (high).** It found eight points. I fixed four:
  - A finished issue was refused at re-check. Fixed by not refusing a non-marker keeper with
    nothing staged.
  - An issue was inferred from a missing retire step. `Plan.issue` now declares it.
  - The shape flag and protocol could drift. A test holds them equal.
  - The distribute note's grammar.

  I kept four as designed; see "Concerns".
- **A second review pass (advisor).** It found the verifier's leak-case gap, the scrub of the
  marker word, and the schema test gap. All three are fixed in `d37a1046`. It also checked that a
  `StoreError` in the new pre-claim read is a wait, not a refusal: it is in `worker.TRANSIENT`,
  which `run_pass` skips to the next pass.

## Checks

- **`87f3303a`:** `Quality`, `Rollout transcription currency`, and `Runner consumer
  compatibility` all passed.
- **`d37a1046`, the last code commit:** `Quality`, `Rollout transcription currency`, and `Runner
  consumer compatibility` all passed.
- **The report commit, the PR's final head:** it changes only this file. Its checks are on #630.

## Files changed

The output of `git diff 9c835981 --stat`:

```
 docs/operations/rotation-executor.md               | 104 ++-
 src/orchestrator/factory-policy.toml               |  52 ++
 .../services/verifier/rotation_verification.py     |   5 +-
 src/rotation_decomposer/decompose.py               |   9 +-
 src/rotation_proposer/generate.py                  |  47 +-
 src/rotation_worker/CLAUDE.md                      |  10 +-
 src/rotation_worker/adapters/__init__.py           |  42 +
 src/rotation_worker/adapters/bws_lane_token.py     | 232 ++++++
 src/rotation_worker/adapters/bws_machine_token.py  |  24 +-
 .../adapters/healthchecks_ping_url.py              |  88 ++
 src/rotation_worker/checks.py                      |  78 +-
 src/rotation_worker/cli.py                         |  43 +-
 src/rotation_worker/contract.py                    | 167 +++-
 src/rotation_worker/coolify.py                     |  11 +-
 src/rotation_worker/inbox.py                       |  28 +-
 src/rotation_worker/plans.py                       |  85 +-
 src/rotation_worker/types.py                       |  16 +
 src/rotation_worker/worker.py                      |   2 +
 tests/architecture/test_rotation_edges.py          |  15 +-
 tests/fixtures/rotation_class_policy.json          |   2 +
 tests/rotation_worker/test_lane_credentials.py     | 906 +++++++++++++++++++++
 tests/rotation_worker/test_plans.py                |  24 +-
 tests/rotation_worker/test_postgres_role.py        |   3 +-
 tests/services/test_rotation_verification.py       |  12 +
 24 files changed, 1925 insertions(+), 80 deletions(-)
```

## Self-review

- **Existing behavior.** Every existing type's derived plan is pinned unchanged
  (`test_existing_types_derive_unchanged`). `same_identity` still runs both logins (its test
  pins the calls). The `_distribute` note is unchanged when no variable is written. The
  `coolify-env` branch runs only for a `WritesVariables` provider, and only the lane token is
  one.
- **The hosted-write rule.** `writes_hosted_service` counts a `coolify-env` in a plan with no host
  step as written at `distribute` and `verify_consumers`. This can only remove a
  `touches_no_hosted_service` mark, never add one, and any other type with such a row is still
  refused (tested).
- **Unit names.** `Adapter.plan` for the lane token is its reissue plan, and for the ping URL its
  issue plan. Code that reads a unit by name (`overlaps`, `inbox.person_mints_before`) sees
  `mint` then `store` in both. I updated the plan tests to assert that every candidate's segments
  are a prefix of `Adapter.plan`'s.
- **The pre-derivation candidate.** For a type that issues, `checks._plan` doesn't offer
  `adapter.plan`. That plan is derived with no destinations, so it has no `distribute`, and
  running it would never write the lane's variable.

## Concerns

- **A ping URL's `verify_new` criterion needs one person's adjudication.** The verifier's
  `_PROBED_STEPS` rule sends a `verify_new` with no probe to a person and never fails it. A
  ping-URL issue therefore waits at `/review` for that click, beside the revision approval. Letting
  the verifier decide it would mean reading the executor's note or the adapter. That changes a
  human gate, so it's HQ's and Devon's decision, not this PR's. A test pins the current
  behavior.
- **A leak reissue's `verify` also goes to a person.** The verifier fix in "Also built" sends it
  there. After a leak, a person is acting anyway.

- **The proposer can't tell a lane token's first issue from a reissue.** `plan_for` takes the
  type's first candidate, which is the rotation plan for `bws-lane-token`. A proposer-generated
  package for a first issue would describe the reissue plan, and the executor refuses it before
  the claim ("the keeper holds only the marker: nothing is issued to rotate"). It fails safe, but
  nothing machine-generates a lane token's first-issue package yet. One option is to choose the
  issue plan when infraops serves `last_rotated` as null; that's a design choice for HQ or PR 4
  (intent-packages' `create-rotation`), so I didn't build it. The ping URL has only an issue
  plan, so its packages come out right.
- **What makes a first issue due.** I didn't read infraops at `9cdaab1f`; the handoff says HQ has
  read it. The executor side is in place: a due row with a marker keeper runs the issue plan.
- **A reissue's `verify` unit re-sends the `PATCH`.** It writes the keeper's current value, the
  same one `distribute` wrote, because the 2xx is the only proof the executor's Coolify token
  allows (item 7). The variable could change in Coolify between the two writes, and the step
  would still pass. The lane phases' reads are the real proof (D-27).
- **The `admin_secrets` slot.** The VM configuration needs `admin_secrets["bws-lane-token"]`
  pointing at the Coolify token. The runbook says so; HQ adds it at install.
- **The same-reach check runs at bind, not before the claim.** A value staged by hand through
  `rotation-worker stage` skips it, as it skips `same_identity` for other types.
- **`create-keeper` isn't atomic.** It does find-then-create, the same pattern as
  `adopt-keeper`'s. Two concurrent runs could create two keepers with the same key. It's an
  operator command run once per lane.
- **The local DST failure.** `test_slo_report.py`'s 3,600 s failure is unrelated to this diff.
  It belongs on the backlog if CI shows it too.
- **The handoff's Standards don't cover the probe rule.** Code Brain has no road for this
  exception to the probe-beside-a-control rule. ADR-0055 decision 3 is the source I followed.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout).** Every bind, refusal, and unit failure
  logs through the existing `rotation_worker.logs` JSON formatter (`inbox_bound`,
  `unit_waiting`, `unit_refused`, `unit_failed`). I added no file logging and no `print` in the
  executor's run path. The keeper command prints only its JSON result, as the other CLI commands
  do.
- **Code Brain rule 4 (never swallow an exception).** No new `except` swallows anything:
  - A failed bind check returns a reason. The value stays in the inbox, and the existing
    `unit_waiting` log records the reason.
  - Every failure after the claim raises `ContractError` or `CoolifyRefused`, and `worker._work`
    fails the unit with it. A `PATCH` 404 is never a skip.
  - In `sharing_reach`, the `except ContractError` counts the unreadable row as sharing the reach
    and names it in the refusal, so the error fails closed and is reported.
- **Code Brain rule 5 (never log secrets).**
  - Values are `Value`s, which render only as fingerprints. Evidence and logs carry fingerprints.
  - No refusal or error text includes a token, a ping URL, or a ping URL's UUID; tests assert
    this for each refusal.
  - The Coolify client keeps only the status code.
  - Test values are placeholders such as `lane-token-placeholder-new` and a zero UUID.
- **Infra Brain 591, 592, and 593 (no BWS token in tracked files or history; bootstrap token never
  inline).** No token appears in code, tests, docs, or history. Test values don't match the BWS
  token shape. The session-end scanner gate applies.
- **Infra Brain 1032 (never revoke without an attested consumer set).** The issue plan revokes
  nothing: it has no `retire_old` or `confirm_dead` (`validate_issue_plan`). The executor never
  revokes a lane token at all; `revoke` raises. A reissue's revoke is a person's act, gated as
  follows:
  - The `verify` unit proves the keeper by a read-back and the lane's variable by the `PATCH`
    2xx.
  - The lane phases' `--token` reads (`lanes-finish`, `lane-run --read`) attest the lane's
    consumer set outside the plan, under D-27. HQ reports those reads before Devon revokes, per
    D-26.
  - After a leak, Devon revokes first. This is the exposure response decision 6 already makes
    his, and `confirm_dead` then proves the old token dead.
- **Infra Brain 212 (HTTPS for public FQDNs).** Not applicable: this PR adds no app or domain.
