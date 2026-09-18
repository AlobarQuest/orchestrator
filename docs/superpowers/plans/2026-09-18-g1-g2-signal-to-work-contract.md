# The signal→work contract, and making one lane conform

**Status:** proposed, not started. **Repos:** `AlobarQuest/change-manager`, `AlobarQuest/orchestrator`.
**Spec:** `docs/superpowers/specs/2026-09-18-g1-g2-signal-to-work-contract-design.md` (merged `3184c999`).
**Implements:** ADR-0026 decision 2, which has neither half built; extends decision 3; retires decision 6.

**Format note.** This plan follows the two most recent plans in this directory rather than the
`writing-plans` skill's checkbox-and-literal-code template. That is deliberate and it follows a
standing instruction: *handoffs state pointers, not shapes — never assert wire shapes or enums in a
plan, name where to read them.* Every file:line below was measured on 2026-09-18 at `3184c999`
(orchestrator) and `4f7298f9` (change-manager); read the code, do not trust these line numbers after
either repository moves.

## Three corrections to the spec, measured

The spec names three symbols that do not exist where it says. Fix the spec's wording in whichever
increment touches that area; do not propagate the wrong names into code or comments.

| spec says | reality |
|---|---|
| `_matches_legacy_shape` (§5) | **`_legacy_identity_matches`**, `src/orchestrator/services/package_intake.py:299-334` |
| `_ANCHOR_BUILDERS` alongside `resolve_anchors` (§6) | it is in **`src/orchestrator/api/routes.py:2239-2246`**; `resolve_anchors` is `services/traceability.py:69-107` |
| `TraceabilityAnchor` as a schema (§2) | a **frozen dataclass**, `services/traceability.py:45-66`. `schemas.py` has `TraceabilityAnchorResponse`, a different thing |

None changes the design. All three would send an implementer to the wrong file.

## The ordering, which is forced rather than preferred

Both change-manager schemas are `extra="forbid"` (`app/schemas.py:122`, `:175`), so a producer that
sends `originating_observation_id` before change-manager declares it gets **HTTP 422 and proposes
nothing**. change-manager ships first. Within the orchestrator, the observation must exist before a
record can name it, which is C2's order.

Increments are therefore strictly sequential. Each lands its own pull request and is independently
revertible.

---

## Increment 0 — ADR-0026 amendment 1 (orchestrator, docs only)

Smallest and first, so the record is straight whatever happens to the rest. The spec retires decision
6 and extends decision 3; a reader who greps `docs/decisions/` today finds decision 6 still Accepted.

Write `## Amendment, 2026-09-18 — decision 3 extended to the revision; decision 6 retired` at the end
of `docs/decisions/0026-the-signal-to-work-record.md`, in the shape ADR-0015 uses (that file carries
three; read them for tone and for how a reversal is recorded without rewriting the original).

Two things it must say, because they are the parts a later reader will otherwise re-litigate:

- **Decision 6 is retired, not deferred.** It became moot when decision 1 placed proposing in
  change-manager. Left Accepted, it invites a future session to build a `propose` route on OBSERVER
  that nobody needs.
- **Decision 3 now carries the observation id onto the revision as well as onto the record.** The
  reason is structural: every branch of `resolve_anchors` is a local database query and the
  orchestrator has no egress, so without the second carry *"what work did this signal cause?"* needs
  a hop across a boundary the orchestrator cannot cross.

Leave the original decision text untouched. ADR-0014's back-dating rule applies to the ADR itself.

---

## Increment 1 — change-manager declares the field

**Repo:** `change-manager`. Nothing in the orchestrator may ship before this merges *and deploys* —
change-manager redeploys on push to `main`, so the gap is minutes, but it is real.

### What changes

- **`app/schemas.py`:** `WorkChangeIn` (`:158-201`) and `DeployChangeIn` (`:109-155`) each gain
  `originating_observation_id`. Both models are `extra="forbid"`; adding the field to only one makes
  the other refuse a conforming producer.
- **`app/models.py`:** `change_items` (`:22-96`) gains an `originating_observation_id` column,
  `String`, nullable. There is no observation-referencing column today — `deploy_observations` points
  the other way (`models.py:200`, FK → `change_items.id`) and is ADR-0022's subject, not this one.
