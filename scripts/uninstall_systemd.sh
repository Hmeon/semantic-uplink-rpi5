#!/usr/bin/env bash
set -euo pipefail

# Uninstall semantic-uplink stack systemd service.
#
# Usage:
#   sudo ./scripts/uninstall_systemd.sh

SEMUP_SERVICE_NAME="${SEMUP_SERVICE_NAME:-semantic-uplink-stack.service}"
service_path="/etc/systemd/system/${SEMUP_SERVICE_NAME}"

echo "[uninstall] stop + disable: ${SEMUP_SERVICE_NAME}"
systemctl disable --now "${SEMUP_SERVICE_NAME}" || true

if [[ -f "${service_path}" ]]; then
  echo "[uninstall] removing unit: ${service_path}"
  rm -f "${service_path}"
fi

echo "[uninstall] systemd reload"
systemctl daemon-reload

