# WS-P2.6 — Traceability Query ("why is this code in production?")

**Date:** 2026-07-25
**Workstream:** WS-P2.6 (Wave 2 — LEGIBLE) · program plan `[C#5]`
**Status:** design approved; ready for implementation plan
**Owning repo:** `AlobarQuest/orchestrator` (orchestrator-only; no factory-runner change)

## Summary

A first-class **bidirectional traceability query** over the existing canonical chain
`intent ↔ work unit ↔ event ↔ PR ↔ commit ↔ artifact ↔ deployment ↔ observation`.
Given any node on the chain, resolve the full ordered chain and answer *"why is this code
in production?"* and its inverses.

This is a thin **resolver + assembler** that **composes** the WS-P2.5 projections
(`evidence_pack_projection`, `release_evidence_pack_response`) and the existing binding /
observation fetchers. It is not a re-walk of the graph and it introduces no new canonical
data — every link it traverses already exists post-WS-5.2/5.3/6.1 and WS-P2.5.

Program exit criterion **#6** ("PR, commit, artifact, **deployment, and observation** linked
bidirectionally") is this workstream's definition of done. Note that #6 names `deployment`
and `observation` as **two distinct nodes** — both are in scope (see Decisions).

## Decisions (locked)

1. **Full bidirectional resolver, no migration.** Entry keys: `work_unit_id`, `revision_id`,
   `artifact_digest`, `commit`, `pr_number` (+ `source_repository`), `environment`. Each
   resolves to the canonical anchor via **query-time filters** on the existing hub tables. At
   current ledger scale a materialized reverse index is unnecessary; **no migration** this
   increment.
2. **One report view: the "why in production?" chain.** No release-notes, rollback-impact, or
   audit-report views this increment. The raw composed chain is the deliverable.
3. **JSON `/api` only.** No `/review` GUI page this increment (machine-readable audit surface;
   add a GUI later only if an operator need appears).
4. **Structured JSON only.** No rendered markdown/text — so no new public-surface exposure and
   no new redaction discipline. (The JSON is full-fidelity and auth-gated, like the evidence-pack
   JSON.)
5. **The observation tail is IN scope — both `ReconciliationCondition` and `Observation`.** The
   chain's terminal `observation` node surfaces, per unit, the FK-linked
   `ReconciliationCondition` divergence verdicts (`digest_divergence`, `deploy_split_brain`,
   `pr_state_divergence`, `check_result_flip`, `external_merge_alarm`) **and** the raw linked
   generic `Observation` rows. This is the one node whose omission would leave exit criterion #6
   at 4/5 links with **no other scheduled owner** (WS-P2.8 produces more observations but never
   builds the query surface). The linkage already exists, so including it is nearly free.

## Non-goals

- **No observation-as-entry-key.** The reverse direction (unit → its observations/conditions)
  is covered by including them in the chain. Entering the query *from* an observation id is
  deferred (YAGNI).
- **No release-notes / change-summary / rollback-impact / audit-report views** (defer).
- **No `/review` GUI, no markdown/text rendering, no orchestrator→GitHub (or any) egress.**
- **No migration / materialized reverse index.**
- **No new observation producers.** The generic `Observation` table's only producer today is
  `POST /api/v1/observations`; no external monitor is wired yet, so the observation tail will
  usually be **empty in production** until monitors post. This is expected — the query proves the
  *linkage* and lights up automatically as producers come online. Wiring monitors is out of scope.
- **No dispatch.** `ORCHESTRATOR_DISPATCH_ENABLED=false` throughout.

## Data model (verified 2026-07-25 against `persistence/models.py`)

The chain is assembled from these existing rows. **The `ReleaseArtifactBinding` is a hub row** —
it alone carries `artifact_digest`, `source_commit`, `merge_commit`, `implementation_pr_number`,
`source_repository`, `work_package_revision_id`, and `work_unit_id` together — so most reverse
lookups are single-table filters on it.

| Chain node | Source row | Key fields used | Link to unit |
|---|---|---|---|
| intent | `WorkPackageRevision` | `revision`, `content_hash`, `source_path`, `source_commit`, `registered_by` | `WorkUnit.work_package_revision_id` |
| unit | `WorkUnit` | `id`, `unit_key`, `title`, `state`, `authority_fingerprint` | self |
| approvals | `Approval` | `subject_type` (`authority`), `decision`, `approved_by` | `subject_id == unit.id` |
| PR | `UnitPrBinding` | `pr_number`, `head_sha` | PK `work_unit_id` |
| commit | `ReleaseArtifactBinding` | `source_commit`, `merge_commit`, `source_repository`, `implementation_pr_number` | `work_unit_id` |
| artifact | `ReleaseArtifactBinding` | `artifact_digest`, registry/repo/name/tag, workflow_*, builder_*, provenance_*, sbom_* | `work_unit_id` |
| deployment | `DeploymentObservation` | `environment`, `observed_artifact_digest`, `deployment_ref/url`, `deployer`, probe/route/auth/status summaries | `implementation_work_unit_id`; via `release_artifact_binding_id` |
| observation | `ReconciliationCondition` | `observation_kind`, `condition_type`, `detail`, `resolution_generation` | **FK `work_unit_id`** |
| observation | `Observation` | `source_system`, `observation_type`, `status`, `severity`, `summary`, `facts` | soft: `subject_type == "work_unit"` ∧ `subject_reference == str(unit.id)` |

