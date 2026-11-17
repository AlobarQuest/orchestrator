#!/usr/bin/env bash
# One bump-proposer pass: a dependency bump the estate will not land by itself becomes proposed
# work (ADR-0028). WHICH BUMPS THOSE ARE IS READ FROM change-manager's LANDING POLICY (ADR-0038),
# not from a transcription of the GitHub Actions workflow that used to enforce it -- that workflow
# is being removed and the orchestrator becomes the merger, which would otherwise have left this
# pass reading a registry with no subject.
#
# WHAT A WRITING PASS ACTUALLY DOES, because it is more than the siblings do. It edits two
# lines of a standing package in the intent-packages checkout -- or, for a failed bump in a
# repository the landing policy declares (inert or deploying) that no package covers yet, creates
# the package (bridge Part 3; ADR-0028 amendment 3 widened it to deploying repositories, whose
# packages get reach live_estate where inert ones get source_repository) -- takes the
# revision through that repository's audited lifecycle, re-pins its hash fixture, COMMITS the
# files, PUBLISHES that commit to the authoring repository's default branch (ADR-0033), and
# proposes a work record in change-manager naming the target repository and the
# `dependency-update` change class. It cannot approve the record: change-manager's work policy
# approves one that conforms (ADR-0025 amendment), and a person decides every other one.
#
# A failed bump it cannot route -- no tooling for its ecosystem, a requirement range its tooling
# can't carry, a dependency a package in another ecosystem already covers -- and a bump with no
# tooling in an ecosystem its repository's block excludes, passing or not (docker in an inert
# repository, github_actions in a deploying one), holds one keyed alert open in alert-hub as the
# `sds-bridge` sender. A passing bump in any other ecosystem is the landing party's. The alert is
# resolved by the first writing pass that no longer raises it: its pull request merged or closed,
# or (in an ecosystem the landing party lands) a re-run passed. A re-run failing again but not yet
# settled holds the key.
# The keys it has fired are kept in `~/Library/Application Support/sds/bump-proposer/`.
#
# PUBLISHING IS THE COMMIT FINISHED, NOT A SECOND ACT. Until ADR-0033 the pass stopped at the
# commit and nobody was told to move the branch, so the revision got no CI, the `source_commit`
# the intake records named a commit only this machine held, and local `main` drifted from
# origin under every other lane that reads this checkout. It surrenders no gate: that branch
# takes a direct push and reports its required checks afterwards whoever performs it.
#
# ORDER MATTERS ON THE DAY THE OBSERVATION LANE SHIPS, AND GETTING IT WRONG IS QUIET. This pass
# files the fact before it proposes, and the orchestrator accepts this producer's vocabulary only
# once the image carrying it is live AND its migration has been applied -- neither of which a
# `git pull` of this checkout does, and this lane adds no console script to pause at. Land it,
# migrate, roll the image, then pull here. Out of that order every bump reports `unobserved`,
# nothing is proposed, and the pass exits 3 rather than failing loudly.
#
# IT REFUSES A DIRTY CHECKOUT. Committing is not tidiness -- the orchestrator's intake payload
# records `source_commit` as that checkout's git HEAD, so a revision left uncommitted is
# registered against a commit that does not contain it. Refusing a dirty tree is what stops
# this sweeping somebody else's work-in-progress into a commit it wrote the message for.
#
# WHY HOURLY IS WRONG FOR THIS ONE, unlike the deploy producer. Every writing pass commits to
# a checkout on this machine, so a pass that finds nothing to do is free and a pass that finds
# something is a commit somebody has to notice. Daily, and before the carry's own 07:05 run
# -- though be precise about what that ordering buys, because it is less than it reads:
# the carry fires once, at 07:05, and reads only APPROVED records, so a record proposed
# here is carried the same day only if a person approves it within fifteen minutes.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  nothing arose that needs a person for an anomalous reason. NOT "the pass did
#      nothing": a pass that revised a package, committed it and proposed a record also
#      exits 0, because `proposed` is this program's ordinary output rather than a
#      finding. Read the lines to learn whether anything was written.
#   1  the tool itself failed (a missing or unreadable credential).
#   2  the tool ran but could not use its inputs (no standing packages, a dirty checkout, a
#      checkout carrying a commit a previous pass wrote and could not publish -- a state no
#      further revision may be built on, and one only a person can resolve -- or, since
#      ADR-0038, a landing policy it could not read: unreachable, refused, malformed, declaring
#      no inert population or no deploying one (ADR-0028 amendment 3), a `landing` block that
#      decides by update type, or an inert population that does not permit the update bot whose
#      pull requests are the only ones this pass can see. One declaration covers every
#      repository, so a rule this pass cannot read stops the pass rather than one repository.
#      An absent declaration is a REFUSAL and not a waiver: it is a version that did not decide
#      the question, and this pass will not guess what it would have decided).
#   3  something was found -- an alert alert-hub would not take, a standing package targeting a
#      repository the landing policy declares neither inert nor deploying (`not-declared-landing`),
#      a pull request whose title and update trailer disagree, a bump whose
#      CAUSE COULD NOT BE FILED (G1+G2: the observation is the cause a proposal names, so a bump
#      that cannot be stated is reported `unobserved` and nothing is proposed for it), a record
#      stranded by a bump that moved, a refused proposal, or a revision that was committed and
#      could not be published (ADR-0033: a failed publish is a finding, not a warning; the line
#      names the sha it stranded).
# On a `--submit` run, 1, 2 and 3 all ping `/fail` with the report as the body, and 0 pings success.
# The program's code is this script's code.
#
# BARE INVOCATION IS A DRY RUN. `--submit` separates reporting from writing, and this wrapper
# must not supply it: an operator reaching for "just look at what it would do" would otherwise
# mint package revisions in a tamper-evident chain that has no undo.
#
# TWO HOSTS, ONE LAUNCHER (ADR-0059 increment 4, step 3f-4). A set `BWS_ACCESS_TOKEN` means the
# lane's container: `lane-run` passes the lane's own token as its one secret. There the launcher
# skips activation (the container runs the image's own code, and clones its packages checkout fresh
# each pass), reads no Keychain item and calls no `gh`, and reads every secret with that one token,
# in the environment, never as a process argument:
#   - every pass: the change-manager READ bearer (`314f276d-…`), the orchestrator OBSERVER bearer
#     (`f793576f-…`), the `sds-bridge` alert sender (`b415d03b-…`) and the read-only App's key
#     (`314a966c-…`, piped straight into the mint), as the Mac reads the first three on every pass;
#   - `--submit` only: the change-manager PROPOSE bearer (`acccb346-…`), the factory-events append
#     password (`2ce0dbc7-…`) and the "SDS lanes" App's key (`b8f547b9-…`, piped straight into
#     its mint).
# It mints `BUMP_PROPOSER_GITHUB_TOKEN` from the read-only App for exactly the repositories
# `bump-proposer repositories` prints (ADR-0059 decision 2, Q9), clones intent-packages with it
# through `scripts/sds-git.sh` into a directory the pass discards, and on `--submit` mints the
# "SDS lanes" App's push token for intent-packages alone and hands it to git, with the App's `[bot]`
# identity, in place of the read token's header. The push token is in no variable a program reads:
# only git's extraheader carries it. The chain's three settings come from the compose (a missing
# one fails the pass; there is no file mode). intent-packages' lifecycle runs under the image's
# interpreter with the clone's `src` on `PYTHONPATH`, as `package_sources` runs it for the carrier,
# because a venv built in the clone can't append to the chain (the lane report says why). The
# launcher unsets `BWS_ACCESS_TOKEN` before git or any program runs. Without a set
# `BWS_ACCESS_TOKEN` this is the Mac's launchd job, unchanged until increment 6.
#
# Usage:
#   scripts/run-bump-proposer.sh              # dry run: reports, writes nothing
#   scripts/run-bump-proposer.sh --submit     # revises, approves by policy, proposes
set -uo pipefail

