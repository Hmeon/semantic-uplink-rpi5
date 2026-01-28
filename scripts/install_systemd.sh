#!/usr/bin/env bash
set -euo pipefail

# Install and start semantic-uplink stack as a systemd service.
#
# Usage:
#   sudo SEMUP_USER=pi SEMUP_GROUP=pi SEMUP_RUN_DIR=/var/lib/semantic-uplink/run ./scripts/install_systemd.sh
#
# Notes:
# - This script generates /etc/systemd/system/semantic-uplink-stack.service.
# - Environment overrides live in /etc/semantic-uplink-stack.env (copied from infra/ if missing).

dirname="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${dirname}/.." && pwd)"

SEMUP_SERVICE_NAME="${SEMUP_SERVICE_NAME:-semantic-uplink-stack.service}"
SEMUP_USER="${SEMUP_USER:-pi}"
SEMUP_GROUP="${SEMUP_GROUP:-pi}"
SEMUP_WORKDIR="${SEMUP_WORKDIR:-${repo_root}}"
SEMUP_RUN_DIR="${SEMUP_RUN_DIR:-/var/lib/semantic-uplink/run}"

service_path="/etc/systemd/system/${SEMUP_SERVICE_NAME}"
env_path="/etc/semantic-uplink-stack.env"

echo "[install] writing systemd unit: ${service_path}"
cat > "${service_path}" <<EOF
[Unit]
Description=Semantic Uplink (edge + collector + broker)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SEMUP_USER}
Group=${SEMUP_GROUP}
WorkingDirectory=${SEMUP_WORKDIR}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-${env_path}
ExecStart=${SEMUP_WORKDIR}/scripts/run_stack.sh
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

if [[ ! -f "${env_path}" ]]; then
  echo "[install] creating env file: ${env_path}"
  cp "${repo_root}/infra/systemd/semantic-uplink-stack.env.example" "${env_path}"
  sed -i "s|^RUN_DIR=.*$|RUN_DIR=${SEMUP_RUN_DIR}|g" "${env_path}" || true
else
  echo "[install] env file exists; leaving as-is: ${env_path}"
fi

echo "[install] ensuring run dir exists: ${SEMUP_RUN_DIR}"
install -d -o "${SEMUP_USER}" -g "${SEMUP_GROUP}" "${SEMUP_RUN_DIR}"

echo "[install] systemd reload + enable + start: ${SEMUP_SERVICE_NAME}"
systemctl daemon-reload
systemctl enable --now "${SEMUP_SERVICE_NAME}"
systemctl status --no-pager "${SEMUP_SERVICE_NAME}" || true

