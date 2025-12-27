# AIoT Semantic Uplink (의미전송) on Raspberry Pi 5
**AI-Driven Semantic Uplink for Low-Bandwidth IoT**

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi%205-C51A4A?logo=raspberrypi&logoColor=white)
![Messaging](https://img.shields.io/badge/MQTT-Mosquitto-6CBD45?logo=eclipse-mosquitto&logoColor=white)
![Status](https://img.shields.io/badge/Status-PoC-brightgreen)
![CI](https://img.shields.io/badge/CI-ruff%20%7C%20pytest-blueviolet)

> KR: 저속·불안정 링크(LoRa 급 제약)에서 **필요한 순간·필요한 정보만** 보내도록,
> 엣지가 전송 정책(임계값 τ, 양자화 k)을 스스로 조절하는 **의미전송(Semantic Uplink)** 실험 플랫폼입니다.
> EN: A Raspberry Pi 5 semantic uplink that adapts **threshold (τ)** and **quantization (k)** to send
> **only information that matters** on constrained links, proving superiority over periodic sampling.

---

<a id="highlights"></a>
## Highlights
**KR**
- 이벤트 기반 전송: EWMA 잔차 |e|가 τ를 넘을 때만 전송
- 적응형 정책: LinUCB가 (τ, kbits) 팔을 선택해 링크 품질에 대응
- 신뢰성: MQTT QoS1 + Outbox(오프라인 큐) + 재전송 백오프
- 링크 제약 실험: tc/netem 프로파일로 저속/지연/손실을 재현
- 분석 파이프라인: AoI/MAE/Rate 집계 + 진단 플롯(ucb, heatmap, pareto)

**EN**
- Event-triggered sampling via EWMA residuals and adaptive thresholds
- LinUCB policy that selects (τ, kbits) arms under changing link conditions
- QoS1 + Outbox for offline reliability and retry safety
- Reproducible tc/netem link shaping for constrained-link experiments
- Built-in analysis pipeline with AoI/MAE/Rate and diagnostics

---

<a id="toc"></a>
## Table of Contents
- [Overview](#overview)
- [Goals and Success Criteria](#goals)
- [Deliverables](#deliverables)
- [Architecture](#architecture)
- [Data Flow](#data-flow)
- [Policies](#policies)
- [Data Model](#data-model)
- [Key Metrics](#key-metrics)
- [Quick Start (RPi5)](#quick-start)
- [Running Experiments](#running-experiments)
- [Time Sync (NTP/RTC)](#time-sync)
- [Artifacts and Outputs](#artifacts-outputs)
- [Analysis and Plots](#analysis)
- [Final Results (slow_10kbps, 3h)](#final-results)
- [Configuration](#configuration)
- [Hardware](#hardware)
- [UI and Controls](#ui-controls)
- [Repository Layout](#repo-layout)
- [Quality Gates](#quality-gates)
- [Security and Privacy](#security)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)
- [Appendix](#appendix)

---

<a id="overview"></a>
## Overview
**KR 요약**
- **문제**: 주기 전송은 저속/손실 링크에서 불필요한 트래픽을 늘리고 AoI를 악화시킵니다.
- **해결**: 잔차 기반 이벤트 전송 + 양자화 비트 조절로 “의미 있는 변화만” 전송합니다.
- **핵심**: LinUCB가 (τ, kbits)을 선택해 링크 상태에 적응합니다.

**EN Summary**
- **Problem**: Periodic sampling wastes bandwidth and degrades freshness (AoI) on constrained links.
- **Approach**: Event-triggered transmission using residual thresholds and adaptive quantization.
- **Core**: LinUCB chooses (τ, kbits) arms to balance rate, freshness, and error.

---

<a id="goals"></a>
## Goals and Success Criteria
- **Accuracy (primary)**: MAE_event mean increase <= 10% versus fixed_tau
  (event-based MAE)
- **Rate constraint**: >= 60% rate reduction versus periodic
- **Freshness (secondary)**: Adaptive AoI mean improves >= 15% versus fixed_tau while
  keeping rate increase <= 50% versus fixed_tau

These are guiding targets. The actual trade-off is reported per profile and per sensor.

---

<a id="deliverables"></a>
## Deliverables
1) **Executable pipeline**
   - Edge: `python -m edge.edge_daemon`
   - Collector: `python -m collector.collector --run-dir <dir>`
   - Analyzer: `python -m collector.analyze --input <logs> --out <dir>`
   - Link shaper: `python -m link.shaper.tc_profiles ...`
   - Batch runner: `python -m experiments.run_scenarios --help`

2) **Reproducible experiments**
   - Policy arms/reward/guardrails: `configs/policy*.yaml`
   - Device defaults: `configs/device.yaml`
   - Link profiles: `configs/link_profiles.yaml`

3) **Artifacts and reports**
   - Parquet logs + meta JSON in `artifacts/<run>/logs/`
   - Summary tables and plots in `results/<run>/`

4) **Final evaluation**
   - Per-profile rate/AoI/MAE tables
   - Baseline deltas and Pareto trade-offs
   - Run commands + config hashes for reproducibility

---

<a id="architecture"></a>
## Architecture
> Sensors → Edge (predict + policy) → MQTT uplink → constrained link (tc) → Broker → Collector → Analysis

<p align="center">
  <img src="docs/figma/architecture_ko.png" alt="Architecture Map" width="88%">
</p>

**Components**
- **Sensors**: USB mic RMS, DS18B20 temperature
- **Edge**: EWMA/AR1 predictor → residual → policy → quantization → uploader
- **Shaper**: tc/netem profiles (rate/delay/loss)
- **Broker/Collector**: Mosquitto + QoS1 dedup + Parquet sink
- **UI**: LCD/console + optional buttons

---

<a id="data-flow"></a>
## Data Flow
> Event is emitted only when |e| > τ. After broker Ack, the outbox entry is removed.

<p align="center">
  <img src="docs/figma/sequence_ko.png" alt="Event Sequence" width="88%">
</p>

1) **Sampling** → 2) **Predict & Residual** → 3) **Policy (τ, kbits)** →
4) **Quantize** → 5) **MQTT QoS1** → 6) **Collector (dedup + store)**

---

<a id="policies"></a>
## Policies
| Policy | CLI mode | Behavior | Use case |
| --- | --- | --- | --- |
| Periodic | `--mode periodic` | Send every sample | Baseline (upper bound on rate) |
| Fixed τ (ETS) | `--mode fixed_tau` | EWMA + threshold trigger | Deterministic trade-off |
| Adaptive (LinUCB) | `--mode adaptive` | Contextual bandit selects (τ, kbits) | Link-aware optimization |

### LinUCB details
- **Context features**: AoI (edge interval + ACK delay EWMA), residual, residual variance, loss EWMA (nack/timeout), outbox queue length
- **Reward**: weighted AoI + MAE + Rate (normalized by scales)
- **Guardrails**: optional AoI/MAE thresholds force safer arms
- **Diagnostics**: UCB terms, reward decomposition, timing metrics (enable in policy config)

---

<a id="data-model"></a>
## Data Model
**Event (sensor event)**
```json
{
  "ts": "2025-11-03T10:21:34.512Z",
  "seq": 10231,
  "device_id": "rpi5a",
  "sensor": "mic",
  "val": -42.1,
  "pred": -43.3,
  "res": 1.2,
  "tau": 3.0,
  "kbits": 8,
  "aoi_ms": 1200,
  "profile": "slow_10kbps",
  "policy": "linucb#5",
  "event_reason": "threshold"
}
```

**PolicyDecision (adaptive only)**
```json
{
  "ts": "2025-11-03T10:21:34.480Z",
  "device_id": "rpi5a",
  "state_aoi": 1200.0,
  "state_res": 1.2,
  "state_res_var": 0.6,
  "state_loss": 0.0,
  "state_q_len": 5,
  "tau": 3.0,
  "kbits": 8,
  "reward": -1.42
}
```

---

<a id="key-metrics"></a>
## Key Metrics
**Primary (paper-grade)**
- **Rate [B/s]**: broker-side bytes per second (lower is better)
- **AoI mean / p95 [ms]**: freshness at receiver (lower is better)
- **MAE_event mean / p95**: event-log |res| error (lower is better)

**Secondary (diagnostics)**
- `kbits_mean`, `event_rate_hz`, `send_ratio`
- `rx_delay_*` (requires time sync)
- LinUCB diagnostics: `ucb_exploitation`, `ucb_exploration`, reward terms, arm distribution

> Note: MAE is event-based. Full-signal MAE requires raw logging or reconstruction pipeline.

---

<a id="quick-start"></a>
## Quick Start (RPi5)
```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y mosquitto mosquitto-clients iproute2 python3-venv python3-dev build-essential libportaudio2

python3 -m venv .venv && source .venv/bin/activate
pip install -e .[analysis,hw]
# dev/test: pip install -e .[dev,analysis,hw]
# fallback: pip install -r requirements.txt

sudo systemctl enable mosquitto && sudo systemctl start mosquitto
```

**Parquet note**
- Collector writes Parquet by default. If `pyarrow` is missing or fails to import, it falls back to CSV.
- On Python 3.13, ensure `pyarrow>=18.0`.

---

<a id="running-experiments"></a>
## Running Experiments

### Option A: Single-Pi all-in-one
```bash
bash scripts/run_stack.sh
```
Systemd install (optional):
```bash
sudo bash scripts/install_systemd_stack.sh
sudo systemctl start semantic-uplink-stack
```

### Option B: Manual run (Terminal A/B)
**Terminal A (Collector)**
```bash
python -m collector.collector --run-dir artifacts/run1
```

**Terminal B (Edge)**
```bash
python -m edge.edge_daemon \
  --device-id rpi5a --profile slow_10kbps \
  --mode periodic \
  --mic-enable --temp-enable \
  --ui-enable --ui-kind lcd1602 --ui-address 0x27
```

### Option C: 3-policy 3-hour quality benchmark (automated)
The script runs **Periodic → Fixed τ → Adaptive** sequentially.
It applies tc/netem and (optionally) freezes NTP for stable AoI.
By default, NTP freeze is enabled (`NTP_FREEZE=1`).

```bash
export IFACE=lo
export PROFILE=slow_10kbps
export W1_PATH="$(ls /sys/bus/w1/devices/28-*/w1_slave | head -n 1)"
export RUN_SECONDS=10800
export RUN_GRACE=30
export ADAPTIVE_ARMS=configs/policy_adaptive_aiot.yaml

# use venv python for parquet
PYTHON=$HOME/.venv/bin/python bash scripts/run_3h_sequence.sh
```

**Resume from a stage**
```bash
START_FROM=fixed_tau bash scripts/run_3h_sequence.sh
```

**Disable NTP freeze**
```bash
NTP_FREEZE=0 bash scripts/run_3h_sequence.sh
```

---

<a id="time-sync"></a>
## Time Sync (NTP/RTC)
AoI uses `t_recv_ns - ts` and is **sensitive to time steps**.
For objective AoI, ensure time is stable before each run.

**Pre-run checks**
```bash
timedatectl status
timedatectl timesync-status
```

**Freeze NTP during run (optional)**
```bash
sudo systemctl stop systemd-timesyncd
# run experiments
sudo systemctl start systemd-timesyncd
```

**RTC (optional, DS3231)**
```bash
python -m edge.edge_daemon \
  --device-id rpi5a --profile slow_10kbps \
  --mic-enable --temp-enable \
  --rtc-enable --rtc-bus 1 --rtc-address 0x68 \
  --rtc-drift-guard 2.0 --rtc-resync 600
```

---

<a id="artifacts-outputs"></a>
## Artifacts and Outputs
**Collector logs**
- `artifacts/<run>/logs/events_*.parquet`
- `artifacts/<run>/logs/decisions_*.parquet` (adaptive only)
- `artifacts/<run>/logs/markers_*.parquet`
- `artifacts/<run>/logs/collector_meta.json`

**Outbox**
- `artifacts/<run>/outbox.sqlite`

---

<a id="analysis"></a>
## Analysis and Plots
Typical analysis command:
```bash
python -m collector.analyze \
  --input artifacts/run1/logs \
  --out results/run1 \
  --baseline-policy periodic \
  --save-parquet --plots --paper-plots --diagnostic-plots --ucb-timeseries \
  --pareto-p95 --entropy-smooth-window 60 --arm-top-n 0 --audit
```

Outputs:
- `results/run1/metrics_summary.csv`
- `results/run1/metrics_by_run.csv`
- `results/run1/metrics_vs_periodic.csv`
- `results/run1/report.md`
- `results/run1/figs/*` (plots and diagnostic panels)

---

<a id="final-results"></a>
## Final Results (slow_10kbps, 3h)
**Dataset**
- Inputs: `artifacts/slow10_periodic_3h_B/logs`, `artifacts/slow10_fixed_3h_B/logs`,
  `artifacts/slow10_linucb_3h_B/logs`
- Output: `results/final_compare_3h_slow_10kbps`
- Baseline: `periodic`
- Note: MAE is event-based (`res`), AoI uses `t_recv_ns`. (B logs are controlled/synthetic for
  closed-environment comparison.)

**Summary (mean values)**
| sensor | periodic (Rate/AoI/MAE) | fixed_tau | adaptive |
| --- | --- | --- | --- |
| mic_rms | 524.0 B/s / 1536.8 ms / 0.052 | 26.0 B/s / 6287.7 ms / 0.052 | 37.4 B/s / 4745.2 ms / 0.053 |
| temp | 255.0 B/s / 1802.9 ms / 0.036 | 23.7 B/s / 6952.2 ms / 0.036 | 27.4 B/s / 5756.9 ms / 0.035 |

**Evaluation vs goals**
- Rate reduction (>=60% vs periodic): PASS (~89-95% reduction)
- MAE change (<=10% vs fixed_tau): PASS (mic +1.9%, temp -2.8%)
- AoI improvement (>=15% vs fixed_tau): PASS (~17-25% improvement)
- Rate increase (<=50% vs fixed_tau): PASS (mic +44%, temp +16%)
- Trade-off: Adaptive improves AoI vs fixed_tau with moderate rate increase while keeping MAE stable.
- Detailed write-up: `docs/final/FINAL_EVALUATION.md`

---

<a id="configuration"></a>
## Configuration
**Policy arms and reward**: `configs/policy.yaml`, `configs/policy_adaptive_aiot.yaml` (AIoT balance), or `configs/policy_adaptive_quality.yaml` (quality-first)
```yaml
arms:
  - { tau: 1.5, kbits: 6 }
  - { tau: 3.0, kbits: 8 }
  - { tau: 6.0, kbits: 10 }
reward:
  alpha: 1.0
  beta: 1.0
  gamma: 0.5  # AoI, MAE, Rate weights
safety:
  aoi_max_ms: 5000
  mae_max: 2.0
```

**Device defaults**: `configs/device.yaml`
```yaml
device_id: rpi5a
sensors:
  mic:  { frame_ms: 100, samplerate: 16000, normalize: true }
  temp: { period_hz: 1 }
ui:    { enabled: true, backend: "lcd" }  # lcd | console
mqtt:  { host: localhost, port: 1883, base_topic: "edge" }
```

**Link profiles**: `configs/link_profiles.yaml`
```yaml
profiles:
  slow_10kbps:
    rate_kbit: 10
    delay_ms: 300
    jitter_ms: 50
    loss_pct: 3.0
  cellular_var:
    rate_kbit: null
    low_kbit: 50
    high_kbit: 200
    var_default_period_s: 30
    delay_ms: 120
    jitter_ms: 80
    loss_pct: 2.0
```

---

<a id="hardware"></a>
## Hardware
- **Controller**: Raspberry Pi 5 (8GB)
- **Sensors**: DS18B20 (1 Hz), USB mic RMS (100 ms frames)
- **UI**: 1602 I2C LCD (PCF8574, addr 0x27)
- **Buttons**: mode (BCM 17), profile (BCM 27), marker (BCM 22)
- **Optional**: DS3231 RTC, buzzer (BCM 18)

**Audio privacy**
- Raw audio is never stored or transmitted. Only RMS statistics are used.

**Audio calibration**
- For reproducible measurements, keep Mic gain fixed and disable AGC across all runs.

---

<a id="ui-controls"></a>
## UI and Controls
- **LCD 1602**: `--ui-enable --ui-kind lcd1602 --ui-address 0x27`  
  Shows mode/profile, rate, AoI, MAE, and queue status.
- **Console UI**: `--ui-enable --ui-kind console` for headless debugging.
- **Buttons (optional)**: `--buttons-enable`  
  - Button1: policy cycle (Periodic → Fixed τ → Adaptive)  
  - Button2: link profile cycle (SLOW_10K → DELAY_LOSS → CELLULAR_VAR)  
  - Button3: marker publish (`marker/<device_id>`) for timeline alignment

---

<a id="repo-layout"></a>
## Repository Layout
```text
.
├── common/         # shared schemas, metrics, MQTT helpers
├── edge/           # sensors → prediction → policy → uploader → UI
├── collector/      # MQTT subscriber, persistence, analysis tools
├── link/           # tc/netem profiles and helpers
├── configs/        # device, policy, link YAMLs
├── stack/          # single-Pi supervisor (broker+collector+edge)
├── scripts/        # launchers and automation scripts
├── experiments/    # batch scenario runner
├── tests/          # unit & integration tests
├── docs/           # diagrams and references
└── requirements.txt, pyproject.toml
```

---

<a id="quality-gates"></a>
## Quality Gates
- Topic/schema validation passes, QoS1 dedup ok
- Outbox recovery with 0 loss
- 3 profiles × 3 modes × ≥3 repeats recommended
- `metrics_summary.csv` and `metrics_vs_periodic.csv` show improvement trends
- `figs/` includes Pareto and diagnostic panels (UCB terms)

---

<a id="security"></a>
## Security and Privacy
- MQTT TLS and auth are recommended when leaving localhost
- QoS1 can create duplicates; dedup is seq-based in collector
- No raw audio is stored or transmitted

---

<a id="troubleshooting"></a>
## Troubleshooting
- **CSV instead of Parquet**: `pyarrow` missing or failing to import. Use the venv and install extras.
- **No events collected**: collector not running, broker down, or wrong MQTT host/port.
- **Mic not detected**: check `arecord -l`, `--mic-arecord-device`, and ALSA permissions.
- **AoI looks abnormal**: time sync is unstable; check `timedatectl` and NTP freeze.
- **tc/netem apply fails**: requires root or `CAP_NET_ADMIN`; use loopback (`lo`) during dev.

---

<a id="contributing"></a>
## Contributing
```bash
pip install -e .[dev,analysis,hw]
ruff check .
pytest -q
```

---

<a id="license"></a>
## License
TBD

---

<a id="appendix"></a>
## Appendix
<p align="center">
  <img src="docs/figma/pipeline_ko.png" alt="Pipeline Diagram" width="86%">
</p>
