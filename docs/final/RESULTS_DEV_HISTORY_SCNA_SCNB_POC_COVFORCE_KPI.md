# 결과물 최종본 및 발전과정 기록 (scnA/scnB · poc_covforce_kpi)

작성일: 2026-01-31  
프로젝트: `semantic-uplink-rpi5`

이 문서는 **최종 결과물(8개 폴더)** 이 무엇인지 명확히 정의하고, 그 결과물에 도달하기까지의 **시행착오/원인분석/개선** 과정을
재현 가능하도록 기록합니다.

> 결론(요약)
> - `results/` 하위 폴더 중 **아래 8개만 최종 산출물**이며, 나머지는 개발/검증 과정에서 생성된 시행착오 산출물이다.
> - KPI4(coverage) 안정화는 “fixed_tau로의 회귀(가드레일)”이 아니라 **segment liveness(세그먼트 당 1회 emit 보장)** 옵션으로 해결했다.
> - “파이프라인 진단(결정/학습/큐/전송)”이 비어 보이던 문제는, **결정 로그(diagnostics)와 이벤트 payload(diagnostics)를 분리**하여
>   공정성(payload-fair)과 진단 가시성(결정 로그 풍부화)을 동시에 만족시키는 방향으로 마무리했다.

---

## 1) 최종 결과물(8개) — “이 폴더만 보면 된다”

아래 8개 폴더가 최종본입니다.

### 1.1 Scenario B (변동 큼)
- `results/scnB_poc_covforce_kpi_rep00`
- `results/scnB_poc_covforce_kpi_rep01`
- `results/scnB_poc_covforce_kpi_rep02`
- `results/scnB_poc_covforce_kpi_agg_seeded`

### 1.2 Scenario A (상대적으로 안정)
- `results/scnA_poc_covforce_kpi_rep00`
- `results/scnA_poc_covforce_kpi_rep01`
- `results/scnA_poc_covforce_kpi_rep02`
- `results/scnA_poc_covforce_kpi_agg_seeded`

> `rep00~02`는 seed별(run 1개 = seed 1개) 결과, `agg_seeded`는 seed 0..2를 **seed-aware로 집계**한 결과입니다.  
> 각 폴더의 `analysis_meta.json`에 입력(artifacts)과 분석 플래그가 기록되어 있어, 해당 파일이 “재현 스펙” 역할을 합니다.

---

## 2) 최종 결과 폴더 구성(무엇이 들어있어야 “완성”인가)

최종 결과물 폴더들은 공통적으로 아래 파일/폴더를 포함합니다.

| 경로 | 의미 |
|---|---|
| `analysis_meta.json` | 분석 입력(artifacts 경로) + 플래그(plots/diagnostics 등) 메타 |
| `kpi_verdict.json` | 프로젝트 PASS/FAIL + 실패한 (profile×sensor) 및 reason |
| `kpi_final.csv` | K1..K5, overall (profile×sensor 단위 strict KPI) |
| `metrics_by_run.csv` | run 단위 지표(정량 원본 테이블) |
| `metrics_summary.csv` / `metrics_summary.parquet` | 요약 테이블(집계/리포팅용) |
| `metrics_vs_periodic.csv` / `metrics_vs_fixed_tau.csv` | baseline 대비 개선율 요약 |
| `linucb_arm_distribution.csv` | adaptive의 arm 선택 분포(센서/프로파일/seed별) |
| `linucb_entropy_60s.csv` | 60초 윈도우 action entropy(정책 변화/탐색량 정량) |
| `quality_audit.json` / `quality_audit.md` | 결과 생성 품질 점검(누락/NaN/분포 경고 등) |
| `plot_manifest.json` | 생성된 플롯 목록/메타 |
| `report.md` | 핵심 표 + KPI PASS/FAIL 요약 + figure 링크 |
| `figs/` | 비교/진단/paper 플롯들(png/pdf) |

