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

- [ ] (P1) Rotate the production orchestrator-postgres password — exposed in a Claude session transcript 2026-07-09 (internal-Coolify-network-only exposure); rotate DB password, update ORCHESTRATOR_DATABASE_URL in Coolify, redeploy, verify /health/ready — added 2026-07-09
- [ ] (P2) `is_expansion()` (kernel/authority.py) has zero call sites in src/ — verify the CLAUDE.md invariant "authority-expanding standing-context updates require a named human approval" is actually wired to it, or remove the dead function — added 2026-07-09
- [ ] (P2) Work units created via the direct path (`POST /revisions/{id}/work-units`) are not stamped with `constraints.work_unit_id`, so factory-runner will reject them. Either stamp there too or document the path as non-dispatchable — added 2026-07-09
- [ ] (P2) Provide a helper that computes `authority.conformance` from real repo state (`security_scan.cli.scan` + `portfolio.compliance.build_rows`, both importable and local-only) so decomposition authors do not hand-type the claim — added 2026-07-09
- [ ] (P3) Tighten the dispatch conformance gate: the `standards_touched ⊆ accepted_standards` branch is a tautology if a producer ever echoes `accepted = touched`. Consider requiring `status == "green"` and treating acceptance as evidence rather than a bypass — added 2026-07-09

- [ ] (P1) Attestation audit: enumerate every place the system ATTESTS rather than REFUSES, and make each fail closed. The refusal layers held during the WS-6.4 canary (authority tool scoping denied WebSearch; the change-detection guard refused a hollow PR; decomposition_already_approved refused a silent repair; the kill switch blocked dispatch). The attestation layers did not: `make check` exits 0 having verified nothing, pytest exit 5 is swallowed, finalize records a literal 'passed'. A false attestation is not caught by the gates above it — it is trusted by them. Conformance already has this rule (accepted_standards must never echo standards_touched) precisely because someone saw the tautology coming; generalize it. Evidence: ~/docs/software-delivery-system/2026-07-10-ws64-revision2-envelope-evidence.md — added 2026-07-10
- [ ] (2) Split FACTORY_RUNNER_TOKEN into per-repo worker credentials. One shared credential is currently written into 6 repos' Actions secrets (WS-6.4a, Devon's call for the MVP), so revoking one repo's access means rotating all six. Same agent_id, distinct credential_key_ids. — added 2026-07-10
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
