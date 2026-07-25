# WS-P2.5 Increment 2 — Per-Release Evidence Pack

**Date:** 2026-07-25
**Status:** Approved (design)
**Repo:** `AlobarQuest/orchestrator`
**Predecessor:** Increment 1 (per-unit Evidence Pack) — shipped and deployed 2026-07-25
(`docs/superpowers/specs/2026-07-25-evidence-pack-into-pr-design.md`).

## Summary

Add a **per-release** Evidence Pack: a release-scoped evidentiary record spanning all
work units in a package revision, joined to that revision's release artifact bindings and
deployment observations. It is exposed two ways from **one** assembler:

- a structured JSON GET (`/api/v1/revisions/{revision_id}/evidence-pack`) for machine
  consumers, primarily WS-P2.6 (traceability query); and
- a read-only `/review` GUI page (`/review/revisions/{revision_id}/evidence-pack`) for
  human operators.

The per-release pack **composes** the Increment-1 per-unit `EvidencePackResponse`; it does
not reimplement per-unit assembly.

## Decisions (locked)

1. **A "release" is a package revision** (`WorkPackageRevision`). Chosen over per-binding or
   per-deployment because the program plan specifies "all units in a package revision", and
   both `ReleaseArtifactBinding` and `DeploymentObservation` already carry
   `work_package_revision_id`, making the joins direct.
2. **Surface = JSON + `/review` GUI page. No PR comment.** A release spans N units → N PRs
   (or none, or across repos), so there is no canonical PR target; and a release-level
   comment would require the orchestrator to gain a GitHub-posting (egress) capability it
   does not have today, or cross-unit relay coordination no single runner owns. Deferred to
   WS-P2.6 or later, if a clean target ever emerges.
3. **Standards/skill versions: deferred** (as in Increment 1) to the immutable-snapshot
   work. The revision's `enforcement_snapshot`/`registry_version` exist but are not surfaced
   here.
4. **No redaction machinery.** Redaction exists in Increment 1 only for the *markdown relayed
   onto a possibly-public PR comment*. This increment emits no markdown and no PR comment.
   The JSON is auth-only and the GUI is behind forward-auth — both stay inside the trust
   boundary, so both are full-fidelity, exactly like the per-unit JSON route and the per-unit
   `/review` GUI page.

## Non-goals

- No rewrite of the per-unit pack — compose `EvidencePackResponse` only.
- No post-verification refresh (Increment 1.5, separately deferred).
- No immutable/hashed snapshot (separately deferred).
- No markdown view, no PR comment, no orchestrator→GitHub posting capability.
- No standards/skill-version fields.
- No database migration — the pack assembles from existing rows.

## Data model (verified 2026-07-25)

```
WorkPackageRevision (the "release")
 ├── WorkUnit (N)                        work_units.work_package_revision_id
 ├── ReleaseArtifactBinding (N)          release_artifact_bindings.work_package_revision_id
 │      └── artifact_digest, commits, PR#, workflow, builder, provenance, sbom, summary
 └── DeploymentObservation (N)           deployment_observations.work_package_revision_id
        └── environment, observed_artifact_digest, base_url, deployer, observed_at,
            probe/route/auth/dispatch/status summaries
```

Because bindings and observations store `work_package_revision_id` directly, the three
release-level collections are each fetched with a single indexed query keyed on the revision
— no per-unit walk is required to gather them.

## Components

### 1. Service — `src/orchestrator/services/release_evidence_pack.py` (new)

Single public function:

```python
def release_evidence_pack_response(
    session: Session, revision_id: uuid.UUID
) -> ReleaseEvidencePackResponse: ...
```

Behavior:
- `revision = session.get(WorkPackageRevision, revision_id)`; if `None`, raise
  `DomainError("revision_not_found", "package revision does not exist", None)` — mirrors the
  per-unit `work_unit_not_found` path and is an existing code (`release_artifacts.py`,
  `deployment_observations.py` both raise it), so the registered `DomainError` handler maps
  it to a clean 4xx.
