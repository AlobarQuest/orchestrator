# Increment 7 report: GitHub PATs, the Atlassian token and factory-runner's bearer

Handoff: `2026-10-19-handoff-increment-7-github-atlassian.md`. Build plan:
`2026-10-19-increment-7-build-plan.md`. Decisions and measurements: ADR-0055 amendment 10.
Nothing was rotated, no keeper moved, no package was written, and the VM wasn't reinstalled.

## What merged

| Repository | Pull request | What |
|---|---|---|
| orchestrator | #457 | The Actions-secret writer, the fingerprint read, and the `github-pat`, `bitbucket` and `orchestrator-distributed` adapters; ADR-0055 amendment 10; the runbook; CLAUDE.md |
| intent-packages | #134 | `secret-fingerprint.yml` |
| infraops-mcp-server | #124 | `secret-fingerprint.yml` (and a `.prettierignore` entry, to keep it byte-identical) |
| security-standards | #76 | `secret-fingerprint.yml` |
| change-manager | #122 | `secret-fingerprint.yml`; redeployed, serving `19ff770a` |
| brain | #98 | `secret-fingerprint.yml`; redeployed, every brain serving `2d608eb0` |
| project-standards | #50 | The portfolio scan fetches `FACTORY_PR_TOKEN` from BWS each run, with a Healthchecks check that fails when the fetch does |

## What waits, and on what

| Pull request | Waits on |
|---|---|
| orchestrator #455 (the factory-runner bearer's `entry`; drops two rows) | Devon's go-ahead: the executor reads the registry live |
| factory-runner #96 (drops two copies; registers the scan) | Devon's go-ahead, as above |
| infraops-mcp-server #125, draft (the mirror's tokens become `runtime-fetch`) | The mirror's cutover |
| github-to-bitbucket-mirror #2 (BWS fetch and a Healthchecks ping) | The mirror's BWS account, a Healthchecks check, and an App Brain landing determination (the repository has none, so it's un-landable) |
| The VM reinstall at `88f84075` | Devon's go-ahead. The installed executor knows none of the three new adapters |

**Healthchecks was at its 20-check free-plan limit.** Devon freed two slots on 2026-10-19: a check
outside the SDS, and `sds-production-current`, the only SDS check no registry row names (every
other one verifies a `runtime-fetch` reader). `portfolio-scan` and `github-to-bitbucket-mirror`
now exist, and the account is at 20 again. The revision watcher's condition report went with its
check: a stale application is still recorded in the `production_revision` observation each pass,
but nothing pages on it.

**After #96 and #455 merge**, delete the copies they drop:
`gh secret delete FACTORY_PR_TOKEN --repo AlobarQuest/project-standards`, the same for
`FACTORY_RUNNER_TOKEN` and `FACTORY_RUNNER_CREDENTIAL_KEY_ID` there, and
`gh secret delete FACTORY_PR_TOKEN --repo AlobarQuest/factory-runner`. Then remove the
`FACTORY_PR_TOKEN=` line from `~/.portfolio/credentials.env`, which the scan now ignores.

## After the report

Everything the "What waits" table held landed on 2026-10-19, outside the change window, with
Devon's go-ahead:

- **The checks.** Devon freed two slots. `portfolio-scan` (daily, 03:00 New York) and
  `github-to-bitbucket-mirror` (hourly) exist.
- **The mirror.** App Brain records its landing as `inert`: Coolify builds it from a deploy key,
  and it has no workflow and no webhook. #2 merged. Devon created its BWS account and the
  ping-URL secret (`59a7df60-…`, `Ops / Platform`). With the secret ids set, the first run pinged
  start and success. With `GITHUB_TOKEN` and `BITBUCKET_TOKEN` then deleted from its Coolify env,
  the second run succeeded on tokens from BWS alone.
- **infraops #125.** Its own executor classifies a `runtime-fetch` consumer as manual, so its
  test now expects a manual plan for both mirror credentials. The orchestrator's executor rotates
  them.
- **orchestrator #455 and factory-runner #96.** Both merged. The four copies they dropped are
  deleted, and so is the `FACTORY_PR_TOKEN=` line in `~/.portfolio/credentials.env`. Nothing read
  any of them: no workflow in either repository names the three secrets, and `factory-runner.yml`
  has never run in factory-runner itself.
- **The VM.** Reinstalled at `88f84075` with the App keys. The two ready units (change-manager's
  `observe` bearer and `orchestrator-observer`) touch no registry row #455 changed. The first pass
  on the new install skipped both with the change window's 409, as before, and logged no refusal.
  As the service user under `env -i`, the install read the App key, minted a read token and read
  brain's report end to end.

