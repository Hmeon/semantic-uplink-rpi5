#!/usr/bin/env bash
set -euo pipefail

# Sequential 3h runs: periodic -> fixed_tau -> adaptive.
# Designed for RPi5. Adjust env vars as needed.

dirname="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${dirname}/.." && pwd)"
cd "${repo_root}"

DEVICE_ID=${DEVICE_ID:-rpi5a}
PROFILE=${PROFILE:-slow_10kbps}
IFACE=${IFACE:-lo}
BROKER=${BROKER:-localhost}
PORT=${PORT:-1883}

TC_ENABLE=${TC_ENABLE:-1}   # 1=apply tc/netem shaping, 0=skip tc entirely
TC_BOTH=${TC_BOTH:-0}       # 1=apply ingress shaping via ifb0 as well
TC_PROFILES_CONFIG=${TC_PROFILES_CONFIG:-}  # optional YAML override (e.g., configs/link_profiles.yaml)

FIELD_LABEL=${FIELD_LABEL:-}  # e.g. A|B (free text ok; used for run tagging)
SEQ_ID=${SEQ_ID:-$(date -u +"%Y-%m-%dT%H-%M-%SZ")}
RUN_ROOT=${RUN_ROOT:-artifacts/field_runs}
RUN_ROOT_DIR=${RUN_ROOT_DIR:-}
RESULTS_ROOT=${RESULTS_ROOT:-results/field_runs}
RESULTS_DIR=${RESULTS_DIR:-}

RUN_SECONDS=${RUN_SECONDS:-10800}
RUN_GRACE=${RUN_GRACE:-30}

SEQ_LOG_DIR=${SEQ_LOG_DIR:-}
SEQ_LOG_FILE=${SEQ_LOG_FILE:-}

ANALYZE=${ANALYZE:-1}          # 1=run collector.analyze at the end
ANALYZE_ONLY=${ANALYZE_ONLY:-0} # 1=skip running; just analyze existing RUN_ROOT_DIR
ANALYZE_PLOTS=${ANALYZE_PLOTS:-1}
ANALYZE_AUDIT=${ANALYZE_AUDIT:-1}
ANALYZE_EXTRA_ARGS=${ANALYZE_EXTRA_ARGS:-}
KPI_ENFORCE_PASS=${KPI_ENFORCE_PASS:-1}  # 1=exit non-zero when KPI != PASS

NTP_FREEZE=${NTP_FREEZE:-1}   # 1=stop NTP during runs to avoid time steps
NTP_SERVICE=${NTP_SERVICE:-}  # optional override: systemd-timesyncd|chronyd|chrony|ntp
START_FROM=${START_FROM:-periodic}  # periodic|ewma|fixed_tau|adaptive
ALLOW_UNSYNC=${ALLOW_UNSYNC:-0}
ALLOW_OVERWRITE=${ALLOW_OVERWRITE:-0}

# LinUCB reproducibility
SEMUP_SEED=${SEMUP_SEED:-0}

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

ADAPTIVE_ARMS=${ADAPTIVE_ARMS:-configs/policy.yaml}
DECISION_PUBLISH=${DECISION_PUBLISH:-never}   # event|always|never (decision msg)

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

# Default run root directory: artifacts/field_runs/<utc_ts>_<device_id>[_fieldX]
if [[ -z "${RUN_ROOT_DIR}" ]]; then
  RUN_ROOT_DIR="${RUN_ROOT}/${SEQ_ID}_${DEVICE_ID}"
  if [[ -n "${FIELD_LABEL}" ]]; then
    RUN_ROOT_DIR="${RUN_ROOT_DIR}_field${FIELD_LABEL}"
  fi
fi

if [[ -z "${SEQ_LOG_DIR}" ]]; then
  SEQ_LOG_DIR="${RUN_ROOT_DIR}"
fi
if [[ -z "${SEQ_LOG_FILE}" ]]; then
  SEQ_LOG_FILE="${SEQ_LOG_DIR}/sequence.log"
fi

if [[ -z "${RESULTS_DIR}" ]]; then
  RESULTS_DIR="${RESULTS_ROOT}/$(basename "${RUN_ROOT_DIR}")"
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

export SEMUP_SEED

mkdir -p "${SEQ_LOG_DIR}"
mkdir -p "${RUN_ROOT_DIR}"

log() {
  local ts
  ts="$(date -Iseconds)"
  echo "[${ts}] $*" | tee -a "${SEQ_LOG_FILE}"
}

