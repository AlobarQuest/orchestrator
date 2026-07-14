# ADR 0001 — The work-unit authority envelope is the WS-4.1 ↔ WS-4.2 contract

**Date:** 2026-07-09
**Status:** Accepted
**Workstream:** WS-6.4.0 (dispatch/runner seam closure)
**Companion:** `~/docs/software-delivery-system/2026-07-09-ws64a-readiness-blocker-report.md`

## Context

WS-4.2 (the orchestrator's dispatch adapter) and WS-4.1 (`AlobarQuest/factory-runner`) were
each built to completion and each unit-tested — against **their own fixtures**. Nothing ever
validated a single envelope against both ends. The seam had therefore never executed: as of
2026-07-09, `gh run list` showed **zero** runs of `factory-runner-pilot.yml` in orchestrator
and zero runs in factory-runner, and factory-runner's `PROJECT.md` recorded its one pilot
target as `merged, credentialed, not dispatched`.

WS-6.4a's readiness pass found six incompatibilities. Five were between the two repos; the
sixth (B6) was found *by the contract test written to close the other five*, before any code
shipped.

The two ends disagreed most sharply on **vocabulary**:

- The orchestrator's dispatch gate requires `unit.required_capability` to appear in the
  envelope at level `allowed` (`services/dispatch.py`), and its own fixtures used the
  **security-standards registry** vocabulary (`repository_write`, `pr_open`, …).
- factory-runner's `validate_authority` **raises** on any capability outside a six-term
  runner-tool vocabulary, and accepts only two levels
  (`factory_runner/authority.py`: `SUPPORTED_CAPABILITIES`, `SUPPORTED_LEVELS`).

An envelope satisfying one raised in the other. `required_capability` is an unconstrained
`String` column, so the two "worked" only by author discipline — the fork was one authoring
mistake away from becoming permanent and invisible.

## Decision

**1. Work-unit authority envelopes use the runner vocabulary.**
`capabilities` keys are drawn from `{repo.read, repo.edit, command.run, github.pr.create,
orchestrator.claim, orchestrator.evidence.write}` and levels from `{allowed, prohibited}`.
`required_capability` names one of those keys (for dependency-update: `repo.edit`).

Intent packages continue to use the security-standards registry vocabulary in their own
`authority.{allowed,requires_approval,prohibited}` lists. The **projection** from package
authority to unit authority is applied by the decomposition author. It is a *narrowing*:

| intent-package (registry) | work-unit (runner) |
|---|---|
| `repository_read` | `repo.read` |
| `repository_write` | `repo.edit` |
| `test_execution` | `command.run` (+ `constraints.allowed_commands`) |
| `pr_open` | `github.pr.create` |
| *(orchestrator protocol)* | `orchestrator.claim`, `orchestrator.evidence.write` |
| `merge_to_main` | **never projected** — Devon's merge gate is permanent |

**Level collapse is part of the projection.** The orchestrator kernel models three tiers
(`prohibited < requires_approval < allowed`); the runner accepts two. A capability that is
`requires_approval` in the package **must not** appear in a unit envelope at all — it cannot be
expressed, and a unit is not the place to resolve a human gate.

Automated projection (a validator that derives unit envelopes from package authority) is
deferred to Program Phase 2. Until then the ADR plus the contract test are the enforcement.

**2. The envelope's routing and admission fields are fingerprinted by value.**
`constraints` and `change_class` entered `KNOWN_FIELDS` and `AuthorityEnvelope.normalized()`.
Previously `normalized()` emitted only `budgets`, `capabilities`, and the sorted *names* of
unknown fields — so `constraints.target_repository` (which decides **where code ships**) and
`change_class` (the dispatch allowlist key) were **not covered by the fingerprint a human
approves**. Two units targeting different repositories hashed identically.

This was the single largest governance hole in the seam. It was fixed while production held
2 work units, both `completed`, and an empty ledger — the migration cost was zero, and would
have risen with every approved unit thereafter.

**3. `constraints.work_unit_id` is server-owned, stamped at proposal time.**
The runner refuses any envelope that does not name its own work unit — correctly, since that
binding is what stops a captured envelope being replayed against a different unit. But the
UUID does not exist when a human authors the envelope. It *is* derivable at proposal time,
because `_proposal_unit_id = uuid5(proposal.id, unit_key)` is deterministic in the just-flushed
proposal id. So `submit_decomposition_proposal` stamps it, and the approver reviews exactly the
envelope the runner will be served. An author-supplied `work_unit_id` is rejected
(`authority_work_unit_id_forbidden`) rather than silently overwritten.

**4. Dispatch routes per unit, never per process.**
`ORCHESTRATOR_DISPATCH_TARGET_REPOSITORY` (a single process-global string) is replaced by
`ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES` (an allowlist, **empty by default**).
Dispatch resolves the repository from `unit.authority.constraints.target_repository`.

This is not ergonomics. The runner requires `target_repo == current_repo` — it may only mutate
the repository it checked out. A process-global target would have sent every unit of a fan-out
to whichever repository was configured at startup: a runner opening a dependency PR **against
the wrong repository**. That fails *open*, silently. Per-unit routing plus an explicit allowlist
fails closed twice (`target_repository_missing`, `target_repository_not_allowed`).

**5. Conformance is attested per unit, in the envelope.**
`enforcement_snapshot.conformance` (per package revision, written at intake) cannot describe
a fan-out: intake runs *before* decomposition chooses the target repositories, and in
production the orchestrator has no checkout of them to inspect. So conformance moved to
`authority.conformance`, attested by the decomposition author against that unit's own target
repository, and — because the envelope is fingerprinted (decision 2) — covered by the human's
per-unit authority approval.

The claim is `{status, standards_touched, accepted_standards}`. It is shape-validated at
proposal time (`authority_conformance_invalid`) rather than admitted as an opaque unknown
field, and a missing claim fails closed (`conformance_missing`).

**`accepted_standards` must originate from a real waiver source** — project-standards'
`exceptions:` frontmatter or security-standards' `.security-scan-allow.toml` — never from
`standards_touched`. The gate admits when `status == "green"` **or** `touched ⊆ accepted`; if
a producer ever echoes `accepted = touched`, that second branch becomes a tautology admitting
everything. Both sources are importable and local-only (`security_scan.cli.scan`,
`portfolio.compliance.build_rows`), so no network is needed to produce an honest claim.

A consequence worth stating: `package_sources.py` needs no change at all. The intake payload
never had to grow a conformance producer, because conformance was never a package-revision
fact.

**6. `change_class` and `conformance` are orchestrator-owned; the runner grants nothing from
either.** factory-runner's `AuthorityEnvelope` is `extra="forbid"`, so it rejected the
envelope outright once the orchestrator began serving `change_class` (B6, caught by the
contract test before anything shipped). Rather than smuggle these fields into `constraints` —
which means "bounds on this unit's execution" and is read by the runner — the runner declares
both explicitly as optional fields it carries and ignores. Capabilities remain the sole source
of runner permissions.

**7. Dependency updates with `repo.edit` declare their intended mutators.**
`constraints.mutation_commands` is required only when `change_class` is `dependency-update`
and `repo.edit` is allowed. It is an ordered, fingerprinted list whose entries must also appear
in the complete ordered `allowed_commands` list without changing spelling. The field is a subset
declaration, not semantic proof that a command mutates a dependency; it binds the approved
envelope to the commands expected to do so.

This is a coordinated cross-repository contract field: the shared fixture changes together in
orchestrator and factory-runner. Existing stored envelopes are immutable and are not rewritten;
the new declaration applies only to newly admitted dependency-update `repo.edit` envelopes.

## Enforcement — the test that did not exist

`tests/fixtures/runner_authority_envelope.json` is the single source of truth for the envelope
shape, with a byte-identical copy in factory-runner under the same name.

- **orchestrator** (`tests/contract/test_runner_envelope_contract.py`) drives the real
  intake → decomposition proposal → approval → `runner_brief` path, asserts the served envelope
  equals the golden file, and asserts `dispatch_work_unit` admits it and routes to the unit's
  own repository.
- **factory-runner** (`tests/test_orchestrator_envelope_contract.py`) asserts
  `validate_authority` accepts that same envelope and yields the expected permissions.

Both tests pin the same `CONTRACT_SHA256` over the canonical JSON, so a one-sided edit fails
loudly in the repo that was not updated. **One envelope, both validators** is WS-6.4.0's exit
criterion.

## Consequences

- A fan-out of N repos is **1 decomposition approval + N authority approvals + N ready
  commands + N dispatches**. Each unit's envelope differs (different repo, different
  work_unit_id) and therefore has a distinct fingerprint requiring its own named human
  approval. This is *more* human gating than the WS-6.4 handoff assumed ("one named human
  approval"), not less. We accept the friction rather than build batch approval.
- Changing `normalized()` rewrote every authority fingerprint. Safe only because production
  held no approved-and-pending units. **Any future change to `KNOWN_FIELDS` carries a
  migration cost proportional to the live ledger.**
- The two capability vocabularies remain separate systems reconciled by a human-applied
  projection. That is a known, bounded debt with a named owner (Program Phase 2), not an
  oversight.

## Related findings, not fixed here

- ~~`is_expansion()` (`kernel/authority.py`) has **zero call sites in `src/`**.~~
  **RESOLVED by WS-P2.15 (2026-07-12): the function is DELETED.** The resolution matters more
  than the deletion, so state it precisely — a false equivalence here would be worse than the
  dead function:

  - The CLAUDE.md invariant **is** enforced, but by `classify_context_update()`
    (`kernel/context.py`) via `services/context.py::_effective_decision`, which requires an
    `Approval` with a named `approved_by` bound to the exact `context_fingerprint`.
  - **It is NOT the same check.** `classify_context_update()` compares *standing contexts* —
    capability **sets** and authority-profile **rank**. `is_expansion()` compared *authority
    envelopes* — capability **levels**, **budgets** (`max_attempts`, `max_llm_calls`), and
    fail-closed on unknown fields. **Budget and capability-level expansion have NO detector.**
  - That is safe for exactly one reason, and it is **structural, not behavioural**: a work
    unit's `authority` envelope is **write-once**. It is assigned only at construction (two
    sites: `services/packages.py`, `services/deployment_observations.py`), so there is no "old
    vs new" for an envelope comparison to compare. The live budget-raising path (`retry`,
    `services/claims.py`) raises the **column** `unit.max_attempts` and never touched the
    envelope — `is_expansion()` never saw it either.
  - **`tests/architecture/test_authority_write_once.py` now enforces that premise**, scanning
    attribute assignment, `setattr`, bulk `update().values()`, and in-place JSON mutation. **If
    WS-P2.4 (cost controls) introduces a path that raises a unit's budget by mutating the
    envelope, that test goes red and forces a fail-closed check to ship with it.**

  Read that last point as the actual decision: the guard was replaced by a structural invariant
  that fails loudly, rather than a latent function nobody called.
- The dispatch gate's `touched ⊆ accepted` branch is only as honest as its producer. Nothing
  structurally prevents a future producer from echoing `accepted = touched`. A tighter gate
  (requiring `status == "green"` and treating acceptance as evidence rather than a bypass) is
  worth considering in Program Phase 2.