### 2.1 “파이프라인 진단이 비어있다”의 의미와 최종 상태
과거에는 `metrics_by_run.csv`의 LinUCB/진단 컬럼이 `NaN`으로 대량 비어보이는 문제가 있었으나,
최종본에서는 **adaptive row에서** 다음 값들이 채워집니다(예: `linucb_ucb_*`, `linucb_reward_*`, `linucb_forced_reason_*` 등).

다만 아래는 “정상적으로 NaN일 수 있는” 케이스가 있습니다.
- `outbox_pending_recovery_s`: outbox pending이 peak 이후 **0으로 복귀하지 않으면** 정의상 계산할 수 없어 NaN이 될 수 있음.
- `linucb_*` 컬럼이 periodic/fixed_tau 행에서 NaN: LinUCB 의사결정이 없으므로 정상.

---

## 3) 발전 과정(시행착오 → 원인 규명 → 최종 해법)

이 섹션은 “왜 이런 최종 폴더가 생겼는지”를 논리적으로 남기기 위한 기록입니다.

### Phase 0 — 목표/공정성/스펙 정리
- 목표/목적/KPI 정의를 문서화하여 “무엇을 성공이라 부를지”를 먼저 고정:
  - `docs/specs/PROJECT_GOALS.md`
  - `docs/specs/architecture.md`
- 핵심 원칙: **정책만 바꾸고 나머지는 동일**해야 비교가 공정하다(센서/링크 RNG 분리, 동일 profile/seed/scenario 유지).

### Phase 1 — “현장 실측 직전” 수준의 현실적 synthetic 시나리오(A/B) 구축
- 요구사항: 시나리오는 현실과 근사하고, 편향이 없어야 한다.
- A/B 설계:
  - A: 비교적 안정적인 변화 + 제한된 이상 구간
  - B: 변동/비정상성이 큰 환경(적응형에 유리할 수 있음)
- 스펙/생성기:
  - 스펙: `docs/field_synthetic_scenarios_A_B_spec_v1.md`
  - 생성기: `scripts/generate_synthetic_run.py` (`--model field --scenario A|B`)
- 중요한 공정성 포인트:
  - decision 로그는 `--decision-publish local`로 기록(링크 트래픽에 포함하지 않음).

### Phase 2 — KPI 파이프라인(Analyzer) 정합성: seed 혼합 버그 제거
- 증상: multi-seed를 한 번에 분석하면, baseline 정렬/coverage 등 seq-aligned KPI가 seed 간 섞여 계산되어 결과가 왜곡될 수 있었다.
- 조치: baseline 정렬을 `(profile, sensor, scenario, seed)` 단위로 분리하여 계산하도록 Analyzer를 수정.
- 결과: 이후 “정책이 실제로 KPI4를 불안정하게 만든다”는 현상이 재현되며, KPI 문제 vs 정책 문제 판단이 가능해졌다.

### Phase 3 — KPI4 실패(coverage) 원인분석: “짧은 이상 세그먼트”의 1-run 불안정성
- 증상: 시나리오 B 일부 seed에서 adaptive가 anomaly segment를 0회 hit하여 KPI4(AnomalySegmentRecall)가 FAIL.
- 정성/정량 판단:
  - KPI4 자체를 완화하면 “KPI를 게임해서 rate만 줄이는” 방향으로 흐를 위험이 큼.
  - 따라서 KPI4를 건드리기보다, 정책/런타임이 “최소한의 커버리지(liveness)”를 갖도록 개선하는 것이 타당.

### Phase 4 — “가드레일(= fixed_tau 회귀)” 반려, 대안으로 liveness 채택
- 사용자 요구: 가드레일을 키면 사실상 fixed_tau와 유사해져 AI의 의미가 사라짐 → 프로젝트 근간을 위협.
- 의사결정:
  - **arm 강제(= fixed_tau로 회귀)** 는 하지 않는다.
  - 대신 anomaly segment(len>=2)에서 **세그먼트 당 1회 emit만 보장**하여, coverage를 안정화한다.

