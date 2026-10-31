# Landing Part 5: measurements

## Task 3, Step 4: update-bot commit census (read-only, 2026-10-31)

The 14 members of the production member record (the list in
`2026-10-30-landing-p4-measurements.md`, Step 2). For each open pull request the update bot
authored, `GET /pulls/{n}/commits?per_page=100` was read with `gh api` and both rules applied to
the same rows: the old hold-term rule (the one bot-authored commit on the branch) and the gate's
rule (`commits[0]`, bot-authored and verified).

- Open update-bot pull requests: **4** (brain 2, orchestrator 2; the other 12 members have none).
- Old and new rule agree on **4 of 4**; disagreements: **0**.

The population is small, so this shows no case of the change moving a live answer; the
disagreement cases are pinned by `tests/contract/test_update_bot_commit_agreement.py`.
