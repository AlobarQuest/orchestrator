Outcome: done

# Report: adr-0055-accept-amend-19

## What I built

PR #628 (draft), "ADR-0055 amendment 19: accepted", branch `hq/adr-0055-accept-amend-19`.

- Base: `d3c915010c55a553ee6452682f5faa61d7449d1a` (`origin/main` after `git fetch origin`).
- Head: `742245b9aa036a6d087e924bd2459f72caebaac1`.
- Two commits. The first sets the status and the ADR-0059 references. The second replaces a
  placeholder with PR #628 after I opened the PR, so the ADRs cite the accepting PR by number
  instead of a SHA or a guessed number.

Edits:

- ADR-0055, line 27 ("Amended" list): amendment 19 is "accepted", not "proposed". The handoff named
  only the status line, but this index line also states amendment 19's status, so I changed it to
  match.
- ADR-0055, lines 2187-2189: `**Status: Accepted (2026-11-07).**` plus one sentence giving the bar:
  a clean Opus whole-branch review of PR #626 at `f17ee687` and of PR #628, under HQ's standing
  answer of 2026-11-06 (limit 3). The line "ADR-0059 increment 2 waits on it" is kept.
- ADR-0059, lines 943-945 (increment 2): "(proposed, merged at `d485d051`)" becomes "(merged at
  `d485d051`, accepted in PR #628)". The sentence is rewrapped, with no other wording changes.
- ADR-0059, lines 1408-1409 (Dependencies, ADR-0055): the same change.
- ADR-0059, lines 1706-1707 (Amendment 2, "Decided by"): "(Proposed, merged at `d485d051`, PR
  #626)" becomes "(merged at `d485d051`, PR #626, and accepted in PR #628)".

I left Amendment 2's own status line (`Accepted on merge`, 1703) unchanged. It doesn't depend on
amendment 19's acceptance. `grep -n -i 'amendment 19'` in ADR-0059 shows no other sentence that
calls amendment 19 proposed or makes Amendment 2 conditional on it. `docs/roadmap.md` doesn't name
amendment 19.

`git diff d3c91501 --stat`:

```
 ...55-credential-rotation-runs-in-a-pull-based-executor.md |  7 ++++---
 ...scheduled-lanes-run-as-pinned-containers-off-the-mac.md | 14 +++++++-------
 2 files changed, 11 insertions(+), 10 deletions(-)
```

## Tests and checks

- Local `make check` against `orchestrator_test_hq_w_42` (migrated to head, `PGTZ=UTC`,
  `SECURITY_STANDARDS_DIR` set to the fixture): exit 0, **13,925 passed, 1 skipped**. I ran it
  before the final rewrap of ADR-0055 line 2188-2189 and the PR-number substitution. Both are prose
  changes to the same lines.
