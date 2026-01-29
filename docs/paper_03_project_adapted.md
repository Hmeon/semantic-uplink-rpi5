# (논문 정리·프로젝트 적용) Comparative Analysis of Classical and LinUCB Bandits… (ITM Web of Conferences 78, 01031, 2025)

- 원문: **Zehao Li**, *Comparative Analysis of Classical and LinUCB Bandits in Recommender Systems Based on Cumulative Regret and Reward*, ITM Web of Conferences 78, 01031 (CSEIT 2025), 2025.  
- 본 문서 목적: 이 논문을 **semantic-uplink-rpi5(LoRa 엣지 + LinUCB 정책 + 안전가드/강제 emit)** 프로젝트에 맞춰,
  1) 논문이 실제로 한 실험/결론을 **수치 기반으로 요약**하고  
  2) 우리 프로젝트 KPI/평가 설계에 **어떻게 “재현 가능한 형태”로 전환할지** 정리한다.  
- 핵심 전제: 논문은 **추천 시스템(MovieLens)** 환경이다. 따라서 결과 수치(예: reward 45,000)는 **우리 통신 KPI로 직접 이식 불가**.  
  대신 “**LinUCB가 장기 상호작용 + 컨텍스트가 의미 있는 환경에서 안정적**”이라는 경험적 근거와, **평가 프레임(회차/지표/통계검정/강건성 시나리오)**를 가져온다.

---

## 1) 논문이 실제로 한 것 (실험 설계 요약)

### 1.1 비교 대상 알고리즘
- ETC(Explore-Then-Commit)
- UCB
- Asymptotically optimal UCB
- Thompson Sampling(TS)
- **LinUCB** (컨텍스트 밴딧)

### 1.2 데이터셋/문제 설정
- MovieLens 1M 데이터셋에서 약 120k 샘플 추출
- 장르(genre)를 arm으로 보고, 매 라운드 장르를 추천 → 사용자 피드백(평점)으로 reward 생성
- LinUCB는 user feature + item feature를 One-Hot 기반으로 연결해 컨텍스트 벡터 구성

### 1.3 평가 지표(논문 정의)
- Reward mean: (1/T) Σ r_t
- Regret mean: (1/T) Σ (r*_t − r_t)
- Reward/Regret std: 반복 실험 간 분산
- Convergence rate: 누적 reward 곡선의 기울기
- Error bars: ±1 std

### 1.4 실험 시나리오(라운드)
- 500 라운드: cold-start(초기 학습 구간)
- 10,000 라운드: long-term interaction(장기 상호작용)
- 100,000 / 1,000,000 라운드: 장기 regret 성장 관찰
- 강건성 테스트:
  - sparse feedback(희소 보상)
  - non-stationary(시간에 따른 보상 확률 변화)
- 통계검정:
  - Wilcoxon signed-rank test (p-value)
  - Bootstrap resampling(B=1000)로 95% CI

---

## 2) 논문 핵심 결과 (숫자만 뽑아 정리)

### 2.1 500 rounds (cold-start)
- ETC: **cumulative reward 2050 ± 50** (초기 최고)
- LinUCB: **cumulative reward 1820 ± 18** (ETC보다 낮지만 표준편차가 작아 안정적)
- (표 내에서 regret mean은 ETC 110±75, UCB 145±44, TS 184±29, LinUCB 145±18로 제시됨)

**해석(논문 주장):**
- 초기에는 ETC가 빠르게 “좋아 보이는 arm”을 고정해 보상이 크게 나오지만, 변동성이 크고 이후 적응성이 떨어짐.

### 2.2 10,000 rounds (long-term)
- LinUCB: **cumulative reward 45,000 ± 350**
- 비교군: ETC 40,000 ± 320, UCB 39,500 ± 300, TS 39,800 ± 310

**해석(논문 주장):**
- 장기에서는 컨텍스트 모델링(LinUCB)이 누적 reward/안정성에서 우위.

### 2.3 1,000,000 rounds (cumulative regret)
- LinUCB: **cumulative regret 1420 ± 15** (최저 + 분산도 최소)
- 비교군(논문 표): ETC 9000±400, UCB 6000±1000, TS 4500±600, Asymptotic UCB 1800±300

**주의(데이터 일관성 체크):**
- 본문 후반 Bootstrap CI로 “regret [2703, 2792]”를 제시하는 부분이 있는데, 위 Table의 1420±15와 수치가 맞지 않는다.  
  가능한 원인 후보(논문 텍스트만으로 확정 불가):
  1) regret 스케일링(도표에서 “scaled by 10” 표기 존재)  
  2) 다른 horizon/setting에서 계산된 regret  
  3) regret 정의(평균/누적/스케일)가 섞여 기재  
