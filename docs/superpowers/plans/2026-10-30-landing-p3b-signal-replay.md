# Landing Part 3b, Task 0: the served-revision signal replayed

Measured 2026-10-30 by `scripts/replay_deploy_signal.py`. Read-only: every call was a GET.

> **Since Task 2** the script replays the orchestrator's own time-based rule
> (`revert_watch.served_verdict`, imported), not the row-counting draft measured below. After Task 2's
> second fix round (the settle time delays rather than drops a reading; production behind the bump's
> parent is not the bump's fault), re-run on the same cached data (2026-10-30): 60 `clean`, 10
> `pending` (the nine landings before the watcher's first row, and brain #70 with app-brain alone
> behind), 1 `bad`: brain #79, at 12:54 UTC. The cache holds no heartbeat history, so the replay
> stands in the watcher's newest row of any kind for it. The tables below are the draft's.

## Result in brief

- **No landing judged `bad`.** The stop condition in Task 0 step 2 (a `bad` landing that was in
  fact harmless) did not occur.
- **The rule missed the one landing that really broke production.** Brain #79 (SQLAlchemy 2.0.54 to
  2.1.1) produced an image that crashed at import in all four brains. Production served the
  previous build for about 8.5 hours until a person fixed forward. The rule judges #79 `clean`.
- **The rule cannot reach `bad` for a stuck deployment.** `production_revision` rows are
  content-addressed, so an application that stays on one wrong commit produces one row, however
  many hourly passes repeat it. "Two most recent readings both behind" needs production to move
  from one wrong commit to a different wrong commit. That never happened in this data.
