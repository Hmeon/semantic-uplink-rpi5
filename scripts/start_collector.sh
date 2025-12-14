#!/usr/bin/env bash
set -euo pipefail

RUN_DIR=${1:-${RUN_DIR:-artifacts/run1}}
BROKER=${BROKER:-localhost}
PORT=${PORT:-1883}
FLUSH_INTERVAL_S=${FLUSH_INTERVAL_S:-10}
CLIENT_ID=${CLIENT_ID:-collector}
CLOCK_OFFSET_NS=${CLOCK_OFFSET_NS:-0}

python -m collector.collector \
  --run-dir "${RUN_DIR}" \
  --broker "${BROKER}" \
  --port "${PORT}" \
  --flush-interval-s "${FLUSH_INTERVAL_S}" \
  --client-id "${CLIENT_ID}" \
  --clock-offset-ns "${CLOCK_OFFSET_NS}"
