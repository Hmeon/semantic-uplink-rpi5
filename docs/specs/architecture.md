# 아키텍처 스펙(요약)

## 목표
- 전송량 ≥60% 절감, AoI ≥30% 개선, MAE 증가 ≤10%

## 계층
- 센서(Perception) → 엣지 → 링크 → 브로커 → 수집/분석 → UI

## 데이터 모델
- Event, PolicyDecision (see `common/schema.py`)

## 운영
- 링크 프로파일(tc) 3종, 실험 자동화 스크립트, 로그 표준 경로
