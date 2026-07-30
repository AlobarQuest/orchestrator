# WS-P2.12 — Work-Unit Context Enrichment

Date: 2026-07-30. Wave 3, fourth workstream. Owner surface: `AlobarQuest/orchestrator`,
with coordinated changes in `AlobarQuest/intent-packages` and `AlobarQuest/factory-runner`.
Mode: construction (`~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`).

## Problem

Worker quality depends on the worker remembering to query the brains. Nothing in the
dispatched work unit tells it which paved roads, rules, or lessons govern the change it is
about to make. The fix is a deterministic per-change-class projection of governed brain
material into what the worker actually reads.

## Findings that shaped the design

Every one of these was verified against source or a live query on 2026-07-30, not assumed.

1. **`RunnerBrief` is `extra="forbid"`** (`factory-runner/src/factory_runner/models.py:75`).
   A new top-level brief key makes the runner's `model_validate_json` raise. The brief is a
   breaking cross-repo contract, not an open bag. `AuthorityEnvelope` is `extra="forbid"` too.

2. **`ContextSnapshot` cannot carry enrichment.** Its writer is `POST
   /work-units/{id}/preflight` → `services/context.py::record_preflight`, and
   `ContextSnapshot.context` is `normalize_standing_context(...)` — exactly the ten
   `REQUIRED_CONTEXT_FIELDS` (`kernel/context.py:8`), with everything else silently dropped.
   It is the authority-expansion attestation surface: fingerprinted, approval-bound, fed to
   `classify_context_update`. Enrichment placed there is either discarded, or — if added to
   `REQUIRED_CONTEXT_FIELDS` — rewrites every context fingerprint and entangles brain content
   with authority-expansion classification.

3. **A `WorkUnit` has no free-form column.** Only `authority`, which is the byte-pinned
   cross-repo envelope. Authoring-time enrichment therefore still needs an orchestrator
   column, a brief field, and a factory-runner change; it removes the *egress*, not the
   schema work.

4. **The worker-visible surface is `factory-runner/cli.py::_prompt()` (line 142)**, not the
   brief JSON. The brief is transport. Enrichment that does not reach `_prompt` changes
   nothing about worker behaviour.

5. **`routing-policy.toml` already ruled on the mechanism.** Its `[no_llm].items` lists
   "claim-time context enrichment" — deterministic by design, never calls a model.

6. **The brains are nearly empty for the classes with production traffic.** Live queries:

   | Query | Result |
   |---|---|
   | Code Brain `get_rules(min_authority="required")` | zero rules |
   | Code Brain, all rules | 11, every one `authority: informational` |
   | Code Brain road `dependency-update` | `paved`, `decided_approach: null`, 0 rules/exemplars/lessons |
   | Code Brain `search("dependency update lockfile")` | 0 roads, 0 rules, 0 lessons |
   | Infra Brain `search_lessons("dependency update uv lock pins python")` | 0 lessons |
   | Infra Brain `get_rules(min_authority="required")` | 4, all BWS/credential security |

   The inherited scope's phrase "`authority: required` rules" describes content that does
   not exist in Code Brain. The only substantive road is `error-logging` (9 approved rules,
   a real `decided_approach`), which is `software-delivery` material.

7. **`severity` and `authority` are orthogonal and disagree.** Infra Brain rule #1 ("never
   source-build Next.js on the VPS") is `severity: BLOCK` but `authority: informational`.
   Anything keyed on one of those words must say which; "BLOCK rules" ≠ "required rules".

8. **The brains expose a REST read API built for this.** `GET /api/roads`, `/api/road/{slug}`,
   `/api/rules`, `/api/search` on Code Brain; `GET /api/rules` on Infra Brain. Its own
   docstring: *"Lets off-machine agents query Code Brain accurately without an MCP client.
   Write-back stays on MCP."* `RuleRepository.list_all` defaults `include_proposed=False`,
   so **approved-only containment holds by construction**. The REST route does not expose
   `min_authority` (the repository supports it, the route does not pass it), so authority
   filtering is client-side on the returned field.

9. **intent-packages can already reach it.** `factory/api.py` uses `httpx`;
   `factory/credentials.py` resolves BWS secrets by UUID. No new capability class.

10. **Production serves the affected surfaces.** `sds.alobar.net/openapi.json` lists 58
    paths including `runner-brief`, `preflight`, `context-snapshots`. Deployed matches `main`
    here; MERGED ≠ DEPLOYED still applies to what this workstream ships.

## Decisions

**Where enrichment executes: authoring time, in intent-packages** (Devon, 2026-07-30).
`factory decompose` resolves enrichment while composing the decomposition proposal, so the
enrichment is inside the artifact a human approves. No orchestrator egress; the push-only
posture is untouched. Cost accepted: staleness between authoring and claim, bounded by the
fact that brain content changes on a months scale.

