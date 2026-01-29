# (논문 정리·프로젝트 적용) Opportunistic Semantic and Bit Communications in Uplink NOMA → **semantic-uplink-rpi5 “Adaptive Mode” 설계 레퍼런스**

- 원문: **Xidong Mu, Yuanwei Liu, Petar Popovski, Naofal Al‑Dhahir**, *Opportunistic Semantic and Bit Communications in Uplink NOMA*, IEEE ICC 2023 (Signal Processing for Communications Symposium).  
- 본 문서 목적: 본 프로젝트에서 **적응형 모드(adaptive mode)**를 설계할 때,
  - “**(저전송/저간섭) semantic 모드** vs **(고정확도) bit 모드**를 채널 상태·제약조건에 따라 **기회적으로(opportunistic)** 선택”
  - “**하드 제약(guardrail)**을 가진 최적 정책을 **라그랑지안(프라이멀-듀얼)**로 푸는 구조”
  를 **우리 코드/로그/KPI** 언어로 변환해 MD로 고정한다.

> 이 논문은 NOMA(다중 사용자, SIC, 간섭)를 다룬다.  
> 우리 프로젝트(LoRa uplink/semantic uplink)는 동일한 PHY가 아니지만, **‘모드 전환 + 제약 만족 + 상태별 정책’**이라는 제어 구조는 그대로 가져올 수 있다.

---

## 1) 논문이 실제로 푸는 문제(요약) — “모드 전환 + 제약 + 상태별 최적 정책”

### 1.1 시스템 구성(논문)
- 2-user uplink NOMA:
  - **N-user(Primary, near user)**: BitCom로 전송, 전력 `P_n` 고정.
  - **F-user(Secondary, far user)**: 같은 RB를 재사용(NOMA)하여 업링크. 매 채널 상태에서
    - **SemCom** 또는 **BitCom** 중 하나를 선택(지표 `ρ(v)∈{0,1}`)
    - F-user의 전력 `p(v)`와 시간 점유율 `α(v)`를 함께 결정.

- SIC decoding order: AP는 먼저 N-user bit 신호를 디코딩한 뒤 SIC로 제거하고 F-user를 디코딩.  
  → F-user가 존재하면 N-user가 **간섭을 받기 때문에**, N-user 성능 제약이 핵심.

### 1.2 채널 상태별(rate / semantic rate) 정의(논문)
- 수신신호:
  \[
  y(v)=\sqrt{P_n}x_n(v)+\sqrt{p(v)}x_f(v)+z(v)
  \]

- N-user의 순간 bit rate:
  \[
  R(v)=\alpha(v)\log_2\!\left(1+\frac{P_n|h_n(v)|^2}{p(v)|h_f(v)|^2+\sigma^2}\right)
  +(1-\alpha(v))\log_2\!\left(1+\frac{P_n|h_n(v)|^2}{\sigma^2}\right)
  \]
  (즉, α 구간에서는 간섭 포함, 1−α 구간에서는 단독 전송)

- F-user의 semantic rate(DeepSC 기반 “semantic similarity” ε):
  - semantic similarity를 SNR `γ(v)=p(v)|h_f(v)|^2/σ^2`의 함수로 두고, 실험(DeepSC)로 얻은 ε를 회귀(logistic)로 근사.
  - “유효 semantic rate”는 **유사도 임계치 ε_min 이상일 때만 유효**(indicator로 gating).

- F-user의 BitCom도 “equivalent semantic rate”로 변환해 SemCom와 같은 척도로 비교.

결론적으로 F-user의 목적함수는 “(SemCom or BitCom) 중 선택했을 때의 equivalent semantic rate”를 최대화.

### 1.3 최적화 문제(논문)
- 목표: F-user의 ergodic(equivalent) semantic rate 최대화
- 제약: N-user의 **최소 ergodic bit rate** 보장
- 추가 제약: F-user 평균/피크 전력, α∈[0,1], p∈[0,P̂]

형태:
\[
\max_{\rho(v),\alpha(v),p(v)} \mathbb{E}_v[S(v)]
\quad\text{s.t.}\quad \mathbb{E}_v[R(v)]\ge \bar R
\]

