# Pre-Phase-4 Foundation Cleanliness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the foundation and Phase-3 add-on repositories clean enough to trust before Phase 4 execution runtimes at scale.

**Architecture:** Treat this as a contract-first cleanup workstream with separate PRs per owning repository. The foundation matrix remains the portfolio conformance signal, but the cleanup gate adds stricter local quality criteria: clean git state, no hidden tool skips, no type-check warnings, no pytest warnings, and explicit integration-test classification.

**Tech Stack:** Python 3.12, pytest, pyright, ruff, uv, FastAPI/Starlette/httpx test clients, TypeScript/Node/Vitest/tsc/prettier, project-standards `portfolio foundation`, intent-packages lifecycle.

## Global Constraints

- Do not fold this cleanup into WS-3.4 closure.
- Use an approved intent package before changing application or standards code.
- Preserve repository ownership boundaries; one implementation PR per touched repo unless the approved package says otherwise.
- Do not revert unrelated local changes; dirty worktrees must be attributed and resolved explicitly.
- Do not mutate production infrastructure, live event stores, launchagents, or secrets.
- No BWS token material, live DSNs, PATs, or production credentials in tracked files.
- Devon merges PRs manually.
- A repo is not "full clean" unless its default documented gate passes without hidden skips, warnings, or environment-specific PATH assumptions.

---

## Cleanliness Target

The workstream is complete only when this matrix is true for each foundation repo and each Phase-3 add-on repo:

| Criterion | Required State |
| --- | --- |
| Git state | On expected base branch or documented active PR branch; no unattributed modified/untracked files. |
| Canonical check | Default documented local command passes from a fresh shell. |
| Tool resolution | Checks use repo-local toolchain, not whichever Homebrew/global binary appears first on `PATH`. |
| Python type check | `pyright` reports `0 errors, 0 warnings, 0 informations`. |
| Python tests | `pytest` reports all tests passed with no warnings. |
| Skipped tests | Zero default skips, or skips are moved to an explicit integration gate with a named command and documented prerequisite. |
| Node checks | `tsc`, prettier, eslint, and vitest run when the repo declares the matching config. They must not be silently skipped because local `PATH` omits `node_modules/.bin`. |
| Foundation matrix | `cd ~/Projects/project-standards && uv run portfolio foundation` reports `violations=0 accepted=0 unknown=0`. |

## Repo Set

Foundation repos currently detected by `PROJECT.md foundation: true`:

- `/Users/devon/Developer/code-standards`
- `/Users/devon/Projects/project-standards`
- `/Users/devon/Projects/security-standards`
- `/Users/devon/Projects/change-manager`
- `/Users/devon/Projects/brain`
- `/Users/devon/Projects/infraops-mcp-server`
- `/Users/devon/Projects/intent-packages`
- `/Users/devon/Projects/orchestrator`
- `/Users/devon/Projects/alobar-id`
- `/Users/devon/Projects/vps-backup`

Phase-3 WS-3.4 participating repos:

- `/Users/devon/Projects/orchestrator`
- `/Users/devon/Projects/security-standards`
- `/Users/devon/Projects/intent-packages`
- `/Users/devon/Projects/change-manager`

## Current Findings To Resolve

| Repo | Finding |
| --- | --- |
| `orchestrator` | Full gate passes, but pytest emits Starlette/httpx deprecation warning. |
| `security-standards` | Full gate passes, but pyright emits 5 missing-source warnings and pytest skips 3 `FACTORY_TEST_DSN` tests. |
| `intent-packages` | Full gate passes, but pyright emits 3 missing-source warnings and validation emits a legacy package profile warning. |
| `project-standards` | Full gate passes, but pyright emits 2 missing-source warnings. |
| `change-manager` | Bare `make check` can fail by resolving global pytest/python; `.venv/bin`-first gate passes but pytest emits Starlette/httpx warning. |
| `brain` | Gate passes but pytest emits Starlette/httpx warning; local checkout is on `codex/foundation-ci-green` with untracked `uv.lock`. |
| `infraops-mcp-server` | Bare `make check` exits 0 while skipping `tsc` and prettier when `node_modules/.bin` is absent from `PATH`; checkout is on `codex/foundation-ci-green`. |
| `alobar-id` | Dirty worktree: modified `PROJECT.md`; no local code gate, only launchagent-backed required check. |

---

### Task 1: Author Pre-Phase-4 Cleanup Intent Package

