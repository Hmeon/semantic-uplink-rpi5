# Entrypoints (generated)

## Edge daemon (RPi5)
- Minimal safe dev (mock temp + console UI):
  `python -m edge.edge_daemon --device-id dev1 --mode periodic --temp-enable --temp-backend mock --ui-enable --ui-kind console`
- Full device run (uses configs/device.yaml):
  `python -m edge.edge_daemon --device-id rpi5-01 --profile slow_10kbps --mode adaptive --run-dir artifacts/run_rpi5 --device-config configs/device.yaml --arms configs/policy.yaml`

## Collector
- `python -m collector.collector --run-dir artifacts/run1 --broker localhost --port 1883`

## Analyze / plots
- `python -m collector.analyze --input artifacts/run1 --out artifacts/analysis --diagnostic-plots --audit`

## Benchmarks
- Policy runtime timing: `python scripts/bench_policy_rpi5.py --steps 2000 --out artifacts/bench_policy_rpi5.csv`

## Stack (single Pi)
- `bash scripts/run_stack.sh`

## Experiments (scenario runner)
- `python experiments/run_scenarios.py --run-root artifacts/experiments --profiles slow_10kbps --modes periodic,fixed_tau,adaptive --no-mic --temp --with-collector`

## Link shaping
- `sudo python -m link.shaper.tc_profiles apply --iface lo --profile slow_10kbps`
