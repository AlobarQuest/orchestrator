#!/usr/bin/env bash
# One rotation-proposer pass: a credential infraops reports due for rotation becomes a revision of
# its standing package and, once a named human has approved that revision, proposed work
# (ADR-0054 amendment 1). Modelled on run-bump-proposer.sh; read that header for the reasoning this
# one shares, and this one for where the two differ.
#
# WHAT IS DUE IS infraops' ANSWER, NOT THIS PROGRAM'S. The pass runs
# `node $ROTATION_PROPOSER_INFRAOPS_CHECKOUT/dist/cli/security-drift-cli.js cred-findings`, which
# reads the registries listed in ~/.config/infra-drift/cred-consumers.list and the rotation state
# beside them and prints JSON. Neither file is opened by this program, and the subcommand writes
# nothing. An infraops checkout that predates the subcommand answers "unknown command" and the
# pass exits 2: land the infraops change and pull that checkout before scheduling this.
#
# WHAT A WRITING PASS DOES, IN TWO PASSES. For a due credential whose standing package
# `rotation-<credential_id>` does not yet carry the rotation, it files the observation, revises
# the package, writes its `occurrence`, takes it to `ready_for_review`, re-pins the hash fixture,
# commits and PUBLISHES (ADR-0033, through bump-proposer's one publishing act) -- and proposes
# NOTHING. It never approves the revision: no approval policy grants this profile, so Devon
# approves it by name, in the packages checkout, and publishes that. The first pass after that
# approval files the observation again (it replays) and proposes the `work` record. It does not
# approve the record either.
#
# IT SHARES bump-proposer's PACKAGES CHECKOUT, AND ITS VARIABLE. Both lanes commit to the same
# intent-packages checkout and each refuses to begin on a dirty tree or an unpublished commit, so
# neither can build on the other's unfinished work.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  nothing arose that needs a person for an anomalous reason. NOT "the pass did nothing": a
#      pass that revised and published a package, one that proposed a record, and one that found
#      a revision still awaiting Devon's approval all exit 0. Read the lines.
#   1  the tool itself failed (a missing or unreadable credential, or no venv).
#   2  the tool ran but could not use its inputs: infraops could not say what is due (no checkout,
#      no node, an infraops without `cred-findings`, an unparseable registry, an answer this
#      program cannot read), the packages checkout is dirty or carries an unpublished commit,
#      a standing package is misnamed, or a service refused this program's identity.
#   3  something was found -- a rotation revision `stacked` on one still awaiting review, a
#      package in a state this lane never writes, a check infraops reported that this program
#      cannot read, a cause that could not be filed (nothing revised), a refused proposal, a
#      record stranded by a newer revision, or a revision committed and not published.
#
# A WRITING PASS TELLS A PERSON through alert-hub (`rotation_proposer/alerts.py`): each finding, and
# an approval wait older than 48 hours. A failed send prints to stderr and never changes the exit
# code; a pass that exits 2 sends nothing, so every alert stays as it was.
#
# BARE INVOCATION IS A DRY RUN, and it needs no credential at all: it reads infraops and the
# checkout and writes nothing and sends nothing. `--submit` is never supplied by this wrapper.
#
# TWO HOSTS, ONE LAUNCHER (ADR-0059 increment 4, step 3f-5). A set `BWS_ACCESS_TOKEN` means the
# lane's container, and the block after the `--submit` test runs that pass and exits. There:
#   - `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` come from the compose alone; either
#     unset or empty, or its file (`cred-consumers.list`, `cred-rotation-state.json`) missing,
#     exits 2 before anything is read or run. Where those files live is decided outside this
#     launcher (ADR-0059 decision 5).
#   - `--submit` only, it reads the change-manager PROPOSE bearer (`acccb346-…`), the orchestrator
#     OBSERVER bearer (`f793576f-…`), the `sds-bridge` alert sender (`b415d03b-…`) and the "SDS
#     lanes" App's key (`b8f547b9-…`, piped straight into its mint), each with the lane's one token.
#     A dry run reads no secret.
#   - It clones intent-packages and infraops-mcp-server anonymously, shallow at `main`, into a
#     directory the pass discards, and on `--submit` hands git the App's push token with its `[bot]`
#     identity. No program variable holds the push token; only git's extraheader does.
#   - The lifecycle runs under the image's interpreter with the clone's `src` on `PYTHONPATH`. No
#     chain setting reaches the program: this lane holds no chain credential (D-43 B).
#   - It unsets `BWS_ACCESS_TOKEN` before git or any program runs, and reads no Keychain item.
# Exit 1 there also covers a failed mint or clone; exit 2 also covers the fence.
#
# Usage:
#   scripts/run-rotation-proposer.sh              # dry run: reports, writes nothing
#   scripts/run-rotation-proposer.sh --submit     # observes, revises to review, proposes
set -uo pipefail

