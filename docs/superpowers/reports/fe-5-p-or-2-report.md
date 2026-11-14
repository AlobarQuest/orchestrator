Outcome: done

# FE-5 P-OR-2 build report: the factory-events session wrapper and its operations page

- **PR:** https://github.com/AlobarQuest/orchestrator/pull/704, against `main`, not merged.
- **Base:** `origin/main` `03075209` (P-OR-1, #703).
- **Code head:** `48c830fa`, three commits: `595435ad` (the change), `73f11cb3` (a test that kills
  a mutation survivor) and `48c830fa` (review fixes). This report is committed on top as docs only,
  so read the PR's head for the final sha.
- **Worker:** W-158. Fix round 0.

## What I built

The plan's P-OR-2 "Files" and "Changes" (plan lines 227-260 at `03075209`), in full.

1. **`scripts/factory-events-session.sh`** (mode 0755).
   - It unsets its own names and the five chain variables first (lines 45-49). It leaves a retired
     `FACTORY_EVENTS_*_DSN` set, so the child refuses it by name, as P-OR-1's launchers do.
   - It sources `scripts/sds-bws.sh` for `sds_bws_value` and the switch reader (line 59).
   - `approve <checkout> <package-dir>`:
     - It needs exactly three arguments.
     - It refuses an empty or option-like checkout, then resolves the checkout to an absolute
       path (lines 70-75).
     - It refuses an empty or option-like package directory, and resolves a relative one from the
       caller's directory (lines 76-81).
     - It refuses when `~/.factory/chain-db.env` is absent (`factory_events_switch_present`), then
       reads the settings from it.
     - It runs `<checkout>/.venv/bin/python -m intent_packages approve <package-dir>` in the
       checkout.
   - `import-file <path>`:
     - It needs exactly two arguments and refuses an empty or option-like path.
     - It refuses while `chain-db.env` exists, even as a dangling symlink
       (`factory_events_switch_present`).
     - It refuses without `chain-db.env.pending`, then reads the settings through
       `factory_events_read_settings "$HOME/.factory/chain-db.env.pending"`.
     - It runs `~/Projects/security-standards/.venv-events/bin/python -m factory_events import-file --path <path>`.
   - Any other first argument refuses, `verify` included.
   - The order is: refuse on arguments, the switch and staging conditions, settings and the
     interpreter's presence; only then read the Keychain item with `/usr/bin/security` (no
     environment override); then `sds_bws_value <uuid> "$token"` (lines 112-119); then the child
     with the four variables as an assignment prefix (lines 121-133).
   - Every refusal prints `factory-events-session: <reason>` on stderr and exits 2. The wrapper
     exits with the child's code when the child runs.
2. **`docs/operations/factory-events-chain.md`**: what the wrapper needs, the switch and staging
   files, how a session approves and imports, why a session doesn't verify, and what fails closed.
3. **A one-line pointer** in `docs/operations/package-intake.md`, after the "By hand" summary in the
   page's introduction.
4. **`layers.toml`**: the wrapper, its test file and the page are in `[intent]`, beside
   `factory_chain_read.py`. That layer's edges test has no shell rows to extend.

## Item 2: does `approve` pass any further argument through

No. `approve` takes only `<checkout> <package-dir>`, and no other argument passes through. I didn't
write a question file, because nothing a session records changes without one:

- **Approver.** `--approver` defaults to `devon` (intent-packages `ae84d53`, `cli.py` line 27). Every
  documented interactive approval passes `--approver devon` explicitly, for example sds-hq
  `state/gates/landing-ledger-token-issue.md` line 21 and the three sibling gate files. So the
  recorded approver is the same with or without the flag.
- **Event actor.** The emitter reads `FACTORY_AGENT_ID` and otherwise uses
  `claude-code-interactive` (intent-packages `ae84d53`, `emitter.py` lines 21 and 132). The wrapper
  neither sets nor clears `FACTORY_AGENT_ID`, so the caller's value, or the default, reaches the
  child unchanged.
- **`--by-policy`** belongs to the cycle and the bump proposer, under their own accounts. A session
  approves on Devon's word (D-47).

A session that needed some other approver couldn't get one through the wrapper. That's a
deliberate narrowing, and the page says so.

## A decision the plan doesn't state: the child's working directory

