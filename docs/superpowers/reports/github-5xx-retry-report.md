Outcome: done

# Report: github-5xx-retry (W-112)

- PR: [AlobarQuest/orchestrator#678](https://github.com/AlobarQuest/orchestrator/pull/678), ready for review, not merged.
- Content commits: `d48c022c` (the retry) and `c27bb8f6` (no-op sleep in five existing ledger tests), on `origin/main` `3df369b2`, which was still the head when I pushed.
- Head: the commit that adds this report, which follows `d48c022c`.

## What I built

The deploy watcher's and the landing ledger's GitHub readers now repeat a GET answered 502, 503 or 504, at most three attempts in all, with a 2-second pause between attempts.

- **Shared helper:** `get_through_gateway_errors` in `src/estate_clients/confined.py`, with the constants `GATEWAY_ERRORS`, `GATEWAY_ATTEMPTS = 3`, and `GATEWAY_RETRY_WAIT = 2.0`. It returns the last response and interprets no status. Each reader keeps its own status handling and wording. `ConfinedClient.request` is unchanged, so no other lane gains a retry.
- **Readers:** `GitHubReader._get` (deploy watcher) and `GitHubReader._request` (ledger) call the helper. Each reader takes an injectable `sleep` keyword (default `time.sleep`), following the precedent of `alert_hub_client`'s injected `sleep`.
- **Behavior kept:**
  - A 404 returns `None`.
  - The deploy watcher raises on any non-200 status after one request, 301 included.
  - The ledger raises `LedgerError` from 400 up after one request, and returns a 301 body as it does today (HQ's answer (a)).
  - A persistent gateway error raises the existing error naming the last status.
  - `TransportFailure`, 500, 429 and 403 are never retried.

### Why one shared helper

The handoff named `test_layering.py`, but it scans only `src/orchestrator`, so it doesn't govern this choice. The guards that do are `layers.toml` and `test_out_of_process_isolation.py`:

- `estate_clients` is in the `runtime` layer.
- Both programs, which are in the `sensors` layer, already import `estate_clients.confined`.
- Both programs' isolation rows already permit `estate_clients`.

So the helper adds no new layer edge. The module's docstring gains one bullet stating the helper's contract.

The one guard change: `test_out_of_process_isolation.py` rows now list `time` for both programs and `collections` for the ledger, each with a one-line reason. These are real new standard-library imports, the injectable sleep's default and its type, so the rows declare them.

## Tests and checks

The following tests were added:

- `tests/deploy_watcher/test_github.py`:
  - `test_a_gateway_error_then_an_answer_returns_the_answer[502|503|504]`
  - `test_a_gateway_error_on_every_attempt_raises_the_last_status_after_three_requests`: answers 502, 503, then 504, and would answer 200 on a fourth request. It asserts `ReadError` naming 504, three requests, and two sleeps.
  - `test_any_other_failure_raises_after_one_request[500|301|429|403]`
  - `test_a_404_still_returns_none_after_one_request`
- `tests/landing_ledger/test_github.py`: the same four tests with `LedgerError`, using 500, 429, and 403 for the third. It also adds `test_a_301_is_read_as_today_after_one_request`, which checks one request, no sleep, and the body returned.
- `tests/estate_clients/test_confined.py`:
  - `test_a_transport_failure_after_a_gateway_error_raises_without_another_attempt`
  - `test_a_gateway_retry_still_asks_the_permit_first`

I ran each mutation from the committed tree, then restored it by inverse edit and cleared `__pycache__` each time. The diff was empty afterward. All seven mutants were killed:

| Mutation | Killed by |
|---|---|
| Drop 503 from the set | `…returns_the_answer[503]` (both clients), `…after_three_requests` (both), the helper's transport-failure test |
| Drop 502 | `…returns_the_answer[502]` and `…after_three_requests` (both clients) |
| Drop 504 | `…returns_the_answer[504]` (both clients) |
| Add 500 | `test_any_other_failure_raises_after_one_request[500]` (both clients) |
| Attempts 3 → 2 | `…after_three_requests` (both clients) |
| Attempts 3 → 4 | `…after_three_requests` (both clients) |
| Remove the sleep | every recovery and persistent test, and the helper's transport-failure test |

Check results:

- **`make check`** on `c27bb8f6` against `orchestrator_test_hq_w_112`: exit 0, ruff and pyright clean, **14,715 passed, 1 skipped** (10 xdist workers, with `--durations=5`). The first run failed two `test_out_of_process_isolation` cases on the new `time` and `collections` imports. I declared them in the rows, as described in an earlier section, and the rerun passed.
- **Real sleeps in existing tests:** a `--durations` run over the tests of both programs, `estate_clients`, and every lane that imports the readers showed eight ledger tests taking about 4.1 seconds each. Five reader constructions in `tests/landing_ledger/test_cli.py`, `test_audit_pass.py`, and `test_github.py` answer 502 or 503 on purpose. I injected `sleep=lambda _: None` into each, and those suites dropped from 36 seconds to 2.8 seconds. The slowest entries in the final `make check` are setup and teardown in tests this diff doesn't touch.
- **`/code-review`** (medium) on the branch diff: six points, none applied:
  - Retry-After handling and other lanes' readers are out of scope by the handoff, and both are listed as follow-ups.
  - The outage runtime cost is disclosed in the self-review.
  - A `ConfinedClient` retry option contradicts the plan HQ approved (`request` unchanged).
  - The loop structure is correct for the fixed constant of 3.
  - The point that the helper is generic: it repeats only GETs, and only for a program that calls it.
- **Security scan** from the repo root: no BLOCK findings. Two WARN findings remain: `bws.secret-files-gitignored` (`*-migration/`) and `bws.manifest-matches-usage` (an undeclared UUID). This diff touches neither `.gitignore`, the manifest, nor any UUID.

## Files changed

- `src/estate_clients/confined.py`
- `src/deploy_watcher/github.py`
- `src/landing_ledger/github.py`
- `tests/estate_clients/test_confined.py`
- `tests/deploy_watcher/test_github.py`
- `tests/landing_ledger/test_github.py`
- `tests/landing_ledger/test_cli.py`
- `tests/landing_ledger/test_audit_pass.py`
- `tests/architecture/test_out_of_process_isolation.py`
- `docs/superpowers/reports/github-5xx-retry-report.md` (this report)

## Self-review

- The final attempt sits outside the loop, so the helper never sleeps after the last answer. A `TransportFailure` on any attempt propagates at once, and the reader turns it into its existing "unreachable" error.
- These programs also use the two readers and inherit the retry: `change_proposer`, `estate_lander`, and `inert_lander` (deploy watcher), and `bump_proposer` (ledger), confirmed by their imports. The retry is safe for them because it applies only to GETs; both readers refuse every other method before the transport.
- **Cost in a sustained outage:** about 4 more seconds per failed read, so roughly 3 minutes for a 42-item pass. The deploy watcher runs hourly (`StartInterval` in its plist).
- No test sleeps for real: every new test injects `sleeps.append`, and the five existing gateway-status readers inject a no-op `sleep`.
- The nested `src/deploy_watcher/CLAUDE.md` and `src/landing_ledger/CLAUDE.md` rules don't apply. The change alters no derived facts, observations, or workflow transcription.

## Concerns

- The landing ledger reads a 301 body as an answer, and an empty-body 301 raises `JSONDecodeError`, not `LedgerError`. This is listed as a follow-up in the PR (HQ's answer (a)).
- The change edits a guard the handoff's standards don't cover. `test_out_of_process_isolation.py` rows gain `time` (both programs) and `collections` (the ledger), each with a one-line reason. These declare real new imports; they don't widen a pattern.
- A retry is not logged, because neither module logs. A recovered blip is therefore invisible. A retry count in the pass output is a possible follow-up.
- HQ's renamed `github-5xx-retry-question-answered.md` is untracked in the worktree. I didn't commit it, because it's HQ's file.

## Follow-ups (in the PR body)

Other GitHub clients make a one-shot GET with no gateway retry:

- `bump_driver/client.py`
- `rotation_worker/github_actions.py`
- `tool_installer/github.py`
- `revision_watcher/github.py`
- `pin_watcher/github.py`
- `work_watcher/github.py`
- `fork_sync/estate.py`
- `membership_check/declaration.py`
- `unit_verifier/required_checks.py`
- `orchestrator/services/verifier/github_checks.py`

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** the change adds no logging and no file logging. Neither module logs today.
- **Code Brain rule 4 (never silently swallow an exception):** a retried gateway answer is a response, not an exception. The third failure reaches the reader's existing `ReadError` or `LedgerError`, naming the last status, which the persistent-failure tests check. A `TransportFailure` is never caught by the helper. The change adds no `except … pass`.
- **Code Brain rule 5 (never log secrets, auth headers, tokens, or bodies):** the change adds no log line. The error messages are unchanged and name only the path and status.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline `BWS_ACCESS_TOKEN`):** the diff contains no token and no BWS reference. The security scan reports no BLOCK finding.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value during this work. Test tokens are the placeholders `"t"` and `"fixture"`.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply, because the change adds or alters no endpoint. Both readers still use `https://api.github.com`.
- **Infra Brain 2890 (never commit a ping URL):** the diff contains no ping URL, and ping behavior is unchanged.
