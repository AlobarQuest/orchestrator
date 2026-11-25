Outcome: done

# Build report: a broker lane stays registered through the allowlist

Handoff: `sds-hq/state/handoffs/broker-registration-allowlist.md` (ADR-0055 Amendment 32 item 4,
Open item 13 option A). Worker W-308. Branch `hq/broker-registration-allowlist`, based on
`origin/main` at `3675d24f`, which had not moved when work started.

PR: PR_URL (head `PR_HEAD`). Not merged.

## What I built

`registered_lanes` (`scripts/deploy_orchestrator.py`) registers a lane by its token row in
`.cred-consumers.toml` or by its table in `lanes/secrets-broker/allowlist.toml`, both read at
`origin/main`.

The change, by requirement:

1. **One reader, the broker's parser.** A new `_allowlist_at(world, commit)` holds the read that
   `_broker_allowlist` did inline: `git ls-tree` for the file, `git show`, then `parse_allowlist`.
   A missing file returns `{}`, so no broker lane is registered. Any other git failure, and every
   `AllowlistError`, is a `Refused`. `_broker_allowlist` (the lane phases) and `registered_lanes`
   both call it, so registration and the compose check can't read the allowlist differently.
2. **Unknown lane.** `allowlisted_lanes(allowlist)` refuses a table for a lane outside `LANES`
   (`LANES = frozenset(LANE_CHECKS)`): "`lanes/secrets-broker/allowlist.toml` at main names
   unknown lanes [...]". This mirrors the unknown token-lane refusal.
3. **A lane in both sources.** `registered_lanes` returns the union, so inert landing (fallback
   token row plus table) is registered once. The "more than one token entry" refusal stays inside
   `lanes_in_registry`, which only sees token rows.
4. **Shape.** `lanes_in_registry(text)` is unchanged. The allowlist side is its own small function,
   `allowlisted_lanes`, and it takes the parsed allowlist, not text. That way the git read and the
   parse live once, in `_allowlist_at`, which the lane phases already needed. The code review found
   my first version duplicated `_broker_allowlist`'s read, and this shape removes the duplication.
5. **Held equal by a test.**
   `test_the_allowlists_tables_are_exactly_the_broker_lanes_composes` reads the repository's own
   files. For every lane in `LANES`, it requires `lanes/<lane>/compose.yaml` to exist. It asks
   `broker_lane` with an allowlist that names every lane, so only the compose half (no token, own
   socket bind, no stray broker mount) decides. Then it asserts that set equals the allowlist's
   tables. My reading matches the ADR text: a table can't register a lane whose compose maps a
   token, and a broker compose can't exist without a table.
6. **The repository's registered set.**
   `test_the_repositorys_registry_and_allowlist_register_seventeen_lanes` is a sibling of the
   thirteen-lanes test. It asserts that the allowlist adds exactly `bump-driver`, `change-window`,
   `estate-landing`, and `follow-up-mint` beyond the 13 token lanes, and that the union is 17.
   The unit tests for requirements 1 to 3 use the `lanes` git fixture, so they go through
   `origin/main`:
   - `test_an_allowlist_table_registers_a_broker_lane_without_a_token_entry` (no file: empty;
     then a table registers its lane)
   - `test_an_unreadable_allowlist_at_main_is_refused` (TOML error, and a malformed table)
   - `test_an_allowlist_table_for_an_unknown_lane_is_refused`
   - `test_a_lane_with_a_token_entry_and_a_table_is_registered_once`
7. **Docs.** `docs/operations/deploy.md`, "Registered lanes and the lane-token matcher", says a
   lane is registered by its token entry or its allowlist table. It also says what removing each
   does, and that a lane with both stays registered until both are gone.

Stop points checked: `origin/main` was still `3675d24f`. Every allowlisted lane has a tracked
compose, and `broker_lane` says each is a broker lane, because the equality test passes. No new
registry shape, credential, or production step was needed.

One existing guard changed: `tests/architecture/test_runtime_edges.py`'s `DEPLOY_COMMANDS` pins
the commands the release path runs. The release path now runs `git ls-tree`, the read-only
missing-file probe the lane phases already use. I added it to the set with a reason comment. No
existing test or deploy path refuses in a way the ADR doesn't describe.

## Behavior change

With the four broker lanes registered, `prepare` and `finish` treat them as they treat token
lanes:

- **`prepare`** calls `require_lanes_current(world, previous, sha, mode)`. In production mode a
  broker lane with a passed `--add` whose last finished run isn't at the served commit refuses the
  release: "production serves X, but `<lane>` runs sha-Y". A broker lane with no passed `--add` is
  skipped. Development mode skips the lag check, and an unfinished run refuses in both modes, as
  before.
- **`finish`** calls `lanes_to_follow(world)` after fetching and before the binder. It includes
  each broker lane with a passed `--add`. `finish` runs nothing for a lane. It prints, and returns
  in `lane_phases`, the command `uv run python -m scripts.deploy_orchestrator lanes-prepare --lane
  <lane> --tag sha-<built_commit>` for each lane to follow.
