Outcome: done

# Report: ADR-0055 amendment 22, increment 2

PR [#660](https://github.com/AlobarQuest/orchestrator/pull/660) is open and ready, not merged.
The code head is `c505ffc8`. This report is committed in the commit after it, so the PR head is that
commit.

## What I built

`rotation-proposer standing-approval <credential_id>` is a read-only subcommand on the rotation
proposer's existing CLI. `rotation-proposer [--submit]` behaves as before, and
`--submit standing-approval` is refused by argparse (exit 2).

- **Module:** `src/rotation_proposer/standing_approval.py`, a new module. `standing.py` is
  unchanged. It holds one pure function per condition plus `evaluate`, which runs all six in
  order. It does no I/O, catches no exceptions, and approves nothing.
- **Command:** `standing_approval()` in `src/rotation_proposer/cli.py` does the following, in order:
  1. Reads the registry row with `find_credential`, through the proposer's mirror
     (`default_mirror_root("rotation-proposer")`).
  2. Syncs intent-packages once and reads `package.yaml` and `lineage.yaml` at that one commit,
     with `load_package`.
  3. Logs `standing_approval.read`, naming `<registry repo>@<commit>` and
     `AlobarQuest/intent-packages@<commit>`.
  4. Reads infraops' `last_rotated` through `read_due()`, the same `cred-findings` read a pass
     makes.
  5. Runs `evaluate` and logs one `standing_approval.condition` line per condition, then
     `standing_approval.verdict`, which lists every refused condition.
- **Exit codes:**
  - 0: no condition refused.
  - 1: at least one condition refused.
  - 2: anything raised, logged as `standing_approval.unreadable`. That covers a mirror failure, a
    missing or unparseable package or lineage, `FindingsError`, `GenerateError`, `ContractError`,
    and any unforeseen exception, through one outer handler as in `compare-destinations`.

  A `last_rotated` map that infraops doesn't serve, or one without the credential, raises
  `FindingsError` (exit 2), never null.

### Each condition and where it's checked

All the following functions are in `src/rotation_proposer/standing_approval.py`:

| Condition (logged name) | Function | What it checks |
| --- | --- | --- |
| `proposer_generated` | `proposer_generated` | `created_by == "rotation-proposer"`. `generate.render(entry, occurrence, created_at, first_issue=...)` uses the package's own `profile_fields.occurrence` and `created_at`, and the plan signal from `last_rotated`. The result equals the stored package in every top-level field except `status`. The reason names the differing field names only. |
| `unapproved_revision_1` | `unapproved_revision_1` | The package `revision` is 1. The lineage `package_id` matches, `revisions` has exactly one entry and it's revision 1, `approvals == []`, and `current_state == ready_for_review`. |
| `issue_plan` | `issue_plan` | `last_rotated` is null. `plan_for(entry, first_issue=...)` chooses the issue plan, and `select_plan(candidates(...), package.acceptance)` is the issue plan. |
| `lane_type` | `lane_type` | The row `class` is `bws-lane-token` or `healthchecks-ping-url` (`LANE_KINDS`), and the package destinations hold `provider name="<class>"`. |
| `destinations` | `lane_destinations` | `destinations.same` (the comparison `compare-destinations` uses) holds between the row's destinations and the package's (`standing_destinations`). The kinds multiset equals the type's: a token has one `provider`, one `bws-secret`, and one `coolify-env`; a ping URL has one `provider` and one `bws-secret`. No `coolify-env` has `redeploy` other than false. |
| `standard_authority` | `standard_authority` | `package.authority == generate.authority(adapter, entry.destinations, issue=plan.issue)`, compared whole: allowed, requires-approval, prohibited, and budgets. |

The re-render and the destination comparison use the same `RegistryEntry`, so the destinations are
read at the commit the re-render used. The type condition still names both types (amendment 23
doesn't narrow it here).

### One-off check against the real packages (read-only, not committed)

I ran `evaluate` locally on the two real packages: `git show` of intent-packages `d6440e5` and
`0d09201`, which only read objects. I compared them with this worktree's `.cred-consumers.toml`
rows, with `last_rotated` null. I used no credentials and no live run.

- **`landing-ledger-bws-token` (`d6440e5`):** all six conditions pass. The YAML-to-dict comparison
  holds on a real package.
- **`landing-ledger-ping-url` (`0d09201`):** the re-render differs in `acceptance` and
  `profile_fields`, and `select_plan` finds no plan, so the command would exit 2. That package
  predates amendment 23's generator change (#651: a ping URL's mint is the machine's, reading its
  check). It's the "Generator drift" case the amendment accepts, and it fails closed. It's already
  approved by `devon` (`c5c9225`), so the command isn't needed for it.

## Does every issue set `last_rotated`?

**No.** An issue sets infraops' `last_rotated` only through the proposer's record walk. That walk
records it only when all of the following hold:

1. **It's a `--submit` pass.** `_walks` returns before the record walk on a dry run
   (`src/rotation_proposer/cli.py:874`, `if reader is None`).
2. **infraops serves the `last_rotated` field.** An infraops predating it records nothing
   (`cli.py:675`).
3. **The record is `resolved` by `work-watcher`.** `_newest_retired` keeps only those
   (`cli.py:599`). A record a person resolved records nothing.
4. **The registry row still exists and sets `rotated_by_sds`** (`cli.py:642`).
5. **The rotation doesn't close an exposure.** If an exposure is open now, or the record's
   occurrence ends in `-exposure`, nothing is recorded (`cli.py:644`, `_closes_an_exposure`).
6. **The completion day is later than the known anchor.** A null anchor is always earlier
   (`_record_retired`, `cli.py:568-581`), so the first retirement of an issue records it.
   `record-rotation --date <day>` then moves `lastRotated`.

It's recorded on the next `--submit` pass after `work-watcher` retires the record, not at
completion. Between those two events, `last_rotated` stays null.

**What this means for the amendment's first case** (a package deleted and generated again for a
credential already issued):

