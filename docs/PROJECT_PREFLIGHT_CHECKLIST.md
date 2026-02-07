# Project Preflight Checklist (Mandatory Before Any Change Batch)

이 체크리스트는 실전 프로젝트 변경 전에 "전체 맥락 파악"을 강제하기 위한 운영 기준이다.

## 1) 워크트리/변경 안정성

1. `git status --short` 확인
2. 예상 외 변경 파일 존재 시 범위 분리
3. 작업 범위 밖 파일 수정 금지

## 2) 아키텍처/의도 동기화

1. 프로젝트 목표 재확인:
   - `docs/specs/PROJECT_GOALS.md`
   - `docs/specs/architecture.md`
2. 현재 README/운영 경로 확인:
   - `README.md`
   - `scripts/run_stack.sh`
   - `scripts/run_3h_sequence.sh`

## 3) 품질 게이트 기준선 측정

1. `ruff check .`
2. `pytest -q --cov=common --cov=collector --cov=edge --cov=link --cov-report=term-missing`
3. 스킵/실패 원인 기록

## 4) 핵심 경로 영향 분석

변경 예정 코드가 아래 경로에 미치는 영향 여부 확인:

1. 정책/결정: `edge/policy/runtime.py`, `edge/policy/linucb.py`
2. 전송 신뢰성: `edge/uploader/outbox.py`, `edge/uploader/mqtt_publisher.py`
3. 수집/분석/KPI: `collector/collector.py`, `collector/analyze.py`
4. 실험/운영: `experiments/run_scenarios.py`, `stack/pi_stack.py`

## 5) 배치 종료 조건

1. 린트 통과
2. 테스트 통과
3. 변경 목적 대비 정량 결과 제시
4. 다음 배치 우선순위 제시

