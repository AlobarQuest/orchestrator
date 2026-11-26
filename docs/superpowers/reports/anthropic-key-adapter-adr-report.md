Outcome: done

# Report: ADR-0055 Amendment 36, a rotation adapter for Anthropic API keys (docs)

Worker W-320, branch `hq/anthropic-key-adapter-adr`, based on orchestrator `origin/main` at
`9caad335`. Docs only. It made no live call and no production act, and wrote no code.

## What I built

I appended ADR-0055 Amendment 36 (Proposed) to
`docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`, in the shape of
Amendments 33 and 35. It plans the `anthropic-api-key` class's adapter for its one row,
`anthropic-change-window-key`. The row at `9caad335` (`.cred-consumers.toml`, lines 1828-1852)
matches the handoff's description, so there was no stop point.

### The amendment's items

1. **The type.** `anthropic-key`, `Capabilities(machine_mint=False, machine_revoke=False)`, which
   is Bitbucket's derived plan: Devon mints in the Console into the inbox, the machine stores and
   verifies, Devon deletes, and the machine confirms. The type applies by class, so it also types
   factory-runner's shared `anthropic-api-key` row (Actions secrets, `Ops / Platform`, incomplete
   by design). `check_row` refuses that row, so it stays by hand.
2. **The probe and control.** `GET https://api.anthropic.com/v1/models` with `anthropic-version`.
   Live is exactly 200. The control is a random value of the key's shape, described in words, and
   `dead_statuses` is `{401}`. Exit check 1 records the control's measured status.
3. **The identity check.** Both values answer 200, and each response's `anthropic-workspace-id`
   header equals the row's declared `workspace_id`. The docs document this header for any
   authenticated API response, but not specifically for `/v1/models`. A missing header refuses
   the bind, and exit check 1 measures the header on the current key. I state that the inbox's
   other-keeper check compares against nothing with one row of the type.
4. **The consumers' proof and R10.** The existing scheduled `runtime-fetch` route. The read is
   fatal (`run-change-window.sh`, lines 132-134), so a green `--submit` pass read the new key. A
   **carried gap** remains: a green ping doesn't prove Anthropic accepted the key in use. The
   pinned infraops bundle turns a failed agent call into that item's `failed` outcome and exits 0,
   and a window with no item makes no call. It's not a revoke risk, because the reader keeps no
   value.
5. **The revoke and confirm-dead.** Devon uses the Console's Delete, not Disable, which is
   reversible. `confirm_dead` passes on 401 beside the control, and the deleted key's status is
   unmeasured (item 11). The adapter sets `revoke_may_come_first`. A **carried gap** remains: a
   stray key made by hand is outside the proof.
6. **The keeper's project.** No change. The keeper is in `Rotation / Keepers`, which the root
   writes, and the broker has a single-secret grant there.
