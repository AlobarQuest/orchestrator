# Retiring a work record when its work completes

**Status:** proposed, not started. **Repos:** `AlobarQuest/orchestrator`, `AlobarQuest/change-manager`.
**Backlog:** see `PROJECT.md`.

## The defect, measured

A `work`-source change record records a human's decision that a bump should be built (ADR-0028).
Nothing moves it out of `approved` when the build finishes. Measured 2026-08-19 on record **61**
(`infraops-mcp-server-npm-eslint`): its work unit `eb7c36f7` reached `completed` and its pull
request `infraops-mcp-server#81` merged, and the record still read `approved` the next morning.

Two consequences, one noisy and one quiet.

**Noisy.** `work_carrier` selects on status alone —
`HttpWorkRecordSource.approved_work()` fetches `/api/items?status=approved&source=work` — so a
finished record is re-selected on every pass forever. Its idempotency key is fixed
(`prepare.py::emit_key` → `work-carry-{record}-{revision}`), while the payload it registers carries
`source_commit` from the checkout's git HEAD (`orchestrator/package_sources.py`). Once
`intent-packages`' `main` moves, the same key carries a different payload and the orchestrator
answers **409 `idempotency key belongs to a different operation`** — correctly; the guard exists to
stop one key meaning two things. It is permanent: `main` never returns to the commit the key was
minted against. `cli.py` returns `EXIT_FINDINGS` (3) for an unregistered record, so once the
LaunchAgent is installed this is a red pass every morning.

**Quiet, and the reason to fix it.** An approved-and-done record is indistinguishable from an
approved-and-waiting one, so **"what has been approved but not yet built?" has no correct answer.**

Note the symmetry with record **59**, which reached a terminal state it should not have (a mis-click
during the window when the UI offered no Approve button, unrecoverable because `reactivate` is
guarded to `wontfix`). 59 and 61 are the same missing thing in opposite directions: the lane has no
path between *the work finished* and the record that asked for it.

## The question this turns on

May a machine retire a record a human approved? ADR-0028 deliberately keeps the record as the human
decision and change-manager's `propose` scope refuses every status-moving route server-side.

**CORRECTED 2026-08-20, after this plan was first committed: the `deploy` source has solved BOTH
directions and the `work` source has neither.** `app/deploy_retirement.py` (ADR-0019 inc 5b) closes
a record whose pull request closed UNMERGED; `app/deploy_settlement.py` (**ADR-0022**) closes one
whose landing SUCCEEDED. The success direction is the one this plan is about, so read
`deploy_settlement.py` first — its opening paragraph describes the identical defect one source over:
*"an approved record sits in the estate authorising a landing that is done — the mirror image of the
closed-unmerged case … and the half nobody had."*

**Two rules it states that this plan must answer, and the first one reframes open question 2.**

*"WHY THE WATCHER AND NOT THE PRODUCER. ADR-0022. The producer's remit is what MAY happen; the
watcher's is what DID."* Deploy gave the closure to the component that already held the fact, rather
than to the one that would have had to go looking. The `work` lane has no watcher — so the question
is not "`work_carrier` or a new producer" as a matter of taste, it is **which component already
holds the completion fact, or whether one must exist**. Answer it against that rule.

*"WHY THIS IS NOT A ROUTE OF ITS OWN, where retirement is."* A settlement needs no route because the
server DERIVES the fact from coordinates already supplied; a retirement needs one because the fact is
visible only to the caller. **change-manager has no orchestrator egress and cannot derive "the unit
completed"** — so the work case is retirement-shaped even though its direction is settlement's. That
is why the design below is a route, and the reason is now stated rather than assumed.

Also carry ADR-0022's judgment about *strength*: it settles on `revision_confirmed` (production was
asked) and refuses `rollout_unverified` (something merely looked green). The work analogue is below —
completion, not settlement.

**The retirement precedent still gives the SHAPE.** `app/deploy_retirement.py`
already lets a producer retire a `deploy` record. Read its module docstring in full before designing
anything; the load-bearing paragraph is *"WHY IT IS SAFE TO ACT ON A FACT THIS SERVICE CANNOT
CHECK… The difference is DIRECTION."* A retirement can only ever remove permission, so a caller that
lied could stop work that was going to happen anyway and could not cause any. Its shape:

- one route, one source (`deploy` records only);
- **a closed observation vocabulary of exactly one member**, because the route's justification is
  that its outcome cannot be chosen — the caller reports a fact, the server decides the status;
- already-terminal is a **replay, not an error**, so a sweeping producer does not turn its own
  earlier retirement into a finding.

## Design

Mirror it. `POST /api/items/{item_id}/work-retirement`, `work` records only, observation vocabulary
`{work_unit_completed}`, outcome `resolved`, replay on already-terminal.

**Retire on COMPLETION, not on settlement.** A `failed` unit may still be retried (`FAILED → READY`
is a SYSTEM edge), so retiring on "no longer in flight" would terminate a record whose work is still
live. A `cancelled` unit is a human decision and the matching record decision stays human — that is
what happened with record 60, set to `wontfix` by hand. The machine acts on exactly one fact.

