# Entrypoints (generated)

## Edge daemon (RPi5)
- Minimal safe dev (mock temp + console UI):
  `python -m edge.edge_daemon --device-id dev1 --mode periodic --temp-enable --temp-backend mock --ui-enable --ui-kind console --base-topic edge`
- Full device run (uses configs/device.yaml):
  `python -m edge.edge_daemon --device-id rpi5-01 --profile slow_10kbps --mode adaptive --run-dir artifacts/run_rpi5 --device-config configs/device.yaml --arms configs/policy_adaptive_aiot.yaml --base-topic edge`

## Collector
- `python -m collector.collector --run-dir artifacts/run1 --broker localhost --port 1883 --base-topic edge`

## Analyze / plots
- `python -m collector.analyze --input artifacts/run1/logs --out artifacts/analysis --diagnostic-plots --audit`

## Benchmarks
- Policy runtime timing: `python scripts/bench_policy_rpi5.py --steps 2000 --out artifacts/bench_policy_rpi5.csv`

## Stack (single Pi)
- `bash scripts/run_stack.sh` (optional overrides: `BASE_TOPIC=edge`, `MOSQUITTO_LISTEN_HOST=127.0.0.1`, `MQTT_USERNAME=...`, `MQTT_PASSWORD=...`)

## Experiments (scenario runner)
- `python -m experiments.run_scenarios --run-root artifacts/experiments --profiles slow_10kbps --modes periodic,fixed_tau,adaptive --no-mic --temp --with-collector --base-topic edge`

## 3-policy sequence
- `PYTHON=$HOME/.venv/bin/python bash scripts/run_3h_sequence.sh`

## Link shaping
- `sudo python -m link.shaper.tc_profiles apply --iface lo --profile slow_10kbps`
