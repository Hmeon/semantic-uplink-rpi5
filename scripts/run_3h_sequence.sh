#!/usr/bin/env bash
set -euo pipefail

# Sequential 3h runs: periodic -> fixed_tau -> adaptive.
# Designed for RPi5. Adjust env vars as needed.

DEVICE_ID=${DEVICE_ID:-rpi5a}
PROFILE=${PROFILE:-slow_10kbps}
IFACE=${IFACE:-lo}
BROKER=${BROKER:-localhost}
PORT=${PORT:-1883}

RUN_SECONDS=${RUN_SECONDS:-10800}
RUN_GRACE=${RUN_GRACE:-30}

SEQ_LOG_DIR=${SEQ_LOG_DIR:-artifacts/sequence_logs}
SEQ_LOG_FILE=${SEQ_LOG_FILE:-${SEQ_LOG_DIR}/sequence.log}

NTP_FREEZE=${NTP_FREEZE:-1}   # 1=stop NTP during runs to avoid time steps
NTP_SERVICE=${NTP_SERVICE:-}  # optional override: systemd-timesyncd|chronyd|chrony|ntp
START_FROM=${START_FROM:-periodic}  # periodic|ewma|fixed_tau|adaptive

MIC_DEVICE=${MIC_DEVICE:-hw:2,0}
MIC_SR=${MIC_SR:-48000}
MIC_FRAME_MS=${MIC_FRAME_MS:-100}
MIC_ALPHA=${MIC_ALPHA:-0.2}
MIC_TAU=${MIC_TAU:-3.0}
MIC_KBITS=${MIC_KBITS:-6}
MIC_HEARTBEAT=${MIC_HEARTBEAT:-10}
MIC_MIN_EMIT_MS=${MIC_MIN_EMIT_MS:-0}

TEMP_HZ=${TEMP_HZ:-1.0}
TEMP_ALPHA=${TEMP_ALPHA:-0.5}
TEMP_TAU=${TEMP_TAU:-0.2}
TEMP_KBITS=${TEMP_KBITS:-8}
TEMP_HEARTBEAT=${TEMP_HEARTBEAT:-10}
TEMP_MIN_EMIT_MS=${TEMP_MIN_EMIT_MS:-0}

UI_KIND=${UI_KIND:-lcd1602}
UI_BUS=${UI_BUS:-1}
UI_ADDR=${UI_ADDR:-0x27}

ADAPTIVE_ARMS=${ADAPTIVE_ARMS:-configs/policy_adaptive_aiot.yaml}
DECISION_PUBLISH=${DECISION_PUBLISH:-always}   # event|always|never (decision msg)

# Optional RTC args (set if hardware present)
RTC_ARGS=${RTC_ARGS:-}

W1_PATH=${W1_PATH:-}
if [[ -z "${W1_PATH}" ]]; then
  W1_PATH="$(ls /sys/bus/w1/devices/28-*/w1_slave 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "${W1_PATH}" || ! -e "${W1_PATH}" ]]; then
  echo "[run_3h_sequence] ERROR: W1_PATH not found. Set W1_PATH or connect DS18B20." >&2
  exit 1
fi

if ! command -v timeout >/dev/null 2>&1; then
  echo "[run_3h_sequence] ERROR: 'timeout' not found (coreutils). Install it first." >&2
  exit 1
fi

# Prefer venv python if present.
PYTHON="${PYTHON:-$(pwd)/.venv/bin/python}"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="${PYTHON3:-python3}"
fi

SUDO=${SUDO:-sudo}
if ! command -v sudo >/dev/null 2>&1; then
  SUDO=""
fi

mkdir -p "${SEQ_LOG_DIR}"

log() {
  local ts
  ts="$(date -Iseconds)"
  echo "[${ts}] $*" | tee -a "${SEQ_LOG_FILE}"
}

ACTIVE_COLLECTOR_PID=""
NTP_FROZEN=0
START_INDEX=0