# The PROPOSE-scoped change-manager bearer, shared with the bump and deploy producers. Never the
# full one: this program must not be able to approve the record it writes.
CHANGE_MANAGER_PROPOSE_UUID="${ROTATION_PROPOSER_BWS_UUID:-acccb346-4baa-43ec-a1d4-b4a400c048ee}"

# The orchestrator's OBSERVER bearer, whose entire write surface is `POST /api/v1/observations`.
OBSERVER_BEARER_UUID="${ROTATION_PROPOSER_OBSERVER_BWS_UUID:-f793576f-e9aa-4f9d-8089-b4a000b9e2d5}"

# The `sds-bridge` sender's alert-hub token (rotation Part 1), BWS `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE`
# in `SDS Operator`, listed in `.bws-secrets.toml`. Read only by a writing pass, with the narrow
# identity: a dry run sends nothing, so it needs no token.
SDS_BRIDGE_ALERT_UUID="${ROTATION_PROPOSER_ALERT_BWS_UUID:-b415d03b-f92d-46b1-8a9d-b4f10103df49}"

# The "SDS lanes" App's private key and id (ADR-0059 decision 2, Q1), `alobar-sds-lanes`, whose
# installation on intent-packages holds `contents: write`. Read in the container on `--submit` only,
# piped straight into `sds-github-mint`; the token it mints reaches git alone.
PUSH_APP_KEY_UUID="b8f547b9-67d7-4417-adc0-b4fd0126b0f0"
PUSH_APP_ID="5684778"
# The App's `[bot]` identity, which authors and commits each revision the container publishes:
# GitHub's noreply address for the bot account, `<bot user id>+<slug>[bot]@users.noreply.github.com`,
# with the id `run-bump-proposer.sh` names (`GET /users/alobar-sds-lanes%5Bbot%5D`).
PUSH_APP_BOT_NAME="alobar-sds-lanes[bot]"
PUSH_APP_BOT_EMAIL="354576843+alobar-sds-lanes[bot]@users.noreply.github.com"

# NO READ-SCOPED BEARER, unlike run-bump-proposer.sh. That lane's dry run reads change-manager's
# landing policy; this one reads no service at all, so fetching a credential for it would be a
# credential nothing uses.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# RUN FROM THE MAIN TREE, NEVER FROM A BUILD WORKTREE. This pass commits to and publishes from the
# packages checkout, and a scheduled lane runs the main tree's working copy and `.venv` by absolute
# path; a pass started from a worktree runs code that was never merged against a checkout every
# lane shares.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-install.sh"
sds_refuse_linked_worktree "$REPO_ROOT"

export BUMP_PROPOSER_PACKAGES_CHECKOUT="${BUMP_PROPOSER_PACKAGES_CHECKOUT:-$HOME/Projects/intent-packages}"
export ROTATION_PROPOSER_INFRAOPS_CHECKOUT="${ROTATION_PROPOSER_INFRAOPS_CHECKOUT:-$HOME/Projects/infraops-mcp-server}"

# The dead-man switch, armed before any credential fetch so that a fetch failure is reported too.
# It reports and never gates; until a Healthchecks check named sds-rotation-proposer exists it
# logs that alerting is disabled and the pass goes on.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-rotation-proposer --finding 3 "$@"

# The one BWS reader, with the colour guard and the venv's own interpreter. Sourced on every run
# and called only by a writing one.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

case " $* " in
  *" --submit "*) NEEDS_CREDENTIAL=1 ;;
  *) NEEDS_CREDENTIAL=0 ;;
esac