**Files:**
- Create: `/Users/devon/Projects/intent-packages/packages/pre-phase4-foundation-cleanliness/package.yaml`
- Create: `/Users/devon/Projects/intent-packages/packages/pre-phase4-foundation-cleanliness/lineage.yaml`

**Interfaces:**
- Consumes: intent-packages CLI lifecycle commands.
- Produces: an approved cleanup package before cross-repo edits begin.

- [ ] **Step 1: Re-establish baseline**

Run:

```bash
for repo in \
  /Users/devon/Developer/code-standards \
  /Users/devon/Projects/project-standards \
  /Users/devon/Projects/security-standards \
  /Users/devon/Projects/change-manager \
  /Users/devon/Projects/brain \
  /Users/devon/Projects/infraops-mcp-server \
  /Users/devon/Projects/intent-packages \
  /Users/devon/Projects/orchestrator \
  /Users/devon/Projects/alobar-id \
  /Users/devon/Projects/vps-backup
do
  printf '\n== %s ==\n' "$repo"
  git -C "$repo" status -sb
  git -C "$repo" branch --show-current
done
```

Expected: Output records the same dirty/non-main states or newer Devon-resolved states. Do not modify any repo in this step.

- [ ] **Step 2: Create the draft package through the authoring front door**

Use package ID `pre-phase4-foundation-cleanliness` unless Devon explicitly chooses a different ID before authoring.

The package must include:

- repositories listed in this plan;
- required checks from each repo;
- authority to make code-standards Makefile/toolchain parity changes where needed;
- authority to change test clients or warning policy in affected app repos;
- authority to classify security-standards factory-store tests as default or integration;
- stop condition if a dirty worktree contains unattributed user work;
- rollback plan: abandon per-repo branches before merge; after merge, revert the specific PR;
- explicit exclusion of Phase 4 runner dispatch, production deployment, live factory-events store mutation, and infra mutation.

- [ ] **Step 3: Validate and hash the package**

Run:

```bash
cd /Users/devon/Projects/intent-packages
PYTHONPATH=src .venv/bin/python -m intent_packages validate packages/pre-phase4-foundation-cleanliness
PYTHONPATH=src .venv/bin/python -m intent_packages hash packages/pre-phase4-foundation-cleanliness
```

Expected: validation succeeds; hash is recorded for Devon.

- [ ] **Step 4: Move to ready_for_review and obtain approval**

Run:

```bash
cd /Users/devon/Projects/intent-packages
PYTHONPATH=src .venv/bin/python -m intent_packages transition packages/pre-phase4-foundation-cleanliness --to ready_for_review
```

Expected: package is `ready_for_review`. Stop until Devon approves the exact revision and hash.

---

### Task 2: Normalize Local Gate Tool Resolution

**Files:**
- Modify if needed: `/Users/devon/Projects/change-manager/Makefile`
- Modify if needed: `/Users/devon/Projects/orchestrator/Makefile`
- Modify if needed: `/Users/devon/Projects/infraops-mcp-server/Makefile`
- Modify if needed: `/Users/devon/Developer/code-standards/templates/Makefile` or equivalent vendored source

**Interfaces:**
- Consumes: repo-local `.venv/bin` and `node_modules/.bin`.
- Produces: default `make check` commands that do not depend on caller PATH and do not silently skip installed repo tools.

- [ ] **Step 1: Write failing gate-parity tests or scripts**

For Python repos using the vendored generic Makefile, add or update tests in the owning standards repo so generated Makefiles prefer `.venv/bin` when it exists.

For `infraops-mcp-server`, add a Makefile behavior test or runbook assertion that `make check` invokes `./node_modules/.bin/tsc` and `./node_modules/.bin/prettier` when those files exist.

- [ ] **Step 2: Verify the current failure**

Run:

```bash
cd /Users/devon/Projects/change-manager
make check
```

Expected before fix on the current machine: bare `make check` may fail by using global pytest/python.

Run:

```bash
cd /Users/devon/Projects/infraops-mcp-server
PATH="/usr/bin:/bin:/usr/sbin:/sbin" make check
```

Expected before fix: the command reports skipped Node tools or cannot find tools.

- [ ] **Step 3: Update Makefile tool lookup**

For Python repos, use repo-local tools when present:

```make
VENV_BIN := $(CURDIR)/.venv/bin
PATH := $(VENV_BIN):$(PATH)
```

For Node repos, use local Node tools when present:

```make
NODE_BIN := $(CURDIR)/node_modules/.bin
PATH := $(NODE_BIN):$(PATH)
```

The implementation must still degrade gracefully only when the repo does not contain the relevant tool installation.

- [ ] **Step 4: Verify from a fresh shell**

Run:

```bash
cd /Users/devon/Projects/change-manager && make check
cd /Users/devon/Projects/infraops-mcp-server && make check
```

Expected after fix: no global-tool failure and no skipped `tsc` or prettier messages when local dependencies exist.

---

### Task 3: Remove Starlette/httpx TestClient Warnings

**Files:**
- Modify: `/Users/devon/Projects/orchestrator/tests/**`
- Modify: `/Users/devon/Projects/change-manager/tests/**`
- Modify: `/Users/devon/Projects/brain/tests/**`
- Modify: each affected repo `pyproject.toml`

**Interfaces:**
- Consumes: FastAPI/Starlette ASGI apps.
- Produces: warning-clean HTTP tests.

- [ ] **Step 1: Inventory synchronous TestClient usage**

Run:

```bash
for repo in /Users/devon/Projects/orchestrator /Users/devon/Projects/change-manager /Users/devon/Projects/brain
do
  printf '\n== %s ==\n' "$repo"
  rg -n "from (fastapi|starlette)\\.testclient import TestClient|TestClient\\(" "$repo/tests"
done
```

Expected: all warning-producing imports/usages are listed.

- [ ] **Step 2: Convert tests to `httpx` ASGI transport where practical**

Preferred async form:

```python
import httpx
import pytest

@pytest.mark.asyncio
async def test_health(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
```

For repos with extensive synchronous fixtures, use the smallest local compatibility helper rather than repeating setup in every test.

- [ ] **Step 3: Add warning-as-error policy after conversion**

In each affected repo `pyproject.toml`, add:

```toml
[tool.pytest.ini_options]
filterwarnings = [
  "error::starlette.exceptions.StarletteDeprecationWarning",
]
```

If the repo already has `[tool.pytest.ini_options]`, merge this key into the existing table.

- [ ] **Step 4: Verify warning-free tests**

Run:

```bash
cd /Users/devon/Projects/orchestrator
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test make check

cd /Users/devon/Projects/change-manager
make check

cd /Users/devon/Projects/brain
make check
```

Expected: all pass with no Starlette/httpx warning summary.

---

### Task 4: Remove Pyright Missing-Source Warnings

**Files:**
- Modify: `/Users/devon/Projects/security-standards/pyproject.toml`
- Modify: `/Users/devon/Projects/intent-packages/pyproject.toml`
- Modify: `/Users/devon/Projects/project-standards/pyproject.toml`
- Modify lockfiles only if dependency metadata changes require it.

**Interfaces:**
- Consumes: `yaml` and `jsonschema` runtime dependencies.
- Produces: `pyright` output with zero warnings.

- [ ] **Step 1: Reproduce exact warnings**

Run:

```bash
cd /Users/devon/Projects/security-standards && make check
cd /Users/devon/Projects/intent-packages && make check
cd /Users/devon/Projects/project-standards && make check
```

Expected before fix: pyright missing-source warnings for `yaml` and/or `jsonschema`.

- [ ] **Step 2: Prefer typed dependency configuration over suppression**

For each repo, test whether installing or declaring the appropriate type package removes the warning:

```bash
cd <repo>
uv add --dev types-PyYAML
```

If `jsonschema` still warns because upstream provides no complete source typing for this usage, add the narrowest pyright configuration that makes the policy explicit:

```toml
[tool.pyright]
reportMissingModuleSource = "none"
```

Use this only if type package/dependency correction does not resolve the warning.

- [ ] **Step 3: Verify each repo**

Run:

```bash
cd /Users/devon/Projects/security-standards && make check
cd /Users/devon/Projects/intent-packages && make check
cd /Users/devon/Projects/project-standards && make check
```

Expected: `0 errors, 0 warnings, 0 informations` from pyright in all three repos.

---

### Task 5: Classify Security-Standards Factory Store Tests

**Files:**
- Modify: `/Users/devon/Projects/security-standards/tests/test_factory_ship.py`
- Modify: `/Users/devon/Projects/security-standards/pyproject.toml`
- Modify: `/Users/devon/Projects/security-standards/Makefile`
- Modify: `/Users/devon/Projects/security-standards/src/factory_events/README.md`