- **Migration:** `alembic/versions/`, `down_revision = "2c3d4e5f6a7b"` (the sole head, the ADR-0026
  package-fields migration — its direct counterpart). Note change-manager uses `alembic/versions/`,
  not `migrations/versions/` as the orchestrator does.
- **`app/api.py::_item_dict`** (`:56-125`) returns the field, or the carry cannot read it back.
- **`app/work_changes.py`:** decide `_ASSERTED_FIELDS` (`:58-65`) — see below.

### Two decisions a build session must make, not assume

**1. Is `originating_observation_id` in `_ASSERTED_FIELDS`?** That tuple is what a re-proposal
re-asserts onto an existing record. `actor` is deliberately excluded. **HQ's reading is that it must
be excluded too**, because the spec's own obligation says a re-proposal replaying onto an existing
record does not retro-fill the id — including it would do exactly that, back-dating a cause onto a
record created before the contract. But the opposite argument is real: a producer that corrected a
wrong cause could not. Decide it, and say which in the module.

**2. What shape does change-manager validate?** It cannot verify the observation exists — it has no
egress, and `WorkChangeIn`'s own docstring states the principle (*"a registrar that inferred what it
was told could not later be checked against the thing that told it"*). It **can** check the shape.
The sibling precedent is `package_revision: int = Field(gt=0, le=2_147_483_647, strict=True)`
(`schemas.py:183`), which is strict precisely so pydantic's lax mode cannot read `true` as `1`. A
UUID-shaped string check is in keeping; a bare `str` is not. Reject blank-but-present via the
existing `_required_text` helper (`schemas.py:20-32`) rather than writing a second one.

### Also fix, in the same change

`app/work_changes.py`'s module docstring (`:24-28`) claims *"Today this route's callers are an
operator and the tests."* **`bump_proposer` has been a caller since ADR-0028.** This repository has
been bitten twice by a module docstring asserting a count a later increment falsified; correct it
while you are in the file.

---

## Increment 2 — the orchestrator's observation vocabulary, and `bump_proposer` observes

**Repo:** `orchestrator`. The largest increment, and the one carrying a new credential.

### The vocabulary

`subject_type: "repo"` already exists (`persistence/models.py:115`). Neither
`OBSERVATION_SOURCE_SYSTEMS` (`:63-111`, 12 members) nor `OBSERVATION_TYPES` (`:125-179`, 17) has a
member for this producer, so each gains one **with a written rationale for why the near miss is
wrong** — that is the house pattern every preceding lane followed; read the comments on
`pin_watcher` (`:93`) over `github`, and `tool_installer` (`:110`) over `machine_activation`.

The cost is a migration touching **both** the model's CHECK constraints (`models.py:941-964`) and the
frozen copy inside the migration, because migrations inline the vocabulary rather than importing it.
`down_revision = "0034_revision_watcher_obs"` — the sole head. Beware: filename stem and revision id
disagree for `0032_pin_watcher_observations.py`, so read the `revision = ` line rather than the
filename.

### The producer

**`bump_proposer` has no orchestrator client today.** Measured: the package imports nothing from
`orchestrator`, its only absolute URL is change-manager's, and its launcher sets no `ORCHESTRATOR_*`
variable at all. So this increment adds:

- **A confined client module**, copying the shape of `src/bump_proposer/change_manager.py` — an
  allowlist regex asserted before the transport, one path only (`POST /api/v1/observations`). The
  sibling exemplars are `src/work_carrier/orchestrator_client.py` and
  `src/work_watcher/orchestrator_client.py`.
