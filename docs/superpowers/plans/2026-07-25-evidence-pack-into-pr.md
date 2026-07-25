# Evidence Pack into the PR — Implementation Plan (WS-P2.5 Increment 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing per-unit Evidence Pack as structured JSON + server-rendered markdown on the orchestrator `/api`, and have factory-runner relay the markdown into a marker-keyed, edit-in-place PR comment at finalize.

**Architecture:** Extract the existing `_projection` (per-unit assembly) into a shared service; the `/review` HTML GUI keeps using it. Add two `/api` GET routes over one projection: JSON (`EvidencePackResponse`, canonical/machine-readable) and markdown (`PlainTextResponse`, server-rendered). The runner fetches the markdown (opaque) and posts/updates a PR comment via `gh`, best-effort/non-blocking.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Pydantic v2, Jinja/Python markdown, pytest; factory-runner: typer CLI + httpx + `gh` shell-out; GitHub Actions.

**Design spec:** `docs/superpowers/specs/2026-07-25-evidence-pack-into-pr-design.md`

## Global Constraints

- **Auth: authentication-only, NO role gate** on both new `/api` routes — use `_actor: ActorDep` exactly like `runner_brief_route`. Do NOT gate on SYSTEM/VERIFIER (that would lock out the runner's worker credential). The runner reads with its existing standing credential; no new credential/role plumbing.
- **One projection source.** `_projection` (moved to a service) is the single assembly feeding the GUI, the JSON, and the markdown. Do not create a second query path that can drift.
- **`_projection` returns ORM objects + `set[UUID]`** — NOT JSON. The JSON route needs a Pydantic `EvidencePackResponse` + an explicit ORM→response serializer (mirror `runner_brief`'s dict→`RunnerBriefResponse` coercion). Do not `return _projection(...)` from a JSON route.
- **Best-effort relay catches TWO failure types:** `OrchestratorError` (the HTTP fetch) AND `RuntimeError` (raised by `_run_command` on a failed `gh pr comment`). A pack-comment failure logs (`typer.echo(..., err=True)`) and continues — it must NEVER block `submit`/finalize. (The `_emit_cost_actuals` guard covers only the HTTP half — extend it.)
- **No migration** (reads existing rows). **No byte-pinned cross-repo contract** (the runner treats the markdown as opaque text). **No new token** (the runner already uses `FACTORY_PR_TOKEN` for `gh pr create`; `pull-requests: write` is already granted in CI).
- **Route inventory:** both new `/api/v1` GET routes must be added to the explicit allowlist in `tests/architecture/test_scope_guards.py::test_production_get_route_inventory_is_explicit` — or CI fails (the WS-P2.4-Increment-1 lesson: an uncommitted/absent inventory line is a CI-only failure).
- **WS32 word-ban:** no bare `dispatch`/`deploy` in new `src/orchestrator/` identifiers or string literals (incl. docstrings/markdown-template prose).
- `make check` green **on a CLEAN tree** (read the collected count; confirm `git status` clean before trusting local green). `ruff format` (never JSON). factory-runner suite green under **bare `pytest`**; sibling test imports use `from test_cli import …`, not `from tests.test_cli`.

---

### Task 1: Extract `_projection` into a shared service

Move the per-unit assembly out of `web.py` (the GUI module) into a service both the GUI and the API can call. Pure move — no behavior change.

**Files:**
- Create: `src/orchestrator/services/evidence_pack.py`
- Modify: `src/orchestrator/web.py` (import the moved function; delete the local copies)
- Test: existing `/review` evidence-pack test must still pass; add a service-level test.

**Interfaces:**
- Produces: `evidence_pack_projection(session: Session, unit_id: uuid.UUID) -> dict[str, Any]` (the existing `_projection` output verbatim) and the helper `_event_publication_projection`. Consumed by Tasks 2, 3, and the GUI.

- [ ] **Step 1: Move `_projection` and `_event_publication_projection`** verbatim from `web.py:205-313` into `src/orchestrator/services/evidence_pack.py`, renaming the public one `evidence_pack_projection` (keep `_event_publication_projection` private in the service). Bring the imports they need (`WorkUnit, WorkPackageRevision, Evidence, Adjudication, Event, Dependency, Claim, Approval, EventPublication`, `normalize_authority`, `dependency_update_authority_violation`, `DomainError`, sqlalchemy `select, and_, or_`).

- [ ] **Step 2: Update `web.py`.** Delete the moved functions; `from orchestrator.services.evidence_pack import evidence_pack_projection`; change the `/review` `evidence_pack` route to call `evidence_pack_projection(session, unit_id)`. (If other `web.py` code used `_projection` for a different route, point it at the moved function too — grep `web.py` for `_projection(` first.)

- [ ] **Step 3: Run the existing GUI test + a new service test.** Add `tests/services/test_evidence_pack.py::test_projection_assembles_core_facts` asserting the returned dict has the 13 keys (`unit, authority, revision, evidence, current_evidence_ids, adjudications, current_adjudication_ids, approvals, events, event_publications, dependencies, claims, authority_violation`) for a built unit with one AC + one evidence row. Run the `/review` evidence-pack test (grep `tests/` for it) + the new one.

Run: `.venv/bin/pytest tests/services/test_evidence_pack.py tests/web/ -q`
Expected: PASS (GUI unchanged, service test green).

- [ ] **Step 4: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/services/evidence_pack.py src/orchestrator/web.py tests/services/test_evidence_pack.py && git add -A src/orchestrator/services/evidence_pack.py src/orchestrator/web.py tests/services/test_evidence_pack.py && git commit -m "refactor(wsp25): extract evidence-pack projection into a shared service"
```

---

### Task 2: JSON endpoint — structured `EvidencePackResponse`

**Files:**
- Modify: `src/orchestrator/api/schemas.py` (add `EvidencePackResponse` + nested models)
- Modify: `src/orchestrator/services/evidence_pack.py` (add `evidence_pack_response(projection) -> EvidencePackResponse` serializer)
- Modify: `src/orchestrator/api/routes.py` (add the JSON GET route)
- Modify: `tests/architecture/test_scope_guards.py` (add the route to the GET inventory allowlist)
- Test: `tests/api/test_evidence_pack_api.py` (create)

**Interfaces:**
- Consumes: `evidence_pack_projection` (Task 1).
- Produces: `GET /api/v1/work-units/{unit_id}/evidence-pack` → `EvidencePackResponse` (JSON). A structured, serializable pack: `work_unit` (id, title, state, authority_fingerprint), `provenance` (revision, content_hash, source_path, source_commit, registered_by), `authority` (the normalized envelope dict + authority_violation), `evidence[]` (ac_id, current, evidence_type, ref, supersedes), `adjudications[]` (ac_id, outcome, current, decided_by, rationale, waiver fields), `approvals[]`, `event_publications[]` (source_ref, status, event_id, export_ref), `events[]` (occurred_at, action, actor_id, from_state, to_state, reason). **Design the field names so a per-release pack (Increment 2) can nest a list of these.** Consumed by Task 3 (markdown renders from it) and P2.6.

- [ ] **Step 1: Write the failing API test** `tests/api/test_evidence_pack_api.py`. Reuse the `db_client` + worker-auth + a built-and-evidenced unit harness (mirror `tests/api/test_cost_actuals_route.py` / the status-ledger API test). Assert: GET returns 200; JSON has `work_unit.authority_fingerprint`, a `provenance.content_hash`, an `evidence` entry with `ac_id` + `current` flag, an `adjudications` entry with waiver fields when waived, and `events`. Also a test that the runner's WORKER credential can read it (not just SYSTEM), and an unknown unit → clean 4xx (DomainError work_unit_not_found), not 500.

- [ ] **Step 2: Run — expect FAIL** (route 404 / schema import error).

- [ ] **Step 3: Add `EvidencePackResponse`** (+ nested `EvidencePackEvidence`, `...Adjudication`, `...Approval`, `...EventPublication`, `...Event`, `...Provenance`, `...WorkUnit`) to `api/schemas.py`. Use plain `BaseModel` fields (types matching the ORM columns); the markdown of the exact field set is in `templates/evidence_pack.html` (mirror those bindings). Then add `evidence_pack_response(projection: dict) -> EvidencePackResponse` to `services/evidence_pack.py` mapping each ORM object/set into the response (e.g. `current = row.id in projection["current_evidence_ids"]`).

- [ ] **Step 4: Add the route** in `api/routes.py` (near the other work-unit GETs), authentication-only:

```python
@router.get("/work-units/{unit_id}/evidence-pack", response_model=EvidencePackResponse)
def evidence_pack_route(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return evidence_pack_response(evidence_pack_projection(session, unit_id))
```

Add imports (`EvidencePackResponse`, `evidence_pack_response`, `evidence_pack_projection`).

- [ ] **Step 5: Add the route to the GET inventory allowlist** in `tests/architecture/test_scope_guards.py::test_production_get_route_inventory_is_explicit` (find the expected-set literal; add `"/api/v1/work-units/{unit_id}/evidence-pack"`).

- [ ] **Step 6: Run.**

Run: `.venv/bin/pytest tests/api/test_evidence_pack_api.py "tests/architecture/test_scope_guards.py::test_production_get_route_inventory_is_explicit" -v`
Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/api/schemas.py src/orchestrator/services/evidence_pack.py src/orchestrator/api/routes.py tests/api/test_evidence_pack_api.py tests/architecture/test_scope_guards.py && git add -A src/orchestrator/api tests/api/test_evidence_pack_api.py tests/architecture/test_scope_guards.py src/orchestrator/services/evidence_pack.py && git commit -m "feat(wsp25): GET /work-units/{id}/evidence-pack JSON (structured pack)"
```

---

### Task 3: Markdown endpoint — server-rendered from the structured pack

**Files:**
- Modify: `src/orchestrator/services/evidence_pack.py` (add `render_evidence_pack_markdown(pack: EvidencePackResponse) -> str`)
- Modify: `src/orchestrator/api/routes.py` (add the markdown GET route)
- Modify: `tests/architecture/test_scope_guards.py` (GET inventory allowlist)
- Test: `tests/api/test_evidence_pack_api.py` (add markdown cases), `tests/services/test_evidence_pack.py` (renderer unit test)

**Interfaces:**
- Consumes: `EvidencePackResponse` (Task 2).
- Produces: `GET /api/v1/work-units/{unit_id}/evidence-pack/markdown` → `text/markdown` (PlainTextResponse). Renders the same 8 sections as the GUI (Canonical provenance, Authority, Dependencies & claims, AC-keyed evidence, Adjudications & waiver facts, Approvals, Event publications, Event history).

- [ ] **Step 1: Write failing tests.** Service: `render_evidence_pack_markdown(pack)` returns a string containing the authority fingerprint, an AC id with its outcome, and a `## ` section per the 8 headers. API: `GET .../evidence-pack/markdown` returns 200, `content-type` starts `text/markdown`, body contains the same key facts; and a test that JSON and markdown derive from ONE projection (change the underlying data → both reflect it).

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement the renderer** in `services/evidence_pack.py` — a pure `EvidencePackResponse -> str` markdown builder (Python f-strings/loops; mirror the template's field set; keep the `dispatch`/`deploy` words out of headers/labels). Then add the route in `routes.py`:

```python
from fastapi.responses import PlainTextResponse  # new import in routes.py

@router.get(
    "/work-units/{unit_id}/evidence-pack/markdown",
    response_class=PlainTextResponse,
    include_in_schema=True,
)
def evidence_pack_markdown_route(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> PlainTextResponse:
    pack = evidence_pack_response(evidence_pack_projection(session, unit_id))
    return PlainTextResponse(render_evidence_pack_markdown(pack), media_type="text/markdown")
```

- [ ] **Step 4: Add the markdown route to the GET inventory allowlist** (same test as Task 2 Step 5). Note: a `response_class=PlainTextResponse` route still appears in `openapi.json` paths (it's `include_in_schema=True`), so the inventory guard will see it — add it or CI fails.

- [ ] **Step 5: Run.**

Run: `.venv/bin/pytest tests/api/test_evidence_pack_api.py tests/services/test_evidence_pack.py "tests/architecture/test_scope_guards.py::test_production_get_route_inventory_is_explicit" -v`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/services/evidence_pack.py src/orchestrator/api/routes.py tests/api/test_evidence_pack_api.py tests/services/test_evidence_pack.py tests/architecture/test_scope_guards.py && git add -A src/orchestrator/services/evidence_pack.py src/orchestrator/api/routes.py tests/api tests/services/test_evidence_pack.py tests/architecture/test_scope_guards.py && git commit -m "feat(wsp25): GET /evidence-pack/markdown (server-rendered)"
```

---

### Task 4: factory-runner relay — post the pack as a PR comment

**Files:**
- Modify: `src/factory_runner/client.py` (add `get_evidence_pack_markdown`)
- Modify: `src/factory_runner/cli.py` (add `_post_evidence_pack_comment` + call it after `pr_binding`)
- Test: `tests/test_evidence_pack_comment.py` (create)

**Interfaces:**
- Consumes: the markdown endpoint (Task 3), the existing `client`, `_run_command`, `pr_url`, `work_unit_id`.

- [ ] **Step 1: Add the client method** in `client.py` (mirror `get_runner_brief`, but return text):

```python
    def get_evidence_pack_markdown(self, unit_id: str) -> str:
        response = self._request("GET", f"/api/v1/work-units/{unit_id}/evidence-pack/markdown")
        return response.text
```

- [ ] **Step 2: Write failing tests** `tests/test_evidence_pack_comment.py` (reuse the CLI-test harness — `from test_cli import …`, a fake/recording client + monkeypatched `_run_command`). Assert:
  - On first finalize: `_run_command` is called with `["gh", "pr", "comment", <pr_url>, "--body", <body>]` where `<body>` contains the marker `<!-- sds-evidence-pack:{unit_id} -->` and the fetched markdown.
  - On a second finalize where a comment with the marker already exists: it EDITS (uses `gh pr comment --edit-last` or an api-find-then-edit) rather than creating a duplicate. (Pick the mechanism — `gh pr comment {pr} --edit-last --body` is simplest if the bot's last comment is the pack; otherwise `gh api` to find-by-marker then PATCH. Decide and test whichever you implement.)
  - **Best-effort:** if `client.get_evidence_pack_markdown` raises `OrchestratorError`, OR the `gh` `_run_command` raises `RuntimeError`, finalize CONTINUES and `client.submit` STILL runs (assert submit called, exit 0). This is the critical guard — test BOTH failure types.

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement `_post_evidence_pack_comment`** in `cli.py`:

```python
def _post_evidence_pack_comment(client, work_unit_id: str, pr_url: str) -> None:
    """Project the evidence pack onto the PR as a comment. Best-effort: a fetch or gh failure
    logs and continues -- the pack is a readable projection, never a delivery gate."""
    marker = f"<!-- sds-evidence-pack:{work_unit_id} -->"
    try:
        markdown = client.get_evidence_pack_markdown(work_unit_id)
        body = f"{marker}\n{markdown}"
        # edit-in-place if our marker comment already exists, else create
        _upsert_pr_comment(pr_url, marker, body)
    except (OrchestratorError, RuntimeError) as error:
        typer.echo(f"evidence-pack comment skipped: {error}", err=True)
```

Implement `_upsert_pr_comment(pr_url, marker, body)` using `_run_command` + `gh` (find-by-marker then edit, or `--edit-last`; whichever you tested in Step 2). Call `_post_evidence_pack_comment(client, work_unit_id, pr_url)` in `_finalize_workspace` right after the `client.pr_binding(...)` call (`cli.py:~766`).

- [ ] **Step 5: Run the new tests + full runner suite (bare pytest).**

Run: `.venv/bin/pytest tests/test_evidence_pack_comment.py -v && .venv/bin/pytest -q`
Expected: PASS; read the full-suite count.

- [ ] **Step 6: Commit.**

```bash
cd /Users/devon/Projects/factory-runner && ruff format src/factory_runner/client.py src/factory_runner/cli.py tests/test_evidence_pack_comment.py && ruff check src/factory_runner/client.py src/factory_runner/cli.py tests/test_evidence_pack_comment.py && git add src/factory_runner/client.py src/factory_runner/cli.py tests/test_evidence_pack_comment.py && git commit -m "feat(wsp25): relay evidence pack into a PR comment (best-effort)"
```

---

### Task 5: Drill, full gate, reviews, handoff

**Files:** `tests/api/test_evidence_pack_drill.py` (orchestrator) or extend an existing drill; else verification-only.

- [ ] **Step 1: Public-surface drill (orchestrator).** Drive HTTP: build + evidence a unit, `GET /evidence-pack` (JSON) and `/evidence-pack/markdown` as the worker credential; assert the JSON carries the AC outcome + authority fingerprint and the markdown contains the same facts + the 8 section headers. (The runner→PR-comment leg is covered by Task 4's stubbed test; a real `gh` call is out of scope for a unit drill.)

- [ ] **Step 2: Clean-tree full gate (orchestrator).** `git status --short` clean, then `make check 2>&1 | tail -30` — read `collected N` / `N passed`; confirm the GET-inventory guard, the `/review` evidence-pack test, and the new API tests passed.

- [ ] **Step 3: Runner full gate.** `cd /Users/devon/Projects/factory-runner && .venv/bin/pytest -q` — green, count read.

- [ ] **Step 4: `/code-review`** each branch diff.

- [ ] **Step 5: Independent adversarial whole-branch review** (fresh agent, most-capable model). Probe: (a) the two routes are authentication-only (worker can read; no accidental role gate); (b) the JSON serializer maps every field correctly and never leaks an ORM object (JSON-serializable); (c) markdown and JSON derive from ONE projection (no drift); (d) the relay catches BOTH `OrchestratorError` AND `RuntimeError` — a pack failure never blocks `submit`; (e) the comment upserts (edits, not duplicates) on re-push; (f) both new GET routes are in the inventory allowlist; (g) no `dispatch`/`deploy` prose in new code. Fix survivors (one fix subagent).

- [ ] **Step 6: Push + open PRs; hand off to Devon.** Deploy is Devon-gated: **orchestrator route-first** (the runner's fetch is best-effort, so a runner-before-orchestrator slip degrades to a skipped comment, not a failure — but route-first is correct); amd64, **no migration**, byte-identical bundle; digest-verify; confirm `/api/v1/work-units/{id}/evidence-pack` + `/evidence-pack/markdown` serve in prod `openapi.json`.

---

## Self-Review

**Spec coverage:** structured JSON canonical + markdown server-rendered from one source → Tasks 1-3 ✓. Runner relays markdown into a marker-keyed edit-in-place comment, best-effort → Task 4 ✓. Auth mirrors runner-brief (worker-readable) → Global Constraints + Tasks 2/5 ✓. Per-unit designed to compose into per-release → Task 2 interface note ✓. No migration/contract/token → Global Constraints ✓. Machine-readability preserved (JSON) → Task 2 ✓.

**Deferred (per spec):** per-release pack; post-verification refresh; immutable snapshot; in-tree file; orchestrator→GitHub posting; standards/skill-version fields.

**Placeholder scan:** the serializer (Task 2 Step 3) and markdown renderer (Task 3 Step 3) enumerate the field set by pointing at the verbatim `evidence_pack.html` bindings rather than repeating ~40 field mappings — a deliberate "mirror the existing template's fields" instruction with the exact source named. The comment-upsert mechanism (Task 4) is left as an implement-and-test choice (`--edit-last` vs find-by-marker) because both are valid and the test pins whichever is built.

**Type consistency:** `evidence_pack_projection -> dict` (Task 1) → `evidence_pack_response(dict) -> EvidencePackResponse` (Task 2) → `render_evidence_pack_markdown(EvidencePackResponse) -> str` (Task 3) → `get_evidence_pack_markdown -> str` (Task 4). The marker `<!-- sds-evidence-pack:{unit_id} -->` is identical across Task 4's post + upsert + tests. ✓
