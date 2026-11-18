#!/usr/bin/env bash
# The credential registries infraops reads, cloned and listed in the rotation proposer's lane
# container (ADR-0059 decision 5; Amendment 12 decision 1). `run-rotation-proposer.sh` sources it
# inside its container block only, and a person sources it for an infraops command between passes.
# The Mac reads its own hand-kept `~/.config/infra-drift/cred-consumers.list` and never this file.
#
# Source this (don't execute it):
#     source "$REPO_ROOT/scripts/sds-cred-registries.sh"
#
# Then:
#     sds_anonymous_clone <repository> <directory>   # one public AlobarQuest repository, at main
#     sds_cred_registries <clone-root> <config-dir>  # the four registries, and their list
#     sds_infraops_session <subcommand> [args...]    # a person's infraops command (deploy.md)
#
# THE LIST NAMES FOUR REGISTRIES, the same four as the Mac's: the `.cred-consumers.toml` of
# infraops-mcp-server, change-manager, factory-runner and orchestrator, each at `main`. infraops'
# exposure scan (Infra Brain rule 1032, `cred.exposure-rotate`) reads exposures from the registries
# the list names, so a shorter list hides every exposure declared in the one it drops. A registry
# file missing from its clone therefore stops the pass (return 2) instead of being left out.
#
# THE CLONES ARE ANONYMOUS. Each repository is public, and git gets no credential: no global or
# system config, no prompt, no trace, no `GIT_CONFIG_COUNT` pairs, and no lane token in its
# environment (the token is still set while the container block gathers its inputs).
#
# Returns, as the launchers' exit codes read: 0 when done; 1 when a clone or a write failed (the
# tool itself failed); 2 when a clone lacks its registry file (the inputs can't be used). Each
# failure prints one `FATAL:` line on stderr. Nothing here reads or prints a secret.

SDS_CRED_REGISTRIES=(infraops-mcp-server change-manager factory-runner orchestrator)

sds_anonymous_clone() {
  local repository="$1" directory="$2" out
  # git's own message is kept for the log; it holds no credential (none is configured).
  if ! out="$(env -u BWS_ACCESS_TOKEN -u GIT_CONFIG_COUNT -u GIT_TRACE -u GIT_TRACE_CURL \
      -u GIT_TRACE_PACKET -u GIT_CURL_VERBOSE \
      GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
      git clone --quiet --depth 1 --single-branch --branch main \
      "https://github.com/AlobarQuest/$repository.git" "$directory" 2>&1)"; then
    echo "FATAL: could not clone https://github.com/AlobarQuest/$repository.git into $directory: $out" >&2
    return 1
  fi
}

# Clones each registry's repository into `<clone-root>/<repository>` and writes
# `<config-dir>/cred-consumers.list`, one absolute path per line, only once all four are present.
sds_cred_registries() {
  local root="$1" config="$2" repository registry list=""
  for repository in "${SDS_CRED_REGISTRIES[@]}"; do
    sds_anonymous_clone "$repository" "$root/$repository" || return 1
  done
  for repository in "${SDS_CRED_REGISTRIES[@]}"; do
    registry="$root/$repository/.cred-consumers.toml"
    if [ ! -f "$registry" ] || [ ! -r "$registry" ]; then
      echo "FATAL: no readable .cred-consumers.toml at main in AlobarQuest/$repository ($registry): infraops can't be asked what is due" >&2
      return 2
    fi
    list+="$registry"$'\n'
  done
  if ! { mkdir -p "$config" && printf '%s' "$list" > "$config/cred-consumers.list"; }; then
    echo "FATAL: could not write cred-consumers.list in $config" >&2
    return 1
  fi
}

# A person's infraops command between passes (`resolve-exposure`, `record-rotation`), run inside the
# lane's container as the lane's user (docs/operations/deploy.md, "A person's infraops command").
# It makes its own clones and list in a fresh directory, never the pass's, which a pass deletes at
# its start, and discards them after. `SECURITY_DRIFT_STATE_DIR` is the compose's, which the exec
# session inherits. Exits with infraops' own code, or 1 and 2 as above.
sds_infraops_session() (
  unset BWS_ACCESS_TOKEN
  # infraops refuses a state file not owned by the reading uid, and its write leaves the file owned
  # by the writer: root's write would make every later pass refuse the file.
  if [ "$(id -u)" -eq 0 ]; then
    echo "FATAL: run this as the lane's user (docker exec -u orchestrator), not root: a root write leaves cred-rotation-state.json unreadable to every pass" >&2
    exit 1
  fi
  if [ -z "${SECURITY_DRIFT_STATE_DIR:-}" ]; then
    echo "FATAL: SECURITY_DRIFT_STATE_DIR is not set: run this in the rotation proposer's lane container" >&2
    exit 2
  fi
  # infraops would start a new, empty state file in its place; refuse instead.
  if [ ! -f "$SECURITY_DRIFT_STATE_DIR/cred-rotation-state.json" ]; then
    echo "FATAL: no cred-rotation-state.json in SECURITY_DRIFT_STATE_DIR ($SECURITY_DRIFT_STATE_DIR)" >&2
    exit 2
  fi
  if ! root="$(mktemp -d "${TMPDIR:-/var/tmp}/sds-infraops-session.XXXXXX")"; then
    echo "FATAL: could not create a directory for this session's clones" >&2
    exit 1
  fi
  trap 'rm -rf -- "$root"' EXIT
  sds_cred_registries "$root" "$root/infra-drift" || exit $?
  INFRADRIFT_CONFIG_DIR="$root/infra-drift" \
    node "$root/infraops-mcp-server/dist/cli/security-drift-cli.js" "$@"
)
