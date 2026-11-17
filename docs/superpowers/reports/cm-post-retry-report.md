Outcome: done

# cm-post-retry: report

- PR: AlobarQuest/orchestrator#730 (ready for review, not merged)
- Branch: `hq/cm-post-retry`, rebased onto `origin/main` `e7744356`
- Content commit: `0d49810c`
- Head: the commit that adds this report, directly on `0d49810c`

## What I built

The deploy watcher's change-manager client now sends each request through a bounded retry. A
request that raises `TransportFailure` is sent again, up to three attempts in all, with a 1 s wait
between them. The first answer is returned whatever its status, so the client's status handling
didn't change:

- A 409 raises `RefusedError` after one request.
- Any other status of 400 or more raises `ChangeManagerError` after one request.
- A failure that outlasts three attempts raises the same message as before:
  `change-manager is unreachable for <METHOD> <path>: <ErrorType>`.

The retry covers both reads (`GET /api/items`, `GET /api/items/{id}/deploy-observations`) and both
writes (`POST …/deploy-observation`, `POST …/deploy-supersession`). I confirmed both writes are
safe to replay; the following "Replay safety of each write" section gives the reasons.

### The helper: a sibling in `estate_clients.confined`

I added `request_through_transport_failures(client, method, path, *, sleep, **kwargs)` with the
constants `TRANSPORT_ATTEMPTS = 3` and `TRANSPORT_RETRY_WAIT = 1.0`. I didn't extend
`get_through_gateway_errors`, for these reasons:

- It's GET-only and retries gateway *statuses* (502, 503, 504). On a `TransportFailure` it raises
  at once, and `test_a_transport_failure_after_a_gateway_error_raises_without_another_attempt`
  pins that behavior.
- Two GitHub readers call it (`deploy_watcher/github.py`, `landing_ledger/github.py`). Changing it
  would change those lanes, which is out of scope.
- The two rules answer different questions. One repeats a read that got a gateway answer; the
  other repeats any request that got no answer, which the caller must judge safe to replay. Keeping
  them separate keeps that judgment at the call site.

Strata: `tests/architecture/test_layering.py` covers only `src/orchestrator`, so it doesn't apply
here. The rule that applies is in `test_out_of_process_isolation.py`: every out-of-process lane may
import `estate_clients`, and `estate_clients` imports no program. The helper imports nothing new.
`layers.toml` already places `src/estate_clients/**` and `tests/estate_clients/**`, and I added no
files. I updated the module docstring: its claim that nothing retries a request that got no answer
was no longer true.