- 따라서 **우리 포트폴리오에 인용 시 ‘정확히 어떤 정의의 regret인지’ 확인 문장**을 반드시 붙여야 한다.

### 2.4 희소 보상(sparse feedback) / 비정상(non-stationary) 강건성
- sparse feedback에서 UCB/ETC 성능 저하가 크고 변동성이 커짐
- TS와 LinUCB는 비교적 안정적으로 reward 유지
- non-stationary에서 TS와 LinUCB는 변화에 빠르게 적응, UCB/ETC는 적응 부족

### 2.5 연산 비용(라운드당 평균 실행 시간)
- ETC ≈ 0.2ms
- UCB 계열 ≈ 0.6~0.8ms
- TS ≈ 0.9ms
- LinUCB ≈ 1.3ms

**해석(논문 주장):**
- LinUCB는 행렬 업데이트 때문에 비용이 더 크지만, 정확도/안정성 관점에서 장기에는 가치가 있음.

---

## 3) 이 논문을 “semantic-uplink-rpi5”에 맞춰 재해석

### 3.1 추천 시스템 → LoRa uplink 정책으로의 매핑(개념 레벨)
논문:  
- arm = 장르(추천 선택지)  
- context = 유저/아이템 피처  
- reward = 평점 기반 보상  
- sparse feedback = 클릭/평점이 드문 환경  
- non-stationary = 유저 취향 변화

프로젝트(우리):  
- arm = (tau, kbits) 조합 또는 정책 선택지  
- context = [AoI, residual, residual_var, loss, q_len] (정규화된 상태)  
- reward = -(w_aoi·AoI_norm + w_mae·MAE_norm + w_rate·Rate_norm)  
- sparse feedback = ACK/성공 관측이 희소하거나 loss 추정이 불안정(수신측 지연/누락)  
- non-stationary = 채널/SNR/간섭/부하 변화로 “같은 arm의 효과”가 시간에 따라 바뀜

즉, 논문이 직접 주는 가치는 “LinUCB가 **컨텍스트가 의미 있는 장기 상호작용 환경에서** 안정적으로 누적 성과를 키운다”는 경험적 결과 +  
“희소/비정상 환경에서의 강건성 테스트 프레임”이다.

### 3.2 우리 프로젝트 KPI/평가에 가져올 수 있는 것
논문에서 그대로 가져와야 하는 건 **숫자**가 아니라 **평가 설계**다.

- (A) “초기 구간”과 “장기 구간”을 분리해서 본다  
  - 우리에게서 초기 구간은 “채널/센서/스케일 초기화 + LinUCB warm-up”  
  - 장기 구간은 “정책이 안정적으로 수렴한 뒤 KPI 유지”  
- (B) 희소/비정상 환경을 별도 시나리오로 분리  
  - 희소: ACK 없는 구간(또는 high loss), 리시버 지연  
  - 비정상: 거리/장애물/간섭 변화로 PDR·SNR 변동
- (C) 반복 실험 + 통계검정  
  - “한 번 돌려서 좋았다” 금지  
  - 최소 n=10 반복, Wilcoxon/Bootstrap 같은 비모수 검정으로 **정책 간 차이 유의성** 보고

---

## 4) 우리 프로젝트에 맞춘 “Regret” 정의(논문 프레임을 그대로 쓰면 깨지는 지점)

논문 regret은 r*_t(oracle optimal reward)를 전제한다.  
우리 프로젝트는 “oracle을 만들기 어렵다”는 문제가 있다(미래 AoI/손실을 알아야 함).

따라서 **프로젝트에서 쓸 수 있는 regret 정의는 2개 중 하나**로 고정해야 한다.

### 4.1 Baseline-regret (현실형, 추천)
- 기준 정책 `b ∈ {fixed_tau, periodic}`를 정하고,
\[
\mathrm{Regret}_T^{(b)} = \sum_{t=1}^{T} \left( r_t^{(b)} - r_t^{(\pi)} \right)
\]
- r가 “최대화” 보상(우리처럼 음수 비용)이면, 부호를 명확히:
  - 비용 C를 최소화하는 경우: regret = Σ(C_π − C_b)  
  - 보상 r = −C로 쓰는 경우: regret = Σ(r_b − r_π)