**Interfaces:**
- Consumes: optional `FACTORY_TEST_DSN`.
- Produces: default unit gate with zero skips and a separate integration command for real disposable store tests.

- [ ] **Step 1: Decide the approved classification**

Recommendation: move `FACTORY_TEST_DSN` tests out of default `make check` and into `make check-integration`. Default `make check` should have zero skips; integration tests should run only with an explicit disposable DSN.

- [ ] **Step 2: Mark integration tests explicitly**

Use a marker:

```python
import pytest

pytestmark = pytest.mark.integration
```

Apply it to `tests/test_factory_ship.py`.

- [ ] **Step 3: Exclude integration tests from default pytest**

In `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
  "integration: tests requiring external disposable services",
]
addopts = "-q -m 'not integration'"
```

Merge with existing pytest options.

- [ ] **Step 4: Add explicit integration target**

In `Makefile`:

```make
.PHONY: check-integration

check-integration:
	@if [ -z "$$FACTORY_TEST_DSN" ]; then echo "FACTORY_TEST_DSN is required for check-integration"; exit 2; fi
	$(PY) -m pytest -q -m integration
```

- [ ] **Step 5: Verify default and integration behavior**

Run:

```bash
cd /Users/devon/Projects/security-standards
make check
FACTORY_TEST_DSN= make check-integration
```

Expected: `make check` passes with zero skips; `make check-integration` exits 2 with the explicit DSN message when no DSN is supplied.

---

### Task 6: Resolve Intent-Packages Legacy Validation Warning

**Files:**
- Modify one of:
  - `/Users/devon/Projects/intent-packages/packages/ws-2.3-intent-authoring-skill/package.yaml`
  - `/Users/devon/Projects/intent-packages/src/intent_packages/checks_*.py`
  - `/Users/devon/Projects/intent-packages/docs/**`

**Interfaces:**
- Consumes: legacy package content and validator semantics.
- Produces: `validate --all` output with no warnings, or a documented accepted legacy exception with a non-warning status.

- [ ] **Step 1: Inspect warning source**

Run:

```bash
cd /Users/devon/Projects/intent-packages
PYTHONPATH=src .venv/bin/python -m intent_packages validate packages/ws-2.3-intent-authoring-skill
rg -n "recognized profile evidence tags|declared profile|acceptance indexes" src tests packages/ws-2.3-intent-authoring-skill
```

Expected: the validator rule and package fields causing the warning are identified.

- [ ] **Step 2: Choose the least historical-risk fix**

Recommendation: do not edit approved historical intent semantics unless validation already supports metadata-only revisions. Prefer making the validator classify this known legacy condition as an accepted legacy note that does not print as a warning during `validate --all`.

- [ ] **Step 3: Add regression coverage**

Add a test that `validate --all` output for the repository contains no `warning:` lines while still detecting real malformed profile evidence in new packages.

- [ ] **Step 4: Verify**

Run:

```bash
cd /Users/devon/Projects/intent-packages
PYTHONPATH=src .venv/bin/python -m intent_packages validate --all
make check
```

Expected: all packages validate, no warning lines, tests pass.

---

### Task 7: Resolve Dirty and Non-Main Foundation Checkouts

**Files:**
- `/Users/devon/Projects/alobar-id/PROJECT.md`
- `/Users/devon/Projects/brain/uv.lock`
- PR/branch state for `/Users/devon/Projects/brain`
- PR/branch state for `/Users/devon/Projects/infraops-mcp-server`
- PR/branch state for `/Users/devon/Projects/intent-packages`

**Interfaces:**
- Consumes: git status and open PR state.
- Produces: all foundation repos on expected branches with no unattributed local changes.

- [ ] **Step 1: Attribute each dirty/non-main state**

Run:

```bash
for repo in \
  /Users/devon/Projects/alobar-id \
  /Users/devon/Projects/brain \
  /Users/devon/Projects/infraops-mcp-server \
  /Users/devon/Projects/intent-packages
do
  printf '\n== %s ==\n' "$repo"
  git -C "$repo" status -sb
  git -C "$repo" branch --show-current
  git -C "$repo" log --oneline -3
done
```

- [ ] **Step 2: Do not discard changes automatically**

If `alobar-id/PROJECT.md` and `brain/uv.lock` are intentional, include them in the appropriate PRs. If they are unrelated user work, stop and ask Devon where they belong.

