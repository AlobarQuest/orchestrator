# Execution Part 4: edges are clear Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** One test states what Execution reaches, including every contract pin with factory-runner.

**Architecture:** The dispatch call moves to `ConfinedClient`, so the existing capture sees it. `test_execution_edges.py` joins the three edge tests, and `edge_table.py` gains the shape it needs.

**Spec:** `docs/superpowers/specs/2026-11-01-execution-layer-design.md`, Part 4. Decision 3 stands: the pins stay.

## Global Constraints

- The dispatch request keeps its URL, headers, body, timeout, and error codes, with a named User-Agent added. `TransportFailure` maps to `request_error`.
- `quote(workflow_id, safe="")` stays.
- Word guards apply. Reword, never allowlist.

## Review Focus

- A refused path in `ConfinedClient` must land in a `DispatchRecord`, never escape as a 500.
- A malformed repository name (`CLAUDE.md`: nothing raises on an httpx call).
- A pin whose fixture exists here but not in factory-runner fails the test when that checkout is on disk, and skips with a stated reason when it isn't.
- A new outbound route added to `dispatch.py` fails the edge test.
- An edge row for `execution_watch` that widens fails the test.

---

### Task 1: Dispatch through `ConfinedClient`

**Files:** Modify `src/orchestrator/services/execution/dispatch.py`. Test `tests/services/test_dispatch.py`, `tests/contract/test_workflow_dispatch_contract.py`.

- [ ] Write failing tests: `test_a_refused_path_records_a_failed_dispatch`, `test_a_transport_failure_is_request_error`, and `test_the_dispatch_request_is_unchanged`, which pins URL, headers, body, and timeout.
- [ ] Add `dispatch_permits(method, path) -> bool`, true only for POST on `/repos/<owner>/<repo>/actions/workflows/<one segment>/dispatches`, and build the client with `base_url="https://api.github.com"` and a named User-Agent. Keep the catch-all the file's "nothing raises" promise needs.
- [ ] Move the existing tests' patch target. Run. Commit.

### Task 2: The edge test

**Files:** Create `tests/architecture/test_execution_edges.py`. Modify `tests/architecture/edge_table.py`, `layers.toml`.

- [ ] State these rows, each held to the real predicate or source:
  - **Orchestrator to GitHub:** the one dispatch route and the App token.
  - **`execution_watch`:** `GET /api/v1/execution-status`, alert-hub, and launcher bearers (`OBSERVER_BEARER`, `SDS_BRIDGE_ALERT_BEARER`, the dead-man pair).
  - **The member-record read:** `MemberRecordGrants` as the only grant source for dispatch and claim.
  - **Routes the runner calls:** read from factory-runner when on disk, each present in `openapi`.
  - **Pins:** a table of the seven shared fixtures, each with its pinning test, hash constant, and counterpart path, compared byte for byte when `~/Projects/factory-runner` or `FACTORY_RUNNER_DIR` is on disk. In CI the table's local half still runs.
- [ ] Mutate each row once and confirm the test fails. Run `make check`. Commit.
