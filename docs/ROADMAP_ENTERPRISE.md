# Enterprise Hardening Roadmap (Semantic Uplink · RPi5)

## 0. 목적과 원칙

이 로드맵의 목적은 프로젝트를 다음 단계로 끌어올리는 것이다.

- PoC 재현 가능성 -> 운영 안정성
- 기능 중심 구현 -> 품질 게이트 기반 개발
- 개인 개발 속도 -> 팀/조직 확장 가능 구조

핵심 원칙:

1. 모든 개선은 정량 지표와 함께 진행한다.
2. 실험용 유연성과 운영용 안정성을 분리한다.
3. 성능 개선보다 먼저 신뢰성/재현성/관측성을 고정한다.

---

## 1. 현재 기준선 (2026-02-07)

코드/테스트 기반 사실:

- 테스트: `67 passed, 1 skipped`
- 전체 커버리지: `37.84%`
- CI 최소 게이트: `20%` (낮음)
- 핵심 진입점 존재:
  - `python -m edge.edge_daemon`
  - `python -m collector.collector`
  - `python -m collector.analyze`
  - `python -m experiments.run_scenarios`

리스크 요약:

1. 커버리지 게이트가 낮아 회귀를 조기에 차단하기 어렵다.
2. 대형 파일(특히 분석기)에 기능이 과집중되어 변경 비용이 크다.
3. 일부 모듈은 미사용/미검증 상태로 유지보수 불확실성이 있다.
4. 실험/운영 경계는 존재하지만 계약(interfaces/SLA/error budget)이 문서화되어 있지 않다.

---

## 2. 단계별 고도화 로드맵

## Phase 0: Baseline Lock (즉시 시작)

목표:

- 현재 동작을 깨지 않고 기준선을 수치화/고정.

작업:

1. 미검증 모듈 단위 테스트 추가 (`common.timeutil`, `collector.store_sqlite`)
2. CI에 진입점 smoke check 추가
3. coverage gate를 현실적인 수준으로 상향 (현재 성과 이하로 하향 금지)

완료 기준 (DoD):

- CI에서 테스트/린트/스모크 전부 통과
- 커버리지 게이트가 기존 대비 상향
- 신규 테스트가 회귀를 최소 1개 이상 차단 가능

---

## Phase 1: 품질 게이트 강화 (1~2주)

목표:

- "작동"에서 "신뢰 가능한 변경"으로 전환.

작업:

1. 커버리지 임계치를 단계적으로 `35% -> 45% -> 55%` 상향
2. 중요 경로 테스트 우선 보강:
   - `edge.edge_daemon` 정책/CLI 경계
   - `edge.uploader.outbox` 재시도/타임아웃/복구 경계
   - `collector.analyze` KPI 계산 경계
3. flaky 테스트 기준 정의 및 격리

완료 기준:

- PR 단위 회귀 검출률 향상
- 핵심 경로(Policy/Uplink/Analyzer) 커버리지 상승
- flaky zero 또는 원인/우회 정책 명문화

---

## Phase 2: 아키텍처 분해와 계약 명세 (2~4주)

목표:

- 대형 모듈의 변경 리스크 축소.

작업:

1. `collector/analyze.py`를 서브모듈로 분해:
   - load/normalize
   - metric
   - kpi
   - plotting
   - report/audit
2. 공용 데이터 계약(JSON/Parquet schema) 버전 정책 수립
3. 공용 에러 코드/실패 모드 표준화

완료 기준:

- 기능 동등성 유지 + 파일 복잡도 감소
- 계약 위반 시 테스트가 즉시 실패
- 릴리스 노트에 하위호환성 영향 자동 표기

---

## Phase 3: 운영 신뢰성 (SRE 관점) (2~4주)

목표:

- "실험 가능"을 넘어 "운영 가능"으로 전환.

작업:

1. structured logging + correlation id(run_id/device_id/seq) 표준화
2. 메트릭 수집 표준화(Prometheus 또는 OTEL exporter)
3. 장애 대응 런북(runbook) 작성:
   - broker 다운
   - outbox backlog 급증
   - time sync 불안정
4. Error Budget/SLO 도입:
   - 전달 성공률
   - 분석 파이프라인 성공률
   - KPI 산출 성공률

완료 기준:

- 장애 시 탐지/원인 파악/복구 시간 단축
- 운영 이벤트에 대한 자동 알림/대응 가능

---

## Phase 4: 보안/규정 준수 강화 (1~2주)

목표:

- 실전 배포 가능한 최소 보안선 확보.

작업:

1. MQTT TLS/mTLS 기본 경로 검증 자동화
2. secret 관리 정책(.env, CI secrets, rotation) 문서화
3. dependency/SCA + 취약점 스캔 파이프라인
4. SBOM 생성 및 릴리스 아티팩트에 첨부

완료 기준:

- 평문 운용 경로 최소화
- 보안 이슈 탐지/수정 루프 CI 내재화

---

## Phase 5: 성능/비용 최적화 (지속)

목표:

- KPI PASS를 유지하면서 비용 효율 개선.

작업:

1. 정책 파라미터 sweep 자동화 및 회귀 대시보드
2. CPU/메모리/스토리지 profiling 자동 수집
3. Analyzer 성능 병목 제거(대용량 parquet 처리 최적화)

완료 기준:

- 동일 KPI에서 자원 사용량 감소
- 릴리스마다 성능 회귀 자동 검출

---

## 3. 우선순위 백로그 (즉시 착수 순)

P0:

1. 테스트 공백 해소 (timeutil/store_sqlite)
2. CI coverage gate 상향
3. 엔트리포인트 smoke check 추가

P1:

1. analyzer 분해 설계/이행
2. outbox/mqtt 재전송 경계 테스트 강화

P2:

1. observability/SLO 표준화
2. 보안 자동화 파이프라인

---

## 4. 진행 관리 방식

각 단계는 아래 템플릿으로 관리한다.

- 목표 지표(숫자)
- 작업 항목(코드/테스트/문서)
- 위험/의존성
- 종료 판정(Go/No-Go)

변경은 항상 아래 순서로 병합한다.

1. 테스트
2. 구현
3. 문서/런북
4. CI 게이트 상향