resolve_start_from() {
  case "${START_FROM}" in
    periodic)
      START_INDEX=0
      ;;
    ewma|fixed_tau)
      START_INDEX=1
      ;;
    adaptive)
      START_INDEX=2
      ;;
    *)
      echo "[run_3h_sequence] ERROR: START_FROM must be periodic|ewma|fixed_tau|adaptive" >&2
      exit 1
      ;;
  esac
}

detect_ntp_service() {
  if [[ -n "${NTP_SERVICE}" ]]; then
    return 0
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    return 1
  fi
  local svc
  for svc in systemd-timesyncd chronyd chrony ntp; do
    if systemctl is-active --quiet "${svc}" >/dev/null 2>&1; then
      NTP_SERVICE="${svc}"
      return 0
    fi
  done
  return 1
}

ntp_status() {
  if ! command -v timedatectl >/dev/null 2>&1; then
    log "[run_3h_sequence] WARN: timedatectl not found; skipping NTP status."
    return
  fi
  log "[run_3h_sequence] NTP status (timedatectl status)"
  local status
  status="$(timedatectl status 2>&1 || true)"
  echo "${status}" | tee -a "${SEQ_LOG_FILE}"
  if echo "${status}" | grep -q "System clock synchronized: yes"; then
    log "[run_3h_sequence] NTP sync: yes"
  else
    log "[run_3h_sequence] WARN: NTP sync not confirmed."
  fi
  log "[run_3h_sequence] NTP status (timedatectl timesync-status)"
  timedatectl timesync-status 2>&1 | tee -a "${SEQ_LOG_FILE}" || true
}

ntp_freeze() {
  if [[ "${NTP_FREEZE}" != "1" ]]; then
    log "[run_3h_sequence] NTP freeze disabled (NTP_FREEZE=${NTP_FREEZE})."
    return
  fi
  if [[ -z "${SUDO}" ]]; then
    log "[run_3h_sequence] WARN: sudo not found; cannot stop NTP."
    return
  fi
  if ! detect_ntp_service; then
    log "[run_3h_sequence] WARN: no active NTP service detected."
    return
  fi
  log "[run_3h_sequence] Stopping NTP service: ${NTP_SERVICE}"
  "${SUDO}" systemctl stop "${NTP_SERVICE}"
  NTP_FROZEN=1
}

ntp_resume() {
  if [[ "${NTP_FROZEN}" != "1" ]]; then
    return
  fi
  if [[ -z "${SUDO}" ]]; then
    return
  fi
  log "[run_3h_sequence] Starting NTP service: ${NTP_SERVICE}"
  "${SUDO}" systemctl start "${NTP_SERVICE}" || true
}

cleanup() {
  ntp_resume
  if [[ -n "${ACTIVE_COLLECTOR_PID}" ]]; then
    kill -SIGINT "${ACTIVE_COLLECTOR_PID}" >/dev/null 2>&1 || true
    wait "${ACTIVE_COLLECTOR_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${SUDO}" ]]; then
    "${SUDO}" -E "${PYTHON}" -m link.shaper.tc_profiles clear --iface "${IFACE}" >/dev/null 2>&1 || true
  else
    "${PYTHON}" -m link.shaper.tc_profiles clear --iface "${IFACE}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

apply_tc() {
  if [[ -n "${SUDO}" ]]; then
    "${SUDO}" -E "${PYTHON}" -m link.shaper.tc_profiles apply --iface "${IFACE}" --profile "${PROFILE}"
    "${SUDO}" -E "${PYTHON}" -m link.shaper.tc_profiles status --iface "${IFACE}"
  else
    "${PYTHON}" -m link.shaper.tc_profiles apply --iface "${IFACE}" --profile "${PROFILE}"
    "${PYTHON}" -m link.shaper.tc_profiles status --iface "${IFACE}"
  fi
}

