# CLAUDE.md split into nested files (2026-11-05)

## Why

The root `CLAUDE.md` was 76,132 bytes (919 lines) and Claude Code loads it in full on every turn of
every session in this repository. Session analysis showed instruction files are a meaningful share
of all tokens spent here. A `CLAUDE.md` in a subdirectory loads only when the session reads or edits
files in that directory, so a rule that governs one subsystem costs tokens only in the sessions that
touch that subsystem.

## What changed

- The root file is now 30,374 bytes. It keeps the `code-standards` managed block byte for byte, and
  84 of the 200 "Known Non-obvious Invariants" bullets: the rules that cross directories or are not
  tied to one (deploy, credentials, envelope authoring, landing-lane behaviour that spans the
  server and the lanes, test-database and worktree procedure, the whole-repo guards a change
  anywhere can trip). Where a placement was unclear, the rule stayed at the root.
- 116 bullets moved, verbatim and in their original order, into 27 nested files. Each starts with a
  heading naming its subsystem and saying it continues the root section. The root section's intro
  paragraph lists them all.

| Nested file | Bullets | Bytes |
| --- | --- | --- |
| `migrations/CLAUDE.md` | 1 | 318 |
| `scripts/CLAUDE.md` | 3 | 1,750 |
| `src/alert_hub_client/CLAUDE.md` | 1 | 424 |
| `src/bump_driver/CLAUDE.md` | 1 | 1,024 |
| `src/deploy_watcher/CLAUDE.md` | 2 | 653 |
| `src/execution_watch/CLAUDE.md` | 1 | 1,285 |
| `src/fork_harden/CLAUDE.md` | 1 | 609 |
| `src/fork_sync/CLAUDE.md` | 3 | 1,684 |
| `src/landing_ledger/CLAUDE.md` | 1 | 379 |
| `src/orchestrator/CLAUDE.md` | 13 | 7,293 |
| `src/orchestrator/api/CLAUDE.md` | 1 | 445 |
| `src/orchestrator/kernel/CLAUDE.md` | 5 | 1,546 |
| `src/orchestrator/persistence/CLAUDE.md` | 3 | 1,301 |
| `src/orchestrator/services/execution/CLAUDE.md` | 3 | 497 |
| `src/orchestrator/services/intake/CLAUDE.md` | 24 | 4,861 |
| `src/orchestrator/services/landing/CLAUDE.md` | 16 | 5,549 |
| `src/orchestrator/services/lifecycle/CLAUDE.md` | 7 | 1,727 |
| `src/orchestrator/services/membership/CLAUDE.md` | 1 | 771 |
| `src/orchestrator/services/release/CLAUDE.md` | 1 | 760 |
| `src/orchestrator/services/reporting/CLAUDE.md` | 3 | 1,433 |
| `src/orchestrator/services/verifier/CLAUDE.md` | 5 | 2,222 |
| `src/rotation_decomposer/CLAUDE.md` | 1 | 840 |
| `src/rotation_proposer/CLAUDE.md` | 1 | 2,634 |
| `src/rotation_worker/CLAUDE.md` | 14 | 7,932 |
| `src/tool_installer/CLAUDE.md` | 2 | 744 |
| `src/unit_verifier/CLAUDE.md` | 1 | 1,571 |
| `tests/protocol/CLAUDE.md` | 1 | 576 |

- Grouped rules moved together: the decomposition policy's rotation and bump branches (intake), the
  pr-merge policy path (landing), and the runner-authority predicate with the registration rule
  that refers to it (kernel). The runner-envelope term group stays at the root, whole.
- `tests/architecture/test_execution_writers.py` cited "Four rules in CLAUDE.md"; it now names the
  lifecycle file for the lease, version and claim-release rules and the root for the dispatch rule.
  The rules cited by `test_transaction_ownership.py` and `src/rotation_proposer/findings.py` stayed
  at the root.
- `layers.toml` places the four nested files no existing pattern covered (`src/orchestrator/`,
  `src/orchestrator/api/`, `src/unit_verifier/`, `scripts/`) in the layer of the code they govern.
  The rest fall under their directory's existing `**` pattern.

## How it was checked

- Every bullet of `origin/main`'s section appears exactly once across the root and nested files:
  the multiset of bullets before (200, all distinct) equals the multiset after (200).
- The text up to `<!-- code-standards:end -->` is byte-identical to `origin/main`, and running
  code-standards' `inject_stanza` over a copy of the new root file changes nothing.
