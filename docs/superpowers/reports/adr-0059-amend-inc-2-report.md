Outcome: done

# Build report: adr-0059-amend-inc-2 (ADR-0059 Amendment 2)

- **Worker:** W-40; fix round 1 by W-41
- **Branch:** `hq/adr-0059-amend-inc-2`, from `origin/main` at `d485d051`
- **PR:** https://github.com/AlobarQuest/orchestrator/pull/627 (open, not merged)

## What I built

Amendment 2 to ADR-0059, after Amendment 1 and in its shape (status block, `### Why`,
`### What changes`, `### Verify in increment 2`, `### What stays`, `### Passages this amendment
changed`). It matches ADR-0059 to ADR-0055 amendment 19 and marks each changed body passage with
"(Amendment 2)", the way Amendment 1 marked its own. Where a verify item is answered, it uses
Amendment 1's "Answered by …" prefix form. Its status line copies Amendment 1's: "Accepted on
merge. HQ merges it only after a clean Opus whole-branch review (HQ D-12, limit 3)."

One section isn't in Amendment 1's shape: `### Verify in increment 2`, which the handoff asked for.

## Passages changed

Line numbers are at the branch head after fix round 1 (file
`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`).

| Lines | Passage | Follows |
|---|---|---|
| 9-11, 13 | Header `Decided by` (D-24 to D-28) and `Relates to` (amendment 19) | amendment 19 |
| 142-143 | Lane-phase bullet: the change list gains `--token` | item 6 |
| 164-166 | `lanes-prepare`: `--token` writes the compose at the running tag, no variable | item 6 |
| 211-216 | `lanes-finish`: `--token`'s pass conditions and its close as failed with no inverse | item 6 |
| 253-263 | Quiet-slot bullet: "nothing staged" gains a `--token` exception, whose token is already staged (fix round 1) | item 6 and "Open before building", `--token` sub-item 4 |
| 428-430 | Per-resource-variable bullet: the executor writes with `redeploy` false, never redeploys | item 6 |
| 499-502 | Decision 2 token bullet: each lane token keeps a keeper (D-24) | item 1 |
| 660-662 | `.cred-consumers.toml` bullet: first issue and early or leak reissue before increment 7 | items 3, 10 |
| 694-697 | Decision 3: the VM root creates the ping-URL secret with the keeper command, in the moving increment's cut-over | items 1, 3, 8 |
| 703-705 | Decision 3: the ping URL binds by shape alone; cut-over step 4 proves it (D-28) | item 5 |
| 941-944 | Increment 2: its ADR-0055 amendment is amendment 19 | amendment 19 |
| 949-992 | Increment 2 verify items, each marked answered (row at create; `redeploy` false; two waiting mints; the ping-URL wait and bind; D-25 actor; D-26 revoke order) | "What the code settles", items 2, 3, 5, 6, 8, 10 |
| 1046-1057 | Increment 7: the redeploy's actor points to amendment 19; the leak order (D-26); R10 | items 6, 7 |
| 1085-1093 | Cut-over step 1: amendment 19 item 8's order (keepers, rows, grants, mint, ping URL) | item 8 |
| 1149-1151 | Cut-over step 4: the check's last-ping read proves the ping URL (D-28) | item 5 |
| 1338-1346 | Consequences, executor: keepers at rest on the VM (D-24) | item 1 |
| 1347-1366 | Consequences, lane-token rotation: age rule, D-25 proposer, reach bind (no hand stage), D-26, D-27 | items 4, 7, 9, 10 |
| 1406-1410 | Dependencies, ADR-0055: increment 2's amendment is amendment 19 | amendment 19 |
| 1413-1415 | "Devon's decisions" introduction: D-24 to D-28 | amendment 19 |
| 1451-1466 | Q3: the executor never redeploys before increment 7; reissues before it; keepers | items 1, 6, 9, 10 |
| 1517-1520 | Q8: the amendment is amendment 19, and it adds keepers | item 1 |
| 1701-1860 | Amendment 2 (fix round 1: its distinct-status verify item, 1826-1832; "What stays", 1842-1850; "Passages this amendment changed", 1854-1860) | amendment 19 |

