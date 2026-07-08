# WS-5.3 Post-Deploy Verification Evidence

Date: 2026-07-08

## Scope

WS-5.3 adds bounded post-deploy verification work generation to the
orchestrator. It records normalized deployment observations against immutable
WS-5.2 release artifact bindings, creates generated post-deploy verification
work units, records bounded evidence/events, and verifies those generated units
through the WS-5.1 verifier and existing lifecycle completion guards.

This implementation did not deploy production, mutate infrastructure, enable
dispatch automation, merge a PR, add tracker canonicalization, promote brain
knowledge, or add secrets.

## Code

- Repository: `/Users/devon/Projects/orchestrator`
- Worktree: `/Users/devon/Projects/orchestrator/.worktrees/ws53-post-deploy-verification`
- Branch: `codex/ws53-post-deploy-verification`
- Baseline: `4c419de` (`main` after WS-5.2 merge)
- Design commit: `7ec444c`
- Plan commit: `377c682`
- Red-test commit: `cf04cd5`
- Implementation commits:
  - `8580162` (`feat: record deployment observations`)
  - `e06a9e4` (`feat: expose deployment observation API`)
  - final review hardening commit on this branch

## Implementation Summary

- Added `deployment_observations` persistence through Alembic migration
  `0011_ws53_deploy_obs`.
- Added:
  - `POST /api/v1/release-artifacts/{binding_id}/deployment-observations`
  - `GET /api/v1/release-artifacts/{binding_id}/deployment-observations`
- Added a system-only deployment observation service that:
  - requires an existing release artifact binding;
  - requires the release binding's implementation work unit to be completed;
  - requires the observed digest to exactly match the immutable release binding
    digest;
  - stores bounded probe, route, auth, dispatch posture, and status facts;
  - rejects secret-shaped keys or values;
  - creates at most one generated post-deploy work unit per binding/environment;
  - rejects changed facts for the same binding/environment;
  - replays identical observations idempotently.
- Added generated post-deploy verifier criteria for:
  - artifact digest match;
  - health probes;
  - route presence;
  - M2M auth behavior;
  - dispatch-disabled posture.
- Extended deterministic verifier evaluators for bounded post-deploy evidence
  types.
- Extended lifecycle/adjudication guards narrowly so generated post-deploy ACs
  satisfy completion only for the generated unit linked by a deployment
  observation.
- Restricted generated post-deploy adjudications to the verifier command path so
  public adjudication cannot bypass deterministic post-deploy evaluation.
- Tightened observation normalization and validation for canonical HTTPS URLs,
  bounded fact size, allowed fact keys, probe/route cardinality, and status-code
  ranges.
- Added event-publication mappings for:
  - `deployment.observed`
  - `post_deploy_verification.created`
- Added operations documentation:
  `docs/operations/post-deploy-verification.md`.

## Verification

- `SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check`
  passed:
  - ruff check passed;
  - ruff format check passed;
  - pyright reported `0 errors, 0 warnings, 0 informations`;
  - pytest passed `758` tests.
- Security scanner:
  - command:
    `PYTHONPATH="$HOME/Projects/security-standards/src" .venv/bin/python -m security_scan.cli . --category security`
  - result: `0 BLOCK`, `0 WARN`, `1 INFO`.
- `git diff --check` passed.
- `/Users/devon/Developer/code-standards/.venv/bin/code-standards check`
  returned success.

## Boundaries Preserved

WS-5.3 did not implement:

- automatic merge;
- automatic deployment;
- production infrastructure mutation;
- dispatch automation enablement;
- tracker canonicalization;
- brain learning, promotion, or graduation automation;
- a new credential, env file, BWS manifest entry, GitHub Actions secret, merge
  authority, or deploy authority.

The original implementation work unit is not mutated by deployment observation.
The generated post-deploy verification unit can reach `completed` only through
the WS-5.1 verifier and existing lifecycle completion guards.

## Production Status

Production was not deployed during implementation. At the start of the session,
production `https://sds.alobar.net` was healthy but still lacked WS-5.1 and
WS-5.2 routes. Phase 5 closeout still needs a Devon-approved production deploy
of merged `main` containing WS-5.1, WS-5.2, and WS-5.3.
