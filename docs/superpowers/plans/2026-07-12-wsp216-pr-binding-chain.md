# WS-P2.16 — The PR-Binding Chain + Cross-Boundary Vocabulary Enforcement

**Date:** 2026-07-12 · **Revision 5** (revisions 1–4 were EACH KILLED in adversarial review)
**Repos:** `AlobarQuest/orchestrator`, `AlobarQuest/factory-runner`
**Blocks:** all of Wave 2 (program exit criterion #6)

> **Five reviews, five kills, and every kill landed on the section written with most confidence.**
>
> - **Rev 1** prescribed a D3 fix that `failed_closed` every automated AC (§2.4); planned to dispatch
>   to a repo with no dispatchable workflow (§5.2); proposed a detector whose green was reachable by
>   editing a fixture (§3.6.3); and pinned a cross-repo fixture by hash that nobody derived from —
>   *the exact `can_create_pr` defect it exists to fix* (§3.1).
> - **Rev 2** fixed the evaluator's payload shape and **missed the other half of the same kill**: the
>   runner writes **one** evidence row per unit (`cli.py:219`, `_first_ac_id`), so promoting
>   `automated_test` to deterministic `failed_closed`s ACs #2..N on **absent** evidence. Its
>   replacement evaluator was also **constant-true** (`exit_code` is a hardcoded `0`, `cli.py:486`)
>   and command-blind — a **fail-open** that would auto-pass *"the tests pass"* on evidence that
>   `uv sync` ran. And its guard predicate was satisfiable by a **stale binding** from a previous
>   attempt (§3.4).
> - **Rev 3** shipped D3's "safe half" — and **pointed it at the wrong schema**. It said validate
>   `schemas.py:75`, which is **`EvidenceCommand.evidence_type`** (the *evidence-row* vocabulary,
>   `runner.pr.opened`), not `PackageAcceptanceCriterionCommand.evidence_type` (`:698`, the *criterion*
>   vocabulary). The two fields are **byte-identical** declarations. Implemented as written it 422s
>   **every** evidence submission and strands **every** unit in `EXECUTING`. **This plan documented
>   that exact two-vocabulary confusion as blind-spot 5 — and then committed it three paragraphs
>   later.** Its `attempt` column also had no semantics for the SYSTEM writer, the *only* repair path.
>
> **Every one of these passed a careful read. Four times.** They were found only by adversarial review
> against executable reality. The dead versions are kept, because *why* they were wrong is the most
> useful content in this file.
>
> **The pattern is the lesson, and it is now unmistakable:** *knowing* the failure class does not
> protect you from it. Rev 3 named the trap and fell in it. **Only the review caught it.**
>
> - **Rev 4** finally aimed D3 at the right field — and then **put the capability vocabulary in
>   `tests/fixtures/` and made PRODUCTION code load it at import.** `tests/` is in **neither** the
>   orchestrator image (`Dockerfile:32-39`) nor factory-runner's wheel (no `package-data`), so the
>   container **would not boot** and every dispatched run would `FileNotFoundError`. Its negative
>   control runs in the source tree and goes **green** (§3.1). Its D1 negative control — for the defect
>   the whole workstream exists to fix — was **not runnable by any harness that exists** (§6). And its
>   fixture migration silently reds **drill-2 and drill-4** (§2.1).
>
> **Every one of these passed a careful read. Five times.** They were found only by adversarial review
> against executable reality. The dead versions are kept, because *why* they were wrong is the most
> useful content in this file.
>
> **The pattern is the lesson, and it is now beyond doubt:** *knowing* the failure class does not
> protect you from it. Rev 3 named the two-vocabulary trap and fell in it. Rev 4 named *"a value
> computed and never consumed"* and *"a fixture calling a service is not a caller"* — and committed
> **both**. **Only the review has ever caught this.**
>
> **Rev 5 (Devon, 2026-07-12):** the vocabulary becomes a **shipped package resource**, proven present
> in the wheel and the image (§3.1). D1's control moves **into factory-runner** as a caller assertion
> (§6). The drill seed migration is taken **faithfully**, budgeting drill-2/drill-4 (§2.1). The
> detector is **split into U5** — it is ~5× larger than assumed and its predicate needs another pass.
> The guard's column is `binding_attempt` and the kernel gets a **new clause** (§3.4). §4.0 records
> what five reviews have confirmed **sound**.

---

## 1. What is actually broken

**D1 — No worker has ever written a `unit_pr_binding` row.** `factory_runner/client.py` has no
`pr_binding` method (`grep -rn "pr_binding" src tests .github` in factory-runner → **zero hits**).
The route `POST /work-units/{id}/pr-binding` (`api/routes.py:779`) is reachable and has **no client**.
So the reconciliation runner has no PR to poll, records no observation, raises no condition —
*silently*, since `skipped_correlations` never increments either. WS-P2.5 (Evidence Pack), WS-P2.6
(traceability) and exit criterion #6 all sit on this joint.

**D2 — the unit capability vocabulary is unenforced.** `grep -rn "github.pr.create" src/` in the
orchestrator → **zero hits**. `_validate_unit_constraints` (`services/decomposition.py:489-509`)
checks `constraints` and `conformance` only. The orchestrator accepts **any string** as a capability.

**D3 — `evidence_type: automated_test` matches nothing in the verifier, and the ORCHESTRATOR is the
wrong side.** See §2.3.

### 1.1 There are FOUR vocabularies; only one has a source of truth

| # | Vocabulary | Example values | Source of truth |
|---|---|---|---|
| 1 | **package** `authority.allowed` | `repository_write`, `pr_open` | ✅ security-standards `registry/capabilities.yaml`, enforced by the intent-packages validator |
| 2 | **unit** `authority.capabilities` | `repo.edit`, `github.pr.create` | ❌ factory-runner only, at runtime, too late |
| 3 | **unit** `required_capability` | `repo.edit` (prod), `repository_write` (73 fixtures) | ❌ none — free string |
| 4 | **`evidence_type`** | `automated_test` … | ❌ mismatched between intent-packages and the verifier |

> **PRECISION — do not over-apply the fix.** Vocabulary 1 is **correct as it stands.** The package
> layer *should* speak the registry vocabulary; ADR-0001's package-authority → unit-capability
> projection (`pr_open` → `github.pr.create`) is the boundary this workstream closes. The ingress
> guard applies to **unit** fields (2 and 3) **only**, never to `work_package_revisions.authority`.
> An implementer who "helpfully" unifies vocabulary 1 breaks every existing package.

**A fifth instance, found in review** — `dispatch.py:283`:
`return normalize_authority(unit.authority).change_class or unit.required_capability`. When
`change_class` is absent a **capability** string is presented as a **change class**. That is not an
ops footgun; it is the same bug class, and revision 1 documented it in §5.1 without noticing.

---

## 2. Traps a careful implementer walks straight into

### 2.1 The ordering — and why the predicate is fine

The guard *"a unit whose envelope allows `github.pr.create` may not reach `SUBMITTED` without a
binding"* looks too strict **and** too lax. Both objections are about **order, not predicate**:

- **"Too strict":** every dispatched unit's envelope carries `"github.pr.create": "allowed"`
  (`tests/fixtures/runner_authority_envelope.json`) and **no worker writes a binding** — so shipping
  the guard first hard-fails *every* unit at `EXECUTING → SUBMITTED`. **U2 removes this.**
- **"Too lax":** `scripts/drill_common.sh:235,242-243` seeds `repository_write`, so no drill
  exercises the guard.

**Hence: vocabulary → runner writes the binding → guard. Not negotiable.**

> ☠ **REV 4 CLAIMED "U1 REMOVES THE TOO-LAX OBJECTION." IT DOES NOT — and the drills are coupled to
> U1 and U3 in a way no revision modelled.**
>
> **Only `drill-3` writes a PR binding** (`drill-3-external-pr-conflict.sh:71,82`). **`drill-2`
> (`:140`) and `drill-4` (`:75`) submit with NO binding at all.** So the `seed_unit` migration has two
> possible landings, and rev 4 budgeted neither:
>
> - **(a) minimal — seed `{repo.edit: allowed}`.** The guard's first disjunct (*envelope does NOT allow
>   `github.pr.create`*) is then **always** taken. Drills stay green and **no drill ever executes the
>   guard's binding branch.** *"Too lax" survives U1 entirely.*
> - **(b) faithful — seed the full six-capability runner envelope** (incl. `github.pr.create`), which
>   is what the §2.1 argument actually requires. Then **drill-2 and drill-4 hard-fail at submit.**
>
> **Decision: take (b), and budget the drill changes.** Drills 2 and 4 must write a binding before
> submit — which is *also* what finally gives the guard real drill coverage. **U1 and U3 are therefore
> coupled through `drill_common.sh`; land the seed change with the guard, not before it.**
>
> *(Safe either way: drills don't set `dispatch_enabled`, and `_blocked_reason` returns
> `dispatch_disabled` **first** (`dispatch.py:259`), so the seed change cannot trigger an outbound
> GitHub call.)*