# The PROPOSE-scoped change-manager bearer, shared with the deploy producer. Deliberately NOT
# the full one: this program must never be able to approve the record it writes, or the human
# decision ADR-0028 keeps would be one the machine could take for itself.
CHANGE_MANAGER_PROPOSE_UUID="${BUMP_PROPOSER_BWS_UUID:-acccb346-4baa-43ec-a1d4-b4a400c048ee}"

# The READ-scoped change-manager bearer (ADR-0038). A SECOND credential rather than a wider use of
# the one above, and the reason is the property this script states two paragraphs down: a dry run
# must not need -- or touch -- a credential that can write to change-manager or the orchestrator.
# (It does fetch the alert sender token, which it never uses, and creates the state directory.)
# Since ADR-0038 a dry run must READ
# change-manager, because the rule it reports against is declared there rather than transcribed
# into this repository; had this credential not already existed, that property would have had to
# be traded for the read. Measured 2026-08-31, and the pair is what makes it mean something:
# `GET /api/landing-policy` answers 200 with this bearer and 401 with a garbage one, while
# `POST /api/deploy-changes` answers 403 with this bearer and 422 -- request validation, i.e.
# reached -- with the propose one. The scope is enforced by change-manager, not merely declared.
CHANGE_MANAGER_READ_UUID="${BUMP_PROPOSER_READ_BWS_UUID:-314f276d-55ca-4ddc-a24d-b4a3013508cd}"

