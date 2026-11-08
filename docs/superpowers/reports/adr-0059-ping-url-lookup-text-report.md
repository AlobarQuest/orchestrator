Outcome: done

# Report: ADR-0059 ping-URL text matches ADR-0055 amendment 21

- **Branch:** `hq/adr-0059-ping-url-lookup-text`, based on `origin/main` at `50dd4d52`.
- **Pull request:** #633, opened as a draft. It isn't merged or marked ready.
- **Content head:** `963c621f96cb547e525b5e5e1bde7306e40e4c0d`. The PR number was taken at this
  head. This report is committed on top of it.

## What I built

ADR-0059 now agrees with ADR-0055 amendment 21: a pasted ping URL binds by its shape and by the
read-only key's lookup of its check beside a random UUID's, never by a ping. Each fixed place
points at amendment 21. Each place also says that cut-over step 4 is still the only proof of the
full URL, because the lookup reaches only the UUID's first half. Where the ADR records D-28, the
record stays, with a note that D-31 C superseded it.

Changes in `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (line
numbers after the change):

1. Header `Decided by`, lines 11-12: adds that D-31 C, recorded in ADR-0055 amendment 21,
   superseded D-28.
2. Decision 3, the "narrowest credential" bullet, lines 705-713: replaces "binds the paste by its
   shape alone, never by a ping (Amendment 2; D-28)" with the shape-plus-lookup bind. The
   paragraph is rewrapped; the rest of its words are unchanged.
3. Increment 2's answered verify item, lines 985-989: the same replacement, and adds the
   `unique_key` lookup beside the refused random UUID.
4. Cut-over step 4, lines 1155-1167: "the proof of the ping URL, which the executor bound by its
   shape alone (D-28, ADR-0055 amendment 19 item 5; ...)" becomes "the only proof of the full ping
   URL", with the lookup bind and amendment 21. The step is rewrapped; the comparison rules are
   unchanged.
5. "Devon's decisions" introduction, line 1423: adds that amendment 21 records D-31 C, which
   superseded D-28.
6. Amendment 2 "Why", lines 1730-1732: keeps D-28's record and adds that D-31 C later superseded
   it.
7. Amendment 2 "What changes" item 3, lines 1786-1788: keeps the D-28 answer and adds that
   amendment 21 replaced it.
8. Amendment 2 "What changes" item 7, lines 1816-1817: adds that under amendment 21, step 4 is the
   proof of the full URL.
9. Amendment 2 "Verify in increment 2", lines 1824-1826: marks the ping URL's check read as
   answered by amendment 21. Amendment 21's context names that open item as the read that led to
   D-31.

`docs/operations/rotation-executor.md`, line 1313: the 105-character line is wrapped onto lines
1313-1314. The words are unchanged.

## What I searched for and found

I searched the whole ADR and `docs/operations/deploy.md` for `shape`, `D-28`, `D-30`, `D-31`,
`by its shape`, `shape alone`, `lookup`, `unique_key`, `R14`, `known-bad` and `ping URL`.

- Every stale bind statement is in the preceding list. `D-30` and `D-31` didn't appear in ADR-0059.
- Line 683 ("The read-only key can't replace the Management key for the lookup") stays. It is about
  `sds_deadman_resolve`'s lookup by name that takes `ping_url`, which the read-only key still can't
  do, because no response to it carries `ping_url` or `uuid`. It isn't a bind statement.
- `docs/operations/deploy.md` has no ping-URL bind statement. Its `shape` hits are about the
  registry build context (`shape_registry_context.py`) and observation summary shapes.

## Tests and checks

- No markdown or line-width check runs in CI: neither `Makefile` (`make check`) nor
  `.github/workflows/quality.yml` has one.
- Width: every added line is 100 characters or fewer (`git diff -U0 origin/main | grep '^+' | awk
  'length>101'` prints nothing).
- Security scanner from the repo root: exit 0, no BLOCK finding. The three findings that predate
  this change are WARN `bws.secret-files-gitignored`, WARN `bws.manifest-matches-usage` and INFO
  `bws.least-privilege-scope`.
- Consumers: nothing in `tests/`, `scripts/` or `docs/operations/*.toml` reads or hashes either
  edited file. `src/rotation_worker/worker.py` and some nested `CLAUDE.md` files name
  `rotation-executor.md` only as a pointer. `docs/superpowers/**` is in `layers.toml`, so this
  report needs no manifest change.
- I didn't run the full test suite. It needs Postgres and a migrated database, and the handoff
  waives it for a docs-only diff.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`
- `docs/operations/rotation-executor.md`
- `docs/superpowers/reports/adr-0059-ping-url-lookup-text-report.md` (this report)

## Self-review

- Each statement matches amendment 21's Decision items 1 and 2 and its first Consequence: shape,
  marker and no-other-keeper rules stand, the lookup is added, no ping is sent, and step 4 proves
  the full URL.
- No change to ADR-0055, to any status line, or to any ADR-0059 text other than the ping-URL
  statements and the D-28 records.
- No new decision was needed, so I wrote no question file.

## Concerns

- ADR-0059 has no amendment or changelog entry for this text fix, and the "Passages this amendment
  changed" list in Amendment 2 is unchanged. The commits and PR #633 are the record. HQ framed this
  as a text fix, not a decision, so I didn't add an amendment.
- The diff-scoped Stop hook lints changed code files; a docs-only diff gives it nothing to lint.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** docs-only diff. No code path or log
  changes.
- **Code Brain rule 4 (never swallow an exception silently):** docs-only diff. No code path raises
  or catches anything new.
- **Code Brain rule 5 (never log secrets, including the ping URL, its UUID and the `X-Api-Key`):**
  docs-only diff, with no logging. The text names no URL, UUID, `unique_key` or key value.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files or history):** the diff holds no
  token-shaped string, and the security scanner reports no BLOCK finding.
- **Infra Brain 2890 (never commit a ping URL; examples use a placeholder):** the diff adds no ping
  URL. The only ping-base string in it is `rotation-executor.md`'s existing
  `https://healthcheck.alobar.net/ping/` followed by the words "one lowercase UUID", rewrapped
  with its words unchanged.
