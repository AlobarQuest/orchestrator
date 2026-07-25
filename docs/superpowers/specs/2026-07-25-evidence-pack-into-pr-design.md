# Design: Evidence Pack into the PR (WS-P2.5 Increment 1)

- **Status:** approved design, ready for implementation planning
- **Date:** 2026-07-25
- **Workstream:** WS-P2.5 Evidence Pack — Increment 1 of 2 (per-unit → PR)
- **Repos:** `AlobarQuest/orchestrator` (structured pack + JSON/markdown) and
  `AlobarQuest/factory-runner` (relay to the PR)
- **Serves:** Wave-2 objective — "any question about what happened, why, and under whose
  authority is answerable in one place" — and the Wave-2 exit item **"a merged PR carries its
  Evidence Pack"** (the per-unit half; per-release is Increment 2).

## Why

A per-unit Evidence Pack already exists — `GET /review/units/{id}/evidence-pack`
(`web.py::_projection` → `evidence_pack.html`) assembles most of the plan's contents list
(canonical provenance, authority fingerprint + enforcement snapshot, AC-keyed evidence with
current-vs-superseded flagging, adjudications incl. full waiver facts, approvals,
event-publication status, event history). **But it lives only in the auth-gated `/review`
GUI.** The plan wants the pack **on the PR**, so evidence travels with the artifact on the
actual review surface (GitHub), not just inside the orchestrator UI. This increment projects
the existing per-unit pack into the PR as a comment.

## Settled decisions

1. **PR comment** (not a committed file, not a check-run). Re-generatable, updatable, no
   repo-tree pollution; the runner already holds a PR-write token (it opens the PR).
2. **Orchestrator is the single source of the pack.** It exposes the pack as **structured
   JSON** (the canonical representation) AND renders **markdown from that same structure,
   server-side**. The runner is a **dumb relay** — it fetches the markdown and posts it; it
   never assembles or parses the pack. Keeping JSON exposed deliberately keeps the pack
   **machine-readable** for WS-P2.6 (traceability), dashboards, and audit — markdown alone
   would be a lossy, human-only projection.
3. **Runner posts at finalize (Option A).** In `_finalize_workspace`, after the PR is opened
   and bound, the runner GETs the markdown and posts/updates the comment. The pack therefore
   reflects state **as of submission**.
4. **Re-generatable projection; the immutable/hashed point-in-time snapshot is a KNOWING
   deferral.** The pack shows current orchestrator state, not a tamper-evident snapshot of
   "what we knew at merge." This is an accepted trade for a *readable* pack now; a materialized
   hashed artifact is a future workstream if the audit story demands it. (One-way for any PR
   merged before that exists — accepted.)
5. **Per-unit designed to COMPOSE into per-release** (Increment 2): a release pack = a
   collection of per-unit packs + release-level fields (artifact digest, deploy/health). Build
   the per-unit structure so Increment 2 wraps rather than rewrites it.

## Non-goals (explicit boundary)

- **Per-release pack** — Increment 2.
- **Post-verification refresh** — the pack is as-of-submit; a refresh when verification/
  adjudication/deploy land later is a fast follow-up (Increment 1.5), not here.
- **Orchestrator → GitHub posting** — the orchestrator gains no GitHub write capability; the
  runner relays.
- **Immutable/hashed snapshot** — knowingly deferred (decision 4).
- **In-tree committed `EVIDENCE.md`** — not now (additive later if offline/portable audit is
  wanted; a comment and a file can coexist).
- **New pack CONTENT fields** beyond what `_projection` already assembles — standards/skill
  versions as first-class fields (plan mentions them) are a small add only if trivially
  available; otherwise deferred with the per-release work.

## Components and data flow

```
orchestrator: _projection(session, unit_id)  ── existing structured assembly
        │  (reuse; formalize as the canonical pack structure)
        ├─→ GET /api/v1/work-units/{id}/evidence-pack        → JSON  (machines: P2.6, dashboards, audit)
        └─→ GET .../evidence-pack (markdown representation)  → markdown, rendered server-side FROM the JSON
                                                                     │
factory-runner _finalize_workspace (after PR open + bind):          │
        GET the markdown representation ──────────────────────────┘
        post/update a PR comment (gh), keyed by a hidden marker
        best-effort: a comment failure logs and continues (never blocks submit)
```

