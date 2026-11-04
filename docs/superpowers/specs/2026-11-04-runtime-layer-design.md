# Lane runtime and operations layer design

**Status:** Draft for Devon's approval. Layer id: `runtime` (`make layer L=runtime`).

## Intent

This layer keeps the lanes running and the orchestrator deployed: the shared launcher helpers
(`scripts/sds-*.sh`), the credential manifests, the deploy procedure, the image build scripts,
`estate_clients`, `alert_hub_client` and `standing_identity`. Its alarm library decides what every
scheduled lane's exit tells a person, so this pass judges that library by what reaches alert-hub,
not by what reaches a log.

Recon found the alarms sound where a lane stops: every missed run in Healthchecks' retained window
became a down alert, except where a standing finding already held the check down. It also found
four lanes whose findings reach only a log, a standing finding that has blinded one lane's liveness
since October 31, 19 records that stopped being true, and no test stating the layer's outbound
surface.

This pass uses the same four outcomes as the earlier layers:

1. Every signal has a home: a finding or failure reaches alert-hub as one keyed alert (or, for an
   outcome-alarm launcher, a Healthchecks `/fail` that alert-hub hears), never only a log.
2. Scope comes from the member record: act by grant, observe by membership.
3. Records stay true: no record, doc, docstring or state says something that stopped being so.
4. Edges are clear: one test states each lane's outbound surface.

## Measurements, 2026-11-04

Read from `launchctl`, the installed plists, the Healthchecks Management API (checks, flips and
pings, whitelisted fields only), alert-hub's history and the lane logs. Nothing was written.

| Measure | Value |
|---|---|
| SDS launch agents loaded | 20: 19 `run-*.sh` lanes plus `rotation-worker` (the layer map says 17) |
| Lane checks in Healthchecks | 19, each with a cron or period schedule and a grace; every plist's schedule matches its check |
| Alarm modes | 11 armed dead-man lanes (7 read 2 as found, 4 read 3), 8 outcome alarms |
| Installed plists differing from their template | 3: activation-sweep (no `~/.local/bin` on `PATH`), estate-landing and rotation-decomposer (`~/Projects/Orchestrator` capitalised) |
| Runs that logged "alerting disabled" | 203 across 18 logs, almost all beside a failed credential read in the same outage |
| Missed runs in the retained window, by check | All heard as a down flip, except 6 `sds-bump-driver` runs on 2026-11-03 |
| `sds-bump-driver` | Down since 2026-10-31 on a standing finding (a unit in `revision_required`), so it hears no stop |
| Open alert-hub alerts | 11, all muted |
| Findings that reach only a log | `tool-installer` (`rea` failed to build on all 9 logged passes), `activation-sweep` (three checkouts behind), and `revision-watcher` and `pin-watcher` by design (none live today) |

The November 3 outage shows the carried gap end to end. `sds-membership-check` logged "could not
reach origin", then "the Healthchecks API key could not be read", then a failed observer-credential
read, and its `/fail` was a no-op. Four hours later its check went down, and alert-hub raised
`hc-33dc730b-…` "sds-membership-check is down". Fourteen other lanes followed the same path that day.

## Claude's calls, under Devon's rule that a deviation needs a documented reason

1. **No new hearer for an unreadable Healthchecks key.** The key read fails when BWS or the
   network does, and anything else that could report it needs the same two. Healthchecks' own
   schedule hears the missing ping, which the window above proves for every lane with no standing
   finding. Part 1c fixes the one defect on the path: the key is parsed with `python3` from `PATH`.