### 2.2 The naive vocabulary allowlist makes the orchestrator reject its OWN units

WS-5.1 post-deploy units are generated by the orchestrator with
`required_capability="post_deploy_verification"` and `capabilities: {"post_deploy_verification":
"allowed"}` (`deployment_observations.py:250,263`) — a capability factory-runner's
`SUPPORTED_CAPABILITIES` does **not** contain. The orchestrator's vocabulary is a strict **superset**.

> **The check must live in `register_approved_unit` (`services/packages.py:355`) — never in
> `normalize_authority` or the kernel.**

`WorkUnit(...)` is constructed in exactly **two** places: `register_approved_unit` (whose only two
callers are `routes.py:337` and `decomposition.py:298`) and `deployment_observations.py:254`, which
bypasses the service entirely. So a service-level invariant covers **both** ingress paths and leaves
post-deploy generation untouched. A `normalize_authority`-level check would self-reject post-deploy
units and red `tests/kernel/test_authority_round_trip.py:41-42`.

### 2.3 D3: the orchestrator's verifier is the side that is wrong

The intent-packages validator accepts **exactly five** `evidence_type` values
(`profiles/_evidence_tags.py`): `automated_test`, `automated_check`, `human_review`,
`external_attestation`, `observation`. A package **cannot legally declare `pytest`.** The verifier
covers **none** of the five:

| package emits | orchestrator has | result |
|---|---|---|
| `automated_test` | neither set | `judgment_required` — **wrong**, silently |
| `automated_check` | neither set | `judgment_required` — **wrong**, silently |
| `human_review` | `human.review` (**dot, not underscore**) | right answer, **wrong reason** |
| `external_attestation` | neither set | right by accident |
| `observation` | neither set | right by accident |

**This is why nothing caught it.** Three of five accidentally land correctly, so the human ACs look
fine; only the *automated* ones misroute. That is why all 16 of WS-P2.15's `automated_test` ACs had
to be adjudicated out-of-band. The "no adjudication form in `/review`" gap was never a UI gap.

**And there is a second fallthrough.** `verifier_evaluators.py:62` returns `judgment_required` for a
`DETERMINISTIC_TYPES` member with **no `EVALUATORS` entry**. So adding `automated_test` to
`DETERMINISTIC_TYPES` alone — the obvious fix, the one the P1 backlog item invites — **is a no-op.**

### 2.4 ☠ The fix revision 1 prescribed would have halted the factory

Revision 1 said: map `automated_test → _status_result`. **That fails closed on every real unit.**

`evaluate_criterion` keys on **`criterion.evidence_type`**, not the evidence row's type
(`verifier_evaluators.py:50,57`). `_status_result` (`:65-77`) reads **only top-level** `status` /
`conclusion` / `result` / `exit_code`, else returns `failed_closed`.

The only evidence the runner writes is `build_pr_opened_evidence`
(`factory-runner/src/factory_runner/evidence.py:75-91`):

```python
{"pr_url": ..., "head_sha": ..., "verification": [{"command":..., "exit_code": 0, "summary": "passed"}]}
```

**No top-level result field — `exit_code` is nested inside `verification[]`.** So: AC declares
`automated_test` → now deterministic → `_status_result` → no top-level field → **`failed_closed`**.
Today that AC lands on `judgment_required` and is adjudicated out-of-band. After the "fix", **every
automated AC on every factory unit hard-fails.** Not a stale-ledger risk — a **forward** defect: no
future runner evidence satisfies `_status_result` either.

*Corroborating, already latent:* `runner.verification` is in `DETERMINISTIC_TYPES` → `_status_result`,
but `build_verification_evidence` writes `{"commands": [...]}` — also no top-level field. That pair is
**already** mismatched; it is invisible only because no package can legally declare
`runner.verification` and `cli.py` never calls it.

---

## 3. Design

### 3.1 Capability vocabulary — one source of truth, DERIVED not hashed

Mirror the mechanism that pins the authority envelope (`CONTRACT_SHA256`,
`tests/contract/test_runner_envelope_contract.py` ↔ factory-runner's
`test_orchestrator_envelope_contract.py`).

> ⚠ **A hash pin proves the FILE matches. It does not prove anyone USES it — and that is exactly the
> `can_create_pr` defect this workstream exists to fix.** factory-runner could commit the fixture,
> assert its hash, and leave `SUPPORTED_CAPABILITIES` hardcoded beside it: both repos green,
> vocabulary still forked, a value computed and never consumed.
>
> **So assert DERIVATION.** `SUPPORTED_CAPABILITIES` must be *loaded from* the vocabulary at import
> time, with a named test asserting `SUPPORTED_CAPABILITIES == frozenset(_VOCAB["runner"])`.
> **Negative control:** hardcode the set and add a term → factory-runner goes red. *Not* "change a
> byte → the hash test reds", which proves nothing about use.