run_case() {
  local name="$1"
  local mode="$2"
  local collector_id="$3"
  local arms_path="${4:-}"

  local run_dir="artifacts/${name}"
  mkdir -p "${run_dir}/logs"

  log "[run_3h_sequence] START ${name} (${mode})"

  timeout -s SIGINT -k "${RUN_GRACE}" "$((RUN_SECONDS + 30))" \
    "${PYTHON}" -m collector.collector \
      --run-dir "${run_dir}" \
      --broker "${BROKER}" --port "${PORT}" \
      --client-id "${collector_id}" \
      --flush-interval-s 10 \
      --log-level INFO \
      --log-file "${run_dir}/logs/collector.log" &
  ACTIVE_COLLECTOR_PID=$!

  EDGE_ARGS=(
    --device-id "${DEVICE_ID}"
    --profile "${PROFILE}"
    --mode "${mode}"
    --run-dir "${run_dir}"
    --device-config configs/device.yaml
    --broker "${BROKER}" --port "${PORT}"
    --mic-enable --mic-backend arecord --mic-arecord-device "${MIC_DEVICE}"
    --mic-sr "${MIC_SR}" --mic-frame-ms "${MIC_FRAME_MS}"
    --mic-alpha "${MIC_ALPHA}" --mic-tau "${MIC_TAU}" --mic-kbits "${MIC_KBITS}"
    --mic-heartbeat "${MIC_HEARTBEAT}" --mic-min-emit-ms "${MIC_MIN_EMIT_MS}"
    --temp-enable --temp-backend w1 --temp-w1-path "${W1_PATH}"
    --temp-hz "${TEMP_HZ}" --temp-alpha "${TEMP_ALPHA}" --temp-tau "${TEMP_TAU}"
    --temp-kbits "${TEMP_KBITS}" --temp-heartbeat "${TEMP_HEARTBEAT}"
    --temp-min-emit-ms "${TEMP_MIN_EMIT_MS}"
    --ui-enable --ui-kind "${UI_KIND}" --ui-bus "${UI_BUS}" --ui-address "${UI_ADDR}"
    --buttons-disable
    --log-level DEBUG --log-file "${run_dir}/logs/edge.log"
  )

  if [[ -n "${arms_path}" ]]; then
    EDGE_ARGS+=(--arms "${arms_path}")
  fi
  if [[ "${mode}" == "adaptive" ]]; then
    EDGE_ARGS+=(--decision-publish "${DECISION_PUBLISH}")
  fi
  if [[ -n "${RTC_ARGS}" ]]; then
    # shellcheck disable=SC2206
    EDGE_ARGS+=(${RTC_ARGS})
  fi

  local edge_status=0
  set +e
  timeout -s SIGINT -k "${RUN_GRACE}" "${RUN_SECONDS}" \
    "${PYTHON}" -m edge.edge_daemon "${EDGE_ARGS[@]}"
  edge_status=$?
  set -e
  if [[ "${edge_status}" -ne 0 ]]; then
    if [[ "${edge_status}" -eq 124 ]]; then
      log "[run_3h_sequence] edge timeout reached (expected)"
    else
      log "[run_3h_sequence] ERROR: edge exit status ${edge_status}"
      return "${edge_status}"
    fi
  fi

  wait "${ACTIVE_COLLECTOR_PID}" || true
  ACTIVE_COLLECTOR_PID=""

  log "[run_3h_sequence] DONE ${name}"
}

run_if() {
  local idx="$1"
  shift
  local name="$1"
  if [[ "${idx}" -lt "${START_INDEX}" ]]; then
    log "[run_3h_sequence] SKIP ${name} (start_from=${START_FROM})"
    return 0
  fi
  run_case "$@"
}

resolve_start_from
log "[run_3h_sequence] START_FROM=${START_FROM}"
if [[ "${NTP_FREEZE}" == "1" ]]; then
  ntp_status
  ntp_freeze
fi

apply_tc

run_if 0 "slow10_periodic_3h_q" "periodic" "collector_periodic_q"
run_if 1 "slow10_fixed_3h_q" "fixed_tau" "collector_fixed_q"
run_if 2 "slow10_linucb_3h_aiot" "adaptive" "collector_linucb_aiot" "${ADAPTIVE_ARMS}"

log "[run_3h_sequence] ALL DONE"