- **When the issue's record was retired by `work-watcher`, outside an exposure, and a later pass
  ran:** `last_rotated` is dated, so this command refuses `issue_plan`. For a ping URL it exits 2
  instead, because `plan_for` has no rotation plan to choose.
- **When a person resolved the issue's record, or it's inside the window before the next pass:**
  `last_rotated` can still be null. The issue-plan condition doesn't catch a regenerated revision 1
  then. The executor's `checks._check_issue_fact` (`src/rotation_worker/checks.py:178`) still
  refuses the issue before the claim, because the keeper holds a value other than the empty marker
  and this rotation's staged value. Amendment 22 item 3 already relies on that for the carry form.

## Where increment 3 imports the checks from

The checks are in `rotation_proposer.standing_approval`. Its imports are
`rotation_proposer.generate` and `rotation_worker.{destinations, plans, registry, types}`. The
carrier's import closure already covers those `rotation_worker` modules (through
`rotation_worker.types`), and none of them reaches the network.

I tested the architecture guards. I added a temporary
`from rotation_proposer import standing_approval` to `src/work_carrier/prepare.py` and ran
`tests/architecture` and `tests/work_carrier`. Exactly one guard failed on the carrier import:
`tests/architecture/test_out_of_process_isolation.py::test_each_program_imports_only_its_row_and_the_orchestrator_imports_none_of_it[work_carrier]`,
because `rotation_proposer` isn't in `work_carrier`'s `TABLE` row. The other failure in that run
was this change's own `BORROWED` declaration, which I then made. `test_bridge_edges`,
`test_layering`, `test_layer_manifest`, `test_wsp21_invariant_scan`, and
`test_external_content_observes_only` passed. I removed the import by an inverse edit, and it isn't
in the diff.

**For increment 3:** the carrier may import these checks only after `"rotation_proposer"` is added
to `work_carrier`'s row in `test_out_of_process_isolation.py`, with a reason. I didn't change that
guard: it's increment 3's change, and the handoff forbids a guard change here. Placing the checks
anywhere else wouldn't avoid it. They need `generate.render`, `authority`, and `plan_for`, which
live in `rotation_proposer`.

**The carry form's two differences** are seams, not new code:

- It replaces `unapproved_revision_1` with its own lineage condition: one policy approval on
  revision 1, whose hash is the package's.
- It passes `first_issue=True` to `proposer_generated` and `standard_authority`, and doesn't call
  `issue_plan`'s infraops half.

## Tests and checks

- **`make check`** on `orchestrator_test_hq_w_87`, run from a committed tree: ruff passed, pyright
  found 0 errors, and pytest **collected 14,417 tests, 14,416 passed, and 1 was skipped**
  (10 xdist workers).
- **36 new tests** are in `tests/rotation_proposer/test_standing_approval.py`. The rows are the
  landing ledger's two real rows from `.cred-consumers.toml`, the package is `generate.render`'s
  output for each, and the registry and intent-packages are throwaway git origins read through the
  real `RepoMirror`. They cover the following cases:
  - The all-pass case for both types (exit 0, both commits named, six conditions).
  - Each refusal, through `cli.run`:
    - Failing alone: `proposer_generated` (an edited title, and `created_by: devon` with its own
      reason), `unapproved_revision_1` (five lineage edits), `issue_plan` (a dated lane token
      rendered for a reissue), `lane_type` (the provider names another type), and `destinations`
      (`redeploy=true`).
    - Named beside others: a non-lane class (`lane_type` and `destinations`), a second ping-URL
      copy, a registry that moved, a widened authority (`proposer_generated` and
      `standard_authority`), and package revision 2 (`proposer_generated` and
      `unapproved_revision_1`).
  - Each authority part compared whole, at function level.
  - Exit 2 for each of the following:
    - A credential not in the registry.
    - A missing package or lineage, and a package that doesn't parse.
    - No `last_rotated` map, or one without the credential.
    - infraops refusing.
    - A dated ping URL.
    - An injected `RuntimeError`.
  - `--submit` refused.
  - The no-value test: a placeholder sentinel is planted in three credential environment variables,
    the row's `note`, and an edited package field. It's absent from stdout and stderr on the exit
    0, 1, and 2 paths, and every line has exactly one of the four key sets.