---

## 2) 논문 핵심: “상태별로 SemCom/BitCom 중 무엇이 이득인지”를 라그랑지안으로 결정

### 2.1 라그랑지안 구성(논문)
논문은 비볼록+정수 변수(ρ) 문제지만, time-sharing 조건으로 강대칭(strong duality)이 성립한다고 보고 **라그랑지안 듀얼**로 푼다.

- 듀얼 변수:
  - β ≥ 0 : N-user rate 제약( \(\mathbb{E}[R]\ge\bar R\) )에 대한 multiplier
  - δ ≥ 0 : 평균 전력 제약( \(\mathbb{E}[\alpha p]\le \bar P\) )에 대한 multiplier

- 상태 v(페이딩 상태)별 서브문제로 분해 가능:
  - ρ=1( SemCom )일 때의 최적 L 값 vs
  - ρ=0( BitCom )일 때의 최적 L 값을 비교하여 선택

### 2.2 상태별 결정 구조(논문) — “모드 선택은 비교, α는 bang-bang”
서브문제의 구조적 결론:

- 먼저 ρ=1(SemCom) 가정 시, 전력 p에 대한 1D 탐색으로 `p_s*`를 찾고,
  - Π_s(p_s*)가 양수면 α_s*=1, 음수면 α_s*=0 (경계점이면 임의)
- ρ=0(BitCom)도 동일하게 `p_b*`, α_b*를 찾음
- 최종 모드 선택:
  \[
  \rho^*(v)=
  \begin{cases}
  1 & \text{if } L_{\rho=1}(α_s^*,p_s^*)>L_{\rho=0}(α_b^*,p_b^*)\\
  0 & \text{otherwise}
  \end{cases}
  \]

즉,
- **모드 선택 = “SemCom 이득” vs “BitCom 이득” 비교**
- **시간 점유율 α = 0 또는 1로 떨어지는 bang-bang(온/오프) 구조**
- 전력 p는 각 모드에서 1D 최적화

### 2.3 듀얼 업데이트(논문) — “제약을 만족하도록 β,δ를 조정”
듀얼 변수는 서브그라디언트로 갱신:

- Δβ = E[R*] − \(\bar R\)  (rate 제약의 slack)
- Δδ = \(\bar P\) − E[α* p*] (전력 제약의 slack)

엘립소이드 같은 방법으로 (β,δ)를 갱신하여 최적 듀얼을 찾고,
그 후 **primal(α(v))은 LP로 다시 구성**해 최적 해를 완성한다.

---

## 3) 이 논문을 우리 프로젝트 “Adaptive Mode”에 어떻게 가져올지 (핵심만)

### 3.1 우리 프로젝트에서의 “SemCom vs BitCom” 재정의(현실적 매핑)
논문에서 SemCom/BitCom은 “텍스트 DeepSC vs Shannon bit”이지만,
우리 프로젝트에서는 다음처럼 정의하면 구조가 그대로 맞는다.

- **SemCom 모드(저전송/저비트/의미 보존)**  
  - 예: 더 강한 손실 압축(더 낮은 kbits), 이벤트/이상구간 중심 전달, 중요도 기반 요약(semantic payload)
  - 목표: **Rate(전송량)↓**를 크게 가져가면서도 **품질 KPI(재구성/이상구간 보존)**를 최소 기준 이상으로 유지

- **BitCom 모드(고정확도/고비트)**  
  - 예: 높은 kbits 또는 원본/저손실 전달(또는 fixed_tau 대비 품질 안정화 목적)
  - 목표: 품질이 빡센 구간(예: 급격 변화/이상 이벤트)에서 **MAE/Recall 가드레일을 깨지 않도록** 보수적으로 보냄

즉 “모드”는 **압축·전송 정책 클래스**를 의미한다.

### 3.2 논문에서의 α(v) = 0/1 구조를 우리 런타임에 매핑
논문에서 α(v)=0/1은 “그 페이딩 상태에서 F-user를 아예 admit 할지 말지(온/오프)”에 가깝다.

