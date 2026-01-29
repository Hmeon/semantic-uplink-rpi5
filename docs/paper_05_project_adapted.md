# (논문 정리·프로젝트 적용) Rate Region Characterization for Semantics- and Bits-based Multiuser Communications → **semantic-uplink-rpi5 “Adaptive Mode” 설계 레퍼런스**

- 원문: **Xidong Mu, Yuanwei Liu**, *Rate Region Characterization for Semantics and Bits based Multiuser Communications*, ICASSP 2023, DOI: 10.1109/ICASSP49357.2023.10094629
- 본 문서 목적: 본 프로젝트에서 **적응형 모드(adaptive mode)**를 설계할 때,
  - “SemCom(의미 중심) vs BitCom(비트 정확 전달)”의 **우열이 채널 상태(SNR)/전력 구간에 따라 뒤집힌다**
  - “전송 자원(시간/전력) 배분으로 얻을 수 있는 trade-off를 ‘rate region’으로 해석한다”
  를 **우리 코드/지표( Rate / MAE / Recall / AoI / 안전가드 )** 언어로 전환해 참고 가능하게 만든다.

> 논문은 2-user uplink에서 OMA/NOMA를 비교하지만, 프로젝트에서 가져올 핵심은 PHY가 아니라 **‘의미 성능은 포화(saturate)할 수 있고, BitCom은 고 SNR에서 계속 성장하며, 따라서 모드 전환이 필수’**라는 구조다.

---

## 1) 논문이 실제로 푸는 문제(요약)

### 1.1 시스템(논문)
- AP(수신기) + 2 사용자:
  - **N-user(near)**: BitCom 업링크 (Shannon bit rate)
  - **F-user(far)**: SemCom 업링크 (DeepSC 기반 text SemCom)
- 두 사용자 동시 수용을 위해
  - OMA: 시간 분할(서로 다른 슬롯)
  - NOMA: N-user는 전체 시간에 BitCom, F-user는 그 중 α 구간에서 SemCom(중첩) + AP에서 SIC

### 1.2 SemCom의 성능 정의(논문이 핵심으로 쓰는 “effective semantic rate”)
논문은 SemCom의 “semantic rate”를 **semantic similarity** 함수 ε(K,γ)로 표현하고,
실제로는 similarity가 임계치 ε_min 이상일 때만 “유효(meaningful)”로 인정한다.

- 채널 SNR:  γ = p_f |h_f|^2 / σ^2  
- semantic similarity: ε(K,γ)  (DeepSC를 돌려 얻는 값)
- 실무적으로 폐형식이 없어서, ε(K,γ)를 **generalized logistic function**으로 회귀 근사:
  - ε̂_K(γ) ≈ A_{K,1} + (A_{K,2}-A_{K,1}) / (1 + exp(-(C_{K,1}γ + C_{K,2})))
- 유효 semantic rate(논문 정의):
  - S = α · (I/(K·L)) · ε̂_K(γ) · 1(ε̂_K(γ) ≥ ε_min)

즉, SemCom은 SNR이 올라도 ε̂_K(γ)가 **상한(A_{K,2})로 포화**되면 더 이상 커지지 않는 구조가 된다.

### 1.3 BitCom의 성능 정의(논문)
- Bit rate: R = α log2(1 + γ_f) (F-user가 BitCom을 한다고 가정한 비교 케이스)
- “공정 비교”를 위해 BitCom의 bit rate를 “equivalent semantic rate”로 변환:
  - S_B = R_B · (I/(μ·L)) · ε_C
  - μ: 평균 bits/word, ε_C: BitCom에서의 semantic similarity(비트 에러에 의해 결정)

---

## 2) 논문 핵심 결론(프로젝트에 바로 쓰는 문장 3개)

### 2.1 NOMA의 semantic-vs-bit rate region은 OMA를 포함한다(항상 우수)
- 정의:
  - OMA rate region: R^O_{SvB} = ⋃_{α∈[0,1]} {(S,R): S≤S_O, R≤R_O}
  - NOMA rate region: R^N_{SvB} = ⋃_{p_f∈[0,P_f], α∈[0,1]} {(S,R): S≤S_N, R≤R_N}
- 결론: R^O_{SvB} ⊆ R^N_{SvB}

프로젝트로 번역:
- “동일 자원(시간/전력)에서, **모드/자원 배치가 더 유연할수록(Pareto frontier가 넓을수록)** 더 좋은 정책이 나온다.”
- LoRa가 NOMA/SIC를 쓰지 않더라도, “자원 배치 자유도를 늘리는 정책(예: 윈도우/이벤트 단위 선택, kbits 선택, 전송 여부 선택)”이 **trade-off frontier를 확장**한다는 해석은 유지된다.

