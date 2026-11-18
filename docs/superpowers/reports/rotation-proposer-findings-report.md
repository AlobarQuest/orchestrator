Outcome: done

# The rotation proposer reports the scan's three findings (ADR-0059 Amendment 12, step 3f-5-state work 2)

Branch `hq/rotation-proposer-findings`, base `origin/main` at `ea2905a9`. PR #737 is open and ready
for review. It isn't merged.

## What changed

In the lane's container, a due credential that the registry doesn't mark `rotated_by_sds` is now a
finding. The pass raises it as an alert-hub alert, as sender `sds-bridge`, in the same sync as its
other alerts. On the Mac, nothing changes: the credential still reports `legacy`.

Files:

- `src/rotation_proposer/outcome.py`: adds `manual-exposure`, `manual-request` and `manual-age` to
  `FINDING_STATUSES`. Adds `NOT_HANDED_OVER_STATUSES`, which maps each trigger to its status.
- `src/rotation_proposer/cli.py`: adds `NOT_HANDED_OVER_ENV`
  (`ROTATION_PROPOSER_REPORT_NOT_HANDED_OVER`). `run` reads it as on only when it's exactly `1`.
  `_not_handed_over` returns a list, and with the switch on it puts `_person_rotates(item)` first.
  `_person_rotates` builds the finding and its checklist detail. With a package, the list also holds
  `not-handed-over`; with no package and the switch off, it holds `legacy`, as before. `_pass` and
  `_walks` pass the switch through as a required keyword.
- `src/rotation_proposer/alerts.py`: adds `CRITICAL_STATUSES = {"manual-exposure"}` and
  `CRITICAL_ALERTS_FILE = "alerts-critical.json"`, plus a `_hub` builder. It splits the findings
  into a `warning` instance (`alerts.json`, unchanged) and a `critical` instance. The `sync_alerts`
  signature is unchanged.
- `src/rotation_proposer/CLAUDE.md`: one new invariant bullet for the switch and the second state
  file.
- `scripts/run-rotation-proposer.sh`: the container branch runs
  `export ROTATION_PROPOSER_REPORT_NOT_HANDED_OVER=1` right after the fence, on every pass. The Mac
  branch runs `unset ROTATION_PROPOSER_REPORT_NOT_HANDED_OVER` just before the program. The header's
  exit-code 3 entry and the "TWO HOSTS" list are updated.
- `docs/operations/rotation-proposer.md`: updates "What it reads", "Alerts" and `legacy`, and adds a
  "Credentials not handed over" section, which the alert detail points to.
- Tests: `tests/rotation_proposer/test_alerts.py`, `tests/rotation_proposer/test_pass.py` and
  `tests/scripts/test_run_rotation_proposer_lane.py`. Tests were only added; no existing test line
  was edited or removed (`git diff origin/main -- tests/ | grep -c '^-[^-]'` is `0`).

I added no test file and touched no file outside the fence.

## Statuses and keys

| infraops check | Status | Key | Severity | Kind |
| --- | --- | --- | --- | --- |
| `cred.exposure-rotate` | `manual-exposure` | `rotation-finding:<credential>:manual-exposure` | `critical` | `alert` |
| `cred.rotation-requested` | `manual-request` | `rotation-finding:<credential>:manual-request` | `warning` | `alert` |
| `cred.rotation-age` | `manual-age` | `rotation-finding:<credential>:manual-age` | `warning` | `alert` |

The title is `Rotation <status>: <credential>`, from the existing `_findings`. Two constraints shaped
the names:

- They avoid the bare trigger words: `exposure` is a substring of `exposure-open`, and
  `test_no_key_is_a_substring_of_another_finding_kind` refuses that.
- They fit `_report`'s 17-character column.

Each alert fires on the pass that reports it. Because `KeyedAlerts` runs without `refire`, it stays
firing without being re-sent while the finding holds. It resolves on the first pass that doesn't
report it. Alerts go out only on `--submit`: a dry run prints the status and sends nothing.

Because the new statuses are findings, a container pass that reports one exits 3. The launcher arms
the dead-man switch with `--finding 3`, so exit 3 still pings success. The header's `EXIT CODES`
block states this for the container.

## How severity is sent, and the second state file

`KeyedAlerts` holds one severity per instance, and a state file has one `sync` caller, so
`sync_alerts` builds two instances:

- **`warning`:** `alerts.json`, every existing key, and `manual-request` and `manual-age`. The
  approval-wait keys and the `unsaved` hold stay in this instance.