- **An `OUTBOUND_ALLOWLIST` entry with a reason** (`tests/architecture/test_wsp21_invariant_scan.py`,
  around `:321` where the lane's two existing entries sit). A new `src/` module also draws three
  parametrized cases in that file — reconcile the collected count by node-id diff, not arithmetic.
- **A credential.** Posting an observation requires `ActorRole.SYSTEM` or `ActorRole.OBSERVER`
  (`services/observations.py:226`). **Use the OBSERVER bearer** — `orchestrator-observer`, the one
  credential every observe-and-report producer shares by design, whose entire write surface is this
  route (`api/dependencies.py:35`). Do not reach for the SYSTEM bearer: it can transition units and
  merge, and this lane must not be able to. The launcher gains the fetch in the shape
  `run-bump-proposer.sh` already uses for its two change-manager bearers, and **read the Keychain item
  directly** rather than sourcing `sds-token.sh` alongside a default — this launcher already juggles
  two BWS identities and the estate has a recorded failure from exactly that collision.

### Identity and facts, which is where this lane can wedge permanently

`bump_proposer` runs on a schedule and replays. Two measured traps bound the reference:

- **`source_reference` must identify the bump**, not the run and not a content digest — repository,
  pull request number and target version at minimum. A reference that is not content-addressed wedges
  a re-runnable producer at `observation_conflict` forever: observations have no supersession model
  and no delete route. And the update bot has been observed rewriting a bump in place under a stable
  pull request number, so the version belongs in the reference or two bumps collide on one row.
- **`facts` carry only claims that stay true** — never a date, never a count, never where a value was
  read. `normalized_fact_hash` is computed over `_fact_identity` (`services/observations.py:366-380`,
  which covers `source_reference`, `subject_*`, `environment`, `observation_type`, `status`,
  `severity`, `observed_at`, `summary`, `facts`, `payload_digest`), and a later correction is refused.

Note `observed_at` is inside that identity. A pass that stamps "now" makes every replay a new fact at
the same reference, which is the conflict above. Derive it from the bump, not from the clock.

**On a replay the pass names the observation it already posted** rather than posting a second one.
`record_observation` returns the existing row for a matching `(source_system, source_reference,
normalized_fact_hash)` (`:154-164`), so the natural implementation is to post and read the id back —
but prove that, because the alternative (a local cache) is a second source of truth.

### Two API facts that will otherwise cost a live pass

- **`ObservationCommandModel` extends `CommandBase`** (`api/schemas.py:13-15`), so `idempotency_key`
  **and** `expected_version` are required, and `expected_version` is `int = Field(ge=0)` — required
  and non-nullable. A payload omitting it is a FastAPI 422 before any service code runs. This is the
  exact shape that refused all twelve candidates on the binding lane's first live pass.
- **`record_observation` RETURNS its `DomainError`s rather than raising them**
  (`services/observations.py:88-90`). A client reaching for `.id` on the result gets an
  `AttributeError` naming an attribute instead of naming the conflict.

### The proposal

`_proposal` (`src/bump_proposer/cli.py:263-271`) gains the field. The write is at `cli.py:337`.
C2's order is load-bearing: **observe, then propose.** A record naming an observation that does not
exist is a dangling cause with no repair.

---

## Increment 3 — the join, the refusal, and the query

**Repo:** `orchestrator`.

### The carry

`PackageIntakeRegistration` (`api/schemas.py:1164-1188`) and `work_package_revisions`
(`persistence/models.py:210-275`) gain `originating_observation_id` beside `change_record_id`. The
precedent is exact and worth reading rather than paraphrasing: the comment at `models.py:263-274`
explains why `change_record_id` has **deliberately no foreign key** (it belongs to a foreign system)
and why NULL means *"nothing recorded a cause"* and never *"no cause exists."* An observation id is
different in one respect — **this database owns that table** — so a foreign key is available here and
is a real decision, not a default. Weigh it against the migration cost on existing rows and say which.

Five hops carry `change_record_id` today and the new field follows every one: schema
(`schemas.py:1185`) → `package_intake_command` (`routes.py:490`, shared with the `/review` browser
form) → route (`routes.py:494`) → `PackageIntakeCommand` (`services/package_intake.py:74`) →
`register_revision` (`services/packages.py:208`, into `candidate` at `:292` so it joins both the row
and the `revision.registered` event identity).

### Intake replay

`_legacy_identity_matches` (`services/package_intake.py:299-334`) gains a fourth clause, built exactly
like the `change_record_id` one at `:325-326`: **double-gated** on both "the command has no value"
and "the stored event lacks the key". The docstring explains why an unconditional exemption is wrong —
an event that legitimately carries the key would then mismatch. Add it to `_command_identity`
(`:337-380`) too, for the reason stated at `:362-365`: two intakes of one revision naming different
causes are two registrations, not a replay.

### Refuse an unknown observation id, fail-closed

change-manager cannot verify the observation exists; the orchestrator owns the table and can. Refuse
an intake naming an unknown id as a `DomainError`, **with the UUID parse wrapped** — an unwrapped
`uuid.UUID(bad)` raises `ValueError`, and only `DomainError` and `APIAuthenticationError` have
registered handlers, so it reaches the wire as a bare HTTP 500.

### The query

- **`TraceabilityIntentHop`** (`api/schemas.py:1872-1882`) declares the field. A FastAPI
  `response_model` silently drops any key it does not declare, so the model and
  `traceability.py:222-229` move in the same commit or the consumer receives nothing.
- **An `observation` anchor:** one key in `_ANCHOR_BUILDERS` (`api/routes.py:2239-2246`), one builder
  beside `_build_revision_anchor` (`:2208`), one field on the `TraceabilityAnchor` dataclass
  (`services/traceability.py:45-66`), one query parameter, and one branch in `resolve_anchors`
  (`:69-107`) resolving through the revision to its units. It answers `observation_not_found` the way
  its `work_unit` sibling does at `:72`.
- **The observation hop stays unit-scoped.** Do not touch `traceability.py:216-219`. Widening
  `subject_type="work_unit"` would put every repository-scoped observation into every chain touching
  that repository. The originating observation reaches a chain through the **intent** hop. The intent
  hop's own comment says this, and it is exactly where a later reader reaches for the wrong fix.

### The carry program

`work_carrier` hands `change_record_id` to `emit-intake-payload` and then refuses a payload whose
value disagrees with the record. The observation id travels the same path and draws the same refusal,
so a carry cannot file work under the wrong cause. Read `src/work_carrier/prepare.py` and
`src/orchestrator/package_sources.py:477-501` for the current shape — note the payload builder
**omits** the key rather than emitting null when absent (`:597-603`), and say whether the new field
follows that or not.

---

## Increment 4 — the cross-repo check

**Repo:** `orchestrator`. One script, following `scripts/check_brief_consumer_compatibility.py`.

Fetch `app/schemas.py` from change-manager at `main` through the GitHub contents API, AST-parse
`WorkChangeIn` and `DeployChangeIn`, and fail if either fails to declare a field the orchestrator's
producers send or `work_carrier` reads. change-manager is public, so `github.token` reaches it.

Copy four properties from the exemplar rather than reinventing them: the `Unresolvable` exception
whose docstring reads *"The check could not establish what it needed to compare. Never a silent
pass."*; the three ordered exception clauses around the fetch; `Accept: application/vnd.github.raw`
with an optional bearer; and exit 1 for both a real divergence and an unresolvable answer.

**Pin the parser**, the way the brief check pins its AST parse against `RunnerBriefResponse.model_fields`
— a parser that stopped agreeing with pydantic must be caught before it can vet anything wrongly.

### Where it runs — a deliberate deviation from the spec's letter

The spec says *"a step in the existing `Quality` job."* **Put it in the `consumer-compatibility` job
instead** (`.github/workflows/quality.yml:62-151`). The spec's stated reason was to create no new
required status context and so avoid the paired-rename trap; `consumer-compatibility` is already a
required context and already carries **four** cross-repo check steps with identical
`GITHUB_TOKEN: ${{ github.token }}` wiring, and its own preamble (`:93-100`) records this exact
decision. `Quality` is the heavyweight job with a postgres service and a migrated database, which this
check does not need. Same property, better-matched precedent.

Do not create a new job. Read `transcription-currency`'s preamble (`:162-172`) for when a separate
advisory job *is* right — when the bytes that move belong to another repository and would block every
pull request here. That is not this case: the orchestrator's own producers are what must conform.

### The residual, recorded in the script

It vets what `main` declares, not what production serves: `change-mgr.alobar.net/openapi.json` answers
302 behind forward auth, so the deployed surface is not readable. change-manager redeploys on push to
`main`, so the two converge in minutes, and a producer sending the field inside that window gets a
422. This is the estate's *merged is not deployed* rule pointed at the check itself — write it down in
the module, because the next reader will otherwise assume the check proves more than it does.

---

## Clauses that need no build, and why that is not an omission

Three parts of the spec produce no task. Named here so a reader auditing coverage does not read
silence as a gap.

- **C4 (lifecycle)** names change-manager's existing states — `pending` → `approved` → `resolved` |
  `wontfix` — and the three properties `PROPOSED_SOURCES` already carries (`app/sources.py:58`). The
  contract names them rather than inventing them, so there is nothing to write.
- **C5 (withdrawal)** is a rule awaiting a mechanism, by the spec's own words. See Non-goals.
- **The Reach section** settles that a derived record is outside C1 and that WS-P3.2 bridges rather
  than widening it. That is a scope statement binding a later cycle, not work in this one. The check
  in increment 4 vets the proposed sources only, which is the same boundary.

## Guards these changes trip

Read `CLAUDE.md`'s architecture-guard bullets rather than this list; an inventory of guards is itself a
vocabulary that drifts.

- **A new `/api/v1` GET query parameter is not a new route**, but confirm: the traceability route
  already exists, so the **exact** set in `test_scope_guards.py::test_production_get_route_inventory_is_explicit`
  should not move. If it does, you have added a route you did not mean to.
- **A new closed-vocabulary member** is caught by `test_cross_boundary_vocabulary.py`. The observation
  vocabularies are already registered; adding a member to an existing registered vocabulary should be
  inert, but run the whole-repo gate rather than assuming.
- **A new `src/` module** adds three parametrized cases to `test_wsp21_invariant_scan.py`, and any file
  importing an HTTP client needs an `OUTBOUND_ALLOWLIST` entry with a reason.
- **`test_unreachable_guards.py`** requires a production caller for every new public service function.
- **Word guards** forbid bare `dispatch` / `deploy` / `merges` / `coolify` in `src/orchestrator/` prose,
  compounds included. Note the multi-token trap: `merge_pull_request` matches the ordinary spaced
  prose *"Merge pull request #1 from …"*. Reword; never allowlist.
- **No new ingress POST**, so no `COVERAGE_MATRIX` row — unless increment 2's client work adds one,
  which it should not.
- **Two migrations, in two repositories**, each needing its `down_revision` read from the file rather
  than the filename.

## What must be proven, not asserted

- **The ordering, demonstrated rather than reasoned.** Before increment 1 deploys, a producer sending
  the field gets 422. That is the whole argument for the sequence; show it once.
- **The replay property, twice over.** Run the `bump_proposer` pass twice against a real bump and show
  the second names the same observation id rather than posting a second row. This is the failure that
  wedges the lane permanently, and it is invisible on a first pass.
- **Mutation controls** over the new predicates — the legacy-exemption clause, the unknown-id refusal,
  and the anchor branch — with the control run green and every mutant's killing test named. Compute
  which control kills which mutant as arithmetic first, then confirm against the harness: a green set
  says every mutant died, never that the control you believed killed it did.
- **The producer half is where the coverage gap will be.** Twice in one day this estate shipped a
  change fully tested in the module that computes a value and untested in the module that consumes it.
  Assert the proposal actually carries the id, not merely that the client returned one.
- **The check fires both ways.** Red before change-manager declares the field, green after — measured,
  not reasoned. A check that passes on everything is the failure this class of script exists to avoid.
- **Collected count reconciled** by node-id diff against a clean `main` archive. Read `rootdir:` beside
  the count, and take the count from a non-quiet run.

## Non-goals

- **Do not make `change_proposer`'s deploy lane conform.** The spec names it non-conformant on purpose
  and the check will see it from the day it ships. Fixing it here doubles the increment and buries the
  contract's first real test.
- **Do not build a withdrawal mechanism.** C5 states a rule and the spec says so: observations are
  append-only with no supersession model, so nothing can invoke one. A mechanism is its own decision.
- **Do not add a `change_record` anchor.** The observation anchor answers the signal question; a record
  anchor is a sibling nobody asked for.
- **Do not back-fill.** Records 59–62 and their revisions predate the contract. Nothing is retro-filled,
  and a re-proposal onto an existing record does not acquire an id. ADR-0014.
- **Do not loosen the observation hop's `subject_type` filter.** See increment 3.

## Open questions for the human

1. **Foreign key on `work_package_revisions.originating_observation_id`, or not?** Unlike
   `change_record_id`, this database owns the referenced table, so the constraint is available. Against
   it: the sibling column deliberately has none, and matching the precedent is worth something.
2. **Is the observation id in change-manager's `_ASSERTED_FIELDS`?** See increment 1. It decides
   whether a producer can ever correct a wrong cause, which is the one place this contract touches the
   withdrawal question C5 leaves open.
3. **Does the deviation in increment 4 stand?** The spec says the `Quality` job; the measured precedent
   says `consumer-compatibility`. Same stated property, different job.
