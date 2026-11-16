Outcome: done

# Build report: feed-c-carrier-chain-reader (W-188)

ADR-0059 Amendment 11, step 3f-2-feed (c). In the lane's container, the work carrier now reads the
portfolio scan's four check results from the newest `portfolio.scanned` event in the
factory-events chain. The orchestrator's security-standards pin now names `08754a0f`, which holds
the `portfolio-scan` actor and the `portfolio-scan` value of `source.system`.

- **PR:** [AlobarQuest/orchestrator#725](https://github.com/AlobarQuest/orchestrator/pull/725),
  ready for review and not merged.
- **Base:** `origin/main` `bb9fca2fbac2134e1d51cef1aa7a25a0e23a22ab`.
- **Content commits:**
  - `23742e46`: the reader, the hand-over, the pin, the guards, and the docs.
  - `6d31d8b7`: the chain integration test.
  - `83856c8b`: a test that pins the `[feed]` line before `run`'s first return, and the removal of
    a clause no input reaches.
- **Head:** the commit that adds this report, which follows `83856c8b`.

## What I built

### The pin and the fixture

`security-standards.pin.toml` now holds the following values:

- `revision`: `08754a0fb887e572509337f36948b048963c25f4`. That is the full SHA from
  `git -C ~/Projects/security-standards rev-parse 08754a0f`.
- `artifact_sha256`: `0d081268db15e54eace6978d4686f70ede909e49dc38be2d91585d7d60e9bf6d`.

I proved the digest in four steps:

1. I made a scratch bare clone with `git clone --bare ~/Projects/security-standards
   /tmp/w188-pin.*/ss.git`. That clone only reads the main checkout's objects. I then ran
   `scripts/shape_registry_context.py --source <clone> --revision <sha> --output <dir>` and
   `build_registry_bundle.artifact_digest`, which is the method the pin file's comment gives. The
   shaper needs a git source because it uses `git archive`, so a plain `tar -x` directory can't
   be its input.
2. As a control, the same method at `05963b76` gives `4873d9ab…`, which is today's pin.
3. I ran the Dockerfile's gate, `build_registry_bundle.py --artifact-dir <ctx> --source-revision
   08754a0f… --artifact-sha256 0d081268… --output bundle.json`. It exits 0. The bundle reads back
   `source_revision` `08754a0f…`, and its 21 actors include `portfolio-scan`. The same gate with
   the old digest raises `RegistryValidationError: registry artifact digest does not match`.
4. I validated a sample `portfolio.scanned` event against each scratch tree's real
   `factory_events.envelope`. `08754a0f` accepts it, and `05963b76` refuses it with
   `EnvelopeError`.

`git diff 05963b76 08754a0f --stat` shows only #98's four files. The `sds-lanes` image is built
`FROM` the orchestrator image (`lanes/Dockerfile` lines 12 and 44), so this one pin covers both
images.

I left `tests/fixtures/security-standards/` unchanged, which is HQ's answer A. The fixture is a
synthetic stand-in, not a copy of any upstream revision:

- `SOURCE_REVISION` is the placeholder `0123456789abcdef0123456789abcdef01234567`.
  `.github/workflows/quality.yml` pins it on line 284 and pins its digest `258e4757…` on line 285.
- `schema/factory-event.v1.schema.json` is a four-line stub.
- The `source.system` enum lives inline in the fixture's own stand-in `src/factory_events/envelope.py`.
- No guard checks the fixture against a source revision.
- No test or image step builds a `portfolio.scanned` event or validates one against the fixture.
  The fixture has no `store.py`, so it has no walk. For that reason, by the handoff's own
  condition, the `portfolio-scan` agent isn't added to it.

The proof that the pinned copy accepts the event is the integration run. At `08754a0f`, the real
`chain.append_event` accepts the event and the real walk hands it over (11 passed). At `05963b76`,
the same test fails because the append is refused with `EnvelopeError`.

### The reader (item 2)

`orchestrator.factory_chain_read.portfolio_scan(scans, now)` is a pure function that selects in
two tiers:

- **Candidacy.** The actor and `source.system` must both be `portfolio-scan`, and `timestamp` must
  be a whole-second UTC time that is no more than `SCAN_FUTURE_SKEW_SECONDS` (300) after the
  pass. A failure skips the event with a reason, and an older candidate may win. The
  `skipped_reason` on the `[feed]` path names the `seq` and the cause.
- **The newest candidate by `timestamp`, never by `seq`, then decides alone:**
  - `success` with every evidence item in the contract gives rows. A row in the contract has
    `type: repository`, a lowercased `owner/repo`, at most one row per repository, and `checks`
    keys from the four ids with string statuses.
  - `unknown` gives a scan with no rows, which holds every record.
  - Any other `result`, or any evidence outside the contract, gives no usable event and never
    falls back to an older scan. Falling back would let an older pass override a newest scan that
    is `unknown` or outside the contract.
  - Two candidates that share the newest time are ambiguous, and neither is read.

The skew tolerance is 300 s, for the following reasons:

- The scan's time comes from the Mac's clock and the pass's time from the VPS's. Both are
  NTP-synced, so a real event is seconds apart at most.
- An event dated further ahead is a wrong clock or a forgery.
- The chain is append-only, so such an event would never age past 48 hours.

The tolerance is a plain `Final` int with no off value.

`work_carrier.workability.ScanFeed` and `load_scan_feed` read the hand-over:

- **Age.** The age is the pass time minus `scanned_epoch`, which the chain read parsed from
  `timestamp`. It is compared as a number, never as a string. An event in the future within the
  tolerance counts as age 0.
- **Holds.** A record holds when the feed is past `PORTFOLIO_MAX_AGE_HOURS` (48), when the scan's
  result is `unknown`, when there is no usable event, or when the hand-over is unreadable or has
  the wrong shape.
- **Join.** `feed_statuses_for` joins the record's repository to a row by the full name,
  case-folded. That replaces the directory-name join (`_names` and `project_for`), which only the
  Mac path keeps.
- **Status mapping.** `assess` feeds the row's statuses to the same `_from_checks`. I changed that
  function to take a statuses mapping in place of a project row, so both sources share one
  mapping, with `CAPABILITY_CHECKS` and `PERMISSION_CHECKS` unchanged.
- **Unchanged:** `assess_operational`.

### Where the event comes from (item 3)

I took HQ's recommendation:

1. `orchestrator.factory_chain_read` is the one process that walks the chain. It collects
   `portfolio.scanned` events in `_records`, beside `package.approved`, during the same
   read-only, repeatable-read snapshot. `WALK` is the same `SELECT`.
2. Only a verified record carries `portfolio_scan`. A broken walk carries none, which
   `test_a_broken_walk_carries_no_scan` pins.
3. In the container branch of `scripts/run-work-carrier.sh`, the summary parse writes that part
   of the record to a `mktemp` file, and only after it has seen `"outcome": "verified"`.
4. The launcher passes that file to `work-carrier` as `--portfolio-scan <file>` and removes it at
   the end of the pass. A failed removal is logged and doesn't change the exit.
5. The report still prints only the summary fields.

The container reads no Mac file, and nothing new joins `lane-run`'s list. A chain that doesn't
verify sets `_carry=0`, as before, so the carry doesn't run and uses no event.

The guards changed as follows:

- `tests/architecture/test_intent_edges.py` pins the chain read's two new imports, `re` and
  `datetime`. Its environment, commands, connection and `WALK` pins are unchanged and still pass.
- The `work_carrier` row of `tests/architecture/test_out_of_process_isolation.py` gains a comment
  only. The carrier reads the hand-over with `json` and `pathlib`, which the row already lists,
  and imports no driver or `factory_events`.

### The Mac carrier (item 4)

The Mac pass gets no `--portfolio-scan` and still reads `~/.portfolio/portfolio.json` through
`DEFAULT_PORTFOLIO` and `WORK_CARRIER_PORTFOLIO`. `test_the_macs_pass_still_reads_the_file` and the
launcher's Mac-branch assertion prove it.

`DEFAULT_PORTFOLIO` and `WORK_CARRIER_PORTFOLIO` stay for the Mac carrier until its cut-over,
because its pass paired with the VPS run is the cut-over's proof. The file path leaves with the
Mac job in a later step. Amendment 11's verify item "The pieces land together" asks for this
statement.

### The silent case (item 5)

`work_carrier.cli._announce_feed` prints `[feed] …` before anything that can end the pass:

- `main` calls it before its registrar's early returns.
- `run` calls it before the checkout-root check, unless `main` already announced the feed.

The line shows one of two things:

- The scan time, its age, the row count and `result`, for example `portfolio.scanned at
  2026-11-16T08:00:35Z (seq 412), 3h old, 9 rows, result success (…)`.
- `no usable portfolio.scanned event: <reason>`.

The exit changes only through held records, which are `UNDECIDED` and exit 2. An empty queue with
a stale feed exits as it would without the feed. The PR body states this as the code step's plan
statement.

### Docs (item 6)

`docs/operations/deploy.md` gains a paragraph on the feed in the work carrier lane's section. In
its cut-over list, step 8 now lists what is merged. It also says the carrier must run an image
built at this pin before #99 merges, and that the cut-over can't finish without a fresh event or
a named hold. No other operations page describes the carrier's portfolio input, so I changed no
other page. I didn't edit the ADR.

## Tests and checks

The following runs were all green:

- **`make check`**, with my database `orchestrator_test_hq_w_188` and
  `SECURITY_STANDARDS_DIR=tests/fixtures/security-standards`: ruff and pyright are clean, and the
  collected count is **15,134 passed, 1 skipped**. That run was at `83856c8b`.
- **`scripts/check-integration.sh`** with `SECURITY_STANDARDS_DIR` at a scratch `git archive` of
  `08754a0f`, on a disposable Postgres in Docker: 11 passed. The control at `05963b76` fails
  `test_the_real_walk_hands_over_the_newest_scan_by_its_time_not_its_seq` because the append is
  refused with `EnvelopeError`.
- **Security scan from the worktree root:** 0 BLOCK, 2 WARN, 1 INFO.
  - `bws.secret-files-gitignored` flags the `*-migration/` glob in `.gitignore`, which this diff
    doesn't touch.
  - `bws.manifest-matches-usage` flags UUID `aaff45ca-…` in
    `scripts/install-rotation-worker-launchd.sh` and earlier reports, none of which this diff
    touches.

The handoff's test list maps to these tests:

| Handoff test | Where |
|---|---|
| Newest by `timestamp` when an older scan has the higher `seq` | `test_the_newest_by_timestamp_wins_…` (unit); `test_the_real_walk_hands_over_the_newest_scan_by_its_time_not_its_seq` (integration) |
| No event | `test_no_event_…` (both files) |
| Older than 48 hours by `timestamp`, recently appended | `test_a_scan_past_48_hours_holds_whatever_its_seq` (`seq` 999) |
| `result` `unknown` | `test_an_unknown_scan_holds_every_record`, `test_a_newest_unknown_scan_is_never_overridden_…` |
| No row; a repository outside the member record | `test_a_repository_with_no_row_holds_for_a_reason_that_says_so` |
| Malformed: missing `timestamp`, wrong `source.system`, an evidence item of another type | `test_a_malformed_event_is_skipped_…` and `test_a_malformed_event_alone_is_never_read_as_a_pass` (candidacy); `test_a_newest_event_outside_the_contract_…` (content) |
| Future-dated beyond the tolerance | `test_a_future_dated_event_past_the_tolerance_…`, `…_alone_gives_no_usable_scan`, `test_an_event_within_the_tolerance_is_read` |
| Case-folded full-key join, owner kept | `test_the_join_is_the_whole_name_case_folded` |
| An unknown status maps as the file maps it | `test_a_status_maps_as_the_file_maps_it` |
| A walk that doesn't verify uses no event | `test_a_broken_walk_carries_no_scan`, `test_a_chain_that_did_not_verify_hands_over_no_feed` |
| The `[feed]` line on every verified container pass; an empty queue with a stale feed keeps today's exit | `test_every_container_pass_prints_one_feed_line_first`, `test_an_empty_queue_with_a_stale_feed_keeps_todays_exit`, `test_the_feed_line_comes_before_the_pass_can_end_early`, `test_run_prints_the_feed_line_before_a_missing_checkout_root_ends_the_pass` |
| The Mac pass still reads the file | `test_the_macs_pass_still_reads_the_file`; the Mac-branch assertion in `test_run_work_carrier.py` |
| Positive control | `test_a_fresh_feed_carries_the_record`, `test_a_fresh_feed_answers_as_the_file_does` |

## Mutants

I ran each mutant alone on committed content with `/tmp/w188-mut/mutants.py`, outside the repo.
The script cleared `__pycache__` before and after each run, restored with `git checkout -- <file>`,
and left `git status` clean afterwards.

| # | Mutant | Result |
|---|---|---|
| M1 | Newest by `seq`, not `timestamp` | red: `test_the_newest_by_timestamp_wins_…` |
| M2 | Future-dated check removed | red: `test_a_future_dated_event_past_the_tolerance_…` |
| M3 | A newest event outside the contract falls back to an older candidate | red: `…never_falls_back[overrides0]` |
| M4 | An evidence item of another type is skipped, not refused | red: `…never_falls_back[overrides1]` |
| M5 | Actor not checked | red: `test_a_malformed_event_is_skipped_…` |
| M6 | `source.system` not checked | red: same |
| M7 | A tie takes the first | red: `test_two_events_sharing_the_newest_scan_time_are_ambiguous` |
| M8 | Lowercase check removed | red: `…never_falls_back[overrides4]` |
| M9 | Duplicate rows allowed in the event | red: `…never_falls_back[overrides6]` |
| M10 | A broken walk carries the scan too | red: `test_a_broken_walk_carries_no_scan` |
| M11 | The carrier's 48-hour bound removed | red: `test_a_scan_past_48_hours_holds_whatever_its_seq` |
| M12 | An `unknown` scan read as usable | red: `test_an_unknown_scan_holds_every_record` |
| M13 | Join by name, owner dropped | red: `test_the_join_is_the_whole_name_case_folded` |
| M14 | Join not case-folded | red: `test_a_fresh_feed_answers_as_the_file_does` |
| M15 | A missing row reads as an empty row | red: `test_a_repository_with_no_row_holds_…` |
| M16 | A `bool` epoch accepted | **equivalent.** `True` is epoch 1, which is always past 48 hours, so it holds either way. I deleted the clause in `83856c8b`. |
| M17 | Duplicate hand-over rows, last wins | red: `test_duplicate_rows_in_the_hand_over_hold` |
| M18 | The feed ignored in the judgment | red: `test_a_fresh_feed_carries_the_record` |
| M19 | `main` announces no feed before its early returns | red: `test_the_feed_line_comes_before_the_pass_can_end_early` |
| M20 | `run` announces the feed after the checkout-root return | red: `test_run_prints_the_feed_line_before_a_missing_checkout_root_ends_the_pass`. My first version of this mutant only swapped two assignments and survived, so I rewrote it and added the test. |
| M21 | The launcher doesn't hand the feed to the carry | red: `test_a_bare_pass_reads_only_…` (the argument assertion) |
| M22 | The feed file isn't removed | red: `test_a_verified_pass_hands_the_carry_the_scan_feed_as_a_file_it_then_removes[bare]` |
| M23 | The feed is written before the verified check | **survived, equivalent.** On an unverified walk `_carry=0`, so the carry never runs, the file is never read, and the pass removes it. |
| M24 | The Mac branch is handed the feed too | red: `test_on_the_mac_nothing_is_minted_cloned_or_verified[bare]` |

## Files changed

- `security-standards.pin.toml`
- `src/orchestrator/factory_chain_read.py`
- `src/work_carrier/workability.py`
- `src/work_carrier/cli.py`
- `scripts/run-work-carrier.sh`
- `docs/operations/deploy.md`
- `layers.toml` (the new test file)
- `tests/cli/test_factory_chain_read_scan.py` (new)
- `tests/work_carrier/test_scan_feed.py` (new)
- `tests/cli/test_package_chain_database_integration.py`
- `tests/scripts/test_run_work_carrier.py`
- `tests/architecture/test_intent_edges.py`
- `tests/architecture/test_out_of_process_isolation.py`
- `tests/work_carrier/test_container_portfolio.py` (docstring)
- `tests/work_carrier/test_workability.py`, `test_standing.py` and `test_operational_branch.py`:
  their `judge_workability` patches accept the new `portfolio` keyword.
- This report.

## Self-review

- **One mapping.** Both sources feed `_from_checks`, so the feed can't widen what counts as a pass.
- **Fail closed.** Every path in `load_scan_feed` that isn't a fresh `success` returns
  `usable=False`, which is UNKNOWN and never NO.
- **Selection in one place.** The contract checks live in the chain read, the one process that
  sees the events. The carrier checks the hand-over's shape again, because a process boundary
  sits between them.
- **Second check of the event shape.** The carrier repeats only the type checks, not the contract.
  The launcher is the hand-over's only writer.
- **New constants.** `SCAN_CHECKS` carries a `# not-a-vocabulary:` marker. The cross-boundary
  vocabulary guard passes.
- **TDD order.** I wrote the reader before its tests, not after. The mutation table makes up for
  that: every rule has a test that goes red when the rule breaks.
- **The fixture question.** I asked it and HQ answered A. HQ renamed the question file to
  `feed-c-carrier-chain-reader-question-answered.md`, which is untracked in this worktree. I left
  it alone.

## Concerns

- **Rollout order.** Before security-standards #99 merges, the work carrier must run an image
  built at this pin. Merging this PR doesn't deploy, and I ran no release. Every walk listed in
  the next section validates against its image's copy, so a `portfolio.scanned` event reaching an
  image at `05963b76` breaks every walk from then on.
- **The scan's free-text reason.** #99's report notes that the event can carry the scan's reason
  string permanently. The carrier never prints it: `[feed]` shows only times, counts, `result`
  and its own reasons.
- **Clocks.** The 300 s tolerance assumes NTP on both hosts. If the Mac's clock runs more than
  5 minutes fast, its scans are skipped as future-dated. The `[feed]` line then names the reason,
  and records hold.

## For HQ's rollout of #99: what walks or validates the chain today

| Image | Process | Walks or validates | security-standards copy |
|---|---|---|---|
| `sds-lanes` (work-carrier lane) | `scripts/run-work-carrier.sh`'s verify: `python -m orchestrator.factory_chain_read`, once per container pass | Full walk; `store.walk_records` re-validates every event | `PYTHONPATH=$SECURITY_STANDARDS_DIR/src`, where `SECURITY_STANDARDS_DIR=/app/security-standards`. The image's pin, inherited `FROM` the orchestrator image. |
| `sds-lanes` (work-carrier lane) | `work-carrier` runs `orchestrator emit-intake-payload` once per queued record. That runs `package_sources`, which runs the `factory_chain_read` child again. | Full walk, validates | Same: `/app/security-standards`, the pin |
| `sds-lanes` (work-carrier lane) | The same `package_sources` runs intent-packages' `verify-approval`, which walks through its `chain_db` | Full walk, validates | `SECURITY_STANDARDS_DIR` inherited from the carrier: the pin |
| Orchestrator (API server) | None. The server doesn't import `package_sources` on any request path; only the `emit-intake-payload` CLI does. A person running that CLI inside the image would walk with the pin. | No walk | `/app/security-standards` is present (the pin) but unused by the server |

No other process in either image walks or validates the chain. I searched `src/`, `scripts/` and
`lanes/` for `package_sources`, `factory_chain_read`, `verify-approval`, `walk_records` and
`factory_events`. The other hits are the following:

- The Mac-only launchers `run-bump-proposer.sh` and `factory-events-session.sh`.
- `rotation_proposer`'s rotation-record walk, which isn't the chain.
- `bump_proposer/standing.py`, which appends through intent-packages and runs on the Mac.
- Build helpers.

The Mac rows (the nightly, the Mac carrier branch, and hand runs of `verify-approval`) are in #99's
report and load `~/Projects/security-standards`.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout in production):** `factory_chain_read` still
  emits one JSON record on stdout through `_emit`. The new `portfolio_scan` key is part of that
  record. The diff adds no file logging. The `[feed]` line goes to the outcome report, as the
  launcher's other lines do.
