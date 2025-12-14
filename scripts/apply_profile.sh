#!/usr/bin/env bash
set -euo pipefail

IFACE=${1:-lo}
PROFILE=${2:-slow_10kbps}

uid="${EUID:-$(id -u)}"
if [[ "${uid}" -ne 0 ]]; then
  echo "[apply_profile] ERROR: tc requires root." >&2
  echo "[apply_profile] HINT : sudo $0 ${IFACE} ${PROFILE}" >&2
  exit 1
fi

python -m link.shaper.tc_profiles apply --iface "${IFACE}" --profile "${PROFILE}"
