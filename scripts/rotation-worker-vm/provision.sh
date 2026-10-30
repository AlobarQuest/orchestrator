#!/usr/bin/env bash
# Install or update the credential-rotation executor in its isolated VM (ADR-0055 decision 2).
#
# Run as root inside the VM, from the Mac:
#   orb -m rotation-executor -u root bash -s -- <40-char orchestrator commit> \
#     < scripts/rotation-worker-vm/provision.sh
#
# It installs a pinned uv (checksum-verified), checks the orchestrator out at exactly that commit,
# builds the executor's environment from the lockfile (`--no-dev --group rotation-worker`), and
# installs the systemd service and timer. It writes no configuration and no credential: the
# config file (ids and paths only) and the root token are separate steps, so this script is safe
# to re-run for every update. Nothing here depends on OrbStack: on a VPS it runs unchanged.
set -euo pipefail

commit="${1:-}"
if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "provision: pass the full 40-character orchestrator commit to install" >&2
  exit 2
fi
if [[ $EUID -ne 0 ]]; then
  echo "provision: run as root" >&2
  exit 2
fi

UV_VERSION=0.10.9
case "$(uname -m)" in
  aarch64) uv_target=aarch64-unknown-linux-gnu
    uv_sha256=cc0c5a8573e7d6d78aecb954e0a62b5c0d18217bb81f1e19363b428c57a9962a ;;
  x86_64) uv_target=x86_64-unknown-linux-gnu
    uv_sha256=20d79708222611fa540b5c9ed84f352bcd3937740e51aacc0f8b15b271c57594 ;;
  *) echo "provision: no pinned uv for $(uname -m)" >&2; exit 2 ;;
esac

service_user=rotation
state=/var/lib/rotation-worker
opt=/opt/rotation-worker
etc=/etc/rotation-worker
repository=https://github.com/AlobarQuest/orchestrator.git

if ! command -v git >/dev/null || ! command -v curl >/dev/null || ! command -v ssh >/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates openssh-client >/dev/null
fi

if ! id -u "$service_user" >/dev/null 2>&1; then
  useradd --system --home-dir "$state" --shell /usr/sbin/nologin "$service_user"
fi
install -d -o "$service_user" -g "$service_user" -m 0700 "$state"
install -d -o root -g "$service_user" -m 0750 "$etc"
install -d -o root -g root -m 0755 "$opt"

if [[ "$("$opt/bin/uv" --version 2>/dev/null || true)" != "uv $UV_VERSION"* ]]; then
  download="$(mktemp -d)"
  trap 'rm -r -- "$download"' EXIT
  curl -sSfL -o "$download/uv.tgz" \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$uv_target.tar.gz"
  echo "$uv_sha256  $download/uv.tgz" | sha256sum --check --quiet
  tar -xzf "$download/uv.tgz" -C "$download"
  install -D -m 0755 "$download/uv-$uv_target/uv" "$opt/bin/uv"
fi

if [[ ! -d "$opt/src/.git" ]]; then
  git clone --quiet --no-checkout "$repository" "$opt/src"
fi
# GitHub serves any commit reachable from a ref by its id, so a commit on a pull request's branch
# can be installed to test it before it merges.
git -C "$opt/src" fetch --quiet origin "$commit"
git -C "$opt/src" -c advice.detachedHead=false checkout --quiet --force "$commit"

# The interpreter uv downloads and the environment it builds both live under /opt, owned by root,
# so the service user can run them and change neither.
UV_PYTHON_INSTALL_DIR="$opt/python" UV_PROJECT_ENVIRONMENT="$opt/venv" UV_CACHE_DIR="$opt/cache" \
  "$opt/bin/uv" sync --quiet --frozen --no-dev --group rotation-worker --project "$opt/src"

install -m 0644 "$opt/src/scripts/rotation-worker-vm/rotation-worker.service" \
  /etc/systemd/system/rotation-worker.service
install -m 0644 "$opt/src/scripts/rotation-worker-vm/rotation-worker.timer" \
  /etc/systemd/system/rotation-worker.timer
install -m 0755 "$opt/src/scripts/rotation-worker-vm/set-root-token.sh" "$opt/bin/set-root-token"
systemctl daemon-reload

echo "provision: installed ${commit:0:12}; the service runs once $etc/config.toml and the root token exist"