**orchestrator's report workflow was never registered.** GitHub ran nothing for the push of
`88f8407`, the merge of #457, not even `Quality`, though every later push triggered it. So
`secret-fingerprint.yml` sat on `main` unregistered, and the runs list for it answered 404. GitHub
registers a workflow only from a push that changes its file. A comment edit registered it, and a
second change restored the bytes the other five repositories hold. From the executor's side an
unregistered file looks like a stopped schedule: both refuse the unit for want of a report.

## The VM reinstall

Per `docs/operations/rotation-executor.md`, with `88f84075702c51b605e18ac566ece58568c2c838`, then add
to `/etc/rotation-worker/config.toml` and set `source_revision` to the first 12 characters:

```toml
github_app_id = "5383095"
github_app_key_id = "80c752aa-866c-43dc-a9e2-b4e80102d444"
```

The root already reads the key (checked from the VM on 2026-10-19, in `Rotation / Admin`).

## The inventory

| Credential | Keeper (project) | Copies | Read by |
|---|---|---|---|
| `factory-pr-token` | `a3240c2e…` (`SDS Operator`) | Actions secrets in orchestrator, intent-packages, infraops-mcp-server, security-standards, change-manager and brain; the portfolio scan (BWS each run, after #50) | The scan, with `sds-operator` |
| `orchestrator-factory-runner-bearer` | `d2a4c0fc…` (`Ops / Platform`) | Entry `factory-runner-github` in `ORCHESTRATOR_M2M_CREDENTIALS`; Actions secrets in the same six repositories | No runtime reader |
| `github-finegrained-mirror` | `c85e33ad…` (`Ops / Platform`) | The mirror app's `GITHUB_TOKEN` until its cutover, then a BWS fetch | The mirror's own BWS account, after the cutover |
| `bitbucket-mirror-token` | `1078dcaf…` (`Ops / Platform`) | The mirror app's `BITBUCKET_TOKEN`, as above | As above |
| `github-classic-aihelper`, `-lifeops` | Deleted | None. Revoked and confirmed dead by infraops' legacy executor on 2026-07-02 | — |

The four live keepers hold four different values. Every copy has a named owner.

## The rotation App

`alobar-sds-rotation`, App id 5383095, installation 173083952: Secrets write, Actions read and
Metadata read; no webhook; installed on brain, change-manager, infraops-mcp-server,
intent-packages, orchestrator and security-standards. Its key is in `Rotation / Admin`.

## Each adapter's plan

| Adapter | Credentials | Units (AC) | Window |
|---|---|---|---|
| `github-pat` | `factory-pr-token`, `github-finegrained-mirror` | human `mint` · `store` (verify_new 1, store 2, distribute 3, verify_consumers 4) · `retire` (verify_consumers 5, retire_old 6, confirm_dead 7) | Both may set `touches_no_hosted_service: true`. `retire` waits unclaimed while a dispatched run is live |
| `bitbucket` | `bitbucket-mirror-token` | human `mint` · `store` (1, 2) · `verify` (3) · human `revoke` · `confirm` (4) | All non-hosted. `verify` waits unclaimed until every reader has switched |
| `orchestrator-distributed` | `orchestrator-factory-runner-bearer` | `mint` (AC-001 to AC-007, with distribute) · `retire` (AC-008 to AC-010) | Both windowed |

## The grants the keeper moves need

- `factory-pr-token`: `sds-operator`, a direct read, for the portfolio scan.
- `github-finegrained-mirror` and `bitbucket-mirror-token`: the mirror's new BWS machine account,
  a direct read on each, and on the ping-URL secret it reads.
- `orchestrator-factory-runner-bearer`: none beyond the root. It has no runtime reader.

## Verification

- `make check` on the merged change: 7,874 passed. CI passed on every pull request.
- Hand mutation: 47 mutants, 46 killed. The one survivor was equivalent (a `TRANSIENT` entry no
  path could reach) and the dead code was removed. The first batch's other survivors each got a
  test and were then killed.
- `/code-review` found ten issues. Seven were fixed. Two were declined, with reasons in #457: a
  missing report refusing before the claim is the existing rule for every scheduled reader, and an
  Actions secret isn't a hosted service. One is recorded below.

## Found and not fixed

- **Atlassian may challenge an account after failed authentications.** Every Bitbucket probe,
  the known-bad control included, sends failing Basic auth with the real account's email.
  Unmeasured.
- **A scheduled workflow stops after 60 days without repository activity.** The executor then
  refuses the unit, which spends an attempt. Re-enable `Secret fingerprint` in the repository's
  **Actions** tab before a rotation if it has stopped. brain and security-standards are the quiet
  ones.
- **`~/.codex/rules/default.rules`** falls back to the revoked aihelper PAT when `CODEX_GH_PAT` is
  missing. That fallback just fails now.
- **project-standards' `portfolio sds-ready`** still tells a person to load `FACTORY_PR_TOKEN` from
  `credentials.env` (`README.md`, `factory_checks.py`, `dispatch_app.py`).