- **Units** (composition): `select(WorkUnit).where(WorkUnit.work_package_revision_id ==
  revision_id).order_by(WorkUnit.unit_key)`; for each unit,
  `evidence_pack_response(evidence_pack_projection(session, unit.id))`. Reuses Increment 1
  unchanged.
- **Artifacts:** `select(ReleaseArtifactBinding).where(... == revision_id)
  .order_by(ReleaseArtifactBinding.recorded_at, ReleaseArtifactBinding.id)`; map each with
  `ReleaseArtifactResponse.model_validate(row)`.
- **Deployments:** `select(DeploymentObservation).where(... == revision_id)
  .order_by(DeploymentObservation.recorded_at, DeploymentObservation.id)`; map each with
  `DeploymentObservationResponse.model_validate(row)`.
- Assemble and return `ReleaseEvidencePackResponse`.

One assembler feeds both the JSON route and the GUI route (Increment 1's "one structured
source, N views" principle). Mapping via `model_validate` (not per-field attribute access)
keeps the module free of `.dispatch_summary` / `.post_deploy_*` identifiers that would trip
the scope guard (see Invariants below).

### 2. Schemas — `src/orchestrator/api/schemas.py` (extend)

Reuse the existing `EvidencePackResponse` (per-unit), `ReleaseArtifactResponse`, and
`DeploymentObservationResponse` (both already present, both `from_attributes=True`). Add:

```python
class ReleaseEvidencePackRevisionResponse(BaseModel):
    work_package_id: UUID
    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    registered_by: str

class ReleaseEvidencePackResponse(BaseModel):
    revision: ReleaseEvidencePackRevisionResponse
    units: list[EvidencePackResponse]
    release_artifacts: list[ReleaseArtifactResponse]
    deployments: list[DeploymentObservationResponse]
```

`ReleaseEvidencePackRevisionResponse` is dedicated rather than reusing the per-unit
`EvidencePackProvenanceResponse` because it adds `work_package_id` (the intent anchor) and
`approved_by` — the fields WS-P2.6's intent↔…↔deployment traversal needs.

### 3. JSON route — `src/orchestrator/api/routes.py` (extend)

```python
@router.get("/revisions/{revision_id}/evidence-pack",
            response_model=ReleaseEvidencePackResponse)
def release_evidence_pack_route(revision_id: UUID, _actor: ActorDep,
                                session: SessionDep) -> object:
    return release_evidence_pack_response(session, revision_id)
```

Authentication-only (`_actor: ActorDep`), no role gate — identical access model to the
per-unit evidence-pack JSON route. Returns JSON with a declared `response_model`, so it
auto-satisfies `test_every_api_success_response_has_an_explicit_schema`. It is **not**
markdown, so it needs no `NON_JSON_SUCCESS_PATHS` entry.

### 4. GUI route — `src/orchestrator/web.py` (extend)

```python
@router.get("/revisions/{revision_id}/evidence-pack", response_class=HTMLResponse)
def release_evidence_pack(request: Request, revision_id: uuid.UUID,
                          actor: ActorDep, session: SessionDep) -> HTMLResponse:
    _human(actor)
    return _render(request, "release_evidence_pack.html",
                   {"pack": release_evidence_pack_response(session, revision_id)})
```

Behind the existing forward-auth `_human` gate. The function body references neither
`.dispatch_summary` nor `.post_deploy_*` — it passes the response object straight to the
template — so `web.py` needs no scope-guard allowlist entry.

Discoverability: add a link to this page from `intake.html` (the intake detail page is
already keyed per revision at `/review/intakes/{revision_id}`).

### 5. Template — `src/orchestrator/templates/release_evidence_pack.html` (new)

Extends `base.html`. Sections:
- **Release header:** work package id, revision number, content hash, source path/commit,
  approved by, registered by.
- **Units table:** title, state, authority fingerprint; each row links to that unit's
  existing per-unit GUI page (`/review/units/{id}/evidence-pack`). Reads only the
  `work_unit` field of each nested `EvidencePackResponse`.
- **Release artifacts table:** artifact digest, registry/repository/name, PR number,
  source/merge commits, workflow, builder, provenance/sbom.
- **Deployments table:** environment, observed digest, base url, deployer, observed at, and
  a compact rendering of the health/route/auth/dispatch/status summaries.

Templates are `.html` and are not scanned by the scope guard, so the template may reference
`dispatch_summary` / `post_deploy_*` freely.

## Error handling

- `revision_not_found` → `DomainError` → existing handler → 4xx. No new exception types.
- Only `DomainError` and `APIAuthenticationError` have registered handlers; the route and
  service raise nothing else. Route-level path parsing is a typed `UUID` path param (FastAPI
  returns 422 on a malformed UUID before the handler runs), so no stdlib `ValueError`
  escapes as a 500.

## Ordering / determinism

Units by `unit_key`; artifacts and deployments by `(recorded_at, id)`. Deterministic and
stable across calls.

## Known non-obvious invariants this design must satisfy

- **Scope guard (`tests/architecture/test_ws32_scope_guards.py`).** `api/routes.py` and
  `api/schemas.py` are already in `WS42_DISPATCH_PATHS`, so the route + schema are exempt.
  The **new service module must be added to `WS53_POST_DEPLOY_PATHS`** with a justification
  comment (it composes deployment observations; it never dispatches, deploys, or merges) —
  the same treatment as its sibling `deployment_observations.py`. `web.py` is in neither
  allowlist and is **not** added; its route body must avoid bare `deploy`/`dispatch` tokens
  (it does — it only calls the assembler and `_render`). In prose/docstrings of the new
  module, avoid the bare tokens `deploy`, `post-deploy`, and `dispatch` (use "deployment",
  "post-deployment", "deployment observations").
- **JSON-schema invariant (`tests/api/test_lifecycle_api.py`).** Satisfied automatically by
  the `response_model`; no `NON_JSON_SUCCESS_PATHS` change (no markdown route).
- **Reachability guard (`tests/architecture/test_unreachable_guards.py`).** The new service
  function is called by both the JSON and GUI routes, so it is reachable; no allowlist entry.
- **`make check` gate.** Run whole-repo; read the collected-test count (exit 0 ≠ tests ran);
  run `ruff format` (not just `ruff check`) before commit; verify a clean working tree.

## Testing (TDD)

- `tests/api/test_release_evidence_pack_api.py`:
  - Composes a revision with ≥2 units, ≥1 artifact binding, ≥1 deployment observation; the
    response nests each unit's full pack and lists the artifacts and deployments.
  - **Full-fidelity:** approver identity and adjudication rationale ARE present in the JSON
    (the redaction applies only to per-unit markdown; the per-release JSON is not redacted).
  - 401 without credentials.
  - `revision_not_found` → 4xx.
  - Deterministic ordering.
- `tests/web/test_release_evidence_pack.py`:
  - `_human` gate (M2M is rejected; forward-auth human renders).
  - Renders unit links to `/review/units/{id}/evidence-pack` and the artifact/deployment rows.
  - Revision-not-found handling.
- No existing invariant weakened; the invariant suite is extended, not relaxed.

## Definition of done

Per-release Evidence Pack assembled by composing per-unit `EvidencePackResponse` +
`ReleaseArtifactResponse` + `DeploymentObservationResponse`; exposed as structured JSON and a
read-only `/review` GUI page; TDD with per-task and final adversarial whole-branch review;
`make check` green (collected count read, clean tree); `/code-review`; Devon merges; deployed
to `sds.alobar.net` (digest-verified, no migration expected — confirm; new route confirmed in
prod `openapi.json`); Wave-2 closeout note in `~/docs/software-delivery-system/`. After this,
WS-P2.5 is complete and WS-P2.6 (traceability query) is the next workstream.
