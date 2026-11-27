Outcome: done

# Report: the broker token's project-reach bind (ADR-0055 Amendment 34 item 5, outcome C)

Worker W-327, branch `hq/broker-token-project-reach-bind`, based on `18ff4afd`. One orchestrator
PR, not merged (HQ merges after review). No live call to BWS, Coolify, Healthchecks, or the VPS,
and no production act. `rotated_by_sds` stays `false` on `secrets-broker-bws-token`; no registry
row changed.

## What I built, against the task list

1. **`src/rotation_worker/adapters/bws_machine_token.py`.** A child script `_REACH_IDS` beside
   `_REACH`: the same three calls (log in, list projects, list secrets), printing
   `{"projects": [...], "secrets": [...]}`, sorted ids only, or `unreadable` on any failure. A
   method `reach_sets(value) -> tuple[frozenset[str], frozenset[str]] | None` runs it in the same
   fresh child (`_fresh`: new client, empty throwaway `HOME`, token on stdin) and answers `None` on
   no answer, `unreadable`, non-JSON, or JSON that isn't two lists of string ids. `reach` and
   `same_identity` are unchanged.
2. **`src/rotation_worker/adapters/bws_lane_token.py`.** `BwsLaneTokenProvider` takes a new
   keyword, `project_reach`: the row's declared `(projects, secrets)` sets. `build_provider` sets
   it only when `reach_projects` is non-empty; otherwise it's `None`. `bind_refusal` calls
   `_sees_declared_reach`:
   - `project_reach` is `None` (every lane token): the exact digest comparison, as before.
   - Otherwise: `reach_sets`; it passes only when the listed projects equal the declared ones
     exactly and every declared secret is listed. `None` refuses.

   Both paths refuse with the existing message. `sharing_reach`, the other-keeper check, and
   `accepts` are unchanged. The module docstring's "Bind by declared reach" bullet states both
   forms, the 2026-11-27 measurement, and that the project form is weaker than an exact match (a
   broker token with one extra single-secret grant would bind), as Amendment 34 item 5 says.
3. **`tests/rotation_worker/test_lane_token_project_reach.py`** (new). The fake login is a
   `BwsMachineTokenProvider` subclass whose listing is a table, recording which of `reach` and
   `reach_sets` it was asked. The five cases are in the following section, plus a child-script
   test.
4. **The `--extra` doc error.** In `scripts/bws_reach_probe.py`: the usage line and `NO_SDK` say
   `uv sync --frozen --group rotation-worker` and "group". In #822's report, line 112 says "syncs
   the group" with `--group`. `tests/scripts/test_bws_reach_probe.py` doesn't assert the text, so
   it's unchanged. Beyond the named lines, see "Edits beyond the named lines".
5. **The measurement line.** #822's report, after item 5's outcomes: "Measured 2026-11-27:
   outcome C", with counts 1 and 1 and no ids.

One more edit was needed outside the named files:
`tests/architecture/test_rotation_edges.py::test_each_child_login_imports_only_the_named_sdk` pins
each child script's imports, and it failed on the new script. I added
`"_REACH_IDS": {"bitwarden_sdk", "json", "sys"}`. That's mechanical, with no design choice, so it
isn't a stop point.

## The five test cases

| Case | Test | Result |
|---|---|---|
| A project-reach token seeing the declared secrets plus the projects' own secrets binds | `test_a_project_reach_token_seeing_its_projects_own_secrets_binds` | pass |
| One more project, one fewer project, or one declared secret missing refuses | `test_a_project_reach_token_seeing_other_than_its_row_declares_refuses` (3 parameters) | pass |
| A failed listing refuses | `test_a_failed_listing_refuses` | pass |
| A lane token (`reach_projects = []`) with one extra secret still refuses, and asks only `reach` | `test_a_lane_token_with_one_extra_secret_still_refuses` | pass |
| The real `secrets-broker-bws-token` row takes the project path, and binds on a listing holding its projects' own secrets; every other real lane-token row takes the exact path | `test_the_real_broker_row_takes_the_project_path_and_every_lane_row_the_exact_one` | pass |

`test_the_child_prints_sorted_ids_only_and_reach_sets_reads_them` runs `_REACH_IDS` against a
stand-in SDK (sorted ids, no token in the output, `unreadable` on a failed listing) and checks
that `reach_sets` answers `None` on each malformed answer.

**Mutation controls.** I applied each of six mutants to `bws_lane_token.py`, cleared
`__pycache__`, and ran the new file. Every mutant failed at least one test, so none survived:

- Drop the secrets subset.
- Drop the project equality.
- Require exact sets.
- Skip the `None` check.
- Never set `project_reach` in `build_provider`.
- Always take the exact path.

## Tests and checks run

