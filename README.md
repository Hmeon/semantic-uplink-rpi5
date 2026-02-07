<div align="center">
  <img src="docs/figma/semantic_logo.png" alt="semantic_logo" width="250" />
  <h1>AIoT Semantic Uplink (RPi5)</h1>
  <p><b>저속·손실 링크에서 “필요할 때만, 필요한 만큼만” 보내기 위한 엣지 전송 정책 실험 플랫폼</b></p>

  <p>
    <a href="https://github.com/Hmeon/semantic-uplink-rpi5/actions/workflows/ci.yaml"><img alt="CI" src="https://github.com/Hmeon/semantic-uplink-rpi5/actions/workflows/ci.yaml/badge.svg"></a>
    <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-blue">
    <img alt="Platform Raspberry Pi 5" src="https://img.shields.io/badge/Platform-Raspberry%20Pi%205-BC1142">
    <a href="edge/uploader/mqtt_publisher.py"><img alt="MQTT v3.1.1" src="https://img.shields.io/badge/MQTT-v3.1.1-3C5280"></a>
    <a href="pyproject.toml"><img alt="Status PoC" src="https://img.shields.io/badge/Status-PoC-2F855A"></a>
  </p>

  <p>
    <a href="#overview">개요</a> ·
    <a href="#architecture">구성</a> ·
    <a href="#quickstart">퀵스타트</a> ·
    <a href="#experiments">실험</a> ·
    <a href="#outputs">산출물</a> ·
    <a href="#docs">문서</a> ·
    <a href="#operations">운영</a>
  </p>

  <!-- TODO: 프로젝트 로고는 추후 추가 -->
</div>

> [!NOTE]
> 이 저장소의 README는 **퍼블릭 레포 기준으로 실제 존재하는 경로만 링크**한다. (실행 중 생성되는 산출물 폴더는 `.gitignore`에 의해 기본적으로 추적되지 않는다.)

---

<a id="overview"></a>
## 개요

이 프로젝트는 **라즈베리파이(RPi5) 엣지**에서 센서 시계열을 수집하고, **전송 정책(Policy)** 을 통해 “보내야 할 때만” 이벤트를 발행해 **MQTT로 업링크**하는 실험 플랫폼이다.

- **문제**: 저속/손실 링크(LoRa-like)에서는 “그냥 주기 전송”이 곧 병목이 된다.
- **핵심 아이디어**:  
  1) 센서 신호에서 **의미 있는 변화(잔차)** 를 잡고(EWMA),  
  2) 정책이 선택한 **(tau 임계치, kbits 양자화)** 조합으로,  
  3) QoS1 + outbox로 **전송 신뢰성을 유지**한다.
- **평가 관점**: AoI(정보 최신성), 전송률/대역 사용, 이벤트 적중도, 재구성 오차(MAE), outbox backlog 등.

> tc/netem 기반 링크 프로파일은 **IP 레벨 근사**다. LoRaWAN duty-cycle/ACK 같은 MAC 레벨 제약은 모델링하지 않는다. (`configs/link_profiles.yaml` 주석 참고)

---

<a id="architecture"></a>
## 구성

### 시스템 다이어그램

![Figma Architecture](docs/figma/architecture_ko.png)

### 데이터 흐름(요약)

1. **Edge(엣지)**  
   센서 → 예측/필터(EWMA 등) → 이벤트 트리거 → 양자화 → outbox 적재 → MQTT publish(QoS1)
2. **Broker(MQTT)**  
   Mosquitto 등 표준 브로커
3. **Collector(수집기)**  
   MQTT subscribe → 파케이(parquet) 로그 적재 → 분석/품질감사

---

<a id="components"></a>
## 구성요소(코드 기준)

| 구성 | 역할 | 주요 엔트리포인트 |
|---|---|---|
| Edge | 센서 수집/전송 정책/발행(outbox) | `python -m edge.edge_daemon` |
| Policy | periodic / fixed_tau / adaptive(LinUCB) | `edge/policy/*`, `configs/policy.yaml` |
| Collector | MQTT 수집 및 저장, 분석 | `python -m collector.collector`, `python -m collector.analyze` |
| Link Shaper | tc/netem 링크 프로파일 적용/해제 | `python -m link.shaper.tc_profiles` |
| Experiments | 프로파일×모드 매트릭스 자동 실행 | `python -m experiments.run_scenarios` |
| Stack | 단일 Pi 올인원 구동(브로커+엣지+수집) | `scripts/run_stack.sh` |