- **Code Brain rule 4 (never silently swallow an exception):** the diff adds two catches, each
  with an inline reason.
  - `_scan_time` catches `ValueError` for a timestamp that isn't a calendar date. The event is
    skipped, and its reason reaches `skipped_reason`.
  - `load_scan_feed` catches `(OSError, ValueError)` and returns a hold whose detail names the
    error type.

  Every malformed event is skipped with a reason that reaches the `[feed]` line. There is no
  `except Exception: pass`.
- **Code Brain rule 5 (never log secrets, credentials, tokens):** the read password still goes
  only to `psycopg.connect`. The chain read's existing tests assert it never reaches stdout, and
  they pass. The record, the feed file and the report hold repository names, statuses, times and
  counts only. The child's stderr is still discarded (`2>/dev/null`; ADR-0055 amendment 24
  item 13).
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files or history, no inline
  `BWS_ACCESS_TOKEN` value):** the diff contains no token and no `BWS_ACCESS_TOKEN` assignment.
  The security scan reports 0 BLOCK. The test value `a-lane-token` already existed and isn't
  token-shaped.
- **Infra Brain 1032 (`cred.exposure-rotate`):** I saw no secret value. I fetched no credential
  and read no env file. The integration test uses a random throwaway password on a disposable
  cluster.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The diff makes no network change.
  The chain read connects to the same database with the same settings as before.