- `make check` in the worktree against its own database `orchestrator_test_hq_w_327`, migrated
  to head, with `SECURITY_STANDARDS_DIR` at the fixture and `PGTZ=UTC`.
  - **First full run** (at `9d326ec7`): ruff "All checks passed!", pyright 0 errors, and pytest
    **1 failed, 14942 passed, 1 skipped**. The failure was the child-login import guard. I fixed
    it in `559eafd3`. The guard file and the new test file then gave 101 passed.
  - **Final run** (on the tree with this report): exit 0. Ruff "All checks passed!", pyright 0 errors, and pytest **14943 passed, 1 skipped** (11 warnings, 8 min 46 s).
  - That's 14944 collected. #822 collected 14936 on this base, and this PR adds 8 test items.
- `code-standards risk`: `high-risk` (auth: both adapters and the new test), so I ran both
  reviews:
  - **`vendor-review`:** "Review complete: 2 of 2 seats succeeded; agy off by configuration".
    Claude and Codex both approved, with 0 findings kept and 0 dropped.
  - **`/code-review`:** I didn't run it as a separate pass; the vendor review's Claude seat
    covered the same diff.
- **Token-shape grep:** I grepped `git diff origin/main...HEAD` for the BWS token shape (the
  `0.`-uuid-secret form) and found 0 matches. Every value in the new tests is built at run time
  from `secrets.token_hex` or `uuid.uuid4`.

## Files changed

- `src/rotation_worker/adapters/bws_machine_token.py`
- `src/rotation_worker/adapters/bws_lane_token.py`
- `tests/rotation_worker/test_lane_token_project_reach.py` (new)
- `tests/architecture/test_rotation_edges.py` (one line: the new script's import pin)
- `scripts/bws_reach_probe.py` (docs only)
- `docs/superpowers/reports/broker-token-rotation-code-report.md` (docs only)
- `docs/superpowers/reports/broker-token-project-reach-bind-report.md` (this report)

None of W-326's files is touched.

## Self-review

- The project path changes what binds only for a row with a non-empty `reach_projects`. Only
  `secrets-broker-bws-token` has one, and the real-row test pins that every other row asks the
  exact digest.
- `sharing_reach` still compares declared sets, so no two rows can declare the same reach.
- **Edits beyond the named lines.** I fixed the same "extra" error at #822's report lines 87 and
  227. I also replaced a wrong reason in two places, the probe's `_sdk_client` docstring and the
  report's line 87: "CI's `uv sync --frozen` doesn't install it" is false. The `dev` group
  includes `rotation-worker` (`pyproject.toml`), and `uv sync` installs `dev`; the `Quality` job
  runs `uv sync --frozen` (`.github/workflows/quality.yml:85`). Both now say the orchestrator
  image's `--no-dev` sync doesn't install the group, which is true (the `pyproject.toml` comment).
  Line 227 now says the script tests run whether or not the group is installed.
- **Refusal message.** The handoff quotes it as "what it can see isn't the reach its registry row
  declares". The code's existing text has "exactly" ("isn't exactly the reach…"). I kept the
  existing text on both paths, as the handoff's "the existing message" says. On the project path,
  "exactly" is looser than the check. Change it only if HQ wants to; one existing test matches
  only on "declares".

## Concerns

- **Stale runbook text in W-326's files.** `docs/operations/rotation-executor.md:205` ("it sees
  exactly the reach its registry row declares") and about line 1698 ("is exactly the row's
  declared reach") describe only the exact form. Neither names the project form for a row with
  `reach_projects`. I didn't edit them, since they're W-326's files. A follow-up needs to add the
  project form.
- **The weaker bind is accepted, not removed.** A broker token whose account gains one extra
  single-secret grant still binds. That's Amendment 34 item 5's stated trade-off, recorded in the
  module docstring.

## Standards followed

- **Code Brain 3 (logging).** The child script prints its JSON to stdout for the parent only. The
  parent reads it in-process and logs nothing new. No file logging.
- **Code Brain 4 (error handling).** The child catches the SDK's exception and prints
  `unreadable`. `reach_sets` turns that, a missing answer, or malformed JSON into `None`, and
  `bind_refusal` refuses on it; nothing passes silently. The refusal reaches a person:
  - `inbox.bind` (`src/rotation_worker/inbox.py:160`) returns it.
  - `worker.py`'s bind handler (about lines 792-815) files it as the inbox alert act: "The value
    in the inbox isn't taken for …" / "It stays in the inbox, unused: <reason>."
  - A bind that succeeds logs `inbox_bound` with the fingerprint only.
- **Code Brain 5 (security).** Only ids leave the child, never a key, value, name, or the token.
  The token goes on stdin, never in an argument. A test asserts the token isn't in the child's
  output.
- **Infra Brain 591, 592, 593.** No token or token-shaped literal is in code, tests, or docs. Test
  values are built at run time, and the diff's token-shape grep found 0 matches.
- **Infra Brain 1032 (`cred.exposure-rotate`).** R10's loaded-fingerprint proof (`LoadedRead`,
  `consumer_loaded`) is untouched, and no registry row changed.
- **Infra Brain 212 (HTTPS for public FQDNs).** Not applicable: no endpoint is added.

## PR

Opened against `main` by the commit that adds this report; the URL is in the PR's own listing (`gh pr list --head hq/broker-token-project-reach-bind`). Code head before the report commit: `559eafd3`. Not merged.
