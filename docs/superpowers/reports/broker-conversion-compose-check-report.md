Outcome: done

# Report: a conversion's compose is checked (ADR-0055 Amendment 32 item 4)

Worker W-312. Branch `hq/broker-conversion-compose-check`, based on `290282e0` (`origin/main` had
not moved when work started). PR: https://github.com/AlobarQuest/orchestrator/pull/813. The code
commit is `a29d2a5c`; this report is committed after it on the same branch. The PR isn't merged.

## What I built, by requirement

1. **The clause is shared.** `token_refusals(service, lane, allowlist)` in
   `scripts/deploy_orchestrator.py` holds the clause `add_refusals` had: a compose that maps
   `BWS_ACCESS_TOKEN` must map it as `${<LANE>_BWS_ACCESS_TOKEN}`, and one that maps none must pass
   `broker_lane`, or it's refused with the same "… is no broker lane: …" text. `add_refusals`
   calls it. A new `_compose_refusals(kind, …)` runs `compose_broker_refusals` for every change,
   `add_refusals` for an `--add`, and `token_refusals` for a `--tag`. `_plan` calls it. I added that
   helper because the inline branch put `_plan` over ruff's C901 limit (11 > 10).
2. **The mapping check.** Before this change a `--tag` didn't check that the mapping equals
   `${<LANE>_BWS_ACCESS_TOKEN}`. It now does, because the check is part of the shared clause. I
   probed every tracked compose at `290282e0` (`lane_service`, `_environment` and `broker_lane`
   against the tracked allowlist), and none is refused:

   | Lanes | Result |
   |---|---|
   | bump-proposer, change-proposer, deploy-watcher, execution-watch, landing-ledger, membership-check, pin-watcher, revision-watcher, rotation-decomposer, rotation-proposer, secrets-broker, unit-verifier, work-carrier | token, mapped as `${<LANE>_BWS_ACCESS_TOKEN}` |
   | bump-driver, change-window, estate-landing, follow-up-mint, inert-landing | tokenless, `broker_lane` holds |

   No stop point applies: the clause refuses no lane at `290282e0`.
3. **`--schedule` and `--token`.** `_plan_change` sets `run.commit = last.commit` for both, and
   `_plan` reads the compose and the allowlist at `run.commit`. So neither run's compose or
   allowlist can differ from the last finished run's, and I didn't apply the clause to
   `--schedule`. `--token` keeps its broker refusal (amendment 30 item 10), unchanged.
   One consequence: a lane whose last finished `--tag` ran before this rule isn't checked again by
   a later `--schedule`. Every tracked compose passes the rule, so no lane is in that position
   today.
4. **Tests** in `tests/scripts/test_deploy_lanes.py`, after
   `test_a_tokenless_lane_that_isnt_set_up_on_the_broker_is_refused`:
   - `test_a_tag_to_a_tokenless_compose_that_isnt_a_broker_lanes_is_refused`, with the cases
     `not-allowlisted`, `no-allowlist-file` and `no-bind`. Each is refused with "is no broker lane".
   - `test_a_tag_to_a_compose_mapping_another_lanes_token_is_refused`, for the mapping check that
     requirement 2 added.
   - `test_a_tag_converting_a_token_lane_to_the_broker_finishes`: a token lane's `--tag` to a
     commit that carries both the broker bind and the allowlist table plans, rechecks and finishes,
     and the run is `brokered`.
5. **Docs.** `docs/operations/deploy.md`'s `--tag` bullet, in the list of changes that follows
   "Each phase takes `--lane <lane>` and exactly one change", now states the requirement. A `--tag`
   to a compose that neither maps the lane's token variable nor is a broker lane's is refused. A
   lane converts by a `--tag` to a commit that carries the bind and the allowlist table. See
   concerns for why the edit is in this passage.

## Tests and checks run

- `make check`, run on the code commit's tree with test database `orchestrator_test_hq_w_312`:
  exit 0, with 14,788 items collected, 14,787 passed, and 1 skipped. Lint and type-check are clean.
- `tests/scripts/test_deploy_lanes.py`: 141 passed, the new tests included, both before
  and after the C901 refactor.
