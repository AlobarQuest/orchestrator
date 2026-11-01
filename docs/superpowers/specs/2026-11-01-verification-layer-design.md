# Verification layer design

**Status:** Draft for Devon's approval. Layer id: `verification` (`make layer L=verification`).

## Intent

Verification decides whether finished work met its acceptance criteria, and it's why completion belongs to the verifier and not the worker. The logic is sound. The gap is that nothing runs it for an app-change unit: the only lane that verifies factory units skips everything except rotations and bumps. Such a unit reaches `submitted` and stays there with no alert. Phase 4's client requests take that path, so this closes first.

This pass uses the same four outcomes as the earlier layers:

1. Every signal has a home: a finding or failure reaches alert-hub as one keyed alert, never only a log.
2. Scope comes from the member record: act by grant, observe by membership.
3. Records stay true: no record, unit, or state says something that stopped being so.
4. Edges are clear: one test states each lane's outbound surface.

## Measurements, 2026-11-01

| Measure | Value |
|---|---|
| Units in `submitted` or `verifying` | 0 |
| Lanes calling `/verify` | 2: `rotation-verifier` (rotations and bumps) and `image_release` (post-deploy units) |
| App-change units verified by any lane | 0 |
| Outbound clients outside `ConfinedClient` | 1: `github_checks.py` |

## Devon's decisions, 2026-11-01

1. **The existing lane widens to every factory unit, and is renamed `unit-verifier`.**
2. **The server keeps trusting the VERIFIER identity on which check is named,** with the lane's behavior pinned by a test. Binding the name on the server is deferred, and Devon will re-evaluate it before Phase 5. The year plan's open questions and `PROJECT.md` both carry it.

## Goals

- Every submitted factory unit is verified without a person starting it.
- A unit of any class stuck in `submitted` or `verifying` raises one keyed alert.
- The lane acts by grant.
- One test states the verification surface.

## Non-goals

- **Server-side binding of a check name** (decision 2). It changes the package schema across repositories.
- Watching a check that goes red after completion. Landing acts on that, and reading it needs a new watch. *Claude's call.*
- Changing how any criterion type resolves. The human floors stay.

## Anti-goals

- **A machine deciding judgment.** An `automated_test` with no evaluator, and every human criterion, still go to a person.
- **A rotation affected by a factory rule.** The widened lane keeps every rotation path it has.
- **A silent failed verify.** A refusal is a keyed alert, not only a log line.

## Constraints

- `verify_work_unit` is the only path to a verifier adjudication.
- Rotation and bump verification must not change.
- A rename moves the launcher, plist, Healthchecks check, docs, and `layers.toml` together, and the old agent is removed as the new one installs.
- Every `github_checks.py` failure stays a refusal: `CLAUDE.md`'s `(httpx.HTTPError, httpx.InvalidURL, ValueError)` rule applies.

## Design

### Part 1: Every signal has a home

`execution-status` gains a `verification_overdue` field for `submitted` and `verifying` units, past `dead_letter_stalled_verification_seconds`, which the route passes in. `execution-watch` raises `verification-stalled:<unit>`, raised on presence and gated on overdue, as `admission-waiting` is, so seeded keys aren't pruned. The alert covers every unit in those states, including rotation and post-deploy units, so it's an exception to the lane's rotation skip. History is absorbed silently, in two pull requests, as in Admission Part 1. The server deploys before the lane reads the field.

The verifier lane's per-unit refusals (`required_checks_unreadable`, `bump_unit_no_required_check`, a refused verify) become keyed alerts through `KeyedAlerts` on the `sds-bridge` token. A failed alert-token fetch must not stop a rotation. A pass that can't read its queue holds every key. A per-unit refusal exits as a finding, so one cause doesn't raise both `/fail` and an alert.

### Part 2: The lane covers every factory unit

**Selection.** The lane verifies a submitted factory unit with a dispatch and an armed head. It excludes `required_capability == post_deploy_verification`, which `image_release` owns, and whose `/verify` would refuse. It doesn't select by state alone.

**Criteria.** Today every check is filed under `AC-001`, and `verifier_evidence.py` refuses a criterion that isn't `automated_check`, so an app-change unit would be refused every pass. `VerifiableUnitResponse` gains the unit's required `automated_check` ids. Each such criterion gets every required check, because nothing maps a criterion to a check. A unit with none goes straight to `/verify`, where `automated_test` evaluates or goes to a person. The evidence idempotency key gains the criterion id, or a second criterion replays the first's evidence.

**Scope.** Before verifying a non-bump factory unit, the lane reads `GET /api/v1/members`, by `sds_members.populations`. A withdrawn grant holds the unit and alerts. An unread record is not a withdrawal. Rotation units have no repository and skip it. A bump keeps today's behavior, because landing already refuses a bump with no grant.

**No required check.** An app-change unit in a repository with none reaches a person, and never passes by default.

**The rename is cosmetic.** `orchestrator-verifier`, the VERIFIER bearer, and its BWS id all stay, because `image_release` shares the identity. Register the launcher as a reader of `BWS_ACCESS_TOKEN_SDS`, which it isn't today. The rename lands in steps, because `activate-checkout.sh` re-executes the launcher, and deleting it would stop the lane with no ping:

1. Add `run-unit-verifier.sh` and `sds-unit-verifier` beside the old ones, and swap the LaunchAgents.
2. Delete the old launcher, and retire the old check.

**Proof.** Production has no pending unit, so a dry run proves nothing. Prove the widened selection with seeded units in tests, and with an app-change unit on a test repository if Devon agrees.

### Part 3: Records stay true

Correct `dead_letter.py`, `config.py`, `docs/operations/verifier.md`, and `credentials.md`, which say no schedule runs the verifier or name an allowlist that's gone.

### Part 4: Edges are clear

`github_checks.py` moves to `ConfinedClient` with a route predicate and a bounded error catch. The request stays the same apart from a named User-Agent, and `X-GitHub-Api-Version` is pinned. Redirects stay off, the `total_count` refusal stays, and the token passes per request. This also closes the `UnicodeError` escape. `tests/architecture/test_verification_edges.py` states the lane's routes, the GitHub routes, and both tokens, and the lane's row moves there. Tests pin sort-and-stop for non-bumps too, and that unfiled checks never reach `/verify`, because decision 2 rests on them.

### Part 5: Rules into tests

Add the missing test that named evidence is accepted only on `automated_check` criteria, open the five rules whose tests were seen only by name, run a mutation pass over the enforced rules, and move history to the archive.

## Order

Part 1 before Part 2, so the widened lane is watched from its first pass. Each part has its own plan, reviews, and live proof.

## Live proof

| Part | Can prove live | No live proof until a real event | Needs Devon first |
|---|---|---|---|
| 1 | The read, zero firing | Each alert | A staged stall on `sds-canary` |
| 2 | The rename, and the lane's passes | A real app-change unit verified | A real app-change unit on a test repository |
| 3 | Not applicable | Not applicable | None |
| 4 | The test is the proof | Not applicable | None |
| 5 | Mutation counts | Not applicable | None |

## Left to the builder

- Field names, and the alert wording, with the next step in it.
