Outcome: done

# Report: lanes-inc-2-ping-probe

- **PR:** #632 (draft), branch `hq/lanes-inc-2-ping-probe`, head `7e48098e42decfa71712fcff4d9d86a217cd8aba`.
- **Base:** `origin/main` at `74e6f19d` (#631).
- **CI:** green at `7e48098e`: Quality, Runner consumer compatibility, and Rollout transcription
  currency all pass. Not merged, as the handoff says.

## What I built

ADR-0059 increment 2's ping-URL probe, under Devon's D-31 (C).

- **The `unique_key` derivation.** `healthchecks_ping_url.unique_key(url)` computes
  `hashlib.sha1(UUID(...).hex[:16].encode()).hexdigest()`. That is the SHA-1 hex digest of the
  first 16 characters of the check UUID's **hyphenless** hex form (`uuid.UUID.hex`), encoded as
  UTF-8.
  - Source: Healthchecks tag `v4.4`, `hc/api/models.py` lines 432-434
    (`code_half = self.code.hex[:16]`, `return hashlib.sha1(code_half.encode()).hexdigest()`).
  - The route is `hc/api/urls.py` line 51, `checks/<sha1:unique_key>`, and `SHA1Converter.regex`
    at line 21 is `[A-z0-9]{40}`.
  - The view is `hc/api/views.py` lines 489-496, `get_check_by_unique_key`: `@authorize_read`,
    project-scoped, 404 otherwise.
  - I read all three from `raw.githubusercontent.com/healthchecks/healthchecks/v4.4/...`.
  - Only the first half of the UUID counts. A test pins this, because my first control
    placeholder shared that half with the test URL and so had the same `unique_key`.
- **The client.** `readers.Healthchecks.check_status(unique_key)` uses the executor's existing
  read-only key and its one `ConfinedClient`; I added no second client.
  - The client permits the listing and `GET /api/v3/checks/<40 lowercase hex>`, nothing else.
  - It refuses a malformed key before building the path.
  - With no answer, it logs `healthchecks_unanswered` (12-character key prefix and the error type
    only) and returns `None`, which the contract reads as indeterminate, so the step fails.
- **The provider.** The ping-URL provider becomes a regular probe provider:
  - `probe` is the lookup.
  - `known_bad` is a ping URL of a random `uuid4`, injectable for tests.
  - `dead_statuses` is `{404}`.
  - `probe_target` is one endpoint template, so the control and the subject share a target, as the
    verifier requires.
  - `bind_refusal` checks the marker and the shape, then runs the control lookup (must be 404) and
    the URL's lookup (must be 200). Each failure gets its own reason: no such check, a found
    control, another status, or no answer.
- **The contract.** `_verify_new` checks a `ShapeVerified` provider's shape first, so nothing
  malformed is looked up, and then runs decision 3's ordinary probe. R14 has no exception left.
  The note names the check by a 12-character `unique_key` prefix.
- **The verifier.** No code change. It already decides a `verify_new` with a refused control and a
  live subject on one target. A contract test feeds it the executor's own filed payload and gets
  `None`, meaning decided with no person.
- **Docs.**
  - ADR-0055 amendment 21 records D-31 superseding D-28 and D-30, the read-only key's new reach,
    no ping, no revoke, and the cut-over verify item.
  - Amendment 19 gets one parenthetical each in items 3 and 5 and in its open item "The ping URL's
    check read".
  - `docs/operations/rotation-executor.md` covers the key's reach (configuration text and table)
    and the ping URL's bind and lookup steps.
  - `src/rotation_worker/CLAUDE.md` has its two bullets rewritten as the rule stands now.

## Checks run

- `make check`, against `orchestrator_test_hq_w_52`, run on the first commit: ruff, format and
  pyright clean, then **14,128 passed, 1 skipped** (14,129 collected).
- The second commit only changes the bind's refusal wording for no answer. I re-ran
  `tests/rotation_worker` and `tests/contract/test_rotation_step_contract.py` after it (1,383
  passed), plus ruff, format and pyright on the touched files.
- An earlier full run failed one new test under xdist: an alembic `fileConfig` on that worker had
  disabled the logger. I fixed it the way `test_grants.py` does.
- Mutation pass. I mutated the code, cleared `__pycache__`, and restored afterward.
  - Killed: `unique_key` with hyphens, `unique_key` from the second half, a dead status of 401,
    a transport failure read as 404, the bind skipping the control, the bind skipping the lookup,
    `verify_new` skipping the shape check, and `verify_new` filing no probes.
  - Survived: 200 added to `dead_statuses`. It's an equivalent mutant: `_answer` reads 200 as live
    before it consults `dead_statuses`.
- **CI on #632 at `7e48098e`:** Quality passed (10m4s), Runner consumer compatibility passed, and
  Rollout transcription currency passed. The commit that adds this report changes no code.
- `/code-review` (medium) returned five findings. I fixed one: "answered None" became "Healthchecks
  gave no answer". I didn't change code for the other four:
  - The worker already catches a bind's `ContractError` as "it couldn't be checked" (`REFUSALS`).
  - A `StoreError` from the key read is `TRANSIENT` at bind. At `verify_new` it fails the unit, as
    every step's exception does.
  - A 404 from every path fails closed, because the URL's lookup must answer 200.
  - The dropped `run.issue` guard is deliberate: the type has only an issue plan.

## Files changed

- `src/rotation_worker/readers.py`
- `src/rotation_worker/adapters/healthchecks_ping_url.py`
- `src/rotation_worker/adapters/__init__.py`
- `src/rotation_worker/contract.py`
- `src/rotation_worker/types.py` (comment)
- `src/rotation_proposer/generate.py` (docstring only; rendered bytes unchanged)
- `src/rotation_worker/CLAUDE.md`
- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- `docs/operations/rotation-executor.md`
- `tests/rotation_worker/test_lane_credentials.py`
- `tests/contract/test_rotation_step_contract.py`
- `tests/services/test_rotation_verification.py`

## Self-review

- **Probe semantics.** The control is asked first, on the same target, through the contract's one
  `probe`. So the verifier's same-target rule and decision 3's order both hold without a special
  case.
- **Reach.** No request ever goes to `/ping/`. A test asserts that every request across a full
  issue and a bind is a `GET` to `healthcheck.alobar.net` under `/api/v3/checks/`, with the
  read-only key header.
- **Leaks.** Evidence and logs never carry the URL, its UUID, the full `unique_key` or the key.
  Tests assert each of the four is absent from every filed payload. The verify_new payload also
  passes the orchestrator's own `secret_metadata_path` and `LONG_RUN` scan.

## Concerns

1. **Evidence names the check by a 12-character prefix, not the whole `unique_key`.** The handoff
   says the evidence names the check by `unique_key`. The orchestrator's rotation evidence scan
   (`rotation_evidence.py`, `LONG_RUN`) refuses any run of 32 or more characters, and a
   `unique_key` is 40 hex characters, so the whole key would make every ping-URL step's evidence
   unfileable. Amendment 21 item 4 records the prefix and why.
2. **The package's `verify_new` criterion wording is unchanged.** It still says shape, no other
   keeper, and no ping, all of which stay true, but it doesn't mention the lookup. Its bytes are
   SHA-pinned in intent-packages (`tests/test_create_rotation.py`, `ISSUE_FIXTURE_SHA256`, pinned
   at `74e6f19`). Rewording it needs a paired intent-packages change, outside this one-PR handoff.
   Amendment 21's Consequences says so. If HQ wants the wording to name the lookup, it's a
   two-repository follow-up. No ping-URL package exists in intent-packages yet; GitHub code search
   finds only the test and its fixture.
3. **Not confirmed (a cut-over verify item, as the handoff said):** that `sds-execution-watch`
   sits in the read-only key's project. If it doesn't, the bind refuses with "finds no such
   check". I fetched no key.
4. **Amendment 21's status line is "Accepted on merge"**, mirroring amendment 20, with the same
   review bar. HQ owns the review and merge.

## Standards followed

- **Code Brain rule 4 (BLOCK), never swallow an exception silently.** A transport failure in
  `check_status` becomes `None`, with an inline docstring justification and a structured
  `healthchecks_unanswered` warning. The contract then reads it as indeterminate and fails the
  step. At bind it becomes a stated refusal that leaves the value in the inbox. Every failed
  lookup fails the step or refuses the bind; none is skipped.
- **Code Brain rule 5 (BLOCK), never log secrets, tokens, auth headers or full bodies.** No log
  line, refusal reason, exception message or evidence field carries the ping URL, its UUID or the
  `X-Api-Key` value. Only the key's first 12 characters and the HTTP statuses appear, and response
  bodies are never read. Tests assert the absence of each in filed payloads, bind refusals and log
  records.
- **Code Brain rule 3 (BLOCK), structured JSON logs to stdout.** The one new log line uses the
  executor's `rotation_worker` logger, with `extra` fields that `logs.JsonFormatter` renders. I
  added no handler and no file logging.
- **Infra Brain 591, 592 and 593, no BWS token in tracked files or history.** None is added. The
  test key is `hc-read-only-key-placeholder`, which doesn't have the token shape.
- **Infra Brain 2890, never commit a ping URL.** The test URLs use placeholder UUIDs
  (`00000000-0000-4000-8000-0000000000aa`, `ffffffff-0000-4000-8000-0000000000bb`) on the
  self-hosted base. No real check's URL appears.
- **Infra Brain 1032, an issue package states no revoke.** This change adds none. The provider's
  `revoke` and `probe_before_revoke` still refuse, the generated issue package and its pinned
  fixture are byte-unchanged with `credential_revoke` prohibited, and amendment 21 item 6 says so.
