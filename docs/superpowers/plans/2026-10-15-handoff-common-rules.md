# Handoffs of 2026-10-15: rules every one of them follows

Three handoffs run while HQ drives the orchestrator operator bearer's first rotation (ADR-0055
increment 6), in the `live_estate` window from 02:00 to 06:00 America/New_York on 2026-10-16.
Each handoff names this page. Read it first.

- `2026-10-15-handoff-bws-manifest-scanner-gap.md`
- `2026-10-15-handoff-schedule-rotation-proposer.md`
- `2026-10-15-handoff-observer-drift-reporter-inventory.md`

## Don't touch what the rotation touches

Until HQ reports the operator-bearer rotation finished, don't:

- deploy, restart or change the settings of the orchestrator (`sds.alobar.net`, Coolify
  application `eqj5l7k705fhi12x9i74fqf0`), and above all not `ORCHESTRATOR_M2M_CREDENTIALS`;
- reinstall, reconfigure or run a pass of the rotation executor (`orb -m rotation-executor`);
- read, write or move BWS secret `bd71bed1-4aac-4af8-9094-b4970180bc59`, or anything in
  `Rotation / Work`;
- act on work units `085ab6d0-381c-54ef-8a95-c3fa0e54491e` or `e74a58b7-1123-5d54-961d-3ca65cb9f7c1`,
  or on change record 138;
- run `scripts/run-rotation-proposer.sh --submit` or `scripts/run-work-carrier.sh --register` by
  hand.

Merging to `main` is fine: merged isn't deployed, and the VM runs a pinned revision.

## How to work

- Read `CLAUDE.md` in each repository you change, in full. The orchestrator's "Known Non-obvious
  Invariants" apply even where a handoff doesn't repeat them.
- Edit only in a worktree with its own venv and test database
  (`docs/operations/local-development.md`). The main trees run scheduled lanes; never check out a
  branch, edit or run `uv` there.
- Never print, paste or pass a secret value as a tool argument. Evidence is a sha256 prefix and a
  length. Every parsed `bws` call passes `--color no`; a check of whether a BWS token works runs
  with an empty `HOME`.
- Don't call infraops tools that return secrets: `coolify_get_deployment`, the Coolify env-list
  tools, `cloudflare_list_pages_projects`.
- Run the repository's full gate (`make check`) and `/code-review` before declaring a code change
  done. Prove a new guard fires on a known-bad input.

## Closing steps, for every handoff

- **You merge** your own pull requests once their checks pass, squash-merge, and confirm the state
  is `MERGED` before deleting a branch.
- **Tear down** at the end: the worktree, its test database, and the merged branch. Then check the
  main tree's `git status` for stray files you created, and fast-forward the main tree only if it is
  clean and on `main`.
- **Report** back to Devon: what merged (pull request numbers you read from `gh`, never predicted),
  what you measured, and anything you found and didn't fix.