# ---- THE LANE'S CONTAINER (ADR-0059 increment 4, step 3f-5) -----------------------------------
# A set `BWS_ACCESS_TOKEN` means the lane's container: `lane-run` passes the lane's own token as its
# one secret, and the Mac never sets it (its identities are Keychain items, read below). This block
# runs the whole pass and exits, so everything after it is the Mac's launchd job, unchanged until
# increment 6. It skips activation (the container runs the image's own code and clones its
# checkouts fresh each pass), reads no Keychain item, and reads every secret with that one token in
# the environment, never as a process argument.
if [ -n "${BWS_ACCESS_TOKEN:-}" ]; then
  # THE FENCE: WHERE infraops' TWO FILES LIVE IS NOT THIS LAUNCHER'S TO CHOOSE (ADR-0059 decision 5,
  # "Verify in increment 4"). `cred-findings` reads `cred-consumers.list` from
  # `INFRADRIFT_CONFIG_DIR` and `cred-rotation-state.json` from `SECURITY_DRIFT_STATE_DIR`
  # (infraops `src/security-drift/paths.ts`), and falls back to `~/.config/infra-drift` when either is
  # unset. Both come from the compose alone; there is no default and no home-directory fallback.
  # An empty value is refused too: infraops' `??` keeps an empty string, which would resolve both
  # files against the working directory. Checked first, before any read, clone or program, so a
  # pass the fence stops reads nothing and exits 2 (could not use its inputs), saying why.
  for _setting in INFRADRIFT_CONFIG_DIR SECURITY_DRIFT_STATE_DIR; do
    if [ -z "${!_setting:-}" ]; then
      echo "FATAL: $_setting is not set in the lane's container: infraops can't be asked what is due" >&2
      exit 2
    fi
  done
  if [ ! -f "$INFRADRIFT_CONFIG_DIR/cred-consumers.list" ]; then
    echo "FATAL: no cred-consumers.list in INFRADRIFT_CONFIG_DIR ($INFRADRIFT_CONFIG_DIR): infraops can't be asked what is due" >&2
    exit 2
  fi
  if [ ! -f "$SECURITY_DRIFT_STATE_DIR/cred-rotation-state.json" ]; then
    echo "FATAL: no cred-rotation-state.json in SECURITY_DRIFT_STATE_DIR ($SECURITY_DRIFT_STATE_DIR): infraops can't be asked what is due" >&2
    exit 2
  fi
  export INFRADRIFT_CONFIG_DIR SECURITY_DRIFT_STATE_DIR

  # NO CHAIN SETTING (D-43 B; ADR-0055 amendment 24 item 5). This lane holds no chain credential:
  # its `revise` and `transition` emits fail and record `event_id` None in the lineage. Nothing
  # chain-shaped reaches the program, whatever the environment carries.
  unset FACTORY_EVENTS_APPEND_PASSWORD FACTORY_EVENTS_READ_PASSWORD FACTORY_EVENTS_DB_HOST \
    FACTORY_EVENTS_DB_PORT FACTORY_EVENTS_DB_NAME FACTORY_EVENTS_APPEND_DSN \
    FACTORY_EVENTS_READ_DSN FACTORY_EVENTS_IMPORT_DSN

  # The compose sets it to the lane's volume (decision 1, "Lane state").
  if [ -z "${ROTATION_PROPOSER_STATE_DIR:-}" ]; then
    echo "FATAL: ROTATION_PROPOSER_STATE_DIR is not set in the lane's container" >&2
    exit 1
  fi
  export ROTATION_PROPOSER_STATE_DIR
  if ! mkdir -p "$ROTATION_PROPOSER_STATE_DIR"; then
    echo "FATAL: could not create the alert state directory $ROTATION_PROPOSER_STATE_DIR" >&2
    exit 1
  fi

  # THE SECRETS, `--submit` only, as on the Mac: a dry run reads infraops and the checkout, writes
  # nothing and sends nothing, so it needs none. Each is read with the lane's one token.
  unset _PUSH_TOKEN
  if [ "$NEEDS_CREDENTIAL" -eq 1 ]; then
    ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_PROPOSE_UUID")"
    if [ -z "$ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN" ]; then
      echo "FATAL: could not read the propose-scoped change-manager credential from BWS (the lane's token)" >&2
      exit 1
    fi
    export ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN
    ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
    ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
    ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID")"
    if [ -z "$ORCHESTRATOR_API_TOKEN" ]; then
      echo "FATAL: could not read the orchestrator observer bearer from BWS (the lane's token)" >&2
      exit 1
    fi
    export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN
    SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID")"
    if [ -z "$SDS_BRIDGE_ALERT_TOKEN" ]; then
      echo "FATAL: could not read the sds-bridge alert sender token from BWS (the lane's token)" >&2
      exit 1
    fi
    export SDS_BRIDGE_ALERT_TOKEN

    # THE PUSH TOKEN, for intent-packages alone, with `contents: write` and nothing else beside
    # `metadata`. The "SDS lanes" App's key goes from the BWS read straight to the mint's stdin in
    # its stored form, base64 of the PEM; it is never in a variable, an argument or a file, and the
    # mint runs without the lane's token. The token is held in this script's own unexported
    # variable until git's credential takes it, after the clones.
    _PUSH_TOKEN="$(sds_bws_value "$PUSH_APP_KEY_UUID" \
      | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
          --app-id "$PUSH_APP_ID" --repository AlobarQuest/intent-packages \
          --permission metadata=read --permission contents=write --allow-write)"
    push_rc=$?
    if [ "$push_rc" -ne 0 ] || [ -z "$_PUSH_TOKEN" ]; then
      echo "FATAL: could not mint the rotation lane's push token from the SDS lanes App (sds-github-mint exited $push_rc)" >&2
      exit 1
    fi
  fi

  # EVERY BWS READ IS DONE (the dead-man switch read its ping URL when it armed), and nothing after
  # this needs a BWS identity, git and the programs included, so the lane's token reaches none.
  unset BWS_ACCESS_TOKEN

  # THE TWO CLONES, shallow, at `main`, anonymous, into a directory the pass discards (decision 1).
  # Both repositories are public, so git gets no credential for them: no global or system config,
  # no prompt, no trace, and they are made BEFORE the push credential exists, so its header never
  # rides either fetch. `ROTATION_PROPOSER_CLONE_ROOT` is a test seam: `lane-run` never passes it.
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0
  unset GIT_TRACE GIT_TRACE_CURL GIT_TRACE_PACKET GIT_CURL_VERBOSE GIT_CONFIG_COUNT
  _CLONE_ROOT="${ROTATION_PROPOSER_CLONE_ROOT:-/var/tmp/sds-rotation-proposer}"
  PACKAGES_CLONE="$_CLONE_ROOT/intent-packages"
  INFRAOPS_CLONE="$_CLONE_ROOT/infraops-mcp-server"
  # The registry mirrors (`RepoMirror`, `rotation_worker/git_mirror.py`) live under
  # `$XDG_CACHE_HOME/rotation-proposer/mirrors`; pointing it here makes them the pass's too.
  MIRROR_CACHE="$_CLONE_ROOT/cache"
  if ! { mkdir -p "$_CLONE_ROOT" && chmod 0700 "$_CLONE_ROOT"; }; then
    echo "FATAL: could not create the clone directory $_CLONE_ROOT" >&2
    exit 1
  fi
  if ! rm -rf -- "$PACKAGES_CLONE" "$INFRAOPS_CLONE" "$MIRROR_CACHE"; then
    echo "FATAL: could not discard the previous pass's clones under $_CLONE_ROOT" >&2
    exit 1
  fi
  for _remote in intent-packages infraops-mcp-server; do
    # git's own message is kept for the log; it holds no credential (none is configured).
    if ! _clone_out="$(git clone --quiet --depth 1 --single-branch --branch main \
        "https://github.com/AlobarQuest/$_remote.git" "$_CLONE_ROOT/$_remote" 2>&1)"; then
      echo "FATAL: could not clone https://github.com/AlobarQuest/$_remote.git into $_CLONE_ROOT/$_remote: $_clone_out" >&2
      exit 1
    fi
  done
  export BUMP_PROPOSER_PACKAGES_CHECKOUT="$PACKAGES_CLONE"
  export ROTATION_PROPOSER_INFRAOPS_CHECKOUT="$INFRAOPS_CLONE"

  # ON `--submit`, GIT PUSHES AS THE "SDS LANES" APP, and its commits are the App's: the push
  # token's header and the identity pairs author and commit each revision. `standing._run` passes
  # no `env`, so `standing.publish`'s push inherits them.
  if [ "$NEEDS_CREDENTIAL" -eq 1 ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/sds-git.sh"
    if ! sds_git_credential _PUSH_TOKEN "$PUSH_APP_BOT_NAME" "$PUSH_APP_BOT_EMAIL"; then
      echo "FATAL: could not give git the rotation lane's push credential" >&2
      exit 1
    fi
    unset _PUSH_TOKEN
  fi

  if [ ! -x "$REPO_ROOT/.venv/bin/rotation-proposer" ]; then
    echo "FATAL: no rotation-proposer in $REPO_ROOT/.venv" >&2
    exit 1
  fi
  # THE LIFECYCLE'S INTERPRETER: intent-packages' `create-rotation`, `revise`, `transition` and
  # `hash` run under the image's interpreter with the clone's `src` on `PYTHONPATH`, as
  # `run-bump-proposer.sh` runs them, rather than a venv built in the clone. `SECURITY_STANDARDS_DIR`
  # is the image's security-standards (its registry and `src`, at the pin), which intent-packages
  # validates capabilities against and its emitter locates `factory_events` by. Each reaches
  # `rotation-proposer` alone, as an assignment prefix.
  BUMP_PROPOSER_PACKAGES_PYTHON="$REPO_ROOT/.venv/bin/python" \
    PYTHONPATH="$PACKAGES_CLONE/src" \
    SECURITY_STANDARDS_DIR="$REPO_ROOT/security-standards" \
    XDG_CACHE_HOME="$MIRROR_CACHE" \
    "$REPO_ROOT/.venv/bin/rotation-proposer" "$@"
  rc=$?
  # The clones hold no credential (the header lives only in the environment), so a failed removal
  # is logged and doesn't change the pass's code; the next pass removes them before it clones.
  rm -rf -- "$PACKAGES_CLONE" "$INFRAOPS_CLONE" "$MIRROR_CACHE" \
    || echo "[clone] could not discard the clones under $_CLONE_ROOT; the next pass removes them" >&2
  exit "$rc"
fi

# THE PACKAGES CHECKOUT IS ACTIVATED BEFORE THE PASS READS IT, on a `--submit` run only (ADR-0031
# amendment 1), for the reason `run-bump-proposer.sh` gives: a revision committed on a behind
# `main` can't be published, and every later pass refuses it until a person reconciles. After the
# worktree refusal, so a pass started from a build worktree never moves the shared checkout; after
# the switch is armed, which is safe because one argument never re-execs (this script doesn't live
# there); before any bearer is fetched, because the helper may run `uv sync` in that checkout. A
# dry run writes nothing, so it leaves the checkout alone.
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
if [ "$NEEDS_CREDENTIAL" -eq 1 ]; then
  activate_checkout "$BUMP_PROPOSER_PACKAGES_CHECKOUT"
else
  echo "[activation] dry run: leaving ${BUMP_PROPOSER_PACKAGES_CHECKOUT/#$HOME/~} as it is"
fi

if [ "$NEEDS_CREDENTIAL" -eq 1 ]; then
  # TWO BWS IDENTITIES, each read from its own Keychain item into its own variable and passed to
  # the one fetch that needs it, so neither is ever ambient (docs/operations/credentials.md).
  BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
  if [ -z "$BROAD_IDENTITY" ]; then
    echo "FATAL: no broad BWS identity for the change-manager credential (service Claude)" >&2
    exit 1
  fi
  ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_PROPOSE_UUID" "$BROAD_IDENTITY")"
  export ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN
  if [ -z "${ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN:-}" ]; then
    echo "FATAL: could not read the propose-scoped change-manager credential from BWS (broad)" >&2
    exit 1
  fi

  SDS_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
  if [ -z "$SDS_IDENTITY" ]; then
    echo "FATAL: no narrow BWS identity for the observer bearer (service Claude)" >&2
    exit 1
  fi
  ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
  ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
  ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID" "$SDS_IDENTITY")"
  export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN
  if [ -z "${ORCHESTRATOR_API_TOKEN:-}" ]; then
    echo "FATAL: could not read the orchestrator observer bearer from BWS (narrow)" >&2
    exit 1
  fi

  SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID" "$SDS_IDENTITY")"
  export SDS_BRIDGE_ALERT_TOKEN
  if [ -z "${SDS_BRIDGE_ALERT_TOKEN:-}" ]; then
    echo "FATAL: could not read the sds-bridge alert sender token from BWS (narrow)" >&2
    exit 1
  fi
  unset BROAD_IDENTITY SDS_IDENTITY
fi

if [ ! -x "$REPO_ROOT/.venv/bin/rotation-proposer" ]; then
  echo "FATAL: no rotation-proposer in $REPO_ROOT/.venv (run uv sync)" >&2
  exit 1
fi
"$REPO_ROOT/.venv/bin/rotation-proposer" "$@"