> ☠☠ **REV 4 PUT THE VOCABULARY IN `tests/fixtures/` AND MADE PRODUCTION CODE LOAD IT. IT IS NOT
> SHIPPED. THE CONTAINER WOULD NOT BOOT.**
>
> - **orchestrator:** `Dockerfile:32-39` copies `.venv`, `src`, `registry-bundle.json`, `alembic.ini`,
>   `migrations` and the security-standards registry. **`tests/` IS NOT IN THE IMAGE.** The ingress
>   allowlist is consumed by `services/packages.py` — production code.
> - **factory-runner:** `pyproject.toml` is setuptools/src-layout with **no
>   `[tool.setuptools.package-data]` and no MANIFEST**. `uv tool install git+https://…` (§5.3) builds a
>   wheel containing **only `factory_runner/*.py`**. `authority.py` loading
>   `../../tests/fixtures/…json` raises **`FileNotFoundError` at import, in every dispatched run, in
>   every target repo.**
>
> **And the negative control cannot see it:** `pytest` and `make check` run **in the source tree**,
> where `tests/fixtures/` exists. Green in tests, dead in production — *the plan's own recurring
> failure class, committed by the plan.*
>
> **THE VOCABULARY MUST BE A SHIPPED PACKAGE RESOURCE.** Put it *inside the package*:
> `src/factory_runner/capability_vocabulary.json` (+ `[tool.setuptools.package-data]`) and
> `src/orchestrator/capability_vocabulary.json`, loaded via `importlib.resources` — or simply a plain
> Python module, which ships by construction and needs no packaging change at all. **`tests/fixtures/`
> then asserts equality with the SHIPPED resource**, which is what "derived, not hashed" actually buys.
>
> **Prove it ships — in the artifact, not the source tree:** `uv build && unzip -l dist/*.whl | grep
> capability`, and a check that the file is present in the built image. **A source-tree test is not
> evidence.**

> ⚠ **Do not create a SECOND source of truth for the same vocabulary.**
> `tests/fixtures/runner_authority_envelope.json` **already** enumerates all six runner capabilities
> and is **already** byte-pinned across both repos. A new `capability_vocabulary.json` with its own
> hash gives one vocabulary two pinned fixtures — the workstream whose thesis is *one source of truth
> per vocabulary* shipping a second one for its flagship vocabulary. Either derive the envelope
> fixture's `capabilities` keys from the vocabulary fixture, or add a **named** assertion that
> `capability_vocabulary["runner"] == sorted(runner_authority_envelope["capabilities"])`.

Orchestrator allowlist = `runner ∪ {post_deploy_verification}`.

### 3.2 Ingress enforcement — both unit fields, named error

Validate **both** `authority.capabilities` keys **and** `required_capability` in
`register_approved_unit`. Named error `unknown_capability`, carrying the offending key and the
accepted set.

**Blast radius (accepted by Devon):** `repository_write` appears **117 times across 32 files** under
`tests/` (rev 4 said "~73" — it was low), plus both `drill_common.sh` seeds and the **drill-2 /
drill-4 binding writes** (§2.1). **Production is already `["repo.edit"]`** — the fixtures are test-only
drift, so this moves the tests *toward* production, not away.

⚠ **A migrated fixture that still passes while asserting nothing is the same defect class as
everything else here.** When migrating, confirm each fixture's assertion is still *about* something —
a test that asserted on the old capability string and now silently succeeds has been deleted, not
migrated.

**State honestly what this buys.** `kernel/authority.py:41-42` — `level_for()` returns `"prohibited"`
for an unknown capability, so dispatch **already** fails closed (`capability_not_authorized`). D2's
existing failure mode is a **silent stall, not an escape.** The ingress check buys a **named error at
the gate, where a human can fix it.** That is worth having, and it is a smaller claim than "we closed
a security hole." **Do not oversell it in the ACs.**

⚠ **Ingress-valid ≠ dispatchable.** The vocabulary has six capabilities; production enables **one**.
A unit with `required_capability="repo.read"` passes ingress, passes the detector, and silently sits
`blocked: capability_not_enabled`. The green certifies membership in a set that is **not the set the
gate uses.**

### 3.3 factory-runner discharges the binding — hand-built (§5.2)

- **Wire `can_create_pr` — and it must FAIL FAST, at run start.** `validate_authority` computes it
  (`authority.py:35`) and **nothing reads it** (only `models.py:23` and two tests).
  ⚠ **There is exactly one path to `submit()`** (`cli.py:582`, inside `_finalize_workspace`), and it
  is **unconditionally preceded by `gh pr create`** (`cli.py:537-547`). Both `finalize-run` and
  `local_heavy_finalize` funnel through it. So a unit without `github.pr.create` **cannot be run by
  factory-runner at all.** If the refusal fires at PR-creation time it is an `Exit(1)` *after* the
  work is done: the runner never calls `submit()`, the unit sits in `EXECUTING` until lease expiry →
  requeue → **burns an attempt** → repeats → `FAILED`. **Refuse at run start**, before any work, with
  a named `AuthorityError`. Do not relocate the deadlock into factory-runner.
- **Add `OrchestratorClient.pr_binding(...)`** — note the real class name is `OrchestratorClient`
  (`client.py:46`), not `PrBindingClient`.
- **POST it before `client.submit(...)`.**

⚠ **Three corrections to revision 1's one-line sketch, each of which would have 422'd or crashed:**

1. **`PrBindingCommand` extends `CommandBase`** (`schemas.py:8-10,899`): `idempotency_key` and
   `expected_version` are **mandatory**, and `routes.py:793` calls
   `_require_zero_expected_version(...)` — so the client must send **`expected_version=0`** plus a
   stable idempotency key. Omitting them 422s. **factory-runner already documents hitting this exact
   seam for evidence** (`evidence.py:35-37`: *"Omitting them made every evidence submission a 422 — a
   seam neither side's fixtures exercised."*). Do not re-open it.
2. **`pr_number` is NOT in hand.** `cli.py:537-547` captures `pr_url` — a **URL string** from
   `gh pr create`. The route demands `pr_number: int = Field(gt=0)` (`schemas.py:907`) and the DB
   enforces `CheckConstraint("pr_number > 0")` (`models.py:1169`). Derive the integer explicitly
   (`gh pr view --json number`). Only `head_sha` (`cli.py:536`) is genuinely in hand.
3. Revision 1 asserted "both values are already in hand." **They are not.**

### 3.4 The submit guard — with its predicate STATED

Revision 1 introduced `submission_binding_recorded: bool` and **never defined its predicate**, while
reasoning about "a PR-capable unit" — which only makes sense if it *is* capability-keyed. Resolved:

> **Predicate: `submission_binding_recorded = (envelope does NOT allow github.pr.create) OR (a
> UnitPrBinding row exists WHOSE `attempt` IS THIS ATTEMPT)`.** Capability-keyed **and
> attempt-scoped**, computed in `services/lifecycle.py`, passed to the kernel as a plain `bool`.

☠ **Rev 2's predicate was "a row exists", and it was vacuously satisfiable.** `UnitPrBinding`'s
primary key is **`work_unit_id` alone** (`models.py:1177`); `pr_number` / `head_sha` carry **no
attempt column** (only `verification_read_attempt` exists, and it arms the *divergence alarm*, not the
binding). `upsert_pr_binding` overwrites in place (`pr_bindings.py:82-85`) and **nothing deletes the
row on `REVISION_REQUIRED`.**

*Failure scenario:* attempt 1 opens PR #100, writes binding `(100, head A)`, submits → verifier →
`REVISION_REQUIRED` → `READY` → re-dispatch. Attempt 2's `pr_binding` POST fails (network, a 422, an
older console script — recall §5.3: the runner installs from **unpinned** `main`). It proceeds to
`submit()`. Rev 2's guard: *"a row exists"* → **row (100, A) still there** → **passes**. Then
`arm_verification_head` (`pr_bindings.py:99-112`) arms attempt 2's cycle on **attempt 1's head A**,
while the real PR is #101 at head B. **The divergence alarm AC-001 exists to raise is armed at the
wrong head, and the guard built to prevent exactly this certified it as fine.**

