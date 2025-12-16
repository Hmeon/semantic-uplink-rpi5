#!/usr/bin/env bash
set -euo pipefail

uid="${EUID:-$(id -u)}"
if [[ "${uid}" -ne 0 ]]; then
  echo "[install_systemd_stack] ERROR: must run as root." >&2
  echo "[install_systemd_stack] HINT : sudo $0" >&2
  exit 1
fi

dirname="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${dirname}/.." && pwd)"

SERVICE_NAME=${SERVICE_NAME:-semantic-uplink-stack}
SERVICE_USER=${SERVICE_USER:-pi}

unit_path="/etc/systemd/system/${SERVICE_NAME}.service"
run_stack="${repo_root}/scripts/run_stack.sh"

if [[ ! -x "${run_stack}" ]]; then
  echo "[install_systemd_stack] ERROR: missing ${run_stack}" >&2
  exit 1
fi

cat > "${unit_path}" <<EOF
[Unit]
Description=Semantic Uplink single-Pi stack (broker+collector+edge)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${repo_root}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/${SERVICE_NAME}.env
ExecStart=${run_stack}
Restart=on-failure
RestartSec=2

# tc/netem uses CAP_NET_ADMIN (preferred over running the whole stack as root).
AmbientCapabilities=CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_ADMIN
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

echo "[install_systemd_stack] wrote: ${unit_path}"
echo "[install_systemd_stack] optional env: /etc/${SERVICE_NAME}.env (RUN_DIR, DEVICE_CONFIG, TC_IFACE, ...)"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
echo "[install_systemd_stack] enabled: ${SERVICE_NAME}.service"
echo "[install_systemd_stack] start with: systemctl start ${SERVICE_NAME}.service"