### 1. Orchestrator — structured pack + two representations

- **Reuse `web.py::_projection`** as the single assembly. Extract/formalize its output as the
  canonical pack structure (a typed dict/dataclass) so all three surfaces render from one
  source: the existing `/review` HTML page, the new JSON, and the new markdown.
- **New route** `GET /api/v1/work-units/{unit_id}/evidence-pack`. Default representation is
  **JSON** (the structured pack). A **markdown** representation is served from the same
  structure — via `Accept: text/markdown`, a `?format=markdown` query, or a sibling
  `.md`-style path (the plan/implementation picks the cleanest; content negotiation preferred).
  Markdown is rendered server-side (a markdown template or a Python renderer over the pack
  structure) — the format lives in the orchestrator, never in the runner.
- **Auth:** the runner (worker credential) must be able to read this endpoint. **Mirror
  exactly the auth the runner uses to read its `runner-brief` today** — confirm that
  credential/role during planning and match it (do not invent a new role). Human `/review`
  access is unchanged.
- Read-only; no new model, **no migration** (assembles from existing rows).

### 2. factory-runner — relay to the PR

- In `_finalize_workspace`, after `pr_binding` (the PR exists and is bound), GET the markdown
  representation of the pack for `work_unit_id` and post it as a PR comment via `gh`.
- **Idempotent update:** embed a hidden marker in the comment body
  (`<!-- sds-evidence-pack:{work_unit_id} -->`). On re-push, find the existing comment by that
  marker and **edit it in place** (update, never duplicate). If none exists, create it.
- **Best-effort / non-blocking (the Increment-1 lesson):** wrap the fetch + comment in a guard
  that catches the client/`gh` failures, logs, and **continues** — a pack-comment failure must
  never block `submit`/finalize. Exactly the discipline the cost-actuals emit needed.
- **Lightweight drift guard** (avoid silent-vanish): the runner treats the markdown as opaque,
  but a fetch returning empty/non-200 is logged distinctly (so a removed/renamed endpoint is
  visible in runner logs, not silently skipped).
- No new token/capability — reuse the `FACTORY_PR_TOKEN` the runner already uses to open the
  PR. Wire the workflow so the finalize step passes what the relay needs.

## Testing & verification

- **Orchestrator:** the structured pack JSON endpoint (asserts the real fields — provenance,
  authority fingerprint, AC outcomes incl. a waiver, PR refs) and the markdown representation
  (renders from the same structure; contains the key facts). Auth test: the runner's
  credential can read it; an unauthorized caller is rejected as the routing table dictates.
  A test that JSON and markdown derive from ONE assembly (change the source → both change).
- **factory-runner:** the relay posts a comment with the marker on first finalize and EDITS
  (not duplicates) on re-push (stub `gh`/client, assert the marker-find-then-edit path); a
  **best-effort test** proving a fetch/`gh` failure does NOT block `submit` (assert `submit`
  still runs — the exact ordering discipline from the cost-actuals emit).
- A **drill** (public surface where feasible): a finalized unit's PR gets a comment carrying
  the pack's key facts; re-finalize updates in place.
- `make check` green **on a clean tree** (read the collected count; verify `git status` clean
  before trusting local green); `ruff format` (never JSON); `/code-review`; independent
  adversarial whole-branch review. factory-runner suite green under **bare `pytest`** (the
  Makefile's invocation) — and `from test_cli import …`, not `from tests.test_cli`.
- **Deploy:** Devon-gated, amd64, **no migration**, registry bundle byte-identical (no actor
  change); orchestrator route-first (the runner's fetch 404s harmlessly if the runner ships
  first, since the comment is best-effort — but route-first is still correct); digest-verify;
  confirm `/api/v1/work-units/{id}/evidence-pack` serves in prod `openapi.json`.

## What Increment 2 / follow-ups inherit

- **Per-release pack** — wrap the per-unit structure into a release-scoped assembly joined to
  `ReleaseArtifactBinding` (digest) + `DeploymentObservation` (deploy/health).
- **Post-verification refresh** (1.5) — keep the comment current as verification/adjudication/
  deploy land (a late runner step, or the orchestrator-posts capability).
- Standards/skill-version block as first-class pack fields; the immutable/hashed snapshot;
  the in-tree `EVIDENCE.md` mode — all deferred, all additive.