# The orchestrator's OBSERVER bearer (G1+G2). The signal->work contract requires every proposed
# record to name the observation that caused it, so a writing pass files that fact first -- and
# this is the credential whose ENTIRE write surface is `POST /api/v1/observations`. Deliberately
# not the SYSTEM bearer, which can transition units and land pull requests: this lane must not be
# able to, and the whole point of the record it writes is that a person decides.
#
# It is the same bearer every observe-and-report lane holds, by design, which is why the three
# variables exported for it below are the estate-wide spelling rather than a `BUMP_PROPOSER_` one.
OBSERVER_BEARER_UUID="${BUMP_PROPOSER_OBSERVER_BWS_UUID:-f793576f-e9aa-4f9d-8089-b4a000b9e2d5}"

# The `sds-bridge` sender's alert-hub token (bridge Part 3), BWS `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE`
# in `SDS Operator`, and listed in `.bws-secrets.toml`.
SDS_BRIDGE_ALERT_UUID="${BUMP_PROPOSER_ALERT_BWS_UUID:-b415d03b-f92d-46b1-8a9d-b4f10103df49}"

# The read-only lane App's private key and id (ADR-0059 increment 3, Q9), `alobar-sds-lanes-read`,
# in `SDS / Lane Apps`. Read in the container only, piped straight into `sds-github-mint`.
READ_APP_KEY_UUID="314a966c-5654-4e3c-8358-b4fd01268775"
READ_APP_ID="5684758"
# The "SDS lanes" App's private key and id (ADR-0059 decision 2, Q1), `alobar-sds-lanes`, whose
# installation on intent-packages holds `contents: write`. Read in the container on `--submit`
# only, piped straight into `sds-github-mint`; the token it mints reaches git alone.
PUSH_APP_KEY_UUID="b8f547b9-67d7-4417-adc0-b4fd0126b0f0"
PUSH_APP_ID="5684778"
# The App's `[bot]` identity, which authors and commits each revision this lane publishes
# (ADR-0059 decision 2: without it, Q1's "its commits are the App's" would be false). GitHub's
# noreply address for a bot is `<bot user id>+<slug>[bot]@users.noreply.github.com`; the bot user
# id is the App's bot account's (`GET /users/alobar-sds-lanes%5Bbot%5D`, read 2026-11-17), neither
# the App id nor the installation id.
PUSH_APP_BOT_NAME="alobar-sds-lanes[bot]"
PUSH_APP_BOT_EMAIL="354576843+alobar-sds-lanes[bot]@users.noreply.github.com"
# The factory-events append password (`factory-events-append-password`), `--submit` only.
FACTORY_EVENTS_APPEND_UUID="2ce0dbc7-eeac-4bf0-8204-b50200c97516"
# The packages repository the container clones each pass, as a bare `https://github.com/` URL.
PACKAGES_REMOTE="https://github.com/AlobarQuest/intent-packages.git"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export BUMP_PROPOSER_PACKAGES_CHECKOUT="${BUMP_PROPOSER_PACKAGES_CHECKOUT:-$HOME/Projects/intent-packages}"
# The lane's container sets `BWS_ACCESS_TOKEN` and the Mac never does (its identities are Keychain
# items, read into their own variables below).
IN_CONTAINER=0
if [ -n "${BWS_ACCESS_TOKEN:-}" ]; then IN_CONTAINER=1; fi

# THIS CHECK IS AN OUTCOME ALARM (ADR-0021 amendment 2), as `sds-fork-sync-watch`'s is. `launchd`
# discards this script's exit code, so the codes above reach a person only through Healthchecks:
# `/start` before the pass, then success on 0 or `/fail` on anything else, with the pass's report
# as the body, which alert-hub delivers. A finding pages; a pass that stops pinging goes down after
# the grace. Only a `--submit` run pings: a dry run is a person looking, and must neither page nor
# reset the timer of a schedule that may have stopped. Nothing here re-execs this script (the one
# `activate_checkout` below names a checkout this script doesn't live in), so this is the first
# thing the pass does, BEFORE the BWS block below: that block can `exit 1` on a Keychain item this
# machine does not have, and each of those exits now pings `/fail` with its reason. An unresolved
# check logs one line and never stops the pass.
CHECK_NAME="sds-bump-proposer"
case " $* " in
  *" --submit "*) SUBMITTING=1 ;;
  *) SUBMITTING=0 ;;
esac
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_outcome_begin "$CHECK_NAME" bump-proposer "$SUBMITTING"

