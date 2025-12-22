# 실험 요약 리포트

> NOTE: 이 문서는 `test_data/analysis` 샘플을 기반으로 생성된 예시 리포트입니다.  
> 최신 실측 결과는 `python -m collector.analyze --input <logs> --out <dir>`로 재생성하세요.

본 리포트는 **이벤트 기반 MAE(res)**를 사용합니다. 전체 시계열 MAE가 필요하면 원시 스트림 또는 복원 파이프가 추가로 필요합니다.

- AoI/Rate는 기본적으로 **수집기 수신 시각(`t_recv_ns`)** 기반으로 계산합니다 (없으면 `ts` 기반).
- (optional) LinUCB reward components (`reward_aoi`, `reward_mae`, `reward_rate`) are logged only when edge diagnostics are enabled; otherwise plots are skipped.
- 표의 `mean±std`는 **run(리플리케이트) 단위 지표**의 평균/표준편차입니다.
- 비교 기준(baseline): `periodic`

## 지표 요약 (profile × policy × sensor)

| profile | policy | sensor | n_runs | n_events | dur[s] | rate[B/s] | AoI_mean[ms] | AoI_p95[ms] | MAE_event_mean | MAE_event_p95 | k̄ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| slow_10kbps | periodic | mic_rms | 1 | 3018 | 781.5 | 1018.9 | 161.0 | 283.1 | 0.051 | 0.144 | 6.00 |
| slow_10kbps | periodic | temp | 1 | 391 | 780.0 | 128.7 | 1028.4 | 1928.3 | 0.045 | 0.111 | 8.00 |

---

## periodic 대비 변화(정량 비교)

개선율(%) 정의(낮을수록 좋은 지표 기준): `improvement = (baseline - candidate) / baseline * 100`

| profile | sensor | policy | ΔRate[B/s] | Rate 개선율[%] | ΔAoIμ[ms] | AoIμ 개선율[%] | ΔMAE | MAE 개선율[%] |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| slow_10kbps | mic_rms | periodic | +0.0 | +0.0 | +0.0 | +0.0 | +0.000 | +0.0 |
| slow_10kbps | temp | periodic | +0.0 | +0.0 | +0.0 | +0.0 | +0.000 | +0.0 |

### Adaptive vs periodic interpretation

Lower is better for Rate/AoI/MAE; positive improvement means better.
- adaptive: no comparison rows available (baseline missing).

---

## Figures (시각화)

- 생성 경로: `figs/`

### slow_10kbps / mic_rms

![](figs/mic_rms_slow_10kbps_compare_rate_bar.png)
![](figs/mic_rms_slow_10kbps_compare_aoi_p95_bar.png)
![](figs/mic_rms_slow_10kbps_compare_mae_p95_bar.png)
![](figs/mic_rms_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png)

### slow_10kbps / temp

![](figs/temp_slow_10kbps_compare_rate_bar.png)
![](figs/temp_slow_10kbps_compare_aoi_p95_bar.png)
![](figs/temp_slow_10kbps_compare_mae_p95_bar.png)
![](figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png)

---

## Paper Figures (논문용 추가 플롯)

![](figs/mic_rms_all_compare_env_metrics_panel.png)

![](figs/temp_all_compare_env_metrics_panel.png)

### slow_10kbps / mic_rms


### slow_10kbps / temp
