#!/usr/bin/env bash
set -euo pipefail
# TODO: venv 활성화 및 edge 실행
python -m edge.edge_daemon --mode periodic