---

<a id="quickstart"></a>
## 퀵스타트

### 0) 파이썬 환경

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### 1) 단일 RPi “올인원” 실행

`scripts/run_stack.sh`는 다음을 한 번에 띄운다.

- 브로커: mosquitto (옵션, `BROKER_MODE=auto|subprocess|none`)
- Collector: MQTT subscriber + parquet sink
- Edge: 센서 → 정책 → outbox → MQTT QoS1

```bash
chmod +x scripts/*.sh
RUN_DIR=artifacts/live \
DEVICE_CONFIG=configs/device.yaml \
POLICY_ARMS=configs/policy.yaml \
./scripts/run_stack.sh
```

> `BROKER_MODE=auto/subprocess`를 쓰면 시스템에 `mosquitto` 바이너리가 있어야 한다. (없으면 `BROKER_MODE=none`으로 두고 외부 브로커를 지정)

### 2) 개별 프로세스로 실행

```bash
# terminal A: broker (별도로 띄우는 경우)
mosquitto -p 1883

# terminal B: collector
python -m collector.collector --config configs/device.yaml --out artifacts/live

# terminal C: edge
python -m edge.edge_daemon --device-config configs/device.yaml --policy-config configs/policy.yaml
```

---

<a id="experiments"></a>
## 실험

### 링크 프로파일 적용(tc/netem)

tc 조작은 권한이 필요하다(root 또는 CAP_NET_ADMIN).

```bash
python -m link.shaper.tc_profiles --help
```

프로파일 정의는 `configs/link_profiles.yaml`이 source of truth다.

### 시나리오 실행(프로파일 × 모드)

```bash
python -m experiments.run_scenarios --help
```

실험 러너는 tc 프로파일 적용 → edge/collector 실행 → 워밍업/런/쿨다운 → 정리 → 결과 폴더 고정까지를 자동화한다.

---

<a id="outputs"></a>
## 산출물(생성 파일)

기본적으로 다음 경로에 실행 산출물이 쌓인다(레포 추적 대상 아님).

- `artifacts/` : 라이브 실행/실험 로그(파케이, sqlite, 매니페스트 등)
- `results/` : 분석 결과(플롯/CSV/요약)

분석 관련:
- `python -m collector.analyze` : KPI 집계(예: `kpi_final.csv`, `kpi_verdict.json`) 및 플롯 생성
- `python -m collector.quality_audit` : 품질 감사(누락/지연/세그먼트 커버리지 등)

---

<a id="docs"></a>
## 문서(퍼블릭)

- 하드웨어 배선: [`docs/hardware.md`](docs/hardware.md)
- 도식/디자인 자료: [`docs/figma/README.md`](docs/figma/README.md)
- 지표 및 도표 규칙: [`docs/metrics/FIGURE_NAMING.md`](docs/metrics/FIGURE_NAMING.md)
- 엔터프라이즈 고도화 로드맵: [`docs/ROADMAP_ENTERPRISE.md`](docs/ROADMAP_ENTERPRISE.md)
- 변경 전 프리플라이트 체크리스트: [`docs/PROJECT_PREFLIGHT_CHECKLIST.md`](docs/PROJECT_PREFLIGHT_CHECKLIST.md)

---

<a id="operations"></a>
## 운영(systemd)

RPi에서 “상시 실행”이 필요하면 `infra/systemd/`를 사용한다.

- 서비스 유닛: `infra/systemd/semantic-uplink-stack.service`
- 환경파일 예시: `infra/systemd/semantic-uplink-stack.env.example`

예시(경로는 환경에 맞게 조정):

```bash
sudo cp infra/systemd/semantic-uplink-stack.service /etc/systemd/system/
sudo cp infra/systemd/semantic-uplink-stack.env.example /etc/semantic-uplink-stack.env
sudo systemctl daemon-reload
sudo systemctl enable --now semantic-uplink-stack.service
```

---

## 기여

- 코드 스타일/실험 재현성 규칙은 PR 단위로 유지한다.
- 행동 강령: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)

---

## 라이선스

- Apache-2.0: [`LICENSE`](LICENSE)

---

### 영어 문서

- English README: [`README._en.md`](README._en.md)
