# Credential rotation increment 5: OpenRouter (build report)

ADR-0055 increment 5 rotated the first real credential, `openrouter-generic`, through the rotation
executor on production, with every gate clicked. Handoff:
`docs/superpowers/plans/2026-10-12-rotation-increment-5-openrouter-handoff.md`. Sessions on
2026-10-12 and 2026-10-13.

## Outcome

The old key (`1f9697cd`, 73 characters) is deleted at OpenRouter and refused at inference. The new
key (`018a8ca6`, 73 characters) is the keeper's value, with a $5 credit limit and an expiry of
2027-11-17, 400 days after minting. No staging or quarantine secret is left in `Rotation / Work`.
infraops' `cred-findings` reports nothing for the credential. Every fingerprint in this report is a
sha256 prefix; no value, label or key hash appears anywhere in the run's evidence or logs.

| Unit | Id | Steps | Criteria | Attempts |
|---|---|---|---|---|
| `openrouter-mint` | `a0c32a0d-fc65-595d-942c-2f183741172e` | mint, stage, verify_new, store, verify_consumers | AC-001 to AC-005, passed | 1 |
| `openrouter-retire` | `b7ca5217-5861-564f-b282-ebb6510ca93b` | verify_consumers, retire_old, confirm_dead | AC-006 to AC-008, passed | 2 |

The chain: change record 128, approved; intake revision `bc6b9a33-135f-4261-b766-f405a4d7aeab`
(package `rotation-openrouter-generic` revision 3, content hash `e85b188d…`, commit `2b7ac92`);
decomposition proposal `1a15b913-5c4a-4969-9081-8932524d8369`. Both units completed. The VM ran
`1457130eb3f5`.

## What shipped

| Repository | Pull request | What |
|---|---|---|
| infraops-mcp-server | #116 | The consumer inventory: the keeper is the only consumer |
| infraops-mcp-server | #117 | `rotate_requested = "2026-10-12"` |
| infraops-mcp-server | #118 | The new fingerprint; the request cleared |
| intent-packages | #121 | Revision 2, written for the executor |
| intent-packages | #122 | Revision 3: the shared project names (never approved as revision 2) |
| intent-packages | #123 | Devon's approval of revision 3 |
| orchestrator | #405 | The OpenRouter adapter, the contract's second probe, `admin_secrets`, ADR-0055 amendment 5 |
| orchestrator | #406 | The `httpx` logger set to WARNING: a key's request URL carries its hash |
| orchestrator | #407 | ADR-0055 amendment 6: shared rotation projects with direct secret grants |
| orchestrator | #408 | ADR-0055 amendment 7: the exposure-response factors |

No orchestrator service code changed, so production needed no deploy. No dependency was added:
`httpx` was already present.

On the Mac, outside any repository: the `~/.zshrc` export of `OPENROUTER_API_KEY` is replaced by a
comment (backup `~/.zshrc.bak-2026-10-12-openrouter`), octo's OpenRouter seat is disabled (backup
`~/.claude-octopus/.providers-config.bak-2026-10-12`), and the Keychain item `openrouter-api` /
`devon`, which held the old key, is deleted after the run.

## Devon's decisions

- **2026-10-12:** drop octo's seat and the shell export; retire today's key by machine; a $5 limit
  and a 400-day expiry; the session clicks the `/review` gates in his browser for this run.
- **2026-10-13:** shared rotation projects (`Rotation / Keepers` and `Rotation / Admin`) with direct
  secret grants, instead of a project per credential and per provider (amendment 6); two
  exposure-response factors added to decision 6's four (amendment 7); the session also approves the
  change record.

## Browser-click consent

Every human gate in this run was clicked by Claude Code in Devon's signed-in browser, under his
consent for this run only: change record 128 in change-manager, decomposition `1a15b913`, both
units' authority approvals, all eight adjudications and both completions. Each reason or rationale
says so. The ledger attributes them to `devon`, which is the attribution cost he accepted. Decision
10's default (Devon clicks) is unchanged. Devon approved revision 3 himself, by name, in his
terminal.

## The consumer inventory

Searched `~/Projects` and `~/Developer` for the secret's UUID and for `OPENROUTER_API_KEY`; shell
init files; LaunchAgents and their scripts; `~/.claude` and Claude Desktop configuration; production
Coolify (19 applications, 3 services, 439 environment rows) and dev Coolify (2 applications,
2 services, 63 rows), by key name only; both VPS filesystems. Found: the Keychain item, hash-equal
to the keeper, exported at `~/.zshrc:141`, read only by octo's OpenRouter seat, which logs show
never routed to OpenRouter. key-info showed zero usage on every measure.

