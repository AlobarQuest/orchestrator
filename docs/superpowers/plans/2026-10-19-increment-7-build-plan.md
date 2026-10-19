# Increment 7 build plan: the Actions-secret writer, the PAT and Atlassian adapters

Handoff: `2026-10-19-handoff-increment-7-github-atlassian.md`. Devon's decisions of 2026-10-19 are
recorded in the ADR-0055 amendment this build adds; this page is the build order.

## Decisions this plan carries out

- **Actions-secret copies are verified by machine, not attested.** Each holding repository runs
  `.github/workflows/secret-fingerprint.yml` hourly. It has no permissions and prints, for each
  registered secret name, `fingerprint <NAME> <sha256 prefix 8> <length>`, hashing the exact bytes
  (`printf '%s'`). The executor reads the latest successful run's job log with an App token minted
  `{actions: read, metadata: read}` for that repository, and counts the copy switched when the
  reported fingerprint equals the keeper's. No successful run in the last 3 hours is a `ReadError`.
- **The executor writes Actions secrets** (`_distribute`) through the rotation App
  `alobar-sds-rotation` (App id 5383095, installation 173083952, key in `Rotation / Admin`,
  `80c752aa-866c-43dc-a9e2-b4e80102d444`). Each write mints a token for the destination's one
  repository with exactly `{secrets: write, metadata: read}`, refuses unless the mint response's
  `permissions` equal that, seals with PyNaCl, and confirms `updated_at` moved.
- **The mirror fetches both tokens from BWS each cycle** (option 1a) with its own BWS machine
  account and pings a Healthchecks check; its Coolify copies go away.
- **The portfolio scan fetches `FACTORY_PR_TOKEN` from BWS each run** (option 2a) through
  `sds-operator`, with a Healthchecks check that fails when the fetch fails.
- Copies with no reader (project-standards' three secrets, factory-runner's `FACTORY_PR_TOKEN`)
  and the factory-validation-kit `runtime-fetch` row are deleted. The classic PATs stay as they are.
- The App key is `Rotation / Admin`; ADR-0055's asset row moves there.

## Plans

| Adapter | Credentials | Segments (AC) | Window |
|---|---|---|---|
| `github-pat` | `factory-pr-token`, `github-finegrained-mirror` | human `mint` (mint, stage) · `store` (verify_new 1, store 2, distribute 3, verify_consumers 4) · `retire` (verify_consumers 5, retire_old 6, confirm_dead 7) | both `touches_no_hosted_service: true`; retire refuses while a dispatched run is live |
| `bitbucket` | `bitbucket-mirror-token` | human `mint` · `store` (verify_new 1, store 2) · `verify` (verify_consumers 3) · human `revoke` (retire_old) · `confirm` (confirm_dead 4) | all non-hosted; `verify` waits unclaimed like a retire |
| `orchestrator-distributed` | `orchestrator-factory-runner-bearer` | `mint` (mint 1, stage 2, host 3, verify_new 4, store 5, distribute 6, verify_consumers 7) · `retire` (verify_consumers 8, retire_old 9, confirm_dead 10) | both windowed |

The `orchestrator` plan is unchanged: approved packages pin its AC-001 to AC-009.

## Pull requests

| Repository | Change | Merge |
|---|---|---|
| orchestrator | executor, registry rows, fingerprint workflow (+ ws33 allowlist), ADR amendment, runbook | code: on green; registry: Devon's go-ahead |
| factory-runner | registry rows | Devon's go-ahead |
| infraops-mcp-server | fingerprint workflow; mirror registry rows after the mirror cutover | registry: Devon's go-ahead |
| intent-packages, security-standards, change-manager, brain | fingerprint workflow | on green, after reading App Brain's landing route |
| github-to-bitbucket-mirror | BWS fetch per cycle + Healthchecks ping, env fallback kept until cutover | before cutover |
| project-standards (+ security-standards governance) | scan fetch + check, manifest | on green |

## Devon's console acts (one session, after the code is ready)

The mirror's BWS machine account with direct reads on its two keepers and its ping-URL secret;
its access token pasted into the mirror's Coolify env.
