---
name: orchestrator
tier: active
status: active
purpose: Canonical work-unit lifecycle control plane for the software factory.
version: 0.1.0
version_source: pyproject
updated: '2026-07-08'
foundation: true
foundation_contract: 1
applicable_standards:
  project: '1.0'
  security: '1.0'
  code: '1.0'
required_checks:
- id: quality
  executor: github-actions:quality.yml
---

## Backlog

- [ ] (P2) Clean up 4 stale `bump-dependencies-*` work units in production (change-manager `3a650c23`, infraops-mcp-server `69e63f39`, security-standards `ddda84ac`, brain `4afb7207`), all in `ready` state from the original 2026-07-09 fanout, superseded by the rev-4..9 re-proofs. Each has `authority_approval_id: None`, `mutation_commands: None`, and the old `allowed_commands: ['make check']` — so they fail dispatch admission closed and cannot call GitHub, but they are debris that could be mistaken for a corrected replacement. **CONFIRMED 2026-07-23: there is NO cancel path for a never-claimed `ready` unit — `(READY, CANCELLED)` is not a legal edge for any role (`kernel/transitions.py`), verified empirically (`POST /commands/cancel` → HTTP 409 `invalid_transition`, "ready -> cancelled is not legal"). `CANCELLED` is reachable only from `claimed`/`executing`/`awaiting_approval`/`failed` (all HUMAN); the only edge out of `ready` is `ready → claimed` (SYSTEM), and no command drives it. This is a lifecycle gap: a mis-authored, never-claimed `ready` unit is un-retireable.** Proper fix: add a `(READY, CANCELLED)` HUMAN edge (guard + `tests/` + `make check` + `/code-review` + deploy), then cancel the four via `/review`. Until then they sit inert. Found 2026-07-22 during the AC-003 dispatch; cancel-path gap confirmed 2026-07-23 during the AC-004/005 closeout. — added 2026-07-22
- [ ] (P2) Silence is never approval: `awaiting_approval` / `awaiting_review` do not define what happens when a human never answers — a unit can sit forever with nobody told. Fail-closed timeout semantics are currently UNSPECIFIED. **Scoped into WS-P2.15 (fail-closed lifecycle guards), Wave 1** — added 2026-07-12
- [x] (P1) A worker that never reports its PR binding is INVISIBLE to conflict detection: no binding -> the runner has no PR to poll -> no observation -> no condition, and not even a counted `skipped_correlation`. It also breaks the `work unit ↔ PR` link that the Wave-2 Evidence Pack (WS-P2.5) and traceability query (WS-P2.6) are built on. Fix fail-closed, not with a new detector: a unit whose authority includes PR-creating capability must not reach SUBMITTED without a `unit_pr_binding` row. **Scoped into WS-P2.15 (fail-closed lifecycle guards), Wave 1** — added 2026-07-12; DONE 2026-07-23 (WS-P2.16 U3: attempt-scoped EXECUTING→SUBMITTED binding guard, deployed on 4cfa0c8-wsp216-amd64)
- [ ] (P1) Rotate the production orchestrator-postgres password — exposed in a Claude session transcript 2026-07-09 (internal-Coolify-network-only exposure); rotate DB password, update ORCHESTRATOR_DATABASE_URL in Coolify, redeploy, verify /health/ready — added 2026-07-09
- [x] (P2) `is_expansion()` (kernel/authority.py) has zero call sites in src/ — verify the CLAUDE.md invariant "authority-expanding standing-context updates require a named human approval" is actually wired to it, or remove the dead function. A guard nobody calls is worse than no guard: it reads as protection. **Scoped into WS-P2.15 (fail-closed lifecycle guards), Wave 1** — added 2026-07-09; DONE 2026-07-12 (WS-P2.15): deleted. The invariant IS enforced, by `classify_context_update()` — but that checks standing-context capability *sets*, NOT envelope budgets or capability levels, so envelope expansion now has no detector. Safe only because the envelope is write-once, which `tests/architecture/test_authority_write_once.py` now enforces — a future budget-raising path trips it and must ship a fail-closed check.
- [ ] (P2) Work units created via the direct path (`POST /revisions/{id}/work-units`) are not stamped with `constraints.work_unit_id`, so factory-runner will reject them. Either stamp there too or document the path as non-dispatchable — added 2026-07-09
- [ ] (P2) Provide a helper that computes `authority.conformance` from real repo state (`security_scan.cli.scan` + `portfolio.compliance.build_rows`, both importable and local-only) so decomposition authors do not hand-type the claim — added 2026-07-09
- [ ] (P3) Tighten the dispatch conformance gate: the `standards_touched ⊆ accepted_standards` branch is a tautology if a producer ever echoes `accepted = touched`. Consider requiring `status == "green"` and treating acceptance as evidence rather than a bypass — added 2026-07-09

