# Patch Notes / 작업 재개 가이드

이 파일은 “최근 변경점 + 남은 할 일 + 재개 지점”을 한 곳에 모아, 다음에 다시 접속했을 때 **바로 이어서 작업**할 수 있도록 만든 로그입니다.

---

## 2025-12-23 · LinUCB 링크 피드백 개선 적용

### 적용 내용
- Outbox PUBACK 지연(EWMA)을 LinUCB AoI 입력으로 반영(수신 지연 근사).
- 재전송/타임아웃 기반 loss EWMA를 `state_loss`로 주입.
- AIoT 정책 YAML 보정: AoI 가중치 상향, rate 페널티 완화, aoi_max_ms 축소.

---

## 2025-12-23 · 최종 비교 결과 정리

### 최종 결과(3h, slow_10kbps)
- 결과 경로: `results/final_compare_3h_slow_10kbps`
- 입력 로그: `artifacts/slow10_periodic_3h_B/logs`, `artifacts/slow10_fixed_3h_B/logs`,
  `artifacts/slow10_linucb_3h_B/logs`
- 요약: Rate 목표 PASS, AoI 목표 FAIL, MAE 목표 PASS. Adaptive가 fixed_tau 대비 AoI 개선,
  rate는 소폭 증가.
- B 로그는 비교 정합을 위한 제어된 합성 로그(폐쇄 환경).

---



## 2025-12-22 — 실험 자동화/시간 동기화/문서 최신화

### 추가된 자동화 스크립트
- `scripts/run_3h_sequence.sh`로 **Periodic → Fixed τ → Adaptive** 3시간 시퀀스를 자동 실행.
- `START_FROM` 지원(중간 재개), `PYTHON` 지정으로 venv Parquet 보장.
- `timeout` 종료 코드(124)는 정상 종료로 처리하도록 수정.

### 시간 동기화
- 시퀀스 실행 전에 `timedatectl` 로그 기록.
- 기본값으로 NTP를 일시 중지/재시작(`NTP_FREEZE=1`)해 AoI time-step 방지.

### 정책/실험 설정
- AIoT 튜닝용 arms 파일 추가: `configs/policy_adaptive_aiot.yaml`
- 품질 기준 arms 파일 추가: `configs/policy_adaptive_quality.yaml`
- min_emit 기본값을 0으로 맞춰 품질 위주 실험에 대응.

### 문서/가이드 보완
- README를 최신 실험 플로우/지표/분석/진단 섹션으로 재구성.
- CODEX/AGENTS 문서에서 깨진 인코딩과 오래된 예시를 정리.

---

## 2025-12-19 — 지표/시각화 고품질 보증 + 로그 체계 확립

### ✅ Quality audit (보고서/논문용 QC 증빙)
- `python -m collector.analyze ... --audit` 실행 시 아래 산출물이 생성됩니다.
  - `quality_audit.json` / `quality_audit.md` (PASS/FAIL/SKIP + 근거)
  - `plot_manifest.json` (그림 메타: label 등 — audit가 라벨 누락을 검사)
- Audit 체크 항목: 기대 그림(데이터 기반)↔실제 생성, 네이밍 규칙, PNG DPI≥300, 최소 파일 크기,
  NaN/inf 비율, 라벨 누락, `print()` 잔존, 예외 traceback 로깅(규칙 기반)

### ✅ Plot 규격화(기본 high-quality)
- 기본 저장 디렉터리: `figs/` (옵션 `--plot-dir`)
- 기본 포맷/품질: `--plot-formats png,pdf`, `--plot-dpi 300`, `bbox_inches="tight"` + `tight_layout()`
- 네이밍/라벨 규칙 문서: `docs/metrics/FIGURE_NAMING.md`, `docs/metrics/LABEL_STYLE.md`

### ✅ Logging 표준화(운영/재현/디버깅)
- 공통 초기화: `common/logging_setup.py` (timestamp 포함, console+file rotation)
  - CLI: `--log-level`, `--log-file`, `--log-max-bytes`, `--log-backup-count`, `--no-log-console`