- `code-standards risk --repo .`: `normal`, so no vendor review is needed.
- `/code-review` (low) on the commit returned no findings.

### Mutation checks

I ran each mutation on the final source, cleared `__pycache__` before and after, ran the new tests
and their `--add` neighbours, and then restored the file. `diff -q` against the saved copy
confirmed each restore.

| Mutation | Failing tests |
|---|---|
| M1: `_compose_refusals` drops the `--tag` branch | the three `tag_to_a_tokenless_compose…` cases and `tag_to_a_compose_mapping_another_lanes_token…` (4) |
| M2: `token_refusals` drops the mapping check | `tag_to_a_compose_mapping_another_lanes_token…` (1) |
| M3: `token_refusals` drops the broker clause | three `tokenless_lane_that_isnt_set_up…` (`--add`) and three `tag_to_a_tokenless_compose…` cases (6) |
| M4: the `--tag` branch passes `{}` in place of the commit's allowlist | `tag_converting_a_token_lane_to_the_broker_finishes` (1) |

All four mutations are killed.

### Token-shape grep

`git diff origin/main...HEAD` shows no added line that matches the BWS token shape or a long
base64 run. The diff names variables (`BWS_ACCESS_TOKEN`, `${<LANE>_BWS_ACCESS_TOKEN}`) and paths
only.

## Files changed

- `scripts/deploy_orchestrator.py`: adds `token_refusals` and `_compose_refusals`;
  `add_refusals` and `_plan` call them.
- `tests/scripts/test_deploy_lanes.py`: three new tests (five cases).
- `docs/operations/deploy.md`: the `--tag` bullet.
- `docs/superpowers/reports/broker-conversion-compose-check-report.md`: this report.

## Self-review

- One clause, one place. The refusal text is unchanged, so the existing `--add` tests still match.
- A git or parse failure still raises `Refused`: `_allowlist_at` is unchanged, and the new code
  catches nothing.
- `_broker_allowlist` reads the allowlist only for a compose that maps no token. A token compose
  has no use for it, and a tokenless one is checked against the commit's own allowlist, as in
  `--add`.

## Concerns

- **The docs passage is outside Amendment 32's passages table.** The table lists two `deploy.md`
  passages: "Before a lane token's revoke" and "Registered lanes and the lane-token matcher".
  Neither describes what a `--tag` checks of its compose. The broker sections the handoff names are
  per-lane descriptions, and none describes the `--tag` checks either. So I edited the `--tag`
  bullet, the one passage that states what a `--tag` requires. HQ can move the sentence if the
  table is meant to bound this edit.
- **`lanes-finish` doesn't check a token's presence on a `--tag`.** It checks only for `--add` and
  `--token` (`finish_mismatches`). The ADR's context already records this. So a converting
  `--tag` finishes without proof that the container holds no `BWS_ACCESS_TOKEN`; the conversion
  test finishes with no `--token-present`. This is out of this handoff's scope, so I report it as a
  follow-up and didn't change it.
- No `--add` test covers the mapping check (M2 killed only the new `--tag` test). The new test
  covers the shared clause for both callers.

## Standards followed

- **Code Brain 3 (logging).** I added no logging path. The command reports through `Refused` and
  its printed lines, as before.
- **Code Brain 4 (error handling).** Nothing is swallowed. The new functions return refusal lists
  that `_plan` raises as one `Refused`. Allowlist reads and parse failures still go through
  `_allowlist_at`, which raises `Refused`.
- **Code Brain 5 (security).** Refusals name the variable, the expected `${…}` reference, the bind
  path and the allowlist path. They never include a value.
- **Infra Brain 591, 592 and 593 (`bws.no-token-in-tracked-files`, `bws.no-token-in-git-history`,
  `bws.bootstrap-token-not-inline`).** No token or token-shaped literal appears in the code, tests
  or fixtures. The grep is in the "Token-shape grep" section.
- **Infra Brain 1032 (`cred.exposure-rotate`).** Not applicable: no credential was read, changed or
  surfaced.
- **Infra Brain 212 (HTTPS for public FQDNs).** Not applicable: no endpoint was added.
- **Construction mode.** I worked under
  `~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`.
