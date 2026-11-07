Outcome: done

# Report: lanes-inc-2-adr-0055-amendment-fixes-2

Worker W-38. Branched from PR #626's head `22727b94`. The ADR change is commit `b06cda17`; this
report is the following commit. Both are pushed fast-forward to
`hq/lanes-inc-2-adr-0055-amendment`. Line numbers are in
`docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` at `b06cda17`. Only
amendment 19 changed.

## What I built

### Blocker 1: a failed `--token` run is never closed

- Item 6 gains a sub-bullet, "How a failed `--token` run closes" (lines 2313-2320). A mismatch
  leaves the run `unfinished`, which refuses the lane's next `lanes-prepare` and every release
  (`require_lanes_current`). `--token`'s compose is the one the lane already ran, so its
  `lanes-finish --restored` form needs no inverse write. It closes the run as failed, with the
  mismatches recorded as its reason in the run file, and the run never becomes the lane's record.
  After that the lane can take a retry `--token`, a `--tag`, or a rollback to the Mac. The revoke
  stays unresolved.
- That form is carried in the increment-2 `--token` verify item (line 2452).
- Source checked: `_lanes_finish` and `_lanes_restored` (`scripts/deploy_orchestrator.py`, lines
  1739-1781), the `lanes-prepare` refusal (lines 1634-1642), and `require_lanes_current` (lines
  1220-1236). The code matches the handoff.

### Blocker 2: increment 7 doesn't settle who redeploys

- Item 6 (lines 2300-2303) now says that ADR-0059 increment 7's amendment decides whether the
  executor runs the redeploy or hands it to the deploying session. I removed "increment 7 left
  that to the executor".
- Item 7 (lines 2342-2343): "If increment 7's amendment gives the executor the redeploy, it
  restores a machine read; otherwise that amendment states how R10 is met."
- Source checked: ADR-0059 lines 1007-1010.

### Devon's answers

- **D-24.** Item 1 (lines 2229-2232) states the keeper as decided, quotes Devon, and records it as
  a standing answer for every later lane token. I removed the transit-only paragraphs in items 3,
  4, and 7 and the conditional wording in item 8 (lines 2352-2354). Item 4 keeps one sentence on
  why its checks need the keeper (lines 2280-2281). Item 10's leak bullet cites D-24 without a
  condition (lines 2378-2379). The threat model (lines 2467-2472) states that D-24 accepts the
  value at rest.
- **D-25.** Item 10 (lines 2371-2375): the actor is the rotation proposer, and Devon's revision
  approval is the only person step.
- **D-26.** Item 10 (lines 2375-2376) cites D-26 for the revoke order. The bullets are unchanged:
  after a leak, Devon revokes first; otherwise he revokes only after `lanes-finish` and
  `lane-run --read` pass.
- **Questions for Devon** (lines 2460-2463) keep only D-27 (item 7's R10 departure) and D-28 (a
  Healthchecks key that identifies a check). Both stay proposed. Items 3, 5, and 7 cite those ids
  (lines 2264, 2290, 2344).

### Cheap follow-ups

1. Item 6 (lines 2305-2308): the command prints the calls and the deploying session makes them
   (`docs/operations/deploy.md`, "The lane phases", lines 147-148).
2. The `--token` verify item (lines 2450-2456) names the run's own `change_key`. It also names the
   `--tag`-after-`PATCH` case, which loads the new token without `--token`'s reads, so those reads
   must be re-run before the revoke. Finally, it says `--token` refuses `sds-lane-placeholder`.
3. A new increment-2 item (lines 2457-2459) asks whether a pass on a dead token leaves its check
   `started` and so holds off the leak's redeploy. The quiet-slot rule it cites matches
   `deploy.md` ("A `started` check whose `status` is also `down` doesn't hold it").
4. Item 6 (lines 2309-2311): the `lane-run --read` result is a new `lanes-finish` input
   (`finish_mismatches` checks digest, schedule, token presence, and volume), and increment 2's
   code settles its form. The verify item repeats it (line 2453).

## Tests and checks run

- `grep -nE '0\.[0-9a-f]{8}-'` over the ADR: no match.
- Line length over amendment 19: the only lines over 100 characters are the two pre-existing ones
  the handoff puts out of scope ("What the code settles", lines 2403 and 2405, unchanged).
- A grep for `transit-only`, `if Devon keeps`, and `left that to` in amendment 19: no match.
- `git diff --stat`: one file changed, the ADR.
- I didn't run `make check` or `/code-review`. The change is docs only, and no test reads this
  ADR's text.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (amendment 19 only)
- `docs/superpowers/reports/lanes-inc-2-adr-0055-amendment-fixes-2-report.md` (this report)

## Self-review

- The closing state for a failed `--token` run is "closed as failed, reason recorded". I didn't
  name a status value: whether it reuses `restored` or adds a new status is code, so increment 2
  settles it. `restored` means "back on the values the run replaced". That is true of the compose,
  but the executor wrote the token variable, so the container may now hold a new token. A distinct
  status would be the clearer choice.
- Amendment 19's status stays **Proposed**, because D-27 and D-28 are open.
- I didn't touch ADR-0059's own text. Its Consequences and increment-7 wording still differ from
  amendment 19, and HQ plans a separate amendment for that.

## Concerns

- None blocking. The status-name choice in the self-review is left to increment 2's code.

## Standards followed

- **Code Brain rule 5 (never log secrets).** Item 11's logging text is unchanged: UUID,
  destination, and fingerprint, never the value. The new text logs nothing.
- **Code Brain rule 4 (never silently swallow an exception).** A failed `--token` run ends in a
  recorded failure: the run file keeps the mismatches as its reason, and HQ records it in its
  report. It doesn't end in a skip. The revoke stays unresolved.
- **Code Brain rule 3 (structured JSON logs).** Item 11 still cites `rotation_worker/logs.py`.
- **Infra Brain 591, 592, and 593 (no token-shaped string).** The grep found no token-shaped
  string in the ADR. The report holds none either.
- **Infra Brain 1032 `cred.exposure-rotate`.** The leak path is unchanged: Devon revokes, then the
  executor reissues. Nothing is purged.
- **Infra Brain 212 (HTTPS).** Doesn't apply: no endpoint changed.
- **No Code Brain `secrets` road** exists. Nothing in this change needed one.