- **Mutation controls:** I ran 13 mutants on the condition checks and the command's exit and read
  paths. All 13 were killed. The first run had two survivors: the `lane_type` class check (masked
  by its provider check) and the explicit `created_by` check (masked by the re-render). I
  strengthened both tests and re-ran both mutants, which were then killed.
- **Security scan** (security-standards scanner, `--category security`, from the repository root):
  0 BLOCK, 2 WARN, 1 INFO. Both WARNs predate this change: the `*-migration/` gitignore glob, and
  undeclared UUID `aaff45ca-…`, which isn't in this diff.
- **`/code-review` (medium) returned four findings:**
  - I fixed one: `_mirror()` duplicated the pass's mirror constructor, and the pass now uses it.
  - I left three as they are:
    - A `created_at` that YAML parses as a datetime refuses (exit 1), which fails closed. The real
      packages quote it.
    - A non-lane class is reported by both `lane_type` and `destinations`. Listing every failed
      condition is the specification.
    - The `read` line comes before infraops' read. It truthfully names the two commits read, and
      the verdict or `unreadable` line decides.

## Files changed

- `src/rotation_proposer/standing_approval.py` (new): the conditions.
- `src/rotation_proposer/cli.py`: the subcommand, `_parser`, `_log`, `_mirror`,
  `_served_last_rotated`, `EXIT_REFUSED`, and a module-docstring paragraph.
- `tests/rotation_proposer/test_standing_approval.py` (new).
- `tests/architecture/test_rotation_edges.py`: `BORROWED["rotation_proposer"]` gains
  `destinations.render` and `registry.{find_credential, lineage_path, load_package, package_path,
  standing_destinations}`. That's the declaration this guard requires of any new shared surface; it
  isn't a widening for the carrier.
- `docs/operations/rotation-proposer.md`: the new section "Check a lane credential's revision 1 for
  the standing approval".
- This report.

## Self-review

- I didn't touch the `standing.py` docstring ("IT NEVER APPROVES"), `src/rotation_proposer/CLAUDE.md`,
  the root `CLAUDE.md`, or ADR-0055.
- The command never writes. It reads public repositories through the mirror, without credentials,
  and runs infraops' read-only `cred-findings`.
- I didn't run it against the live registry or approve anything. I didn't touch
  `~/Projects/intent-packages` or the main tree; the only access to intent-packages was `git show`
  of two commits, to a scratch directory.
- `evaluate` doesn't short-circuit, so every failed condition is listed. An exception anywhere
  stops the evaluation and exits 2, so a partial list is never reported as a verdict.

## Concerns

- **"Fails alone" isn't achievable for every condition.** Unequal destinations, a changed
  authority, and a package revision other than 1 all change the re-render, so `proposer_generated`
  refuses beside them. The tests assert that both are named instead of carving fields out of the
  re-render. The authority comparison is also tested alone, at function level.
- **A dated ping URL exits 2, not 1.** `plan_for` raises for a ping URL infraops records issued,
  because the type has no rotation plan. That's still never a pass, and the docs say so.
- **The logging premise.** The handoff says the proposer logs structured JSON on stdout today. It
  prints text lines (`_report`). This command follows the standard (Code Brain rule 3): one
  `json.dumps` line per event, on stdout. I didn't reuse `rotation_worker.logs.configure`, because
  its `StreamHandler` writes to stderr.
- **Increment 3 needs one guard row** (`work_carrier`'s isolation row gains `rotation_proposer`).
  See "Where increment 3 imports the checks from".
- **The handoff's report header** offers `partial`. The worker rules allow only
  `done`/`blocked`/`failed`, so this report uses `done`.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** every event of the command is
  one `json.dumps` line printed to stdout (`cli._log`). The tests parse every line as JSON and check
  its keys.
- **Code Brain rule 4 (never swallow an exception silently):** the conditions catch nothing. The
  command has one outer handler that logs `standing_approval.unreadable` with the exception's type
  and message and returns exit 2. No bare `except: pass` exists. The tests include an injected
  `RuntimeError`.
- **Code Brain rule 5 (never log secrets, credentials, auth headers, tokens, or full bodies):** the
  lines carry only the credential id, the two commits, condition names, pass or fail, and reasons
  that name fields and ids. A refused re-render names the differing field names, never their
  contents. `test_no_line_carries_a_value` plants a placeholder in environment credentials, a row
  note, and a package field, and proves it's absent from stdout and stderr on exits 0, 1, and 2.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history, no inline
  `BWS_ACCESS_TOKEN`, placeholders only):** the diff carries no token. The fixture values are the
  sentinel `placeholder-secret-value-not-a-token` and UUIDs `11111111-…`, `22222222-…`, and
  `33333333-…`, none in the token shape. The landing-ledger rows are read from the tracked registry,
  which holds ids only. The scan found 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value anywhere in this work, so
  there's nothing to report.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The diff adds no endpoint, domain,
  or URL.
