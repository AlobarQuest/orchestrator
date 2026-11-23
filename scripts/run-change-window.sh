#!/usr/bin/env bash
# One change-window pass in the `change-window` lane (ADR-0059 row 822, increment 8): execute the
# change requests change-manager holds as approved, for production Coolify only, with infraops'
# own executor, then send the window's digest.
#
# THE EXECUTOR IS infraops', NOT THIS PROGRAM'S. Each pass runs infraops-mcp-server's bundled CLI,
# `dist/bundle/change-mgr-cli.js run-window` (infraops #171), from the copy the lane image bakes in
# at a pinned commit, checked against its sha256 (`lanes/Dockerfile`, `INFRAOPS_COMMIT`; ADR-0059
# Amendment 18, D-70 A and D-66 B): `$REPO_ROOT/infraops/change-mgr-cli.js`, read-only. It's one
# esbuild file with every dependency bundled and Node's built-ins external, so nothing is installed,
# and a pass clones nothing. It lists change-manager's approved items, drops security and rotation
# items, keeps the prod ones (`--instance prod`, D-67 C: the lane never reaches dev Coolify, and a
# person handles dev items), caps them at `MAX_CHANGES_PER_WINDOW` (5), and claims, executes and
# posts an outcome for each. Selection is infraops' `selectItems`, which its dry run and its real
# run share.
#
# BARE INVOCATION IS A DRY RUN; `--submit` ACTS, as in the other acting lanes:
#   bare      `run-window --dry-run --instance prod`. It lists the approved items and prints the
#             ids it would claim as one JSON line. It opens no window, claims nothing, posts
#             nothing and builds no Anthropic client, so it reads the change-manager bearer only.
#             It pings nothing: the check is the window's, and a dry run is no window.
#   --submit  `run-window --instance prod --report-dir <pass directory> --now <UTC time>`, then the
#             digest. It reads all four secrets below.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  the pass ran: the window ran (or the dry run listed its ids), whatever each item's outcome.
#   1  the tool itself failed: an argument this launcher doesn't take, no lane socket or token, a
#      secret that couldn't be read or is empty, or `run-window` failing (change-manager refused or
#      couldn't be reached, a bad flag, an unhandled error). Any other code `run-window` exits with
#      is reported as 1.
#   2  the tool ran but could not use its inputs: the image holds no baked
#      `infraops/change-mgr-cli.js`, or there is no `node`.
#
# THIS CHECK IS AN OUTCOME ALARM (ADR-0021 amendment 2), and it is the window's existing check,
# `Change Manager OPs Check` (slug `operations-check`; change-manager's `.cred-consumers.toml`): the
# phase keeps the same checks. A `--submit` pass pings `/start`, then success on 0 or `/fail` with
# the report on anything else. In the container the ping URL comes from the lane's ping-URL secret,
# whose BWS id the compose names in `SDS_DEADMAN_PING_URL_UUID`.
#
# THE DIGEST is the run's `<date>.change-window.md`, sent to alert-hub as an `info` NOTICE at once
# (`docs/operations/deploy.md`'s change-window paragraphs, under "Registered lanes and the
# lane-token matcher", say why a notice and not a held alert).
# A failed send logs one line and never changes the exit code.
#
# CREDENTIALS, four secrets in BWS `Rotation / Keepers`, each read through the lane secrets broker
# (ADR-0055 amendment 30), which serves this lane only the UUIDs its allowlist lists
# (`lanes/secrets-broker/allowlist.toml`): these four and the ping-URL secret, whose id reaches
# this launcher from the compose through `lane-run`. Each value arrives on the socket, never as a
# process argument. The lane has no BWS token of its own.
#
# THE CONTAINER ONLY. The Mac job stays infraops' `scripts/change-window.sh` until rollback ends, so
# there is no Keychain branch and no activation: `lane-run` names the broker's mounted socket in
# `SDS_SECRETS_SOCKET` (or passes a lane's own token as `BWS_ACCESS_TOKEN`). Outside the lane's
# container (`sds_in_lane_container` false) the launcher refuses before it resolves a ping URL or
# calls anything. Both are unset before `node` runs, which needs neither. The socket itself stays
# reachable, as the design accepts: `node` runs as the same user.
#
# Usage (in the lane's container, through `lane-run`):
#   lane-run change-window              # dry run: lists the ids it would claim
#   lane-run change-window --submit     # runs the window and sends the digest
set -uo pipefail

CHECK_NAME="Change Manager OPs Check"