### Phase 5 — KPI4 안정화 구현: `coverage_force_emit_on_unhit_segment`
- 구현 아이디어(핵심):
  - anomaly segment 내부에서 아직 1회도 emit을 못 했으면 “딱 1회” 전송을 강제한다.
  - 이때 `(tau, kbits)`는 **LinUCB가 선택한 arm 그대로**(safe-arm 강제 없음).
- 결과:
  - KPI4 안정화(특히 시나리오 B) + KPI2(전송률 guard)도 유지.
  - “변동이 큰 B에서 adaptive가 fixed_tau보다 더 효율(낮은 rate 또는 guard 내 우위)”이라는 결론을 객관적으로 낼 수 있는 기반 확보.
- 관련 문서: `docs/specs/KPI_DIAGNOSIS_AND_RECOMMENDATION.md`

### Phase 6 — 결과 폴더 “완성본” 마무리: 모든 지표/지수/플롯 출력
- 초기에는 결과 폴더를 가볍게 만들기 위해 일부 옵션으로 플롯/진단을 최소화했으나,
  최종본에서는 아래를 모두 포함하도록 `collector.analyze`를 재실행했다.
  - plots / paper-plots / diagnostic-plots
  - UCB time series, pareto(p95), parquet 저장
- 산출물: `figs/`, `plot_manifest.json`, `metrics_summary.parquet` 등 포함.

### Phase 7 — “파이프라인 진단이 비어있다” 최종 해결: decision diagnostics와 event payload 분리
- 문제:
  - KPI 공정성을 위해 diagnostics를 끄면, decision 로그에 `arm_id`, `ucb_*`, `forced_reason_*`, reward component 등이 누락되어
    분석 결과에 진단 컬럼이 `NaN`으로 남는 문제가 발생.
  - 반대로 diagnostics를 켜면, `EventMsg.event_reason` 같은 필드가 payload에 포함되어 **MQTT bytes가 증가**하고 KPI2가 편향될 수 있음.
- 최종 해법(의도):
  - **결정 로그 진단(diagnostics)은 켜되**, 이벤트 payload 진단은 끈다(= payload-fair).
- 적용:
  - `configs/policy_poc_covforce_kpi.yaml`:
    - `diagnostics.enabled: true`
    - `diagnostics.events_enabled: false`
  - 이 설정으로 adaptive의 “결정 로그 기반” 진단이 풍부해지고, 이벤트 bytes 공정성은 유지된다.
- 결과:
  - 최종 8개 폴더에서 adaptive 행의 LinUCB/진단 컬럼이 채워지고,
    `report.md`의 “LinUCB/파이프라인 진단” 표도 정상적으로 채워진다.

---

## 4) `results/`의 나머지 폴더(시행착오 결과물) 분류 가이드

아래는 최종본(8개) 외에 남아 있는 폴더들이 “무슨 목적이었는지”를 빠르게 파악하기 위한 가이드입니다.
정확한 재현/입력은 각 폴더 내부의 `analysis_meta.json`을 우선으로 합니다.

- `results/*_failed/`: KPI FAIL 또는 중간 산출물 누락이 있었던 단계의 보관용.
- `results/*_guarded_*/`: guardrail(고정 arm 강제) 방향의 실험 흔적(최종 방향과 다름).
- `results/dev_rewardfix_*`: reward/logging/지표 산출 디버깅(개발용).
- `results/dev_segctx_*`: 세그먼트 컨텍스트/힌트/임계값 제약 등 커버리지/학습 안정화 실험(개발용).
- `results/scn*_poc_v*/`: PoC 반복 실험 버전 히스토리(보상/스케일/집계/플롯/진단 옵션 변형).
- `results/scn*_poc_covforce_rep*/`: coverage-force를 붙이되 KPI/진단/플롯 설정이 최종과 다르거나, 중간 점검용으로 만든 결과.

