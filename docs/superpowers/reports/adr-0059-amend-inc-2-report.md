Outcome: done

# Build report: adr-0059-amend-inc-2 (ADR-0059 Amendment 2)

- **Worker:** W-40
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

Line numbers are at the branch head (file
`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`).

| Lines | Passage | Follows |
|---|---|---|
| 9-11, 13 | Header `Decided by` (D-24 to D-28) and `Relates to` (amendment 19) | amendment 19 |
| 142-143 | Lane-phase bullet: the change list gains `--token` | item 6 |
| 164-166 | `lanes-prepare`: `--token` writes the compose at the running tag, no variable | item 6 |
| 211-216 | `lanes-finish`: `--token`'s pass conditions and its close as failed with no inverse | item 6 |
| 422-424 | Per-resource-variable bullet: the executor writes with `redeploy` false, never redeploys | item 6 |
| 493-496 | Decision 2 token bullet: each lane token keeps a keeper (D-24) | item 1 |
| 654-656 | `.cred-consumers.toml` bullet: first issue and early or leak reissue before increment 7 | items 3, 10 |
| 688-691 | Decision 3: the VM root creates the ping-URL secret with the keeper command, in the moving increment's cut-over | items 1, 3, 8 |
| 697-699 | Decision 3: the ping URL binds by shape alone; cut-over step 4 proves it (D-28) | item 5 |
| 935-938 | Increment 2: its ADR-0055 amendment is amendment 19 | amendment 19 |
| 943-986 | Increment 2 verify items, each marked answered (row at create; `redeploy` false; two waiting mints; the ping-URL wait and bind; D-25 actor; D-26 revoke order) | "What the code settles", items 2, 3, 5, 6, 8, 10 |
| 1040-1051 | Increment 7: the redeploy's actor points to amendment 19; the leak order (D-26); R10 | items 6, 7 |
| 1079-1087 | Cut-over step 1: amendment 19 item 8's order (keepers, rows, grants, mint, ping URL) | item 8 |
| 1143-1145 | Cut-over step 4: the check's last-ping read proves the ping URL (D-28) | item 5 |
| 1332-1340 | Consequences, executor: keepers at rest on the VM (D-24) | item 1 |
| 1341-1360 | Consequences, lane-token rotation: age rule, D-25 proposer, reach bind (no hand stage), D-26, D-27 | items 4, 7, 9, 10 |
| 1400-1404 | Dependencies, ADR-0055: increment 2's amendment is amendment 19 | amendment 19 |
| 1407-1409 | "Devon's decisions" introduction: D-24 to D-28 | amendment 19 |
| 1445-1460 | Q3: the executor never redeploys before increment 7; reissues before it; keepers | items 1, 6, 9, 10 |
| 1511-1514 | Q8: the amendment is amendment 19, and it adds keepers | item 1 |
| 1695-1851 | Amendment 2 | amendment 19 |

The five passages HQ found are all covered: (1) lines 142-216 and Amendment 2 item 1; (2) lines
1040-1051 and item 2; (3) lines 943-986 and item 3; (4) lines 1341-1360 and item 4; (5) lines
493-496, 1332-1340, 1445-1460, 1511-1514 and item 5. The search also found these: the
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
- Code reads that back the verify items: `scripts/deploy_orchestrator.py` at `d485d051`:
  `UNFINISHED, FINISHED, RESTORED` (line 1017), `last_finished` takes only `FINISHED` (1165-1169),
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
