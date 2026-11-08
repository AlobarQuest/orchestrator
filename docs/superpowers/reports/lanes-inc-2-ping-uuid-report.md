Outcome: done

# Report: execution-watch's compose names its ping-URL secret (3d-2b)

Handoff: sds-hq `state/handoffs/lanes-inc-2-ping-uuid.md`. Branch `hq/lanes-inc-2-ping-uuid`, base
`origin/main` at `31ecf2b4`. Draft PR
[#638](https://github.com/AlobarQuest/orchestrator/pull/638). The change is commit `def611e8`,
plus `9dafbc62`, which rewords one manifest comment. The PR head is the last commit on the
branch, which only edits this report. Not merged, not marked ready.

## What I built

- `lanes/execution-watch/compose.yaml`
  - Lines 5-9: the header comment's last sentences are rewritten. They now say that
    `SDS_DEADMAN_PING_URL_UUID` is the BWS secret id of `execution-watch-ping-url` in
    `Rotation / Keepers` (an id, not a secret and not the ping URL), that `lane-run` passes it to
    the launcher, and that the dead-man helper reads the URL from that secret with the lane's own
    token. They also say that the helper pings nothing while the secret holds the inbox's empty
    marker.
  - Line 18: `SDS_DEADMAN_PING_URL_UUID: 97802aac-5a7f-45e8-8185-b4fc00cd318e`, a literal.
- `.bws-secrets.toml`, lines 262-274 (end of file): a `[[secret]]` entry for `97802aac-…`,
  `name = "execution-watch-ping-url"`, `project = "Rotation / Keepers"`,
  `lanes = ["execution-watch"]`. Its comment says what the secret holds, that only the lane's
  container reads it with the lane's own token (compose → `lane-run` →
  `sds-deadman.sh::sds_deadman_resolve_by_uuid`), and why the token's keeper isn't listed.
- `tests/scripts/test_deploy_lanes.py`
  - `test_the_execution_watch_compose_meets_the_add_refusals`: the comment that said the UUID
    "lands with the cut-over" is corrected.
  - `test_the_repositorys_manifest_lists_execution_watchs_secrets`: expects the ping-URL secret
    as the lane's third secret. It still requires every other listed id in
    `scripts/run-execution-watch.sh`; the ping-URL secret is exempt, because its id reaches the
    launcher from the compose.
  - New `test_every_lanes_ping_url_secret_is_listed_for_that_lane`: for every
    `lanes/*/compose.yaml` that sets `SDS_DEADMAN_PING_URL_UUID`, the id must be in
    `deploy.lane_secrets(manifest, lane)`. It fails if no compose names one, so it can't pass
    vacuously.
- `docs/operations/deploy.md`, lines 307-311: the sentence saying the compose doesn't name
  `SDS_DEADMAN_PING_URL_UUID` until the cut-over is rewritten. This file is an operations page,
  not an ADR. No ADR line was made stale: a search of ADR-0059 for the stale phrasing found none.

## Item 3: the value reaches the launcher

Both halves exist; no code changed.

- `lane-run` passes it. `lanes/lane-run:4` runs `scripts/lane_run.py`. `scripts/lane_run.py:46`
  is `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`, and `lane_environment`
  (`scripts/lane_run.py:67-74`) copies each listed name the container sets into the launcher's
  fixed environment. The docstring states the same at `scripts/lane_run.py:10`. Pinned by
  `tests/scripts/test_lane_run.py:87,106`.
- `sds-deadman.sh` reads the URL by that id with the lane's token. `scripts/sds-deadman.sh:126-128`
  (`sds_deadman_resolve`) takes the UUID path when `SDS_DEADMAN_PING_URL_UUID` is set.
  `scripts/sds-deadman.sh:103-118` (`sds_deadman_resolve_by_uuid`) refuses when
  `BWS_ACCESS_TOKEN` is unset (line 105), reads the secret with `sds_bws_value
  "$SDS_DEADMAN_PING_URL_UUID"` in its one-argument form, which uses the environment's token
  (line 109; comment at lines 100-102), and accepts only an `https://` value with no quote,
  backslash, or whitespace (lines 110-117). The inbox's empty marker fails that shape check, so
  the helper pings nothing until the URL is written. Pinned by
  `tests/scripts/test_sds_deadman.py:614-703` and `tests/scripts/test_run_execution_watch.py:160-200`.

