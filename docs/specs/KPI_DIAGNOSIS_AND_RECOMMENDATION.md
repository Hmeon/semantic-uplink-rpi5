# KPI 실패 원인 진단 및 권고 (KPI vs LinUCB 개선 우선순위)

본 문서는 `semantic-uplink-rpi5` 프로젝트를 “현장 실측 직전 단계”의 synthetic 시나리오(A/B) 결과를 바탕으로 해부/분석하여,
KPI 실패가 **KPI 정의의 문제인지** 또는 **LinUCB(적응형 모드) 개발(로직 개선)의 문제인지**를 정성/정량 근거로 판단하고,
필요한 조치(개발/조정)를 정리합니다.

> 핵심 결론(요약)
> - KPI4(AnomalySegmentRecall) 실패는 **KPI 자체의 문제라기보다**, 적응형 모드가 “짧은 이상구간”을 학습만으로 1-run 내 안정적으로 커버하기 어려운 **정책/런타임 설계 문제**가 주원인입니다.
> - “fixed_tau로의 회귀(= safe-arm 강제)” 같은 가드레일은 정책 차이를 무의미하게 만들 수 있으므로 지양해야 합니다.
> - 대신 **세그먼트당 1회 emit 보장(옵션)**은 fixed_tau 동치가 아니며(arm 강제 없음), KPI4를 안정화하면서도
>   시나리오 B(변동 큼)에서 fixed_tau 대비 **더 낮은 rate**를 달성할 수 있음을 정량으로 확인했습니다.

---

## 1) 프로젝트 목표/목적/로직(해부)

### 1.1 목표(Goal)
제한된 uplink 환경에서 센서 전체 스트림을 보내는 대신,
**의미 있는 변화(semantic change)**에 대한 정보만 보내서 **전송량을 크게 줄이되**,
수신 측 품질/커버리지/신선도(AoI)를 일정 수준 이상 유지하는 것이 목표입니다.

### 1.2 시스템 로직(핵심 경로)
- Edge: 센서 샘플 → EWMA 1-step predictor → residual(`res=|x-pred|`) 계산
- Policy:
  - `periodic`: 모든 샘플 전송(상한 baseline)
  - `fixed_tau`: `res > tau` 또는 heartbeat로 전송(사람이 정한 품질 기준)
  - `adaptive(LinUCB)`: 컨텍스트( AoI/잔차/링크/큐/세그먼트 힌트 등 )를 보고 `(tau,kbits)` arm 선택
- Uplink: MQTT(QoS1) + outbox/queue 모델링
- Collector/Analyzer: 수신 로그(중복 제거) → AoI/Rate/Recon/Coverage 계산 → KPI PASS/FAIL 산출

### 1.3 KPI(Strict PASS/FAIL)
`docs/final/FINAL_EVALUATION.md`, `docs/specs/architecture.md` 기준:
1) **Efficiency(Primary)**: `Rate_improvement_vs_periodic >= 85%`
2) **Rate guard(vs fixed_tau)**: `Rate_improvement_vs_fixed_tau >= -10%`
3) **Recon quality guard**: `recon_mae_p95_improvement_vs_fixed_tau >= -10%`
4) **Coverage guard**: `AnomalySegmentRecall >= 0.90`
5) **Freshness guard**: `AoI_p95_improvement_vs_fixed_tau >= -10%`

KPI4(coverage)의 의미:
- anomaly segment는 periodic baseline에서 `|res| > tau_ref(fixed_tau)`가 **연속**된 구간(길이>=2 샘플)으로 정의
- adaptive는 각 segment에서 **최소 1회**라도 전송(hit)하면 해당 segment는 “커버”로 간주

---

## 2) 시나리오(A/B) 구성과 “편향 최소화” 체크

### 2.1 “현장 실측 직전” synthetic의 원칙
본 프로젝트 synthetic 시나리오 스펙은 다음 3가지를 목표로 합니다(요약):
1) 분포 일치(센서값/잔차, 링크 delay/loss/outage)
2) 시간 구조 일치(quiet↔active 블록, burst, ramp/step 등)
3) 정책에 민감한 동역학 일치(fixed_tau가 과도하게 trivial해지지 않도록 잔차 스케일/이벤트 설계)

관련 스펙/생성기:
- 스펙: `docs/field_synthetic_scenarios_A_B_spec_v1.md`
- 생성기: `scripts/generate_synthetic_run.py` (`--model field --scenario A|B`)