`approve` records `git rev-parse HEAD` of its working directory as the approval's commit
(intent-packages `ae84d53`, `cli.py` lines 116-120 and 168). From any other repository it would
record that repository's commit and still exit 0, on an append-only chain. So the wrapper runs the
child in `<checkout>`, as sds-hq's standing approval does (`standing_approve.py` line 571,
`cwd=clones.ip`). It resolves a relative package directory from the caller's directory before it
enters the checkout. The import's child runs in the caller's directory, and its `--path` is passed
as given.

## Tests and checks

| Run | Result |
| --- | --- |
| `tests/scripts/test_factory_events_session.py` (new) | 41 passed |
| `tests/scripts/test_sds_bws.py` | 76 passed |
| `tests/scripts/test_bridge_chain_switch.py` | 36 passed |
| `tests/architecture` + `tests/scripts` | 3,169 passed before the layer entries were committed. One failure: `test_every_pattern_names_a_file` reads tracked files only, and it passed once they were committed. |
| `make check` on the final code head `48c830fa` | rc 0: ruff, format, pyright and shellcheck clean; 15,005 passed, 1 skipped |
| Security scanner (repo root) | 0 BLOCK. Two WARNs, both already on `main`: an undeclared UUID in `scripts/install-rotation-worker-launchd.sh` and an uncovered `*-migration/` glob. |

The tests use stub `security`, `bws`, `env` and child interpreters, a temporary `HOME`, and a PATH
of the stubs plus `/usr/bin:/bin`. Every `/usr/bin/security` in the copied scripts is rewritten to
the stub, as `test_bridge_chain_switch.py` does, so the real Keychain is never read. They assert
the following:

- Each child's argv, its working directory, and that its `FACTORY_EVENTS_*` environment is exactly
  the four values (no read password).
- That the caller's `BWS_ACCESS_TOKEN`, all five chain names and the wrapper's own names, each
  exported with a different fake value, never reach the fetch or the child.
- That `bws` gets the session token as `BWS_ACCESS_TOKEN` alone, and no chain variable.
- That no stub token or stub password appears in any stub's argv, on stdout or on stderr.
- That every refusal is named, exits 2, and (except the Keychain and BWS refusals) reads no
  Keychain item and runs no `bws`. The refusals covered: an unknown subcommand (`verify` included),
  a wrong argument count, an option or empty argument, the missing switch, a present or dangling
  switch for `import-file`, the missing or symlinked staging file, malformed settings, a missing
  checkout or interpreter, a missing Keychain item, and an empty password.
- That the exit code is the child's, and that `--color no` is on the fetch.

**Discrimination.** I applied 24 single mutations to the wrapper in a scratch copy (`git archive`
of HEAD, `__pycache__` cleared each time) and ran the new tests against each. The tests caught 23
of them, failing between 1 and 28 tests each. The original passed 41 of 41 after restore. The one
survivor exports `FACTORY_EVENTS_APPEND_PASSWORD` just before the child. That's equivalent: no
other process starts after that line. One earlier survivor, exporting the token, was a real gap,
and `test_bws_gets_the_session_token_as_its_bws_access_token_alone` (commit `73f11cb3`) now
catches it.

## Files changed

- `scripts/factory-events-session.sh` (new)
- `tests/scripts/test_factory_events_session.py` (new)
- `docs/operations/factory-events-chain.md` (new)
- `docs/operations/package-intake.md` (one pointer)
- `layers.toml` (three paths in `[intent]`)

## Self-review

`/code-review` (medium) returned four findings. I fixed three in `48c830fa`:

- **Relative checkout.** The interpreter check passed from the caller's directory, and the same
  relative path no longer resolved after the `cd`. That failure came after the fetch, with exit
  127. The checkout is now resolved to an absolute path up front.
- **An option-like checkout** is now refused by name, like the package directory.
- **Doc wording.** The page now says exactly which refusals come after the Keychain read.

I declined the fourth, that `approve` accepts any directory as the checkout. The plan names
`<checkout>` as the caller's argument, and a directory whose venv lacks `intent_packages` fails in
the child. Checking that it's "an intent-packages checkout" would be a new rule the plan doesn't
state.

## Concerns

1. **A new suppression comment.** `# shellcheck disable=SC1091` sits above the `source` line. The
   repo's `.shellcheckrc` sets `external-sources=false`, so shellcheck exits 1 on that info-level
   finding without it, and `make check` fails. Every launcher that sources `sds-bws.sh` carries the
   same line (for example `run-bump-proposer.sh` line 172).
2. **A traceback on stderr when BWS fails.** `sds_bws_value`'s JSON parse prints a Python traceback
   on empty input before the wrapper's named line. It carries no value, because the input is empty,
   and it's P-OR-1's shared helper, so I left it alone. The tests check that the named refusal is
   the last stderr line.
