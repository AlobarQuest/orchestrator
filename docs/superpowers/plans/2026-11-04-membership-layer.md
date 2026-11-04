# Membership Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An empty member record is unreadable for every lane, every membership document says who reads the record and what each grant gates, one test states the membership lanes' outbound surface, and the layer's rules each fail a test when broken.

**Architecture:** One client change in `sds_members.client.read_members`; an architecture test holds every reader to handling `MembersUnreadable`; documentation corrections; an edge test; a mutation pass. No server change.

**Tech Stack:** Python 3.14, httpx, pytest, Markdown.

**Spec:** `docs/superpowers/specs/2026-11-04-membership-layer-design.md`

## Global Constraints

- The empty-record refusal lives in `sds_members.client.read_members` alone: raise `MembersUnreadable` naming the cause when the parsed record has no members.
- The orchestrator's own database reads (`services/membership/members.py`, `target_grant.MemberRecordGrants`) don't change.
- The membership check keeps its single outcome alarm; the lane's test-only hold functions and `MEMBER_STATES` stay.
- Docs: present tense, no "currently", "new", "now" or "soon"; superseded text moves to the ADR or archive, never deleted. `CLAUDE.md` edits stay below `<!-- code-standards:end -->`.
- Work in `.worktrees/membership` with its own venv and database `orchestrator_test_membership`. Never touch the main tree.

## Review Focus

1. A lane that reads the record without going through `read_members` (and so misses the empty rule): the architecture test in Task 1 must find every caller of the members route, not only callers of the function.
2. A record of only retired members: it is not empty, so it must still parse; populations decide what each lane does.
3. A doc correction that states a new falsehood: each corrected sentence names its code evidence in the commit body (Task 2).
4. An edge-test control that can't fail (Task 3).
5. A surviving mutant with no documented reason: fix it in the pass, don't leave it (Task 4).

---

### Task 1: An empty record is unreadable

**Files:**
- Modify: `src/sds_members/client.py` (`read_members`)
- Test: `tests/sds_members/test_client.py`
- Create: `tests/architecture/test_member_readers.py`

- [ ] **Step 1: Write the failing tests**
  - `test_an_empty_member_record_is_unreadable`: a transport answering `200` with an empty record raises `MembersUnreadable`, and the message says the record is empty.
  - `test_a_record_of_retired_members_is_read`: a record whose only member is retired parses and returns that member.
  - In `test_member_readers.py`, `test_every_reader_of_the_member_record_handles_unreadable`: every module under `src/` (outside `sds_members` and `src/orchestrator/`) that calls `read_members` or `read_members_from_environment`, or names the `/api/v1/members` path, also references `MembersUnreadable`; plus a control that a synthetic module calling `read_members` without it is reported.
- [ ] **Step 2: Run them**; the empty-record test fails.
- [ ] **Step 3: Implement** the check after `parse_members(body)`.
- [ ] **Step 4: Run** `tests/sds_members`, the new architecture test, and each lane's test directory (`tests/bump_driver tests/bump_proposer tests/change_proposer tests/estate_lander tests/membership_check tests/revision_watcher tests/rotation_proposer tests/work_carrier`). A lane test that relied on an empty record meaning "no members" is updated to expect the unread path, never by loosening the client.
- [ ] **Step 5: Commit**: `Membership: an empty member record is unreadable for every lane`

---

### Task 2: Records stay true

**Files:** `docs/operations/membership.md`, `docs/decisions/0058-sds-membership-is-one-record-with-per-service-grants.md`, `src/membership_check/consistency.py` (module docstring), `scripts/run-membership-check.sh` (header), `tests/sds_members/test_seed.py` (docstring).

- [ ] **Step 1:** For each item in the spec's Part 3, read the statement and the code it describes and note the evidence.
- [ ] **Step 2:** Correct `membership.md`: `sds-members` ships; "the three above" names stage, confirm and withdraw; `rollouts` also gates release binding, deployment observations and the candidates read; the readers are the lanes in the spec's measurements table plus the admission, landing and release gates; an empty or unreadable record is unread for every lane.
- [ ] **Step 3:** Add an ADR-0058 amendment, dated 2026-11-04, recording that every lane reads its scope from the record and that an empty record is unreadable, superseding decisions 5 and 6's phase wording.
- [ ] **Step 4:** Make `consistency.py`'s docstring and the launcher header name the sources compared, counted from the code.
- [ ] **Step 5:** `test_seed.py`'s docstring describes the seed as applied history.
- [ ] **Step 6: Run** `tests/architecture` and `tests/sds_members`; commit with each correction's evidence in the body: `Membership: correct what the layer's records say`

---

### Task 3: One test states the membership lanes' surface

**Files:** Create `tests/architecture/test_membership_edges.py`; place it in `layers.toml` if no pattern covers it.

**Interfaces:** mirror `tests/architecture/test_release_edges.py` and reuse `tests/architecture/edge_table.py`.

- [ ] **Step 1: Write the tests**, each held to the code with a control that fails on a widening:
  - `membership_check`'s hosts, routes, methods and bearers (orchestrator, change-manager, GitHub, Healthchecks, and the BWS identities its launcher reads).
  - `sds_members`' read route (observer) and stage route (SYSTEM), from the client's own predicates.
- [ ] **Step 2: Run** the file and `tests/architecture`; commit: `Membership: one test states the layer's surface`

---

### Task 4: Rules into tests, and the mutation pass

The layer's rules: one writer of the member tables plus `revert_hold.place` (`test_membership_writers.py`); the hold matcher's two copies (`test_hold_agreement.py`); the grant mapping's lane copy (`test_target_grant.py`); `rollouts` as the deploys service's wire value; and Task 1's empty-record rule.

- [ ] **Step 1:** Name the test that pins each rule; write one for the `rollouts` spelling rule if none exists.
- [ ] **Step 2:** Mutation pass from a scratchpad script (never in the repository), clearing `__pycache__` around each mutant and restoring each with the inverse edit. At least: the empty check removed; the empty check moved before parsing a retired-only record; a second writer added to `services/membership`; one matcher copy's comparison changed; `rollouts` renamed in one copy.
- [ ] **Step 3:** Kill each survivor with a test. A survivor exposing behavior with no documented reason is fixed, with a ruling in the report.
- [ ] **Step 4: Run** `make check`; commit with the mutant table in the body: `Membership: the layer's rules each fail a test when broken`

## Live proof

No server change, so nothing deploys. After merge, each lane's next pass from `main` reads the 14-member record as before: confirm `sds-membership-check`'s next run and one bridge lane's log show no unread error. An empty record refused has tests only.
