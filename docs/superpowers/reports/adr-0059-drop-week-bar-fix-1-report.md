Outcome: done

# Report: adr-0059-drop-week-bar, fix round 1 (worker W-109)

- **PR:** #676, https://github.com/AlobarQuest/orchestrator/pull/676 (not merged)
- **Branch pushed:** `hq/adr-0059-drop-week-bar`, fast-forward on `8ab0dccc`
  (`git log 8ab0dccc..origin/hq/adr-0059-drop-week-bar` was empty before the push)
- **Content commit:** `afe47acf`. The report commit follows it.

## What I built

A fix for review finding B1 in ADR-0059 Amendment 6: the cut-over's Mac step is a bootout plus a
disable.

- New decision item 2: the reason (the 2026-11-12 08:02 restart reloaded
  `com.devon.execution-watch` and `com.devon.landing-ledger`, and the execution watch ran on both
  hosts), step 2's `launchctl bootout` then `launchctl disable` with the plist kept, step 4's check
  of `launchctl print-disabled gui/<uid>`, and rollback's `launchctl enable` before the bootstrap.
- Items 2 to 6 renumbered to 3 to 7; the table's references moved with them.
- Items 1 and 6 say "booted out and disabled". The Context's year plan quote is unchanged.
- Item 5 (lanes already cut over): such a lane is moved only once its Mac job is disabled and
  checked as step 4 checks; the execution watch and the landing ledger were disabled on 2026-11-12
  (interactive session, Devon's approval). Nothing about other lanes.
- Three table rows: 1151-1152, 1164, and 1190-1191, each superseded by decision item 2.
- "Decided by" adds Devon's approval of the disable.
- Both non-blocking notes applied (item 5's baseline wording; the 1248-1279 row's "consistent
  with").
- `docs/superpowers/reports/adr-0059-drop-week-bar-report.md` gains a "Fix round 1" section.

## Tests and checks

- `make check` at `afe47acf` with `orchestrator_test_hq_w_109` (`TEST_DATABASE_URL`,
  `ORCHESTRATOR_DATABASE_URL`, `SECURITY_STANDARDS_DIR` at the fixture, `PGTZ=UTC`,
  `alembic upgrade head` first): exit 0, 14677 passed, 1 skipped, pyright 0 errors.
- Security scan from the repo root: 0 BLOCK, 2 WARN (`bws.secret-files-gitignored`,
  `bws.manifest-matches-usage`), and 1 INFO (`bws.least-privilege-scope`), the same as round 0.
- `git diff 519bb23f -- <ADR>` has no removed line: the amendment still only appends.
- I re-read lines 1151-1152, 1164, 1190-1191, and 741-743 at `519bb23f`; each holds the text the
  handoff and the rows quote.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (Amendment 6 only)
- `docs/superpowers/reports/adr-0059-drop-week-bar-report.md` (Fix round 1 section)
- `docs/superpowers/reports/adr-0059-drop-week-bar-fix-1-report.md` (this report)

## Self-review

- I reflowed one wrap that put "1." at a line start inside item 5, which Markdown renders as a
  nested list, and kept new prose lines to 100 characters.
- The existing 1148 row still says "the boot-out ... stand(s)". It's accurate: the boot-out stands,
  and the new 1151-1152 row adds the disable. I left it, per "change nothing else".
- "launchd refuses to bootstrap a disabled service" is the handoff's statement; I didn't test it
  on the Mac, because that would mean changing a launchd service's state outside this task.
- I didn't run `/code-review`: the change is ADR text with no code, and HQ's Opus review gates the
  merge.

## Concerns

- None blocking. No standard was missing for this docs-only change.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply; docs only, no code.
- **Code Brain rule 4 (never swallow an exception silently):** doesn't apply; docs only, no code.
- **Code Brain rule 5 (never log secrets):** the diff names no secret value.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN` value):** none written; the scan from the repo root has 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value during this task.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; docs only, no endpoint.
- **Infra Brain 2890 (never commit a ping URL):** no ping URL appears in the diff.