- **A broker lane's follow run.** When an operator runs that printed `lanes-prepare --tag`, `_plan`
  routes it through `_plan_change`, then reads the lane's compose at the tag's commit. It computes
  `run.brokered` through `_broker_allowlist`/`broker_lane`, and runs
  `compose_broker_refusals(run.compose_sent, lane)` for every change kind, `--tag` included. A
  refusal stops the run with "the tracked compose of lane `<lane>` at `<tag>`: ...". `add_refusals`
  runs only for `--add`, and a `--token` run on a broker lane is refused, as before. So yes, a
  broker lane's `--tag` follow goes through `compose_broker_refusals`.
- **Allowlist failures.** An unreadable allowlist at `origin/main`, or a table for a lane the
  command doesn't know, now refuses `prepare` and `finish` (before the binder), as an unreadable
  registry already did. The code review raised this whole-release blast radius. I kept it because
  requirement 2 asks for it, and it matches the token registry's behavior.

I didn't change that flow.

## Tests and checks

- `make check` on the final head: PASS, 14,783 collected, 14,782 passed, 1 skipped, 0 failed.
  (An earlier run failed `test_the_deploy_command_runs_exactly_its_commands` on the new
  `git ls-tree`; that is the guard change described earlier.)
- `ruff format`, `ruff check`, and `pyright` on the changed files: clean.
- `code-standards risk --repo .`: `normal`.
- `/code-review` (medium) on `3675d24f..HEAD`: four nits. I fixed the duplicated allowlist read.
  I kept the release-wide refusal (requirement 2) and the population count (requirement 6). The
  race between `require_lanes_current`'s and `lanes_to_follow`'s reads already existed for the
  token registry, and this change doesn't widen it.

### Mutation checks

I applied each mutation to a scratch copy of the file, ran the new tests with `__pycache__`
cleared, and restored the file. Six code mutations were rerun after the refactor. All seven were
killed and none survived.

| Mutation | Killed by |
|---|---|
| M1 `registered_lanes` ignores the allowlist | `..._registers_a_broker_lane_without_a_token_entry`, `..._unreadable_allowlist_at_main_is_refused` |
| M2 unknown allowlist lane accepted | `test_an_allowlist_table_for_an_unknown_lane_is_refused` |
| M3 a lane in both sources refused as "more than one token entry" | `test_a_lane_with_a_token_entry_and_a_table_is_registered_once` |
| M4 an `AllowlistError` swallowed as `{}` | `test_an_unreadable_allowlist_at_main_is_refused` |
| M5 a missing allowlist not tolerated (no `ls-tree` probe) | `..._registers_a_broker_lane_without_a_token_entry`, plus two existing lane-phase tests |
| M6 a table added for `execution-watch`, whose compose maps a token | `test_the_allowlists_tables_are_exactly_the_broker_lanes_composes` |
| M7 the `change-window` table deleted (broker compose without a table) | `test_the_allowlists_tables_are_exactly_the_broker_lanes_composes` (assertion: extra `change-window` on the compose side) |

### Token-shape grep

`git diff 3675d24f..HEAD` grepped for the BWS token prefix shape, a full UUID, and an inline
`BWS_ACCESS_TOKEN =` assignment: no match. The new fixtures use empty `read = []` lists, so they
hold no UUIDs.

## Files changed

- `scripts/deploy_orchestrator.py`: `allowlisted_lanes`, `_allowlist_at`, and the widened
  `registered_lanes`. `_broker_allowlist` now calls `_allowlist_at`.
- `tests/scripts/test_deploy_lanes.py`: six new tests and the `load_allowlist` import.
- `tests/architecture/test_runtime_edges.py`: `git ls-tree` added to `DEPLOY_COMMANDS`.
- `docs/operations/deploy.md`: the "Registered lanes and the lane-token matcher" section.
- This report.

## Self-review

- No `.cred-consumers.toml` row and no allowlist table were edited.
- Amendment 32 item 4's second change ("A conversion's compose is checked") wasn't started.
- Each refusal names files and lane names only, never a UUID.
- The equality test asks `broker_lane` with an every-lane allowlist, which ties it to the real
  predicate rather than restating its compose conditions. If `broker_lane`'s compose half changes,
  the test follows it.

## Concerns

- If a later PR adds an allowlist table for a lane before `LANE_CHECKS` knows it, every release
  is refused until that is fixed. This is the same as a token row for an unknown lane, and
  requirement 2 asks for it. Land the `LANE_CHECKS` entry first, or in the same PR.
- After this lands, the first production `prepare` lag-checks the four broker lanes. Any of them
  whose last passed run isn't at the served commit refuses the release until it follows the
  served tag.

## Standards followed

Construction-mode ruling: `~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`.

- **Code Brain 3 (logging):** I added no logging path. `deploy_orchestrator.py` is an operator
  command, and the new failures surface as `Refused`, which the command already reports.
- **Code Brain 4 (error handling):** every git failure and every `AllowlistError` raises
  `Refused`. The one deliberate empty result is a missing file, probed by `git ls-tree` and
  commented inline. M4 and M5 prove both halves.
- **Code Brain 5 (security):** the allowlist holds UUIDs, not values. No refusal prints a UUID.
  The new refusal prints lane names, as the token-lane refusal does, and the parser's errors are
  the broker's own existing messages.
- **Infra Brain 591, 592, and 593 (BWS tokens):** no token or token-shaped literal is in the code,
  tests, or fixtures. The token-shape grep of the diff matched nothing.
- **Infra Brain 1032 (`cred.exposure-rotate`):** not applicable. No credential was read, changed,
  or exposed.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. The change adds no endpoint.
