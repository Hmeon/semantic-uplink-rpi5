# Codex Quick Manual (Start Here)

이 문서는 **Codex CLI(또는 에이전트형 코딩 도구)** 로 `semantic-uplink-rpi5`를 열었을 때, “프로젝트 전체를 1~3분 안에 파악하고 바로 실행/디버깅/수정”할 수 있도록 만든 **운영 메뉴얼**입니다.

---

## 0) TL;DR (가장 빠른 확인 루트)

### 코드 품질(로컬)
```bash
python -m pytest -q
python -m ruff check .   # ruff가 PATH에 없다면: .venv/bin/ruff 또는 .venv/Scripts/ruff.exe
```

### 하드웨어 없이 파이프라인 검증(DEV PC)
1) MQTT 브로커 준비(로컬/원격 아무거나)
2) Collector 실행
3) Edge 실행(온도 mock + console UI)
4) Analyze 실행

```bash
# 1) Collector (run-dir 필수)
python -m collector.collector --run-dir artifacts/run1

# 2) Edge (TEMP mock만으로도 동작 확인 가능)
python -m edge.edge_daemon \
  --device-id dev1 --profile slow_10kbps \
  --mode periodic \
  --temp-enable --temp-backend mock \
  --ui-enable --ui-kind console

# 3) Analyze
python -m collector.analyze --input artifacts/run1/logs --out results/run1
```

---

## 1) 이 프로젝트는 무엇을 “증명/데모”하려고 하나?

### 목표(핵심 주장)
저속/손실/지연 링크에서 **주기 전송(Periodic)** 대비,
- **전송량(Rate)** 을 크게 줄이면서
- **신선도(AoI)** 를 개선하고
- **오차(MAE)** 는 허용 범위 내로 유지하는

“의미 기반(semantic) 이벤트 전송 + (τ,k) 적응”을 **실험적으로 입증**하는 것이 목표입니다.

### 핵심 아이디어
- 센서 값 `x`에 대해 예측 `pred`를 만들고 잔차 `|x - pred| = |e|`가 **임계값 τ** 보다 클 때만 이벤트를 보냅니다.
- 전송 시 센서 값을 **kbits로 균일 양자화**하여 payload 크기를 줄입니다.
- (τ, kbits) 조합을 **컨텍스트 밴딧(LinUCB)** 으로 선택합니다.
  - 컨텍스트: AoI, 잔차, 잔차 분산, outbox 큐 길이, (추후) 링크 손실 추정치 등
  - 보상: `r = -(α·AoI + β·MAE + γ·Rate)` (정규화 후 음의 가중합)
  - 가드레일: AoI/MAE가 임계 초과하면 “보수(safe) 팔”로 즉시 전환

### 신뢰성(실제 IoT에서 중요한 부분)
- MQTT QoS1만으로는 “오프라인/재부팅/일시 단절”에서 유실 위험이 있어 **SQLite Outbox**를 둡니다.
- Publisher는 Outbox에서 메시지를 꺼내 발행하고 PUBACK을 받은 뒤 삭제합니다.
- ACK 타임아웃/재시도/백오프를 사용합니다.

---

## 2) 저장소 구조(한 눈에 보기)

### 실행 진입점(Entry points)
- Edge: `python -m edge.edge_daemon`
- Collector: `python -m collector.collector`
- Analyzer: `python -m collector.analyze`
- Link shaper: `python -m link.shaper.tc_profiles`
- Scenario runner: `python -m experiments.run_scenarios`

### 디렉터리 역할
- `edge/`: 센싱 → 예측 → 정책 → 양자화 → 업로더(UI 포함)
- `collector/`: MQTT 구독/중복 제거/저장/분석 도구
- `link/`: tc/netem 링크 셰이핑
- `common/`: 스키마/양자화/시간/지표/Discord webhook
- `configs/`: 정책 arms YAML 등 (현재 `policy.yaml`만 런타임에서 사용)
- `scripts/`: 편의 실행 스크립트(리눅스/라즈베리파이용)
- `tests/`: 유닛/통합(placeholder)

---

## 3) “무엇을 실행하면 무엇이 생기나?” (산출물/아티팩트)

### Collector(run-dir) 기준
Collector는 `--run-dir <dir>` 아래에 폴더를 만들고 결과를 저장합니다.
- `artifacts/<run_id>/logs/events_*.parquet` (rotated; legacy: `events.parquet`)
- `artifacts/<run_id>/logs/decisions_*.parquet` (rotated; legacy: `decisions.parquet`)
- `artifacts/<run_id>/logs/markers_*.parquet` (rotated; legacy: `markers.parquet`)
- `artifacts/<run_id>/logs/collector_meta.json`

### Edge(run-dir) 기준
Edge는 기본적으로 `artifacts/<ts>_<device_id>/` 형태의 run-dir를 만들고 outbox DB를 둡니다.
- `artifacts/<run_id>/outbox.sqlite`

### Analyze(out-dir) 기준
- `results/<run_id>/metrics_summary.csv` (profile×policy×sensor 요약; `mean±std` 포함)
  - 주요 컬럼: `rate_Bps`, `aoi_mean_ms/aoi_p95_ms`, `mae_event_mean/mae_event_p95`, `kbits_mean`
  - 보조 컬럼: `event_rate_hz`, `send_ratio`(SEQ gap 기반), `rx_delay_*`(t_recv_ns가 있을 때)