Excluded, with the reason: `brain-app` and `brain-open` hold `OPENROUTER_API_KEY` rows whose values
hash-equal BWS `39055caf` (`BRAIN_OPENROUTER_API_KEY`), a different key; FacelessTT's OpenRouter
code was removed in `9d0b81b` and its `llm_roles.yaml` block is commented out; n8n-as-code is
upstream code whose extension isn't installed.

The keeper moved from `Ops / Platform` to `Rotation / Keepers` on 2026-10-13. That removed read
access for the seven Mac identities that reached it through `Ops / Platform`. The direct check of
each identity wasn't run: the session's permission prompt denied reading their tokens, and a secret
belongs to one project at a time.

## Verification

- **Mutation review: 38 hand mutations, all killed.** The first batch of 30 left five survivors:
  the delete's status unchecked, the minted key's shape unchecked, the minted key's expiry ignored,
  paid models asked, and model prices ignored. Each got a test. The expiry mutant survived once more,
  because a missing expiry already failed to parse; a test with a server that stores a different
  expiry killed it. The second batch of eight left one apparent survivor (the provider built after
  the claim), which was a malformed mutation; re-applied correctly, it was killed.
- **An independent adversarial review** found no leak; its robustness findings were fixed in #405.
- **`make check`:** 7,410 passed for #405, 7,411 for #406. CI passed on every pull request.
- **The run was checked from the VM independently of the executor's evidence**, by fingerprint and
  status code: the keeper, the staging and quarantine secrets after mint; the new key's limit and
  expiry through the management API; after retire, the old key's id answering 404 to the management
  lookup, the old key refused and the new one live at inference, and no work secret left.
- **The root's grants** were proven from the VM before the run: it lists `Rotation / Keepers` and
  `Rotation / Admin`, reads the management key (`GET /keys` 200, a malformed key 401), and creates,
  writes, reads back and deletes a canary secret in `Rotation / Keepers`. The first check failed
  because grants were missing in the console, which Devon fixed.

## What went wrong, and what it cost

- **confirm_dead failed retire's first attempt.** The old key was deleted at 15:38:33 UTC (the
  management lookup answers 404). confirm_dead probed three seconds later, and OpenRouter's
  completion endpoint still accepted the key; it was refused by 15:39:09, beside a malformed key
  refused throughout. key-info refused it within about 20 seconds, the reverse of amendment 1's
  measurement that key-info lags. The unit failed with `the old value probed live (200), expected
  dead`; it was requeued as SYSTEM, and attempt 2 found the key already refused, filed
  "nothing to revoke", proved it dead and deleted the work secrets. Cost: one of three attempts and
  a manual requeue. **Every OpenRouter rotation will fail retire's first attempt the same way** until
  the adapter or the contract tolerates the lag.
- **Revision 2 carried stale project names** after amendment 6. Devon's read of the diff caught it
  before he approved; revision 3 corrected three prose lines.
- **The root's new grants were incomplete** at first. The VM check caught it before anything ran.

## Proposed amendments

1. **Confirm-dead must tolerate the provider's propagation lag.** Either the adapter's `probe` for
   an expected-dead subject retries for a bounded time (about two minutes for OpenRouter, from this
   run's 30 seconds), or the contract treats "deleted but still live" as a reason to leave the unit
   for the next pass rather than fail it. The second needs a way to tell "not yet" from "never",
   which is the deadline primitive amendment 5 said the contract lacks. This is a design choice for
   Devon.
2. **security-drift isn't told about SDS rotations.** `record-rotation` writes `lastRotated` in the
   Mac's `~/.config/infra-drift/cred-rotation-state.json`, which the VM can't reach, so this run
   recorded it by hand after confirm_dead. Either the executor files something infraops reads, or the
   rotation proposer records it when it sees a completed rotation.
3. **A failed stage after a mint orphans a key.** The key carries the $5 limit and 400-day expiry,
   so the exposure is bounded, but nothing deletes it. The package's failure modes name it; the
   executor could delete a key it minted but never staged.
4. **The free-model probe depends on OpenRouter's `:free` catalogue.** With no `:free` model
   answering determinately, verify_new and confirm_dead stop as indeterminate. It didn't happen
   here.
5. **Change record 122** (revision 1, from 2026-10-07) is still `approved`. The work carrier refuses
   it each pass (`revision_mismatch`) and the proposer reports it `superseded`. It needs Devon's
   decision on how to close it.

## Prerequisites for increment 6

- Amendment 1 of this list, or every OpenRouter-style provider with a propagation lag costs a
  requeue per rotation.
- The first credential with a consumer account proves a direct secret grant with a canary and an
  empty `HOME` before its keeper moves (amendment 6).
- The rotation proposer is still unscheduled, so a rotation starts only when someone runs it.