> 원칙: 최종본 판별은 “폴더 이름”이 아니라, **해당 폴더의 `analysis_meta.json`이 무엇을 입력으로 삼았고 어떤 플래그로 분석했는지**로 한다.

---

## 5) 최종본 재현(요약)

최종본은 아래 조합으로 구성된다.
- periodic/fixed_tau: 기존 field v11 artifacts 사용
- adaptive: `configs/policy_poc_covforce_kpi.yaml` + scenario A/B + seed 0..2로 새로 생성한 artifacts 사용
- 분석: `collector.analyze`를 “full outputs” 플래그로 실행

정확한 입력 경로는 각 최종 결과 폴더의 `analysis_meta.json`의 `inputs`를 따른다.

### 5.1 재현 커맨드 템플릿(참고)
아래는 “최종본과 동일한 형태의 폴더”를 다시 만들고 싶을 때 참고용 템플릿입니다.
(가장 정확한 스펙은 `analysis_meta.json`이지만, 여기서도 핵심을 남깁니다.)

#### (1) adaptive artifacts 생성 (예: Scenario B, seed 2)
```bash
python scripts/generate_synthetic_run.py --model field --scenario B --policy adaptive --seed 2 \
  --run-dir artifacts/field_scnB_adaptive_3h_poc_covforce_kpi_rep02 --overwrite \
  --arms-config configs/policy_poc_covforce_kpi.yaml --decision-publish local
```

#### (2) seed별 분석 (예: Scenario B, rep02)
```bash
python -m collector.analyze \
  -i artifacts/field_scnB_periodic_3h_v11_rep02 \
  -i artifacts/field_scnB_fixed_tau_3h_v11_rep02 \
  -i artifacts/field_scnB_adaptive_3h_poc_covforce_kpi_rep02 \
  -o results/scnB_poc_covforce_kpi_rep02 \
  --baseline-policy periodic \
  --policy-config configs/policy_poc_covforce_kpi.yaml \
  --audit --plots --paper-plots --diagnostic-plots --ucb-timeseries --pareto-p95 --save-parquet
```

#### (3) seed 집계 분석 (예: Scenario B, agg_seeded)
```bash
python -m collector.analyze \
  -i artifacts/field_scnB_periodic_3h_v11_rep00 \
  -i artifacts/field_scnB_fixed_tau_3h_v11_rep00 \
  -i artifacts/field_scnB_adaptive_3h_poc_covforce_kpi_rep00 \
  -i artifacts/field_scnB_periodic_3h_v11_rep01 \
  -i artifacts/field_scnB_fixed_tau_3h_v11_rep01 \
  -i artifacts/field_scnB_adaptive_3h_poc_covforce_kpi_rep01 \
  -i artifacts/field_scnB_periodic_3h_v11_rep02 \
  -i artifacts/field_scnB_fixed_tau_3h_v11_rep02 \
  -i artifacts/field_scnB_adaptive_3h_poc_covforce_kpi_rep02 \
  -o results/scnB_poc_covforce_kpi_agg_seeded \
  --baseline-policy periodic \
  --policy-config configs/policy_poc_covforce_kpi.yaml \
  --audit --plots --paper-plots --diagnostic-plots --ucb-timeseries --pareto-p95 --save-parquet
```

> 주의: KPI 공정성을 위해, decision 로그는 이벤트 uplink에 포함되지 않도록 `--decision-publish local`을 유지한다.

---

## 6) 관련 참고 문서(함께 봐야 이해가 끝난다)
- 프로젝트 목표/로직/KPI: `docs/specs/PROJECT_GOALS.md`
- KPI 실패 원인 진단/권고(coverage-force의 정당화): `docs/specs/KPI_DIAGNOSIS_AND_RECOMMENDATION.md`
- 시나리오 A/B 스펙: `docs/field_synthetic_scenarios_A_B_spec_v1.md`
