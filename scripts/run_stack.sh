#!/usr/bin/env bash
set -euo pipefail

# Single-Pi "all-in-one" stack runner:
# - Broker: Mosquitto (auto-started if needed)
# - Collector: MQTT subscriber + parquet sink
# - Edge: sensors → prediction → policy → outbox → MQTT QoS1
#
# This script is foreground/blocking (good for systemd ExecStart).

dirname="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${dirname}/.." && pwd)"
cd "${repo_root}"

RUN_DIR=${RUN_DIR:-artifacts/live}
DEVICE_CONFIG=${DEVICE_CONFIG:-configs/device.yaml}
POLICY_ARMS=${POLICY_ARMS:-configs/policy.yaml}

BROKER_HOST=${BROKER_HOST:-localhost}
BROKER_PORT=${BROKER_PORT:-1883}
BROKER_MODE=${BROKER_MODE:-auto}           # auto | subprocess | none
MOSQUITTO_BIN=${MOSQUITTO_BIN:-mosquitto}
MOSQUITTO_LISTEN_HOST=${MOSQUITTO_LISTEN_HOST:-127.0.0.1}
MOSQUITTO_VERBOSE=${MOSQUITTO_VERBOSE:-0}
BASE_TOPIC=${BASE_TOPIC:-}
MQTT_USERNAME=${MQTT_USERNAME:-}
MQTT_PASSWORD=${MQTT_PASSWORD:-}
MQTT_TLS=${MQTT_TLS:-0}
MQTT_CAFILE=${MQTT_CAFILE:-}
MQTT_CERTFILE=${MQTT_CERTFILE:-}
MQTT_KEYFILE=${MQTT_KEYFILE:-}

COLLECTOR_FLUSH_INTERVAL_S=${COLLECTOR_FLUSH_INTERVAL_S:-10}

BUTTONS_ENABLE=${BUTTONS_ENABLE:-1}
TC_ENABLE=${TC_ENABLE:-1}
TC_IFACE=${TC_IFACE:-lo}
TC_BOTH=${TC_BOTH:-0}
TC_PROFILES_CONFIG=${TC_PROFILES_CONFIG:-configs/link_profiles.yaml}

# Prefer venv python if present.
PYTHON="${PYTHON:-${repo_root}/.venv/bin/python}"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="${PYTHON3:-python3}"
fi

ARGS=(
  --run-dir "${RUN_DIR}"
  --device-config "${DEVICE_CONFIG}"
  --policy-arms "${POLICY_ARMS}"
  --broker-host "${BROKER_HOST}"
  --broker-port "${BROKER_PORT}"
  --broker-mode "${BROKER_MODE}"
  --mosquitto-bin "${MOSQUITTO_BIN}"
  --mosquitto-listen-host "${MOSQUITTO_LISTEN_HOST}"
  --collector-flush-interval-s "${COLLECTOR_FLUSH_INTERVAL_S}"
  --tc-iface "${TC_IFACE}"
  --tc-profiles-config "${TC_PROFILES_CONFIG}"
)

if [[ -n "${BASE_TOPIC}" ]]; then
  ARGS+=(--base-topic "${BASE_TOPIC}")
fi

if [[ -n "${MQTT_USERNAME}" ]]; then
  ARGS+=(--mqtt-username "${MQTT_USERNAME}")
fi
if [[ -n "${MQTT_PASSWORD}" ]]; then
  ARGS+=(--mqtt-password "${MQTT_PASSWORD}")
fi
if [[ "${MQTT_TLS}" == "1" ]]; then
  ARGS+=(--mqtt-tls)
  if [[ -n "${MQTT_CAFILE}" ]]; then
    ARGS+=(--mqtt-cafile "${MQTT_CAFILE}")
  fi
  if [[ -n "${MQTT_CERTFILE}" ]]; then
    ARGS+=(--mqtt-certfile "${MQTT_CERTFILE}")
  fi
  if [[ -n "${MQTT_KEYFILE}" ]]; then
    ARGS+=(--mqtt-keyfile "${MQTT_KEYFILE}")
  fi
fi

if [[ "${BUTTONS_ENABLE}" == "1" ]]; then
  ARGS+=(--buttons-enable)
else
  ARGS+=(--buttons-disable)
fi

if [[ "${MOSQUITTO_VERBOSE}" == "1" ]]; then
  ARGS+=(--mosquitto-verbose)
fi

if [[ "${TC_ENABLE}" == "1" ]]; then
  ARGS+=(--tc-enable)
else
  ARGS+=(--tc-disable)
fi

if [[ "${TC_BOTH}" == "1" ]]; then
  ARGS+=(--tc-both)
fi

exec "${PYTHON}" -m stack.pi_stack "${ARGS[@]}"
