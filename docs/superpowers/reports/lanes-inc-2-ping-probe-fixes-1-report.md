Outcome: done

# Report: lanes-inc-2-ping-probe-fixes-1

Fix round 1 on orchestrator PR #632 (`hq/lanes-inc-2-ping-probe`). The new head is `9bb973ed`
(one commit on `3ab56898`), pushed to PR #632's branch. I didn't open a second PR or merge.

## What I built

These are text changes only: docs, one module docstring, one comment, and one CLAUDE.md bullet.
They correct the claim that the read-only lookup proves the whole ping URL. Healthchecks'
`unique_key` hashes only `code.hex[:16]`, so the lookup proves two things: the check exists in the
read-only key's project, and the URL's UUID matches it in its first half. Cut-over step 4, the
container's first scheduled ping landing on that check, is still the only proof of the full URL.

Changed line ranges at `9bb973ed`:

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`:
  - 31-32: amendment 21's status-list line now reads "a read-only lookup finds a ping URL's
    check, beside a random one's; cut-over step 4 still proves the full URL". The amendment's
    own status ("Accepted on merge") is unchanged.
  - 2309-2310: amendment 19 item 5's amendment-21 parenthetical adds that the lookup covers only
    the UUID's first half, so step 4 still proves the full URL.
  - 2464-2465: the "ping URL's check read" open item's parenthetical says the same.
  - 2564: the title is "Amendment 21 — A read-only lookup finds a ping URL's check".
  - 2589-2592 (Context): what the lookup proves and doesn't, with the reason (`unique_key`
    hashes the first half, so a URL wrong in its last 16 hex characters gets the same 200).
  - 2609-2610 (decision 2): a 404 is "a URL wrong in its UUID's first half, or a check in another
    project"; a URL wrong only in its second half answers 200 and passes.
  - 2644-2645 (Consequences): cut-over step 4 is "still the only proof of the full URL". This
    replaces "it no longer stands in for `verify_new`".
- `docs/operations/rotation-executor.md`:
  - 1313: the bind bullet points to the following bullet for what the lookup proves.
  - 1318-1324 ("The ping URL's lookup"): the 404 meaning is corrected, and the bullet now says
    what the lookup proves and that step 4 is the only full-URL proof.
- `src/rotation_worker/adapters/healthchecks_ping_url.py` 16-19 (module docstring): the same
  correction.
- `src/rotation_worker/CLAUDE.md` 101-103: the `verify_new` bullet adds the first-half limit and
  the step 4 proof. The other ping-URL bullet (line 40) says only "its shape and its check's
  read-only lookup", which makes no full-URL claim, so I left it.
- `src/rotation_proposer/generate.py` 98: the comment now reads "A type checked by its shape,
  then probed by a read-only lookup (amendment 21, D-31): a ping URL." The `_BY_SHAPE` string and
  the rendered bytes are unchanged.

To find other sentences, I grepped `docs/` and `src/**/CLAUDE.md` once for `lookup`,
`unique_key`, and `read-only key`. Apart from the lines this report lists, no sentence says the
lookup proves the whole ping URL. The ADR-0059 hits at 683-715 are about the Management key's
name lookup, not this probe. I left amendment 19 item 3's parenthetical (line 2260) as it was:
it says R14 holds, which is still true, and it makes no full-URL claim.

## Tests and checks

All of these ran in the worktree on `orchestrator_test_hq_w_53`, which I created, migrated, and
then dropped:

- `uv run alembic upgrade head`: exit 0.
- `uv run ruff check src tests`: all checks passed.
- `uv run ruff format --check` on the two changed `.py` files: already formatted.
- `uv run pyright src/rotation_proposer src/rotation_worker`: 0 errors, 0 warnings.
- `uv run pytest tests/rotation_worker tests/rotation_proposer
  tests/contract/test_rotation_step_contract.py`: 1,699 passed. This run includes the proposer's
  generated-package byte tests, so the comment change left the rendered package bytes unchanged.
- CI on `9bb973ed`: Quality passed (14,118 passed, 11 skipped, as expected). Rollout
  transcription currency and Runner consumer compatibility passed too.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- `docs/operations/rotation-executor.md`
- `src/rotation_proposer/generate.py` (comment only)
- `src/rotation_worker/CLAUDE.md`
- `src/rotation_worker/adapters/healthchecks_ping_url.py` (docstring only)
- `docs/superpowers/reports/lanes-inc-2-ping-probe-fixes-1-report.md` (this report)

## Self-review

- No behavior or test change: the diff touches no executable line. Pyright, ruff, and the
  suites are unchanged and green.
- The bind refusal string in code, "a wrong URL or another project's check", still says a 404
  means a wrong URL. That's true but loose. I left it because it's runtime output, the handoff
  limits this round to docs, docstrings, and comments, and a test may pin it. HQ can tighten it
  in a later round if it wants.
- Amendment 21's decisions (D-31), its status line, and the ADR's other text are unchanged,
  apart from the ranges listed in "What I built".
- The local branch is named `w53-ping-probe-fixes`, not `work`, because another worktree
  (`hq-W-49`) holds `work`. The push target is PR #632's branch, as the handoff says.

## Concerns

None for this round. The loose wording of the code's refusal string is in "Self-review".

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no code changes. The edited text
  doesn't change how a lookup with no answer is logged: the ADR's decision 4 still describes
  structured JSON on stdout.
- **Code Brain rule 4 (never swallow an exception silently):** no code changes. Every edited
  passage still describes a failed lookup (wrong first half, other project, failed control, other
  status, no answer) as failing the step or refusing the bind.
- **Code Brain rule 5 (never log secrets, including the ping URL, its UUID, and the
  `X-Api-Key`):** no text quotes a ping URL, a UUID, or a key. The edits name only the
  `unique_key` mechanism and the `code.hex[:16]` slice.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history):** the diff contains no
  token or secret value. It describes the mechanism and pastes nothing.
- **Infra Brain 2890 (never commit a ping URL; examples use a placeholder UUID):** the diff adds
  no ping URL and no example UUID, real or placeholder.
- **Infra Brain 1032 (an issue package states no revoke):** this round adds no revoke. Decision 6
  ("No revoke") and the issue package's bytes are unchanged, and the proposer's byte tests pass.