- [ ] (P1) Attestation audit: enumerate every place the system ATTESTS rather than REFUSES, and make each fail closed. The refusal layers held during the WS-6.4 canary (authority tool scoping denied WebSearch; the change-detection guard refused a hollow PR; decomposition_already_approved refused a silent repair; the kill switch blocked dispatch). The attestation layers did not: `make check` exits 0 having verified nothing, pytest exit 5 is swallowed, finalize records a literal 'passed'. A false attestation is not caught by the gates above it — it is trusted by them. Conformance already has this rule (accepted_standards must never echo standards_touched) precisely because someone saw the tautology coming; generalize it. Evidence: ~/docs/software-delivery-system/2026-07-10-ws64-revision2-envelope-evidence.md — added 2026-07-10
- [ ] (2) Split FACTORY_RUNNER_TOKEN into per-repo worker credentials. One shared credential is currently written into 6 repos' Actions secrets (WS-6.4a, Devon's call for the MVP), so revoking one repo's access means rotating all six. Same agent_id, distinct credential_key_ids. — added 2026-07-10
- [ ] (P1) Human-judgment -> passed-adjudication for judgment_required ACs isn't wired to the /review UI. Completion needs a satisfying adjudication for every required AC, but for a judgment_required AC the verifier records none, _authorize_outcome lets only the VERIFIER role record outcome=passed (a human can only 'waive', which needs failed_evidence_id+risk+follow_up), and /review exposes only approval/review/cancel/retry forms — no adjudication form. So a human 'pass' must be recorded out-of-band via the verifier M2M credential. Fix: add a /review adjudication form with a proper human-reviewer authorization model. Surfaced by WS-6.4 canary AC-001. — added 2026-07-10
- [ ] (P3) Parameterize _build_intake_payload with a loader function so intake-protocol-fixture also uses it (DRY the third inlined {**loader(...), idempotency_key, expected_version:0} site in src/orchestrator/cli.py). Pre-existing, surfaced by the PR #45 code review. — added 2026-07-11
- [x] (P1) WS-P2.16 — PR-binding chain: enforce unit-envelope capability vocabulary at ingress, make factory-runner POST its PR binding before submit, then add the attempt-scoped EXECUTING→SUBMITTED binding guard; plus a self-discovering cross-boundary vocabulary detector. Deploy order is mandatory (runner before guard) or the factory halts. U2 (factory-runner) is HAND-BUILT — factory-runner has no factory-runner-pilot.yml, so it cannot be dispatched. Wave 2 (WS-P2.5/P2.6) depends on this link. Plan: docs/superpowers/plans/2026-07-12-wsp216-pr-binding-chain.md — added 2026-07-12; DONE 2026-07-23 (built as plain PRs U1+U3+U4+U5 — factory-runner Step 2 was already merged; each independently adversarially reviewed; PRs #62+#66 merged; deployed image 4cfa0c8-wsp216-amd64, migration 0015, pre-flight cleared. Wave 2 UNBLOCKED. Closeout: ~/docs/software-delivery-system/2026-07-23-wsp216-closeout-evidence.md)
- [ ] (P2) Decomposition proposal API: ac_mappings[].ac_id and retained_acs[].ac_id expect the acceptance criterion's database UUID, not the human ac_id string ("AC-001") — decomposition.py:125 builds criteria_by_id keyed on str(criterion.id). The field name and the expected value disagree, and the failure is a generic package_acceptance_criterion_not_found with no hint. Either accept the human ac_id (it is unique per revision — there is already a UniqueConstraint on (work_package_revision_id, ac_id)) or rename the field to ac_uuid — added 2026-07-12
- [ ] (P2) Pin WorkUnit.authority as NOT mutation-tracked (a one-line test asserting the column is plain JSONB, not MutableDict.as_mutable(JSONB)). The write-once guard's flag_modified rule works precisely BECAUSE the column is untracked — without it the ORM never notices an in-place dict mutation, so flag_modified's presence is the tell. If anyone wraps the column in MutableDict, alias mutations persist WITHOUT flag_modified and slip the guard. Last hole in tests/architecture/test_authority_write_once.py; found by the WS-P2.15 independent verifier — added 2026-07-12 — added 2026-07-12
- [ ] (P1) Deterministic evaluation of automated ACs. Intent packages declare evidence_type 'automated_test' but the verifier's DETERMINISTIC_TYPES/JUDGMENT_TYPES contain neither it nor any of the other four legal package types, so evaluate_criterion falls through to judgment_required for EVERY automated AC — the root of the 'judgment_required ACs must be passed out-of-band via the verifier M2M credential' gap (a vocabulary gap, not a UI gap). ⚠ **DO NOT "just add automated_test to DETERMINISTIC_TYPES" — that HALTS THE FACTORY, and this item previously said to.** Four adversarial reviews established why: (a) the runner nests exit_code inside verification[] while _status_result reads only top-level fields → failed_closed; (b) the runner writes exactly ONE evidence row per unit (_first_ac_id, factory-runner cli.py:219) while a unit maps to N ACs and the verifier looks evidence up per-AC → ACs #2..N hit `evidence is None` → failed_closed → REVISION_REQUIRED → loop → FAILED; (c) exit_code is a hardcoded 0 (cli.py:486) and _run_command raises on nonzero, so any exit_code-based predicate is constant-true — a fail-open that would auto-pass "the tests pass" on evidence that `uv sync` ran. A correct fix needs: factory-runner writing one evidence row PER MAPPED AC, the verifier keying on the EVIDENCE ROW's evidence_type rather than the criterion's, and a command-aware evaluator. That is a workstream, not a patch. WS-P2.16 ships only the behavior-preserving half (declare the five package types, validate the CRITERION's evidence_type at intake — schemas.py:698, NOT the evidence-row types at :75/:857). Plan: docs/superpowers/plans/2026-07-12-wsp216-pr-binding-chain.md §3.5 — added 2026-07-12; STILL OPEN: WS-P2.16 U4 shipped the safe half (vocabulary declared + intake validation + Assertion D, deployed 2026-07-23); this item is now scoped to the DEFERRED deterministic evaluator only (per-AC runner evidence + evidence-row-type keying + command-aware evaluator)
- [ ] (P2) Evidence-row evidence_type is uncorrelated with the criterion it discharges. services/evidence.py accepts a free evidence_type from the writer (factory-runner hardcodes 'runner.pr.opened'; the verifier writes 'verifier.finding'), but evaluate_criterion keys ONLY on criterion.evidence_type — so an evidence row can carry any type at all and nothing checks it against the criterion it is discharging. A fourth free-string field on the same conceptual vocabulary. This is the blind spot WS-P2.16's vocabulary detector explicitly cannot see, and it is a prerequisite for the deterministic-evaluator workstream above — added 2026-07-12
- [ ] (P2) Capture per-attempt actual llm_calls/token consumption so the cost SLO and WS-P2.4 budget enforcement become computable — added 2026-07-23. Plan: docs/superpowers/specs/2026-07-23-wsp22-cost-actuals-capture-proposal.md
## Future plans