## The keeper-entry choice

I didn't add an entry for `execution-watch-bws-token` (`5a21575b-…`).

The line that convinced me is the first line of `.bws-secrets.toml`: "Secrets this repo consumes
through production/runtime or GitHub Actions, by stable BWS UUID." The file's one executor-only
secret, `rotation-inbox`, is listed because this repo's runtime code names its id: its comment
says "`scripts/install-rotation-worker-launchd.sh` writes this id into its configuration". The
security scanner's drift rule works the same way: it counts ids in runtime files and excludes
`.cred-consumers.toml` ("The credential registry lists the consumers of secrets a repo owns, not
secrets it consumes", `security_scan/manifest.py:21`). The lane token's keeper id appears only in
its registry row. The precedent is the rotation canary's keeper, `29bb7360-…`
(`.cred-consumers.toml:608`): only the executor reads it, and it has no manifest entry.

There's also a functional reason. An entry with `lanes = ["execution-watch"]` would put the
keeper in `lane_secrets`, so the `--token` proof would require the lane's account to read its own
keeper. An entry without `lanes` would add nothing a reader of this repo uses.

## The two registry rows (drafted, not landed)

Checked against `src/rotation_worker/registry.py`, `adapters/bws_lane_token.py`,
`adapters/healthchecks_ping_url.py`, `destinations.py`, `types.py`, and infraops'
`src/security-drift/cred-rotation.ts` and `cred-consumers.ts`.

```toml
[[credential]]
id = "execution-watch-bws-token"
class = "bws-lane-token"
provider = "bws-lane-token"
bws_uuid = "5a21575b-b25b-42b5-bf05-b4fc00cd30e0"
rotated_by_sds = true
rotate_requested = "<the day HQ lands this row>"
reach_secrets = [
  "f793576f-e9aa-4f9d-8089-b4a000b9e2d5",
  "b415d03b-f92d-46b1-8a9d-b4f10103df49",
  "97802aac-5a7f-45e8-8185-b4fc00cd318e",
]
reach_projects = []

  [[credential.consumer]]
  kind = "bws-secret"
  uuid = "5a21575b-b25b-42b5-bf05-b4fc00cd30e0"

  [[credential.consumer]]
  kind = "coolify-env"
  instance = "prod"
  resource_type = "service"
  uuid = "<sds-lane-execution-watch's service uuid>"
  key = "EXECUTION_WATCH_BWS_ACCESS_TOKEN"
  redeploy = false
```

```toml
[[credential]]
id = "execution-watch-ping-url"
class = "healthchecks-ping-url"
provider = "healthchecks-ping-url"
bws_uuid = "97802aac-5a7f-45e8-8185-b4fc00cd318e"
rotated_by_sds = true

  [[credential.consumer]]
  kind = "bws-secret"
  uuid = "97802aac-5a7f-45e8-8185-b4fc00cd318e"
```

`rotate_requested` in the token row isn't a second placeholder in the sense the handoff means: it
takes a date, which I can't know until the row lands. The service uuid is the one unknown value.
Remove `rotate_requested` if HQ makes the row due some other way.

What the code wants, field by field:

- `id`, `class`: required by infraops (`cred-consumers.ts:208`). Both classes map to their types
  in `rotation_worker/types.py:66,70`. infraops' class policy has both
  (`cred-rotation.ts:95-102`).
- `provider`: selects the adapter (`registry.py:109`, `checks.py:121`) and becomes the `provider`
  destination (`destinations.py:80`). It isn't a consumer table.
- `bws_uuid`: the keeper (`registry.py:56-72`). It must be the only keeper field: setting
  `keeper_service` or `keeper_account` beside it is refused.
- `rotated_by_sds = true`: **on both rows.** `registry.py:124-126` filters on it, and infraops
  leaves rows without it to the manual checklist. The runbook's token example has it, but its
  ping-URL prose (`rotation-executor.md`, "The registry rows", last paragraph) doesn't mention it.
- `reach_secrets`: a non-empty list of lowercase BWS ids, no duplicates (`bws_lane_token.py:62-75`).
  The bind requires the lane's account to see exactly this set (`bws_lane_token.py:7-15`), so the
  grants Devon gives the lane's account must be exactly these three secrets and no project.
- `reach_projects = []`: optional (defaults to `[]`), but must be a list of ids if present.
- The token row's consumers: `lane_variable` (`bws_lane_token.py:78-93`) requires exactly one
  destination beyond the keeper and provider, a `coolify-env` with `redeploy = false`,
  `instance = "prod"`, `resource_type = "service"`, and a key matching
  `^[A-Z][A-Z0-9_]*_BWS_ACCESS_TOKEN$`. The required fields come from
  `destinations.py:IDENTITY_FIELDS`.
- The ping-URL row's consumers: `check_row` (`healthchecks_ping_url.py:52-59`) requires the
  destination kinds to be exactly `bws-secret` and `provider`. **No other consumer is allowed**,
  so the lane can't appear as a `runtime-fetch` consumer of its ping-URL secret. The runbook's
  "the keeper as its one consumer" agrees.
- `provider_identity`: optional. `worker.py:732-747` adds "It is …" to the mint task only when
  it's a string. The runbook omits it; I omitted it too.
- `created`: optional in infraops' schema (`cred-consumers.ts:62`), but the registry's convention
  wants it: "Dates are BWS creationDate" (`.cred-consumers.toml:20`), every existing row carries
  one, and the canary dates its keeper's creation (`created = "2026-10-11"` beside its
  `bws_uuid`). By that convention both rows take `created = "2026-11-08"`, the day the VM root
  created both keepers. On the token row it starts the 365-day age clock, which counts until the
  first `record-rotation` puts `lastRotated` in infraops' state (`rotationAnchor` prefers state).
  On the ping-URL row it does nothing, since the class never ages. I left it out of the drafts;
  HQ decides whether to add it.