### 2.2 SemCom은 항상 BitCom을 이기지 않는다 (저 SNR/저 전력에서는 SemCom, 고 SNR/고 전력에서는 BitCom이 유리)
논문은 F-user에 대해 SemCom vs BitCom을 비교하며, 다음을 보인다.

- 고 SNR(γ→∞): SemCom semantic rate는 포화(상한)지만 BitCom bit rate는 log2(1+γ)로 계속 증가 → **BitCom이 유리**
- 저 SNR(γ→0): BitCom은 0으로 수렴하지만 SemCom은 (회귀된 similarity 및 정의에 의해) **양(+)의 하한**을 갖는 형태가 가능 → **SemCom이 유리**

이건 프로젝트 “adaptive mode”의 정당화 문장으로 바로 쓴다:
- “링크가 약하거나 전력/자원 제약이 심한 구간에서는 SemCom(요약/의미 보존)이 유리하지만,
  링크가 강한 구간에서는 BitCom(고정밀 전달)이 유리한 구간이 존재하므로, **모드 고정이 아니라 상태 기반 모드 전환이 필요**하다.”

### 2.3 논문이 실제로 보여준 수치 예시(개념 확인 용도)
- ε_min=0.9, K=5, BitCom 측은 μ=40, ε_C=1(비트 오류 없음)으로 둔 예시에서,
  - F-user 전력 예산이 낮을 때(P_f^max=0.1W): SemCom이 유리한 영역이 크게 나타남
  - F-user 전력 예산이 높을 때(P_f^max=10W): BitCom-only가 더 큰 영역을 확보(고 전력 구간에서 BitCom gain)

> 이 값 자체를 LoRa에 이식하지 않는다.  
> 대신 “전력/링크 구간이 바뀌면 모드 우열이 뒤집힌다”는 현상을 확인한 근거로만 쓴다.

---

## 3) 프로젝트(semantic-uplink-rpi5)로의 매핑: “adaptive mode”를 어떻게 설계할지

### 3.1 논문 수식의 최소 매핑(LoRa/시계열)
논문(텍스트 DeepSC)에서의 ε̂_K(γ) 자리에, 프로젝트에서는 다음 중 하나를 둔다.

- (A) 재구성 기반 품질:  q = 1 − MAE_norm  또는  MAE_p95 기반
- (B) 이상구간 보존:  q = AnomalyRecall_window
- (C) 혼합: q = f(MAE, Recall, AoI)

그리고 논문처럼 “유효 성능”을 threshold로 정의한다.

- effective utility:
  - U_sem = utility_sem · 1(q_sem ≥ q_min)
  - U_bit = utility_bit · 1(q_bit ≥ q_min)
- 여기서 q_min은 **안전가드 임계치**(예: Recall≥0.90, MAE 악화≤10%, AoI 상한 등)에서 온다.

> 핵심: “안전가드”는 지금 코드에서 hard override로 구현되어 있다.  
> 논문은 이를 수학적으로 “indicator gating”으로 정식화한다.  
> 따라서 adaptive mode 설계에서 **‘유효 성능(meaningful)’의 기준을 KPI/guardrail로 고정**하면, 모드 비교가 흔들리지 않는다.

### 3.2 모드 전환 기준(논문식 “저 SNR=Sem, 고 SNR=Bit”를 프로젝트 규칙으로 고정)
논문식 비교는 다음 구조다:
- SemCom: ε̂_K(γ)가 포화 → 성능 상한 존재
- BitCom: log2(1+γ)로 계속 증가 → 고 SNR에서 계속 이득

프로젝트에서 대응되는 현상은 다음과 같이 모델링하면 된다.
- Sem mode: kbits를 낮추면 Rate는 크게 줄지만, 품질은 어느 지점에서 급격히 깨지거나 포화
- Bit mode: kbits를 높이면 품질은 계속 개선되며, 특히 링크가 좋아 “전송 성공률/지연”이 안정적일 때 유리

따라서 **모드 전환**은 아래 비교로 정리할 수 있다.

- 링크 상태(대체 γ): SNR/RSSI/최근 ACK 성공률/채널 혼잡 지표
- 품질 예측: q̂_sem(x), q̂_bit(x)  (로그로 회귀/테이블화)
- 비용: Rate_sem(x), Rate_bit(x) (kbits 및 전송빈도 기반)

