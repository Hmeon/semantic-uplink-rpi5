#!/usr/bin/env bash
set -euo pipefail

DEVICE_ID=${DEVICE_ID:-rpi5a}
PROFILE=${PROFILE:-slow_10kbps}
MODE=${MODE:-periodic}
ARMS=${ARMS:-configs/policy.yaml}
BROKER=${BROKER:-localhost}
PORT=${PORT:-1883}
CLIENT_ID=${CLIENT_ID:-edge-pub}
RUN_DIR=${RUN_DIR:-}

# 간단 실행용(필요시 환경변수로 덮어쓰기)
ARGS=(
  --device-id "$DEVICE_ID"
  --profile "$PROFILE"
  --mode "$MODE"
  --arms "$ARMS"
  --broker "$BROKER"
  --port "$PORT"
  --client-id "$CLIENT_ID"
)
if [[ -n "${RUN_DIR}" ]]; then
  ARGS+=(--run-dir "${RUN_DIR}")
fi

python -m edge.edge_daemon "${ARGS[@]}" \
  --mic-enable \
  --temp-enable