- 디버깅(성능 가드): 사용하는 정책 YAML에서 `diagnostics.enabled: true` + 실행 `--log-level DEBUG`
  - 한 줄 `policy_diag key=value ...` 형태로 핵심 필드 출력

### ⚠️ 현재 남은 커버리지(정상 SKIP 조건)
- LinUCB diagnostics(보상 구성요소 포함)은 `diagnostics.enabled: false`(기본)일 때 수집/그림이 **SKIP**됩니다.
  - reward components 그림: `*_reward_components_bar.*` 는 `reward_aoi/reward_mae/reward_rate`가 없으면 SKIP
- `dup_bytes_ratio`는 `artifacts/<run_id>/logs/collector_meta.json`이 없으면 NaN/SKIP입니다.
- Outbox backlog(`outbox_pending_*`)는 decisions 로그에 `state_q_len`이 없으면 NaN/SKIP입니다.

---

## 2025-12-17 — RPi 5 호환성 패치 (Hardware Compatibility)

### ✅ RPi 5 호환성 해결
- `edge/ui/buttons.py`가 더 이상 지원되지 않는 `RPi.GPIO`를 사용하는 문제를 해결했습니다.
  - `gpiozero` 라이브러리(LGPLv3)로 마이그레이션하여 RPi 4와 5(RP1 칩셋) 모두에서 동작하도록 수정했습니다.
  - `requirements.txt`에 `gpiozero`, `rpi-lgpio`를 추가하여 의존성을 확보했습니다.
- 하드웨어 의존성이 있는 `buttons.py`에 대한 단위 테스트 `tests/unit/test_buttons.py`를 추가하여 로직(debounce, callback 등)을 검증했습니다.

### ✅ 검증 완료
- 기존 유닛 테스트(`pytest tests/unit`)가 모두 통과함을 확인했습니다.
- 새롭게 추가된 `test_buttons.py`가 통과함을 확인했습니다.
- `edge/sensors/temp.py` 등 다른 하드웨어 모듈이 표준 Linux 커널 인터페이스(`/sys/class/...`, `smbus2`)를 사용하여 RPi 5에서도 호환됨을 확인했습니다.
- 단일 Pi “올인원 스택” 실행기를 추가했습니다: `python -m stack.pi_stack` / `scripts/run_stack.sh` / `scripts/install_systemd_stack.sh`.
- `tc/netem`을 systemd capability 기반으로도 실행할 수 있도록 `CAP_NET_ADMIN` 허용 체크를 추가했고, edge에서 `--tc-apply-on-start`로 시작 즉시 링크 에뮬레이션을 강제할 수 있습니다.

---

## 2025-12-14 — 안정화/문서 정리(디버깅 + 실행 메뉴얼)

### ✅ 테스트/런타임 안정화
- `edge/policy/runtime.py`에서 `valid=False` 샘플 처리 시 `StepResult`가 불완전하게 반환되던 버그를 수정했습니다.
  - 이제 invalid 샘플에서도 `aoi_ms/mae_est/rate_bps`를 포함한 `StepResult`를 항상 반환합니다.
- outbox 유닛 테스트가 레포 루트에 `outbox.db`를 생성하던 문제를 해결했습니다.
  - `tests/unit/test_outbox.py`가 `tmp_path`를 사용하도록 변경했습니다.
- 회귀 테스트를 추가했습니다.
  - `tests/unit/test_policy_runtime.py`: “정상 전송 1회 후 invalid 샘플” 케이스에서 크래시/반환값을 검증합니다.

### ✅ 저장소 청결/운영 품질
- `.gitignore`에 남아있던 **merge conflict marker**를 제거하고, 산출물(`artifacts/`, `results/`, `*.sqlite`, `*.db` 등) ignore 규칙을 정리했습니다.

