# Release and observation layer design

**Status:** Draft for Devon's approval. Layer id: `release` (`make layer L=release`).

## Intent

This layer records what reached production and what the estate reports about it: release artifact bindings, deployment observations, the observation store every sensor writes to, the reconciliation conditions an observation can open, and the reporting routes that read all of it. It's how the SDS knows a change was deployed, not only merged.

Recon found no failure that goes unheard. The gaps are smaller: a scope check that runs only in one client, text that names things that no longer exist, and edges that are partly pinned. This pass closes them against the same four outcomes as the earlier layers:

1. Every signal has a home: a finding or failure reaches alert-hub as one keyed alert, never only a log.
2. Scope comes from the member record: act by grant, observe by membership.
3. Records stay true: no record, unit, or state says something that stopped being so.
4. Edges are clear: one test states each lane's outbound surface.

## Measurements, 2026-11-03

| Measure | Value |
|---|---|
| Observations stored | 3,992, from 16 producer, type, and subject combinations, 2026-07-09 to 2026-11-03 |
| `GET /api/v1/observations` response | Every row, about 6 MB. The route takes no limit. |
| Callers of `GET /api/v1/observations` | 2, both filtered: `scripts/replay_deploy_signal.py`, and the rotation executor's reader check (`rotation_worker/readers.py`), deployed separately |
| `sds.alobar.net` behind Cloudflare | No. It resolves to the OVH host, and a Python User-Agent gets 200. |
| Callers of `image-release bind` | 1: `scripts/deploy_orchestrator.py`, once per orchestrator deploy, run by an operator |
| Callers of the machine-local bind routes | 1: the `com.devon.activation-sweep` job, daily |
| Server-side grant checks on release binding | 0. The bind routes check only the SYSTEM role. |
| `AlobarQuest/orchestrator`'s `rollouts` grant | `granted` |
| `attest-exit-criteria` runs | Green on 2026-10-19, 10-26, and 11-02. All five claims pass. |

Every `failed` or `degraded` observation comes from a Sensors-layer or Fork-layer producer. Those layers are already reviewed, so this layer's concern is only what ingest does with a row.

## Devon's decisions, 2026-11-03

1. **Release binding requires the `rollouts` grant, checked on the server.** The check is on the bind, deployment-observation, and machine-activation routes, the same pattern as the claim-side grant check. Withdrawing `rollouts` from the orchestrator also refuses binding its own deploys, which is what withdrawing it means.
2. **GitHub's failure email is the home of a failed `attest-exit-criteria` run,** and the documentation says so. An alert-hub alert would put an alert-hub token in Actions secrets, a new credential copy that rotation must register and update.

## Goals

- No release artifact, deployment observation, or machine-activation candidate is served or recorded for a repository the member record doesn't grant `rollouts`.
- Every document, docstring, and alert in the layer describes the system as it runs.
- One test states the layer's outbound and route surfaces.

## Non-goals

- **Deleting retired code.** The tracker adapter, the manual reconciliation passes, `consistency-check`, `in-flight-units`, and `dead-letter` have no scheduled caller, but the exit manifests attest them (`wave-exit-manifest.toml`, `exit-criteria-claims.toml`). `CLAUDE.md` forbids deleting what a manifest attests.
- **Alerting on SLO values.** They're measurements, not failures. *Claude's call.*
- **A keyed alert for `image-release`.** Its only caller is an operator at a terminal, who sees its exit code and JSON. *Claude's call.*
- **Changing what the Sensors-layer producers observe or alert on.**
- **Scheduling the manual reconciliation passes.** ADR-0048 accepted that gap.
- **Bounding `GET /api/v1/observations`.** Both callers filter, so neither reads all 3,992 rows; only an unfiltered read does. A limit or a new order would change a contract the separately deployed rotation executor relies on to hold back retire steps. *Claude's call.*
- **Widening `test_release_routes_call_no_mutator.py`.** It reads only each route's own source, and the deployment-observation route mints its unit inside the service, so the widened scan would pass while proving nothing. ADR-0039's tests already pin minting. *Claude's call.*
- **A named User-Agent in `attest_exit_criteria.py`.** `sds.alobar.net` isn't behind Cloudflare, so the `1010` block can't occur. *Claude's call.*

## Anti-goals

- **A deploy that half-binds.** A grant refusal must come before anything is written, as the claim's does.
- **A silent refusal.** A bind refused for its grant is a `DomainError` the caller prints, never an empty success.
- **A guard keyed on one field while the write uses another.** The grant is read for the unit's own repository, and a request naming a different repository is refused, not trusted.

## Constraints

- The grant check uses `target_grant.MemberRecordGrants.is_granted(repository, "rollouts")`, case-folded, on a non-retired member. `rollouts` is the wire value (word guards).
- The repository whose grant is read is the unit's envelope `target_repository`, never the request body's `source_repository`, which is caller-supplied. A body `source_repository` that differs from it, case-folded, is refused, and so is a unit that names no repository. Every candidate the server serves already matches its unit's target, so neither refusal fires on today's callers. A deployment observation reads its binding's unit. *Claude's call, from the builder check.*
- A refusal raises a `DomainError` with a new code, so it isn't a bare 500. Every new code passes `test_no_reported_kind_is_a_substring_of_another`.
- A changed response model ships with its contract test in the same change (`CLAUDE.md`, the `response_model` rule).
- The orchestrator deploys before any client relies on the new behavior, and `deploy_orchestrator.py`'s own bind runs against the new check in that same deploy.
- Edits go in a worktree. The main tree is never touched.

