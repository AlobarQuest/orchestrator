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