우리 쪽에서는 다음처럼 대응된다.

- α=1: 해당 샘플/윈도우를 **emit(전송)**
- α=0: 해당 샘플/윈도우를 **hold(미전송)**

이는 이미 우리 정책이 하는 “전송 여부 결정”과 동일 계열이다.
→ 이 논문을 참고하면, “전송 여부(emit/hold)”도 결국 **라그랑지안 이득이 양수일 때만 transmit**하는 구조로 정당화할 수 있다.

### 3.3 논문에서의 ‘Primary user 제약’을 우리 KPI 제약으로 치환
논문은 “N-user의 최소 bit rate”를 hard constraint로 둔다.
우리 프로젝트의 현실적 제약은 다음 중 하나(혹은 조합)다.

- **품질 가드레일(semantic quality)**:  
  - fixed_tau 대비 recon_mae_p95 악화 ≤ 10%  
  - AnomalyRecall ≥ 0.90  
  - (옵션) AoI 상한 위반 금지(또는 AoI 강제 emit)
- **전송량/에너지 가드레일(low-rate)**:  
  - periodic 대비 Rate 감소 ≥ X%  
  - fixed_tau 대비 Rate 악화 ≤ Y%

즉, “제약을 만족시키면서 utility를 최대화”라는 논문 구조를 그대로 유지하되,
제약 항을 **우리 KPI 항**으로 바꾸면 된다.

---

## 4) “논문 스타일” 적응형 모드 설계안 — 우리 코드에 바로 꽂히는 형태

여기서부터는 구현 단위로만 정리한다. (추정/미사여구 없음)

### 4.1 상태(state) / 모드(mode) / 행동(action) 정의
- 상태 x_t: 이미 프로젝트에 존재하는
  - `[aoi_ms, res, res_var, loss, q_len]` 정규화 벡터
- 모드 m ∈ {SEM, BIT}  (2개로 시작. 필요하면 3개 이상 확장 가능)
- 행동 a는 기존 arm(tau,kbits)와 결합:
  \[
  a \equiv (m,\tau,kbits)
  \]

### 4.2 유효 semantic utility 정의(논문식 “thresholded effective rate”를 차용)
논문은 semantic similarity가 임계치 ε 이상일 때만 semantic rate를 유효로 인정한다.
우리도 동일한 gating을 둔다.

- 예: 품질 지표 q_t를 정의(선택지는 두 가지)
  1) 재구성 기반: \(q_t = 1 - \mathrm{MAE\_norm}\)
  2) 이상구간 기반: \(q_t = \mathrm{AnomalyRecall\_window}\)

- 유효 utility:
  \[
  U_t(a)=U^\text{raw}_t(a)\cdot \mathbf{1}(q_t(a)\ge q_{\min})
  \]
  - q_min은 KPI 가드레일(예: Recall≥0.90, MAE 악화≤10%)에서 직접 옴
  - 이 항은 “안전가드(safe arm 강제)”와 동일 철학이므로, 중복이면 **하드 가드**로 두고 utility gating은 생략 가능

### 4.3 제약을 가진 최적화(라그랑지안 형태로 “가중치 자동 조정”)
논문과 동일하게 “목표 최대화 + 제약 만족” 형태를 고정한다.

- 목적(예시):
  - maximize: 평균 utility (semantic 성능)
- 제약(예시):
  - E[Rate] ≤ Rate_max  (저전송 목표)
  - E[MAE_p95] ≤ MAE_limit
  - E[AoI_p95] ≤ AoI_limit
  - E[Recall] ≥ Recall_min

여기서 핵심은 “가중합 reward”로 우겨 넣는 게 아니라,
논문처럼 **듀얼 변수(λ)**를 둬서 제약을 맞추도록 만드는 것이다.

- 라그랑지안(예):
  \[
  \mathcal{L}(a;\lambda)=\mathbb{E}[U(a)]
  -\lambda_r(\mathbb{E}[\text{Rate}(a)]-R_{\max})
  -\lambda_m(\mathbb{E}[\text{MAE}(a)]-M_{\max})
  -\lambda_a(\mathbb{E}[\text{AoI}(a)]-A_{\max})
  -\lambda_q(Q_{\min}-\mathbb{E}[\text{Recall}(a)])
  \]