**Fix (Devon, 2026-07-12): add an `attempt` column to `unit_pr_binding`.** `upsert_pr_binding` records
it — the route **already accepts `attempt`** (`schemas.py:908`), it is simply not stored. The guard
then requires a binding written **for the current attempt**.

⚠ **It is NOT "one additive column" — the SYSTEM writer needs a defined semantics, and SYSTEM is the
ONLY repair path.** `_authorize_write` (`pr_bindings.py:182-183`) **early-returns for
`ActorRole.SYSTEM`**: SYSTEM may write a binding with **no attempt and no lease token** (*"SYSTEM may
write without a claim: it is the operator's repair path"*, `:67`), and real call sites do exactly that.
So:

- **`NOT NULL`** → every SYSTEM binding write raises `IntegrityError`. **The operator repair path 500s.**
- **Nullable, with the guard requiring `binding.attempt == unit.attempt_count`** → a SYSTEM binding
  written with `attempt=None` can **never** satisfy the guard, so a unit stuck at
  `EXECUTING → SUBMITTED` **cannot be unblocked by the one actor authorized to unblock it** — while
  §3.4 simultaneously forbids the alternative ("must not suggest fabricating a binding"). **A guard
  that is unrecoverable by design.**

**Decision: the column is NULLABLE, and the guard requires `binding.attempt == unit.attempt_count`.**
An operator repairing a unit **must supply the real attempt** — the field already exists on the route
and is simply optional. Supplying the true attempt for a PR that genuinely exists is *repair*, not
fabrication; leaving it `NULL` fails **closed**. Document this as the operator's obligation.

*Migration safety:* nullable ⇒ safe on a non-empty table, so the migration does not depend on
`unit_pr_binding` being empty in production (per §1 it should be, but **do not infer** that — a
nullable column makes it moot). Alembic head is `0014_wsp21_recovery_controls`; `unit_pr_binding` is
created there (`:177`) and is deliberately **not** append-only, so a nullable `add_column` genuinely
suffices. It does not interact with `ck_unit_pr_binding_armed_head_has_attempt` (which constrains only
the `verification_read_*` pair) nor with `record_verification_read_head` (a different column).

⚠ **Name the column `binding_attempt`, not `attempt`.** A bare `attempt` sitting beside
`verification_read_attempt` on the same table is a confusion waiting to happen — and this workstream
exists because two fields with the same name meant different things.

**Four mechanics rev 4 left unstated:**

1. **A SYSTEM repair must supply the attempt — make it mandatory on the route for SYSTEM.**
   `upsert_pr_binding` overwrites fields unconditionally (`pr_bindings.py:82-85`), so a SYSTEM write
   that fixes only `head_sha` would **NULL a good `binding_attempt`** and make the unit un-submittable.
   The alternative (write-only-when-not-None) means SYSTEM can never *correct* a wrong attempt. Pick
   mandatory-for-SYSTEM and say so.
2. **Against SYSTEM the column is documentation, not enforcement.** `_authorize_write` early-returns
   for SYSTEM (`:180-183`); only the WORKER branch reaches `validate_active_claim`. So SYSTEM *can*
   write `binding_attempt = unit.attempt_count` with no claim and no PR — the fabricated binding §3.4
   forbids, invisible to the guard. **Acceptable (SYSTEM is trusted), but state it.**
3. **The concurrent-reclaim worry is void — for a reason §3.4 did not give.** `_perform_transition`
   checks `unit.version != command.expected_version` (`lifecycle.py:128`) **before**
   `_transition_guards` (`:147`). A reclaim (`claims.py:665`) increments `attempt_count` *and*
   transitions `EXECUTING → CLAIMED`, bumping `version` — so a stale submit dies on `version_conflict`
   **before the guard is evaluated**. **No legitimate binding can fail the guard due to a concurrent
   reclaim.** Write the line, or a sixth reviewer re-opens it.
4. ☠ **The kernel needs a new CLAUSE, not just a field.** `authorize_transition`
   (`kernel/transitions.py:73-91`) has **exactly two hardcoded guard clauses** —
   `AWAITING_APPROVAL → READY` and `target is COMPLETED`. **There is no hook for
   `EXECUTING → SUBMITTED`.** Adding a `TransitionGuards` field and computing it in services does
   **nothing** on its own: the kernel would never read it. U3 must add the third clause **in the
   kernel** — and mind the `dispatch`/`deploy` docstring trap (§3.4).

**Why capability-keyed, stated honestly (rev 2 overclaimed):**

- **Unconditional contradicts a load-bearing contract.** `services/pr_bindings.py:98-100`: *"A unit
  with no PR binding is a no-op: not every work unit opens a pull request, and those must still be
  able to submit."*
- ⚠ **The first disjunct is never taken by a factory unit** — every dispatched envelope carries
  `github.pr.create`, and the runner's only submit path opens a PR (§3.3). Rev 2 claimed "the two
  changes interlock"; that was **circular**. The disjunct's real purpose is **non-runner submitters**
  — drills, SYSTEM, human — which is exactly the class `pr_bindings.py`'s contract protects. That is a
  smaller and truer claim, and it is the one the ACs must make.

**Post-deploy units are not at risk from either variant** — `deployment_observations.py:260`
constructs them directly in `state=SUBMITTED`; they never traverse `EXECUTING → SUBMITTED`, the only
legal producer of that state (`kernel/transitions.py:29`). **State this, or a reviewer re-litigates
it.**

**Placement — VERIFIED CORRECT.** `_perform_transition` (`services/lifecycle.py:111-148`): unit lock
→ **idempotent-replay early return (`:123-127`)** → **version check (`:128-135`)** →
`authorize_transition(..., _transition_guards(...))` (`:143-148`). A retried submit returns from
`_idempotent_result` before the guard is built. Idempotency and `version_conflict` both safe.

⚠ **Revision 1's "hard constraint" about the kernel scope guard was FACTUALLY WRONG.**
`FORBIDDEN_SEQUENCES` (`tests/architecture/test_ws32_scope_guards.py:40-51`) is: `factory-event/v1`,
`("merge","pull","request")` *as adjacent tokens*, `("workflow","dispatch")`, `("factory","runner")`,
`("production","mutation")`, `("auto","merge")`, `productionmutation`, `coolify`, **`dispatch`**,
**`deploy`**. The words `github`, `pr`, and `pull request` are **not** forbidden — a kernel field
named `pr_binding_recorded` passes. **The real trap: any kernel docstring containing the bare word
`dispatch` or `deploy`** — the natural way to write *"the binding is written before the unit is
dispatched"* — reds the guard.

**Circular import (revision 1 got this right):** `services/lifecycle.py` may import `UnitPrBinding`
from `persistence.models`; it may **not** import `services.pr_bindings`, which imports `ActorContext`
*from* lifecycle (`pr_bindings.py:45`).

**Recovery hint must not suggest fabricating a binding.** A PR-capable unit that produces no diff
reaches `FAILED`/`BLOCKED`/`CANCELLED`; the runner already refuses `no changes to submit`. The only
path to *success* would be a SYSTEM `upsert_pr_binding` inventing a PR that does not exist.

### 3.5 D3 — the SAFE HALF only. The evaluator is DEFERRED, and here is why.

> **Rev 2 tried to ship the deterministic evaluator and it was killed. Devon's call (2026-07-12):
> ship the behavior-preserving half; the evaluator becomes its own workstream.**

**Why "just promote `automated_test` to deterministic" cannot ship — BOTH halves must be understood:**

1. **Payload shape (rev 1's kill).** `_status_result` (`:65-77`) reads only **top-level** result
   fields; the runner nests `exit_code` inside `verification[]`
   (`factory-runner/evidence.py:75-91`). → `failed_closed`.
2. **Absent evidence (rev 2's kill — the half rev 2 missed).** The runner writes **exactly ONE
   evidence row per unit**, for AC #1 only: `ac_id = _first_ac_id(brief)`
   (`factory-runner/cli.py:549`, `:219-223`), with a single `submit_evidence` call (`:569`). But a
   unit maps to **N** ACs (`decomposition.py:544-560`), and the verifier looks evidence up **per AC**
   (`verifier.py:110-115`). The moment the type is deterministic, `evaluate_criterion` hits
   `if evidence is None: return ("failed_closed", ...)` (`:52-53`) for ACs #2..N.
   → `record_adjudication(outcome="failed")` → `REVISION_REQUIRED` (`verifier.py:210-211`) → the
   re-attempt writes the same single row → **loop until `max_attempts` → FAILED.**
   ⚠ **Today those ACs land on `judgment_required` and the unit completes via out-of-band
   adjudication.** The "fix" turns a working (if ugly) path into a hard failure.
3. **And the obvious evaluator is a FAIL-OPEN.** `exit_code` is a **hardcoded literal `0`**
   (`factory-runner/cli.py:486`) and `_run_command` **raises** on any nonzero return (`:153-168`), so
   no runner payload can ever carry a failure. *"Pass iff every `verification[].exit_code == 0`"* is
   **constant-true**. It also never checks **which** command ran — `verification_commands` is just
   `constraints.allowed_commands` (`cli.py:295`). An AC reading *"the full test suite passes"* would
   be auto-discharged by evidence that **`uv sync` ran**. That is strictly worse than today's loud
   `judgment_required`.

**A correct D3 therefore requires:** factory-runner writing **one evidence row per mapped AC**; the
verifier keying on the **evidence row's** `evidence_type` rather than the criterion's (§3.6.4
blind-spot 5); and a **command-aware** evaluator. That is a workstream, not a unit. **Backlogged P1.**

#### What DOES ship — zero behavior change

**(a) Name all five package types explicitly**, preserving today's outcome exactly:

| package `evidence_type` | treatment | behavior change |
|---|---|---|
| `automated_test` | `JUDGMENT_TYPES` | **none** (already `judgment_required`) |
| `automated_check` | `JUDGMENT_TYPES` | **none** |
| `human_review` | `JUDGMENT_TYPES` (alias of `human.review`) | **none** |
| `external_attestation` | `JUDGMENT_TYPES` | **none** |
| `observation` | `JUDGMENT_TYPES` | **none** |

Every row is a no-op *at runtime*. What changes is that the vocabulary becomes **declared** instead of
accidental: today these five land on `judgment_required` by **falling off the end of a set**, which is
indistinguishable from a typo. After this, they land there **because we said so** — and a **typo does
not.**

**(b) Validate the CRITERION's `evidence_type` at package intake** against
`DETERMINISTIC_TYPES ∪ JUDGMENT_TYPES`. An unknown type becomes a **named error at the gate** instead
of a silent `judgment_required` at verify. **This is the entire safety win.**

> ☠ **REV 3 CITED THE WRONG SCHEMA AND IT WOULD HAVE HALTED THE FACTORY.** Rev 3 said `schemas.py:75`.
> That is **`EvidenceCommand.evidence_type`** — the **evidence-row** type on the worker's
> `POST /work-units/{id}/evidence`. The runner hardcodes `evidence_type="runner.pr.opened"`
> (`factory-runner/evidence.py:88`), which is in **neither set**. Constraining `:75` **422s every
> evidence submission**, `_finalize_workspace` raises before `client.submit` is reached, and every
> unit strands in `EXECUTING` → lease expiry → burns an attempt → `FAILED`. **For every unit.**
>
> **The two fields are byte-identical (`evidence_type: str = Field(min_length=1)`), which is exactly
> why a careful read slides past it.** This is §3.6.4's blind-spot 5 — the evidence-row vocabulary vs
> the criterion vocabulary — *documented in this plan and then committed by it three paragraphs later.*

**TARGET — exactly one field:**

- ✅ **`PackageAcceptanceCriterionCommand.evidence_type`** (**`schemas.py:698`**), reached via
  `PackageIntakeRegistration.acceptance_criteria` (`:720`) → `services/package_intake.py:329`, the only
  place criteria are built from a package.

**EXPLICITLY OUT OF SCOPE — these speak the EVIDENCE-ROW vocabulary, not the criterion vocabulary:**

- ❌ `EvidenceCommand.evidence_type` (`schemas.py:75`) — `runner.pr.opened`
- ❌ `RecoverEvidenceCommand.evidence_type` (`schemas.py:857`) — the SYSTEM repair path
- ❌ `services/evidence.py`'s writer-supplied type — `verifier.finding` (`verifier.py:177`)

Correlating those two vocabularies is **blind-spot 5, and it is backlogged.** Do not "helpfully"
extend the validation to them.

⚠ **Budget the fixture churn — rev 3 budgeted none.** `review_note` is used as a **criterion**
`evidence_type` in `tests/api/test_package_intake_api.py:56` and `tests/api/test_decomposition_api.py:62,290`,
and it is in neither set. Either add it to `JUDGMENT_TYPES` or migrate those fixtures. §3.2 budgets ~73
capability fixtures; this clause must budget its own.

**(c) Normalize case in exactly one place** — `evaluate_criterion` does `.strip().lower()` (`:50`);
intake does not.

**(d) Assertion D still ships** (§3.6) — `DETERMINISTIC_TYPES ⊆ EVALUATORS ∪ {special cases}`. It is
what makes the deferred evaluator workstream *safe to attempt later*: the moment someone adds
`automated_test` to `DETERMINISTIC_TYPES` without an evaluator, the suite goes red.

### 3.6 The general detector — REVISED after review

> Revision 1 proposed four assertions (A: producer ⊆ consumer; B: single declaration; C: cross-repo
> hash; D: no silent sink). **A and B were unsound; C was decoration.** §3.6.3 records why.

**The load-bearing correction:** `grep -rn automated_test src/` → **zero hits.** Every literal is in
`tests/`. The real producers of `evidence_type` are the **intent-packages repo's YAML** and **any HTTP
client**. No static test in this repo can see either.

> **The runtime ingress validators are the guard. The static detector is a lint on top of them.**
> Only ingress can see an off-repo producer. Revision 1 had this exactly backwards.

**What the detector is: a self-discovering consumer scan that fails closed.** AST-scan `src/` for
module-level collections of ≥2 string constants (`frozenset` / `set` / `list` / **dict keys** /
**tuple-of-tuples**) used in a membership test (`x in S`) or lookup (`S.get(x)`). **Each must be either
registered in the vocabulary registry or carry an explicit `# not-a-vocabulary: <reason>` marker.**

Discovery mechanical, exemption explicit — the direct analogue of WS-P2.15's import-resolved-node fix,
inverting the default from fail-open to fail-closed. **A hand-maintained registry alone IS the
WS-P2.15 failure mode: a vocabulary nobody registers is a guard nobody calls.**

On today's tree it surfaces `DETERMINISTIC_TYPES`, `JUDGMENT_TYPES`, `PASS_VALUES`, `FAIL_VALUES`,
`CHECK_*_CONCLUSIONS`, `KNOWN_FIELDS`, `KNOWN_BUDGETS`, `EVALUATORS`, and — with an env-config rule —
`enabled_capabilities` / `allowed_change_classes`, **including the `change_class` bug (§1.1)**, without
anyone remembering to register them.

Plus the one surviving assertion:

- **D — no silent sink.** `DETERMINISTIC_TYPES ⊆ EVALUATORS ∪ {declared special cases}`, the
  special-case list a *declared constant* the test reads. Sound, cheap, and it has a real subject today
  (`infra_lane.final`, hand-special-cased at `verifier_evaluators.py:60`). It is the only assertion
  that catches §2.3's second fallthrough.

#### 3.6.3 Why the dead assertions died — keep this

- **A was satisfiable by editing a fixture.** Its only visible subjects were fixtures *we own*. Switch
  it on, it reds on `automated_test` — and the cheapest green is `sed s/automated_test/pytest/` across
  `tests/`, which is *precisely* the mechanical fixture migration §3.2 sanctions for capabilities.
  Suite green, real packages still misrouting, detector never fires again. **The negative control does
  not catch this, because it is run by the person who took the shortcut.**
- **B contradicted D.** D *mandates* that `EVALUATORS` enumerate the vocabulary; B *forbids* a second
  enumeration. B needed two exemptions on day one, reading *"in fact this is a legitimate second
  enumeration"* — the WS-P2.15 tell that **the predicate is wrong masquerading as an exemption being
  justified.** Narrowed, B misses real copies (`EVALUATORS` is a dict; `verifier_criteria.py:108-138`
  is a tuple-of-tuples enumerating five evidence types; `drill_common.sh` is shell; factory-runner is
  another repo). Broadened, it false-positives on all four. Both horns fatal. **Dropped.**
- **C pinned a file, not a use** — §3.1.

#### 3.6.4 Blind spots — stated, not hidden

1. **`ac_id`** — DB **UUID** on a decomposition proposal vs the human string `"AC-001"` in evidence.
   One name, two value domains, no enumerated set. Set membership cannot see meaning. *(Backlog.)*
2. **Direction.** `⊆` is one-way. `DETERMINISTIC_TYPES` holds three aliases (`test`, `tests`,
   `pytest`) and nothing says which a producer emits. A consumer member no producer emits is a dead
   branch — how `infra_lane.final` got its special case. Add a **non-blocking** reverse report.
3. **Case/whitespace** — §3.5(d).
4. **Raw SQL.** `tests/persistence/test_append_only.py:190` inserts `'automated_test'` via raw SQL; a
   migration doing the same bypasses ingress *and* the detector.
5. **Evidence rows' own `evidence_type`.** `services/evidence.py` takes a free `evidence_type` from the
   writer, but the verifier keys only on `criterion.evidence_type` — evidence can carry any type and
   **nothing correlates it with the criterion it discharges.** (`build_pr_opened_evidence` writes
   `runner.pr.opened`, which is in neither set — harmless *only* because it is never consulted.)
   *(Backlog.)*
6. **Above all: the producers are in another repo. This test cannot see them.**

These go in the guard's docstring and in `CLAUDE.md`, as WS-P2.15's detector documented its own
blindness.

---

## 4. Units

| Unit | Repo | Route | Content |
|---|---|---|---|
| **U1** | orchestrator | factory | capability vocabulary fixture (derived, §3.1); ingress enforcement of **both** unit fields in `register_approved_unit`; migrate ~73 fixtures + `drill_common.sh` |
| **U2** | factory-runner | **hand-built** | derive `SUPPORTED_CAPABILITIES` from the fixture; **wire `can_create_pr` to refuse at RUN START** (§3.3); `OrchestratorClient.pr_binding` (with `expected_version=0` + idempotency key + **`attempt`**); derive `pr_number`; **POST before `submit`** |
| **U3** | orchestrator | factory | **`binding_attempt` column migration**; `submission_binding_recorded` (§3.4) **incl. a new clause in `authorize_transition`**; **drill-2/drill-4 binding writes** (§2.1) |
| **U4** | orchestrator | factory | D3's **safe half** (§3.5) — declare the five package types, validate the **criterion's** `evidence_type` at intake (`schemas.py:698`), assertion D |
| **U5** | orchestrator | factory | the self-discovering vocabulary scan (§3.6) — **split out of U4** |

**Order:** U1 → U2 → U3. U4 and U5 are independent of U3.

⚠ **U5 is split out because the detector is sized ~5× larger than rev 4 assumed.** An approximation of
§3.6.2's rule over `src/orchestrator` yields **46 subjects, not ~9** — including `SECRET_KEY_PARTS`
(×4), `POST_DEPLOY_AC_IDS` (×2), `COMMAND_TARGETS`, `AUTHORITY_PROFILE_RANK`, and ~20
`persistence/models.py` enum vocabularies whose source of truth is a **DB check-constraint**. Their
registry entries would read *"in fact this is a legitimate second enumeration, pinned by the DB"* —
**§3.6.3's own tell for a wrong predicate.** The predicate needs another pass, and **D3's safe half
must not be held hostage to it.**

Rev 4 also never said what "the vocabulary registry" **is** — a file? a decorator? a constant the test
imports? U5 must define that before it defines anything else.

**Deferred to their own workstreams (P1 backlog):** the deterministic evidence evaluator (§3.5 — needs
per-AC evidence from the runner + evidence-row-type keying + a command-aware evaluator); the `ac_id`
UUID/string collision; evidence-row `evidence_type` correlation (§3.6.4).

### 4.0 VERIFIED SOUND — do not re-litigate these

Four reviews have now confirmed these by reading the code. They are settled; a fifth reviewer should
spend its budget elsewhere.

- **The attempt arithmetic is sound; a worker cannot lie about it.** `claim_unit`
  (`services/claims.py:74,77`) does `unit.attempt_count += 1` then `Claim(attempt=unit.attempt_count)`
  (reclaim likewise, `:665,668`), so `claim.attempt == unit.attempt_count` for the live claim, always.
  The runner sends `attempt = int(claim["attempt"])` (`factory-runner/cli.py:257`) and reuses it at
  finalize (`:516`). `_authorize_write` → `validate_active_claim` (`claims.py:839-877`) takes the
  highest-attempt claim `FOR UPDATE` and requires a matching attempt **and** lease-token hash **and**
  state ∈ {CLAIMED, EXECUTING}. `arm_verification_head` arms on `unit.attempt_count`
  (`pr_bindings.py:110`) — **the same number the guard compares against.** No off-by-one.
- **"Run start" really is before `EXECUTING`.** `_prepare_claimed_workspace`
  (`factory-runner/cli.py:238-273`) calls `validate_authority` at `:238` and checks
  `permissions.can_claim` at `:247` — both `raise typer.Exit(1)` — and only *then* calls
  `client.claim` (`:251`) and `client.start` (`:263`, the transition to `EXECUTING`). A
  `can_create_pr` refusal beside the `can_claim` check fires with the unit still in `READY`. **Nothing
  is stranded.** (Note: if it *did* fire, the workflow fails while the unit stays `READY` → the
  dispatcher retries → three failures → `circuit_open`. Acceptable; worth one sentence in the AC.)
- **§3.5(a) is genuinely a runtime no-op.** `DETERMINISTIC_TYPES` and `JUDGMENT_TYPES` have **exactly
  one consumer between them** — `evaluate_criterion` line 51 (grep confirms; `_target_state`,
  `_replay_evaluation`, adjudication routing, dead-letter and reporting all key on `status`/`outcome`,
  never on set membership). All five package types today take the *second* disjunct
  (`not in DETERMINISTIC_TYPES`); afterwards they take the *first* (`in JUDGMENT_TYPES`). Same return.
- **D3's target field and its fixture budget are COMPLETE (review 5).** `PackageAcceptanceCriterion`
  rows are created in **exactly two** places — `services/package_intake.py:329` and
  `services/verifier_criteria.py:141` (`grep -rn "PackageAcceptanceCriterion(" src/` finds no others;
  decomposition creates none, no migration creates any). **Intake-only validation is sufficient.**
  Complete enumeration of criterion `evidence_type` values in existence: real packages declare
  `automated_test` (152), `human_review` (52), `automated_check` (8), `observation` (1); orchestrator
  tests add `review_note`, `test`, `pytest`, `human.review`. Against `DETERMINISTIC ∪ JUDGMENT ∪ {the
  five}`, **everything passes except `review_note`** — the plan's flagged list is complete. **WS-P2.16's
  own package passes.**
- **Generated post-deploy criteria are safe — and here is WHY, so nobody re-derives it.**
  `verifier_criteria.py:108-138` emits `release.deployment_observed`, `production.health`,
  `production.route_presence`, `production.auth_behavior`, `production.dispatch_posture` — **all five
  are already in `DETERMINISTIC_TYPES`** with evaluators — and they are constructed **directly as ORM
  rows** (`:141`), so they never traverse the intake schema at all. Safe on both counts.

### 4.1 Two landmines nobody had written down

- **`TransitionGuards` fields default to `False`** (`kernel/transitions.py:11-13`), and two call sites
  construct `TransitionGuards()` **bare** — `services/claims.py:717` and `services/evidence.py:1039`.
  Neither targets `SUBMITTED` today (both target `FAILED`), so a new default-`False` field is
  **fail-closed and safe now**. But the next `authorize_transition` caller that targets `SUBMITTED`
  with a bare `TransitionGuards()` is an **instant, silent deadlock**. Say so in the field's comment.
- **The WS-P2.16 units are grandfathered past their own guard — by accident.** The capability check
  lives at `register_approved_unit`, so U3/U4 are ingressed *before* U1 deploys and dispatched *after*.
  It works. **It works only because the gate is at ingress and not at dispatch.** The first implementer
  who "tightens" it by moving the check to dispatch bricks the workstream mid-flight.

---

## 5. Deploy — reversed, the factory halts

### 5.1 Production dispatch config (read live 2026-07-12)

Container `eqj5l7k705fhi12x9i74fqf0-181803097690`, image
`ghcr.io/alobarquest/orchestrator:d6d73b3-ws64-verifier-amd64`:

```
ORCHESTRATOR_DISPATCH_ENABLED                = true
ORCHESTRATOR_DISPATCH_ENABLED_CAPABILITIES   = ["repo.edit"]        # singleton!
ORCHESTRATOR_DISPATCH_ALLOWED_CHANGE_CLASSES = ["dependency-update"]
ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES = [orchestrator, brain, security-standards,
                                                     infraops-mcp-server, intent-packages, change-manager]
```

**Production confirms the design:** `ENABLED_CAPABILITIES` is already the **runner** vocabulary. The
73 `repository_write` fixtures are test-only drift.

**Every WS-P2.16 orchestrator unit must therefore satisfy ALL of `_blocked_reason`**
(`services/dispatch.py:258-279`) — nine gates, of which revision 1 enumerated two:

| Gate | Requirement for our units |
|---|---|
| `capability_not_enabled` (`:267`) | `required_capability` **exactly `repo.edit`** — production enables *only* that |
| `capability_not_authorized` (`:272`) | envelope must carry `repo.edit: allowed` |
| `change_class_not_allowed` (`:270`) | declare an explicit `change_class` **and** add it to the env list ⚠ |
| `conformance_missing` / `not_green` (`:279`) | envelope needs a `conformance` block, `status: green` ⚠ |
| `target_repository_not_allowed` | `AlobarQuest/orchestrator` — already allowed ✅ |

⚠ **`_change_class` falls back to `required_capability` when absent** (`:283`) — a unit omitting
`change_class` presents `repo.edit` as its change class and blocks. **Declare it explicitly.**

**One env write is required:** append this package's change class to `ALLOWED_CHANGE_CLASSES`.
(Because U2 is hand-built, **no `ALLOWED_TARGET_REPOSITORIES` change is needed** — see §5.2.)

Per `CLAUDE.md`: write, then **verify from inside the container** before restarting. Coolify's env
PATCH intermittently 500s here; fallback is delete-by-env-uuid + recreate. `/envs` responses carry
`real_value` for every variable — parse in-process, print only whitelisted keys.

### 5.2 Why U2 is hand-built

`dispatch_workflow_id` is **process-global** (`config.py:19` → `"factory-runner-pilot.yml"`), and
`git ls-tree origin/main .github/workflows/` in factory-runner returns **only `factory-runner.yml`**.
Dispatching a unit that targets factory-runner POSTs to a workflow that does not exist → **404** →
three identical failures → **`circuit_open`** → `blocked`. The factory cannot bootstrap the workflow
that would make it dispatchable. Devon's call: **hand-build U2** with a normal PR.

*(The irony is worth naming: the workstream that fixes the PR-binding chain cannot itself be built by
the factory — which is exactly the hole it exists to close.)*

### 5.3 There is NO pinned `workflow_ref` — revision 1 was wrong on every count

| Thing | Where | Value | What it controls |
|---|---|---|---|
| `dispatch_workflow_ref` | `orchestrator/config.py:20` | `"main"` | the ref of the **target repo's** pilot workflow — nothing to do with runner code |
| reusable-workflow ref | `orchestrator/.github/workflows/factory-runner-pilot.yml` | `…/factory-runner.yml@main` | already `@main` |
| **the runner code itself** | `factory-runner/.github/workflows/factory-runner.yml` | `uv tool install git+https://…/factory-runner.git` | **no ref, no tag** — default-branch HEAD at run time |

**Consequences:** (1) there is nothing to "advance" — merging U2 to factory-runner `main` is
**immediately and globally live**, and rollback means reverting `main`; (2) a negative control that
edits `cli.py` locally is **not** exercised by the workflow, since `actions/checkout` checks out the
caller's tree while `uv tool install` fetches `main`; (3) this unpinned install is nonetheless the
**real ordering guarantee** — U2 is live everywhere the instant it merges.

### 5.4 In-flight units when U3 deploys

The guard runs at **transition** time, not dispatch time. A unit already in `EXECUTING` under the old
console script opens a PR, submits, and is **refused**: the runner exits nonzero, the unit sits in
`EXECUTING` until lease expiry → requeue → re-dispatch, **burning an attempt against `max_attempts`**.
Recoverable, but real. Deploy U3 when no unit is mid-flight, or accept the burned attempt.

### 5.5 Order

1. **U1 + U4** (orchestrator) — ingress + detector + D3. Safe: ingress affects only *new* units.
2. **U2** merged to factory-runner `main` → live immediately (§5.3). **Verify a real unit writes a
   binding row** before proceeding.
3. **Only then U3** — the submit guard.

---

## 6. Verification — negative controls, not assertions

| Guard | Negative control — plant the *real* bug |
|---|---|
| capability ingress | seed a unit with `repository_write` → `unknown_capability` |
| vocabulary **derivation** | **hardcode `SUPPORTED_CAPABILITIES`** in factory-runner and add a term → red (*not* "flip a byte → hash reds") |
| two-fixture drift | `capability_vocabulary["runner"] != sorted(envelope["capabilities"])` → red |
| `can_create_pr` | envelope without `github.pr.create` → runner refuses **at run start**, before doing work (not an `Exit(1)` after the PR step, which strands the unit in `EXECUTING`) |
| **binding written (D1 — the headline defect)** | **delete the `pr_binding` call from `cli.py` → a test IN FACTORY-RUNNER reds**, asserting `cli` invokes `client.pr_binding` on the submit path, plus a command-shape contract test mirroring `tests/test_orchestrator_command_contract.py`. **See the warning below.** |
| vocabulary **ships** | `uv build && unzip -l dist/*.whl \| grep capability` → present; and present in the built image. **A source-tree test is not evidence** (§3.1) |
| drill coverage of the guard | drill-2 / drill-4 seeded with a PR-capable envelope submit **without** a binding → refused (this is what makes the guard non-vacuous; see §2.1) |
| submit guard | PR-capable unit reaches `EXECUTING` with no binding → submit refused; **and** a retried submit still replays idempotently and still surfaces `version_conflict` |
| **stale binding** | **re-submit after `REVISION_REQUIRED` with the attempt-2 `pr_binding` POST suppressed** → submit **refused** (rev 2's guard would have PASSED this — it is the whole reason for the `attempt` column) |
| `evidence_type` intake | a package declaring `automated_tset` (typo) → **named error at intake**, not a silent `judgment_required` at verify |
| D3 no-regression | an `automated_test` AC still evaluates to `judgment_required` — **byte-identical behavior to today** (§3.5 ships no evaluator) |
| detector (D) | add a `DETERMINISTIC_TYPES` member with no `EVALUATORS` entry → red |
| detector (scan) | add an unregistered, unmarked string-constant set used in a membership test → red |

> ☠ **REV 4's D1 CONTROL WAS NOT IMPLEMENTABLE BY ANY HARNESS THAT EXISTS — and it is the control for
> the defect this entire workstream exists to fix.**
>
> It said: *"delete the `pr_binding` call from `cli.py` → a drill on the real HTTP surface reds."* But:
>
> - **factory-runner has no `scripts/`, no drill, no integration harness** — its tests are unit-level.
> - **The orchestrator's drills speak the HTTP API directly** (`drill-3:71` posts to `/pr-binding`
>   itself). **No orchestrator drill invokes factory-runner's `cli.py`.** Deleting the call reds
>   **nothing** in the orchestrator.
> - **Worse: `drill-3` ALREADY exercises the pr-binding route end-to-end today — and D1 exists
>   anyway.** A new orchestrator drill on "the real HTTP surface" proves only what drill-3 already
>   proves: that the *route* works. It says **nothing** about whether the runner *calls* it.
>
> **This is the WS-P2.1 defect, verbatim: _"a test fixture calling a service is not evidence the
> service has a caller."_ The plan quotes that lesson and then wrote a control that embodies it.**
>
> **The only control that discharges D1 lives in FACTORY-RUNNER**, asserting the runner's own submit
> path invokes `client.pr_binding`. The orchestrator drill is still worth having — it proves the guard
> refuses — but it must not be mistaken for evidence of a caller.

**`make check` green — read the collected-test count.** Exit 0 proves nothing; exit 5 ("no tests
collected") is swallowed by the vendored Makefile. All drills green. `/code-review` on the diff.
**Never run two pytest suites against the test DB concurrently** — the fixtures drop and recreate it.

---

## 7. Route through the factory

Author the package → **two independent adversarial reviewers** (they killed revision 1; budget for
them) → Devon approves → annotated tag rescuing `approval_ledger_commit` (intent-packages PRs are
**squash-only**) → intake **from a browser** (`/api` is M2M-only at the proxy) → decomposition proposal
(**`ac_mappings[].ac_id` wants the criterion's DB UUID, not `"AC-001"`**) → Devon approves
(*irreversible*) → **SYSTEM `ready` command** (`DRAFT → READY` is a SYSTEM edge; units do not become
ready on their own) → per-unit authority approval → build → adjudicate → **independent adjudication in
a separate session** → merge.

`profile_fields.repo` is a single string and this package spans two repos — name `orchestrator` and
document the split in `rollback_plan`, as WS-2.3 did.

**Package `evidence_type` must be one of the five legal values** (§2.3) — `automated_test` for
`gate:`/`scan:`-tagged evidence, `human_review` for `human:`.
