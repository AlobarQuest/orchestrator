#!/usr/bin/env bash
# Store the executor's BWS root token on the executor's host, written to a file only the service
# user can read. The value never becomes an argument, an exported environment variable, or a file
# outside the host (ADR-0055 decision 2). It runs in one of two modes:
#
# - With no argument, it reads the value at a prompt that doesn't echo, so it needs a real
#   terminal on stdin. Installed by provision.sh as /opt/rotation-worker/bin/set-root-token:
#     orb -m rotation-executor -u root /opt/rotation-worker/bin/set-root-token
# - With `--stdin`, it reads exactly one line from a pipe, for a host with no terminal step. A
#   terminal on stdin is refused. One trailing newline (LF or CRLF) is stripped; an empty value,
#   a second line, inner whitespace, or more than 4096 characters is refused:
#     <source> | ssh root@<host> /opt/rotation-worker/bin/set-root-token --stdin
#
# Every refusal leaves the existing file unchanged, and no message names the value or an argument.
set -euo pipefail

target=/etc/rotation-worker/root-token
max_length=4096
mode=prompt
if [[ $# -gt 1 ]]; then
  echo "set-root-token: takes at most one argument, --stdin" >&2
  exit 2
fi
if [[ $# -eq 1 ]]; then
  if [[ "$1" != "--stdin" ]]; then
    echo "set-root-token: unknown argument; the only one is --stdin" >&2
    exit 2
  fi
  mode=stdin
fi
if [[ $EUID -ne 0 ]]; then
  echo "set-root-token: run as root" >&2
  exit 2
fi

refuse() {
  echo "set-root-token: $1; the file is unchanged" >&2
  exit 1
}

if [[ $mode == prompt ]]; then
  if [[ ! -t 0 ]]; then
    echo "set-root-token: needs a terminal to read from" >&2
    exit 2
  fi
  IFS= read -r -s -p "Root access token (input hidden): " token
  echo
  if [[ -z "$token" ]]; then
    echo "set-root-token: nothing entered; the file is unchanged" >&2
    exit 1
  fi
else
  if [[ -t 0 ]]; then
    echo "set-root-token: --stdin reads from a pipe, not a terminal" >&2
    exit 2
  fi
  # read fails at end of input; a final line with no newline is still a value.
  IFS= read -r token || [[ -n "$token" ]] || refuse "nothing on stdin"
  token="${token%$'\r'}"
  [[ -n "$token" ]] || refuse "the line is empty"
  extra=
  if IFS= read -r -n 1 extra || [[ -n "$extra" ]]; then
    refuse "stdin holds more than one line"
  fi
  [[ "$token" != *[[:space:]]* ]] || refuse "the value has whitespace inside"
  [[ ${#token} -le $max_length ]] || refuse "the value is longer than $max_length characters"
fi

umask 077
partial="$(mktemp "$target.XXXXXX")"
printf '%s\n' "$token" > "$partial"
unset token
chown rotation:rotation "$partial"
chmod 0400 "$partial"
mv -f "$partial" "$target"
echo "set-root-token: stored, readable only by the rotation user ($(stat -c '%U %a' "$target"))"
