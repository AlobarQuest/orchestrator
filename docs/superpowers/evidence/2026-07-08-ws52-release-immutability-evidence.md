# WS-5.2 Release-Immutability Evidence

Date: 2026-07-08

## Scope

WS-5.2 adds the release immutability layer to the orchestrator. It records
immutable artifact lineage after Devon merges implementation PRs and a build
produces a digest-addressed artifact.

The work is published in draft `AlobarQuest/orchestrator` PR #21 from branch
`codex/ws52-release-immutability`.

## Code

- Repository: `/Users/devon/Projects/orchestrator`
- Worktree used: `/Users/devon/Projects/orchestrator/.worktrees/ws52-release-immutability`
- Branch: `codex/ws52-release-immutability`
- Implementation commit: `56ac297d5b45aa78262f3a6f9fb120b7939b4e95`
- Baseline merge commit: `a04d0947ee07e9ad7a409fa93a894c779c28c332`
- PR: `https://github.com/AlobarQuest/orchestrator/pull/21`

## Implementation Summary

- Added `release_artifact_bindings` persistence and Alembic migration
  `0010_ws52_release_artifacts`.
- Added:
  - `POST /api/v1/work-units/{unit_id}/release-artifacts`
  - `GET /api/v1/work-units/{unit_id}/release-artifacts`
- Release bindings require known completed work units, matching approved package
  revision hashes, source/merge commits, and immutable artifact `sha256:` digests.
- Accepted bindings create one queryable binding row, one
  `release.artifact_bound` evidence row, and one local `release_artifact.bound`
  event.
- Event publication maps `release_artifact.bound` to a bounded factory event. The
  local `system` actor maps to external `unknown` while preserving `raw_actor_id`.
- Replay is idempotent for identical binding facts.
- Conflicting attempts to bind the same package/work-unit/source/artifact tuple to
  a different digest are rejected.
- Secret-shaped release metadata is rejected.

## Verification

- `SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check`
  passed with 735 tests.
- Security scanner:
  - `0 BLOCK`
  - `0 WARN`
  - `1 INFO`
- `git diff --check` passed.
- `/Users/devon/Developer/code-standards/.venv/bin/code-standards check` passed.

## Boundaries Preserved

WS-5.2 did not implement:

- WS-5.3 post-deploy verification unit creation;
- automatic merge;
- automatic deployment;
- production infrastructure mutation;
- lifecycle completion or verifier bypass;
- GitHub Actions dispatch changes;
- local-heavy runtime changes;
- change-manager or infraops behavior;
- tracker lifecycle truth;
- brain learning or promotion;
- graduation automation;
- new secrets, BWS manifest entries, runtime env files, or workflow credentials.