- 듀얼 업데이트(논문 (23) 구조 그대로):
  - \(\lambda_r \leftarrow [\lambda_r + \eta(\mathbb{E}[\text{Rate}]-R_{\max})]_+\)
  - \(\lambda_m \leftarrow [\lambda_m + \eta(\mathbb{E}[\text{MAE}]-M_{\max})]_+\)
  - \(\lambda_a \leftarrow [\lambda_a + \eta(\mathbb{E}[\text{AoI}]-A_{\max})]_+\)
  - \(\lambda_q \leftarrow [\lambda_q + \eta(Q_{\min}-\mathbb{E}[\text{Recall}])]_+\)

> “기존 w_aoi, w_mae, w_rate를 사람이 고정”하는 대신,  
> **듀얼 업데이트로 KPI를 만족하는 방향으로 가중치가 자동 적응**하도록 만드는 게 이 논문을 가져오는 핵심 이득이다.

### 4.4 상태별 모드 선택 규칙(논문 ρ*(v) 비교 구조를 그대로 사용)
논문은 각 상태에서 SemCom vs BitCom의 L 값을 비교한다. 우리도 동일:

- 각 후보 모드/행동 a에 대해 “순이득”을 정의:
  \[
  \Pi(a)=U_t(a) - \lambda_r\cdot \text{Rate}_t(a) - \lambda_m\cdot \text{MAE}_t(a) - \lambda_a\cdot \text{AoI}_t(a) + \lambda_q\cdot \text{Recall}_t(a)
  \]
  (부호는 constraint 정의에 맞게 정리)

- 선택:
  - \(a^*=\arg\max_a \Pi(a)\)
  - 만약 \(\max_a \Pi(a)\le 0\)이면 **emit 하지 않음(α=0)**  
    (논문의 α=0 케이스와 구조적으로 동일)

이 규칙은
- “전송할 가치가 있는 상태에서만 전송”
- “제약이 빡세지면(λ 커지면) 자동으로 보수적 모드로 이동”
을 제공한다.

### 4.5 LinUCB와 결합(두 가지 설계)
논문은 ‘학습’이 아니라 ‘최적 정책’을 폐이딩 상태별로 푼다.  
우리는 LinUCB가 이미 존재하므로 결합 방식은 둘 중 하나로 선택한다.

#### 설계 A: arms 확장 + LinUCB가 직접 모드를 학습
- arm: (m, tau, kbits)
- reward: \(\Pi(a)\) (라그랑지안 순이득)
- 듀얼(λ)은 윈도우 단위(예: 1분/1000샘플)로 업데이트
- 장점: LinUCB가 “언제 Sem, 언제 Bit”를 데이터로 학습
- 주의: \(\Pi\)는 λ에 의해 time-varying → 비정상성 발생 가능  
  → λ 업데이트를 느리게(작은 η, 큰 윈도우) 해서 안정화 필요

#### 설계 B: LinUCB는 “품질-전송 tradeoff”를 학습, 모드 전환은 외부 게이트
- LinUCB는 (tau,kbits)만 학습
- 외부 게이트가 “SEM/BIT 중 어떤 클래스”를 고르고, 그 안에서 LinUCB로 파라미터 선택
- 장점: 비정상성을 줄이고, 안전가드(하드)와 결합이 쉬움
- 주의: 모드 선택 게이트의 비용/품질 예측이 필요(로그 기반 회귀 등)

---

## 5) 이 논문에서 바로 따올 수 있는 “운영 관점” 포인트 (프로젝트 적응형 모드에 중요)

### 5.1 “유효 성능”을 threshold로 정의하는 게 안전가드와 잘 맞는다
논문은 semantic similarity가 임계치 미만이면 semantic rate를 0으로 처리한다.
우리도 동일 구조로 만들면:

- “모드가 좋아 보이지만 품질이 임계치 미만이면 무효”  
→ safe arm 강제/강제 emit 로직과 충돌 없이 정합 가능.

