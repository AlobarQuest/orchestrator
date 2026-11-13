# Question: which repositories does the change proposer's mint name?

Worker W-124, handoff `lanes-inc-4-change-proposer`, base `5ed15482`. Nothing is built or committed
yet; I stopped at the first decision the handoff's facts don't settle.

## What the handoff says

"The lane's scope comes from the member record (repositories granted `rollouts`, narrowed to those
with a transcribed workflow and rollback plan). Derive the mint's repositories from the same source
the tool uses." A scope failure mints nothing and exits 2.

## What the code does

The tool's GitHub reads reach two populations, and only one of them is that scope:

1. **The proposing and retirement passes** read `_in_scope(members)`
   (`src/change_proposer/cli.py:264`): the member record's `rollouts` grants, intersected with
   `ROLLOUT_WORKFLOWS` and a transcribed rollback plan. Calls: `GET /repos/{r}/pulls?state=open`,
   `GET /repos/{r}/pulls/{n}` and `GET /repos/{r}/contents/{path}?ref=` (`pull_requests` and
   `contents` read).
2. **The scope sweep** (`_scope_sweep`, `cli.py:304`) calls
   `audit(ROLLOUT_WORKFLOWS, REGISTRY, …)`, which reads the rollout workflow's blob for **every key
   of `ROLLOUT_WORKFLOWS`, whatever its grant**. `_in_scope`'s docstring says this is deliberate
   ("`_scope_sweep` audits the transcriptions whoever is granted"), and the sweep's docstring keeps
   its population equal to `scripts/check_rollout_transcription_currency.py`'s. `--repository`
   doesn't narrow it. `ROLLOUT_WORKFLOWS` is `alobarquest/change-manager` and `alobarquest/brain`
   today (`src/deploy_watcher/workflows.py:79`).

Because the scope is a subset of `ROLLOUT_WORKFLOWS`, the repositories the tool reads are exactly
the `ROLLOUT_WORKFLOWS` keys. If the mint names only the member-record scope and a transcribed
repository loses (or never had) its `rollouts` grant, the sweep's read of that repository fails
with the token's 404, the sweep reports it unreadable, and every pass exits 2. I can't check today's
grants: the handoff rules out live calls.

ADR-0059 decision 2 (~589-595) says each mint "names only that lane's repositories and only the
read permissions its GitHub calls use", which reads as "the repositories its calls read". The deploy
watcher's mint (#653) names exactly the `ROLLOUT_WORKFLOWS` keys through `deploy-watcher
repositories`.

## Options

- **A (I recommend this).** Add a `change-proposer repositories` subcommand that reads the member
  record and prints the union of `_in_scope(members)` and the sweep's population, which equals the
  `ROLLOUT_WORKFLOWS` keys. An unreadable record or empty list exits 2, and the launcher mints
  nothing. The mint names every repository the tool reads, and no other. Behavior is unchanged from
  the Mac. Cost: the mint can name a transcribed repository that has no `rollouts` grant (read
  only, one file, the read App is installed only on members anyway).
- **B.** Mint only the member-record scope, and narrow `_scope_sweep` to it. This changes the
  tool's behavior: an ungranted transcription goes unaudited by this lane, and its population stops
  matching `check_rollout_transcription_currency.py`'s.
- **C.** Mint only the member-record scope, and leave the sweep. An ungranted transcribed
  repository makes every pass exit 2 (`/fail` on each `--submit` run).

## Question

Is A the intended reading of "the same source the tool uses", or is the mint to be held to the
member-record scope (B or C)? If B, confirm that narrowing the sweep is in scope for this PR.

Permissions, for reference: every call is a GET on `pulls`, `pulls/{n}` or `contents`, so the mint
asks `metadata`, `contents` and `pull_requests` read, and not `actions`. Everything is within the
read App's grant.

## HQ's answer (2026-11-13)

Option A. The `change-proposer repositories` subcommand reads the member record and prints the
union of `_in_scope(members)` and the sweep's population (the `ROLLOUT_WORKFLOWS` keys); an
unreadable record or an empty list exits 2, and the launcher mints nothing. `_scope_sweep` isn't
narrowed: its docstring makes its population deliberate and equal to
`check_rollout_transcription_currency.py`'s. This is ADR-0059 decision 2's "repositories its calls
read", and it matches the deploy watcher's mint (#653), which names the same keys. Permissions:
`metadata`, `contents` and `pull_requests` read; no `actions`.