# THE PACKAGES CHECKOUT IS ACTIVATED BEFORE THE PASS READS IT, on a `--submit` run only (ADR-0031
# amendment 1). The pass revises and publishes from that checkout, and anything landing in
# intent-packages leaves it behind: a revision committed on a behind `main` is refused
# non-fast-forward, and `standing.require_publishable` then refuses every later pass until a
# person reconciles it. One argument, so the helper never re-execs (this script doesn't live
# there); it may run `uv sync` in that checkout, so it comes before any bearer is fetched. A
# checkout carrying an unpublished commit can't fast-forward: the line says HELD and the pass
# refuses it as before. A dry run writes nothing, so it leaves the checkout alone.
# Skipped in the container, which has no `activate-checkout.sh` and clones its checkout each pass.
if [ "$IN_CONTAINER" -eq 0 ]; then
  _SDS_ACTIVATE="$HOME/.claude/bin/activate-checkout.sh"
  if [ -r "$_SDS_ACTIVATE" ]; then
      # shellcheck source=/dev/null
      . "$_SDS_ACTIVATE"
  else
      activate_checkout() {
          echo "[activation] helper missing at $HOME/.claude/bin/activate-checkout.sh —" \
               "this run is not activated"
      }
  fi
  if [ "$SUBMITTING" -eq 1 ]; then
    activate_checkout "$BUMP_PROPOSER_PACKAGES_CHECKOUT"
  else
    echo "[activation] dry run: leaving ${BUMP_PROPOSER_PACKAGES_CHECKOUT/#$HOME/~} as it is"
  fi
fi

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a
# pipe, which breaks the parse below. The IDENTITY IS THE SECOND ARGUMENT rather than an ambient
# export -- see the block underneath.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

unset CHAIN_DB_HOST CHAIN_DB_PORT CHAIN_DB_NAME CHAIN_PASSWORD BUMP_PROPOSER_EVENTS_IDENTITY \
  PACKAGES_CLONE _PUSH_TOKEN
