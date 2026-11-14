Outcome: done

# Report: adr-0059-inc-9-follow-up-minting (worker W-144)

- **PR:** #698, https://github.com/AlobarQuest/orchestrator/pull/698 (ready for review, not merged)
- **Branch:** `hq/adr-0059-inc-9-follow-up-minting`, based on `origin/main` at `5a781dbb`. On
  2026-11-14, `origin/main` had no newer commit to the ADR, and no open PR claimed Amendment 9.
- **Content commit:** `0c9735c2` (the amendment). The question file is commit `0b1f6486`. The head is the report commit, which can't name its own SHA.
- **Amendment's line range:** lines 2962-3048 of
  `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` at `0c9735c2`.

## What I built

I appended ADR-0059 Amendment 9 after Amendment 8, in the form of Amendments 3 and 6: Status (in
Amendment 6's wording, with Devon's D-41 words verbatim), Decided by (Devon, 2026-11-13, HQ D-41,
sds-hq `state/decisions.md` at `700944d`), Relates to, the line-number paragraph, Context, Decision,
"Verify in increment 9", and a supersession table. No earlier text is edited, and the ADR gets no
pointers or header edits.

The amendment makes the following decisions:

1. infra-drift stays on the Mac as Devon's infraops tooling, with all of its steps except minting
   and its current identity. It is outside the phase (D-41, as D-9 placed SDS HQ) and isn't a
   decision 4 Mac exception.
2. Increment 9 moves follow-up minting (`drift-audit.sh` lines 152-179 at infraops `c3359bf`) out
   of infra-drift into SDS, and the SYSTEM bearer `221a48d5-…` leaves with it. Increment 9's plan
   decides where minting runs and its done check.
3. The phase is done when increment 8 has moved change-window at its cut-over checks and
   increment 9 has moved minting at its plan's check. Decision 5's rule that no moved lane's
   input stays on the Mac stands.
4. The infrastructure breakout is named in one item as an add-on after Phase 1.
5. The amendment grants no new authority.

The verify item says that increment 9's plan puts the placement to Devon before any code merges
(standing answer of 2026-11-06, limit 1).

## Table rows (lines at `5a781dbb`)

| Passage | Disposition |
|---|---|
| Decision 5, the rotation proposer's `~/.config/infra-drift/` inputs (796-803) | Stands. The proposer's move still needs the list in a clone and a decided home for the state. infra-drift keeps reading the Mac copies. How the two share them is the proposer's increment's call. |
| "Agents outside this repository", the D-9 sentence (815-816) | Superseded for infra-drift; change-window still increment 8. |
| `change-window` row (822) | Stands. |
| `infra-drift` row, "Moves, increment 9" (823) | Superseded: stays, outside the phase. The minting parts stop describing it once increment 9 lands. |
| Increment 8's shared-identity sentence (1076-1077) | Stands. |
| Increment 9 (1079-1085) | Superseded. Its identity and security-scan questions are withdrawn, and the SYSTEM bearer question moves to its plan. The shared-input fact stands. |
| Week bar, increments 8 and 9's weeks (1292-1293) | Already superseded by Amendment 6; moot for increment 9. |
| Done-when paragraph, the infra-drift sentences (1296-1299) | Superseded by decision item 3; the rest stands. |
| Q7 (1522-1526) | Superseded for infra-drift; change-window stands. |
| Amendment 6, Relates to (2658) | Stands. |
| Amendment 6, decision item 4 (2694-2696) | Stands for 7 and 8. For 9, no Mac job is booted out, and its plan states its check. |
| Amendment 6 row (2734) | Stands; moot for increment 9. |
| Amendment 6 row (2735) | Superseded by decision item 3. |

## Tests and checks

- `make check` against `orchestrator_test_hq_w_144` (migrated with `alembic upgrade head`,
  `PGTZ=UTC`, fixture `SECURITY_STANDARDS_DIR`), run on a committed tree: rc 0, 14886 passed and 1
  skipped (the collected count), with ruff and pyright clean.
- Security scan from the repo root (`security_scan.cli . --category security`): 0 BLOCK, 2 WARN and
  1 INFO findings. The WARN and INFO findings existed before this change and are judgment items
  about manifest and account scoping.
- I grepped the diff for full UUIDs and found none. Secrets are named by truncated UUID only.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (appended)
- `docs/superpowers/reports/adr-0059-inc-9-follow-up-minting-question.md` (new)
- `docs/superpowers/reports/adr-0059-inc-9-follow-up-minting-report.md` (new)

## infra-drift mentions outside the ADR

No doc outside `docs/decisions/` says infra-drift moves in Phase 1, and no other ADR does (ADR-0002,
0054 and 0055 mention it for other reasons). These docs name infra-drift as the minting caller, and
increment 9 changes them; they're listed in the PR body:

- ADR-0007, "Scheduled trigger"
- `docs/operations/follow-up-scheduling.md` (line 87)
- `docs/operations/credentials.md` (line 90)
- `.cred-consumers.toml` (line 221)

## Self-review

- The passages came from the handoff's grep plus a wider one (`D-9`, `8 and 9`, `7, 8 and 9`,
  `those two`, `drift audit`), and I cross-checked each cited line against the file at
  `5a781dbb`. I left change-window (increment 8) as it is.
- I didn't pick a placement in the ADR. The options and my recommendation (a new single-purpose
  lane) are in the question file.

## Concerns

- sds-hq `plan/year-plan.md` line 56 (at `700944d`) still reads "no lane that acts on a member runs
  on the Mac" and names neither D-41's nor D-9's exclusion. ADR-0059's table says infra-drift acts
  on a member. The amendment follows D-41, but the year plan's text lags. I recorded this in the
  question file and didn't treat it as a contradiction, because D-41 is Devon's later decision.
- The handoff's standards don't cover the ADR-0007 loop-free posture. It bears on option C in the
  question file.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply; the change is docs only.
- **Code Brain rule 4 (never swallow an exception silently):** doesn't apply; the change is docs
  only.
- **Code Brain rule 5 (never log secrets):** the diff names no secret value. It refers to the
  SYSTEM bearer only as `221a48d5-…`, as the ADR does.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files or history, no inline
  `BWS_ACCESS_TOKEN` value):** the diff contains no token and no `BWS_ACCESS_TOKEN` value, and the
  scan found 0 BLOCK findings.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value while working, so I have
  nothing to report.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; the change is docs only.