## Known Non-obvious Invariants

- Generic authority approvals satisfy work-unit readiness only. Authority-expanding
  standing-context updates require a named human approval bound to the exact
  standing-context fingerprint.
- Protocol smoke tests may manipulate time or lease expiry as deterministic fixture
  setup. Runtime recovery behavior itself must go through public API/CLI surfaces,
  not private service shortcuts.
- For decomposed units, completion is evaluated only against the approved unit AC
  mapping. Extra package-level adjudications recorded on that unit are ignored by
  the completion guard rather than blocking completion.

## WS-3.1 verification

Persistent orchestrator core is merged and closed. Orchestrator PR #1 merged at
`1ca7090079999dc25441cb0d1066b920b828e271`; intent-package closure PR #9
merged at `473de819ed31a2ab5beadde54dd03c7c71b4c178`. The closed package is
`ws-3.1-orchestrator-core` revision 1 with hash
`4414eae543d9dac8b1983f796593569d9abf97dfee1b8a06ef29b308e7b8337b`.

## WS-3.2 verification

Package intake and decomposition are merged and closed. Orchestrator PR #6
merged at `dd0e3f0deecd12e904b30cb29bfcfc57fb8fd688`; orchestrator
documentation PR #7 merged at `2a73b794665503240e58d12e3df55a8384bbec55`;
intent-package closure PR #10 merged at
`a48a72e10152b08739b3b83d1fba996c203d2f10`. The closed package is
`ws-3.2-package-intake-decomposition` revision 1 with hash
`84c929bc0860b6a585a62ec02fa35d9cdf89fce84773660aea1e383d955689df`.

## WS-3.3 verification

