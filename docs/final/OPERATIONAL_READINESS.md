# Operational readiness

## Install (RPi5)
- Create venv and install deps:
  - `python3 -m venv .venv`
  - `. .venv/bin/activate`
  - `pip install -e .[dev,analysis,hw]`

## Run modes
- Edge daemon:
  - `python -m edge.edge_daemon --device-id rpi5-01 --profile slow_10kbps --mode adaptive --run-dir /var/lib/semantic-uplink/run --device-config configs/device.yaml --arms configs/policy.yaml`
- Collector:
  - `python -m collector.collector --run-dir /var/lib/semantic-uplink/run --broker localhost --port 1883`
- Analyze:
  - `python -m collector.analyze --input /var/lib/semantic-uplink/run --out /var/lib/semantic-uplink/analysis --diagnostic-plots --audit`

## Logging and rotation
- Logging is configured via `common/logging_setup.py` with optional rotating file handler.
- Use `--log-file` and `--log-max-bytes`/`--log-backup-count` on CLI where available.
- Diagnostics logs are gated by `diagnostics.enabled` in `configs/policy.yaml` (default false).

## Failure modes and recovery
- Network outage: edge continues to enqueue to outbox; collector reconnects (MQTT reconnect warnings expected).
- Sensor error: invalid samples are skipped; state updated safely; EWMA continues.
- Disk full: outbox and collector logs may fail to write; monitor disk usage and rotate logs.
- Process crash: recommend systemd `Restart=on-failure` and periodic health checks.

## Clean shutdown
- SIGINT/SIGTERM handled in collector and stack runner; edge exits cleanly via normal process termination.

## Systemd example
- See `docs/final/systemd/semantic-uplink-stack.service`.
- Install:
  - `sudo cp docs/final/systemd/semantic-uplink-stack.service /etc/systemd/system/`
  - `sudo systemctl daemon-reload`
  - `sudo systemctl enable --now semantic-uplink-stack.service`

## Notes for long-running RPi5
- Prefer log rotation to protect SD card endurance.
- Use loopback (`lo`) for tc profiling during dev to avoid breaking host connectivity.
- Validate broker persistence settings if storage endurance is a concern.
