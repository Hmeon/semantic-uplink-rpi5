# 프로젝트 목표/목적/배경 (Semantic Uplink · RPi5)

## 1) 배경: 왜 “Semantic Uplink”인가?
RPi5 같은 엣지 디바이스는 센서 데이터를 “항상(full stream)” 올리기 어렵다. 실제 필드(특히 LoRa/저속/고지연/손실)에서는
uplink 비용(대역/전력/시간/충돌)이 병목이 되고, 결국 **중요한 변화(meaningful change)** 를 놓치지 않으면서도 **전송량을 크게 줄이는**
전략이 필요하다.

이 프로젝트는 **전체 시계열을 올리는 것**이 아니라, 수신기(receiver)가 재구성 가능한 형태로 **의미 있는 이벤트(event)** 만 올리는
“Semantic Uplink” 파이프라인을 PoC로 구현한다.

---

## 2) 목표(Goal): “전송 최소화 + 의미 보존”
핵심 목표는 아래 2가지를 동시에 만족하는 것이다.

1) **Efficiency**: periodic(항상 전송) 대비 uplink 전송률을 크게 줄인다.  
2) **Semantic Quality/Coverage**: 줄인 만큼 의미(품질/이상 구간 커버리지)를 잃지 않는다.

여기서 의미(semantic)는 “사람이 의미 있다고 보는 변화/이상 구간”을, 구현 상으로는 **예측오차(residual) 기반 변화**로 근사한다.

---

## 3) 구현 배경: 파이프라인 구성

### 데이터 흐름(요약)
- **Sensor**: mic RMS(`mic_rms`) / temperature(`temp`)
- **Prediction**: EWMA 1-step predictor → residual `res = |x - pred|`
- **Policy**: 전송 여부/정밀도(압축 수준)을 정책이 결정
- **Uplink**: MQTT(QoS1) + Outbox(SQLite)로 신뢰성/재전송 처리
- **Collector/Analyzer**: 로그 수집(중복 제거) + 지표/플롯 + KPI PASS/FAIL

### 정책(Policy) 정의(고정)
- `periodic`: 가장 빈번한 전송(참조 스트림)
- `fixed_tau`: 고정 임계값 `tau` 기반 전송(수동 베이스라인)
- `adaptive`: LinUCB 기반 적응형 정책(프로젝트 목표 대상)

---

## 4) 평가 원칙: “공정 비교”의 조건
정책 비교가 의미 있으려면 아래를 **가능한 동일하게** 맞춰야 한다.

- 동일한 샘플링 조건(센서 주기/프레임)
- 동일한 링크 프로파일(`configs/link_profiles.yaml`) 또는 동일한 네트워크 조건
- 동일한 run window(같은 시간 구간)
- QoS1 중복 제거 후 분석(`seq` 기반)
- 가능하면 송신/수신 시간 기준이 일관되도록 시간 동기화(분석 시 `t_recv_ns` 기반 AoI 사용)

---

## 5) 최종 KPI(Strict PASS/FAIL): “adaptive”만 평가
최종 성공 조건은 `docs/specs/architecture.md`에 정의된 **strict KPI**를 따른다.
평가 범위는 강제적으로 **profile × sensor** 단위이며, 한 row라도 FAIL이면 프로젝트 FAIL이다.

KPI는 baseline 두 가지를 동시에 사용한다.
- `periodic` 대비: 1차 목표(전송률 절감)
- `fixed_tau` 대비: guardrail(전송/품질/신선도/커버리지)

> 입력에 `adaptive` 정책 로그가 없으면 KPI는 **FAIL이 아니라 SKIP** 이다  
> (`collector.analyze`가 `kpi_verdict.json`에 reason을 포함해 기록).

---

## 6) 최종 적응형 모드(adaptive)의 설계(현재 레포 구현 기준)

### 6.1 액션 공간(action)
`adaptive`는 매 샘플마다 `(tau, kbits)` arm을 선택한다. arm은 `configs/policy*.yaml`에 정의된다.

- `tau`: EWMA residual threshold (sensor units)
- `kbits`: quantization bit width (메타/정밀도 노브)

### 6.2 컨텍스트(context)
LinUCB 컨텍스트 벡터는 (바이어스 포함) 6차원이다.
`[1, aoi_norm, |res|_norm, resvar_norm, loss, qlen_norm]`

여기서
- `aoi_norm`: ACK 지연을 반영한 AoI 기반 정규화
- `loss`: outbox 기반 손실/재시도 추정치
- `qlen_norm`: outbox pending(혼잡)을 log-scale로 정규화

### 6.3 보상(reward): 목표-제약(guardrail) 스타일
이 프로젝트의 KPI 철학(전송 최소 + 제약 만족)에 맞춰,
Rate는 항상 패널티로 두고, AoI/MAE는 **guardrail 초과분만** 패널티로 반영한다.
(“계속 AoI를 최소화”가 아니라 “AoI가 나빠지지 않게” 유지하는 형태)

### 6.4 안전가드(safety): AoI/MAE 강제 팔 선택
- MAE(residual) 제한은 항상 안전가드로 작동한다.
- AoI 제한은 `safety_force_emit_on_aoi=true`일 때만 “강제”로 취급된다.
  - 필요 시 `aoi_safe_arm`을 별도로 지정해, AoI-가드와 MAE-가드가 같은 safe arm을 공유하지 않게 할 수 있다.

---

## 7) 문서(논문)와의 관계: 무엇을 “차용”하고 무엇을 “그대로” 쓰지 않는가
`docs/paper_02~05_project_adapted.md`는 “분산 bandit/모드 전환/제약 최적화(라그랑지안)” 관점을 제공하지만,
현재 프로젝트는 기본적으로 단일 엣지(혹은 소수 디바이스) uplink PoC이므로,
논문에서의 통신비용(학습 파라미터 교환) 최소화 같은 항목은 **그대로 대응되지 않는다**.

대신 프로젝트에 직접 유용한 부분은 다음이다.
- **제약을 KPI로 명시하고, 제약을 만족시키는 방향으로 정책을 구조화**(safe arms/guardrails)
- **모드/정책 전환을 “목표 + 제약” 프레임으로 다루기**(rate region/dual variable 직관)
- **비정상(non-stationary) 환경에서의 강건성**(링크 스트레스 신호 포함, outbox 기반 관측치)