The five passages HQ found are all covered: (1) lines 142-216 and Amendment 2 item 1; (2) lines
1046-1057 and item 2; (3) lines 949-992 and item 3; (4) lines 1347-1366 and item 4; (5) lines
499-502, 1338-1346, 1451-1466, 1517-1520 and item 5. The search also found these: the
per-resource-variable bullet, the `.cred-consumers.toml` bullet, decision 3's two ping-URL
sentences, cut-over steps 1 and 4, Dependencies, the decisions introduction, Q3's
"redeploys" clause, and the header.

## The ping-URL secret's creator

The handoff asked me to say which passage creates it or carry it as a verify item. Amendment 19's
text settles it, so I didn't carry it. Item 1 says the keeper command creates the secret in
`Rotation / Keepers`, and item 8 step 1 has the VM root run that command in the lane's cut-over.
ADR-0059 decision 3 says the increment that moves each lane creates the secret. Both hold, because
the increment's cut-over is where the root runs the command. Decision 3 (lines 688-691) and
Amendment 2 item 6 state this.

## Fix round 1

Worker W-41 fixed the two blocking findings of HQ's Opus whole-branch review of PR #627 at
`d2fac821`, and changed nothing else. The other findings stay as follow-ups in the PR body.

1. **The quiet-slot bullet's "nothing staged" sentence (lines 253-263).** It said a timed-out wait
   leaves nothing staged, which is false for `--token`: the executor's `PATCH` writes
   `<LANE>_BWS_ACCESS_TOKEN` before `lanes-prepare` runs (amendment 19 item 6; this ADR's lines
   164-166 and 428-430). The bullet now says the session writes nothing while it waits, keeps the
   sentence, and adds one exception: in a `--token` run the token is already staged, so any later
   redeploy (by hand, `--tag` or `--schedule`) loads it without `--token`'s reads. The exception is
   for `--token` only; the sentence names no other mode, because an `--add`'s resource also holds
   variables written before its wait (the "Adding a lane creates its resource" bullet). It points to
   the `--tag`-after-`PATCH` verify item and to amendment 19's "Open before building", `--token`
   sub-item 4. It states no new behavior. Amendment 2's "What stays" now lists the sentence as
   qualified for `--token`, and its "Passages this amendment changed" names the quiet-slot bullet.
2. **The distinct-status verify item (lines 1826-1832).** It listed the run file's statuses as
   three. `scripts/deploy_orchestrator.py` at `d485d051` defines seven: line 1016 `WAITING,
   REFUSED, TIMED_OUT, SUPERSEDED` and line 1017 `UNFINISHED, FINISHED, RESTORED`. The item now
   says `lanes-finish` closes an `unfinished` run as `finished` or `restored` (line 1017; the code
   sets them at 1779 and 1757) and names line 1016's four. Its point is unchanged:
   `last_finished` (1165-1169) takes only `FINISHED`, and `require_lanes_current` (1221-1235)
   refuses on `UNFINISHED`. This report's code citation (Tests and checks run) is corrected the
   same way.

Every line number in the "Passages changed" table after line 258 moved by six in the fix; the table
gives them at the fix-round head.

Checks run in fix round 1:

- `grep -nE '0\.[0-9a-f]{8}-'` over the ADR: no match (count 0).
- Line length: no prose line the fix adds is longer than 100 characters (the report's table rows
  are longer, as the table already was).
- `git show d485d051:scripts/deploy_orchestrator.py`, read at lines 1016-1017, 1165-1169,
  1221-1235, 1757 and 1779 to back the corrected citation.
- `make check`: not run. The change is docs only and no test reads the ADR.
- The BWS security scanner (`security_scan.cli . --category security`, worktree root): `BLOCK: 0`.

## Tests and checks run

- `grep -nE '0\.[0-9a-f]{8}-'` over the ADR: no match (exit 1).
- Line length: no line added by the diff is longer than 100 characters. The file's pre-existing
  table rows are longer and are unchanged.
- A blank-line check of the diff: no blank line was removed, so no paragraph merged during the
  reflow.
- `grep` for `0059-scheduled` and `docs/decisions/0059` under `tests/`: no test reads the ADR by
  path. `layers.toml` places `docs/decisions/0059-*.md` already.
- `make check`: not run. The change is docs only, and `make check` needs Postgres and a migrated
  test database; no test reads this file.
