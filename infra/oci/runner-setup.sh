#!/usr/bin/env bash
# SPDX-License-Identifier: FSL-1.1-ALv2
# Installs a GitHub Actions self-hosted runner for the agent workflow on the OCI host.
#
# Run on the host as the ubuntu user:  sudo bash runner-setup.sh
# It asks for the registration token from
#   https://github.com/Tradvia/tradvia/settings/actions/runners/new
# (valid for one hour). The token is read from the terminal and never written to disk.
#
# Isolation on a 1 GB host:
#   - dedicated user `gha-runner`: no sudo, not in the docker group, no access to /opt/tradvia;
#   - the service runs in its own systemd cgroup with MemoryHigh/MemoryMax, so an
#     out-of-memory card kills the runner, not Caddy, the API or Postgres.
set -euo pipefail

RUNNER_VERSION="${RUNNER_VERSION:-2.338.0}"
REPO_URL="${REPO_URL:-https://github.com/Tradvia/tradvia}"
LABELS="${LABELS:-tradvia-oci}"
RUNNER_NAME="${RUNNER_NAME:-$(hostname)}"
RUNNER_USER="gha-runner"
RUNNER_HOME="/home/${RUNNER_USER}"
RUNNER_DIR="${RUNNER_HOME}/actions-runner"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 1
fi

case "$(dpkg --print-architecture)" in
  amd64) ARCH="x64" ;;
  arm64) ARCH="arm64" ;;
  *) echo "Unsupported architecture" >&2; exit 1 ;;
esac

echo "==> Packages"
DEBIAN_FRONTEND=noninteractive apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q git make curl jq tar build-essential

echo "==> User ${RUNNER_USER} (no sudo, no docker group)"
id -u "${RUNNER_USER}" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "${RUNNER_USER}"
gpasswd -d "${RUNNER_USER}" docker >/dev/null 2>&1 || true

echo "==> Runner ${RUNNER_VERSION} (${ARCH})"
install -d -o "${RUNNER_USER}" -g "${RUNNER_USER}" "${RUNNER_DIR}"
TARBALL="actions-runner-linux-${ARCH}-${RUNNER_VERSION}.tar.gz"
if [[ ! -x "${RUNNER_DIR}/config.sh" ]]; then
  sudo -u "${RUNNER_USER}" curl -fsSL -o "${RUNNER_DIR}/${TARBALL}" \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TARBALL}"
  sudo -u "${RUNNER_USER}" tar -xzf "${RUNNER_DIR}/${TARBALL}" -C "${RUNNER_DIR}"
  rm -f "${RUNNER_DIR}/${TARBALL}"
  "${RUNNER_DIR}/bin/installdependencies.sh"
fi

echo "==> Registration"
if [[ ! -f "${RUNNER_DIR}/.runner" ]]; then
  read -r -s -p "Paste the registration token (input hidden): " TOKEN; echo
  # Sent on stdin and exported as ACTIONS_RUNNER_INPUT_TOKEN, so it never appears in argv (ps).
  printf '%s\n' "${TOKEN}" | sudo -u "${RUNNER_USER}" bash -c "read -r ACTIONS_RUNNER_INPUT_TOKEN && \
    export ACTIONS_RUNNER_INPUT_TOKEN && cd '${RUNNER_DIR}' && \
    ./config.sh --unattended --replace --url '${REPO_URL}' \
    --name '${RUNNER_NAME}' --labels '${LABELS}' --work _work"
  unset TOKEN
fi

echo "==> Service"
cd "${RUNNER_DIR}"
./svc.sh install "${RUNNER_USER}" >/dev/null
SERVICE="$(cat .service)"
install -d "/etc/systemd/system/${SERVICE}.d"
cat > "/etc/systemd/system/${SERVICE}.d/10-tradvia-limits.conf" <<'EOF'
[Service]
# Leave room for Caddy, the API and Postgres on a 1 GB host (swap absorbs peaks).
MemoryHigh=600M
MemoryMax=900M
CPUWeight=50
Nice=10
EOF
systemctl daemon-reload
./svc.sh start
systemctl --no-pager --lines=0 status "${SERVICE}" || true

echo "==> Done. The runner appears as '${RUNNER_NAME}' with label '${LABELS}' in"
echo "    ${REPO_URL}/settings/actions/runners"
