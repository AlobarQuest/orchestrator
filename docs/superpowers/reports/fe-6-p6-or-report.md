Outcome: done

# FE-6 P6-OR report

PR: https://github.com/AlobarQuest/orchestrator/pull/722 (branch `hq/fe-6-p6-or`, base
`4644eb1e`). Held: the title and the body's first line say `hold: merge inside FE-6 step 13`. I
didn't merge it or enable auto-merge.

## What I built

The plan's section "3. P6-OR" (`docs/superpowers/plans/2026-11-15-fe-6-mac-db-retire.md`, lines
202-218), exactly:

- I checked `docs/operations/credentials.md` lines 392-393 at `4644eb1e` first. They held the
  handoff's text: "Never read `~/.factory/env` for these settings: the nightly sources it under
  `set -a`, and it carries `FACTORY_DB_DSN`."
- I dropped ", and it carries `FACTORY_DB_DSN`". The sentence ends "…sources it under `set -a`."
  and fits on one line (86 characters, inside the page's prose width of about 100 characters), so the paragraph
  is one line.

No ADR changes, as the plan directs.

### Other `docs/operations/` sentences

I ran `grep -rn 'FACTORY_DB_DSN' docs/operations/` after the edit: no hits. No other sentence
under `docs/operations/` says `~/.factory/env` carries `FACTORY_DB_DSN`, in any tense.

A wider `grep -rn 'factory/env' docs/operations/` found one other mention,
`docs/operations/factory-events-chain.md:106`: "so a value exported by your shell or by
`~/.factory/env` can't reach the child". It names no `FACTORY_DB_DSN` and stays true after step 9,
so it isn't a hit; I didn't edit it.

## Tests and checks run (with counts)

Docs only, so no `make check`, per the handoff. PR #722's CI on the edit commit:

| Check | Result | Duration |
| --- | --- | --- |
| `Quality` | pass | 16m20s |
| `Rollout transcription currency` | pass | 14s |
| `Runner consumer compatibility` | pass | 18s |

The `Quality` job's log (run `43758788557`, job `132948655301`) shows `4 workers [15000 items]`
collected and `14977 passed, 23 skipped, 5 warnings`. Committing this report reruns CI; the report
records the edit commit's run.

## Files changed

- `docs/operations/credentials.md`: one sentence (two lines become one).
- `docs/superpowers/reports/fe-6-p6-or-report.md`: this report.

## Self-review

The diff is one sentence. The remaining sentence is still the reason the reader must not read
`~/.factory/env`: the nightly sources it under `set -a`, so anything in it is exported. That holds
after step 9.

## Standards followed

- **Code Brain 5 (BLOCK), never log secrets:** no value appears in the page, the PR, the commit or
  this report. I read no secret and no `~/.factory/env*` contents.
- **Code Brain 4 (BLOCK), never swallow an exception:** not applicable; no code changed.
- **Code Brain 3 (BLOCK), structured JSON logs:** not applicable; no code changed.
- **Infra Brain 591, 592, 593 (BLOCK), no BWS token in a tracked file or history:** the change
  names no token and no token shape.
- **Infra Brain 1032 (BLOCK), rotate a credential seen in a transcript:** no credential value
  appeared in this session, so there's nothing to rotate.

## Concerns

None. I ran no `docker`, `bws`, `security`, `make install`, lane or nightly command, and didn't
touch the main tree.

## Follow-ups

- Act step 13: merge PR #722 after step 9. The merge commit's CI reruns on `main`.
