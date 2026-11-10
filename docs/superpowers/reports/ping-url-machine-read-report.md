Outcome: done

# Build report: ping-url-machine-read (ADR-0055 amendment 23, increment 1)

Worker W-75. PR [#651](https://github.com/AlobarQuest/orchestrator/pull/651) (draft), branch
`hq/ping-url-machine-read`.

- **Head:** `fe46762d014adaf7b11ad031fc0c2e832299c44b`, the last commit that changed code. Any
  later commit touches only this report.
- **Base:** `3a8f410ce23248c2ba92cd006cb5acf8aa003a71` (`origin/main` when the handoff was
  written)
- **Checks on the head:** all three pass on `fe46762d` (run 42882065904):
  - `Quality` (12m33s): 14,242 passed and 11 skipped, the same 14,253 collected as the local run.
  - `Rollout transcription currency`
  - `Runner consumer compatibility`

## What I built

All of increment 1 (amendment 23, lines 3181-3210), with HQ's seven choices:

1. **The type.** `healthchecks-ping-url` declares `machine_mint=True` and `creates=False`. `creates`
   is a new `Capabilities` field (default `True`) saying whether a machine mint creates a credential.
   `generate.authority` adds `credential_create` only when both are true, so the type's authority stays
   `secret_read` and `secret_write`. The capability vocabulary isn't widened.
2. **The plan.** `plans.derive_issue` accepts a machine mint and derives one unit, named `store`, of
   `mint`, `stage`, `verify_new`, `store` and `verify_consumers` (AC-001 to AC-005).
   - I kept the name `store` because the factory policy's ping-URL row (`rotation_units = ["store"]`)
     matches on it. A `mint` unit would have needed that row widened, which is a standing-authority
     change.
   - `contract.validate_issue_plan` accepts the all-machine shape. I dropped its "a person mints and
     stages" actor check, because no plan can reach it: mint and stage must share a segment, and a
     segment's steps are all one actor's.
3. **The Management-key client.** `readers.PingUrls`:
   - Its permit (`_permits_listing`) allows exactly `GET /api/v3/checks/`, over HTTPS to
     `https://healthcheck.alobar.net`, with no redirects (`ConfinedClient`'s default).
   - It reads the key only on the first read, and closes its client after the one request.
   - Parsing keeps only the named check's `ping_url`, as a `Value`, and drops the rest of the
     listing (amendment 23 item 4). A name is read once per pass, so the claimed mint reuses the
     read made before the claim. Another name lists again.
   - `ReadUnavailable` is no answer, a non-200 status, or an unparseable listing. `ReadError` is no
     check, two checks, or a check listed with no usable `ping_url`.
   - `config.py` gains `healthchecks_management_key_id` (an optional UUID, VM only, in `VM_ONLY`).
     `cli.open_ping_urls` builds the client only when it's set.
4. **The pre-claim read.** `worker._read_mint_waits` runs in `_take` straight after `_mint_waits`,
   for a provider of the `ReadsItsMint` protocol whose unit holds a `mint` step.
   - `ReadUnavailable` or `StoreError` waits unclaimed on the ordinary 48-hour clock.
   - `ReadError` is refused before the claim through `_refuse`, as `_mid_run_waits` does. That covers
     no key configured, no field, zero or two checks, the wrong shape, and a URL another ping-URL
     keeper holds (`_other_keepers`).
   - An unreadable registry or mirror while reading the other keepers (`RegistryError`,
     `DestinationError`, `MirrorError`) becomes `ReadUnavailable` and waits, as the inbox's bind
     treated it.
   - The provider's `mint()` calls the same `read_mint()`, which reuses the pass's cached read, so
     nothing is listed after the claim. A test pins one request across the whole pass.
   - Logs: `ping_url_read` carries unit, check and fingerprint; `ping_url_unreadable` carries unit,
     check and error type.
5. **The registry.** `registry.check_name` is the one rule: `healthchecks_check` is a non-empty,
   trimmed string. Two callers use it:
   - the adapter's `check_row`, a refusal at the checks before the claim;
   - the generator, which raises `GenerateError` for a row with no check.

   The landing ledger's and execution watch's rows gained `healthchecks_check = "sds-landing-ledger"`
   and `"sds-execution-watch"`, the names their launchers ping (`scripts/run-landing-ledger.sh:72`,
   `scripts/run-execution-watch.sh:73`).
6. **The package names the check.** `generate._READS_CHECK` writes the check name into the `mint`
   criterion's `condition`, never its `evidence`. `select_plan` and the verifier full-match the
   evidence text. ADR-0055's open item now records this as settled, with a pointer to the code.
7. **The adapter.** `mint` returns the read URL. I removed `bind_refusal` and the module's inbox
   text. `verify_new`, its shape check, and amendment 21's read-only lookup with its random-UUID
   control are unchanged.
8. **Docs, in the same PR:**
   - `docs/operations/rotation-executor.md`: the plan summary, the inbox section, the `config.toml`
     example and its explanation, the access table (a new Management-key row and the root's direct
     read in the BWS projects row), and "Lane tokens and ping URLs" (intro, row with example, the read
     before the claim, the lookup, the `store` unit).
   - `src/rotation_worker/CLAUDE.md`: the inbox and issue bullets.
   - ADR-0059: a pointer to amendment 23 beside "A lane holds only its own check's ping URL" and at
     the end of "Cut-over for one lane" step 1. Neither original line is rewritten.
   - The factory policy's ping-URL rationale, which said "a person copies it". Only the text
     changed; the matching fields didn't.

## Fixture bytes that change (for increment 2's handoff)

`tests/fixtures/rotation_package_issue_healthchecks-ping-url.json`:

- SHA-256 at the base: `f2f086388eb8881b999af7e54c4a6b5272db0bca1e23e77d626286c0e4b697d6`
- SHA-256 at the head: `6c8ef3275069cc6fb78636805ef3bed7ca748659f23f1fcb2bf531b477e290c3`

The test row behind it (`tests/rotation_proposer/test_generate.py`, `LANE_ROWS`) gained
`"healthchecks_check": "sds-example-lane"`. The only changes are these:

```diff
   "acceptance": [
+    {
+      "approver": "devon",
+      "condition": "The executor reads the ping URL of the Healthchecks check named 'sds-example-lane' from the Management key's check listing, before the unit is claimed; nothing is created.",
+      "evidence": "external: the mint step's rotation-step/1 evidence, filed by the rotation executor.",
+      "evidence_type": "external_attestation",
+      "id": "AC-001"
+    },
+    {
+      "approver": "devon",
+      "condition": "The new value is staged in the executor's work store, with a fingerprint different from the old value's.",
+      "evidence": "external: the stage step's rotation-step/1 evidence, filed by the rotation executor.",
+      "evidence_type": "external_attestation",
+      "id": "AC-002"
+    },
     { ...verify_new...
-      "id": "AC-001"
+      "id": "AC-003"
     { ...store...
-      "id": "AC-002"
+      "id": "AC-004"
     { ...verify_consumers...
-      "id": "AC-003"
+      "id": "AC-005"
...
-    "operating_procedure": "... registry row: units store. A person's acts, each announced by an alert and observed by the executor: mint. Evidence carries fingerprints, probe status codes and notes only.",
+    "operating_procedure": "... registry row: units store. No act is a person's. Evidence carries fingerprints, probe status codes and notes only.",
```

Nothing else in the package changes:

- **Authority:** `allowed` is still `secret_read` and `secret_write`; `credential_create` stays
  prohibited.
- **Reach:** still `external_system`.
- **Every other field:** the same bytes.

## The two registry rows, and why neither becomes due

Both rows gained the field. Adding it can't change whether either row is due. I reasoned from code;
I didn't run `cred-findings`:

- **The due rule.** infraops decides "due" in `cred-rotation.js` (infraops-mcp-server `c3359bf`,
  lines 240-251): a `cred.rotation-requested` finding exists when `rotate_requested` has passed and
  the recorded `lastRotated` (state, else registry `last_rotated`) is missing or earlier. Age findings
  read `rotationAnchor` (`created`, `last_rotated`, state). Neither reads `healthchecks_check`.
- **The parser.** It accepts any `[A-Za-z0-9_-]+` key on a credential table (`cred-consumers.js`,
  lines 123-140), so the field doesn't fail the registry.
- **The orchestrator side.** The proposer's revise-or-generate decision compares destinations, which
  come from `[[credential.consumer]]` tables. The field is on the credential table, outside the
  three-way destination comparison.

The rows' due state is whatever it was at the base. Both carry `rotate_requested` (`2026-11-09` and
`2026-11-10`). Whether infraops records each as issued is state I can't read from here; the next
section covers what that means for the merge.

## Tests and checks

- **`make check`** on the final code (head `fe46762d`), with my own database
  `orchestrator_test_hq_w_75`: exit 0.
  - 14,252 passed, 1 skipped.
  - ruff check and format: "All checks passed".
  - pyright: 0 errors.
- **New file `tests/rotation_worker/test_ping_url_read.py`** (36 tests). It covers:
  - the one-unit plan;
  - the read before the claim, with exactly one listing request over HTTPS and the key in
    `X-Api-Key`;
  - no listing after the claim;
  - waits for 503, 401, no answer, an unparseable listing, and a key the store refuses (claim never
    made, 48-hour clock);
  - each `ReadError` refusal (no key, no check, two checks, no URL, wrong shape, no field, another
    keeper's URL);
  - a wait when the other keepers can't be read (`RegistryError`, `MirrorError`);
  - `check_row`'s refusal;
  - another type's unit never listing;
  - the client's single permitted request;
  - parsing that keeps only the named check's URL, with a second name listing again;
  - the key read only once, lazily;
  - both log events;
  - the configuration (VM loads it, a non-UUID is refused, the Keychain instance is refused, the
    client is built only when configured).
- **The log test** captures the root logger at INFO with httpx forced to INFO (the executor's
  `logs.configure` raises httpx to WARNING, so the test switches that guard off). It asserts that
  httpx records were captured, and that no record holds the URL, its UUID, the marker
  `8000-00000000beef`, another check's URL, the Management key or the read-only key.
- **Updated tests:**
  - `test_lane_credentials.py`: the plan shape; machine-minted issue validity, with four refusals;
    issue runs that start unstaged; the inbox bind tests for ping URLs removed and replaced by a
    staged-wrong-shape refusal.
  - `test_generate.py`: the check in the criterion, no `credential_create`, and a refusal for a row
    with no check.
  - `test_decompose.py` and both row tests: rows carry the field; the row tests pin the lane check
    names.
  - `test_rotation_edges.py`: the proposer's registry imports gain `check_name`; `readers.py` builds
    2 clients; the executor's config keys gain `healthchecks_management_key_id`.
  - `tests/services/test_rotation_verification.py`: the orchestrator's verifier passes an issue's
    machine `mint` and `stage` evidence (old value the marker's fingerprint, with and without the
    retry note) and refuses it when the two fingerprints match. The executor's tests can't reach
    that verifier.
- **Mutations:** 22, each dropping one guard, run against the rotation test files with
  `__pycache__` cleared around each. All 22 were killed and none survived:
  - the pre-claim call in `_take`;
  - a wait turned into a pass;
  - a refusal turned into a pass;
  - the other-keeper check;
  - an unreadable registry no longer caught;
  - `StoreError` no longer a wait;
  - the shape check;
  - the no-key check;
  - two checks accepted;
  - zero checks not refused;
  - a non-200 status accepted;
  - a transport failure no longer a wait;
  - the no-URL check;
  - a widened permit;
  - a client that keeps the raw listing;
  - no per-pass cache;
  - `creates` ignored;
  - the check left out of the criterion;
  - `check_row` ignoring the field;
  - the URL logged in place of its fingerprint;
  - the unreadable log at DEBUG;
  - the check-name validation weakened.

  The tree was clean after each run.
- **Security scanner** (`security_scan.cli . --category security`, from the worktree root): no
  BLOCK. Its two WARNs predate this branch: a `.gitignore` glob, and an undeclared UUID that appears
  only in older reports at the base.
- **Code review** (`/code-review` at medium, `main...HEAD`, run on `971d90ea`): 10 findings. I fixed
  three:
  - one check-name rule in `registry.py` instead of two;
  - one `ping_url_unreadable` block instead of two;
  - a no-URL refusal message that no longer says the key isn't read-write.

  The other seven are covered under "Concerns" or are amendment 23's stated design: no pre-claim
  read-only lookup, a cache per `Setup` that matches the existing `Healthchecks` client, and
  `open_ping_urls` beside `open_healthchecks`. Its finding that an unreadable registry escapes the
  read is fixed in `fe46762d`.
- **Advisor review** before finishing. It led to `fe46762d`: parsing now keeps only the named URL
  (I had kept every check's, against item 4); the registry wait; five more mutations; and the
  verifier test.

## Files changed

`git diff 3a8f410c fe46762d --stat`:

```
 .cred-consumers.toml                               |   6 +
 ...ntial-rotation-runs-in-a-pull-based-executor.md |   3 +
 ...d-lanes-run-as-pinned-containers-off-the-mac.md |  10 +
 docs/operations/rotation-executor.md               |  90 ++--
 src/orchestrator/factory-policy.toml               |   8 +-
 src/rotation_proposer/generate.py                  |  47 +-
 src/rotation_worker/CLAUDE.md                      |  18 +-
 src/rotation_worker/adapters/__init__.py           |  12 +-
 .../adapters/healthchecks_ping_url.py              | 112 +++--
 src/rotation_worker/cli.py                         |   9 +
 src/rotation_worker/config.py                      |   7 +
 src/rotation_worker/contract.py                    |  16 +-
 src/rotation_worker/inbox.py                       |   3 +-
 src/rotation_worker/plans.py                       |  26 +-
 src/rotation_worker/readers.py                     |  72 +++-
 src/rotation_worker/registry.py                    |  12 +
 src/rotation_worker/types.py                       |  11 +-
 src/rotation_worker/worker.py                      |  78 +++-
 tests/architecture/test_rotation_edges.py          |   5 +-
 ...tation_package_issue_healthchecks-ping-url.json |  22 +-
 tests/rotation_decomposer/test_decompose.py        |   6 +
 tests/rotation_proposer/test_generate.py           |  47 +-
 tests/rotation_worker/test_execution_watch_rows.py |   3 +
 tests/rotation_worker/test_landing_ledger_rows.py  |   3 +
 tests/rotation_worker/test_lane_credentials.py     | 152 ++++---
 tests/rotation_worker/test_ping_url_read.py        | 472 +++++++++++++++++++++
 tests/services/test_rotation_verification.py       |  30 ++
 27 files changed, 1094 insertions(+), 186 deletions(-)
```

This report adds one more file.

- **Teardown.** I dropped `orchestrator_test_hq_w_75`; no `_gwN` copy existed. The worktree and
  branch stay for the PR.

## Self-review

- **Stop points.** None fired:
  - `credential_create` under `src/` is only `generate.py` lines 53 and 209 at `3a8f410c`.
  - Neither row's due state changes.
  - No test needs the real Management key, a real ping URL, or a network call: every Healthchecks
    answer comes from `httpx.MockTransport`, and placeholders are obvious (`hc-management-key-
    placeholder`, UUIDs of zeros, ones and `beef`).
  - Nothing in the code contradicted the amendment in a way that changed what is built. The unit name
    `store` is a choice the amendment leaves open ("one unit of `mint`, `stage`, ..."), made to keep
    the factory policy row unchanged.
- **Code Brain rule 4.** Every failure of the read is logged and either waits or refuses; nothing
  falls through to the claim. That includes the other-keeper read, whose registry and mirror
  errors wait as `ReadUnavailable`.
- **Amendment 22's standing approval.** Its `rotation-proposer standing-approval` command isn't in
  the code at the base or here; nothing under `src/` or `tests/` names it. Its written conditions
  don't depend on a person's mint:
  - re-render equality;
  - revision 1;
  - the issue plan;
  - one of the two types;
  - equal destinations;
  - `generate.authority` exactly.

  `render` is deterministic and the authority set is unchanged (`secret_read`, `secret_write`), so
  this change leaves them satisfiable. The command's tests belong to its own increment.
- **A retry reads the listing again.** A unit that failed after `stage` reads the listing on its next
  pass too, though its `mint` re-proves what is staged and doesn't use the read. I kept that on
  purpose: it's the conservative reading of "a read that can't be made never passes", and a listing
  outage only delays the retry.

## Concerns

- **An open ping-URL package.** HQ's merge condition: the PR merges only while no ping-URL package
  is open, together with the intent-packages increment-2 PR. A generated package for either lane
  that states the person's mint plan matches no derived plan after this lands, and three things
  refuse it:
  - the executor (`select_plan`);
  - the proposer's `reissue_body` (`StatedPlanError`) for a due row;
  - the decomposer.

  I can't see infraops' recorded state or intent-packages' open packages from this session.
  `execution-watch-ping-url` (`rotate_requested = "2026-11-09"`) and `landing-ledger-ping-url`
  (`"2026-11-10"`, issued at G-20) are the two to check before merging.
- **Until increment 3,** every ping-URL unit is refused before the claim with "no Healthchecks
  Management key configured". It costs the claim and the `fail`, not a silent wait. This is the
  ADR's designed fallback (decision 5).
- **The quoting in the criterion.** It quotes the check name with Python's `repr`
  (`'sds-example-lane'`). That's fine for the lane names in use, but a name holding a quote character
  would render with escapes. Increment 2 pins these bytes, so I mention it before they're pinned.
- **The `stage` criterion's wording** for an issue still says "a fingerprint different from the old
  value's", where the old value is the marker. That's accurate, but it's generic text and not
  ping-URL-specific.
- **The runbook is ahead of increment 3.** It documents `healthchecks_management_key_id =
  "51509d5e-…"` in the `config.toml` example and the root's direct read on that secret in the
  access table as the configuration. Both are increment 3's acts (a person's grant and the VM's
  config), and neither is in place until then.
- **No leftover paste wording.** I searched `docs/operations/` and `scripts/` for a person copying
  or pasting a ping URL outside the three docs I updated, and found none. The other mentions
  (`rotation-proposer.md` line 205, `deploy.md` lines 309-325) stay accurate.
- **Standards the handoff doesn't cover.** Code Brain has no road for HTTP clients. I followed the
  existing `ConfinedClient` permit pattern (`readers._permits`).

## Standards followed

- **Code Brain rule 3 (BLOCK), structured JSON logs to stdout.** `ping_url_read` and
  `ping_url_unreadable` go through the `rotation_worker` logger with `extra=` fields, which
  `rotation_worker/logs.py` renders as JSON on stdout, like every other executor event.
- **Code Brain rule 4 (BLOCK), never swallow an exception silently.** Every exception the read
  raises (`ReadError`, `ReadUnavailable`, `StoreError`) is logged as `ping_url_unreadable`, then
  either refuses the unit before the claim or records a wait. `PingUrls` turns transport failures
  into `ReadUnavailable` and never returns a default. Mutations M2 and M3 prove that neither branch
  can pass.
- **Code Brain rule 5 (BLOCK), never log secrets, tokens or auth headers.** The Management key goes
  only into the `X-Api-Key` header. It's held as a `Value` and never formatted into a message.
  - The URL is held as a `Value` from parsing on, and other checks' URLs are never wrapped or kept.
    `ping_url_read` logs only its fingerprint (SHA-256 prefix and length).
  - No refusal or wait reason contains the URL; the tests assert this, and `scrub` covers anything
    else.
  - Evidence still holds only the 12-character `unique_key` prefix.
  - Mutation M15, which logs the URL, is killed.
- **Code Brain rule 7 (WARN), log exceptions with context.** `ping_url_unreadable` carries the unit,
  the check name and the error's type. The existing `unit_refused` log carries the reason.
- **Code Brain lesson 6, HTTP libraries log URLs at INFO.**
  `test_no_log_record_holds_the_url_or_the_key` captures the root logger at INFO with httpx at INFO.
  It asserts that httpx records are present, and that no record's `vars()` holds the URL, its UUID,
  the marker or either key. The production guard (httpx at WARNING) stays as it was.
- **Infra Brain 591, 592 and 593: no BWS token in tracked files, no inline `BWS_ACCESS_TOKEN`, and
  only obvious placeholders.** No token or token-shaped string is added. The only secret reference
  is the BWS UUID `51509d5e-64f5-4781-ae82-b4ed00b644e1` in the runbook's `config.toml` example,
  already listed in `.bws-secrets.toml`. Test keys are `hc-management-key-placeholder` and
  `hc-read-only-key-placeholder`.
- **Infra Brain 1032, exposure means rotate.** I saw no secret value anywhere in this work. Nothing
  read a real key or URL.
- **Infra Brain 212, HTTPS for public FQDNs.** `PingUrls` reaches only `HEALTHCHECKS_URL`
  (`https://healthcheck.alobar.net`), with redirects off. A test asserts the request's scheme is
  `https`.
- **Infra Brain 2890, never commit a ping URL.** Every ping URL in the diff is a placeholder of
  zeros, ones or `beef` under `https://healthcheck.alobar.net/ping/`, and no real check UUID appears.
  The registry rows carry check names, never URLs.
