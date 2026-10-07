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
# BARE INVOCATION IS A DRY RUN, and it needs no credential at all: it reads infraops and the
# checkout and writes nothing. `--submit` is never supplied by this wrapper.
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
fi

if [ ! -x "$REPO_ROOT/.venv/bin/rotation-proposer" ]; then
  echo "FATAL: no rotation-proposer in $REPO_ROOT/.venv (run uv sync)" >&2
  exit 1
fi
"$REPO_ROOT/.venv/bin/rotation-proposer" "$@"