### 2.2 편향 방지(공정 비교) 포인트
아래 항목은 “정책만 바뀌고 나머지는 동일”을 보장하기 위한 설계입니다.
- 센서 RNG / 링크 RNG 스트림을 분리(시나리오/seed 고정 시 센서 시계열이 정책에 의해 변하지 않음)
- 동일 profile/seed/scenario에서 periodic/fixed_tau/adaptive만 교체
- adaptive decision 로그는 `--decision-publish local`로 **링크 트래픽에 포함하지 않음**
- KPI 산출 시 baseline(periodic) 기반 seq-aligned 비교로 커버리지/품질 정렬

추가로 발견된 편향 요인(중요):
- EWMA diagnostics가 켜지면 `EventMsg.event_reason`가 payload에 포함되어 **MQTT bytes가 증가**합니다.
  - adaptive만 diagnostics가 켜져 있으면 KPI2(rate vs fixed_tau)가 **불리하게 편향**될 수 있습니다.
  - 해결: **decision/learning 진단**과 **event payload 진단**을 분리해, payload-fair KPI와 진단 가시성을 동시에 확보합니다.
    - `diagnostics.enabled: true` (LinUCB decision 로그 진단 활성)
    - `diagnostics.events_enabled: false` (event payload 진단 비활성 → bytes 공정성 유지)
    - 최종 KPI 프리셋: `configs/policy_poc_covforce_kpi.yaml`

---

## 3) KPI 실패 원인(정량/정성)

### 3.1 (선행) Analyzer 집계 버그: seed 혼합 baseline 문제
여러 seed run을 한 번에 분석할 때, seq-aligned KPI(특히 coverage)가 seed 간 섞여 계산되는 문제가 있었습니다.
이를 수정해 baseline 정렬을 `(profile, sensor, scenario, seed)` 단위로 분리했습니다.
- 관련: `collector/analyze.py`의 meta 기반 그룹화(`meta_seed`, `meta_scenario`)

이 버그 수정 이후에도, 시나리오 B에서 adaptive의 KPI4 실패가 seed에 따라 재현됨을 확인했습니다.

### 3.2 KPI4 실패의 본질: “학습만으로”는 짧은 segment를 안정 커버하기 어려움
관측된 실패 패턴(요약):
- adaptive가 어떤 구간에서 `tau > tau_ref(fixed_tau)` arm을 장시간 선택하면,
  periodic 기준 anomaly segment(길이>=2) 안에서 전송이 0회가 되어 KPI4(AnomalySegmentRecall)가 하락
- 특히 segment 수가 적거나 짧은 seed에서는, LinUCB가 해당 구간을 “학습/탐색”으로 커버하기 전에 segment가 끝나
  1-run 내 KPI4 안정 만족이 어렵고 seed 민감도가 커짐(샘플 복잡도/credit assignment 문제)

정성적 판단:
- KPI4는 “의미 있는 이상구간을 놓치지 말라”는 요구사항에 해당하며,
  이를 단순히 KPI 완화로 해결하면 “더 큰 tau로 보내지 않아서 rate만 줄이는” 방향으로 KPI를 게임할 위험이 큽니다.
- 따라서 **KPI 정의를 먼저 완화하기보다**, 정책이 최소한의 coverage를 보장하도록 런타임/정책 설계를 보완하는 것이 타당합니다.

---

## 4) 권고: KPI 조정보다 LinUCB(적응형 모드) 개선이 우선

### 4.1 가드레일(= fixed_tau 회귀) 대신 “세그먼트 liveness” 옵션
사용자 우려(가드레일이 AI 기능을 무력화)와 KPI4 안정성 모두를 만족하기 위해,
다음 옵션을 추가했습니다.

- 옵션명: `safety.coverage_force_emit_on_unhit_segment`
- 의미: anomaly segment(len>=2)에서 아직 1회도 전송(hit)하지 않았으면 **딱 1회 emit을 강제**
- 중요한 점:
  - **safe arm 강제(= fixed_tau 회귀)가 아님**
  - 전송은 강제하지만, 전송되는 이벤트의 `(tau,kbits)`는 **LinUCB가 선택한 값 그대로** 기록/사용
  - 결과적으로 “AI가 선택하되, 커버리지는 보장”이라는 구조

구현 위치:
- config 스키마: `common/config.py`
- 런타임 로딩/동작: `edge/policy/runtime.py`

### 4.2 KPI 평가용 diagnostics 공정성: decision은 켜고(event는 끈다)
KPI 평가에서 중요한 것은 **(정책별) 이벤트 payload 크기가 달라지지 않는 것**입니다.

