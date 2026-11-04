# Intent layer design and plan

**Status:** Draft for Devon's approval. Layer id: `intent` (`make layer L=intent`). The layer is one module, so the spec and its one plan share this document.

## Intent

`src/orchestrator/package_sources.py` reads an approved intent package from its checkout, proves its approval (a recognised approver, the sibling `intent_packages verify-approval` command, the factory event chain, and a matching approval event), and builds the intake payload the work carrier registers through `orchestrator emit-intake-payload`. Recon found it fails closed on every missing dependency. The gap is that nine different refusals reach a person as the same sentence, "approval verification failed", so the carrier's alarm says something is wrong but not what.

The four outcomes, as in every layer: signals have a home; scope from the member record; records stay true; edges are clear.

## Findings, 2026-11-04

| Outcome | Finding |
|---|---|
| Signals | `_verify_current_approval` and `_verified_approval_from_events` return `None` for nine causes, and the caller raises one message for all of them. No document gives a reason. |
| Scope | Not applicable: the module acts on no repository; admission and dispatch read the grant downstream. |
| Records | The comments citing the module (`decision_facts.py`, `decomposition.py`, `workability.py`) match the code. |
| Edges | Two subprocesses (`python -m intent_packages verify-approval <path>`, `python -m factory_events verify`) and three local reads (the security-standards registry, the factory events file, the package checkout's git head) have no test stating them. |

## Calls (Claude's, under the rule that a deviation needs a documented reason)

1. **Each refusal names its cause.** `PackageSourceError` messages become "approval verification failed: <cause>", one cause per check, so the prefix every existing test and caller matches still holds.
2. **No behavior change otherwise.** Every refusal stays a refusal.

## Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Work in `.worktrees/intent` with its own venv and database `orchestrator_test_intent`; never touch the main tree.

### Task 1: Each refusal names its cause

**Files:** `src/orchestrator/package_sources.py`; tests in `tests/cli/test_package_intake_cli.py`.

- [ ] **Step 1: Write the failing tests**, one per cause, each asserting `pytest.raises(PackageSourceError, match="approval verification failed: <cause words>")`: an unrecognised approver; a non-integer revision; an empty commit or event id; the sibling intent-packages checkout missing; `verify-approval` failing; the factory chain failing to verify; the event log unreadable; no matching approval event.
- [ ] **Step 2: Run them**; they fail on the missing cause text.
- [ ] **Step 3: Implement** by having `_verify_current_approval` and `_verified_approval_from_events` return the cause (or raise `PackageSourceError` with it) instead of `None`; keep the shared prefix. The four existing `match="approval verification failed"` tests stay unchanged and passing.
- [ ] **Step 4: Run** `tests/cli`, `tests/work_carrier`, `tests/services/test_protocol_fixture_intake.py`, `tests/protocol/test_ws33_smoke.py`.
- [ ] **Step 5: Commit**: `Intent: each approval refusal names its cause`

### Task 2: One test states the layer's surface, and the mutation pass

**Files:** create `tests/architecture/test_intent_edges.py` (place it in `layers.toml`'s `intent` layer).

- [ ] **Step 1:** Assert, from the module's source (AST), that it runs exactly two subprocess commands (`intent_packages verify-approval`, `factory_events verify`), imports no HTTP client, and reads only the registry, the events file and the package checkout; each with a control that fails on a widening.
- [ ] **Step 2:** Mutation pass from a scratchpad script (never in the repository), clearing `__pycache__` around each mutant: drop each of the nine checks in turn, and flip the CLI and chain results to pass. Kill each survivor with a test; a survivor exposing behavior with no documented reason is fixed.
- [ ] **Step 3:** Run `make check`; commit with the mutant table: `Intent: one test states the layer's surface, and its checks each fail a test`.

## Live proof

No server change; the carrier runs `emit-intake-payload` from `main` on its next pass. A refusal's new text has tests only until a real package is refused.