**No migration** — all rows and columns already exist.

## Components

### 1. Service — `src/orchestrator/services/traceability.py` (new)

Two responsibilities: **resolve** an entry key to anchor unit(s), then **assemble** each unit's
ordered chain by composing existing projections/fetchers.

```
resolve_anchors(session, anchor)  -> tuple[AnchorMatch, ...]      # AnchorMatch = (unit_id, matched_on, value)
build_chain(session, unit_id)     -> TraceabilityChain            # ordered hops for one unit
traceability_response(session, anchor) -> TraceabilityResponse    # {anchor, chains: [...]}
```

**Resolver** — `anchor` is a validated, exactly-one discriminated value:

| Anchor | Resolution (query-time filter) | Cardinality |
|---|---|---|
| `work_unit_id` | the unit itself (`session.get`) | 1 |
| `revision_id` | all `WorkUnit` where `work_package_revision_id == revision_id`, ordered by `unit_key` | N |
| `artifact_digest` | `ReleaseArtifactBinding.artifact_digest == v` → distinct `work_unit_id` | 1..N |
| `commit` | `ReleaseArtifactBinding` where `source_commit == v OR merge_commit == v` → unit(s) | 1..N |
| `pr` | `(source_repository, implementation_pr_number)` on `ReleaseArtifactBinding`; if no repo given, fall back to `UnitPrBinding.pr_number` | 1..N |
| `environment` | `DeploymentObservation.environment == v`, deduped to the **latest per `implementation_work_unit_id`** by `(observed_at, recorded_at, id)` → unit(s) | 0..N |

An anchor that resolves to zero units returns an empty `chains` list (not an error) — "nothing
is deployed to `staging`" is a valid answer. A **malformed** anchor (bad UUID, zero anchors,
two+ anchors, `commit` not 40-hex) is a `DomainError`.

**Assembler** — `build_chain(session, unit_id)` composes:
- `evidence_pack_projection(session, unit_id)` → intent (revision provenance), unit, authority,
  approvals, events. (Reuse; do **not** re-query these.)
- `list_release_artifacts(session, unit_id)` → commit + artifact hops (ordered; typically 1).
- `list_deployment_observations(session, binding_id)` per artifact binding → deployment hops.
- `ReconciliationCondition` where `work_unit_id == unit_id` (append-only; ordered by recorded time).
- `Observation` where `subject_type == "work_unit" AND subject_reference == str(unit_id)`
  (ordered by `observed_at, received_at, id`). Mirror the existing `_correlated_unit` pattern in
  `reconciliation_detection.py` rather than inventing new linkage.

`UnitPrBinding` is fetched via the existing `get_pr_binding` (or `session.get`) for the PR hop.

**Scope-guard hygiene (this module):** docstrings/prose must avoid the bare tokens `deploy`,
`dispatch` (use `deployment`/`deploys`/`deployments`/`dispatches` — suffixed forms do not match)
and `merges` (use `merge`/`merged`). Attribute access like `row.merge_commit` and string keys like
`"merge_commit"` are fine — the ws32/ws33 guards scan bare-token string literals/prose, and
`merge_commit` tokenizes to `merge`+`commit`, neither of which is the forbidden `merges`.

### 2. Schemas — `src/orchestrator/api/schemas.py` (extend)

New Pydantic response models (JSON-safe, mirroring `EvidencePack*Response` style):
`TraceabilityResponse`, `TraceabilityAnchorResponse` (`matched_on`, `value`),
`TraceabilityChainResponse` (the ordered hops), and per-hop models
(`TraceabilityIntentHop`, `…UnitHop`, `…PrHop`, `…CommitHop`, `…ArtifactHop`,
`…DeploymentHop`, `…ObservationHop` carrying `conditions[]` + `observations[]`).

Reuse existing sub-responses where they already exist (e.g. approval/authority shapes from the
evidence-pack schemas) rather than duplicating fields.

### 3. JSON route — `src/orchestrator/api/routes.py` (extend)

```
GET /api/v1/traceability
    ?work_unit_id= | ?revision_id= | ?artifact_digest= | ?commit=
    | ?pr_number= (&source_repository=) | ?environment=
```

```python
@router.get("/traceability", response_model=TraceabilityResponse)
def traceability_route(
    _actor: ActorDep,
    session: SessionDep,
    work_unit_id: str | None = None,
    revision_id: str | None = None,
    artifact_digest: str | None = None,
    commit: str | None = None,
    pr_number: int | None = None,
    source_repository: str | None = None,
    environment: str | None = None,
) -> object:
    ...
```