- **Of the nine `failed` rollouts,** five judge `clean` (#65, #74, #79, #81, #89), two judge
  `pending` with one reading each (#70, #82), and two have no readings (#59, #60 predate the
  watcher's first row on 2026-09-08). They do not all judge `clean`, and #79's `clean` is wrong.
- **The plan's first Finding needs correcting.** The nine failures were not all a poll that gave up
  too early. #79 was a broken image. In #65, #70, #74 and #89, `app-brain` alone never served the
  merge commit, and the next day's landing healed it. That is an application-specific deploy
  failure on an image the other three brains served, so the bump was not its cause.

## Method

1. **Landings.** `gh pr list --state merged` for `AlobarQuest/brain` and
   `AlobarQuest/change-manager`, authored by Dependabot and merged by `app/alobar-sds-dispatch`
   (the estate lane), merged on or after 2026-09-01. The brief asked for 2026-09-30, but six of the
   nine `failed` rollouts predate that, so the window starts earlier. The table covers both: the 41
   landings since 2026-09-30 judge 31 `clean` and 10 `pending`.
2. **Applications.** The member record's `apps` (`GET /api/v1/members`): four brains
   (`app-brain`, `code-brain`, `infra-brain`, `open-brain`) and `change-manager`.
3. **Readings.** `GET /api/v1/observations?source_system=revision_watcher&observation_type=production_revision&subject_type=service`:
   479 rows, the earliest received 2026-09-08 14:57 UTC. The route has no paging and returned the
   whole history.
4. **Reading time is `received_at`.** The watcher sets `observed_at` to the served commit's
   committer date, a fact-derived clock that keeps its rows idempotent. It says nothing about when
   production was asked. Task 2 must use `received_at` (or an equivalent the watcher adds).
5. **The rule (Task 2's served-revision half).** A reading counts when it was received between 30
   minutes and 48 hours after the merge and names a served commit. Its relation to the merge commit
   comes from `gh api repos/<r>/compare/<merge>...<served>`: `identical` or `ahead` is fine,
   `behind` is behind, anything else is diverged. The landing is `bad` when every application's two
   most recent counted readings are both behind or diverged, `pending` when any application has
   fewer than two, and `clean` otherwise. The checks-on-`main` half is Part 3a's and is not
   replayed.
6. **Rollout verdict.** change-manager `GET /api/items?source=deploy`, then each item's
   `/deploy-observations` `current.verdict`.
7. **A time-based companion measure.** `all-behind hours` is how long, within the 48-hour window,
   every application's latest counted row was behind or diverged. A row stands until that
   application's next row. This is what the store says while production is stuck, and it is the
   measure that sees #79.

Secrets: the script reads each bearer itself. It reads the Keychain item (`BWS_ACCESS_TOKEN_SDS`
for the observer bearer, `BWS_ACCESS_TOKEN_VPS_BACKUP` for change-manager's read bearer) into its
own process and passes it to `bws` only in that one subprocess's environment. The two identities
are never in one environment and no value is printed.

## Results

`readings` is the counted readings per application, in member-record order. `all-behind h` is the
companion measure.

| Repository | PR | Merge commit | Merged (UTC) | Rollout verdict | Readings | Replay verdict | All-behind h |
|---|---|---|---|---|---|---|---|
| brain | #52 | `32c9f4bfae` | 2026-09-01 06:15 | success | 0,0,0,0 | pending | 0.0 |
| brain | #55 | `ed5f8a5a60` | 2026-09-02 06:15 | success | 0,0,0,0 | pending | 0.0 |
| brain | #57 | `e007bdc402` | 2026-09-03 09:15 | success | 0,0,0,0 | pending | 0.0 |
| brain | #59 | `aa65710b5f` | 2026-09-04 06:15 | failed | 0,0,0,0 | pending | 0.0 |
| brain | #58 | `4b80fac8d9` | 2026-09-05 07:15 | success | 0,0,0,0 | pending | 0.0 |
| brain | #60 | `de1bb53b20` | 2026-09-06 06:15 | failed | 0,0,0,0 | pending | 0.0 |
| brain | #63 | `f57535f7b3` | 2026-09-17 07:15 | success | 2,2,2,2 | clean | 0.0 |
| brain | #64 | `a930a7fb99` | 2026-09-18 06:15 | success | 1,1,1,1 | pending | 0.0 |
| brain | #65 | `aabb5fd07e` | 2026-09-19 06:15 | failed | 2,2,2,2 | clean | 0.0 |
| brain | #69 | `6134374086` | 2026-09-20 06:15 | success | 2,2,2,2 | clean | 0.0 |
| brain | #70 | `fe4d9d106f` | 2026-09-21 06:15 | failed | 1,1,1,1 | pending | 0.0 |
| brain | #71 | `5813547795` | 2026-09-24 08:15 | success | 3,3,3,3 | clean | 0.0 |
| brain | #72 | `490a924740` | 2026-09-25 06:15 | success | 2,2,2,2 | clean | 0.0 |
| brain | #73 | `e49bb171bf` | 2026-09-26 06:15 | success | 3,3,3,3 | clean | 0.0 |
| brain | #74 | `71de18850c` | 2026-09-27 06:15 | failed | 3,3,3,3 | clean | 0.0 |
| brain | #75 | `b655ef58c6` | 2026-09-28 07:15 | success | 1,1,1,1 | pending | 0.0 |
| brain | #78 | `f517e94410` | 2026-10-01 08:15 | success | 4,4,4,4 | clean | 0.0 |
| brain | #80 | `80b620ce17` | 2026-10-02 07:15 | success | 4,4,4,4 | clean | 0.0 |
| brain | #79 | `271a2ac8de` | 2026-10-03 06:15 | failed | 4,4,4,4 | clean | 8.5 |
| brain | #82 | `f84e359893` | 2026-10-04 07:15 | failed | 1,1,1,1 | pending | 0.0 |
| brain | #81 | `2a7000c82f` | 2026-10-05 06:15 | failed | 2,2,2,2 | clean | 0.0 |
| brain | #87 | `25939038b9` | 2026-10-08 08:15 | success | 3,3,3,3 | clean | 0.0 |
| brain | #88 | `a2f8233236` | 2026-10-09 06:15 | success | 2,2,2,2 | clean | 0.0 |
| brain | #89 | `b5895e2c61` | 2026-10-10 06:15 | failed | 2,2,2,2 | clean | 0.0 |
| brain | #91 | `05f3c04484` | 2026-10-11 06:15 | success | 2,2,2,2 | clean | 0.0 |
| brain | #90 | `ce1143bedc` | 2026-10-12 06:15 | success | 1,1,1,1 | pending | 0.0 |
| brain | #92 | `cb58d3f80e` | 2026-10-15 08:15 | success | 4,4,4,4 | clean | 0.0 |
| brain | #94 | `8f30a4242f` | 2026-10-16 07:15 | success | 2,2,2,2 | clean | 0.0 |
| brain | #95 | `6b6d676f11` | 2026-10-17 06:15 | success | 1,1,1,1 | pending | 0.0 |
| brain | #93 | `0721c00bff` | 2026-10-18 06:15 | success | 2,2,2,2 | clean | 0.0 |
| brain | #96 | `2442d7b9bc` | 2026-10-19 06:15 | success | 1,1,1,1 | pending | 0.0 |
| brain | #99 | `32081d1f09` | 2026-10-23 06:15 | success | 4,4,3,4 | clean | 0.0 |
| brain | #101 | `fff3c8527d` | 2026-10-24 07:15 | success | 3,3,3,3 | clean | 0.0 |
| brain | #100 | `53860578af` | 2026-10-25 06:15 | success | 2,2,2,2 | clean | 0.0 |
| brain | #103 | `4f9e505f8b` | 2026-10-26 06:15 | success | 2,2,2,2 | clean | 0.0 |
| brain | #104 | `c652400c44` | 2026-10-27 06:15 | success | 2,2,2,2 | clean | 0.0 |
| brain | #102 | `b8728a3321` | 2026-10-28 06:15 | success | 1,1,1,1 | pending | 0.0 |
| brain | #106 | `88f3b1e8b9` | 2026-10-29 08:15 | success | 1,1,1,1 | pending | 0.0 |
| brain | #107 | `6b3e3996b6` | 2026-10-30 06:15 | success | 0,0,0,0 | pending | 0.0 |
| change-manager | #67 | `8f585a2aa1` | 2026-09-01 07:15 | success | 0 | pending | 0.0 |
| change-manager | #75 | `ad3f836992` | 2026-09-02 06:15 | success | 0 | pending | 0.0 |
| change-manager | #76 | `907c9cd5cb` | 2026-09-03 06:15 | success | 0 | pending | 0.0 |
| change-manager | #79 | `2ac2128fe8` | 2026-09-08 07:15 | success | 4 | clean | 0.0 |
| change-manager | #80 | `7d3a3e9253` | 2026-09-09 06:17 | success | 1 | pending | 0.0 |
| change-manager | #81 | `5bcfb7b89c` | 2026-09-10 06:15 | success | 2 | clean | 0.0 |
| change-manager | #85 | `9c83a37ba5` | 2026-09-16 06:15 | success | 2 | clean | 0.0 |
| change-manager | #86 | `6540579f1a` | 2026-09-17 07:15 | success | 1 | pending | 0.0 |
| change-manager | #87 | `df68ef9b03` | 2026-09-21 06:15 | success | 2 | clean | 0.0 |
| change-manager | #90 | `ff17446ad3` | 2026-09-22 06:15 | success | 2 | clean | 0.0 |
| change-manager | #91 | `c427a6fa2f` | 2026-09-23 06:15 | success | 2 | clean | 0.0 |
| change-manager | #92 | `93cee76308` | 2026-09-24 06:15 | success | 1 | pending | 0.0 |
| change-manager | #93 | `49a4bb7380` | 2026-09-26 06:15 | success | 5 | clean | 0.0 |
| change-manager | #100 | `de516d2b59` | 2026-09-29 07:15 | success | 2 | clean | 0.0 |
| change-manager | #101 | `7f4e6c3a7c` | 2026-09-30 06:15 | success | 2 | clean | 0.0 |
| change-manager | #102 | `e9299cfe61` | 2026-10-01 06:15 | success | 3 | clean | 0.0 |
| change-manager | #103 | `1f4e4ad464` | 2026-10-02 07:15 | success | 1 | pending | 0.0 |
| change-manager | #105 | `9cc9d542f7` | 2026-10-06 06:15 | success | 4 | clean | 0.0 |
| change-manager | #106 | `ce56f01406` | 2026-10-07 07:15 | success | 2 | clean | 0.0 |
| change-manager | #107 | `1abca591df` | 2026-10-08 07:15 | success | 1 | pending | 0.0 |
| change-manager | #111 | `f49a0c3c71` | 2026-10-15 06:15 | success | 3 | clean | 0.0 |
| change-manager | #112 | `66789ba75f` | 2026-10-16 06:15 | success | 2 | clean | 0.0 |
| change-manager | #113 | `29d461392a` | 2026-10-17 07:15 | success | 2 | clean | 0.0 |
| change-manager | #114 | `226f1c0cf6` | 2026-10-18 06:15 | success | 4 | clean | 0.0 |
| change-manager | #124 | `9439722fd2` | 2026-10-20 07:15 | success | 4 | clean | 0.0 |
| change-manager | #126 | `362eefaebc` | 2026-10-21 07:15 | success | 4 | clean | 0.0 |
| change-manager | #125 | `7b965c24f0` | 2026-10-23 07:15 | success | 4 | clean | 0.0 |
| change-manager | #127 | `3f62ae50d6` | 2026-10-24 06:15 | success | 2 | clean | 0.0 |
| change-manager | #136 | `546d9703ae` | 2026-10-27 06:15 | success | 6 | clean | 0.0 |
| change-manager | #138 | `d0b520d4b1` | 2026-10-28 07:15 | success | 5 | clean | 0.0 |
| change-manager | #137 | `7a37b08527` | 2026-10-29 07:15 | success | 3 | clean | 0.0 |
| change-manager | #139 | `3b28da2763` | 2026-10-30 07:15 | success | 0 | pending | 0.0 |

Totals: 71 landings (39 brain, 32 change-manager). 46 `clean`, 25 `pending`, 0 `bad`. Of the 25
`pending`, 11 have no reading (nine predate the watcher, and brain #107 and change-manager #139
landed today) and 14 have exactly one. In 13 of those 14 the one reading is fine everywhere. In
brain #70, `app-brain` is behind and the other three are fine.

Across all 355 counted readings, 346 are fine and 9 are behind. Every behind reading is followed by
a fine one for the same application. None is diverged.

## The nine failed rollouts

| PR | What production served | Replay verdict | Correct? |
|---|---|---|---|
| #59, #60 | No readings: before 2026-09-08 | `pending` | No data |
| #65 | `app-brain` stayed on the previous build; the other three served #65. Next day all four served #69. | `clean` | Yes |
| #70 | `app-brain` stayed on #69's build for three days; the others served #70. | `pending` | Yes, no act |
| #74 | `app-brain` on #73's build; the others served #74's descendant. Healed by #75. | `clean` | Yes |
| #79 | All four brains stayed on #80's build from the rollout until 15:22, when a person's fix forward (e6cd48a) came up. | `clean` | **No: a real bad deploy** |
| #81 | `code-brain` one reading behind, then fine. | `clean` | Yes |
| #82 | All four fine on their one reading. | `pending` | Yes, no act |
| #89 | `app-brain` stayed on #88's build (the deploy job's own log says so); the others served #89. Healed by #91. | `clean` | Yes |

Brain #79's fix forward says, in its commit message: "SQLAlchemy 2.1 no longer installs greenlet by
default ... the 2.1.1 image (#79) died at import ..., all four new containers crashed on start, and
the deploy job timed out while the old containers kept serving." The watcher recorded all four
brains `behind` at 06:52 and nothing else until 15:22. In real time, the rule would have held #79
`pending` for those 8.5 hours. It judges `clean` only because the fix forward is a descendant.

## Watcher gaps

The watcher runs hourly on the operator's machine, and a row appears only when its content changes.
A gap in rows is therefore either "nothing changed" or "no pass ran". Example: change-manager has no
row from 2026-10-19 17:41 to 2026-10-20 13:46, although `main` moved at 07:15 (#124), so no pass ran
overnight. A time-based rule must tell these apart, for example from the watcher's Healthchecks
pings or a per-pass heartbeat, before it counts a standing row as a repeated reading.

## Conclusion

The served-revision signal is the right source: it shows #79's outage plainly, and it shows the
other eight `failed` rollouts as harmless, which the rollout verdict cannot. But the rule as Task 2
states it never fires on a stuck deployment, because a stuck application produces a single row.
On this data it judges nothing `bad`, including the one bump that broke production. Task 2 needs a
time-based rule: every application's latest reading received at least 30 minutes after the merge
is behind or diverged and has stood for N hours with the watcher confirmed running. Only #79 has
any all-behind time (8.5 hours), so N between 1 and 8 would have caught it and judged every other
landing `clean`. The data also settles an inconsistency in the plan: Task 2's test list says "one
application of four behind twice is `bad`", while its rule says every application. "Every" is
right. `app-brain` alone stayed behind after #65, #70, #74 and #89 on an image the other three
served, and "one of four" would have reverted four good dependencies. These are design changes to
Task 2 and a correction to the plan's first Finding, so the plan goes back to Devon before Task 2.
