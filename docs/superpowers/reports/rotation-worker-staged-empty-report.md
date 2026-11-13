Outcome: done

# Report: a staged `empty` marker isn't a staged value

Handoff: `sds-hq/state/handoffs/rotation-worker-staged-empty.md` (plan
`docs/superpowers/plans/2026-11-12-phase1-speedup.md` Task 4; design "The staging race").
Branch `hq/rotation-worker-staged-empty` from `origin/main` `d4313d87`.
PR: AlobarQuest/orchestrator#682 (draft). Fix commit `522b4bd2f6c8327e0f2103b7dd9bfed1011387ba`.
Commit `b649e9e2` adds the pass-level test and the nested-rule update, and this report is
committed on top of both, so the PR head is the report commit.

## What I built

A staging secret that holds the inbox's empty marker, a blank, or padded text no longer counts as
staged, so the unit after a person's mint keeps waiting and its mint act stays due. When the inbox
holds a value beside such a secret, the value is written into that secret instead of being created
beside it.

- `src/rotation_worker/inbox.py:63` `pasted(store, secret_id)`: the value a person pasted, or
  `None` for the marker (compared after `read_key`, which strips on BWS) or for text that isn't a
  credential value (`ValueError` from `Value`, which refuses blank or padded text in every store).
  `Inbox.read` uses it as well, so the inbox and staging share one definition.
- `src/rotation_worker/worker.py:648-650`: `_mint_waits` decides "staged" with `pasted`. Both the
  no-inbox and inbox branches use it.
- `src/rotation_worker/inbox.py:146,178` `bind(..., staging_id=None)` and `_stage`: with a
  `staging_id`, `bind` re-reads it and writes `new` into it with `store.write`. It doesn't call
  `store.create`. On BWS a second secret of the same name would make `find` raise on every later
  pass, and the Keychain's `create` refuses an existing item. If the re-read finds a value pasted
  during the checks, `bind` returns a refusal reason and leaves that value in place.
- `src/rotation_worker/worker.py:674`, `_bind_inbox`: passes the marker secret's id through.
- `src/rotation_worker/worker.py:781` `_tidy`: takes the staged `Value` that `_mint_waits`
  already read instead of reading it again, and drops its unused `setup` parameter.

I made no change to the `SecretStore` protocol or outside `src/rotation_worker/` and its tests, so
neither stop point applied.

## Steps

1. **Tests first** (`tests/rotation_worker/test_inbox.py`, 19 new cases). The no-inbox and inbox
   paths come from the `INBOX_PATHS` parametrization.
   - `test_staged_marker_waits`: the unit waits, only the mint act is due, and the marker is
     untouched.
   - `test_staged_marker_with_whitespace_waits`: covers `"empty\n"`, `" empty "` and a blank,
     each on a store that strips (BWS) and one that doesn't (Keychain).
   - `test_staged_token_proceeds`.
   - `test_an_inbox_value_beside_a_staged_marker_is_bound_into_that_secret`: exactly one staging
     secret exists afterwards, it holds the value, and the inbox is emptied.
   - `test_a_staged_marker_beside_an_unreadable_inbox_waits_on_the_clock`.
   - `test_a_value_staged_by_hand_while_the_inbox_s_was_checked_is_not_overwritten`.

   I added a `TypedStore` test double because `MemoryStore` holds only `Value`s and can't hold
   padded text, and its `create` never refuses a duplicate. That's why the bind test counts
   staging secrets.

   The handoff asks that no `store` or `verify_new` step is filed. When `_mint_waits` returns
   `True`, `_take` returns 0 before it reaches `_work` (`worker.py:466-475`). I pinned that at
   pass level with
   `test_the_unit_after_a_person_s_mint_stays_unclaimed_while_staging_holds_the_marker` in
   `tests/rotation_worker/test_worker.py`, which asserts that no orchestrator call is made. With
   the old `staged = staging` restored, that test fails: the unit is claimed and then fails.
2. **Red run:** 17 failed and 45 passed. The 2 `test_staged_token_proceeds` cases passed, as
   expected, because they describe how the code already behaved.
3. **Implementation:** described in the preceding section. After it, `test_inbox.py` passes
   62 of 62 and `tests/rotation_worker` passes 1530.
   - Mutation check: I disabled the re-read guard in `_stage`, and the race test failed. I made
     `_stage` call `create` instead of `write`, and the bind-into test failed. I cleared
     `__pycache__` around each mutation and restored the file afterwards.