- `results/<run_id>/metrics_by_run.csv` (run 단위 지표; 리플리케이트 확인용)
- `results/<run_id>/metrics_vs_periodic.csv` (baseline 대비 변화량/개선율)
- `results/<run_id>/report.md` (표 + baseline 비교 + (옵션) figures 임베드)
- (옵션) `results/<run_id>/figures/*.png` (막대/파레토 이미지; `matplotlib` 설치 시 생성)
- (paper-plots, 기본 ON) `results/<run_id>/figures/paper_*.png` (reward/regret/θ/히트맵/타임라인 등 논문용 추가 플롯)
  - 비활성화: `python -m collector.analyze ... --no-paper-plots`
  - 튜닝: `--reward-window`, `--action-bins`, `--top-actions`, `--cellular-var-period-s`, `--policy-config`
- (옵션) `--save-parquet` → `metrics_summary.parquet`

---

## 4) 개발 환경별 실행 가이드

### 4.1 DEV PC(하드웨어 없음)
권장 전략은 “TEMP mock + console UI”로 파이프라인을 먼저 검증하는 것입니다.

- Edge: `--temp-enable --temp-backend mock --ui-enable --ui-kind console`
- Mic는 하드웨어/오디오 장치 의존도가 크므로 CI/PC에서는 보통 끕니다.

**브로커**
- 로컬에 Mosquitto가 있으면 `--broker localhost --port 1883`
- 없다면, 사내 MQTT 브로커 주소를 사용하거나 Docker로 구동하세요.

### 4.2 Raspberry Pi 5(실장)
필수 패키지(예시):
```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y mosquitto mosquitto-clients iproute2 python3-venv python3-dev build-essential libportaudio2
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl enable mosquitto && sudo systemctl start mosquitto
```

**온도 센서(DS18B20)**
- Raspberry Pi OS에서 1‑Wire 활성화 필요(`dtoverlay=w1-gpio,gpiopin=4`)
- Edge 실행 시 `--temp-enable --temp-backend w1` 또는 `auto`

**LCD/버튼**
- I2C 활성화 필요(`/dev/i2c-1`)
- LCD: `--ui-enable --ui-kind lcd1602 --ui-address 0x27`
- Buttons: `--buttons-enable`

---

## 5) 링크 셰이핑(tc/netem) 사용법

`link/shaper/tc_profiles.py`는 **root 권한이 필요**합니다.

```bash
# 적용
sudo python -m link.shaper.tc_profiles apply --iface eth0 --profile slow_10kbps
# (LoRa-like 근사)
# sudo python -m link.shaper.tc_profiles apply --iface eth0 --profile lora_sf12

# 해제
sudo python -m link.shaper.tc_profiles clear --iface eth0

# 상태
sudo python -m link.shaper.tc_profiles status --iface eth0
```

개발 중에는 네트워크를 망가뜨리지 않도록 `--iface lo`(loopback)로 테스트하는 편이 안전합니다.

---

## 6) 시나리오 매트릭스 실행(experiments)

`experiments/run_scenarios.py`는 “프로파일 × 모드” 반복 실행을 자동화합니다.

```bash
python -m experiments.run_scenarios \
  --device-id rpi5a \
  --iface eth0 \
  --run-root artifacts/experiments \
  --modes periodic,fixed_tau,adaptive \
  --profiles slow_10kbps,delay_loss,cellular_var \
  --repeats 3 \
  --with-collector

# 더 열악한 링크(LoRa-like)도 추가하려면:
#   --profiles slow_10kbps,delay_loss,cellular_var,lora_sf10,lora_sf12

# 종료 후 전체 결과를 한 번에 분석(평균/표준편차 + baseline 비교 + figures)
python -m collector.analyze \
  --input artifacts/experiments \
  --out results/experiments \
  --baseline-policy periodic
```

---

## 7) 디버깅 포인트(어디를 보면 빨리 해결되나?)

### Edge 쪽
- 정책/예측/이벤트 생성: `edge/policy/runtime.py`, `edge/predict/ewma.py`
- LinUCB: `edge/policy/linucb.py`
- Outbox: `edge/uploader/outbox.py`
- Publisher: `edge/uploader/mqtt_publisher.py`
- UI 표시: `edge/ui/status.py`, `edge/ui/lcd.py`, `edge/ui/buttons.py`

### Collector 쪽
- 수신/중복 제거: `collector/collector.py` (`edge/{device}/{sensor}/event` 토픽)
- 분석/요약/Discord: `collector/analyze.py`, `common/discord_webhook.py`

### 데이터/아티팩트 확인
- Parquet 확인:
```bash
python - <<'PY'
import pandas as pd
from pathlib import Path

logs = Path("artifacts/run1/logs")
parts = sorted(logs.glob("events_*.parquet")) or [logs / "events.parquet"]
df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
print(df.head())
print(df.columns)
PY
```

### 흔한 문제
- `tc` 적용 실패: root 권한 필요 + 인터페이스 이름 확인(`ip link`)
- 오디오 입력 실패: `arecord` 설치/장치 선택, 또는 `--mic-enable` 끄기
- Windows 콘솔 한글/특수문자 깨짐: `chcp 65001` 또는 `PYTHONIOENCODING=utf-8`

---

## 8) 개발 체크리스트(에이전트/사람 공통)

### 변경 전/후 확인
- `python -m pytest -q` 통과
- `ruff check .` 통과
- README의 커맨드/스크립트가 실제 CLI와 일치하는지 확인

### 보안/운영
- 브로커/Discord webhook 등 민감 정보는 커밋 금지
- `data/`, `logs/`, `artifacts/`, `results/` 등 산출물은 gitignore로 관리