**장점:** oracle 없이도 정의가 명확하고, 현장 비교가 가능.  
**단점:** baseline 선택이 해석을 좌우함(그래서 KPI도 baseline을 명시해야 함).

### 4.2 Oracle-free proxy-regret (실험형)
- 짧은 윈도우에서 “실현된 최고 보상”을 r*_t로 근사  
- 또는 offline replay로 candidate policy를 동시에 평가  
**주의:** 이건 구현/로깅이 잘못되면 쉽게 허수 성과가 나온다.

---

## 5) 논문 기반으로 우리 프로젝트 평가 설계를 “MD로 고정” (재현용 템플릿)

아래 템플릿은 `collector/analyze.py` 결과를 표로 뽑을 때 그대로 사용 가능한 형태를 목표로 한다.

### 5.1 실험 구간 분할(예시)
- Warm-up: 0~W (LinUCB 학습 안정화 구간, KPI에서 제외하거나 별도 표기)
- Short-term: W~W+S (논문 500 rounds 대응)
- Long-term: W+S~End (논문 10k/1M rounds 대응)

### 5.2 반복 실험
- 동일 설정(profile/sensor/path)에서 **n=10** 반복
- 랜덤성(시드) 기록:
  - LinUCB 탐색 파라미터(α), 초기 θ/Λ
  - 패킷 드롭(재현 불가하면 “현장성”은 확보되나, 통계 검정이 어려움)

### 5.3 보고 지표(최소)
- Rate: periodic 대비 개선율(%), fixed_tau 대비 악화(%)
- recon_mae_p95: fixed_tau 대비 악화(%)
- AnomalyRecall
- AoI: p95 또는 max (guardrail 위반 여부)
- (선택) cumulative reward/cost 곡선, baseline-regret 곡선
- (운영) runtime(ms/step), CPU%, 메모리

### 5.4 통계 검정(정책쌍별)
- Wilcoxon signed-rank (paired):  
  - metric = {Rate, recon_mae_p95, AoI_p95, AnomalyRecall} 중 KPI에 포함된 것
- Bootstrap 95% CI:  
  - (n=10 반복 결과)에서 CI로 “정책 간 격차”를 제시

> 논문이 실제로 “p<0.01”과 Bootstrap CI를 사용한 구조를 그대로 가져오되,  
> 우리에서는 **metric의 정의(누적/평균/스케일)**를 한 줄로 고정해야 한다.

---

## 6) 이 논문을 포트폴리오/보고서에 인용할 때의 ‘정확한 문장’(과장 금지)

- “추천 시스템 맥락에서 LinUCB는 장기 상호작용 구간(10,000 rounds)에서 누적 보상과 안정성이 다른 MAB 알고리즘 대비 우수하다는 경험적 결과가 보고되어 있다(ITM Web of Conferences, 2025).”
- “해당 연구는 sparse feedback 및 non-stationary 환경에서도 LinUCB/TS가 상대적으로 견고하게 동작한다는 실험 프레임을 제공하며, 본 프로젝트는 이를 통신 환경(패킷 손실/채널 변화) 시나리오로 치환해 정책 강건성 평가를 구성했다.”
- “단, 논문 실험은 추천 시스템 데이터셋 기반이며, 통신 KPI로의 직접 전이는 불가능하므로 ‘평가 방법론(반복·통계검정·강건성 시나리오)’ 측면에서 인용한다.”

---

## 7) 실전 체크리스트(우리 코드에 바로 대입)

1) **정의 고정**: reward/cost, regret( baseline-regret vs oracle-regret ) 정의를 문서/코드에 동일하게 박아라.  
2) **스케일 일관성**: AoI/MAE/Rate 정규화 스케일을 “데이터 기반”인지 “arms 기반”인지 명시하고, 비교 시 동일 조건 유지.  
3) **강건성 시나리오 분리**:  
   - sparse(ACK 희소/손실률↑), non-stationary(환경 변화) 각각에서 KPI 패스 여부를 별도 표로 내라.  
4) **통계 검정 최소 세트**: Wilcoxon + Bootstrap CI.  
5) **연산 비용 기록**: Pi에서 LinUCB 업데이트/결정 시간 로그를 남겨, “현장 실행 가능성”을 수치로 제시.

---

## 8) 원문 메타(추적용)
- ITM Web of Conferences 78, 01031 (2025) / CSEIT 2025  
- 제목: Comparative Analysis of Classical and LinUCB Bandits in Recommender Systems Based on Cumulative Regret and Reward  
- DOI: 10.1051/itmconf/20257801031