# BWS UUIDs. The `${...:-}` override is a test seam, and `lane-run` never passes one.
# The dedicated Coolify window token (D-68 B), sent only over the tailnet:
# `change-window-coolify-token`.
COOLIFY_WINDOW_TOKEN_UUID="${CHANGE_WINDOW_COOLIFY_UUID:-3d41395a-06c3-47d5-ac85-b50a00dfbc2f}"
# change-manager's narrow `execute` bearer (#168, `M2M_TOKEN_EXECUTE`): READ plus claim, outcome
# and the window's own run record, nothing else.
CHANGE_MANAGER_EXECUTE_UUID="${CHANGE_WINDOW_CHANGE_MANAGER_UUID:-aa7c76af-bd52-4796-9d13-b50a00e035de}"
# The Anthropic API key the change agent plans each item with: `change-window-anthropic-key`.
ANTHROPIC_KEY_UUID="${CHANGE_WINDOW_ANTHROPIC_UUID:-0e9ebf2d-5b62-4f81-a731-b50a00e0df18}"
# The alert-hub sender token the digest is sent with:
# `ALERT_HUB_SENDER_TOKEN_REPORT_CHANGE_WINDOW`.
DIGEST_SENDER_UUID="${CHANGE_WINDOW_DIGEST_SENDER_UUID:-71616bf5-14ba-4888-b3f8-b50a00e13ed2}"

# Production Coolify over the tailnet (HQ G-73: the lane container reaches it), as estate landing
# reaches it; change-manager and alert-hub over HTTPS.
COOLIFY_URL="http://100.92.138.19:8000"
CHANGE_MANAGER_URL="https://change-mgr.alobar.net"
ALERT_HUB_URL="https://alerts.alobar.net/v1/alerts"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SUBMITTING=0
case "$*" in
  "") ;;
  --submit) SUBMITTING=1 ;;
  *)
    echo "FATAL: run-change-window.sh takes no argument or --submit alone" >&2
    exit 1
    ;;
esac
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
if ! sds_in_lane_container; then
  echo "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN: the change-window lane runs only in its container, through lane-run" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_outcome_begin "$CHECK_NAME" change-window "$SUBMITTING"

# THE PASS DIRECTORY holds the window's report and the digest's request body. It holds no
# credential and is removed on exit, with the outcome report.
PASS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/change-window.XXXXXX")" \
  || sds_outcome_fail "FATAL: could not create the pass directory"
trap 'rm -f "$SDS_OUTCOME_REPORT"; rm -rf -- "$PASS_DIR"' EXIT

# THE INPUT, checked before any secret is read: the bundle the image bakes at its pinned commit.
# The path is derived from the launcher's own location, never from the environment, and it keeps
# the bundle's name, which the bundle needs to run at all (`lanes/Dockerfile`).
BUNDLE="$REPO_ROOT/infraops/change-mgr-cli.js"
[ -f "$BUNDLE" ] || sds_outcome_fail \
  "FATAL: the lane image has no infraops/change-mgr-cli.js (lanes/Dockerfile's pinned bundle): the window can't run" 2
command -v node >/dev/null 2>&1 || sds_outcome_fail "FATAL: no node on PATH: the window can't run" 2

# THE SECRETS this mode needs, each through `sds_bws_value`'s one-argument form: the broker's
# socket, or the lane's token from the environment. An unreadable or empty value fails closed here,
# before any call.
CHANGE_MGR_M2M_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_EXECUTE_UUID")"
[ -n "$CHANGE_MGR_M2M_TOKEN" ] || sds_outcome_fail \
  "FATAL: could not read the change-manager execute bearer from BWS (the lane's socket or token), or it is empty"
