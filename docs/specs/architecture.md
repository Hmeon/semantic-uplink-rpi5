# 아키텍처 스펙(요약)

## 목적
Semantic Uplink(RPi5)는 제한된 업링크 환경에서 **의미 있는 변화만** 전송하기 위한 PoC 파이프라인이다.
센서 → 예측(EWMA residual) → 정책(periodic/fixed_tau/adaptive LinUCB) → Outbox(SQLite) → MQTT(QoS1)
→ Collector(중복제거/로그) → Analyzer(지표/플롯/KPI)로 구성된다.

## KPI 최종 확정안 (PASS/FAIL, strict)
평가 범위(강제):
- **profile × sensor** 단위로 모두 PASS해야 “프로젝트 PASS”.
- (마이크만 PASS 같은 선택적 합격 금지)

Baseline 정의(고정):
- `periodic`: 최빈 송신(참조 스트림)
- `fixed_tau`: 사람이 설정한 고정 임계치(품질 기준점)
- `adaptive`: LinUCB 정책(합격 판정 대상)

PASS 조건(5개 전부 충족):
1) Efficiency – Rate 절감(Primary)  
   `Rate_improvement_vs_periodic >= 85%`
2) Efficiency – fixed_tau 대비 과도한 악화 방지(Constraint)  
   `Rate_improvement_vs_fixed_tau >= -10%`
3) Semantic Quality – 재구성 품질 보존(Constraint)  
   `recon_mae_p95_improvement_vs_fixed_tau >= -10%`
4) Semantic Coverage – 이상구간 보존(Constraint)  
   `AnomalySegmentRecall >= 0.90`
5) Freshness Guardrail – AoI(p95) 제한(Constraint)  
   `AoI_p95_improvement_vs_fixed_tau >= -10%`

AnomalySegmentRecall 정의:
- periodic baseline에서 `|res| > tau_ref`인 연속 구간을 “세그먼트”로 정의
  - `tau_ref`: fixed_tau 정책에서 관측된 `tau`의 중앙값(median)
  - 길이 >= 2 샘플만 세그먼트로 카운트
- 후보 정책이 세그먼트 내부에서 1회 이상 전송하면 hit
- `recall = hit_segments / total_segments`

KPI 산출물(기준):
- `python -m collector.analyze ...` 실행 결과 디렉터리의 `kpi_final.csv`, `kpi_verdict.json`, `report.md`

## 구성(요소)
- Perception: mic RMS(원음 비저장) / temp(DS18B20/sysfs/mock)
- Prediction: EWMA 1-step predictor + residual
- Policy:
  - `periodic`: 매 샘플 송신(참조)
  - `fixed_tau`: EWMA residual threshold(사람 설정)
  - `adaptive`: LinUCB로 `(tau, kbits)` 선택 + 안전가드
- Uplink: Outbox(SQLite WAL) + MQTT QoS1
- Collector: MQTT subscribe + QoS1 중복제거 + Parquet/CSV 회전 저장
- Analyzer: AoI/Rate/MAE + seq-aligned 품질/커버리지 + KPI PASS/FAIL + 플롯

## 메시지/스키마
- `EventMsg`, `PolicyDecisionMsg`: `common/schema.py`

## 운영/재현성
- tc/netem 링크 프로파일: `link/shaper/tc_profiles.py`, `configs/link_profiles.yaml`
- AoI 정확도: 수신 시각(`t_recv_ns`) 기반 산출이므로 시스템 시간 안정화(NTP/RTC)를 권장
