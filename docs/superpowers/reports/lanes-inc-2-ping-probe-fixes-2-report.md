Outcome: done

# lanes-inc-2-ping-probe fix round 2: report

PR #632, branch `hq/lanes-inc-2-ping-probe`. Started from `9bb973ed`. Fix commit `e8a9acb4`; this
report is the commit after it (the PR's head).

## What I built

- The `healthchecks` `PLATFORM_EDGES` row lists the check lookup beside the listing:
  `("GET", _CHECK_PATH)`, where `_CHECK_PATH = "/api/v3/checks/" + "a" * 40` is a placeholder of the
  40-lowercase-hex `unique_key` shape. It isn't a real key or a UUID.
- `ROUTES` holds `_CHECK_PATH`, so `test_every_listed_route_is_in_the_corpus` holds.
- `PROBES` gains five near misses the predicate must refuse: 39 hex, 41 hex, 40 uppercase hex
  (`"/api/v3/checks/" + "A" * 40`), 39 hex plus `g`, and `_CHECK_PATH + "/pause"`. `near_misses`
  adds the trailing-slash, `x`-suffixed and newline forms of `_CHECK_PATH`.
- The `healthchecks-ping-url` `PROVIDER_EDGES` comment states the rule: no client of its own; its
  lookup is the `healthchecks` platform row's client (ADR-0055 amendment 21). The row stays empty.
  The adapter builds no client: `HealthchecksPingUrlProvider.probe` calls the injected
  `Healthchecks.check_status`, and the row's exactness test passes with `built = ()`.
- Optional item, done: the bind refusal reads "the read-only key finds no such check: a URL wrong
  in its UUID's first half, or another project's check". The string occurs once in the source
  (line 108; the handoff's ~236 has no match, and the file has 143 lines). No test pins its text.
- Committed round 1's report unchanged:
  `docs/superpowers/reports/lanes-inc-2-ping-probe-fixes-1-report.md`.

No change to the rotation worker's behavior, amendment 21's text or status line, or the rendered
package bytes.

## Tests and checks

On database `orchestrator_test_hq_w_54` (dropped afterwards):

- `ruff check src tests`: all checks passed. `ruff format --check src tests`: 963 files formatted.
  `pyright`: 0 errors.
- `pytest tests/architecture tests/rotation_worker tests/rotation_proposer
  tests/contract/test_rotation_step_contract.py`: 3,742 passed, before and after the probe fix
  described in the following section. The count doesn't move because the edge tests parametrize by
  lane and row, not by corpus path.
- Every row is still exact: no other lane's or row's predicate accepts a new probe, so nothing to
  report there.
- Mutation controls on `readers.py` (restored after each, `__pycache__` cleared, `git diff` empty
  after), running `test_each_platform_reach_is_exactly_its_routes[healthchecks]`:

  | Mutant | Result |
  | --- | --- |
  | `UNIQUE_KEY` with `re.I` | killed |
  | `[0-9a-f]{39,41}` | killed |
  | `[0-9a-z]{40}` | killed |
  | `fullmatch` to `match` | killed |
  | `[0-9a-f]{40}(/pause)?` | killed |
  | lookup branch dropped | killed |
  | unmutated control | passed |

  6 of 6 killed, no survivors. The first run had one survivor: the uppercase probe was
  `_CHECK_PATH.upper()`, which uppercases the `/api/v3/checks/` prefix too, so the prefix test
  refused it before the hex test ran. I replaced it with `"/api/v3/checks/" + "A" * 40`, and the
  `re.I` mutant then failed.
- CI on the pushed head: see the following section.

## CI

Quality on `e8a9acb4` (run 42431984427): success, 14,118 passed and 11 skipped. The pass count is
the same as at `9bb973ed` because the edge tests parametrize by lane and row, not by corpus path,
so the new probes add assertions, not test cases. The report-only commit after it runs CI again.

## Files changed

- `tests/architecture/test_rotation_edges.py`: 314-316 (`_CHECK_PATH`), 323 (the `healthchecks`
  row's routes), 452-453 (the ping-URL row comment), 715 (`ROUTES`), 764-768 (`PROBES`).
- `src/rotation_worker/adapters/healthchecks_ping_url.py`: 108-111 (the refusal string).
- `docs/superpowers/reports/lanes-inc-2-ping-probe-fixes-1-report.md`: added, copied unchanged.
- `docs/superpowers/reports/lanes-inc-2-ping-probe-fixes-2-report.md`: this report.

## Self-review

- The placeholder is 40 `a` characters, valid hex and lowercase, so the predicate accepts it for
  the right reason and it can't be read as a real `unique_key`.
- The "first half" wording is accurate: `unique_key` is the SHA-1 of the first 16 of the UUID's 32
  hex characters, so the lookup can't tell a URL wrong only in its second half.
- `_CHECK_PATH` is a module-private test constant, not a vocabulary used in a membership test, so
  `VOCABULARY_REGISTRY` doesn't apply.

## Concerns

None.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** the diff adds no logging. The refusal
  string is a return value that existing code records as before.
- **Code Brain rule 4 (never swallow an exception silently):** the diff adds no exception handling.
- **Code Brain rule 5 (never log secrets, including the ping URL, its UUID, and the `X-Api-Key`):**
  the refusal string names no value. The test path is a placeholder in a test file, not a logged
  value, and not derived from any real check.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history):** the diff holds no token.
- **Infra Brain 2890 (never commit a ping URL; examples use a placeholder):** the diff holds no ping
  URL. The only check identifier is the `"a" * 40` placeholder.
- **Infra Brain 1032 (an issue package states no revoke):** this round adds no revoke and doesn't
  touch the rendered package.