- `disposition`: optional (`cred-consumers.ts:57`); only `revoke-no-replacement` changes
  behavior. I omitted it.

How a row is marked due or not yet due. The runbook doesn't say, and ADR-0059 (~1094-1098) says
"the token's row due for its issue and the ping URL's not yet due" without naming the field.
infraops decides this:

- By age: the anchor is `last_rotated` (state, then row), then `created`
  (`cred-rotation.ts:209-211`), against the class's maximum age. `bws-lane-token` is 365 days,
  and `healthchecks-ping-url` never ages (`cred-rotation.ts:95-102`). A row with no `created` has
  no anchor and never ages.
- By request: `rotate_requested = "YYYY-MM-DD"` is due once that day comes, while the credential
  has never been rotated or was last rotated before that date (`cred-rotation.ts:279-290`). A
  malformed value raises `cred.invalid-rotate-requested` and is ignored (`cred-rotation.ts:228-234`).
- A first issue: the proposer chooses the type's issue plan when infraops reports `last_rotated`
  as null (`rotation_proposer/generate.py:11-12,125-127`).

So the token row is due for its issue by carrying `rotate_requested`. The ping-URL row is not yet
due by carrying neither `rotate_requested` nor anything that ages. HQ makes it due after the
token's `store` unit completes by adding `rotate_requested` to it.

## Tests and checks

- Focused lane tests (`test_deploy_lanes.py`, `test_lane_image.py`, `test_lane_run.py`,
  `test_run_execution_watch.py`, `test_sds_deadman.py`): 227 passed.
