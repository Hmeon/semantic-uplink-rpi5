#!/usr/bin/env bash
set -euo pipefail

DEVICE_ID=${DEVICE_ID:-rpi5a}
PROFILE=${PROFILE:-slow_10kbps}
MODE=${MODE:-periodic}
ARMS=${ARMS:-configs/policy.yaml}

# 간단 실행용(필요시 환경변수로 덮어쓰기)
python -m edge.edge_daemon \
  --device-id "$DEVICE_ID" \
  --profile "$PROFILE" \
  --mode "$MODE" \
  --arms "$ARMS" \
  --mic-enable \
  --temp-enable