3. **Plan wording.** The plan's P-OR-2 "Files" line describes the page as "how a session approves,
   imports and verifies". The wrapper has no `verify` (open question 8), so the page has a "Verify
   the chain" section that says a session doesn't verify, and names what does.
4. **The caller's other variables pass through.** Apart from the names it unsets, the wrapper hands
   the caller's environment to the child, including a caller's `BWS_ACCESS_TOKEN`. It never uses
   that token, and it changes nothing a session's direct `approve` would see. The handoff asked only
   that the wrapper ignore the token.
5. **Standards coverage.** The handoff's standards cover everything this change touches.

## Line citations relied on

- Plan `docs/superpowers/plans/2026-11-14-fe-5-cutover.md` at `03075209`: lines 40-93 ("The
  switch"), 95-110 ("Holders"), 227-267 (P-OR-2), the act table after line 432 (steps 0, 5, 6 and
  9), open questions 4 and 8, and follow-ups 1 and 2.
- P-OR-1 report at `03075209`, lines 15-40 and 305-320.
- `scripts/sds-bws.sh` at `03075209`: lines 53-68 (`sds_bws_value`, the two-argument branch at line
  60) and 95-167 (the switch reader).
- `scripts/run-bump-proposer.sh` at `03075209`: lines 264-299 (the fetch pattern) and 303-312 (the
  assignment prefix).
- intent-packages `ae84d53`: `cli.py` lines 25-35, 116-120 and 135-180; `emitter.py` lines 21, 114-123
  and 132.
- security-standards `4e83d7a`: `src/factory_events/cli.py` lines 230-236 (`import-file --path`).
- sds-hq `standing_approve.py` lines 562-572 (`cwd=clones.ip` at line 571), and
  `state/gates/landing-ledger-token-issue.md` line 21 (`--approver devon`).
- ADR-0055 amendment 25 items 1, 3 and 4, and amendment 26 items 4 to 7.

## Standards followed

- **Code Brain rule 5 (never log secrets).** The token reaches only `bws`, through
  `sds_bws_value`'s assignment prefix. The password reaches only the child's environment, through
  the prefix. Neither is exported or written to a file. The tests check every stub's argv, stdout
  and stderr for the fake token and password. No real secret appears in this report or the PR.
- **Code Brain rule 4 (never silently swallow an exception).** Each failure prints a named
  `factory-events-session:` line and exits 2. The two `|| true` guards (the Keychain read and the
  fetch) are followed by an empty check that refuses by name. Nothing falls back to the file
  chain.
- **Code Brain rule 3 (structured logs to stdout; no new log file).** The wrapper writes no log
  file. Its stdout is the child's, and its refusals go to stderr, which is where every launcher in
  this repo puts them.
- **Infra Brain 591, 592, 593 (no BWS token in a tracked file or commit; no inline
  `BWS_ACCESS_TOKEN`).** Test stubs use values like `stub-token-factory-events-session`, which
  don't match the token shape. The scanner reports 0 BLOCK.
- **Infra Brain 1032 (a credential seen in a transcript is exposed).** I saw no credential. I didn't
  run `bws`, a real `security find-generic-password`, or the wrapper outside the test harness.
- **Infra Brain 212 (HTTPS for public FQDNs).** It doesn't apply: the database has no public FQDN.
- **Workspace invariant: every parsed `bws` call passes `--color no`.** The fetch goes through
  `sds_bws_value`, which passes it. A test asserts the argv
  `secret get <uuid> --output json --color no`.
- **Construction mode.** I drove this to an open PR without a question. Item 2 and the working
  directory were settled from the code, with citations.
- **Stop points.** I didn't touch the main tree, the real `~/.factory`, or
  `security-standards.pin.toml`. The only database used was `orchestrator_test_hq_w_158`.

## Follow-ups

- **Act step 0.** Devon mints `BWS_ACCESS_TOKEN_FACTORY_EVENTS_SESSION`. The wrapper refuses until
  then.
- **Act step 2.** Confirm the orchestrator main tree holds this PR's merge commit before step 6.
- **Teardown.** HQ decides when to remove worktree `hq-W-158`, branch `hq/fe-5-p-or-2`, and database
  `orchestrator_test_hq_w_158` after merge. Scratch files `/tmp/w158-mut`, `/tmp/w158-mutate.py`,
  `/tmp/w158-wrapper.orig` and `/tmp/w158-*.log` remain.