- [ ] **Step 3: Merge or close active cleanup PRs**

For `brain`, `infraops-mcp-server`, and `intent-packages`, identify whether the current branch is still an open PR, already merged, or stale:

```bash
cd <repo>
gh pr status
```

Expected: each branch has an explicit disposition before final cleanliness is claimed.

- [ ] **Step 4: Fast-forward clean repos to main after Devon merges**

Run only after Devon confirms relevant PRs are merged:

```bash
cd <repo>
git checkout main
git pull --ff-only
git status -sb
```

Expected: clean `main...origin/main`.

---

### Task 8: Final Cleanliness Evidence Matrix

**Files:**
- Create: `/Users/devon/Projects/orchestrator/docs/superpowers/evidence/pre-phase4-foundation-cleanliness-evidence.md`
- Optionally modify: `/Users/devon/docs/software-delivery-system/2026-07-05-phase3-status-checklist.md`

**Interfaces:**
- Consumes: final local and CI check outputs.
- Produces: evidence Devon can use to decide whether Phase 4 can start.

- [ ] **Step 1: Run final repo status audit**

Run:

```bash
for repo in \
  /Users/devon/Developer/code-standards \
  /Users/devon/Projects/project-standards \
  /Users/devon/Projects/security-standards \
  /Users/devon/Projects/change-manager \
  /Users/devon/Projects/brain \
  /Users/devon/Projects/infraops-mcp-server \
  /Users/devon/Projects/intent-packages \
  /Users/devon/Projects/orchestrator \
  /Users/devon/Projects/alobar-id \
  /Users/devon/Projects/vps-backup
do
  printf '\n== %s ==\n' "$repo"
  git -C "$repo" status -sb
done
```

Expected: no modified or untracked files except deliberately active PR branches documented in the evidence file.

- [ ] **Step 2: Run final canonical checks**

Run:

```bash
cd /Users/devon/Developer/code-standards && make check
cd /Users/devon/Projects/project-standards && make check
cd /Users/devon/Projects/security-standards && make check
cd /Users/devon/Projects/change-manager && make check
cd /Users/devon/Projects/brain && make check
cd /Users/devon/Projects/infraops-mcp-server && make check && npm test
cd /Users/devon/Projects/intent-packages && PYTHONPATH=src .venv/bin/python -m intent_packages validate --all && make check
cd /Users/devon/Projects/orchestrator && PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test make check
cd /Users/devon/Projects/project-standards && uv run portfolio foundation
```

Expected:

- all commands exit 0;
- no pyright warnings;
- no pytest warnings;
- no default pytest skips;
- no skipped tool messages;
- foundation output is `violations=0 accepted=0 unknown=0`.

- [ ] **Step 3: Record explicit exceptions**

If a repo cannot have a local code gate because its required check is a launchagent or operational check, record:

- the required check ID from `PROJECT.md`;
- the last known successful execution source;
- whether this blocks Phase 4.

- [ ] **Step 4: Open PRs ready for review**

Each touched repo gets a ready-for-review PR with:

- summary of warning/skip/tooling issue removed;
- exact local verification output;
- CI check names and status;
- statement that no production infrastructure, live event store, or secrets were touched.

---

## Recommended PR Grouping

Use this grouping unless the approved intent package narrows it:

1. `code-standards`: Makefile template/tool-resolution policy, if shared template changes are needed.
2. `change-manager`: local Makefile parity plus Starlette/httpx warning cleanup.
3. `orchestrator`: Starlette/httpx warning cleanup.
4. `brain`: Starlette/httpx warning cleanup plus `uv.lock` disposition.
5. `security-standards`: pyright warnings plus integration-test classification.
6. `intent-packages`: pyright warnings plus legacy validation warning cleanup.
7. `project-standards`: pyright warnings.
8. `infraops-mcp-server`: Node Makefile tool resolution.
9. `alobar-id`: `PROJECT.md` dirty-state disposition if not already part of an open foundation PR.

## Self-Review

- Spec coverage: The plan covers the observed httpx warnings, pyright warnings, security-standards skips, intent-packages validator warning, hidden tool skips, dirty working trees, non-main branches, and final evidence requirements.
- Placeholder scan: No task uses TBD/TODO/fill-in placeholders. Branch disposition steps stop for Devon only where user-owned dirty work may be present.
- Scope control: The plan excludes Phase 4 runtime dispatch, production deployment, live event-store mutation, and infrastructure mutation.