`resolved` rather than `wontfix`: `app/api.py`'s own comment draws the line — *"Distinct from
wontfix (accepted risk)."* The work was done.

## Prerequisites, in order

### P1 — the back-link (blocks everything)

The fact *"an intake exists for record 61, and here is its revision"* lives only in the orchestrator,
keyed by revision id. Nothing points back from the record, and there is **no listing route and no
lookup by `change_record_id`** — verified 2026-08-20 against the served OpenAPI; the only read is
`GET /api/v1/package-intakes/{revision_id}`. So neither the cheap mitigation (carrier skips a
carried record) nor the structural fix can detect the condition today.

Recommended: an orchestrator read keyed on `change_record_id`. `PackageIntakeResponse` already
exposes the field. Confirm the current shape from the served schema rather than from this document.

Decide and record: does it answer with the revision, or with the revision plus the settlement of its
units? The second makes P3 a single call; the first keeps the route dumb and puts the join in the
producer. Prefer whichever leaves the orchestrator answering questions about its own state rather
than about the producer's.

### P2 — the fact must be one the producer cannot get wrong

Derive completion from the orchestrator's durable record, never infer it. Name in the plan exactly
which read establishes it and what it returns for a revision with several units — the retirement is
about the **record**, and a record names a package revision, which may decompose into more than one
unit. State the rule explicitly (all units completed? at least one? no non-completed terminal?) and
test it; do not leave it to whatever the first implementation happens to do.

### P3 — the change-manager route

New route + scope entries. Note `app/scopes.py` lists `deploy-retirement` in **three** places
(lines ~76, ~107, ~158 as of 2026-08-20); find every list rather than the first. `tests/` has a
worked example in `test_api_deploy_retirement.py` and the scope matrix in `test_auth_scopes.py`.

### P4 — the producer's write surface

`work_carrier`'s change-manager client is **read-only today**: `change_manager.py::is_allowed`
returns `path == "/api/items"` and there is no write path at all. Widening it is the authority
change, and the module docstring's assertion about its own surface must move with it — this
repository has already been bitten by a docstring that kept claiming a count one increment had
falsified (see `change_proposer/change_manager.py`'s "THE COUNT IN THE FIRST LINE IS A BEHAVIOURAL
CLAIM").

Whether the retirement lives in `work_carrier` or a new producer is open. `work_carrier` already
spans both systems and already sweeps approved records, which argues for it; against it, its current
narrowness is an asserted property.

## Guards these changes will trip

Not exhaustive — read `CLAUDE.md`'s architecture-guard bullets, which list the family and warn that
an inventory of guards is itself a vocabulary that drifts.

- A new `/api/v1` GET route must be added to the **exact** set in
  `tests/architecture/test_scope_guards.py::test_production_get_route_inventory_is_explicit`.
- Any new ingress POST needs a `COVERAGE_MATRIX` row or a reasoned exclusion in
  `tests/idempotency/test_matrix.py`.
- A new `src/` module adds three parametrized cases to
  `tests/architecture/test_wsp21_invariant_scan.py`, and any file importing an HTTP client needs an
  `OUTBOUND_ALLOWLIST` entry with a reason.
- `test_unreachable_guards.py` requires a production caller for every new public service function.
- Word guards forbid bare `dispatch` / `deploy` / `merges` / `coolify` in `src/orchestrator/` prose,
  compounds included (`post-deploy` tokenizes to `deploy`). Reword; never allowlist.
- New closed vocabularies are caught by `test_cross_boundary_vocabulary.py` — a genuine
  cross-boundary vocabulary is REGISTERED, not marked exempt.

## What must be proven, not asserted

- **Mutation controls over the new logic, with the control run green**, reported per mutant with its
  killing test named. Compute which control kills which mutant as arithmetic first, then confirm
  against the harness — a green set says every mutant died, never that the control you believed
  killed it did.
- **The producer half is where the coverage gap will be.** Twice in one day (2026-08-19) a change
  was fully tested in the module that computes a value and untested in the module that consumes it,
  and the mutation reverting the consumer passed the whole suite. Assert the retirement call is
  actually made, not just that the predicate returns True.
- **A live differential:** record 61 currently reproduces the 409. Show the pass failing before and
  clean after, from real state.
- **The idempotent-replay property**, by running the pass twice.

## Non-goals

- Do not silence the 409 by weakening the idempotency guard or folding the payload into the key. The
  guard is correct; the 409 is the only thing currently making the defect visible.
- Do not give the producer a general `resolve` verb. The narrowness is the safety argument.
- Do not automate the `cancelled`/`wontfix` direction. That decision stays human.
- Record 59 stays as it is. Back-dating a judgment about it is the mistake ADR-0014 names.

## Open questions for the human

1. **Is an ADR warranted?** This grants a machine a status-moving verb over a human decision. It is
   narrower than ADR-0019's and squarely inside ADR-0022's reasoning, so it may be an increment
   rather than a new decision — but the answer belongs to Devon, not to the build session.
2. **Which component holds the completion fact** — `work_carrier`, a new watcher, or something else
   (see P4, and answer it against ADR-0022's producer/watcher rule rather than by convenience).
3. **Whether the orchestrator read answers settlement or only identity** (see P1).
