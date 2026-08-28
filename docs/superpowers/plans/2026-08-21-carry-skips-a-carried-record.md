# The carry must not re-attempt a record it already carried

**Status:** proposed, not started. **Repo:** `AlobarQuest/orchestrator` only.
**Closes:** backlog item **150** (P1). **Backlog:** see `PROJECT.md`.
**Unblocks:** installing the `com.devon.work-carrier` LaunchAgent, which is held back today because
a full `--register` pass exits 3.

## The defect

`work_carrier` selects on status alone — `approved_work()` fetches
`/api/items?status=approved&source=work` — and nothing tells it a record has already been carried.
So every approved record is re-attempted on every pass, forever, including records whose intake it
registered itself.

**Item 150 and record 62 share that root cause and diverge at the consequence. Be precise about
this; an earlier HQ note conflated them.**

| | item 150 as written | record 62, live today |
|---|---|---|
| trigger | the revision was pasted by a human first | `intent-packages`' `main` moved between carry and re-run |
| refusal | `package_intake_conflict` — `registered_by` mismatch, message says "different content" when the content is byte-identical | `409 idempotency key belongs to a different operation` |
| item 150's two candidate fixes | address it | **do not touch it** — a different guard entirely |

Record 62's mechanism: the key `work-carry-62-2` is fixed (`prepare.py::emit_key`, from record id +
package revision), while the payload carries `source_commit` from the checkout's git HEAD. It
registered at `10b13d47…`; `main` is now past it. The key can never again carry the payload it was
minted against.

**Fix the root cause and both consequences go.** Item 150's own candidates — a `carried` state in
change-manager, or dropping `registered_by` from `register_revision`'s comparison — each address only
its named consequence, and the first costs the carry a change-manager write path it deliberately has
none of.

## What changed: the answer already ships

`GET /api/v1/change-records/{id}/work` went live 2026-08-20 as ADR-0029's retirement prerequisite.
It answers this question too. Measured against production, 2026-08-21:

```
record 62 → revision_ids: ["7e597f88-6e35-4b1e-99f1-67386d11bc53"]   → carried
record 99 → revision_ids: []                                          → never carried
```

**A non-empty `revision_ids` is "already carried."** The carry already holds a SYSTEM bearer for the
orchestrator, so this needs **no new credential, no new scope, and no change-manager write**. That
last point is the one item 150 flagged as the cost of its preferred fix.

## Design

Before registering, the carry asks the orchestrator whether work already exists for the record. If it
does, the record is reported as already-carried and **not** registered.

`work_watcher/orchestrator_client.py::work_for` is the pattern: a client whose allowlist permits
exactly one path (`_WORK = re.compile(r"^/api/v1/change-records/[0-9]{1,9}/work$")`). Copy the shape.

**`work_carrier`'s orchestrator client is write-only today** (`is_allowed_write`, `_INTAKES`). Adding
a read widens the surface **that module asserts about itself**, which is legitimate — unlike adding a
route another program owns. The docstring's claim about its own surface must move in the same commit;
this repository has been bitten twice by a module docstring asserting a count one increment had
falsified.

**Each module does its own read.** On a shared pass the watcher has already called `work_for` for
every approved record, but the two are separate binaries with no shared state, and coupling them
through stdout would be worse than two cheap reads. Name this as a decision rather than leaving it to
look like an oversight.

## Two things a build session must decide, not assume

**1. Skip silently, or report?** A carried record could in principle carry a *wrong* intake. Skipping
loses that; reporting keeps it visible. HQ's preference is **report** — `[CARRIED]` alongside the
watcher's existing `[WAITING]`, contributing no finding — but the exit-code contribution is the
decision: an already-carried record must not make the pass exit 3, or the LaunchAgent stays
uninstallable and nothing was gained.

**2. What happens to `test_carrying_a_revision_a_human_already_pasted_is_a_conflict`?**
(`tests/services/test_machine_intake.py`, which pins item 150's named consequence as *"a KNOWN,
DELIBERATE residual of ADR-0026's join"*.) If the carry never re-attempts, that path is unreachable
from the carry — but the service behaviour it pins is still reachable by other callers, and an
unreachable-from-one-caller guard is not the same as a wrong one. **This decides whether the change is
small or a design one.** Do not delete the test to make a suite green; if it should stay, say what it
now guards.

## Guards this trips

Read `CLAUDE.md`'s architecture-guard bullets rather than this list.

- A new `src/` module adds **three** parametrized cases to
  `tests/architecture/test_wsp21_invariant_scan.py`; any file importing an HTTP client needs an
  `OUTBOUND_ALLOWLIST` entry with a reason.
- `test_unreachable_guards.py` requires a production caller for every new public service function.
- Word guards forbid bare `dispatch` / `deploy` / `merges` / `coolify` in `src/orchestrator/` prose —
  but note this change is under `src/work_carrier/`, so check whether that scan covers it rather than
  assuming either way.
- No new route, so **no route-inventory entry and no idempotency-matrix row**. No migration.

## What must be proven

- **The live differential, and unlike ADR-0029's it is still available.** Record 62 reproduces the
  409 today. Run `--register` before and after: before is a finding and exit 3, after is a clean pass.
  This is the evidence ADR-0029's build could not produce and said so.
- **Mutation controls** over the new predicate, control run green, every mutant's killing test named.
  The likely gap is the same one that has bitten twice: the module that *computes* carried-ness gets
  tested and the call site that *uses* it does not. Assert the registration is not attempted, not
  merely that the predicate returned True.
- **The empty case**, from a record id the orchestrator has never seen — `revision_ids: []` must mean
  carry, not skip. A predicate inverted here silently stops the lane carrying anything.
- **Collected count reconciled** by node-id diff against a clean `main` archive.

## Non-goals

- Do not give the carry a change-manager write path.
- Do not loosen `change_record_id` in `register_revision`. ADR-0026 decided the cause of a piece of
  work is not something a later caller revises, and that join is what ADR-0027 protects.
- Do not weaken the idempotency guard. It is correct; the carry should stop asking, not the
  orchestrator stop refusing.
- No deploy. The route already ships; this changes only a local program.
