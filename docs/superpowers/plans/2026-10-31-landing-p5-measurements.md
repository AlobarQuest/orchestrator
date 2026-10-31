# Landing Part 5: measurements

## Task 3, Step 4: update-bot commit census (read-only, 2026-10-31)

The 14 members of the production member record (the list in
`2026-10-30-landing-p4-measurements.md`, Step 2). For each open pull request the update bot
authored, `GET /pulls/{n}/commits?per_page=100` was read with `gh api` and both rules applied to
the same rows: the old hold-term rule (the one bot-authored commit on the branch) and the gate's
rule (`commits[0]`, bot-authored and verified).

- Open update-bot pull requests: **4** (brain 2, orchestrator 2; the other 12 members have none).
- Commits per pull request: brain#110 1, brain#109 2, orchestrator#508 1, orchestrator#506 1, so
  **1 of the 4** had more than one commit (all authored by the update bot).
- Old and new rule agree on **4 of 4**; disagreements: **0**.

The population is small, so this shows no case of the change moving a live answer; the
disagreement cases are pinned by `tests/contract/test_update_bot_commit_agreement.py`.

## Ship and live proof (2026-10-31 EDT)

| Time | Step | Actor |
|---|---|---|
| 11:07 | change-manager#148 merged, after a read showed no rotation unit ready or claimed | build session |
| 11:09 | change-manager served 9570036d; the new route answered 401 and a control route 404 | deploy workflow |
| 11:10 | orchestrator#566 merged (1da6b682) | build session |
| 11:13 | `1da6b68-p5-amd64` deployed, digest `sha256:15ef6ed1…`, no migration; no lander pass fell in the gap | build session |
| 11:38 | Pass retired items 78, 79, 89, 94, 104, 115, 117, 118 and 125, each to `resolved`, each citing run 41175882784 | `deploy-watcher` |
| 12:40 | Pass reported all 9 `[finished]`, with no exception, finding or incomplete line | `deploy-watcher` |

The estate lander's first pass inside the landing hours is 02:15 on 2026-11-01, after this record. It asks only
about `approved` records, and the 9 are `resolved`.

`_resettle` and the factory's resettle have no live proof: the state was never observed and can't be produced
without a fault in GitHub. Their evidence is the tests and the mutation passes.