- Mutation: with `lanes = ["execution-watch"]` removed from the new manifest entry, both the
  updated test and the new test fail (2 failed). I restored the file from a copy afterwards.
- `make check` on my own database `orchestrator_test_hq_w_56` (with `TEST_DATABASE_URL`,
  `ORCHESTRATOR_DATABASE_URL`, `SECURITY_STANDARDS_DIR`, and `PGTZ=UTC` set): exit 0. Lint
  "All checks passed!", pyright "0 errors, 0 warnings", tests **14130 collected, 14129 passed,
  1 skipped**.
- Security scan (`security_scan.cli . --category security`) from the repo root: no BLOCK. Two
  WARNs predate this branch: `*-migration/` gitignore coverage, and undeclared `aaff45ca-…`,
  named by `scripts/install-rotation-worker-launchd.sh` on `origin/main`.
- A search of the changed files for `healthcheck.alobar.net/ping/` followed by a hex character
  found nothing.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `lanes/execution-watch/compose.yaml`
- `.bws-secrets.toml`
- `tests/scripts/test_deploy_lanes.py`
- `docs/operations/deploy.md`
- `docs/superpowers/reports/lanes-inc-2-ping-uuid-report.md` (this report)

## Self-review

- The compose's literal passes the deploy command's add refusals: the existing
  `test_the_execution_watch_compose_meets_the_add_refusals` accepts a `BWS_UUID`-shaped value.
- `lane_secrets` returns ids in file order. The new entry is at the end of the file, so the lane's
  order is observer, bridge, ping URL, and the `--token` proof reads all three.
- The two tests overlap a little: the execution-watch test pins the literal id, and the new test
  ties compose to manifest for any lane. I kept both because each fails on its own breakage.

## Concerns

- The `.bws-secrets.toml` comment on the new entry refers to the token keeper's
  `.cred-consumers.toml` row, which lands in cut-over item 4. The comment says so.
- Besides `deploy.md`, I searched ADR-0055, ADR-0059, and `docs/operations/` for
  `SDS_DEADMAN_PING_URL_UUID` and "ping-URL secret". No hit claims that the compose doesn't name
  the variable or that the secret doesn't exist, so no ADR line changed.
- `scripts/sds-deadman.sh:40-41` says a lane whose compose doesn't name the variable "yet
  (`execution-watch` until its cut-over)" falls through to the name lookup. After this change
  that example is stale. The handoff rules out changing `sds-deadman.sh`, so I left it. It's a
  comment-only fix for a later change.
- ADR-0059 doesn't name the field that makes a row due. The code's answer is `rotate_requested`,
  as the preceding section shows. If HQ intends another lever, the drafted token row needs
  changing.
- The bind requires the lane's account to see exactly `reach_secrets`. The grants given to the
  lane's account must therefore be the three listed secrets, with no project grant, or the token
  won't bind.
- The handoff lists Infra Brain rules 591, 592, 593, and 2890. I couldn't query Infra Brain from
  this session, so I applied them as the handoff states them.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging changes. The diff adds no log
  line and changes no logging code.
- **Code Brain rule 4 (never swallow an exception silently):** the new test has no `try` or
  `except`. A parse or lookup failure in `lane_service`, `_environment`, or `lane_secrets` raises
  and fails the test.
- **Code Brain rule 5 (never log secrets):** no log line was added. The tests' assertion messages
  name only the lane or the BWS secret id. The prose keeps the BWS secret id (`97802aac-…`)
  separate from the ping URL's UUID, and never gives the second.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; obvious placeholders):**
  the diff holds BWS secret ids only, no token. The security scan reports no BLOCK. The tests add
  no token-shaped value.
- **Infra Brain 2890 (never commit a ping URL):** the diff commits the ping-URL secret's BWS id,
  which the handoff allows. A search found no `https://healthcheck.alobar.net/ping/` followed by a
  UUID in any changed file.
