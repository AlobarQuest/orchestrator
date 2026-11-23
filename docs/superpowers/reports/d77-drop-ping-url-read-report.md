Outcome: done

# D-77 C: drop Amendment 29's `ping_url_read` exit check

## What I built

A docs-only change recording Devon's answer to D-77 (option C) in ADR-0055 Amendment 29 item 5.

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`:
  - Removed the bullet "A ping-URL listing logs `ping_url_read`." from item 5's list (was line
    4257). Every other bullet stands.
  - Added a dated note under the list, lines 4263-4270 ("2026-11-23, D-77 C: ..."). It says the
    check is dropped and why: `ping_url_read` is logged only inside an issue's mint step
    (`src/rotation_worker/worker.py`), a `healthchecks-ping-url` has no rotation plan once issued
    (`src/rotation_worker/plans.py`), and all 17 ping-URL rows in `.cred-consumers.toml` are
    issued. It names the G-91 evidence that stands in its place and the next new lane's first
    ping-URL issue on the host as the natural proof, its mint step failing visibly if the read
    fails.
- `docs/operations/rotation-executor.md`: no change. The cut-over step (line 634) says "Run
  amendment 29 item 5's checks" and lists none of them, so it follows the ADR. Line 1630 describes
  what the executor logs during a mint and stays, as the handoff says.

I checked the cited facts before writing them: `plans.py` (the `Capabilities` docstring, "a ping
URL has none"), `worker.py` 743-772 (the read runs only for a unit with a `MINT` step and logs
`ping_url_read` at 769-772), and `.cred-consumers.toml` (34 `healthchecks-ping-url` lines, a
`class` and a `provider` line per row, so 17 rows). I didn't verify that every row is issued; that
fact is HQ's, from the handoff.

## Stop-point checks

- Item 5's text matched the handoff's description exactly (the bullet was at line 4257).
- No test or fixture pins item 5's text: `git grep` for "A ping-URL listing logs" found only the
  ADR, and the fixtures that name ADR-0055 cite amendment 15 only.

## `git grep -n ping_url_read docs/` results

| Hit | Exit check? | Changed |
|---|---|---|
| `docs/decisions/0055-...md:3172` (amendment 23's description of the log line) | No | No |
| `docs/decisions/0055-...md:4257` (item 5's bullet) | Yes | Removed |
| `docs/decisions/0055-...md:4263` (the new note, quoting the dropped check) | No, records the drop | Added |
| `docs/operations/rotation-executor.md:1630` (what a mint logs) | No | No |
| `docs/superpowers/reports/adr-0055-amendment-23-ping-url-from-api-report.md:186` | No | No |
| `docs/superpowers/reports/lanes-inc-3-deploy-watcher-rows-report.md:181` | No | No |
| `docs/superpowers/reports/ping-url-machine-read-report.md:55, 155, 264, 339, 350` | No | No |

No other doc names `ping_url_read` as an increment 7 exit check.

## Tests and checks

The change is docs-only, but I ran the full gate anyway, against my own database
`orchestrator_test_hq_w_284` (migrated with `alembic upgrade head`):

- `make check`: exit 0. `ruff check` all checks passed; `ruff format --check` 1045 files already
  formatted; `pyright` 0 errors, 0 warnings; `pytest` 14768 passed, 1 skipped.
- `uv run --project ~/Developer/code-standards code-standards risk --repo .`: `normal`. No
  vendor review needed.
- `/code-review`: skipped as a docs-only change (CLAUDE.md lets docs edits skip it); I re-read
  the diff myself.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (+9, -1)
- `docs/superpowers/reports/d77-drop-ping-url-read-report.md` (this report)

## PR

https://github.com/AlobarQuest/orchestrator/pull/800 (open, not merged)

## Self-review

The note keeps to one paragraph and follows the ADR's prose register. It cites files without line
numbers, so later code edits don't make it stale. It changes no other amendment, no code, and not
`.cred-consumers.toml`.

## Concerns

- The insertion shifts every later line of ADR-0055 by 8. Amendment 31's table (lines 4586-4587)
  cites Amendment 29 text at "lines 4246-4247" and "lines 4252-4253". Those references were
  already off before this change (the cited text was at 4286 and 4292 at `HEAD`, now 4294 and
  4300). I left them alone, because the handoff forbids editing any other amendment. HQ can decide
  whether line citations in that table need a refresh.

## Standards followed

- **Code Brain 3 (logging, structured JSON to stdout):** no logging changes; the change is docs
  only.
- **Code Brain 4 (error handling, never swallow an exception):** no code changes.
- **Code Brain 5 (never log secrets):** nothing is logged; the note names no secret value, only
  file paths and the log event's name.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files, history, or inline):** the diff
  holds no token and no secret id.
- **Infra Brain 1032 (`cred.exposure-rotate`):** not touched; no credential was exposed or used.
- **Infra Brain 212 (HTTPS):** doesn't apply; no endpoint changes.