7. **Open, for Devon: the executor calling `api.anthropic.com`** (a new destination for the
   executor's process).
8. **The row's fields.** The row gains `workspace_id`, and its comment is reworded. It becomes
   `rotated_by_sds = true` with `rotate_requested` only after the route is built, the exit checks
   pass, and items 7 and 11 are answered.
9. **Logging and failure.** Fingerprints only. No key, control, header or body is logged.
   Indeterminate answers fail the step, and nothing is swallowed.
10. **Open, for Devon: an Admin API key for the executor.**
11. **Open, for Devon: the measurement.**
12. **What D-82, D-83 and D-84 cover**, from sds-hq `standing-answers.md` at `b2460b34`:
    - D-82 covers nothing here.
    - D-83 rule 2 (fix forward) applies.
    - D-84 applies: its condition 1 is already met, and its condition 3 (T1) is the code pull
      request's.
    - Their exceptions are items 10 and 11. Item 7 is in none of them.
13. **The code pull request's changes** (file list) and two exit checks:
    1. A one-off probe of the current key, without a mint.
    2. One real rotation.

The amendment also has the "What it grants, and nothing more", "Consequences" and "Passages it
governs" sections.

### Open, for Devon

- **Item 7, the executor calling Anthropic.**
  - **A.** Accept it, with a confined client that permits only `GET /v1/models`.
  - **B.** Leave the row by hand.

  **Recommendation: A**, the OpenRouter adapter's pattern. The host already calls public HTTPS
  providers, and no host egress filter is documented. **Blocks:** the row's `rotated_by_sds`.
- **Item 10, an Admin API key.**
  - **A.** No: a person mints and deletes.
  - **B.** An Admin API key in `Rotation / Admin` gives a machine revoke (`status: "archived"`)
    and a list of the workspace's keys. That's an organization-wide admin credential, plus an
    id-matching design that only `partial_key_hint` can support.

  **Recommendation: A.** **Blocks:** nothing.
- **Item 11, the measurement.**
  - **A.** No new key: exit check 1 measures the control and header on the current key, and the
    first real rotation's `confirm_dead` records the deleted key's status, failing closed.
  - **B.** A throwaway key that Devon mints and deletes, which a session probes.

  **Recommendation: A**, as D-84 (14) A. **Blocks:** nothing under A. Under B, the code pull
  request's merge.

## The pull request

https://github.com/AlobarQuest/orchestrator/pull/819. The amendment's text is final at `f918ddfa`,
which was the head when the PR opened. The PR's head is one commit later: the commit that adds
this paragraph changes only this report.

## Checks run

- **The stop point:** I ran `git fetch`, then `git log 9caad335..origin/main -- <ADR-0055>`, at the
  start and again just before the push. Both were empty.
- **Each repository claim** was read at `9caad335` in this worktree with `sed -n` or `grep -n`, and
  each line number was re-checked after writing. Five numbers were corrected in a second pass:
  - factory-runner's row is lines 112-118, and its comment is line 103.
  - The standing answers end at line 323.
  - The OpenRouter management-key row in `rotation-executor.md` is line 674.
  - The launcher's ping lines are 34-38.

  The claims checked:
  - **This repository's row and registry:** the row's fields and consumers, the broker row's
    comment, `.bws-secrets.toml` lines 658-662, and the allowlist's line 60.
  - **The lane:** the launcher's lines 26, 34-38, 72-73 and 128-134; the crontab's lines 9-10;
    the compose's line 4; and `lanes/Dockerfile`'s line 26 (`INFRAOPS_COMMIT=8dc4d43e…`).
  - **The rotation code:** `registry.py` 57; `UNTYPED` 26 and 29; `rotation_proposer/cli.py`
    502-504; `types.py` 38, 39 and 67; `inbox.py` 12, 20, 120 and 172; `worker.py` 954;
    `values.py` 17.
  - **Egress:** `openrouter.py` 62 and `github_actions.py` 43.
  - **Architecture tests and policy:** `test_rotation_edges.py` 366; `test_wsp21_invariant_scan.py`
    782; `factory-policy.toml` 464 and 575.
  - **ADR-0055:** lines 205-225, 342, 852-854, 1779-1784, 4386-4389 and 4651-4664.
  - **Elsewhere in the repository:** ADR-0059 lines 822, 4203-4205 and 4366-4370;
    `scripts/secrets_broker.py` 16-19; and `rotation-executor.md` 674 and 677.
- **Other repositories:**
  - infraops at `8dc4d43e`: `change-mgr-cli.ts` 153-155 and `run-window.ts` 70-80.
  - factory-runner at `280b641`: the shared row.
  - sds-hq: `standing-answers.md` 257-323 at `b2460b34`, `state/decisions.md` line 16, and
    `state/history/progress-2026-11.md` 6174-6175.
- **Readers:** I grepped for the keeper's UUID across this worktree, every `~/Projects` repository,
  `~/.claude/bin` and `~/Library/LaunchAgents`.
- **Egress:** I grepped `src/rotation_worker` and `test_rotation_edges.py` for "anthropic" and
  found nothing. I also grepped `rotation-executor.md` for egress and firewall, and found nothing.
- **Anthropic's docs:** a subagent read them on 2026-11-26 and returned verbatim quotes. The
  amendment lists every page it relies on. The following points are marked unmeasured or
  undocumented:
  - A deleted or disabled key's status and lag.
  - The header on `/v1/models`.
  - Whether `/v1/models` is billed.
- **Token-shape grep of the diff:** I grepped the added lines of `git diff 9caad335..HEAD`, both
  files, for Anthropic's key prefix, BWS token shapes, a `wrkspc_` id and runs of 40 or more key
  characters, after the last edit. There were 15 matches. Each is a file path or documentation
  URL that matched the long-run pattern, and none is a key or token. The amendment describes key
  prefixes in words and quotes no workspace id.
- **Risk check:** `uv run --project ~/Developer/code-standards code-standards risk --repo .`
  returned `normal`, so `vendor-review` isn't required.
- **Not run:** `make check`, which needs Postgres and a migrated database. The change is one ADR
  and this report, and no test reads ADR-0055's text: a grep of `tests/`, both exit manifests and
  `layers.toml` finds none. `docs/superpowers/**` is in `layers.toml` (line 730).

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (Amendment 36
  appended)
- `docs/superpowers/reports/anthropic-key-adapter-adr-report.md` (this report)

## Self-review

- Every Open item has options and a recommendation, and none is a grant. The amendment grants
  nothing.
- **Items 3 and 4 were the two risks of a false claim.** I wrote both narrowly:
  - The workspace header is documented, but not for `/v1/models`, and an exit check measures it.
  - A green ping proves the read, not Anthropic's acceptance, and that is stated as a carried gap.
- **Item 2's control status** is inferred from the docs' 401 definition and is measured by exit
  check 1.
- **Item 11 A** relies on the first rotation for the deleted-key status. Devon's deletion comes
  before `confirm_dead`, so a non-401 there revokes nothing by machine, and the unit goes to a
  person.

## Concerns

- **Two documentation conflicts:**
  - The Retrieve page says the key routes need an Admin API key, but the overview also accepts
    `org:admin` OAuth and unscoped personal keys. The amendment treats all three as admin.
  - `anthropic-version` is marked required on one page and optional on another. The probe sends
    it.
- **Standards gap:** Code Brain has no `secrets` road, per the handoff. The plan follows the
  executor's existing value handling (fingerprints only).

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** item 9 logs through the executor's existing
  structured logging, with no new log path.
- **Code Brain 4 (never silently swallow):** item 9 says every transport failure, unexpected
  status, missing or mismatched header, or unrefused control fails the step with its cause named.
  Nothing is swallowed.
- **Code Brain 5 (never log secrets):** item 9 says the key, the control and the `Authorization`
  header appear in no log, exception or evidence field. Evidence holds only fingerprints (sha256
  prefix and length, `values.py`), and a failure names its status, never a body.
- **Infra Brain 591, 592 and 593 (no token in tracked files or history, no inline bootstrap
  token):** the diff holds no token, key or key-shaped literal (the token-shape grep in Checks
  run). Key shapes are described in words.
- **Infra Brain 1032 (`cred.exposure-rotate`):**
  - Item 4: the old key is deleted only after the reader's green pass after the store.
  - Item 8: the row's consumer and exposure fields stay valid.
  - Item 5: after an exposure, Devon may delete first (`revoke_may_come_first`).
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable, because no endpoint is added. The
  amendment says so in "What it grants, and nothing more".
- **Merge bar (no false claims, no unapproved authority):** every claim cites a file and line at a
  named commit, or a documentation page. Every new reach (item 7), admin credential (item 10) and
  proof setup (item 11) is an "Open, for Devon" item.
- **Construction-mode ruling**
  (`~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`): items that are
  Devon's are raised as Open items. No HQ-settled item was needed.