CHAIN_MODE="file"
if [ "$IN_CONTAINER" -eq 1 ]; then
  # ---- THE LANE'S CONTAINER (ADR-0059 increment 4, step 3f-4) ---------------------------------
  # Every read takes the lane's one token from the environment (`sds_bws_value` with one argument),
  # so it never appears in a process argument, and no Keychain item is read. Each secret is always
  # read, never taken from the environment, which `lane-run` passes nothing else through.
  FROM=" (the lane's token)"

  # THE CHAIN'S SETTINGS COME FROM THE COMPOSE, and a missing one fails the pass, a dry run too,
  # before anything is read, as the Mac's switch file is read on every pass. There is no
  # `~/.factory/chain-db.env` here and no file mode to fall back to: an approval emitted anywhere
  # but the VPS chain is one the carrier never sees (ADR-0059 amendment 7). Only a `--submit` pass
  # passes them on, with the append password, below; a dry run approves nothing.
  for _setting in FACTORY_EVENTS_DB_HOST FACTORY_EVENTS_DB_PORT FACTORY_EVENTS_DB_NAME; do
    [ -n "${!_setting:-}" ] || sds_outcome_fail \
      "FATAL: $_setting is not set in the lane's container: the factory-events chain can't be reached"
  done
  CHAIN_DB_HOST="$FACTORY_EVENTS_DB_HOST"
  CHAIN_DB_PORT="$FACTORY_EVENTS_DB_PORT"
  CHAIN_DB_NAME="$FACTORY_EVENTS_DB_NAME"
  # Passed to `bump-proposer` alone, as an assignment prefix, and only on `--submit`.
  unset FACTORY_EVENTS_APPEND_PASSWORD FACTORY_EVENTS_READ_PASSWORD \
    FACTORY_EVENTS_DB_HOST FACTORY_EVENTS_DB_PORT FACTORY_EVENTS_DB_NAME

  # Every pass, as on the Mac: the landing policy, the member record and the alert sender.
  BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_READ_UUID")"
  [ -n "$BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the READ-scoped change-manager credential from BWS$FROM"
  export BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN
  OBSERVER_BEARER="$(sds_bws_value "$OBSERVER_BEARER_UUID")"
  [ -n "$OBSERVER_BEARER" ] || sds_outcome_fail \
    "FATAL: could not read the orchestrator observer bearer from BWS$FROM"
  export SDS_MEMBERS_OBSERVER_TOKEN="$OBSERVER_BEARER"
  SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID")"
  [ -n "$SDS_BRIDGE_ALERT_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the sds-bridge alert sender token from BWS$FROM"
  export SDS_BRIDGE_ALERT_TOKEN
  # The compose sets it to the lane's volume (decision 1, "Lane state").
  [ -n "${BUMP_PROPOSER_STATE_DIR:-}" ] || sds_outcome_fail \
    "FATAL: BUMP_PROPOSER_STATE_DIR is not set in the lane's container"
  export BUMP_PROPOSER_STATE_DIR
  mkdir -p "$BUMP_PROPOSER_STATE_DIR" || sds_outcome_fail \
    "FATAL: could not create the alert state directory $BUMP_PROPOSER_STATE_DIR"

  if [ "$SUBMITTING" -eq 1 ]; then
    BUMP_PROPOSER_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_PROPOSE_UUID")"
    [ -n "$BUMP_PROPOSER_CHANGE_MANAGER_TOKEN" ] || sds_outcome_fail \
      "FATAL: could not read the propose-scoped change-manager credential from BWS$FROM"
    export BUMP_PROPOSER_CHANGE_MANAGER_TOKEN
    ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
    ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
    ORCHESTRATOR_API_TOKEN="$OBSERVER_BEARER"
    export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN
    CHAIN_PASSWORD="$(sds_bws_value "$FACTORY_EVENTS_APPEND_UUID")"
    [ -n "$CHAIN_PASSWORD" ] || sds_outcome_fail \
      "FATAL: could not read the factory-events append password from BWS$FROM"
    CHAIN_MODE="database"
  fi

  # THE REPOSITORIES ARE THE PROGRAM'S, NOT LISTED HERE: `bump-proposer repositories` prints the
  # packages repository and each repository the member record grants `dependency_updates`, a
  # superset of the lane this pass reads (ADR-0059 decision 2: the mint names only the lane's
  # repositories). AN UNDERIVABLE LIST MINTS NOTHING, AND SAYS SO: the subcommand exits 2 for an
  # unreadable member record, as the pass would, and that code is the pass's; any other non-zero
  # code is passed through verbatim. Its reason, the subcommand's last stderr line, is in the
  # failure's message, which `/fail` carries.
  _DERIVE_ERR="$SDS_OUTCOME_REPORT.derive"
  REPOSITORIES="$(env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/bump-proposer" repositories \
    2> "$_DERIVE_ERR")"
  derive_rc=$?
  _derive_reason="$(tail -n 1 "$_DERIVE_ERR")"
  rm -f -- "$_DERIVE_ERR"
  if [ "$derive_rc" -ne 0 ]; then
    sds_outcome_fail \
      "the bump lane's repositories could not be derived: bump-proposer repositories exited $derive_rc (${_derive_reason:-no reason given}); nothing was minted or proposed" \
      "$derive_rc"
  fi
  TARGETS=()
  while IFS= read -r repository; do
    [ -n "$repository" ] && TARGETS+=(--repository "$repository")
  done <<< "$REPOSITORIES"
  [ "${#TARGETS[@]}" -gt 0 ] || sds_outcome_fail \
    "the bump lane's repositories could not be derived (an empty list); nothing was minted or proposed" 2

  # THE READ TOKEN IS MINTED EACH PASS, dry or writing. The App's key goes from the BWS read
  # straight to the mint's stdin in its stored form, base64 of the PEM; it is never in a variable,
  # an argument or a file, and the mint runs without the lane's token. The four permissions are
  # what the pass's reads use: the clone and each repository's record (`contents`, `metadata`), its
  # open pull requests and their first commits (`pull_requests`), and their workflow runs and jobs
  # (`actions`, `landing_ledger/github.py::_concluded_checks`).
  BUMP_PROPOSER_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
    | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
        --app-id "$READ_APP_ID" "${TARGETS[@]}" \
        --permission metadata=read --permission contents=read \
        --permission pull_requests=read --permission actions=read)"
  mint_rc=$?
  if [ "$mint_rc" -ne 0 ] || [ -z "$BUMP_PROPOSER_GITHUB_TOKEN" ]; then
    sds_outcome_fail "FATAL: could not mint the bump lane's GitHub read token from the read-only App (sds-github-mint exited $mint_rc)"
  fi
  export BUMP_PROPOSER_GITHUB_TOKEN

  # THE PUSH TOKEN, `--submit` only, for intent-packages alone, with `contents: write` and nothing
  # else beside `metadata`. It is held in this script's own unexported variable until git's
  # credential takes it below, and never in `BUMP_PROPOSER_GITHUB_TOKEN`, which an HTTP client reads.
  if [ "$SUBMITTING" -eq 1 ]; then
    _PUSH_TOKEN="$(sds_bws_value "$PUSH_APP_KEY_UUID" \
      | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
          --app-id "$PUSH_APP_ID" --repository AlobarQuest/intent-packages \
          --permission metadata=read --permission contents=write --allow-write)"
    push_rc=$?
    if [ "$push_rc" -ne 0 ] || [ -z "$_PUSH_TOKEN" ]; then
      sds_outcome_fail "FATAL: could not mint the bump lane's push token from the SDS lanes App (sds-github-mint exited $push_rc)"
    fi
  fi

  # EVERY BWS READ IS DONE, and nothing after this needs a BWS identity, git and the programs
  # included, so the lane's token reaches none of them.
  unset BWS_ACCESS_TOKEN

  # THE PACKAGES CLONE, shallow, at `main`, into a directory the pass discards (decision 1), with
  # the read token. `BUMP_PROPOSER_CLONE_ROOT` is a test seam: `lane-run` never passes it.
  # shellcheck disable=SC1091
  source "$REPO_ROOT/scripts/sds-git.sh"
  sds_git_credential BUMP_PROPOSER_GITHUB_TOKEN || sds_outcome_fail \
    "FATAL: could not give git the bump lane's GitHub read credential"
  _CLONE_ROOT="${BUMP_PROPOSER_CLONE_ROOT:-/var/tmp/sds-bump-proposer}"
  PACKAGES_CLONE="$_CLONE_ROOT/intent-packages"
  { mkdir -p "$_CLONE_ROOT" && chmod 0700 "$_CLONE_ROOT"; } || sds_outcome_fail \
    "FATAL: could not create the clone directory $_CLONE_ROOT"
  rm -rf -- "$PACKAGES_CLONE" || sds_outcome_fail \
    "FATAL: could not discard the previous pass's packages clone at $PACKAGES_CLONE"
  # git's own message is kept for the report; it never holds the header (no trace is on).
  _clone_out="$(git clone --quiet --depth 1 --single-branch --branch main \
    "$PACKAGES_REMOTE" "$PACKAGES_CLONE" 2>&1)" || sds_outcome_fail \
    "FATAL: could not clone $PACKAGES_REMOTE into $PACKAGES_CLONE: $_clone_out"
  export BUMP_PROPOSER_PACKAGES_CHECKOUT="$PACKAGES_CLONE"

  # ON `--submit`, GIT PUSHES AS THE "SDS LANES" APP, and its commits are the App's: the push
  # token's header replaces the read token's (one extraheader), and the identity pairs author and
  # commit each revision. `standing._run` passes no `env`, so `standing.publish`'s push inherits them.
  if [ "$SUBMITTING" -eq 1 ]; then
    sds_git_credential _PUSH_TOKEN "$PUSH_APP_BOT_NAME" "$PUSH_APP_BOT_EMAIL" || sds_outcome_fail \
      "FATAL: could not give git the bump lane's push credential"
    unset _PUSH_TOKEN
  fi
  # The image's security-standards (its `src` and registry, at the pin), which intent-packages'
  # emitter locates the chain's writer by. `lane-run` passes no `SECURITY_STANDARDS_DIR`.
  export SECURITY_STANDARDS_DIR="$REPO_ROOT/security-standards"