## Design

### Part 1: Every signal has a home

No new alert. Two fixes:

- `execution_watch/decide.py`'s `_verification_steps` tells a person to "read the image-release lane's log", but there's no such lane or log. The text names what actually verifies a post-deploy unit: the bind and verify step of `scripts/deploy_orchestrator.py`, run in a deploy session, and how to rerun it.
- The `attest-exit-criteria` workflow header and the doc that describes exit attestation state that a failed run's home is GitHub's failure email (decision 2).

A bind refused by the new grant check reaches its caller as a non-zero exit with the refusal code. `image-release` runs in an operator's terminal. For `activation-sweep`, the builder confirms that the sweep's existing outcome alarm covers a refused bind. If it doesn't, the refusal is added to the alarm the sweep already sends, with no new machinery.

### Part 2: Scope comes from the member record

The server refuses, before any write:

- `POST /api/v1/work-units/{unit_id}/release-artifacts` for a `source_repository` without `rollouts`.
- `POST /api/v1/release-artifacts/{binding_id}/deployment-observations` for a binding whose `source_repository` has lost `rollouts`.
- `GET /api/v1/machine-activation-candidates` for a repository without `rollouts`.

**Replays.** `_record_release_artifact` has two replay paths. A request that repeats an idempotency key answers as it did, before any check, so an exact retry after a withdrawal isn't turned into an error. The repository match and the grant come next, before the same-source-tuple match and before any write, so a fresh request after a withdrawal is refused even when an earlier binding exists. This mirrors the decomposition proposal, which asks past replay. Tests pin both paths. *Claude's call.*

**The candidates read.** It refuses every role, with the same code, for a repository without `rollouts`. It exists only to plan a bind, so it follows the bind's rule, not "observe by membership". `image-release`'s dry run reads it only for the orchestrator, which is granted, and the sweep asks only for repositories its filter already granted, so neither sees the refusal today. *Claude's call.*

**The sweep.** `activation-sweep` runs under the dead-man switch. The builder reads how it reports a refused bind and makes the refusal a finding through the alarm it already sends, never only `/fail` with no reason.

`activation-sweep`'s client-side filter stays as a convenience. `CLAUDE.md`'s grant invariant gains release binding.

### Part 3: Records stay true

Correct each statement recon found false:

- `docs/operations/post-deploy-verification.md`: "verify dispatch automation remains disabled" (dispatch is live), the WS-5.3 closeout written as current procedure, and the secrets section written before the two BWS bearers.
- `docs/operations/release-immutability.md`: the deleted event layer described in the present tense. The builder checks each claim against the code before rewriting it.
- `docs/operations/credentials.md`: the Todoist token's consumers named as "tracker launchers", which ADR-0040 deleted. The token has no rotation registry row, so no rotation waits on it.
- `routes/reconciliation.py` docstrings and the `test_wsp21_invariant_scan.py` comment that describe a reconciliation runner, which ADR-0048 deleted.
- `docs/operations/rotation-executor.md`'s "reaches the dead-letter report": nothing polls that report. The alert it names, `rotation-unit-failed`, is the real home, so the sentence keeps only that.

History moves to the ADR or archive that superseded it and is never deleted.

### Part 4: Edges are clear

- A test holds `image_release`'s `REQUIRED_ROUTES` to the served route table.
- `tests/architecture/test_release_edges.py` states the layer's surface in one place: `image-release`'s routes and identities, the activation sweep's bind routes, the observer's one write, the two filtered readers of `GET /api/v1/observations`, and `attest_exit_criteria.py`'s host. A new caller of these routes has to be added there.

### Part 5: Rules into tests

The layer has three rules in `CLAUDE.md`: the `deployment_observation` summary models, the traceability observation hop, and `_resolve_pr`'s two anchors. Open each rule's test and confirm that it fails when the rule breaks. Add the grant rule from Part 2. Run a mutation pass over the enforced rules, report every survivor, and move history to the archive, as earlier layers did.

## Order

Part 2 first, because it's the one behavior change and its proof is the layer's deploy. Parts 1, 3, 4, and 5 follow in one plan, because none changes behavior.

## Live proof

| Part | Can prove live | No live proof until a real event | Needs Devon first |
|---|---|---|---|
| 1 | The alert text, read from the deployed lane | The text in a real stalled post-deploy alert | None |
| 2 | The layer's own deploy binds the orchestrator under the new check, and the next `activation-sweep` pass binds its repositories | A refused bind | Withdrawing `rollouts` from a real repository to see a refusal |
| 3 | Not applicable | Not applicable | None |
| 4 | The test is the proof | Not applicable | None |
| 5 | Mutation counts | Not applicable | None |

A refusal can be proven on `sds-canary` without touching a real project, if it holds a machine-local or release binding. The builder checks whether it does. If not, the refusal has tests only, and the report says so.

## Left to the builder

- Refusal code names, and the alert wording, with the next step in it.
