Outcome: done

# Report: ADR-0055 amendment 22, increment 3

[PR #664](https://github.com/AlobarQuest/orchestrator/pull/664) is open and ready, not merged. The
code is final at `ea80cc98`. The commit after it holds only this report, so the PR head is the
report commit. HQ merges it.

The build needed three questions to HQ, each about a guard the handoff made a stop point. HQ
answered all three, and Devon approved the third:

- Question 1: the four observation-route guard changes.
- Question 2: the settings-list entry.
- Question 3: the two `layers.toml` entries.

## What I built

### The carry form

**Where:** `src/rotation_proposer/standing_approval.py`.

`carry(entry, package, lineage, *, package_hash)` runs item 2's conditions in item 2's order. It
reuses the increment 2 functions `proposer_generated`, `lane_type`, `lane_destinations` and
`standard_authority`, each called with `first_issue=True`. It differs from the approving form in
three ways:

- **One policy approval, not none.** `policy_approved_revision_1` replaces `unapproved_revision_1`.
  It requires package revision 1 and a lineage naming the package, with exactly one revision
  (revision 1) and exactly one approval. That approval must be on revision 1, by an approver with
  the prefix `policy:non-software-operational@`, and its `approved_hash` must equal the package's
  hash.
- **The registry at `main`.** The caller reads the row, and the carrier reads it at each registry
  repository's `main`.
- **No infraops read.** `stated_issue_plan` is `issue_plan`'s plan half, factored out as
  `_plan_problems` and shared by both. It requires the issue plan from `plan_for` and from the
  package's criteria, without `last_rotated`.

The caller computes `package_hash`. That keeps `rotation_proposer` from borrowing
`rotation_worker.canonical`, which would have needed a `BORROWED` change in `test_rotation_edges.py`.

**PR #660 follow-up 3:** the `standing-approval` command has its own mirror root,
`default_mirror_root("rotation-proposer-standing-approval")`, through `_standing_approval_mirror()`
in `cli.py`. The scheduled pass keeps `_mirror()`.

### The carrier

**Where:** `src/work_carrier/standing.py`, which is new, plus `prepare.py`, `cli.py` and
`orchestrator_client.py`.

`prepare.standing_approval` runs after `prepare` and `held_refusal` on every prepared record.
`standing.is_policy_rotation` keys on the payload's `profile`, which is the package's, and never on
the record's `change_class`. Every other record, including one a person approved and a
policy-approved bump, is returned unchanged.

For a policy-approved rotation revision, `standing.check` takes the following steps:

1. **Re-reads the checkout.** It re-reads `package.yaml` and `lineage.yaml` from the checkout the
   emitter read, and refuses unless `rotation_worker.canonical.package_hash` of the package equals
   the payload's `content_hash`.
2. **Binds the lineage to the verified approval.** It refuses unless the lineage's one approval
   equals the emitter's verified approval field for field: `approver` and `approved_by`,
   `approved_hash` and `content_hash`, `revision`, `commit` and `approval_ledger_commit`, and
   `event_id` and `approval_event_id`.
3. **Reads the registry.** It reads the credential's row at `main`, through
   `find_credential(RepoMirror(default_mirror_root("work-carrier")), …)`, the carrier's existing
   mirror root.
4. **Runs the carry form.** Any refused condition is a refusal naming each condition and its
   reason.

A refusal is `Refused(record, "standing_approval_refused", detail)`, so the record waits for a
person. Any exception is a refusal too: a named error from `rotation_worker` with its message, any
other by its type alone.

**The chain event:** the emitter (`orchestrator emit-intake-payload`) already verifies the
factory-events chain. It also matches a `package.approved` event on the lineage approval's hash,
revision, approver, commit and event id (`package_sources._verified_approval_from_events`), and
writes the match into the payload. Step 2 binds the lineage to that verified event, so the carrier
relies on the event, never on the lineage's own word, and there is no second chain reader. A
missing chain event refuses earlier, at the emitter, as `package_not_intakeable`.

**On a pass:** the check composes the observation. On a `--register` pass, `_carry` posts it
through `_record_standing` immediately before `register_intake`, and a failed post registers
nothing. A bare pass checks and writes nothing.

| Field | Value |
| --- | --- |
| `source_system` | `work_carrier` |
| `observation_type` | `standing_approval_check` |
| `subject_type` | `package_revision` |
| `subject_reference` | `<package_id>@r<revision>`, because the revision has no orchestrator id before intake |
| `status` | `passed` |
| `facts` | `package_id`, `revision`, `content_hash`, `package_commit` (the lineage approval's commit), `approver`, `registry_entry`, `registry_repository`, `registry_commit`, and `refused: []` |
| `source_reference` | `standing-approval:<package_id>@r<revision>:<content_hash>:<registry repo>@<registry commit>` |
| `observed_at` | The verified approval's `approved_at`, never a clock read: it's part of the fact identity, so a clock value would make a retry `observation_conflict` |
| Idempotency key | Derived from the subject, the hash prefix and the registry commit prefix, under 200 characters |

Two passes over one revision and registry commit replay one row. A moved registry is a new
reference and a new row.

**The identity seam:** `cli._observation_identity()` reads `WORK_CARRIER_OBSERVATION_KEY_ID` and
`WORK_CARRIER_OBSERVATION_TOKEN`.

- **No identity:** if either is unset or blank, or the key id is `orchestrator-system`, the result
  is no identity. Every policy-approved rotation record is then refused `standing_approval_refused`
  in both modes, before any read. It never falls back to SYSTEM.
- **With an identity:** a `--register` pass builds a second `OrchestratorClient` from it (the
  `recorder`) and posts only the observation with it. Intakes stay on `SYSTEM_KEY_ID`.

The client allows a second write, `POST /api/v1/observations`, through `record_observation`. I
rewrote the `SYSTEM_KEY_ID` comment: a distinct identity is now load-bearing for this one act.

### The setting

**Where:** `src/orchestrator/config.py`.

`work_carrier_agent_id` (`ORCHESTRATOR_WORK_CARRIER_AGENT_ID`) is `None` by default, and blank reads
as unset, the same as the executors' ids. No deployed configuration, compose file or example sets
it.

### The vocabulary and migration

- **Model tuples:** `persistence/models.py` adds `work_carrier` to `OBSERVATION_SOURCE_SYSTEMS` and
  `standing_approval_check` to `OBSERVATION_TYPES`, each with its rationale and near miss.
  `package_revision` was already a subject type.
- **Migration:** `migrations/versions/0048_standing_approval_obs.py` (revises
  `0047_retire_tracker_bindings`; the id is 26 characters) widens `ck_observations_source_system`
  and `ck_observations_type`. The downgrade restores the frozen old lists.
- **Fixture:** `tests/fixtures/model_in_checks.json` follows the model.
- **Vocabulary registration:** the repository registers these vocabularies through the model tuples,
  which `test_observation_vocabulary_constraints.py` and `test_check_constraint_text.py` hold equal
  to the database. No other registry lists them.

### The gate

**Where:** `src/orchestrator/services/intake/standing_approval.py`, which is new, and
`decomposition.py`.

`_rotation_policy_outcome` behaves as follows:

- **Another policy approver:** a `policy:` approver without the `policy:non-software-operational@`
  prefix still refuses `revision_approved_by_policy`, as before.
- **The standing approver:** `standing_approval_refusal` refuses `standing_approval_unrecorded` with
  a detail naming the failed check. It checks, in order:

  | Check | Refusal detail |
  | --- | --- |
  | `revision.profile` is `non-software-operational` | `not_a_rotation_revision` |
  | `revision.revision == 1` | `not_revision_1` |
  | The snapshot's `profile_fields.destinations` holds exactly one of `provider name="bws-lane-token"` and `provider name="healthchecks-ping-url"` | `not_a_lane_type` |
  | The setting is set | `carrier_identity_unset` |
  | An observation exists: `work_carrier`, `standing_approval_check`, `package_revision`, subject `<package_id>@r1`, `recorded_by` the setting, `status` `passed`, and facts binding package id, an integer revision and `content_hash`, with `refused == []` | `no_carrier_record` |
  | The read succeeds; it runs in a savepoint | `carrier_record_unreadable` plus the error type |

- **After admission:** an admitted revision continues through every existing rotation condition:
  dependencies, budget and pattern recognition. On approval, the reason adds "whose check the work
  carrier recorded".

The gate reads no registry and runs no checks.

**The setting reaches the gate this way:** `submit_decomposition_proposal(…, *,
work_carrier_agent_id=None)` takes it, and the route passes `settings.work_carrier_agent_id` from
`SettingsDep`. The 107 existing call sites are unchanged, and default to refusing.

The gate's constants are spelled in the orchestrator, because it imports no out-of-process program.
Tests hold them equal to the following:

- `CARRIER_SOURCE`, `CARRIER_CHECK`, `ROTATION_PROFILE` and `subject_for`, to the carrier's
  (`test_the_record_the_carrier_composes_is_the_one_the_gate_reads`).
- `LANE_PROVIDERS`, to `rotation_worker.destinations.render`
  (`test_the_lane_providers_are_the_executor_s_destination_strings`).

`LANE_PROVIDERS` carries a `# not-a-vocabulary:` marker. It's whole destination strings matched
against the snapshot, not a value exchanged across a boundary.

### Invariants and docs

- **Root `CLAUDE.md`:** it had no invariant saying a named human approves each rotation revision. I
  added one cross-subsystem bullet stating the rule as it now holds, with both identities.
- **`src/orchestrator/services/intake/CLAUDE.md`:** I rewrote the "one click" bullet, and the
  rotation-branch bullet that refused every policy approver.
- **`src/rotation_proposer/CLAUDE.md`:** I rewrote "the first pass after a named human approves it",
  and stated the exception and the carrier's one import.
- **`docs/operations/rotation-proposer.md`:** I replaced step 3's "No approval policy grants this
  profile". The standing-approval section names the command's own mirror, and it gains "What the
  carrier checks before it registers an approved revision 1".

Every rewrite says the exception works only once increment 1's grant and the carrier's identity
exist. I didn't edit ADR-0055: the code cites it, and nothing needed adding.

## Guard changes, each with its reason

| Guard | Change | Reason | Approved |
| --- | --- | --- | --- |
| `tests/architecture/test_out_of_process_isolation.py` | The `work_carrier` row gains `rotation_proposer`, with a comment in the `rotation_worker` entry's form | Amendment 22 item 3: the carrier runs the same checks, not a copy | Handoff |
| `tests/architecture/test_external_content_observes_only.py` (ADR-0039) | `CLASSIFICATION["work_carrier"].writes` gains `OBSERVATIONS`, with one reason clause | Amendment 22 item 3: the carrier records its check before the intake | HQ, question 1 |
| `tests/architecture/test_bridge_edges.py` | `EDGES["work_carrier"]` gains `("POST", "/api/v1/observations")` | Same | HQ, question 1 |
| `tests/work_carrier/test_client_surface.py` | The write surface is two routes (`/api/v1/observations/` and `/api/v1/observations/1` stay forbidden); the public methods add `record_observation` | Same | HQ, question 1 |
| `tests/architecture/test_platform_edges.py` | The settings list gains `work_carrier_agent_id`, so the count goes from 36 to 37; it isn't in `SECRET_FIELDS` | The handoff requires the setting | HQ, question 2 (Devon) |
| `layers.toml` | Two single-file entries: `tests/services/test_standing_approval_gate.py` in `[admission]`, and `tests/api/test_work_carrier_observation_ingest.py` in `[bridge]` | Every tracked file belongs to one layer | HQ, question 3 |

**The carrier's import (PR #660 follow-up 1):** the carrier imports `rotation_proposer.standing_approval`
only. The isolation guard checks top-level names only, so I added
`test_the_carrier_imports_only_the_proposer_s_checks`. It parses every `src/work_carrier/*.py` and
asserts the set of `rotation_proposer` modules imported is exactly `{rotation_proposer.standing_approval}`.

No other guard changed. `BORROWED`, `test_unreachable_guards`, the outbound scan, the word guards
and the vocabulary registry all passed.

## Settled in the report

### The carry form's plan

**The executor's checks are enough. The carrier doesn't need infraops' `last_rotated`.** The case
is a row issued after the standing approval but before the carry, so the carry form, reading no
infraops, accepts an issue-plan revision 1 for a credential that's already issued. Three things
stop it from writing over a live value:

1. **Before the claim:** `rotation_worker/checks.py::_check_issue_fact` (lines 178-198) reads the
   keeper. An issue plan is refused when the keeper holds a value other than the empty marker and
   this rotation's staged value.
2. **The case the claim lets through:** a keeper holding a value with nothing staged. The issue's
   store step refuses it: `contract.py` lines 693-706 ("Write the keeper only after reading the
   marker there"), which raises "the keeper holds neither the marker nor the staged value".
3. **Nothing binds a value without a person:** an issue writes nothing until a value binds
   (amendment 22 item 6). For a lane token, the value is Devon's mint and paste.

So the worst case is a failed unit that a person sees, not an overwritten credential. Reading
infraops would add a credential-bearing read to an unattended lane, to catch earlier what these
steps already refuse. I didn't build it.

### The chain the carrier reads

- **Today:** the cycle's `intent-packages approve` writes the `package.approved` event to the chain
  under its machine's `FACTORY_EVENTS_HOME`, which is `~/.factory/events.jsonl` on the Mac. The
  carrier's emitter reads and verifies the chain under its own machine's `FACTORY_EVENTS_HOME`
  (`package_sources._factory_events_file`, `_verify_factory_chain`). Both are the Mac, so they're
  one chain.
- **After ADR-0059's cut-over:** `work-carrier` moves into a container (ADR-0059, the lane table:
  "Moves"). ADR-0059 is silent on the emitter's three local inputs: the sibling intent-packages
  checkout for `verify-approval`, a security-standards checkout for `factory_events verify`, and
  `FACTORY_EVENTS_HOME`. In a container, `FACTORY_EVENTS_HOME` is the container's own file, which
  holds no event the Mac's cycle wrote. Every approval there, a person's as well as the policy's,
  then fails at the emitter as `package_not_intakeable`. That fails closed, but it stops the lane.
- **What would keep them one chain:** one of the following, decided in the carrier's ADR-0059
  increment.
  1. The carrier's container reads the Mac's chain file and its anchor, read-only.
  2. The cycle and the carrier both write and verify against the shipped chain. The nightly
     factory-events pass already ships it (`FACTORY_DB_DSN`), so verification would read it there
     rather than from a local file.
  3. The cycle runs where the carrier runs.

  Option 2 keeps one source of record. Options 1 and 3 keep a machine-local file. Either way, the
  carrier's increment must provide the emitter's other two inputs too. I didn't build any of this.

### The identity design, for Devon

The gate trusts the carrier's record only because one identity can write it. Any SYSTEM or OBSERVER
caller can record an observation (`OBSERVATION_ROLES`), so the gate keys on `recorded_by`. That is
the registry agent id of whoever posted, and no other caller may hold it.

**My recommendation:**

1. **Agent id:** `work-carrier`, a new agent in security-standards'
   `registry/agents/work-carrier.yaml`.
2. **Two credentials, not one.** The carrier keeps the shared SYSTEM credential for intakes,
   unchanged, and holds a second M2M credential, key id `work-carrier-observer`, with the
   **OBSERVER** role and agent id `work-carrier`, used only for the standing-approval observation.
   OBSERVER can record an observation and do nothing else, so a leak of it can forge a carrier
   record but can't register an intake, propose a decomposition or move a unit. That's as narrow as
   the amendment's Consequences asks: "held as narrowly as the rotation executors' credentials".
   One credential would mean moving intakes to a new SYSTEM agent `work-carrier`. Then a leak could
   both register an intake and forge its check, and intake attribution would change from
   `orchestrator-system`. The seam I built works either way: set both
   `WORK_CARRIER_OBSERVATION_*` variables to the one credential.
3. **The security-standards change:** the agent file, merged, then an orchestrator image rebuild,
   because `agent_id` resolves against the bundle baked into the image. I recommend a new authority
   profile, `work-carrier-observer-v1`, rather than `observer-v1`, so the following boot check can
   name it.
4. **A boot check, as a follow-up.** It mirrors `_require_rotation_executors` (`main.py`): boot
   refuses a credential holding `work-carrier-observer-v1` that the setting doesn't name, and a
   setting naming an agent no credential holds. This change has no such check, because the profile
   doesn't exist yet. Until it does, a misset `ORCHESTRATOR_WORK_CARRIER_AGENT_ID` either refuses
   everything (a typo) or trusts the wrong identity (a SYSTEM agent's id). So set it only after the
   credential exists.
5. **Deployment order,** per the root invariants:
   1. Add the key to `ORCHESTRATOR_M2M_CREDENTIALS` and verify it in the container.
   2. Add `{"work-carrier-observer": "observer"}` to `ORCHESTRATOR_M2M_ROLES`.
   3. Set `ORCHESTRATOR_WORK_CARRIER_AGENT_ID=work-carrier`.
   4. Restart.
6. **The rotation executor's rewrite** of `ORCHESTRATOR_M2M_CREDENTIALS` reads what the running
   orchestrator loaded (`GET /api/v1/m2m-identities`) and changes only the entry its approved
   `coolify-env-hash` destination names. So a new `work-carrier-observer` entry survives every other
   credential's rotation untouched. To rotate it later through the SDS, it needs its own registry
   row in `.cred-consumers.toml`, with a BWS keeper and a `coolify-env-hash` destination on that
   variable. Restart straight after the hand edit that adds it, or a pending rotation overwrites it.
7. **The secret:** the token goes in BWS project `SDS Operator`, beside the SYSTEM bearer, with its
   UUID in `.bws-secrets.toml`. `run-work-carrier.sh` would fetch it into
   `WORK_CARRIER_OBSERVATION_TOKEN` only on a `--register` pass, as it fetches the SYSTEM token, and
   export `WORK_CARRIER_OBSERVATION_KEY_ID=work-carrier-observer`.

None of this is built. It's a credential scope, which is Devon's.

## Tests and checks

- **`make check`** on `orchestrator_test_hq_w_91`, at `ea80cc98`, from a committed tree:
  - Ruff and the format check passed (1,051 files), and pyright found 0 errors.
  - pytest **collected 14,505 tests: 14,503 passed, 1 was skipped, and 1 failed.**
- **The one failure predates this change:** `tests/services/test_slo_report.py::test_queue_age_median_of_ready_units`
  is off by exactly 3,600 seconds, a daylight-saving offset (its fixture date is in July, and the
  test runs in November). I checked it against a control: a `git archive HEAD` copy of `main`
  `4bc30f17`, run with `PYTHONPATH` at that copy. `orchestrator.__file__` resolved inside the copy,
  and the test failed identically. Per HQ, I didn't fix it here.
- **64 new tests:**

  | File | New | What it covers |
  | --- | --- | --- |
  | `tests/rotation_proposer/test_standing_approval.py` | 11 | The carry form. All six conditions pass for both lane types. Each difference refuses alone: no approval, two approvals, an approval on revision 2, a person approver, another policy's approver, a hash mismatch in the lineage, and a package hash the caller passes that isn't the approval's. A moved registry refuses `proposer_generated` and `destinations`, and a rotation-plan package refuses. |
  | `tests/work_carrier/test_standing.py` | 30 | The check and its record, replay, a moved registry being a new reference, and a lineage approval the emitter didn't verify (approver, commit, event id). Also a checkout that no longer hashes, carry-form refusals, unreadable reads naming only composed text, and a missing lineage. Unchanged records: a person-approved rotation revision and a policy-approved bump; the check keys on the profile, not `change_class`. The identity seam's five cases. Through `run`: the observation is posted before the intake, a failed record posts no intake, a refusal records and posts nothing, a missing chain event refuses at the emitter, an unset identity refuses, the SYSTEM key as identity refuses, a person-approved rotation is registered with no record, and a bare pass writes nothing. Two no-value tests, and the import-closure test. |
  | `tests/services/test_standing_approval_gate.py` | 18 | The admit case. Each admit condition failing alone: another policy prefix, the profile, the revision number, three lane-type shapes, an unset setting, no record. A record by another actor (SYSTEM, and another observer). Five non-binding facts: package, revision, a boolean revision, hash, and `refused`. A failed read, and the provider strings. |
  | `tests/api/test_work_carrier_observation_ingest.py` | 4 | The composed record through the real route: accepted, and replayed on a second pass. The route passes the setting to the gate: `approved` when it's set, and `proposed` when it's unset. |
  | `tests/persistence/test_migrations.py` | 1 | `test_0048_…` upgrades, downgrades to `0047` against Postgres, asserts both constraints narrow and keep their old members, then upgrades again. |

- **Mutation controls:** 16 mutants: 7 on the gate, including the `decomposition.py` call site; 6 on
  the carrier; and 3 on the carry form. All 16 were killed and 0 survived. I cleared `__pycache__`
  around each one, and the tree was clean after.
- **Security scan** (security-standards scanner, `--category security`, from the repository root):
  0 BLOCK, 2 WARN, 1 INFO. Both WARNs predate this change: the `*-migration/` gitignore glob, and
  the undeclared UUID `aaff45ca-…`, which isn't in this diff.
- **`/code-review` (medium) returned seven points, and none changed the code:**
  1. **No boot check on the setting:** a real gap. It belongs with the identity design (item 4 of
     "The identity design, for Devon").
  2. **A record doesn't expire, and the gate doesn't compare its registry commit to anything:** the
     amendment binds the record to package id, revision and `content_hash`, and the content is
     fixed by its hash. I note it under "Concerns".
  3. **A record posted, then a failed intake, leaves the record:** the record attests to that
     revision's content, so a later intake of the same hash relies on a true check. I note it
     under "Concerns".
  4. **`check` returns `Checked | str`:** a style point. Every caller is typed, and pyright passes.
  5. **The gate's read isn't bounded:** one row per registry commit per revision is a small
     population.
  6. **The constants are duplicated across three places:** deliberate, because the orchestrator
     can't import the carrier, and a test holds them equal.
  7. **An injected recorder skips the SYSTEM-key refusal:** reachable only from tests, because
     `main()` never injects one.

## Files changed

- **Source:**
  - `src/rotation_proposer/standing_approval.py`
  - `src/rotation_proposer/cli.py`
  - `src/work_carrier/standing.py` (new)
  - `src/work_carrier/prepare.py`
  - `src/work_carrier/cli.py`
  - `src/work_carrier/orchestrator_client.py`
  - `src/orchestrator/services/intake/standing_approval.py` (new)
  - `src/orchestrator/services/intake/decomposition.py`
  - `src/orchestrator/api/routes/intake.py`
  - `src/orchestrator/config.py`
  - `src/orchestrator/persistence/models.py`
- **Migration:** `migrations/versions/0048_standing_approval_obs.py` (new)
- **Tests:**
  - `tests/work_carrier/test_standing.py` (new)
  - `tests/services/test_standing_approval_gate.py` (new)
  - `tests/api/test_work_carrier_observation_ingest.py` (new)
  - `tests/rotation_proposer/test_standing_approval.py`, which also patches the new mirror function
  - `tests/persistence/test_migrations.py`
  - `tests/work_carrier/conftest.py`, which clears the two identity variables
  - `tests/fixtures/model_in_checks.json`
- **Guards:** the six in "Guard changes, each with its reason".
- **Docs:**
  - `CLAUDE.md`
  - `src/orchestrator/services/intake/CLAUDE.md`
  - `src/rotation_proposer/CLAUDE.md`
  - `docs/operations/rotation-proposer.md`
- **This report.**

## Self-review

- **Scope:** I created no credential, registry agent, BWS entry or scope. I changed no
  `.bws-secrets.toml`, `.cred-consumers.toml`, compose file or launcher. I touched nothing in
  intent-packages, security-standards or the main tree. I ran nothing against the live registry,
  change-manager or the deployed orchestrator.
- **The gate never admits on its own reading.** With the code merged and nothing configured, it
  refuses `carrier_identity_unset`, and the carrier refuses for want of an identity. Intent-packages
  still refuses the policy approver for this profile until increment 1. That's three independent
  refusals before a standing approval can stand.
- **A person-approved rotation revision and a policy-approved bump carry exactly as before.** Tests
  pin both, at the prepare and the `run` level.
- **The bare-pass rule holds:** "a refusal is the same refusal in both modes". The identity check
  reads the environment in both modes, and only a `--register` pass posts.
- **The carrier's error text:** the carrier prints refusals in its text report, as every refusal
  there is printed, and reports an unforeseen exception by its type alone.
- **HQ's question files:** I didn't commit `docs/superpowers/reports/adr-0055-a22-increment-3-question-answered.md`.
  It's HQ's file, and it's untracked in this worktree.

## Concerns

- **The carrier's identity has no boot check yet.** Until Devon's identity design lands with a
  profile and the matching check, `ORCHESTRATOR_WORK_CARRIER_AGENT_ID` must be set only after the
  credential exists, and never to a SYSTEM agent's id. See item 4 of "The identity design, for
  Devon".
- **The carrier's record has no expiry.** If an intake of the same revision is later registered
  another way (by hand, or after a failed carrier intake), the gate admits on the earlier record.
  It's the same content by hash, and the executor still re-reads the registry at `main` and refuses
  a destination mismatch before the claim. So I judged this within the amendment's binding.
- **The ADR-0059 cut-over breaks the emitter's chain read for every intake,** not only standing
  approvals, unless the carrier's increment provides the chain. See "The chain the carrier reads".
- **The pre-existing SLO test** fails on `main` from a daylight-saving offset. It's unrelated, and
  I didn't fix it here.
- **A standard the handoff's section doesn't cover:** none. Observation ingestion's secret detector
  (`kernel/secret_metadata.py`) applied, and the fact keys avoid its substrings (`registry_entry`,
  not `credential_id`). The real-route test proves the composed body is accepted.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** the gate logs nothing. Its
  refusal is the existing `decomposition.policy_not_applied` event, recorded as before. The carrier
  reports through its existing text report on stdout, which run-work-carrier.sh captures, as every
  carrier line is today. The handoff said "the carrier and the gate log as each already does", and
  neither gained a new logging path.
- **Code Brain rule 4 (never swallow an exception silently):** every failure is a named refusal,
  and no `except …: pass` exists.
  - `standing.check` turns every exception into a `standing_approval_refused` with the error type.
  - The gate's read runs in a savepoint and returns `carrier_record_unreadable` with the error type,
    tested with an injected `OperationalError`.
  - A failed observation post prints `NOT CARRIED`, is counted a finding, and posts no intake.
- **Code Brain rule 5 (never log secrets, credentials, auth headers, tokens, or full bodies):**
  refusals, report lines and observation facts carry ids, commits, hashes, condition names and
  error types only. An unforeseen exception's text is never repeated. Three tests prove it, with
  the placeholder `placeholder-secret-value-not-a-token`:
  - `test_no_report_line_carries_a_value` plants it as the identity token.
  - `test_a_refusal_carries_no_error_text_it_didn_t_compose` plants it in an exception.
  - `test_a_read_that_fails_refuses_and_names_only_what_it_composed`.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN`; placeholder fixtures only):** the diff carries no token and no
  `BWS_ACCESS_TOKEN` assignment. Fixture values are the placeholder string, `"a"*40`-style commits,
  and `11111111-…` UUIDs, none in the token shape. The security scan found 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value anywhere in this work, so
  there's nothing to report.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The diff adds no endpoint or domain.
  The carrier's new write goes to the existing orchestrator base URL and an existing route.
