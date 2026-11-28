Outcome: done

# Report: record D-87 in ADR-0055 amendment 38, and request rotation of the four orchestrator bearers

PR: AlobarQuest/orchestrator#837, branch `hq/d48b-d87-orchestrator`, base `b5bd2cb6`. Change
commit `e14c8c44`; this report is a second commit on the same branch, so the PR head is the commit
after `e14c8c44`. Not merged.

## What I built

- **ADR-0055 amendment 38.**
  - The header entry (the 2026-11-28 amendment 38 line in the history list) now names D-87's
    answers.
  - `#### D-86's open items` quotes D-87 verbatim. It marks D86-A "Answered by D-87: (A), out" and
    D86-B "Answered by D-87: (A), both edits allowed", and keeps both options texts.
  - A new paragraph says where the edits land. The four orchestrator dates land in this PR.
    change-manager's four handed-over rows get theirs in a change-manager PR. The execute bearer's
    flag and date wait for HQ's D-84 condition 2 check and land together later, because a date on
    a row not handed over would make the proposer report it every pass.
  - The "Count" paragraph now reads "1 waiting on HQ's D-84 condition 2 check (D-87 (B))", and the
    18 ping URLs "stay out (D-87 (A))".
  - The status stays Proposed.
- **`.cred-consumers.toml`.** Each of the four rows has `rotate_requested = "2026-11-28"`, placed
  after `rotated_by_sds = true` and before the first consumer table, as the other rows carry it.

| Row | `last_rotated` key in row | Last rotation in infraops state | New line |
|---|---|---|---|
| `orchestrator-operator-bearer` | none | 2026-10-16 | 295 (added) |
| `orchestrator-observer-bearer` | none | 2026-10-21 | 340 (added) |
| `orchestrator-drift-reporter-bearer` | none | 2026-10-27 | 451 (changed in place from `"2026-10-21"`) |
| `orchestrator-factory-runner-bearer` | none | none | 547 (added) |

All four rows are `rotated_by_sds = true`.

### The due check

No registry row carries `last_rotated`. infraops' requested-date rule is in
`infraops-mcp-server/src/security-drift/cred-rotation.ts` lines 301-311, read-only in the local
checkout. A row is due when `now >= rotate_requested`, and its last rotation is either missing or
strictly earlier than the request. infraops takes the last rotation from `state.lastRotated[id]`,
or else from the row's `last_rotated`. `created` isn't consulted for a request, so the
factory-runner row reads as "never" and is due.

I read the last rotation dates from `~/.config/infra-drift/cred-rotation-state.json` (last
written 2026-11-19), and printed only the dates for these four ids. Limit: the rotation proposer
runs in a lane container, so the live state may be a copy on the VPS, which the handoff bars me
from reading. Amendment 38 records that neither type has rotated on the host since the move, so
no later rotation is expected. Every date is before 2026-11-28, so no stop point fired.

### Why one row changed in place

The drift-reporter row already held `rotate_requested = "2026-10-21"`. TOML allows a key only once
per table, so I couldn't add a second copy. That request was spent: the row rotated on
2026-10-27. The handoff's "one line, nothing else" still holds: the key appears once, and its
value changed.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_342` (migrated with `alembic upgrade head`,
  `PGTZ=UTC`): exit 0. 15,196 items collected, 15,195 passed, 1 skipped. pyright reported 0
  errors.
- The modified file parses with `tomllib`, and all four rows read back `rotate_requested =
  "2026-11-28"`.
- A grep of `tests`, `scripts` and `src` found no test that reads these rows' fields from the
  registry. The matches are fixtures with their own values.
- Security scan (`security_scan.cli . --category security`, from the repo root): 0 BLOCK, 2 WARN,
  1 INFO.
- `code-standards risk --repo .`: `normal`, so `vendor-review` wasn't needed. I didn't run
  `/code-review`: the diff is one config value per row and ADR prose, and CLAUDE.md lets trivial
  changes skip it.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- `.cred-consumers.toml`
- `docs/superpowers/reports/d48b-d87-orchestrator-report.md` (this report, committed as #835 did
  its report)

## Self-review

- The D-87 quote matches the handoff's text word for word.
- No `[[credential.exposure]]` entry was added, and no text calls a row exposed.
- No other field changed in the four rows. The diff for `.cred-consumers.toml` is 4 insertions and
  1 deletion.

## Concerns

These passages still describe D86-A and D86-B as open, or are stale. They are outside the
handoff's scope, so I left them:

- Amendment 38, "The rows in the pass": the opening sentence ("if Open item D86-B lets its flag be
  set"), the operator row's Blocker cell ("The due trigger (Open item D86-B)"), and the execute
  bearer's Blocker cell ("Open item D86-B (its flag)").
- Amendment 38, "What the pass leaves out": two bullets end "Open item D86-A". One is the ping
  URLs bullet; the other is `sds-hq-healthchecks-ping-url`.
- Amendment 38's opening paragraph says the amendment "changes nothing that runs: no registry
  row". That still describes the amendment, since every registry change is its own PR, as this
  one is.
- `.cred-consumers.toml`: the observer row's comment ends "Request cleared.", which describes the
  2026-10-21 rotation. Read beside the new request, it might mislead.

HQ can fold these into a later edit of amendment 38.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply; the change has no code.
- **Code Brain rule 4 (never silently swallow an exception):** doesn't apply; the change has no
  code.
- **Code Brain rule 5 (never log secrets, credentials, auth headers, tokens):** every credential
  is named by registry id. The rotation state read printed only dates for four ids, and no value
  entered the transcript, the diff, or this report.
- **Infra Brain 591 `bws.no-token-in-tracked-files` and 593 `bws.bootstrap-token-not-inline`:**
  neither changed file contains anything token-shaped. The security scan reports 0 BLOCK.
- **Infra Brain 592 `bws.no-token-in-git-history`:** the branch's commits carry only the two
  changed files and this report, with no token. The same scan covers history and reports 0 BLOCK.
- **Infra Brain 1032 `cred.exposure-rotate`:** a `rotate_requested` date isn't an exposure entry.
  None was added, and no row is called exposed.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; no endpoint changed.