2. **The masking fix covers only a code a lane already delivers as keyed alerts.** A finding may
   ping success only where every exit with that code was delivered as a keyed alert and a failed
   delivery exits with another code (Devon's decision, ADR-0021 amendment 3). Of the three outcome
   lanes that send keyed alerts, only `bump-driver` meets that today (Part 1b).
3. **The four log-only lanes get keyed alerts and keep their dead-man checks** (Devon's decision).
   They use the pattern `unit-verifier` and `execution-watch` already use, through
   `alert_hub_client.KeyedAlerts`, with the `sds-bridge` sender.
4. **The rotation decomposer and proposer keep swallowing a failed alert send.** It's a stated
   choice in `rotation_decomposer/cli.py::_tell_a_person` ("the exit code describes the rotations"),
   and `KeyedAlerts` retries an unsent fire on the next pass.
5. **The credential manifests are corrected in full**, including the reader rows of two
   SDS-rotated credentials (`bws-sds-operator-token`: five readers; the observer bearer: three).
   The rotation proposer revises a package only for a credential infraops reports as due
   (`rotation_proposer/cli.py::_decide`), so neither package changes until its next rotation, and
   each revision is then approved by name (ADR-0055 amendment 16). A reader counts as switched only
   while its check is `up` or `grace` (`rotation_worker/readers.py`), so an outcome-alarm reader
   holding a standing finding holds that credential's retire step until the finding clears, and
   the rotation's 192-hour reader clock alerts on it. That is the registry telling the truth: a
   lane whose check is down hasn't shown it reads the new value. Part 1b goes first, because
   `sds-bump-driver`, already a listed reader, would otherwise hold any operator-token rotation
   today.

## Left for Phase 1

The year plan's Phase 1 replaces each launch agent with a scheduled container on a pinned image and
moves each lane's credentials to its own BWS identity. This pass leaves the following alone for that
reason:

- the plists and installers, beyond reinstalling the three that drifted;
- the Keychain reads and the two-identity scheme in the launchers;
- `activate-checkout.sh` and the main-tree working copy;
- provisioning every lane's check from its installer (5 of 20 installers call `sds_upsert_check`
  today).

Phase 1 keeps the dead-man and outcome alarms and the lane code, so Parts 1 and 4 carry over.

## Goals

- A finding a person must act on reaches alert-hub as one keyed alert, in every scheduled lane.
- A finding already delivered as a keyed alert no longer holds its lane's liveness check down.
- Every record this layer owns says what the code and production do.
- One test states the layer's outbound surface.

## Non-goals

- **A cache of ping URLs so a lane can ping when BWS is down.** It would store a value that lets
  anyone ping a check, and Phase 1 changes how lanes reach BWS.
- **Changing the exit-code vocabularies.** Codes 2 and 3 still mean opposite things across lanes;
  that is documented, and this pass keys everything per launcher.
- **Unmuting alerts.** Muting is Devon's choice in alert-hub.
- **A Sensors or Fork layer re-pass.** Part 1a adds alerts only; each program's logic stays.

## Anti-goals

- **A page per pass.** A keyed alert fires once per subject and resolves when the subject clears.
- **A finding that pings success without having been delivered.** That would trade a masked stop
  for a lost finding.

## Constraints

- Edits go in `.worktrees/runtime`; the main tree is never touched.
- No secret enters a prompt, argument or output. Every parsed `bws` call passes `--color no`.
- A launcher change takes effect at the lane's next run from `main`, proved from that run's log.
- Nothing merges that restarts the orchestrator or a lane while a rotation unit is ready or claimed,
  or a dispatched run is live.
- The manifest change merges only while no rotation of `bws-sds-operator-token` or the observer
  bearer is in flight, since a registry change breaks the destination equality of a running one.

## Design

### Part 1: Signals

#### 1a. Keyed alerts for the four log-only lanes

Each program raises one keyed alert per subject on its acting pass, through
`KeyedAlerts.sync(firing, unread)`, and resolves it when the subject clears. `unread` takes keys:
for a subject the pass couldn't read, the caller passes the remembered keys for that subject
(`KeyedAlerts.remembered()`), so they neither fire nor resolve. None of the four has a state
directory, so each state file is `~/Library/Application Support/sds/<lane>/alerts.json`, and the
activation sweep keeps one file per subcommand (`sweep-alerts.json`, `bind-alerts.json`), because
`sync` resolves every remembered key not firing and the two subcommands run in one launcher.

| Lane | Keys (built with `alert_key`) | Fires when | Unread |
|---|---|---|---|
| `tool_installer` | `tool-install:<tool>` | the row's action is in `FAILING_ACTIONS` (exit 3) | a row the pass didn't act on (`ACTION_NOT_PERMITTED`); an early exit for an unreadable window or fork revision calls no `sync` |
| `activation_sweep`, `sweep` | `checkout-behind:<member>`, `checkout-dirty:<member>`, `checkout-parked:<member>` | the checkout's `CONDITIONS` | an unavailable checkout |
| `activation_sweep`, `bind` | `activation-incomplete:<member>` | any of the member's units is bound but not fully activated | a member with any unanswered unit |
| `revision_watcher` | `revision-stale:<application>` | `Pass.findings` (behind its branch, or serving something not on it) | `Pass.unreadable` |
| `pin_watcher` | `pin-behind:<repository>` | `result.findings` | `result.unreadable` |

`<member>` is the member's repository name as `sds_scope` prints it, for firing and unread keys
alike, so an unavailable checkout's key matches the one it fired under. An undeclared application
is reported and is not a finding (`revision_watcher/cli.py`), so it raises nothing; Part 3 corrects
the launcher header that calls it one.

A first pass fires every standing subject once (`rea`, the three checkouts). That is wanted: each
went unheard.

The acting pass is the scheduled one: `--install` without `--dry-run` for the tool installer, and
any pass without `--dry-run` for the other three, whose plists pass no arguments. A `--dry-run`
pass sends nothing and needs no alert token. On an acting pass, the launcher fetches the
`sds-bridge` token (BWS `b415d03b-…`) through the narrow identity and passes it as
`SDS_BRIDGE_ALERT_TOKEN`; a token it can't read exits 1, as `run-execution-watch.sh` does. A failed
send (`AlertHubError`) or an unusable state file exits with the lane's could-not-report code, 3 for
the sweep and the two watchers and 2 for the tool installer, after the pass's own work. Each of
those codes outranks the lane's finding code, so a failed send can't hide a finding, and every exit
here pings `/fail`.

The guards that hold each lane's surface gain the new import: `test_out_of_process_isolation.py`,
`test_external_content_observes_only.py`, and `OUTBOUND_ALLOWLIST` if a new module imports `httpx`.

#### 1b. A delivered finding pings success (ADR-0021 amendment 3)

`sds_outcome_end` gains `--told <code>`: an exit with that code pings success with the report as the
body, and the log says the finding was delivered as a keyed alert. Every other non-zero exit still
pings `/fail`. The flags parse in any order after `<rc>`, and `--body-on-success` keeps its meaning
for exit 0. A launcher may pass `--told` only for a code whose every exit was a delivered keyed
alert, and only when a failed delivery exits with a different code. `test_sds_deadman.py` holds
each `--told` code to a sentence in that launcher's `EXIT CODES` block saying so.

| Lane | Today | After |
|---|---|---|
| `bump-driver` | 2 (a person decides) and 3 (unreadable, or alert-hub not told) both `/fail` | `--told 2`. Exit 2 is `results & FINDINGS`, and every such outcome carries a key; a failed sync returns 3. A structural test holds that every `Outcome` whose result is in `FINDINGS` has a key |
| `bump-proposer` | Unchanged | Its keyed alerts already exit 0 (status `alerted`); no exit-3 cause is a delivered alert |
| `rotation-decomposer` | Unchanged | None of its exit-2 causes (`declined`, `refused`, `superseded`) raises an alert (`alerts.py` says so), and a failed send never changes the code (call 4) |

#### 1c. The key read uses the shared parser

`sds_deadman_resolve` reads the Healthchecks key through `sds-bws.sh` (`sds_bws_value <uuid>
"$broad"`, sourced as `"$(dirname "${BASH_SOURCE[0]}")/sds-bws.sh"`), and parses the check listing
with the same venv Python `sds-bws.sh` finds, never `python3` from `PATH`. `sds_upsert_check`
already does the key read this way. `test_sds_bws.py`'s rule that no script parses `bws` output
itself extends from `scripts/run-*.sh` to `sds-deadman.sh`, and its pattern catches a pipe into
`VAR=… python3` as well as `python3`.

### Part 2: Scope

No change. `sds_scope.py` reads the member record and refuses an unreadable or empty one with exit
3. `deploy_orchestrator.py` and `compute_image_tags.py` name only the orchestrator itself. No
component in this layer acts on a hard-coded list of repositories.

### Part 3: Records

| Record | Correction |
|---|---|
| CLAUDE.md, the outcome-alarm rule | Add `run-rotation-decomposer.sh`; name which lanes ping on their acting run only; state the `--told` rule |
| CLAUDE.md, "SDS M2M bearers live in BWS project `SDS Operator`" | SYSTEM and VERIFIER are there; the operator and observer bearers are in `Rotation / Keepers` |
| CLAUDE.md, the dead-man rule | State that a Healthchecks key that can't be read is heard as the check going down after its grace |
| `scripts/sds-deadman.sh` header | Drop the counts and the group lists; point at each launcher's header and the test |
| `scripts/sds-bws.sh`, `scripts/sds-token.sh` headers | Every launcher that fetches uses it, after 1c; eleven launchers read both identities; the narrow account also reads two keepers by direct grant |
| `tests/scripts/test_sds_deadman.py` docstrings | Counts follow the code, or go |
| `.bws-secrets.toml` | Reader lists regenerated from a grep of each UUID; add `rotation-inbox` |
| `.cred-consumers.toml` | Add the five missing readers of `bws-sds-operator-token` and three of the observer bearer, each with its `healthchecks_check` read; add the SYSTEM and VERIFIER readers; correct the scope notes |
| ADR-0046 | The drills that exported the switches were retired (#319); `bump_merge_enabled` is a fourth switch |
| ADR-0050 | Two clients stay outside it; the tracker adapter left in #606 |
| ADR-0021 | Amendment 3 (Part 1b) and a note under amendment 1 that the sensors' observations are their fact home and alert-hub tells the person |
| `docs/operations/local-development.md` | The exit-code groups, the outcome-alarm list, and lane counts that age |
| `docs/operations/credentials.md` | The `main.py` line reference |
| `tests/architecture/edge_table.py` | The `activate-checkout.sh` comment says it fetches no secret, not "nothing": it runs `git fetch` and a fast-forward |
| `scripts/run-revision-watcher.sh` header, `revision_watcher/cli.py` docstring | An undeclared application is reported, not a finding; the "open decision" on telling a person is settled by Part 1a |
| Installed plists | Reinstall activation-sweep, estate-landing and rotation-decomposer from `main` |

A CLAUDE.md rule that changes moves its old text to `docs/history/claude-md-invariants-archive.md`.

### Part 4: Edges

`tests/architecture/test_runtime_edges.py`, built on `edge_table.py` like the other layers' tests:

- `alert_hub_client`: the captured `permits` predicate (`test_bridge_edges.py::_alert_hub_permits`)
  allows `POST /v1/alerts` and nothing else, over the corpus and its near misses, with a control
  that widens it and is caught.
- `estate_clients.confined`: one `httpx.Client`, redirects off by default.
- `deploy_orchestrator.py`: its one HTTP host is `https://sds.alobar.net`, and the commands it runs
  are exactly `git`, `gh run list`, `gh run view`, `gh workflow run`, `/usr/bin/security`, `bws` and
  the `image-release` binder. Its `gh` argv is built at run time, so the test runs the command's
  paths with a recording `World.run` and compares the recorded heads; a control that adds
  `gh pr merge` is caught.
- The shell helpers (`sds-deadman.sh`, `sds-install.sh`, `sds-bws.sh`, `sds-token.sh`): the
  external commands and literal hosts each one names, read by a small line parser added to
  `edge_table.py`; `launchctl` appears only in `install-*.sh`.
- `standing_identity` and `compute_image_tags`: no network and no process. `sds_scope`: the members
  read alone. `build_registry_bundle` and `shape_registry_context`: `git` only, through `subprocess`.
  `edge_table.py`'s `network` and `processes` scans gain a variant for single files under `scripts/`.
- The four lanes from Part 1a: their outbound sets gain `alert_hub_client` and the `sds-bridge`
  bearer, in their own layers' edge tests where those exist.

## Live proof

| Part | Proof | Kind |
|---|---|---|
| 1a `tool_installer` | The 03:15 pass raises `tool-install:rea` | Real |
| 1a `activation_sweep` | The 07:10 pass raises one alert per behind checkout, or none if they caught up | Real |
| 1a `revision_watcher`, `pin_watcher` | Tests only until a real finding | None |
| 1b `bump-driver` | The next :50 pass pings success with its standing finding, and `sds-bump-driver` flips up while its keyed alerts stay open | Real |
| 1b, failed delivery | Tests only | None |
| 1c | The next armed run of each lane logs `armed`; an unreadable key is proved by the November 3 history, not by staging one | Real for the read; historical for the failure |
| 3, manifests | None now: each package is revised at its credential's next due date (call 5) | None |
| 4 | `make check` | Tests |

Nothing is staged or forced to get a proof. A proof that needs one is asked of Devon first.

## Status

Built on branch `runtime-layer-pass`, not yet merged:

- **Part 1a:** the tool installer, the activation sweep (sweep and bind, one state file each), the
  revision watcher and the pin watcher raise keyed alerts with the `sds-bridge` sender.
- **Part 1b:** `sds_outcome_end --told <code>`, used by `run-bump-driver.sh` alone (`--told 2`).
- **Part 1c:** `sds-deadman.sh` reads the Healthchecks key through `sds-bws.sh` and parses the
  listing with the venv Python.
- **Part 2:** no change, as designed.
- **Part 3:** the records in the table, the credential manifests and ADR-0021 amendment 3. The
  plist reinstall waits for the merge.
- **Part 4:** `tests/architecture/test_runtime_edges.py`.