write_checklist() {
  local p="${RUN_ROOT_DIR}/CHECKLIST.md"
  cat >"${p}" <<EOF
# Field Run Checklist (A/B reproducibility)

Run root: \`${RUN_ROOT_DIR}\`
Results:  \`${RESULTS_DIR}\`

## Preconditions (before starting)
- [ ] Edge + Collector are on the **same host** (recommended), or hosts are time-synced (NTP/PTP) with no steps.
- [ ] \`timedatectl status\` shows \`System clock synchronized: yes\` (or you explicitly accept risk with \`ALLOW_UNSYNC=1\`).
- [ ] No manual clock changes during the whole sequence (9h if RUN_SECONDS=3h × 3 policies).
- [ ] Power is stable (avoid brownouts), CPU throttling is not active.
- [ ] Link shaping: \`TC_ENABLE=${TC_ENABLE}\` (profile=\`${PROFILE}\`, iface=\`${IFACE}\`, both=\`${TC_BOTH}\`).
- [ ] Sensors stable: mic backend=\`arecord\`, DS18B20 path=\`${W1_PATH:-<auto>}\`.

## Identity (must match across policies)
- device_id: \`${DEVICE_ID}\`
- profile: \`${PROFILE}\`
- broker: \`${BROKER}:${PORT}\`
- SEMUP_SEED: \`${SEMUP_SEED}\` (adaptive reproducibility)
- fixed_tau baseline: mic(tau=\`${MIC_TAU}\`,kbits=\`${MIC_KBITS}\`) / temp(tau=\`${TEMP_TAU}\`,kbits=\`${TEMP_KBITS}\`)
- adaptive arms: \`${ADAPTIVE_ARMS}\`

## After run (PASS reproduction)
- [ ] Run analyzer and check \`${RESULTS_DIR}/kpi_verdict.json\` is \`PASS\`.
- [ ] Archive \`${RUN_ROOT_DIR}\` and \`${RESULTS_DIR}\` together (include \`sequence.log\`).
EOF
  log "[run_3h_sequence] Wrote checklist: ${p}"
}

write_meta() {
  local meta="${RUN_ROOT_DIR}/RUN_META.txt"
  {
    echo "utc_start=$(date -u -Iseconds)"
    echo "local_start=$(date -Iseconds)"
    echo "run_root_dir=${RUN_ROOT_DIR}"
    echo "results_dir=${RESULTS_DIR}"
    echo "device_id=${DEVICE_ID}"
    echo "profile=${PROFILE}"
    echo "iface=${IFACE}"
    echo "tc_enable=${TC_ENABLE}"
    echo "tc_both=${TC_BOTH}"
    echo "tc_profiles_config=${TC_PROFILES_CONFIG}"
    echo "broker=${BROKER}"
    echo "port=${PORT}"
    echo "run_seconds=${RUN_SECONDS}"
    echo "run_grace=${RUN_GRACE}"
    echo "start_from=${START_FROM}"
    echo "ntp_freeze=${NTP_FREEZE}"
    echo "ntp_service=${NTP_SERVICE}"
    echo "allow_unsync=${ALLOW_UNSYNC}"
    echo "allow_overwrite=${ALLOW_OVERWRITE}"
    echo "semup_seed=${SEMUP_SEED}"
    echo "adaptive_arms=${ADAPTIVE_ARMS}"
    echo "decision_publish=${DECISION_PUBLISH}"
    echo "mic_device=${MIC_DEVICE}"
    echo "mic_sr=${MIC_SR}"
    echo "mic_frame_ms=${MIC_FRAME_MS}"
    echo "mic_alpha=${MIC_ALPHA}"
    echo "mic_tau=${MIC_TAU}"
    echo "mic_kbits=${MIC_KBITS}"
    echo "mic_heartbeat=${MIC_HEARTBEAT}"
    echo "mic_min_emit_ms=${MIC_MIN_EMIT_MS}"
    echo "temp_hz=${TEMP_HZ}"
    echo "temp_alpha=${TEMP_ALPHA}"
    echo "temp_tau=${TEMP_TAU}"
    echo "temp_kbits=${TEMP_KBITS}"
    echo "temp_heartbeat=${TEMP_HEARTBEAT}"
    echo "temp_min_emit_ms=${TEMP_MIN_EMIT_MS}"
    echo "ui_kind=${UI_KIND}"
    echo "ui_bus=${UI_BUS}"
    echo "ui_addr=${UI_ADDR}"
  } >"${meta}"

  if command -v git >/dev/null 2>&1; then
    {
      echo
      echo "git_commit=$(git rev-parse HEAD 2>/dev/null || true)"
      echo "git_status_porcelain:"
      git status --porcelain=v1 2>/dev/null || true
    } >>"${meta}"
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    {
      echo
      echo "sha256sum:"
      sha256sum scripts/run_3h_sequence.sh 2>/dev/null || true
      sha256sum configs/device.yaml 2>/dev/null || true
      sha256sum "${ADAPTIVE_ARMS}" 2>/dev/null || true
    } >>"${meta}"
  fi

  log "[run_3h_sequence] Wrote meta: ${meta}"
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
    log "[run_3h_sequence] WARN: timedatectl not found; cannot verify time sync."
    if [[ "${ALLOW_UNSYNC}" != "1" ]]; then
      log "[run_3h_sequence] ERROR: Set ALLOW_UNSYNC=1 to proceed without timedatectl."
      exit 1
    fi
    return
  fi
  log "[run_3h_sequence] NTP status (timedatectl status)"
  local status
  status="$(timedatectl status 2>&1 || true)"
  echo "${status}" | tee -a "${SEQ_LOG_FILE}"
  if ! echo "${status}" | grep -q "System clock synchronized: yes"; then
    log "[run_3h_sequence] WARN: System clock synchronized is not 'yes'."
    if [[ "${ALLOW_UNSYNC}" != "1" ]]; then
      log "[run_3h_sequence] ERROR: Refusing to run without time sync (ALLOW_UNSYNC=1 to override)."
      exit 1
    fi
  fi
  log "[run_3h_sequence] NTP status (timedatectl timesync-status)"
  timedatectl timesync-status 2>&1 | tee -a "${SEQ_LOG_FILE}" || true
  if command -v chronyc >/dev/null 2>&1; then
    log "[run_3h_sequence] NTP status (chronyc tracking)"
    chronyc tracking 2>&1 | tee -a "${SEQ_LOG_FILE}" || true
  fi
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
  if [[ "${TC_ENABLE}" == "1" ]]; then
    local tc_args=(--iface "${IFACE}")
    if [[ "${TC_BOTH}" == "1" ]]; then
      tc_args+=(--both)
    fi
    if [[ -n "${SUDO}" ]]; then
      "${SUDO}" -E "${PYTHON}" -m link.shaper.tc_profiles clear "${tc_args[@]}" >/dev/null 2>&1 || true
    else
      "${PYTHON}" -m link.shaper.tc_profiles clear "${tc_args[@]}" >/dev/null 2>&1 || true
    fi
  fi
}
trap cleanup EXIT INT TERM

apply_tc() {
  if [[ "${TC_ENABLE}" != "1" ]]; then
    log "[run_3h_sequence] TC_ENABLE=0 (skipping tc/netem shaping)"
    return 0
  fi
  local tc_args=(--iface "${IFACE}" --profile "${PROFILE}")
  local status_args=(--iface "${IFACE}")
  if [[ "${TC_BOTH}" == "1" ]]; then
    tc_args+=(--both)
    status_args+=(--both)
  fi
  if [[ -n "${TC_PROFILES_CONFIG}" ]]; then
    tc_args=(--profiles-config "${TC_PROFILES_CONFIG}" "${tc_args[@]}")
  fi
  if [[ -n "${SUDO}" ]]; then
    "${SUDO}" -E "${PYTHON}" -m link.shaper.tc_profiles apply "${tc_args[@]}"
    "${SUDO}" -E "${PYTHON}" -m link.shaper.tc_profiles status "${status_args[@]}"
  else
    "${PYTHON}" -m link.shaper.tc_profiles apply "${tc_args[@]}"
    "${PYTHON}" -m link.shaper.tc_profiles status "${status_args[@]}"
  fi
}

run_case() {
  local scenario="$1"
  local mode="$2"
  local collector_id="$3"
  local arms_path="${4:-}"

  local run_dir="${RUN_ROOT_DIR}/${scenario}"
  if [[ -d "${run_dir}" && "${ALLOW_OVERWRITE}" != "1" ]]; then
    if compgen -G "${run_dir}/logs/events*.parquet" >/dev/null 2>&1; then
      log "[run_3h_sequence] ERROR: ${run_dir} already has events logs. Set ALLOW_OVERWRITE=1 or use a new RUN_ROOT_DIR/SEQ_ID."
      exit 1
    fi
  fi
  mkdir -p "${run_dir}/logs"

  log "[run_3h_sequence] START ${scenario} (${mode})"

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

  log "[run_3h_sequence] DONE ${scenario}"
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

analyze_runs() {
  mkdir -p "${RESULTS_DIR}"
  if [[ "${ALLOW_OVERWRITE}" != "1" && -f "${RESULTS_DIR}/kpi_verdict.json" ]]; then
    log "[run_3h_sequence] ERROR: ${RESULTS_DIR}/kpi_verdict.json already exists. Set ALLOW_OVERWRITE=1 or use a new RESULTS_DIR."
    exit 1
  fi

  local inputs=()
  local d=""
  for d in \
    "${RUN_ROOT_DIR}/${PROFILE}__periodic" \
    "${RUN_ROOT_DIR}/${PROFILE}__fixed_tau" \
    "${RUN_ROOT_DIR}/${PROFILE}__adaptive"; do
    if [[ -d "${d}" ]]; then
      inputs+=(--input "${d}")
    fi
  done

  if [[ "${#inputs[@]}" -eq 0 ]]; then
    log "[run_3h_sequence] ERROR: no run directories found under ${RUN_ROOT_DIR}"
    exit 1
  fi

  local cmd=(
    "${PYTHON}" -m collector.analyze
    "${inputs[@]}"
    --out "${RESULTS_DIR}"
    --baseline-policy periodic
    --policy-config "${ADAPTIVE_ARMS}"
  )
  if [[ "${ANALYZE_PLOTS}" == "1" ]]; then
    cmd+=(--plots --paper-plots)
  else
    cmd+=(--no-plots --no-paper-plots)
  fi
  if [[ "${ANALYZE_AUDIT}" == "1" ]]; then
    cmd+=(--audit)
  fi
  if [[ -n "${ANALYZE_EXTRA_ARGS}" ]]; then
    # shellcheck disable=SC2206
    cmd+=(${ANALYZE_EXTRA_ARGS})
  fi

  log "[run_3h_sequence] ANALYZE -> ${RESULTS_DIR}"
  log "[run_3h_sequence] ANALYZE CMD: ${cmd[*]}"
  "${cmd[@]}" 2>&1 | tee -a "${SEQ_LOG_FILE}" "${RESULTS_DIR}/analyze.log"

  cp -f "${SEQ_LOG_FILE}" "${RESULTS_DIR}/sequence.log" 2>/dev/null || true
  cp -f "${RUN_ROOT_DIR}/RUN_META.txt" "${RESULTS_DIR}/RUN_META.txt" 2>/dev/null || true
  cp -f "${RUN_ROOT_DIR}/CHECKLIST.md" "${RESULTS_DIR}/CHECKLIST.md" 2>/dev/null || true

  local verdict_json="${RESULTS_DIR}/kpi_verdict.json"
  if [[ ! -f "${verdict_json}" ]]; then
    log "[run_3h_sequence] ERROR: KPI verdict missing: ${verdict_json}"
    exit 1
  fi

  local verdict_info=""
  verdict_info="$("${PYTHON}" - <<'PY' "${verdict_json}"
import json
import sys
from pathlib import Path

p = Path(sys.argv[1])
obj = json.loads(p.read_text(encoding="utf-8"))
print(obj.get("project_verdict", "UNKNOWN"))
failed = obj.get("failed") or []
print(",".join(str(x) for x in failed))
print(obj.get("reason", ""))
PY
)"

  local kpi_lines=()
  mapfile -t kpi_lines <<<"${verdict_info}"
  local verdict="${kpi_lines[0]:-UNKNOWN}"
  local failed="${kpi_lines[1]:-}"
  local reason="${kpi_lines[2]:-}"
  log "[run_3h_sequence] KPI verdict: ${verdict} (failed=${failed})"
  if [[ -n "${reason}" ]]; then
    log "[run_3h_sequence] KPI note: ${reason}"
  fi

  if [[ "${KPI_ENFORCE_PASS}" == "1" && "${verdict}" != "PASS" ]]; then
    log "[run_3h_sequence] ERROR: KPI != PASS (set KPI_ENFORCE_PASS=0 to not fail the script)"
    exit 2
  fi
}

resolve_start_from
write_checklist
write_meta
log "[run_3h_sequence] run_root_dir=${RUN_ROOT_DIR}"
log "[run_3h_sequence] results_dir=${RESULTS_DIR}"
log "[run_3h_sequence] START_FROM=${START_FROM}"

if [[ "${ANALYZE_ONLY}" == "1" ]]; then
  log "[run_3h_sequence] ANALYZE_ONLY=1 (skipping runs)"
  analyze_runs
  log "[run_3h_sequence] ALL DONE"
  exit 0
fi

ntp_status
ntp_freeze

apply_tc

run_if 0 "${PROFILE}__periodic" "periodic" "collector_${DEVICE_ID}_periodic"
run_if 1 "${PROFILE}__fixed_tau" "fixed_tau" "collector_${DEVICE_ID}_fixed_tau"
run_if 2 "${PROFILE}__adaptive" "adaptive" "collector_${DEVICE_ID}_adaptive" "${ADAPTIVE_ARMS}"

if [[ "${ANALYZE}" == "1" ]]; then
  analyze_runs
fi

log "[run_3h_sequence] ALL DONE"