**Scope: the seam plus the one change class with real content** (Devon, 2026-07-30).
`software-delivery` is defined and demonstrated; `dependency-update` and
`maintenance-remediation` ship with explicitly-empty projections, named as
empty-by-content residuals.

**Frozen, never re-resolved** (author's call, follows from the ruling). Re-resolution before
dispatch would mean the worker sees material no human approved. The stored document is what
the unit executed under.

**The resolver runs for every factory-executable profile.** A profile with a `change_class`
always produces a document, possibly with empty `roads`/`rules`. Skipping the resolver would
make "enriched but empty" indistinguishable from "never enriched".

## Architecture

### The one definition site

`DeliveryProfile` (`intent-packages/src/intent_packages/profiles/base.py`) gains
`enrichment: EnrichmentSpec | None` — the same governed shape that already carries
`default_authority` and `tooling`.

```python
@dataclass(frozen=True)
class EnrichmentSpec:
    code_road_slugs: tuple[str, ...] = ()
    infra_min_authority: str = "required"
```

Initial values:

- `software-delivery`: `code_road_slugs=("error-logging",)`, `infra_min_authority="required"`
- `dependency-update`: `code_road_slugs=()`, `infra_min_authority="required"`
- `maintenance-remediation`: `code_road_slugs=()`, `infra_min_authority="required"`

The orchestrator holds **no copy of this vocabulary**. It receives a resolved document and
validates its *shape*, never its membership — so there is nothing for the cross-boundary
vocabulary guard to register, by design rather than by exemption.

### The resolver

New module `intent_packages/factory/brains.py`:

- An httpx client over the brains' REST read API, base URLs from configuration and the
  access key resolved by BWS UUID through the existing `credentials.resolve_token` path.
- For each `code_road_slugs` entry: `GET /api/road/{slug}` → the road's `decided_approach`
  plus its approved rules, exemplars, and lessons.
- `GET /api/rules` on Infra Brain, filtered client-side to `authority >= infra_min_authority`.
- Bounded and normalised into the canonical document below.

### The enrichment document

```jsonc
{
  "schema_version": 1,
  "profile": "software-delivery",
  "change_class": "software-delivery",
  "roads": [ { "brain", "slug", "name", "status", "decided_approach", "summary" } ],
  "rules": [ { "brain", "id", "road_slug", "category", "severity", "authority",
               "rule", "reason" } ],
  "content_fingerprint": "sha256:…",
  "resolved_at": "2026-07-30T…Z",
  "sources": [ { "brain", "endpoint", "query" } ]
}
```

`content_fingerprint` covers `schema_version`, `profile`, `change_class`, `roads`, `rules`
under canonical JSON (sorted keys, sorted records, compact separators). `resolved_at` and
`sources` are provenance and sit **outside** the fingerprint deliberately: that is what makes
*same brain state → same fingerprint* a real, testable property instead of one defeated by a
clock. It is the same reasoning as `AuthorityEnvelope.normalized()` — the fingerprint attests
content, not circumstance.

Both `severity` and `authority` are carried per rule, never collapsed, because finding 7 shows
they disagree.

### Transport into the orchestrator

`factory decompose` attaches the document to each proposed unit in the `--data` JSON it
already POSTs. `orchestrator propose-decomposition` passes `--data` through verbatim, so no
CLI change is required.

### Persistence

- `ProposedUnit.context_enrichment: Mapping[str, Any] | None`
  (`services/decomposition.py`)
- `decomposition_proposal_units.context_enrichment` — JSONB, nullable
- `work_units.context_enrichment` — JSONB, nullable, assigned **only** in
  `register_approved_unit`

Write-once, enforced by an architecture test mirroring
`tests/architecture/test_authority_write_once.py`. Enrichment that can be mutated after the
fact is no longer a record of what the worker saw.

Migration id: `0021_wsp212_enrichment` (22 chars — the `alembic_version.version_num` column
is `varchar(32)` and over-long ids fail at stamp time, not at authoring time). Current head
is `0020_wsp28_follow_up`; confirm it again before writing the revision.

**Null versus empty.** `NULL` means the unit predates enrichment. A document with
`roads: []` and `rules: []` means the class was enriched and the brains had nothing. These
must never collapse into each other, or the residual this workstream deliberately names
becomes invisible.

### Ingress validation

`_validate_context_enrichment` in `services/decomposition.py`, called from the same place as
`_validate_unit_constraints`:

- required keys present, `schema_version == 1`
- `roads`/`rules` are lists of flat string-valued objects with the expected keys
- total serialized size ≤ 16 KB; ≤ 50 roads; ≤ 200 rules; each text field ≤ 4000 chars
  (the per-field cap mirrors `knowledge_promotions.MAX_TEXT`)
- `content_fingerprint` recomputed and compared

Every rejection raises `DomainError`. Only `DomainError` and `APIAuthenticationError` have
registered handlers in `main.py`; anything else escaping a route is a bare 500.

### Surfacing

`runner_brief()` returns the stored document verbatim under a new `enrichment` key. Verbatim
is what makes the projection byte-deterministic: same unit, same stored bytes, same output.

### The worker's prompt

`factory-runner`:

- `RunnerBrief.enrichment: dict[str, Any] | None = None`
- `_prompt()` renders a bounded, clearly-labelled governed-material section, deliberately
  contrasted against the existing hostile-data warning — this is the one input in the prompt
  that *is* governed, and saying so is the containment framing made real.
- `_sanitize_runner_brief` needs no change; enrichment carries no secret-bearing fields.

## Ordering constraint

**factory-runner merges before the orchestrator ships the brief field.** `RunnerBrief` is
`extra="forbid"` and the runner is installed fresh per run from its default branch, so
merge-first is sufficient. Reversed, every brief fails to parse and every run dies at claim.
This is the same fail-closed shape as the credentials-before-roles env write.

## The guard this workstream adds

The brief is a breaking cross-repo contract and **nothing tests it across the boundary**.
Only the *envelope* has a shared fixture plus a pinned `CONTRACT_SHA256`
(`tests/contract/test_runner_envelope_contract.py` here,
`tests/test_orchestrator_envelope_contract.py` there). The brief has only
`tests/api/test_runner_brief_api.py`, which is single-repo.

This is the identical defect WS-6.4.0 closed for the envelope, still open for the brief. Add
a byte-identical `runner_brief` fixture and a pinned hash in both repos, and **prove it fires**
by adding a field on one side only before wiring the other.

## Testing

- **Determinism, storage:** same unit → byte-identical `enrichment` across repeated
  `runner_brief()` calls.
- **Determinism, resolution:** the resolver against a recorded brain response fixture yields
  a byte-identical document and fingerprint across runs; `resolved_at` differs and the
  fingerprint does not.
- **Write-once:** the architecture test fails if any path assigns `context_enrichment`
  outside `register_approved_unit`.
- **Null vs empty:** a `dependency-update` unit carries an empty document, not `NULL`.
- **Bounds:** each rejection path returns its `DomainError` code, never a 500.
- **Cross-repo contract:** the shared brief fixture, proven to fire.
- **Prompt rendering:** `_prompt()` includes the enrichment section when present and omits it
  cleanly when `None`.

## Guard environment

Verified applicable, and each is engineering rather than ceremony:

- **No new route** — enrichment rides the existing decomposition-proposal POST and the
  existing brief GET. The POST/GET route-inventory sets and the idempotency `COVERAGE_MATRIX`
  are therefore untouched. Re-verify at the final gate rather than assuming.
- **`test_unreachable_guards`** — every new service function ships with its production caller
  in the same increment.
- **ws32/ws33 word bans** — new orchestrator modules must avoid the bare tokens `dispatch`,
  `deploy`, and `merges` in prose, docstrings included.
- **`test_wsp21_invariant_scan`** — the orchestrator gains **no** HTTP client import. The
  egress allowlist is unchanged; that is the point of the ruling.
- **`test_cross_boundary_vocabulary`** — nothing to register, because the orchestrator holds
  no enrichment vocabulary.
- **`make check`** needs Postgres, `SECURITY_STANDARDS_DIR`, and a migrated database; read the
  collected-test count, never the exit code (exit 5 is swallowed).
- **`ruff format --check .`** is whole-repo and may red on pre-existing debt in untouched
  files; diff against `main` before blaming this change.

## Increments

1. **factory-runner** — brief field, prompt rendering, shared contract fixture. Merges first.
2. **intent-packages** — `EnrichmentSpec`, brain client, resolver, `factory decompose`
   wiring, determinism tests.
3. **orchestrator** — migration, columns, plumbing, validation, brief field, write-once and
   contract tests.
4. **Deploy and demo** — migrate-first, image swap, verify the running container's digest,
   then drive the first `software-delivery` unit in this repo end-to-end.

## Definition of done

The seam built; per-change-class enrichment defined in `DeliveryProfile` and pointed to from
nowhere else; a real `software-delivery` unit's brief demonstrably carrying enrichment that
reached the worker's prompt; determinism tested; the brief-shape change verified against
factory-runner's actual parsing via a shared fixture proven to fire; closeout per
`~/docs/software-delivery-system/session-closeout-contract.md` with the empty-by-content
classes named as residuals for the adoption era.

## Residuals, named now

- `dependency-update` and `maintenance-remediation` are enriched with empty documents because
  the brains hold no content for them. The pipe works; the content does not exist yet.
- Code Brain has **zero** `authority: required` rules. Until that changes, an
  authority-floor filter against Code Brain returns nothing, and only Infra Brain's four
  security rules clear the bar.
- App Brain is not wired. The inherited scope names "app-brain context"; there is no
  per-change-class definition of what an app-scoped pull would mean, and inventing one
  without a consumer would be the dead-config defect this repo has already paid for twice.