- CI on head `742245b9`, run 42395008384: `Quality`, `Runner consumer compatibility` and `Rollout
  transcription currency` all succeeded. The earlier run on the first commit (`fe8bf5bb`) was
  cancelled when the second push superseded it.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`
- `docs/superpowers/reports/adr-0055-accept-amend-19-report.md` (this report, uncommitted)

## Self-review

- Each edit only changes how amendment 19's status is stated. No decision, verify item or other
  wording changed. The rewraps keep lines within the files' width of about 100 columns.
- I didn't run `/code-review`: the diff is status prose, and HQ runs the Opus review that the bar
  requires.

## Concerns

- The handoff says D-24 to D-29 are recorded in the amendment. ADR-0055 amendment 19 ("Questions
  for Devon. None open: D-24 to D-28") and ADR-0059 (lines 9, 1415 and 1705) record D-24 to D-28,
  and no ADR in `docs/decisions/` mentions D-29. Either the handoff's range is off by one, or a
  D-29 exists in HQ's log that the ADRs don't record. I didn't change anything about it.
- The ADR-0055 index line (27) was outside the handoff's literal item 1. I changed it because it
  states amendment 19's status and would otherwise contradict the status line.
- Teardown: the test database `orchestrator_test_hq_w_42` and this worktree remain for HQ to
  remove after it merges.

## Standards followed

- **Code Brain rule 3 (JSON logs to stdout):** doesn't apply. The diff changes ADR prose only and
  adds no logging.
- **Code Brain rule 4 (no silent exception swallow):** doesn't apply. No code changed.
- **Code Brain rule 5 (never log secrets):** the diff and this report contain no secret value or
  token. They cite only PR numbers and commit SHAs.
- **Infra Brain rules 591, 592 and 593 (no BWS token in tracked files or history; bootstrap token
  never inline):** the diff contains no token of any kind and names no secret.
- **Infra Brain rule 2 (no secrets in git):** the diff adds no secret to git. Neither commit
  touches a credential, env file or manifest.

## D-30 recording

Worker W-43, on HQ's handoff `adr-0055-accept-amend-19-d30`. Started from
`742245b9aa036a6d087e924bd2459f72caebaac1` (PR #628's head, fast-forwarded from `d3c91501`). This
report was untracked in W-42's worktree and not on the branch; I copied it unchanged and added
this section, so it's committed with the D-30 change.

It answers the one blocking finding of HQ's Opus whole-branch review at `742245b9`: item 3 ran
`verify_new` on every issue plan and said "Every other decision 3 invariant holds", but R14 needs
every probe beside a refused known-bad control, `_verify_new` is a live probe
(`rotation_worker/contract.py`, lines 561-566), and for a ping URL a probe is a ping, which item 5
forbids. Devon accepted the departure (2026-11-07, HQ D-30: "Yes").

Lines changed, in `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` at
my head:

- Item 3, lines 2248-2252: "Every other decision 3 invariant holds" now names the one exception:
  for a `healthchecks-ping-url` issue, `verify_new` is item 5's shape check, with no probe and no
  known-bad control, a departure from R14 that Devon accepted (HQ D-30), pointing to item 5. The
  following sentence is rewrapped, with no wording change.
- Item 5, lines 2291-2294: the issue plan's `verify_new` for a ping URL repeats the shape check
  and sends no ping; it departs from R14 for ping URLs only (D-30); cut-over step 4 is the proof.
  The citation matches the D-28 line's style.
- "Open before building", "Questions for Devon", lines 2471-2472: "D-24 to D-28" becomes "D-24 to
  D-28 and D-30". This is the amendment's one list of recorded decisions. It has no list of
  departures; the D-27 departure is cited inline only (items 3 and 7).

No status line, follow-up, verify item or other wording changed. `git diff --word-diff` shows only
these insertions.

Checks:

- `grep -nE '0\.[0-9a-f]{8}-'` on ADR-0055: no match. No ping URL, UUID-bearing URL or token
  appears in the diff.
- Every changed line is within the file's 100-column width.
- `make check` against `orchestrator_test_hq_w_43` (migrated to head, `PGTZ=UTC`,
  `SECURITY_STANDARDS_DIR` set to the fixture): exit 0, **13,925 passed, 1 skipped**
  (ruff and pyright clean). It ran on the ADR edits, before this report was added.

D-30 concerns:

- No D-29 appears in ADR-0055, so the recorded list now reads "D-24 to D-28 and D-30". If D-29
  exists in HQ's log and belongs in this amendment, it isn't recorded.
- I found no other sentence in amendment 19 that is false against `_verify_new` or a recorded
  decision. I read items 1 to 7, "Open before building" and "Consequences"; I didn't re-check every
  code citation in the amendment.

## Standards followed (D-30 recording)

- **Code Brain rule 3 (JSON logs to stdout):** doesn't apply. ADR prose only; no logging added.
- **Code Brain rule 4 (no silent exception swallow):** doesn't apply. No code changed.
- **Code Brain rule 5 (never log secrets, credentials, auth headers, tokens):** the diff names the
  ping URL only by its class and shape. It contains no real ping URL, UUID-bearing URL or token.
- **Infra Brain rules 591, 592 and 593 (no BWS token in tracked files or history; bootstrap token
  never inline):** the grep for the token shape on ADR-0055 found nothing, and the diff names no
  secret.
- **Infra Brain rule 1032 (`cred.exposure-rotate`):** the leak path (revoke and reissue, item 7)
  is unchanged.
- **Infra Brain rule 212 (HTTPS):** doesn't apply; no endpoint changes.
