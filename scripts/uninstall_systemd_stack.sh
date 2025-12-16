#!/usr/bin/env bash
set -euo pipefail

uid="${EUID:-$(id -u)}"
if [[ "${uid}" -ne 0 ]]; then
  echo "[uninstall_systemd_stack] ERROR: must run as root." >&2
  echo "[uninstall_systemd_stack] HINT : sudo $0" >&2
  exit 1
fi

SERVICE_NAME=${SERVICE_NAME:-semantic-uplink-stack}
unit_path="/etc/systemd/system/${SERVICE_NAME}.service"

systemctl disable --now "${SERVICE_NAME}.service" >/dev/null 2>&1 || true

if [[ -f "${unit_path}" ]]; then
  rm -f "${unit_path}"
  echo "[uninstall_systemd_stack] removed: ${unit_path}"
fi

systemctl daemon-reload
echo "[uninstall_systemd_stack] done"