- **Auth-only** (`ActorDep`, no role gate) — identical posture to the evidence-pack routes;
  full-fidelity JSON, inside the trust boundary.
- The route builds the discriminated `anchor` and calls `traceability_response`. It **parses and
  validates all input up front, raising `DomainError`** for bad UUIDs, wrong anchor count, or a
  malformed commit — never letting stdlib (`uuid.UUID`, int coercion) raise (WS-P2.3: only
  `DomainError`/`APIAuthenticationError` have handlers; anything else is an unhandled 500).

### 4. Route inventory — `tests/architecture/test_scope_guards.py` (extend)

Add `/api/v1/traceability` to `test_production_get_route_inventory_is_explicit`'s exact GET set,
or CI reds. **No** `NON_JSON_SUCCESS_PATHS` entry (JSON only).

## Error handling

| Condition | Result |
|---|---|
| No anchor / two+ anchors | `DomainError("traceability_anchor_required" / "traceability_anchor_ambiguous", …)` |
| Malformed `work_unit_id`/`revision_id` UUID | `DomainError("invalid_*_id", …)` (wrap `uuid.UUID`) |
| `commit` not 40-hex | `DomainError("invalid_commit", …)` |
| `pr_number <= 0` | `DomainError("invalid_pr_number", …)` |
| **Filter** anchor (`artifact_digest`/`commit`/`pr`/`environment`) matches 0 rows | success, `chains: []` — "nothing matches" is a valid answer |
| **Named** anchor (`work_unit_id`/`revision_id`) refers to a nonexistent row | `DomainError("work_unit_not_found" / "revision_not_found", …)` — you named a specific entity that does not exist (client error), consistent with the evidence-pack routes |

## Ordering / determinism

- `chains` ordered by `unit_key` (revision anchor) or by resolution order (single-unit anchors).
- Within a chain: artifacts by `(recorded_at, id)`; deployments by `(recorded_at, id)`;
  conditions by recorded time; observations by `(observed_at, received_at, id)` — matching the
  existing services' orderings so output is stable.

## Known non-obvious invariants this design must satisfy

- **Route input parsing raises `DomainError`, never stdlib** (WS-P2.3) — no unhandled 500s.
- **New GET route → exact route-inventory set** in `test_scope_guards.py` (WS-P2.4/P2.5 CI break).
- **ws32/ws33 word guards** — keep the new module's docstrings clear of bare `deploy`/`dispatch`/
  `merges`.
- **`make check` exit 0 ≠ tests ran** — read the collected-test count; run on a **clean tree**
  (`ruff format --check .` over the whole repo may red on pre-existing format debt in untouched
  files — differential, not this change).
- **Compose, don't reimplement** — reuse `evidence_pack_projection`,
  `release_evidence_pack_response`'s building blocks, `list_release_artifacts`,
  `list_deployment_observations`, and the `_correlated_unit` linkage pattern. No new graph walk.
- **Read-only** — this module never writes, never transitions, never dispatches/deploys/writes to
  git. Pure projection over canonical rows, like `release_evidence_pack.py`.

## Testing (TDD)

- **Resolver:** each anchor type resolves correctly; zero-anchor and multi-anchor rejected;
  bad UUID / bad commit / non-positive PR → `DomainError`; `environment` picks the latest
  observation per unit; fan-out anchors (`revision`, shared `digest`) return all units.
- **Assembler:** chain hops correct and ordered; observation tail includes both
  `ReconciliationCondition` and `Observation`; empty tail when none exist; PR/artifact/deployment
  hops absent-gracefully when a unit has no binding yet.
- **Route:** auth required; each anchor round-trips; JSON schema valid (every success response has
  a JSON schema — the every-success-has-a-schema invariant); malformed input → clean 4xx not 500.
- **Guards:** route-inventory test updated; new module passes ws32/ws33.

## Definition of done

- Bidirectional traceability query over `intent ↔ unit ↔ event ↔ PR ↔ commit ↔ artifact ↔
  deployment ↔ observation`, composing existing projections; the "why is this code in production?"
  chain returned as structured JSON on `GET /api/v1/traceability`.
- Observation node (conditions + observations) included → program exit criterion **#6** satisfied
  (bidirectional linkage of PR, commit, artifact, deployment, and observation).
- TDD; per-task two-stage reviews; final adversarial whole-branch review on the most capable model.
- `make check` green on a clean tree (collected count read); `/code-review`; Devon merges.
- Deployed to `sds.alobar.net` (amd64 single-manifest via the pinned security-standards
  git-archive registry build-context; digest-verified; no migration so no migrate-first step);
  new route confirmed present in prod `openapi.json`.
- Wave-2 closeout note in `~/docs/software-delivery-system/`. After WS-P2.6, the Wave-2
  Evidence + Traceability pair is complete.
