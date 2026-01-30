# Operational readiness

## Install (RPi5)
- Create venv and install deps:
  - `python3 -m venv .venv`
  - `. .venv/bin/activate`
  - `pip install -e .[dev,analysis,hw]`

## Run modes
- Edge daemon:
  - `python -m edge.edge_daemon --device-id rpi5-01 --profile slow_10kbps --mode adaptive --run-dir /var/lib/semantic-uplink/run --device-config configs/device.yaml --arms configs/policy_poc_covforce_kpi.yaml --decision-publish event`
- Collector:
  - `python -m collector.collector --run-dir /var/lib/semantic-uplink/run --broker localhost --port 1883`
- Analyze:
  - `python -m collector.analyze --input /var/lib/semantic-uplink/run/logs --out /var/lib/semantic-uplink/analysis --diagnostic-plots --audit`

## Logging and rotation
- Logging is configured via `common/logging_setup.py` with optional rotating file handler.
- Use `--log-file` and `--log-max-bytes`/`--log-backup-count` on CLI where available.
- Policy diagnostics are gated by:
  - `diagnostics.enabled`: decision/learning diagnostics (written to `decisions_*.parquet` when decision publish is enabled)
  - `diagnostics.events_enabled`: event-level diagnostics payload (e.g., `event_reason`); when omitted it defaults to `diagnostics.enabled`

  For payload-fair KPI comparisons, prefer `diagnostics.enabled: true` with `diagnostics.events_enabled: false`
  (this is how `configs/policy_poc_covforce_kpi.yaml` is configured).

## Failure modes and recovery
- Network outage: edge continues to enqueue to outbox; collector reconnects (MQTT reconnect warnings expected).
- Sensor error: invalid samples are skipped; state updated safely; EWMA continues.
- Disk full: outbox and collector logs may fail to write; monitor disk usage and rotate logs.
- Process crash: recommend systemd `Restart=on-failure` and periodic health checks.

## Clean shutdown
- SIGINT/SIGTERM handled in collector and stack runner; edge exits cleanly via normal process termination.

## Systemd example
- Unit/env templates:
  - `infra/systemd/semantic-uplink-stack.service`
  - `infra/systemd/semantic-uplink-stack.env.example`
- Recommended install (generates a unit pointing to your repo path):
  - `sudo ./scripts/install_systemd.sh`
- Manual install:
  - Copy the unit to `/etc/systemd/system/semantic-uplink-stack.service`
  - Copy env example to `/etc/semantic-uplink-stack.env` and edit values
  - `sudo systemctl daemon-reload && sudo systemctl enable --now semantic-uplink-stack.service`

## Notes for long-running RPi5
- Prefer log rotation to protect SD card endurance.
- Use loopback (`lo`) for tc profiling during dev to avoid breaking host connectivity.
- Validate broker persistence settings if storage endurance is a concern.
- AoI accuracy requires stable time; check `timedatectl` and freeze NTP during long runs if needed.
