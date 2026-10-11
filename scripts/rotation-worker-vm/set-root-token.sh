#!/usr/bin/env bash
# Store the executor's BWS root token in the VM: typed or pasted at a prompt that doesn't echo,
# written to a file only the service user can read. The value never becomes an argument, an
# environment variable on the Mac, or a file outside the VM (ADR-0055 decision 2).
#
# Installed by provision.sh as /opt/rotation-worker/bin/set-root-token. From the Mac, in a real
# terminal (it needs a TTY):
#   orb -m rotation-executor -u root /opt/rotation-worker/bin/set-root-token
set -euo pipefail

target=/etc/rotation-worker/root-token
if [[ $EUID -ne 0 ]]; then
  echo "set-root-token: run as root" >&2
  exit 2
fi
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

umask 077
partial="$(mktemp "$target.XXXXXX")"
printf '%s\n' "$token" > "$partial"
unset token
chown rotation:rotation "$partial"
chmod 0400 "$partial"
mv -f "$partial" "$target"
echo "set-root-token: stored, readable only by the rotation user ($(stat -c '%U %a' "$target"))"