- 이벤트 쪽(EWMA) diagnostics가 켜지면 `event_reason` 등이 payload에 포함되어 bytes가 증가할 수 있습니다.
- 반대로 decision 로그(LinUCB diagnostics)는 이벤트 uplink와 분리하면(= payload에 포함되지 않으면) KPI2를 왜곡하지 않습니다.

따라서 최종 KPI 프리셋은 아래처럼 구성합니다.
- `configs/policy_poc_covforce_kpi.yaml`
  - `diagnostics.enabled: true` (파이프라인/학습 진단 지표 확보)
  - `diagnostics.events_enabled: false` (payload-fair)
  - synthetic에서는 `--decision-publish local`로 decision을 링크 트래픽에 포함하지 않음

---

## 5) 정량 결과(“현실적/객관적” 확인)

아래 결과는 `scripts/generate_synthetic_run.py --model field` 기반이며,
시나리오 A/B 각각 seed 0..2를 생성한 뒤 `collector.analyze`로 KPI를 산출했습니다.

### 5.1 시나리오 B(변동 큼): adaptive가 fixed_tau보다 더 효율적(낮은 rate) + KPI PASS
- (참고) coverage-force 적용 전(학습/리워드 shaping만 적용)에는 seed 혼합 버그 수정 후에도
  KPI4가 불안정하게 FAIL 했습니다. 예: `results/scnB_poc_v4_agg_seeded/kpi_final.csv`
  - mic_rms anomaly_segment_recall=0.448 → FAIL
  - temp anomaly_segment_recall=0.694 → FAIL

- Aggregated 결과: `results/scnB_poc_covforce_kpi_agg_seeded/kpi_final.csv`
  - mic_rms: rate vs fixed_tau **+15.69%**, anomaly_segment_recall **1.0**, overall **PASS**
  - temp: rate vs fixed_tau **+5.26%**, anomaly_segment_recall **1.0**, overall **PASS**

→ 시나리오 B처럼 변동이 큰 환경에서 adaptive는 fixed_tau 대비 전송량을 더 줄이면서(KPI2 만족),
커버리지(KPI4)도 보장할 수 있음을 확인했습니다.

### 5.2 시나리오 A(상대적으로 안정): seed 0..2 모두 KPI PASS
- Aggregated 결과: `results/scnA_poc_covforce_kpi_agg_seeded/kpi_final.csv`
  - mic_rms / temp 모두 overall **PASS**

---

## 6) 재현 커맨드(핵심만)

### 6.1 시나리오 B seed 2 예시
```bash
# adaptive 생성 (coverage-force + KPI config)
python scripts/generate_synthetic_run.py --model field --scenario B --policy adaptive --seed 2 \
  --run-dir artifacts/field_scnB_adaptive_3h_poc_covforce_kpi_rep02 --overwrite \
  --arms-config configs/policy_poc_covforce_kpi.yaml --decision-publish local

# KPI 분석
python -m collector.analyze \
  -i artifacts/field_scnB_periodic_3h_v11_rep02 \
  -i artifacts/field_scnB_fixed_tau_3h_v11_rep02 \
  -i artifacts/field_scnB_adaptive_3h_poc_covforce_kpi_rep02 \
  -o results/scnB_poc_covforce_kpi_rep02 \
  --baseline-policy periodic --policy-config configs/policy_poc_covforce_kpi.yaml \
  --audit --no-plots --no-paper-plots --no-diagnostic-plots --no-ucb-timeseries --no-pareto-p95
```

---

## 7) 결론(의사결정)

1) KPI4 실패는 KPI 정의를 바꿔서 해결하기보다, 적응형 모드가 “이상구간을 최소 1회는 잡는다”는
   **coverage liveness**를 갖추도록 LinUCB 런타임을 개선하는 것이 우선입니다.
2) 제안/구현된 `coverage_force_emit_on_unhit_segment`는 “fixed_tau로의 회귀 가드레일”과 달리,
   **arm 강제 없이** KPI4를 안정화하며, 시나리오 B에서 fixed_tau 대비 더 낮은 rate(효율)를 달성할 수 있음을
   정량으로 확인했습니다.
3) KPI 평가에서는 diagnostics(특히 event_reason)로 인한 payload 차이가 KPI2를 왜곡할 수 있으므로,
   KPI용 config(진단 off)를 분리해 공정성을 확보해야 합니다.