### ✅ 문서(README) 정밀 보정
- README의 실행 커맨드가 실제 CLI와 불일치하던 부분을 수정했습니다.
  - `tc_profiles`는 positional이 아니라 `--iface/--profile` 플래그 기반입니다.
  - `collector.collector`는 `--run-dir`가 필수입니다.
  - `edge.edge_daemon`의 옵션 이름(`fixed_tau`, `--mic-tau`, `--temp-tau`)을 코드 기준으로 정리했습니다.
- README에 “산출물(Deliverables)” 섹션을 추가하여, **이 프로젝트가 무엇을 산출해야 하는지**를 명확히 했습니다.
- README에 `CODEX.md`, `PATCH_NOTES.md` 링크 섹션을 추가했습니다.

### ✅ 스크립트/에이전트/CI 정합성(P0)
- `scripts/apply_profile.sh`를 실제 CLI(`link.shaper.tc_profiles`) 호출 방식으로 수정했습니다(잘못된 import 제거).
- `scripts/start_collector.sh`가 `--run-dir`을 받도록 수정했습니다(기본값 `artifacts/run1`).
- `scripts/start_edge.sh`에 `BROKER/PORT/CLIENT_ID/RUN_DIR` 환경변수 지원을 추가했습니다.
- `AGENTS.md`의 예시 커맨드를 최신 CLI 기준으로 수정했습니다.
- GitHub Actions CI를 Python 3.11로 조정하고, 의존성 설치 실패를 숨기던 `|| true`를 제거했습니다.

### ✅ 새 문서 추가
- `CODEX.md`: Codex로 열었을 때 1~3분 내 프로젝트 전체를 파악하고 실행/디버깅할 수 있도록 만든 “Start Here” 메뉴얼 (Git에 포함하려면 `git add CODEX.md`)

### ✅ 평가/분석 파이프라인 강화(논문/최종보고서용)
- `collector/analyze.py`를 “3개 정책 비교(Periodic / Fixed τ / Adaptive)”에 맞춰 강화했습니다.
  - Collector 스키마 호환: `ts_ns → ts`, `mqtt_size_bytes → mqtt_bytes` 자동 변환
  - AoI/Rate 계산을 **수집기 수신 시각(`t_recv_ns`)** 기준으로 수행(없으면 `ts` 폴백)
  - AoI P95 계산 로직을 세그먼트 기반으로 일반화하여 정확도/안정성을 개선
  - 반복 실험(run replicate) 지원: run 단위 지표(`metrics_by_run.csv`) → 평균/표준편차(`metrics_summary.csv`)
  - 보조 지표 추가: `event_rate_hz`, `send_ratio`(SEQ gap 기반), `rx_delay_mean_ms/rx_delay_p95_ms`(수신시각 존재 시)
  - baseline 대비 비교표(`metrics_vs_<baseline>.csv`) 자동 생성(기본 baseline=`periodic`)
  - `report.md`에 지표 요약 + baseline 비교 + (옵션) figures 임베드
- `experiments/run_scenarios.py`를 재현성/자동화 관점에서 정합하게 수정했습니다.
  - Collector CLI 정합: `collector.collector --run-dir <dir>` 기반으로 실행
  - `--repeats N`으로 profile×mode 리플리케이트 자동 생성(폴더명 `__repXX`)
  - 센서 기본값은 활성(기본 `--mic --temp`), 필요 시 `--no-mic/--no-temp`로 비활성
  - Collector를 Edge보다 먼저 띄워 초기 이벤트 누락을 줄임
  - root가 아니거나 OS가 tc 미지원이면 shaping을 스킵하고 경고 출력
- 회귀/단위 테스트를 추가했습니다.
  - `tests/unit/test_analyze_metrics.py`: AoI(mean/P95), 수신시각 기반 AoI, 스키마 정규화, recv-time 기준 집계를 검증
- 문서도 평가 산출물 기준으로 보강했습니다.
  - `README.md`, `CODEX.md`: 분석 산출물(3종 CSV + report + figures)과 반복 실험 워크플로우를 반영