### 5.2 최적 정책이 bang-bang(전송/미전송) 구조로 떨어진다는 점
논문에서 α는 사실상 0/1로 떨어진다.
우리도 “매 샘플 전송 vs 홀딩”이 기본이므로,  
“미세한 α 조정”보다 **전송 여부(emit/hold)**가 정책의 본질이라는 점을 정당화할 수 있다.

### 5.3 제약을 multiplier로 두면 KPI 충돌을 ‘해석 가능’하게 만든다
현재 프로젝트는 KPI가 두 세트로 공존하고 철학이 다르다.  
이 논문 스타일로 가면 다음을 명확히 할 수 있다.

- 목표(최대화) 1개를 고정(예: semantic utility 최대)
- 나머지는 constraint로 이동(예: MAE, AoI, Rate, Recall)
- 듀얼 변수(λ)가 “각 제약의 가격(price)”가 됨  
  → KPI 충돌 시 무엇이 제약으로 더 비싸졌는지(λ가 커졌는지) 로그로 설명 가능

---

## 6) 프로젝트에 적용할 때 반드시 확인해야 할 차이점(그대로 들고 오면 깨지는 부분)

1) 논문은 채널 상태(v)에서 연속 최적화(전력 p, 시간 α)를 한다.  
   우리 프로젝트는 대개 이산 선택(arms: (tau,kbits))이며, 전력은 고정일 가능성이 크다.  
   → p 최적화 부분은 “kbits/전송량 선택”으로 치환하는 게 자연스럽다.

2) 논문은 DeepSC semantic similarity ε(γ)를 실험 기반 회귀로 둔다.  
   우리는 텍스트가 아니라 센서/시계열.  
   → ε(·) 대신 “MAE/Recall/AoI 기반 quality 모델”로 대체해야 한다.
   - (권장) 로그로 품질 예측 모델을 fit(예: logistic/GBDT)하고, 그 예측값으로 gating/penalty를 적용

3) 논문은 NOMA의 ‘간섭’이 제약의 원인이다.  
   우리 시스템은 다중 사용자 간섭이 없을 수도 있다.  
   → 제약의 의미를 “전송량/에너지/규제(duty-cycle) + 품질 가드레일”로 재정의하면 구조는 동일하게 유지된다.

---

## 7) “포트폴리오/보고서에서 인용 가능한 문장”(과장 금지, 구조만 차용)

- “채널 상태별로 (Semantic vs Bit) 모드를 기회적으로 선택하고, 최소 서비스 제약(Primary user rate) 하에서 평균 성능을 최적화하는 라그랑지안/프라이멀-듀얼 구조가 제안되어 있다.”
- “해당 구조는 모드 선택(이득 비교)과 admit(온/오프, α) 의사결정이 상태별로 분리되는 형태로 나타나며, 제약 만족은 듀얼 변수의 서브그라디언트 업데이트로 달성된다.”
- “본 프로젝트의 적응형 모드는 동일한 프레임(목표 최대화 + KPI 제약 + 듀얼 업데이트)을 사용하되, NOMA 간섭 제약을 (Rate/MAE/AoI/Recall) 가드레일 제약으로 치환하여 적용한다.”

---

## 8) 구현 체크리스트(최소 로그 항목)
적응형 모드를 “재현 가능”하게 만들려면, 논문이 사실상 요구하는 로그는 다음이다.

- 윈도우별 KPI: Rate, recon_mae_p95, AoI_p95(or max), AnomalyRecall
- 듀얼 변수(λ) 로그: λ_r, λ_m, λ_a, λ_q
- 모드 선택 빈도: P(mode=SEM), P(mode=BIT)
- 안전가드 발동 횟수: safe arm 강제, 강제 emit 횟수
- (선택) “순이득 Π(a)” 상위 3개 후보 값(디버깅/검증용)

---

## 9) 원문 메타
- 키워드: opportunistic semantic/bit communications, uplink NOMA, SIC, effective semantic rate, time-sharing, Lagrange duality, subgradient/ellipsoid, primal reconstruction LP