- Code reads that back the verify items: `scripts/deploy_orchestrator.py` at `d485d051`
  defines seven run statuses, `WAITING, REFUSED, TIMED_OUT, SUPERSEDED` (line 1016) and
  `UNFINISHED, FINISHED, RESTORED` (line 1017); `lanes-finish` closes an `unfinished` run as
  `RESTORED` (1757) or `FINISHED` (1779), `last_finished` takes only `FINISHED` (1165-1169),
  `require_lanes_current` (1221), `finish_mismatches` (1723), and `_lanes_finish`'s message "Run
  the inverse printed on stderr now" (1772-1775).
- The BWS security scanner (`security_scan.cli . --category security`, from the worktree root):
  `BLOCK: 0`. Its WARN findings are the pre-existing gitignore-coverage pattern check.
- After the advisor's review, the verify section's lead-in cites amendment 19 exactly (five
  `--token` sub-items, then its dead-token item, then the two PR #626 items), and each verify
  item names increment 2.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`
- `docs/superpowers/reports/adr-0059-amend-inc-2-report.md` (this report)

ADR-0055 is untouched.

## Self-review

- Every behavior Amendment 2 states for `--token` is amendment 19 item 6's: the compose at the
  running tag and no variable, the existing `coolify_update_service` and `coolify_deploy`, the
  `lanes-finish` pass conditions, and the close as failed with no inverse write. What amendment 19
  leaves to increment 2's code (`change_key`, the `--restored` form, the read's input form, the
  `--tag`-after-`PATCH` case, the placeholder refusal, and the dead-token quiet-slot interaction)
  is listed under "Verify in increment 2", with no behavior stated.
- Amendment 19 is Proposed. Each citation of it says "proposed, merged at `d485d051`" where it is
  named as the authority.
- One verify item adds a requirement that amendment 19 doesn't state: the closing call for a
  failed `--token` run "doesn't report the run as a success". It comes from the handoff's Code
  Brain Rule 4, not from amendment 19. Its exit code is left to increment 2.
- The distinct-status item notes that `last_finished` already ignores `restored`. The item is
  about the name: a failed `--token` run restored nothing, so `restored` would misdescribe it.
- Cut-over step 1 keeps its original sentences and adds item 8's order after them. HQ's reviewer
  may prefer a rewrite of the step's order instead of an appended sentence.

## Concerns

- None blocking. The standards section covers this change: docs only, no logging code, no
  endpoint.

## Standards followed

- **Code Brain Rule 5 (never log secrets).** No logging text changed. The ADR names a lane
  token and a ping URL only by variable name, secret, class and UUID role. No real ping URL and no
  token value appears.
- **Code Brain Rule 4 (never silently swallow an exception).** Amendment 2 keeps amendment 19's
  visible failure for a `--token` run: exit 2 at `lanes-finish`, an `unfinished` run that refuses
  the next `lanes-prepare` and every release, a close as failed with its mismatches recorded, and
  HQ's report. Its verify items require a distinct closed status and a closing call that doesn't
  report the run as a success.
- **Code Brain Rule 3 (structured JSON logs).** No code changed. Existing references stay.
- **Infra Brain 591, 592 and 593 (no token-shaped string).** `grep -nE '0\.[0-9a-f]{8}-'` over the
  ADR returns nothing. The commit adds no example token.
- **Infra Brain 1032 `cred.exposure-rotate`.** The leak path ends in revoke and reissue: Devon
  revokes first after a leak (D-26), and the reissue follows. Amendment 2 item 4 says so, and
  the Consequences no longer describe a hand stage.
- **Infra Brain 212 (HTTPS).** Doesn't apply: no endpoint changes.
- **No `secrets` road in Code Brain.** None was needed: the change is docs only.
- **Fix round 1 against the same standards.** The two edits add no token-shaped string (grep
  count 0), no real ping URL and no secret value: the quiet-slot fix names the token only as
  `<LANE>_BWS_ACCESS_TOKEN` (Code Brain Rule 5; Infra Brain 591, 592 and 593). The status fix
  keeps the requirement that a failed `--token` run closes visibly, not as a success (Rule 4). No
  logging reference changed (Rule 3). The leak path still ends in revoke and reissue (1032). No
  endpoint changed (212 doesn't apply).