CHANGE_MGR_API_BASE="$CHANGE_MANAGER_URL"
export CHANGE_MGR_API_BASE CHANGE_MGR_M2M_TOKEN
if [ "$SUBMITTING" -eq 1 ]; then
  COOLIFY_API_TOKEN="$(sds_bws_value "$COOLIFY_WINDOW_TOKEN_UUID")"
  [ -n "$COOLIFY_API_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the Coolify window token from BWS (the lane's socket or token), or it is empty"
  ANTHROPIC_API_KEY="$(sds_bws_value "$ANTHROPIC_KEY_UUID")"
  [ -n "$ANTHROPIC_API_KEY" ] || sds_outcome_fail \
    "FATAL: could not read the Anthropic API key from BWS (the lane's socket or token), or it is empty"
  # Never exported: the window's program doesn't need it, and only the digest's curl reads it.
  _DIGEST_TOKEN="$(sds_bws_value "$DIGEST_SENDER_UUID")"
  [ -n "$_DIGEST_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the digest's alert-hub sender token from BWS (the lane's socket or token), or it is empty"
  # The token becomes one quoted line of curl's config, so it may hold no quote, backslash or
  # whitespace, as `sds_deadman_resolve_by_uuid` holds the ping URL.
  case "$_DIGEST_TOKEN" in
    *[[:space:]\"\\]*) sds_outcome_fail \
      "FATAL: the digest's alert-hub sender token holds a character curl's config can't carry" ;;
  esac
  COOLIFY_BASE_URL="$COOLIFY_URL"
  export COOLIFY_BASE_URL COOLIFY_API_TOKEN ANTHROPIC_API_KEY
fi
# PROD ONLY (D-67 C). infraops' Coolify client reads `COOLIFY_PROD_*` before `COOLIFY_BASE_URL`
# and `COOLIFY_API_TOKEN`, and `COOLIFY_DEV_*` for a dev item, so none of the four reaches it.
unset COOLIFY_DEV_BASE_URL COOLIFY_DEV_API_TOKEN COOLIFY_PROD_BASE_URL COOLIFY_PROD_API_TOKEN
# Every secret read is done, and nothing after this needs a BWS identity: neither the socket's
# marker nor a lane token reaches `node`, and the digest's curl gets only its own token.
unset BWS_ACCESS_TOKEN SDS_SECRETS_SOCKET

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DATE="${NOW%%T*}"
REPORT_DIR="$PASS_DIR/reports"
if [ "$SUBMITTING" -eq 1 ]; then
  mkdir -p "$REPORT_DIR" || sds_outcome_fail "FATAL: could not create the report directory"
  node "$BUNDLE" run-window --instance prod --report-dir "$REPORT_DIR" --now "$NOW" \
    > "$SDS_OUTCOME_REPORT" 2>&1
else
  node "$BUNDLE" run-window --dry-run --instance prod > "$SDS_OUTCOME_REPORT" 2>&1
fi
rc=$?
case "$rc" in
  0|1) ;;
  *) echo "FATAL: run-window exited $rc" >> "$SDS_OUTCOME_REPORT"; rc=1 ;;
esac

# THE DIGEST, `--submit` only, as an `info` notice with the Mac's key and subject. It is
# BEST-EFFORT, as on the Mac: the window has already run and recorded each outcome in
# change-manager, so a digest that can't be sent is a lost message about a finished act, and
# failing the pass for it would report the window itself as failed. Each failure logs one line and
# leaves `rc` alone. Neither the token nor the request body is logged.
send_digest() {
  local digest="$REPORT_DIR/$DATE.change-window.md" request="$PASS_DIR/digest.json" status
  if [ ! -f "$digest" ]; then
    echo "[change-window] the window wrote no digest; none sent"
    return 0
  fi
  # The hub caps a notice's message at 12,000 characters (UTF-16 units, as it counts) and a request
  # body at 16,384 bytes; a longer digest is cut, never split inside a character, with a footer
  # naming where the whole one is.
  if ! "$REPO_ROOT/.venv/bin/python" -I - "$digest" "$request" "$DATE" <<'PY'
import json, sys
digest, out, date = sys.argv[1], sys.argv[2], sys.argv[3]
limit, byte_limit = 12000, 15000
text = open(digest, encoding="utf-8").read()
units = lambda s: len(s.encode("utf-16-le")) // 2
size = lambda s: len(json.dumps(s, ensure_ascii=False).encode("utf-8"))
if units(text) > limit or size(text) > byte_limit:
    footer = (f"\n\n… (cut at {limit:,} of {units(text):,} characters; the whole report is the"
              " window run's report_md in change-manager)")
    room_units, room_bytes = limit - units(footer), byte_limit - size(footer)
    used_units, used_bytes, end = 0, 0, 0
    for ch in text:
        cost_units, cost_bytes = units(ch), size(ch) - 2
        if used_units + cost_units > room_units or used_bytes + cost_bytes > room_bytes:
            break
        used_units, used_bytes, end = used_units + cost_units, used_bytes + cost_bytes, end + 1
    text = text[:end] + footer
payload = {"key": f"report:change-window:{date}", "severity": "info", "kind": "notice",
           "title": f"Change window {date}", "message": text}
with open(out, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
PY
  then
    echo "[change-window] could not build the digest's request; not sent (the pass's exit is unchanged)"
    return 0
  fi
  # The token reaches curl on stdin as its config, never as an argument. curl follows no redirect,
  # so the token can't reach another host.
  status="$(printf 'header = "Authorization: Bearer %s"\n' "$_DIGEST_TOKEN" \
    | curl -sS --max-time 10 --config - -o /dev/null -w '%{http_code}' -X POST "$ALERT_HUB_URL" \
        -H 'Content-Type: application/json' --data-binary "@$request" 2>/dev/null)"
  if [ "$status" = "202" ]; then
    echo "[change-window] digest sent to alert-hub (report:change-window:$DATE)"
  else
    echo "[change-window] digest not sent: alert-hub answered '${status:-nothing}' (the pass's exit is unchanged)"
  fi
  return 0
}
if [ "$SUBMITTING" -eq 1 ]; then
  send_digest >> "$SDS_OUTCOME_REPORT"
  unset _DIGEST_TOKEN
fi
sds_outcome_end "$rc"