#### 결정 규칙(가장 단순한 실전형)
1) 먼저 guardrail을 체크한다.
   - q̂_sem < q_min이면 Sem 후보 탈락
   - q̂_bit < q_min이면 Bit 후보 탈락(대개는 발생 빈도 낮음)
2) 둘 다 가능하면 “기대 유효 성능 / 비용”을 비교한다.
   - score_sem = Û_sem − λ·Rate_sem
   - score_bit = Û_bit − λ·Rate_bit
3) score가 큰 모드 선택

여기서 λ는 “Rate에 대한 가격”이며, KPI를 맞추기 위해 윈도우 단위로 조정할 수 있다(이전 NOMA 논문(ICC 2023)에서의 프라이멀-듀얼 구조와 동일 철학).

### 3.3 LinUCB와 결합(권장 구조)
이 논문은 밴딧이 아니라 “rate region” 분석이다.  
따라서 LinUCB는 다음처럼 결합하는 게 안전하다.

- 1단계(게이트): Sem vs Bit 모드 선택
  - 입력: link-quality, 최근 지표(ACK, AoI, loss 등)
  - 출력: mode ∈ {SEM, BIT}
- 2단계(LinUCB): 선택된 모드 내부에서 (tau,kbits) 선택
  - SEM 모드: 낮은 kbits 영역을 중심으로 팔 구성
  - BIT 모드: 높은 kbits 또는 보수적인 tau 영역 중심

> 장점: 논문이 말하는 “구간별 우열 뒤집힘”을 구조적으로 반영하면서,  
> LinUCB는 각 모드 내부에서 미세 조정만 담당하게 된다(학습 안정성↑).

---

## 4) 이 논문을 바탕으로 “우리 평가/그래프”를 어떻게 만들지 (재현 관점)

논문이 제시한 핵심 산출물은 **semantic-vs-bit rate region(2D frontier)**이다.  
프로젝트에서는 아래 2D/3D 프론티어로 치환하면 된다.

### 4.1 2D: (Rate, Quality) 프론티어 (권장)
- x축: Rate(전송량/전송빈도)  — KPI에 이미 존재
- y축: Quality(예: 1−MAE_norm, Recall, 또는 유효 품질 indicator)

정책 비교:
- periodic / fixed_tau / adaptive(LinUCB) 각각에서 얻은 점들을 모아,
- Pareto frontier(동일 품질에서 더 낮은 Rate, 동일 Rate에서 더 높은 품질)를 비교

### 4.2 3D: (Rate, MAE, AoI) 혹은 (Rate, MAE_p95, Recall)
KPI가 2세트로 공존하는 현재 상황에서는,
- “(Rate, MAE, AoI)” 같은 3D 공간에서
- “유효 영역(guardrail 만족)”만 남기고 프론티어를 보는 방식이 가장 설명이 쉽다.

---

## 5) 포트폴리오/보고서에서 인용 가능한 문장(과장 금지)
- “SemCom의 유효 성능을 semantic similarity 임계치로 정의하면, 채널 SNR에 따른 의미 성능은 로지스틱 회귀 형태로 근사될 수 있으며, 유효 semantic rate는 임계치 미만에서는 0으로 처리된다.”
- “SemCom은 저 SNR/제한 자원 구간에서 유리할 수 있으나, 고 SNR 구간에서는 BitCom의 bit rate가 계속 증가하는 반면 SemCom의 의미 성능은 포화될 수 있어, 모드 우열이 구간에 따라 뒤집힐 수 있다.”
- “따라서 ‘고정 모드’가 아니라 상태(링크 품질/자원 제약)에 따른 **adaptive 모드 전환**이 합리적인 설계가 된다.”

---

## 6) 최소 체크리스트(구현/로그)
adaptive mode가 “논문 기반 설계”라고 주장하려면 아래 로그가 있어야 한다.

- 링크 상태: SNR/RSSI 또는 최근 ACK 성공률(슬라이딩 윈도우)
- 모드 선택 빈도: P(mode=SEM), P(mode=BIT)
- 모드별 KPI: Rate, MAE_p95, Recall, AoI_p95
- guardrail 위반 카운트: safe arm 강제 / 강제 emit 횟수
- (가능하면) 모드 전환 임계치(혹은 score 비교 값) 로그

---

## 7) 원문 메타
- ICASSP 2023 / DOI: 10.1109/ICASSP49357.2023.10094629  
- 키워드: semantic-versus-bit rate region, OMA vs NOMA, effective semantic rate, semantic similarity threshold, logistic regression of similarity, SemCom vs BitCom crossover (low vs high SNR)
