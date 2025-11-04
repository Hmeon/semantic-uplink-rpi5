# semantic-uplink-rpi5

AIoT 의미전송(Semantic Uplink) PoC — 라즈베리 파이 5에서 **센서 → 엣지 예측 → 정책(LinUCB) → 의미전송(MQTT + Outbox) → 링크 에뮬(tc) → 수집/평가(AoI/MAE/Rate)** 파이프라인.

## 모듈 개요
- `edge/`: 센서 샘플링, 예측(EWMA/AR1), 정책(LinUCB), 업로더(MQTT/Outbox), UI
- `collector/`: 브로커 구독 → 복원 → 지표(AoI/MAE/Rate) 계산/저장
- `link/`: `tc`를 이용한 링크 제약 적용/해제
- `common/`: 스키마/양자화/AoI/시간/MQTT 유틸
- `experiments/`: 기준선/고정 τ/적응 정책 자동 실행 스크립트
- `configs/`: device/policy/link 프로파일 (코드 없이 실험 바꿈)
- `infra/`: Mosquitto 등 인프라 설정
- `docs/`: Figma 도면·사양 문서
- `tests/`: 단위/통합 테스트

## 빠른 시작
1) Mosquitto 실행, `tc` 권한 확인
2) `collector/collector.py` 실행(구독/로그)
3) `edge/edge_daemon.py` 실행(주기→고정τ→적응 정책 순서)
4) `collector/analyze.py`로 로그 집계 및 파레토 곡선 생성

> 이 저장소는 **스캐폴딩**입니다. 각 파일의 `TODO`를 따라 구현하세요.
