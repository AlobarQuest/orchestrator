Outcome: done

# ADR-0059 Amendment 15 (D-70): build report

Worker W-234, branch `hq/adr-0059-a15-d70` from `origin/main` at `67e8d959`. Docs only. No live
call (no infraops, Coolify or BWS), no production act, no merge.

## What I built

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`, lines 4387-4493:
  `## Amendment 15 (2026-11-19): change-window's production Coolify token is a dedicated
  team-wide token (D-70 A)`, appended after Amendment 14 (lines 4003-4386, unedited). It holds:
  - The D-70 question and recorded answer, quoted verbatim from sds-hq `state/decisions.md` at
    `8cc39b0`, line 13.
  - Context: Amendment 14 left the token to D-70 (keeping its hedge on what its sources show;
    the flat "team-wide" premise is attributed to D-70's question); the title "on its own narrow credentials" is no
    longer accurate for the Coolify token (#750 follow-up 10). The heading isn't renamed.
  - Decision item 1, the token: dedicated, this lane only, team-wide `read`, `write`, `deploy`, no
    `read:sensitive`, with the reach ADR-0055 line 341 records and probe 1's measurement (line
    525); separately revocable; sent only to `http://100.92.138.19:8000`; never reads
    `bbd71f41-…`.
  - Decision item 2, authority: the token (Devon's create-and-mint at the mint gate) and the lane
    account's read grant on it (Devon's act at the identity gate), and nothing wider.
  - Decision item 3, the pin: before the security phase starts, the lane runs infraops at a
    pinned commit; D-66 B is the shape on record, the design a later decision; doesn't block
    increment 8; not a verify item; tracked in sds-hq `state/goal.md` Queued, since
    `plan/year-plan.md` names no security phase (line 76 at sds-hq `5aede8b`).
  - Decision item 4: "Not a standing answer", quoted.
  - A "Passages this amendment changes" table superseding, by rows, Amendment 14's token
    bullet (4166-4184), item 7's preamble exception (4265-4267) and token bullet (4268-4273), the
    grant sentence (4307-4308), the verify items "D-70 is open" (4337-4341) and "The Coolify
    window token's permissions" (4342-4343), the D-66 revisit verify item (4359-4363, stands with
    the bound added), table rows 4372, 4373, 4375 and 4384, and the title (4003, not renamed).
- `docs/operations/deploy.md`, change-window cut-over step 2 (lines 1258-1271): names D-70 A's
  token in place of "a dedicated Coolify window token (D-68 B)" (#750 follow-up 8; the D-70 hold
  is moot now that the token is authorized), and adds the session's `M2M_TOKEN_EXECUTE` hash
  write (the JSON entry holding the hash, per change-manager `docs/operations/bearer-settings.md`),
  the change-manager restart and its verification, from Amendment 14 decision item 7
  (#750 follow-up 2). It fit in the same step.
- This report.

## Tests and checks

- `make check` (full) against `orchestrator_test_hq_w_234`, on the first draft before the report
  existed and before the review fixes (`PGTZ=UTC`, fixture
  `SECURITY_STANDARDS_DIR`): exit 0. Lint "All checks passed", pyright 0 errors, pytest 15,832
  passed, 1 skipped, 15,833 items collected.
- Security scanner from the repo root (`--category security`): 0 BLOCK, 2 WARN, 1 INFO.
- Line and quote checks, each at the commit named:
  - D-70 at sds-hq `8cc39b0` line 13: quoted verbatim (compared mechanically, whitespace
    collapsed), including option (C) "Something else"
    (closing #750 follow-up 12's paraphrase gap for this amendment).
  - ADR-0055 lines 341 and 525 are identical at `3585b4f9` and `67e8d959`; both say what the
    amendment cites.
  - Every Amendment 14 line in the table was read at `67e8d959` (Amendment 14 relative line + 4002).
    Two ranges differ slightly from the handoff's "about" figures: item 7's exception starts at
    4265 (the handoff said 4266), and the grant sentence is 4307-4308 (handoff 4306-4309; 4309
    is a different sentence that stands). Both say what the handoff says. Every quoted fragment
    in the table was matched mechanically against lines 4003-4386 (whitespace collapsed).
  - D-66's question and option B: Amendment 14 lines 4011-4023.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (+104)
- `docs/operations/deploy.md` (+12, -3)
- `docs/superpowers/reports/adr-0059-a15-d70-report.md` (new)

## Self-review

- Appends only; Amendment 14's text and heading are unchanged.
- Authority stays at the token and its read grant; the pin is stated as a requirement, not an
  authority, and not as an increment verify item.
- I added a row for Amendment 14's D-66-revisit verify item (stands, bound added), because
  decision item 3 bears on it. I also superseded item 7's preamble ("except the production
  Coolify window token, which it doesn't authorize"), which the handoff's 4266 citation covers.
- `deploy.md` step 2 is longer; it keeps one step because the hash write and restart are the
  session's part of the same mint gate.

## Concerns

- The pin's Queued entry was uncommitted when I first read it; HQ committed it at sds-hq
  `5aede8b` (line 76) before I opened the PR, and the amendment cites that commit.
- #750 follow-ups 1, 3 to 7, 9 and 11 are outside this handoff and remain open.
- Amendment 14's verify item "D-66's revisit" cites sds-hq `state/goal.md` at `68466c01` line 45
  for option B; I didn't re-read that commit, and Amendment 15 doesn't rely on it.

## Sources cited

- sds-hq `state/decisions.md` at `8cc39b0`, line 13 (D-70).
- orchestrator `67e8d959`: ADR-0059 Amendment 14 (lines 4003-4386); ADR-0055 lines 341 and 525
  (same text at `3585b4f9`); `docs/operations/deploy.md` change-window step 2.
- orchestrator PR #750 body, follow-ups 2, 8, 10 and 12.
- sds-hq `state/goal.md` at `5aede8b`, line 76 (Queued); sds-hq `plan/year-plan.md` at
  `8d6899a` (no security phase).

## Standards followed

- **Code Brain 3 (structured JSON logs) and 4 (never swallow an exception):** don't apply; no code.
- **Code Brain 5 (never log secrets, credentials, auth headers, tokens):** no secret value and
  no token-shaped example in any file; secrets are named by role or by UUID prefix with `…`
  (`bbd71f41-…`).
- **Infra Brain 591 (no token in tracked files), 592 (no token in git history), 593 (bootstrap
  token not inline):** no token written; the scanner reports 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** doesn't apply; nothing was exposed.
