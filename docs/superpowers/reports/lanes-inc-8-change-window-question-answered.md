# Question: lanes-inc-8-change-window (W-231)

I stopped before writing any code. Two of the handoff's facts don't hold against the tree at
`669c8a78`. One of them is a stop condition the handoff names. Each question is answerable in one
line, and each comes with my recommendation.

## Q1. The check-name test accepts only an `sds-*` check (handoff stop condition)

The handoff says the lane keeps the window's existing check, `Change Manager OPs Check` (slug
`operations-check`). It also says to stop if `test_the_check_names_are_the_launchers` can name only
an `sds-*` check. It can:

- `tests/scripts/test_deploy_lanes.py`:
  `_ARM = re.compile(r"^\s*sds_deadman_arm\s+(sds-[a-z-]+)\b", re.MULTILINE)` and
  `_CHECK_NAME = re.compile(r'^\s*CHECK_NAME="(sds-[a-z-]+)"', re.MULTILINE)`.
- `_launcher_check` asserts `len(names) == 1` for every `scripts/run-*.sh` outside
  `STAYS_ON_THE_MAC`. A launcher with `CHECK_NAME="Change Manager OPs Check"` matches neither
  regex, so the test fails on it.

The following parts already accept the existing name:

- `LANE_CHECKS` in `scripts/deploy_orchestrator.py` is a free-form `dict[str, str]`.
- `quiet_slot_unmet` (~1416) matches the Healthchecks listing by exact `name`.
- `sds_upsert_check` takes any payload. This lane has no installer, so nothing calls it.
- In the container, the outcome pings resolve by `SDS_DEADMAN_PING_URL_UUID`, so the launcher
  never uses the name at run time. The name matters only to `lanes-*`'s quiet-slot read.

I can't verify one thing without a live call: whether the self-hosted listing that
`quiet_slot_unmet` reads (`https://healthcheck.alobar.net/api/v3/checks/`) holds exactly one check
named `Change Manager OPs Check`. The Mac job's header still says "Healthchecks.io", although
change-manager's `.cred-consumers.toml` names the check without a host.

Options:

- **A (recommended).** Widen the two regexes in the test to accept any quoted name
  (`CHECK_NAME="([^"]+)"`), keep `len(names) == 1`, and add
  `"change-window": "Change Manager OPs Check"` to `LANE_CHECKS`. The test then still holds the
  table to the launchers; it only stops requiring the `sds-` prefix. This edit is inside my fence
  (`test_deploy_lanes.py`), but it loosens a test, so it's your call.
- **B.** Name a new check, `sds-change-window`, which reverses "the phase keeps the same checks".
  It adds a Healthchecks check at the cut-over, and change-manager's registry line changes with it.
- **C.** Keep the test as it is and exempt `change-window` by name, as `STAYS_ON_THE_MAC` exempts
  two launchers. Then nothing holds this lane's `LANE_CHECKS` row to its launcher.

## Q2. `lane-run` doesn't pass the four secret-UUID compose variables

The handoff puts each secret's UUID in a compose variable, empty in this PR, like
`SDS_DEADMAN_PING_URL_UUID: ""`. `scripts/lane_run.py` starts the launcher with a fixed
environment. `lane_environment` copies only `COPIED` (the token, `TZ`, and
`SDS_DEADMAN_PING_URL_UUID`), `STATE_VARIABLES`, `CHAIN_SETTINGS`, and `INFRAOPS_SETTINGS`. Any
other compose variable never reaches `run-change-window.sh`, so every pass would fail closed on an
empty id, even after a later PR filled the compose.

The other lanes hold their secret UUIDs in the launcher, not the compose. For example,
`run-estate-landing.sh` has `COOLIFY_TOKEN_UUID="${ESTATE_LANDING_COOLIFY_UUID:-f9f5…}"`, and
`test_the_repositorys_manifest_lists_execution_watchs_secrets` checks that "the launcher names each
secret it reads, except the ping-URL secret, whose id reaches it from the compose through
`lane-run`". Only the ping-URL id travels through the compose.

Options:

- **A (recommended).** Follow the estate pattern. The launcher holds four UUID constants, each
  empty in this PR (for example, `COOLIFY_WINDOW_TOKEN_UUID=""`), and fails closed with one line
  when a pass needs an empty one. A test set like `placeholders` lists the empty ones, so the PR
  that fills one must update the test. The compose carries only `SDS_DEADMAN_PING_URL_UUID: ""`,
  as #738 to #740 do. Neither `lane_run.py` nor its docstring changes.
- **B.** Follow the handoff as written. Put the four variables in the compose and add their four
  names to a new tuple in `lane_run.py`, with the `test_lane_run.py` cases that prove they're
  needed. That's four non-secret variables, not the one the fence allows, and it makes this lane
  the only one whose secret ids live in its compose.

## Facts I'll report, not ask about

- The `execute` scope covers every call the CLI makes at `fdf3e91b`: `GET /api/items`
  (`?status=approved`, the dry run's only call), `POST /api/items/{id}/claim`,
  `POST /api/items/{id}/outcome`, `POST /api/window-runs`, and `PATCH /api/window-runs/{id}`.
  The `scopes.py` comment at `e57076f2` still says `outcome` isn't gated on `in_progress`, but
  `app/api.py` at the same commit returns 409 unless the item is `in_progress` (~529). The code
  matches the handoff; only the comment is stale.
- The digest goes as an `info` notice, sent at once, not as a held `info` alert. A closed alert
  cancels its pending deliveries (`staleReason`: `alert closed`, in alert-hub's
  `src/engine/delivery.ts`), so a daily key followed by a close never delivers when sent in quiet
  hours. An alert left open makes the next day's same-key send a silent repeat. The sender guide
  also forbids a date in an alert key, and an alert's `message` caps at 4,000 characters, while a
  notice's caps at 12,000. The cost is delivery at about 04:00 instead of the Mac's 07:30 outbox
  send. This needs no answer unless you want the stateless lane to queue the digest some other way.

Once you answer, I build the whole PR in one pass.