Runtime protocol semantics are merged and closed. Orchestrator PR #9 merged at
`183cbd945ad0dbe871661252cd313d84fd737f22`; intent-package closure PR #11
merged at `61550f21f59b4f70c4f03205e15415bf97cd87fd`. The closed package is
`ws-3.3-protocol-smoke-runtime-semantics` revision 1 with hash
`7829f22bfa30630a906d75131c84bc018c5dac3ceac7b933b7c9b46d23e5047a`.

## Phase 4 bootstrap verification

Option A for the runner reachability gap is approved: deploy the orchestrator to
production before WS-4.1 factory-runner work. Governing package
`orchestrator-production-deploy` revision 1 is approved with hash
`2f6bc7da07aa00106cb6008fc8a85878e001652f6ec645bf25a37760d84c2e7d`.

Repository deploy-prep is merged. Orchestrator PR #14 merged at
`22ce0a9fd4183df1794f0155ec4bd4ba6e4a83b5`; local `main` is clean at that
commit. Verification after merge: `make check` passed with 673 tests; security
scan reported `0 BLOCK`, `0 WARN`, and one judgment-only BWS least-privilege
INFO.

Local dogfooding through the orchestrator completed `deploy-plan`,
`repo-deploy-prep`, and `backup-coverage`. The `backup-coverage` dependency was
satisfied by Devon's explicit uncommon bootstrap waiver only to unblock
infrastructure creation; physical `vps-backup` manifest and restore verification
must still be proven after the production DB exists and before real production
orchestrator data is accepted. `infra-mutation` then ran in a fresh
infrastructure-only session through the change-manager/infraops lane.

`infra-mutation` is complete in the local `orchestrator_runtime` database at
version 11. Production Coolify app `orchestrator` serves `https://sds.alobar.net`;
Alembic is at `0007_work_unit_authority`; health checks pass; the human
surface is protected by Alobar ID forward-auth; M2M auth rejects missing/invalid
credentials and accepted only the configured bootstrap smoke credential during
verification. The bootstrap smoke token was deleted after the test.

The production orchestrator database is now covered by `vps-backup`. The
`vps-backup` repo commit `8ed7586` adds the `orchestrator` dump and verification;
the manifest includes the Coolify Postgres resource and `./verify-backup.sh`
passed with a valid `orchestrator.sql.gz` dump. The durable GitHub-runner M2M
credential was not created in this infra-mutation session; that belongs to
WS-4.1 factory-runner credential rollout through BWS/Coolify-managed secret
references.

## WS-4.1 factory-runner verification

Factory-runner is merged and pilot-ready. `AlobarQuest/factory-runner` PR #1
merged at local merge commit `f0e796f`; `AlobarQuest/orchestrator` PR #16 merged
at local merge commit `03cce5c`. The orchestrator pilot workflow is present but
has not been dispatched.

The durable GitHub-hosted runner M2M credential is active through
BWS/GitHub/Coolify-managed references only. The credential key ID is
`factory-runner-github`; the BWS secret UUID is
`d2a4c0fc-128b-4bf5-8e25-b481010e1be0`; production stores only the token hash in
`ORCHESTRATOR_M2M_CREDENTIALS`.

Production was repaired after WS-4.1 closeout with
`ghcr.io/alobarquest/orchestrator:03cce5c-ws41-closeout`, including the
`factory-runner` registry actor from `security-standards` commit `972c64a`.
Post-rollout smoke checks showed `/health/live` and `/health/ready` returning
200, missing M2M auth returning 401, and the configured key ID plus BWS-backed
bearer returning 200. Devon's merge gate remains permanent; no automatic merge
behavior was added.

## WS-4.2 dispatch-adapter verification

Dispatch adapter implementation is complete locally on branch
`codex/ws42-dispatch-adapter` and pending PR merge. It adds
`dispatch_records`, `POST /api/v1/work-units/{unit_id}/dispatch`,
disabled-by-default runtime dispatch settings, fail-closed admission, idempotent
GitHub Actions `workflow_dispatch`, repeated-failure circuit breaking,
conformance admission, human-gate age-out evidence, and dispatch outcome
events.

Runtime dispatch remains disabled unless explicitly configured through approved
secret/config rollout. No production config was mutated during implementation.
Devon's merge gate remains permanent; no worker or dispatcher may merge PRs.

Verification at implementation closeout:

- `SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check`
  passed with 698 tests.
- Security scan reported `0 BLOCK`, `0 WARN`, and one judgment-only BWS
  least-privilege INFO.
- `cd /Users/devon/Projects/project-standards && uv run portfolio foundation`
  reported `violations=0 accepted=0 unknown=0`.
- Production `/health/live` and `/health/ready` returned 200.
- Missing M2M auth returned 401.
- Configured WS-4.1 M2M auth returned 200 without printing secret values.