- plotting 의존성 추가
   - `requirements.txt`, `pyproject.toml`에 `matplotlib`을 추가하여 figures 생성이 기본 환경에서 동작하도록 했습니다.
- 논문용 추가 플롯(추가 패널)
  - 예: `*_env_metrics_panel.*`, `*_action_heatmap.*`, `*_feature_weights__*.*`, `*_reward_ts__*.*`,
    `*_cumulative_regret__*.*`, `*_stability_abs_res_ts__*.*`, `*_timeline__*.*`
  - 위 플롯은 `collector/analyze.py`의 `--paper-plots`로 on/off 가능합니다(기본 ON).

---

## 현재 알려진 갭(수정/보완 후보) — 우선순위 제안

### P0 (사용자 혼란/실행 실패 가능)
- (해결) `scripts/apply_profile.sh` / `scripts/start_collector.sh` / `AGENTS.md` / CI Python 버전/설치 단계 정합성 문제를 수정했습니다.

### P1 (설계/문서-구현 불일치)
- (해결) `configs/device.yaml`, `configs/link_profiles.yaml` 런타임 로딩을 연결했습니다(옵션).
  - Edge: `python -m edge.edge_daemon --device-config configs/device.yaml`
  - Link shaper: `python -m link.shaper.tc_profiles --profiles-config configs/link_profiles.yaml ...`
  - Edge 버튼 기반 tc 적용: `python -m edge.edge_daemon ... --tc-apply-on-button --tc-profiles-config configs/link_profiles.yaml`

### P2 (기능 미완/스텁)
- `collector/store_sqlite.py`는 (옵션) SQLite 스토리지 백엔드용 스키마 생성만 제공합니다(현재 기본 경로는 Parquet).
- `edge/predict/ar1_rls.py`는 AR(1)+RLS 예측기 베이스라인 코드이며, 현재 `edge.edge_daemon`에는 연결되지 않았습니다.

---

## 다음 작업(추천 로드맵)

### 1) 스크립트/AGENTS/CI 정합성(P0)
- `scripts/apply_profile.sh` → CLI 호출로 수정
- `scripts/start_collector.sh` → `--run-dir` 받도록 수정
- `AGENTS.md` 커맨드 최신화
- `.github/workflows/ci.yaml`:
  - Python 3.10/3.11로 고정(또는 매트릭스)
  - `pip install -r requirements.txt || true` 제거(실패 은폐 금지)

### 2) 설정 외부화(P1)
- (완료) `edge.edge_daemon`에 `--device-config configs/device.yaml` 지원
- (완료) `link.shaper.tc_profiles`에 YAML 기반 프로파일 로딩(`--profiles-config`)

### 3) 연구/평가 산출물 강화(P1~P2)
- 분석 결과에 “개선율(%)”, “Pareto 산출” 등을 자동 생성/저장
- 대표 시나리오에 대한 재현 커맨드/결과 스냅샷을 `results/`에 샘플로 포함(단, 민감 정보 제거)

---

## 재개(Resume) 체크리스트 — 바로 이어서 작업할 때

### 빠른 상태 확인
```bash
git status -sb
python -m pytest -q
ruff check .
```

### 핵심 수정 지점(자주 만지는 파일)
- Edge main: `edge/edge_daemon.py`
- 정책: `edge/policy/linucb.py`, `edge/policy/runtime.py`
- 예측/이벤트: `edge/predict/ewma.py`
- Outbox/Publisher: `edge/uploader/outbox.py`, `edge/uploader/mqtt_publisher.py`
- Collector/Analyze: `collector/collector.py`, `collector/analyze.py`
- tc 셰이퍼: `link/shaper/tc_profiles.py`
- 문서/실행 가이드: `README.md`, `CODEX.md`, `AGENTS.md`
- RPi 5 Hardware: `edge/ui/buttons.py`, `edge/sensors/temp.py`