4. **`make check`** on the final tree, in the worktree against `orchestrator_test_hq_w_117`, with
   `SECURITY_STANDARDS_DIR` pointing at the test fixture and `PGTZ=UTC`, after
   `alembic upgrade head`:
   - Exit 0, with ruff and pyright clean.
   - **14736 items collected, 14735 passed, 1 skipped.** The run before the pass-level test
     had 14735 collected and 14734 passed.
   - CI `Quality` on `1684437a` (run 43331489527) succeeded: 14736 items collected, 14713
     passed, 23 skipped.
5. **Commit, push, and PR:** I committed `fix(rotation_worker): a staged empty marker isn't a
   staged value`, pushed, and opened draft PR #682, which cites plan Task 4 and the design
   section.

## Files changed

- `src/rotation_worker/inbox.py`
- `src/rotation_worker/worker.py`
- `tests/rotation_worker/test_inbox.py`
- `tests/rotation_worker/test_worker.py`
- `src/rotation_worker/CLAUDE.md`: the person's-mint rule now says that a marker, blank, or
  padded staging secret isn't staged, and that a bound value is written into that secret.
- `docs/superpowers/reports/rotation-worker-staged-empty-report.md`

## Self-review

`/code-review` (medium) raised 7 findings. I changed one, and the rest are either outside this
task or don't hold:

- **Fixed:** the race comment in `_stage` claimed more than the code guarantees. The re-read
  narrows the window, and a paste between the re-read and the write is still overwritten. The
  comment says so now.
- **Outside this task's scope:** the other readers that treat any staging secret as staged. See
  the first concern.
- **Doesn't hold:** the claim that the Keychain `read_key` returns `"empty\n"` as a value.
  `Value.__init__` refuses padded text in every store, so `pasted` returns `None`, and the
  `keychain` test cases cover this.
- **Accepted:** `_mint_waits` now reads the staging value on every pass in which a staging secret
  exists. A `StoreError` from that read propagates exactly as one from `find` already did, which
  is the handling the handoff asks to keep (Code Brain rule 4). The cost is one more BWS read per
  pass, and only for a unit that is about to claim.
- **Accepted:** the race refusal goes through the ordinary `Refusals` path, so a person hears of
  it only on a second failure. Pass N+1 sees the hand-staged value and runs `_tidy`, which already
  handles the case.

## Concerns

- These other `find(...) is not None` sites still treat a marker staging secret as staged. They
  are outside the mint wait and I left them as they were:
  - `cli.py:318`: `rotation-worker stage` refuses with "already staged" while a person's
    half-made secret exists.
  - `checks.py:194`, `_check_issue_fact`: on an issue plan whose keeper holds a value, it reads
    the marker staging secret and can raise "an issue runs only on a keeper holding only the
    marker". That check runs in `check_unit`, before `_mint_waits`.
  - `contract.py:476`, `UnitRun.staged`: this is used only after the mint wait has passed, so a
    marker can't reach it through the person-mint path.

  If HQ wants the same rule there, `pasted` is the helper to reuse.
- On BWS, a real value pasted into staging with a trailing newline counts as staged, because
  `read_key` strips it. The store step's `read` then raises `ValueError` for it. This is
  unchanged: before, `find` alone already counted it as staged.
- Not done, per the handoff: no deploy, no VM executor update, and no `docs/operations/` change.
  HQ updates the VM executor after merge.

## Follow-ups

- Optional: apply `pasted` at `cli.py:318` and `checks.py:194`, as described in the first
  concern.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** I added no log line. The existing
  `LOG.info("unit_waiting", extra=...)` lines keep their form and reasons ("nothing staged yet",
  "the inbox is empty").
- **Code Brain rule 4 (never swallow an exception silently):** I added no bare `except`.
  `pasted` catches only `ValueError`, which is how the existing `Inbox.read` already reads "no
  value", and the catch has a comment. A `StoreError` from the new read propagates as one from
  `find` did.
- **Code Brain rule 5 (never log secrets):** the staged value is compared only, with
  `reveal() == EMPTY`, `same_as`, and `is not None`. It appears in no log, exception, `Wait`
  reason, alert, or refusal text, and the new refusal reason names no value. Tests assert on
  outcomes (`waits`, due keys, `same_as`, secret counts) and print no values.
- **Infra Brain 591, 592, 593:** no BWS token appears in tracked files or history, and there is
  no inline bootstrap token. Test values are obvious placeholders (`the-new-value-for-tests`,
  `a-value-staged-by-hand`, `empty`) that don't match the token shape.
- **Infra Brain lesson 450 (rotation landmines):** no secret value appears in the plan, a test
  fixture name, or a log. Fixture and test names describe states ("staged marker", "staged
  token") and carry no values.
