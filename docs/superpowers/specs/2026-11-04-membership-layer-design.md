# SDS membership layer design

**Status:** Draft for Devon's approval. Layer id: `membership` (`make layer L=membership`).

## Intent

The member record is the one list of repositories the SDS looks after, with a grant per service. Every lane now reads its scope from it, so this layer's pass is about keeping that authority trustworthy: one rule for a record that can't be trusted, documents that say who reads it, and a test that states its edges. Recon found no failure that goes unheard and no write outside the sanctioned writers.

This pass uses the same four outcomes as the earlier layers:

1. Every signal has a home: a finding or failure reaches alert-hub as one keyed alert, never only a log.
2. Scope comes from the member record: act by grant, observe by membership.
3. Records stay true: no record, unit, or state says something that stopped being so.
4. Edges are clear: one test states each lane's outbound surface.

## Measurements, 2026-11-04

| Measure | Value |
|---|---|
| Members | 14, all `member`, 4 machine-local |
| Grants `granted` | `dependency_updates` 14, `rollouts` 10, `app_changes` 7, `security_patches` 5, `credential_rotation` 4; `backups` and `monitoring` 0 |
| Holds | 1 |
| Lanes reading the record through `sds_members` | 8 (bump driver, bump proposer, change proposer, estate lander, membership check, revision watcher, rotation proposer, work carrier), each handling `MembersUnreadable` |
| `membership_check` last runs | One 503 during an orchestrator deploy (exit 3, reported to `/fail`), then clean |

## Claude's calls, under Devon's rule that a deviation needs a documented reason

1. **An empty member record is unreadable, everywhere.** `read_members` returns an empty list as a valid record; five lanes then do nothing quietly, while the rotation executor's documented rule treats an empty record as unread. No document gives the five a reason, so the one client raises `MembersUnreadable` for an empty record, and every lane's existing unread handling applies.
2. **The membership check keeps its single outcome alarm.** ADR-0021 amendment 2 documents the trade-off (a standing finding hides a new one, accepted because findings are rare), and the check has no standing finding.
3. **The lane's test-only hold functions and the `MEMBER_STATES` copy stay.** Each has a documented reason: the agreement test compares the whole matcher, and the copy is pinned.

## Goals

- No lane acts on, or quietly skips, an empty member record.
- Every document about the record says who reads it and what each grant gates, as the code does.
- One test states the membership lanes' outbound surface.

## Non-goals

- **A keyed alert per membership finding** (call 2).
- **A check that every `rollouts` grant reaches a release path.** Nothing has asked for it, and the release gate refuses on its own.
- **The launcher path that logs "alerting disabled this run"** when the Healthchecks key can't be read. It is in `sds-deadman.sh`, which belongs to the Lane runtime layer, and is carried to that pass.

## Anti-goals

- **A lane treating an outage as "no members".** The empty-record rule exists to stop exactly that.
- **A second writer of the member tables.** Nothing in this pass writes them.

## Constraints

- The empty-record refusal lives in `sds_members.client.read_members` alone, and its message names the cause.
- The orchestrator's own reads (`services/membership/members.py`, `target_grant.MemberRecordGrants`) are out of scope: they read the database, not the route.
- Edits go in a worktree. The main tree is never touched.

## Design

### Part 1: Signals

No change (call 2).

### Part 2: Scope

`read_members` raises `MembersUnreadable("the member record is empty")` when the parsed record has no members. A test per lane isn't needed, because each already handles the error; one test pins the client, and one per-lane check confirms each of the eight catches `MembersUnreadable` (an architecture test over their source, so a ninth reader is held to it too).

### Part 3: Records

- `docs/operations/membership.md`: `sds-members` ships (lines 35-38); "the three above" names stage, confirm and withdraw (lines 171-173); `rollouts` also gates release binding, deployment observations and the candidates read; the reader list names every lane in the measurements table plus the admission, landing and release gates; the record is the scope authority for every lane (ADR-0058 amendment 1 and the bridge, rotation and execution passes).
- `docs/decisions/0058-*`: an amendment recording that every lane now reads its scope from the record, superseding decisions 5 and 6's phase wording.
- `membership_check/consistency.py:3` and `scripts/run-membership-check.sh:2-4`: name the sources compared, counted from the code.
- `tests/sds_members/test_seed.py`: the docstring describes the seed as applied history.

### Part 4: Edges

`tests/architecture/test_membership_edges.py` states `membership_check`'s hosts, routes, methods and bearers (orchestrator, change-manager, GitHub, Healthchecks, BWS) and `sds_members`' read and stage routes with their identities, each held to the code with a control, as `test_release_edges.py` does.

### Part 5: Rules into tests

Open the test for each membership rule in `CLAUDE.md` (one writer; holds' two copies; `rollouts` as the deploys wire value; the empty-record rule) and run a mutation pass over the enforced rules, reporting every survivor. A survivor with no documented reason is fixed, not left.

## Order

One plan, Part 2 first. No server change, so the deploy is the lanes' next run from `main`.

## Live proof

| Part | Can prove live | No live proof until a real event | Needs Devon first |
|---|---|---|---|
| 2 | Each lane's next pass reads the record as before | An empty record refused | None |
| 3, 4, 5 | The tests are the proof | Not applicable | None |

## Left to the builder

- Wording of the empty-record message and the docs.