else
  # ---- THE MAC, unchanged until increment 6 ----------------------------------------------------

  # TWO BWS IDENTITIES SINCE G1+G2, AND NEITHER CAN READ THE OTHER'S SECRET. The change-manager
  # bearers live in a project only the BROAD machine account can read; the orchestrator's observer
  # bearer lives in `Rotation / Keepers`, readable only by the narrow read-only `sds-operator` account
  # (by a direct grant on that one secret).
  # Each is read directly from its own Keychain item into its own variable and passed to the fetch
  # that needs it, so no identity is ever ambient and each fetch says which one it used.
  #
  # THIS SCRIPT USED TO EXPORT ONE, and that was correct while every secret it read belonged to the
  # broad account. The upgrade is NOT "add a source of sds-token.sh": that helper respects an
  # already-set `BWS_ACCESS_TOKEN` and exports, so alongside the old bootstrap one ambient value
  # would have served as both identities -- after which NO value of it works, a failure that appears
  # only in an operator's shell and never under launchd.
  #
  # NAME THE FAILURE, because it does not name itself: `bws` answers `404 Resource not found` for a
  # secret an identity is DENIED, which reads as a missing secret rather than as the wrong identity
  # and has cost this estate a diagnosis more than once.
  BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
  [ -n "$BROAD_IDENTITY" ] || sds_outcome_fail \
    "FATAL: no broad BWS identity for the change-manager credentials (service Claude)"

  # FETCHED ON EVERY RUN, dry or writing. The landing policy is what this pass reports against, so
  # a dry run needs it; it is READ-scoped, so needing it surrenders nothing. Fetched BEFORE the
  # propose credential so that a run which can read but not write fails on the write path, where
  # the message names the credential that is actually missing.
  BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_READ_UUID" "$BROAD_IDENTITY")"
  export BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN
  [ -n "${BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN:-}" ] || sds_outcome_fail \
    "FATAL: could not read the READ-scoped change-manager credential from BWS (broad)"

  # THE OBSERVER BEARER IS FETCHED ON EVERY RUN, dry or writing, because the member record is the
  # scope authority: which repositories this lane considers is read from it, so even a dry run needs
  # the read. It is exported as `SDS_MEMBERS_OBSERVER_TOKEN` always, and as the estate-wide
  # `ORCHESTRATOR_API_*` trio only for a writing run, since filing an observation is a write and a
  # dry run proposes nothing, so it has no cause to file. One fetch serves both names.
  SDS_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
  [ -n "$SDS_IDENTITY" ] || sds_outcome_fail \
    "FATAL: no narrow BWS identity for the observer bearer (service Claude)"
  OBSERVER_BEARER="$(sds_bws_value "$OBSERVER_BEARER_UUID" "$SDS_IDENTITY")"
  [ -n "$OBSERVER_BEARER" ] || sds_outcome_fail \
    "FATAL: could not read the orchestrator observer bearer from BWS (narrow)"
  SDS_MEMBERS_OBSERVER_TOKEN="$OBSERVER_BEARER"
  export SDS_MEMBERS_OBSERVER_TOKEN

  # THE SENDER TOKEN IS FETCHED ON EVERY RUN, with the same narrow identity. Only a writing pass
  # sends, but a dry run that skipped the fetch would hide a missing token until the morning pass.
  SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID" "$SDS_IDENTITY")"
  [ -n "$SDS_BRIDGE_ALERT_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the sds-bridge alert sender token from BWS (narrow)"
  export SDS_BRIDGE_ALERT_TOKEN
  export BUMP_PROPOSER_STATE_DIR="${BUMP_PROPOSER_STATE_DIR:-$HOME/Library/Application Support/sds/bump-proposer}"
  mkdir -p "$BUMP_PROPOSER_STATE_DIR" || sds_outcome_fail \
    "FATAL: could not create the alert state directory $BUMP_PROPOSER_STATE_DIR"

  # ONLY FETCHED FOR A WRITING RUN. A dry run reports what it would do and sends nothing, so it
  # must not need -- or touch -- a credential that writes to change-manager or the orchestrator. The
  # alert sender token above is fetched on every run, but a dry run never sends with it.
  if [ "$SUBMITTING" -eq 1 ]; then
    BUMP_PROPOSER_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_PROPOSE_UUID" "$BROAD_IDENTITY")"
    export BUMP_PROPOSER_CHANGE_MANAGER_TOKEN

    ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
    ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
    ORCHESTRATOR_API_TOKEN="$OBSERVER_BEARER"
    export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN
  fi

  # `set -e` is deliberately not used, so a failed fetch would otherwise leave these EMPTY and
  # fall through. The tool refuses an empty credential and would exit 2 -- fail-closed, but
  # reporting "unusable input" for what is actually a credential failure. Named separately so the
  # message points at the identity that could not read, which is the question a 404 from `bws`
  # does not answer by itself.
  if [ "$SUBMITTING" -eq 1 ] && [ -z "${BUMP_PROPOSER_CHANGE_MANAGER_TOKEN:-}" ]; then
    sds_outcome_fail "FATAL: could not read the propose-scoped change-manager credential from BWS (broad)"
  fi

  # THE GITHUB CREDENTIAL HAS NO BWS RECORD, exactly as the deploy producer's and the ledger's do
  # not. It falls back to `gh auth token`, an interactive login: a scheduled job resting on one
  # breaks the moment the login is re-issued. The scheduled run then pings `/fail` with the
  # reason (ADR-0021 amendment 2), but the source is still a gap, not a design.
  if [ -z "${BUMP_PROPOSER_GITHUB_TOKEN:-}" ]; then
    BUMP_PROPOSER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
    export BUMP_PROPOSER_GITHUB_TOKEN
  fi
  if [ -z "${BUMP_PROPOSER_GITHUB_TOKEN:-}" ]; then
    sds_outcome_fail "FATAL: no GitHub token (set BUMP_PROPOSER_GITHUB_TOKEN or run gh auth login)"
  fi

  # THE FACTORY-EVENTS CHAIN, behind the switch file (FE-5 cut-over plan, "The switch"; ADR-0055
  # amendments 24 item 4 and 26; ADR-0059 amendment 7 act 5). `approve --by-policy`, and every other
  # lifecycle command the pass runs, inherits `bump-proposer`'s environment, and a set
  # `FACTORY_EVENTS_APPEND_PASSWORD` with the three `FACTORY_EVENTS_DB_*` settings makes them append to
  # the database chain (`bump_proposer/standing.py` redacts those values from a child's refusal).
  #   - No `~/.factory/chain-db.env`: nothing here runs. No password is fetched and no chain variable
  #     is set or cleared, so whatever this environment carries passes through, as before the switch.
  #   - The file present: it is read with the refusals in `sds-bws.sh`, on every run, so a malformed
  #     switch fails a dry run too. Only a `--submit` run fetches the append password, through the
  #     `bump-proposer` account's own Keychain item (never the broad or narrow identity above), and
  #     passes the password and the settings to `bump-proposer` alone, as an assignment prefix. A dry
  #     run fetches nothing and passes nothing, and with the switch on an ambient chain variable is
  #     cleared first. Any failure is `sds_outcome_fail`, never the file.
  # Every name below is this script's own, unset before use, so none is left exported by a caller's
  # environment, and none is exported: the token reaches only `bws`, the password only the child.
  if factory_events_switch_present; then
    factory_events_read_settings \
      || sds_outcome_fail "FATAL: the factory-events switch: $(factory_events_settings_refusal)"
    # With the switch on, this script is the only source of the chain variables: an ambient one
    # (a shell, or `~/.factory/env` under `set -a`) is cleared, so no other child gets one. A
    # retired `FACTORY_EVENTS_*_DSN` is left set, so the child refuses it by name.
    unset FACTORY_EVENTS_APPEND_PASSWORD FACTORY_EVENTS_READ_PASSWORD \
      FACTORY_EVENTS_DB_HOST FACTORY_EVENTS_DB_PORT FACTORY_EVENTS_DB_NAME
    if [ "$SUBMITTING" -eq 1 ]; then
      BUMP_PROPOSER_EVENTS_IDENTITY="$(/usr/bin/security find-generic-password \
        -s 'Claude' -a 'BWS_ACCESS_TOKEN_BUMP_PROPOSER' -w 2>/dev/null || true)"
      [ -n "$BUMP_PROPOSER_EVENTS_IDENTITY" ] || sds_outcome_fail \
        "FATAL: no BWS identity for the factory-events append password (Keychain Claude / BWS_ACCESS_TOKEN_BUMP_PROPOSER)"
      CHAIN_PASSWORD="$(sds_bws_value "$FACTORY_EVENTS_APPEND_UUID" "$BUMP_PROPOSER_EVENTS_IDENTITY")"
      unset BUMP_PROPOSER_EVENTS_IDENTITY
      [ -n "$CHAIN_PASSWORD" ] || sds_outcome_fail \
        "FATAL: could not read the factory-events append password from BWS (bump-proposer)"
      CHAIN_MODE="database"
    fi
  fi
fi

# THE LIFECYCLE'S INTERPRETER, in the container only. intent-packages' lifecycle commands run under
# the image's own interpreter with the clone's `src` first on `PYTHONPATH`, as `package_sources`
# runs intent-packages for the carrier, rather than a venv built in the clone: intent-packages
# declares neither `jsonschema` nor `psycopg` at runtime, and its emitter runs the chain's writer
# (`factory_events`) under that same interpreter where security-standards carries no `.venv-events`,
# as the image's doesn't. The image's venv carries both, and every runtime dependency intent-packages
# declares. Both reach `bump-proposer` alone, as an assignment prefix; the Mac sets neither, and its
# two invocations are as they were.
if [ "$IN_CONTAINER" -eq 1 ] && [ "$CHAIN_MODE" = "database" ]; then
  BUMP_PROPOSER_PACKAGES_PYTHON="$REPO_ROOT/.venv/bin/python" \
    PYTHONPATH="$PACKAGES_CLONE/src" \
    FACTORY_EVENTS_APPEND_PASSWORD="$CHAIN_PASSWORD" \
    FACTORY_EVENTS_DB_HOST="$CHAIN_DB_HOST" \
    FACTORY_EVENTS_DB_PORT="$CHAIN_DB_PORT" \
    FACTORY_EVENTS_DB_NAME="$CHAIN_DB_NAME" \
    "$REPO_ROOT/.venv/bin/bump-proposer" "$@" > "$SDS_OUTCOME_REPORT" 2>&1
elif [ "$IN_CONTAINER" -eq 1 ]; then
  BUMP_PROPOSER_PACKAGES_PYTHON="$REPO_ROOT/.venv/bin/python" \
    PYTHONPATH="$PACKAGES_CLONE/src" \
    "$REPO_ROOT/.venv/bin/bump-proposer" "$@" > "$SDS_OUTCOME_REPORT" 2>&1
elif [ "$CHAIN_MODE" = "database" ]; then
  FACTORY_EVENTS_APPEND_PASSWORD="$CHAIN_PASSWORD" \
    FACTORY_EVENTS_DB_HOST="$CHAIN_DB_HOST" \
    FACTORY_EVENTS_DB_PORT="$CHAIN_DB_PORT" \
    FACTORY_EVENTS_DB_NAME="$CHAIN_DB_NAME" \
    "$REPO_ROOT/.venv/bin/bump-proposer" "$@" > "$SDS_OUTCOME_REPORT" 2>&1
else
  "$REPO_ROOT/.venv/bin/bump-proposer" "$@" > "$SDS_OUTCOME_REPORT" 2>&1
fi
rc=$?
unset CHAIN_PASSWORD
# The container's clone is removed here, at the end of a pass that reaches it, and before every
# clone. A clone left behind holds no credential (the header lives only in the environment), so a
# failed removal is logged and doesn't change the pass's code.
if [ -n "${PACKAGES_CLONE:-}" ]; then
  rm -rf -- "$PACKAGES_CLONE" \
    || echo "[clone] could not discard $PACKAGES_CLONE; the next pass removes it" >> "$SDS_OUTCOME_REPORT"
fi
sds_outcome_end "$rc"