`ChangeManagerClient` takes `sleep: Callable[[float], None] = time.sleep`, following
`GitHubReader` (#678) and `alert_hub_client`.

## Replay safety of each write

I read change-manager `origin/main` at `67111f7`.

- **`POST /api/items/{id}/deploy-observation`** (`app/api.py` `observe_deploy`, then
  `app/deploy_settlement.py` `record_rollout_and_settle`). This matches the handoff.
  `record_deploy_observation` keys the row on
  `item:merge_commit:run_id:attempt:fact_digest`. When the key exists it returns the stored row
  and the route answers 200. A concurrent duplicate's `IntegrityError` is rolled back and replayed
  as the winner's row. The settlement step that follows writes nothing once the record is
  `resolved` or `wontfix`. A replay of the same body therefore adds no row and no event. A 409
  comes only from `DeployObservationRefused`, which the client still raises at once.
- **`POST /api/items/{id}/deploy-supersession`** (`supersede_deploy`, then
  `app/deploy_retirement.py` `supersede_deploy_change`). Its docstring says it's idempotent, and
  the code agrees. After checking the source and the pull request number, it returns `False`
  without writing if the record is already `resolved` or `wontfix`, and the route answers 200 with
  the item. A first request that succeeded leaves the record `resolved`, so a sequential replay
  changes nothing.

Neither write changes a record twice on a sequential replay, so the handoff's stop point didn't
fire. One residual applies to both (see "Concerns"): neither path takes a row lock. If a retry
reached the server while the first request was still running, both could pass the terminal check
and each append an event. The status ends at `resolved` either way.

## What I found about the dropping hop

I based this on the code and docs, plus one statement of general proxy behavior that I label
where it's used and didn't verify in this estate. I touched no infrastructure and read no live
logs.

1. **The error type says the client's own connection was reset, not closed or answered.** In
   httpcore's sync backend (`httpcore/_backends/sync.py`, lines 80 and 125) every `OSError` during
   a read maps to `ReadError`. That includes `ECONNRESET` and `ssl.SSLError`, which is an
   `OSError`. A peer that closed cleanly without answering raises `RemoteProtocolError` ("Server
   disconnected without sending a response", `_sync/http11.py` line 230). So the client's TCP or
   TLS peer reset the connection, or sent a TLS-level error, after the request was written.
2. **The client's TCP peer is the first proxy, not uvicorn.** The client calls
   `https://change-mgr.alobar.net`, which is Cloudflare-proxied (root CLAUDE.md invariant on
   `change-mgr.alobar.net`). A failure on a later hop reaches the client as an HTTP answer.
   *General proxy behavior, not verified here:* Traefik that reuses a connection uvicorn has just
   closed answers 502, and Cloudflare answers a 52x page when its origin fails. On that
   assumption, neither reaches the client as a transport error. uvicorn runs with no keep-alive flag (`entrypoint.sh`:
   `uvicorn app.main:app --host 0.0.0.0 --port 8000`), so its default `timeout_keep_alive = 5`
   applies (read from uvicorn 0.51.0 `config.py`, line 228, in change-manager's local venv; I
   didn't read the image's version). That timeout governs the Traefik-to-uvicorn connection, and on
   the preceding assumption a drop there shows up as a 502 status, not a `ReadError`. **So the hop that can produce what
   was seen is the one whose connection the client holds: Cloudflare's edge, or something on the
   network path between the VPS and that edge (for example the container's NAT state).** Code and
   docs can't tell those two apart.
3. **The code explains why only POSTs failed.** httpx's pool keeps an idle connection for 5 s
   (`keepalive_expiry` default, `httpx/_config.py` line 178). In a pass (`cli.py` 240 to 330), the
   change-manager GETs come first or back to back. Each POST comes after that item's GitHub reads,
   which go over a different connection. That makes the POST the one change-manager request sent
   on a connection that has sat idle for seconds but less than 5 s, so httpx reuses it instead of
   opening a new one. The roughly 3 s gaps fit this. A stale pooled connection that an upstream
   peer has already dropped fits the timing and the error type. It's a candidate, consistent with
   the evidence, not proven. I can't explain why change-manager's access log has no POST lines,
   including for POSTs that succeeded. That gap stops the log from showing whether a failed POST
   reached the app, so I relied on the replay-safety reading and not on a claim that it didn't.

The retry works whichever hop it is. When a request fails mid-response, httpcore's
`HTTP11Connection` calls `_response_closed()` in its exception path (`_sync/http11.py` line 132),
and that method closes any connection whose exchange didn't reach `DONE` on both sides (line 250).
The dead connection therefore leaves the pool, and the next attempt opens a new one.

## Tests and checks

Tests added in `tests/deploy_watcher/test_client_surface.py`:

- `test_a_read_that_got_no_answer_is_repeated_and_answers`: one `ReadError`, then the listing.
- `test_an_observation_that_got_no_answer_is_repeated_and_answers`: two resets, then a 200.
- `test_a_supersession_that_got_no_answer_is_repeated_and_answers`: one reset, then the item.
- `test_three_requests_without_an_answer_raise_the_unreachable_error` (GET and observation POST):
  the exact existing message, after exactly three requests and two sleeps.
- `test_an_answered_refusal_is_never_repeated` (409 raises `RefusedError`, 422 raises
  `ChangeManagerError`): one request, no sleep.
- `test_a_gateway_answer_to_a_write_is_not_repeated`: a 502 is an answer; one request.

Tests added in `tests/estate_clients/test_confined.py`:

- `test_a_transport_retry_still_asks_the_permit_first`: a refused path never reaches the
  transport and never sleeps.
- `test_a_transport_retry_returns_the_first_answer_whatever_its_status`: a 500 after a reset is
  returned, not repeated.

No write was judged unsafe, so no not-retried-write test applies.

Mutations: I edited the source, cleared `__pycache__`, ran the two test files, and restored the
source. Every mutation was killed:

| Mutation | Killed by |
|---|---|
| `TRANSPORT_ATTEMPTS` 3 to 2 | `test_an_observation_that_got_no_answer_is_repeated_and_answers`, `test_three_requests_without_an_answer_raise_the_unreachable_error` (both) |
| `TRANSPORT_ATTEMPTS` 3 to 4 | `test_three_requests_without_an_answer_raise_the_unreachable_error` (both) |
| Retried exception `TransportFailure` to `RuntimeError` | all three "is repeated and answers" tests, both "three requests" cases, `test_a_transport_retry_returns_the_first_answer_whatever_its_status` |
| Retried exception widened to `Exception` (would catch the refusal) | `test_a_transport_retry_still_asks_the_permit_first` |
| `_post` calls `self._client.request` directly (writes skip the retry) | the observation and supersession "is repeated" tests, the POST "three requests" case |

Checks:

- `make check` against `orchestrator_test_hq_w_191`, with `SECURITY_STANDARDS_DIR` set: exit 0.
  ruff and pyright are clean. pytest collected **15,275 items**: 15,274 passed and 1 skipped
  (316 s).
- `/code-review` (medium) on `origin/main..hq/cm-post-retry`: five points, none needing a code
  change. See "Self-review" for each one and what I did with it.
- CI on #730: see "CI" at the end of this report.
- Security scan from the repo root (`security_scan.cli . --category security`): no BLOCK. It
  reported two WARNs that predate this change and are outside the diff (gitignore glob
  `*-migration/` not covered; an undeclared UUID `aaff45ca-…` in code) and one INFO judgment item.

## Files changed

- `src/estate_clients/confined.py`: the helper, its two constants, and a docstring update.
- `src/deploy_watcher/change_manager.py`: the injectable `sleep`, plus `_request`, which `_get`
  and `_post` both use.
- `tests/deploy_watcher/test_client_surface.py`: eight test cases.
- `tests/estate_clients/test_confined.py`: two test cases.
- `docs/superpowers/reports/cm-post-retry-report.md`: this report.

## Self-review

- Every status branch and error string in `_get` and `_post` is byte-identical. Only the call that
  produces `response` changed.
- The retry happens below the permit check: `ConfinedClient.request` asks the permit on every
  attempt, and a refusal isn't a `TransportFailure`, so it raises from the first attempt.
- No log line was added. A retry is silent, and the last failure still raises the existing message
  (method, path, error type).
- No existing deploy-watcher test raises a transport error through `ChangeManagerClient`. The
  new tests inject `sleep`. With `--durations=5`, `tests/deploy_watcher` and `tests/estate_clients`
  (266 tests) ran in 1.62 s, and the slowest single test took 0.29 s, so no test waits on the real
  `time.sleep`.
- `/code-review` (medium) raised five points:
  - The retry also covers `InvalidURL` and `ValueError`, which don't heal on retry. Kept:
    `base_url_problem` refuses a malformed host at construction and every path is a fixed
    pattern, so the only cost is 2 s on a request that fails anyway. The handoff names
    `TransportFailure` as the retried exception.
  - Replaying a write rests on change-manager's behavior, not on a test here. Answered by the
    "Replay safety of each write" section, which I read from change-manager's source. No replay
    path reaches a 409, because the subject and pull-request checks see the same body both times.
    No cross-repo contract test pins it, so I listed it under "Concerns".
  - Blocking sleep with no total deadline. This is the duration point under "Concerns".
  - A fixed 1 s wait with no jitter doesn't span a redeploy. Kept: the observed fault is a reset
    pooled connection, which a fresh connection fixes at once. The handoff asked for a short
    module-constant wait.
  - The change-manager GETs don't also get the gateway-status retry. I listed it under
    "Concerns" as a possible follow-up. It's outside this handoff.

## Concerns

- **Worst-case duration.** `ReadTimeout` is also a `TransportFailure`, so a change-manager that
  accepts connections and never answers now costs up to about 92 s per call (3 × 30 s plus 2 s of
  waits), where it cost 30 s. `_watch_one` contains failures per item, so a pass with many items
  against a hung server runs about three times longer before it exits 3. The handoff asked for
  transport failures in general, and I followed that. If HQ wants timeouts excluded, the helper
  can narrow to connection-level failures.
- **Overlapping replay.** Neither write path takes a row lock. A retry that reaches the server
  while the first attempt is still running could, in a narrow window, append a second `settled` or
  `superseded` event. The record still ends `resolved`. Because the client's connection is already
  dead when it retries, this needs the first request to still be in flight on the server after the
  reset. I didn't treat this as the handoff's stop point, which covers changing a record twice,
  and the observation write was already directed to retry.
- **The missing POST lines** in change-manager's access log are unexplained (see the hop section).
- **A recovered fault leaves no trace.** The retry is silent, as the handoff's no-logging scope
  implies, so once this deploys nobody can tell from the pass output whether the hop still drops
  connections. HQ decides whether a retried attempt warrants a line in the pass output.
- **No contract test pins replay safety.** It's read from change-manager `67111f7`. If
  change-manager changes either write's repeat handling, nothing here fails.
- **No gateway retry on change-manager GETs.** They retry on no answer only. A 502, 503 or 504
  still raises at once, as it did before this change.
- **Follow-ups**, listed in the PR body: the other lanes' change-manager clients
  (`src/bump_proposer/change_manager.py`, `src/change_proposer/change_manager.py`,
  `src/estate_lander/change_manager.py`, `src/work_watcher/change_manager.py`,
  `src/bump_proposer/landing_policy.py`) still make one attempt per request. Each write needs its
  own replay-safety read before it opts in.
- Standards: the handoff's section covered every concern this change touches.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout in production).** The change adds no logging
  and no file logging. The pass's existing `_say` output is unchanged.
- **Code Brain rule 4 (never silently swallow an exception).** The helper catches
  `TransportFailure` only on the first two attempts. The third attempt's failure propagates, and
  the client converts it into the same `ChangeManagerError` as before. There is no bare
  `except … pass`. Tests assert that three failures raise.
- **Code Brain rule 5 (never log secrets, auth headers, tokens, or bodies).** No retry message
  exists. The final error names the method, path, and exception type only, as it did before.
  `TransportFailure` still carries only the type name.
- **Code Brain lesson 6 (httpx logs every request URL at INFO).** No URL changed, and none carries
  a credential. The bearer stays in the `Authorization` header. The httpx log level is untouched.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN`).** The diff contains no token or token-shaped string. The security scan from
  the repo root reported no BLOCK.
- **Infra Brain 1032 (exposure means rotate).** I saw no secret value during this work.
- **Infra Brain 212 (HTTPS for public FQDNs).** Doesn't apply: no endpoint or base URL changed.
  `DEFAULT_BASE_URL` is still `https://change-mgr.alobar.net`, and `base_url_problem` still
  requires https.
- **Infra Brain 2890 (never commit a ping URL).** The diff contains no ping URL, and no ping
  handling changed.

## CI

CI on #730 at `ea8e6688` (run 44013698220) passed all three jobs:

- `Quality` collected 15,275 items: 15,252 passed and 23 skipped (918 s).
- `Runner consumer compatibility` passed.
- `Rollout transcription currency` passed.

The commit that adds this section changes only this report.