- **`critical`:** `alerts-critical.json`, for `manual-exposure` only.

Each instance gets `_held(<its own remembered()>, unread)`, so a credential reporting `error` holds
its keys in both. A pass that exits 2 returns before `_tell_a_person`, so neither instance syncs.
Each instance has its own `try`. A failure in one is reported in the returned reason (the critical
one's is prefixed `critical: `) and doesn't stop the other.

The critical instance runs only when it has an alert to fire or a key remembered. A pass that never
reports `manual-exposure`, which includes every Mac pass, creates no `alerts-critical.json` and opens
no second client.

## The container-only switch, and how I proved the Mac path unchanged

- **The container always turns it on.** The container branch runs the `export ...=1` after the fence,
  so it replaces any inherited value. `test_the_container_always_turns_on_the_findings_for_credentials_not_handed_over`
  runs the real launcher under `env -i` with the variable unset, empty, `0` and `1`, on a dry run
  and on `--submit`. Each case hands the program `1`.
- **The Mac never passes it.** The Mac branch gained one line, `unset
  ROTATION_PROPOSER_REPORT_NOT_HANDED_OVER`, just before the program runs. It does nothing when the
  variable is absent, which is the live job's case: the plist doesn't set it.
  `test_on_the_mac_the_findings_switch_never_reaches_the_program` runs the Mac branch with the
  variable unset and with it inherited as `1`. Neither hands it to the program.
- **Unset, Python behaves as before.** `test_without_the_switch_the_same_pass_is_legacy_and_alerts_nothing`
  reports `legacy` with exit 0. `test_only_the_value_the_launcher_sets_turns_it_on` covers `""`,
  `0`, `true` and `yes`. `test_a_pass_that_never_reports_a_critical_status_opens_no_second_file`
  shows the state directory holds exactly the two files it held before.
- **The existing tests pass unedited**, including `test_a_credential_infraops_still_rotates_is_left_to_it`,
  `test_on_the_mac_nothing_is_minted_or_cloned_and_the_checkouts_are_its_own` and
  `test_the_compose_meets_the_add_refusals_and_maps_nothing_else`. The compose is unchanged, so its
  pinned environment and `lane_run.py`'s list still agree.
- **Mutation controls.** I ran each mutation, then restored the file and cleared `__pycache__`:
  - Removing the container `export` fails 6 tests.
  - Removing the Mac `unset` fails 2.
  - Emptying `CRITICAL_STATUSES` fails 5.
  - Making the critical instance always run fails 2.
  - Dropping `not-handed-over` beside the finding fails 2.

  Every mutation failed at least one test.

## The precedence table

I tested each combination end to end: inline infraops JSON through `findings.parse`, then `run`
with the switch on (`test_each_combination_fires_the_one_finding_its_precedence_names`).

| infraops reports, for one credential not handed over | Fires |
| --- | --- |
| exposure | `manual-exposure` (critical) |
| request | `manual-request` (warning) |
| age | `manual-age` (warning) |
| age and request, in either order | `manual-request` |
| exposure and age | `manual-exposure` |
| exposure and request | `manual-exposure` |
| all three | `manual-exposure` |

**One finding the scan reports separately is hidden: the age finding beside a request.** infraops'
`credFindings` (`cred-rotation.ts` at `c3359bf`) reaches the age and request checks only for a
credential with no open exposure. Its `continue` after the exposure means it never reports an
exposure together with either. It can report age and request together, and the scan turns them into
two change-manager items: "Rotation due" and "Rotation requested". `PRECEDENCE` collapses them to
one `manual-request`, so the age finding has no alert of its own.

Nothing goes unrotated. One rotation and its `record-rotation` move `lastRotated`, which clears both
checks. The `manual-request` detail also says that "an age finding, if infraops reports one too, is
answered by the same rotation". I didn't change `findings.py`'s parse.

Exposure combined with either one appears only in synthetic input; infraops never produces it. The
scan reports only the exposure there too, so it hides nothing.

## The checklist text

The detail for an age finding reads as follows (`<...>` marks the values):

> `<credential>` (`<class>`) is due: aged from `<date>`, past its class's maximum age. The
> registry doesn't mark it rotated_by_sds, so a person rotates it. First read its
> .cred-consumers.toml row: its class's landmines, open preconditions, provider, replacement scope,
> keeper and consumers. Then: 1. CREATE: mint the replacement at the provider and name it
> distinctly. 2. STORE and DEPLOY: update the keeper in BWS in place, then every mapped consumer.
> 3. REVOKE: revoke the old credential at the provider once the consumers verify. 4. RECORD: run
> infraops' security-drift-cli record-rotation for `<credential>`. A row whose disposition is
> revoke-no-replacement has no replacement: revoke it, retire the BWS copy, close any exposure with
> resolve-exposure and delete the row (record-rotation refuses it). Where to run the commands:
> docs/operations/rotation-proposer.md, "Credentials not handed over".

The other two triggers change only parts of that text:

- **An exposure:** the reason reads "exposure `<id>`, dated `<date>`: rotate now, whatever its age".
  Step 4 adds "then close the exposure with resolve-exposure for exposure `<id>`".
- **A request:** the reason reads "a rotation requested `<date>` and none recorded since", plus the
  age note.

The detail names subcommands only, with no command line and no path; the cut-over decides where the
CLI lives. It holds names, a class, dates and an exposure id, and nothing secret. A test asserts
that none of the pass's tokens appear in it, that it has no `dist/` or `node `, and that it stays
under alert-hub's 4,000-character limit. The full steps, with the commands, are in
`docs/operations/rotation-proposer.md`, "Credentials not handed over".

**What the alert omits compared with the scan's item** (the verify item asks for this statement).
`rotationClassification` builds its item from the registry row. The proposer's `Due` doesn't carry
these fields, and the pass reads no registry row for a credential not handed over, so the alert
leaves out:

- the class's `landmines`;
- the open `rotation_preconditions`;
- the "consumers not attested" warning;
- the provider identity and `replacement_scope`;
- the BWS UUID;
- which disposition branch applies.

Instead, the alert tells the person to read the row first and names both branches. Carrying the row
would mean reading the registry for these credentials. That's a new read on this path and a new
`error` mode, and I judged it outside this step.

## Report-don't-fix findings

### Follow-up 10: the lane's `cred-consumers.list` (not fixed)

On the Mac, the list names four absolute paths under `~/Projects`, each a repository's
`.cred-consumers.toml`: infraops-mcp-server, change-manager, factory-runner and orchestrator. In the
container, the launcher clones only intent-packages and infraops-mcp-server, and only for one pass.
The other three registries aren't on disk there.

A fix needs two things:

- the three registry repositories on disk at known in-container paths, for example cloned beside
  the others under `/var/tmp/sds-rotation-proposer`;
- a list naming those paths in `INFRADRIFT_CONFIG_DIR`.

The launcher's fence says that directory comes "from the compose alone", and Amendment 12 leaves
the directories to the `rotation-state-dirs` act. Whether the launcher writes the list each pass, or
the act carries it as data, is a design choice that belongs to that act, so I didn't make it here.
Whatever the choice, a list written for one pass has to agree with the per-pass clone paths.

### Follow-up 6: can a backdated `record-rotation` hide a finding in the proposer's pass? (Yes, if it's run on the Mac)

`recordRotation` (`cred-rotation.ts` lines 185-192 at `c3359bf`) overwrites `lastRotated` with the
given date and has no guard that keeps the date from moving backward. `rotationAnchor` reads
`lastRotated` first. The answer depends on which copy the backdated write lands on:

- **Written in the container, to the volume copy: no.** The volume's date moves earlier, never
  later. The age anchor moves back, so `cred.rotation-age` fires sooner. A date earlier than
  `rotate_requested` reopens `cred.rotation-requested`. Both effects over-report.
- **Written on the Mac during the window: yes.** Suppose the volume holds a date D2 and a person
  corrects it to an earlier D1 by running `record-rotation` on the Mac, as the scan's checklist
  still tells them to. The write reaches only the Mac copy, so the volume, which the proposer
  reads, keeps the newer D2. Two findings can then be hidden:
  - `cred.rotation-age`, if D2 is too recent to age;
  - `cred.rotation-requested`, if D2 is on or after `rotate_requested` and D1 is before it.

  This lasts until the write is repeated in the container. The infraops change doesn't end it,
  because after that change nothing reads the Mac copy.

The proposer's own `record-rotation` records only a date later than `last_rotated`, so it can't
cause either case. Otherwise, the volume copy hides a finding only when its record is false: someone
records a rotation that didn't happen, or one dated after a request it doesn't answer.

The cut-over needs one more step for this. "What the cut-over still needs" lists it: replay in the
container every `record-rotation` and `resolve-exposure` that a person runs on the Mac during the
window, backdated ones included.

### Values HQ needs to match against alert-hub's routing rules

- **Sender (source):** `sds-bridge`, the sender token at BWS `b415d03b-…` that the lane already
  reads.
- **Kind:** `alert`.
- **Severities:** `warning` for `manual-request`, `manual-age` and every existing rotation alert;
  `critical` for `manual-exposure`.
- **Key prefix:** `rotation-finding:`. The approval waits are `rotation-approval:`.
- **User-Agent:** `rotation-proposer/1 (+AlobarQuest/orchestrator)`.

The proof HQ needs is that `sds-bridge` plus `warning` doesn't page. An `sds-bridge` plus `critical`
alert is meant to reach a person: it's an exposure, which Infra Brain rule 1032 makes rotate-now.

## Checks run

- `make check` on the final tree, with the worktree's venv, `TEST_DATABASE_URL` set to
  `orchestrator_test_hq_w_209`, `SECURITY_STANDARDS_DIR` set to the fixture, and `PGTZ=UTC`: ruff
  check, ruff format, pyright (0 errors) and shellcheck all pass. **15,482 tests passed, 1
  skipped**, from the unfiltered log.
  After that run, the code review changed one string in the age detail and its test. After that
  edit, ruff, format and pyright pass, and the 406 tests in `tests/rotation_proposer` pass.
- CI on PR #737 at `aa933f6e`, the head with that fix: `Quality` (9m42s), `Rollout transcription
  currency` and `Runner consumer compatibility` all pass.
- The new tests cover these cases:
  - each severity and kind, from the literal payload;
  - resolving on the next pass that doesn't report it;
  - the hold on `error` in both instances, and on exit 2;
  - a failed critical send, which is named and retried on the next pass;
  - a failed warning send, which doesn't block the critical one;
  - existing keys staying `warning`, with no second file;
  - a package plus the trigger, which reports both statuses;
  - the precedence table;
  - the checklist content;
  - a dry run, which reports and sends nothing;
  - the container switch for every inherited value;
  - the Mac branch never passing the switch.

## Code review

I ran `/code-review` (medium) on `origin/main...hq/rotation-proposer-findings` and checked each of
its seven findings:

- **Fixed:** the age detail said "last rotated `<date>`". infraops' `anchor` can be the row's
  `last_rotated` or `created` date, so the detail now says "aged from `<date>`".
- **Not a defect:** an uncaught `StateUnwritable` or `ValueError` from the critical `sync`.
  `StateUnwritable` is an `AlertHubError`, so it's caught. A `ValueError` needs a key outside
  `KEY_GRAMMAR`, and `alert_key` escapes every part, as it does for the existing instance.
- **Not new:** a corrupt state file re-fires the critical alerts. That's `KeyedAlerts`' behavior for
  every state file, including `alerts.json`.
- **Not changed:**
  - Restructuring `_not_handed_over`: the branches are short, and tests pin all four cases.
  - Merging the two partition comprehensions: the code stays readable as it is.
  - Replacing the environment variable with a CLI flag: the handoff prescribes the variable, and a
    flag would mean editing the launchd plist and crontab argument contract.
  - A second `KeyedAlerts` on every pass: `remembered()` on a missing file reads nothing and writes
    nothing, and a test pins that.

## Self-review

- `sync_alerts`'s signature didn't change, so the pass rig's stub and every existing call still fit.
  The severity split depends only on the status.
- The switch is read once, in `run`, and passed through as a required keyword. No hidden default
  can turn it off in a new caller.
- A handed-over credential's path is untouched (`test_a_handed_over_credential_reports_what_it_did_with_the_switch_on`).
  `NOT_ROTATIONS` and `PRECEDENCE` didn't change, so `cred.unknown-class` stays unreported.
- I added no suppression comment.

## Standards followed

- **`error-logging` rule 3 (structured JSON logs to stdout):** this change adds no logging. Sends
  log through `KeyedAlerts`' existing `notified` and `notify_failed` records. The pass's own report
  lines and its stderr reasons are unchanged in form.
- **`error-logging` rule 4 (never silently swallow an exception):** each instance's `AlertHubError`
  is caught and appended to the reasons that `_tell_a_person` prints to stderr. A failed critical
  send is named `critical: …` and kept for the next pass. Neither failure is dropped, and neither
  changes the exit code, as before.
- **`error-logging` rule 5 (never log secrets):** the detail holds only names, a class, dates and an
  exposure id. A test asserts that none of the pass's tokens appear in it. The `sds-bridge` token
  reaches only `KeyedAlerts`' token callable, as before.
- **`notifications` road:** everything goes through alert-hub through `alert_hub_client.KeyedAlerts`,
  which doesn't raise when the hub is down. Each key is stable (`rotation-finding:<credential>:<status>`,
  with no date or counter). A finding closes with `status: resolved`. Every alert is kind `alert`;
  none is a notice.
- **`secrets-handling` road:** no secret is read, stored or moved. The launcher's BWS reads are
  unchanged.
- **Infra Brain 212 (HTTPS):** the alert-hub client's base URL is `https://alerts.alobar.net`,
  unchanged. The lane serves nothing.
- **Infra Brain 591 and 592 (no BWS token in tracked files or history):** this change adds no token.
  The UUIDs in this report are identifiers already in `.bws-secrets.toml`.
- **Infra Brain 593 (no inline bootstrap token):** the compose and launcher changes add no token.
  The compose is unchanged.
- **Infra Brain 1032 (`cred.exposure-rotate`):** `manual-exposure` is `critical`. It stays firing
  while infraops reports the exposure for a credential not handed over, and an `error` pass or an
  exit-2 pass holds it. It resolves on the first pass that doesn't report it. That normally means
  `resolve-exposure` or the revoke record ended the exposure, with one exception: the registry row
  flips to `rotated_by_sds` while the exposure is still open. Concern 6 covers that case. I didn't
  surface any exposed credential; I made no live read.

## Concerns and follow-ups

1. **The age finding beside a request is folded into `manual-request`** (see "The precedence
   table"). The detail names it, but it has no alert of its own. If HQ wants one alert per
   scan item, `parse` would have to return every trigger for a credential not handed over. The
   handoff told me not to change that.
2. **The alert carries a generic checklist, not the row's landmines, preconditions or provider.**
   The detail and the doc tell the person to read the row first. Whether that closes the
   "how the manual checklist reaches a person" verify item is HQ's or Devon's call.
3. **The commands' home between passes is still open.** Both `record-rotation` and
   `resolve-exposure` need the infraops CLI and the registry files. In the container, both are
   per-pass clones. The doc says the cut-over settles this, as Amendment 12 does.
4. **Infra Brain rule 1032's text** still says the 03:00 scan raises the exposure (#735 follow-up
   2). After the infraops change, this pass raises it for credentials not handed over. I can't write
   to Infra Brain from here.
5. **Exit 3 in the container** now also means "a credential a person rotates is due". Because the
   dead-man switch treats exit 3 as success, it changes no ping. Anything that later reads the
   lane's exit code needs to know this.
6. **A `rotated_by_sds` flip resolves an open exposure's critical alert.** Suppose the registry row
   gains `rotated_by_sds` while its exposure is still open. The next pass takes the handed-over path
   and reports no `manual-exposure`, so the critical alert resolves as "Rotation no longer needs a
   person". The exposure itself doesn't go dark: the handed-over path files it as a rotation and
   revises or generates its package, and that path reports what it reports today. But the
   `critical` signal ends before the exposure does. I left the code alone, because the handoff
   keeps handed-over reporting as it is. Holding the critical key across the flip would mean
   reporting something new on the handed-over path.

## What the cut-over still needs

- The `rotation-state-dirs` act sets `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` in the
  compose. It must also put a `cred-consumers.list` there that names in-container registry paths,
  and get those registries on disk (follow-up 10). Until then, the launcher's fence stops every
  container pass with exit 2.
- The cut-over replays in the container every `record-rotation` and `resolve-exposure` that a
  person ran on the Mac between the copy and the infraops change, backdated ones included
  (follow-up 6).
- The `rotation-state-copy` act copies the Mac state file, checks the digest, mode 0600 and owner,
  and checks that `alerts.json` and `approval-since.json` come along. `alerts-critical.json` doesn't
  exist on the Mac and needs no copy.
- The cut-over states where the infraops CLI and the registry files are when a person runs
  `record-rotation` or `resolve-exposure` between passes, and shows each command running there.
- The first VPS pass that reports these findings, or shows none due, is cited in the infraops pull
  request that stops the Mac scan's read (Amendment 12 decision item 3, "Order").
- HQ checks alert-hub's routing: `sds-bridge` plus `warning` doesn't page.
