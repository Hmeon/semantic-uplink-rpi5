#!/usr/bin/env bash
set -euo pipefail
DEV=${1:-lo}
PROFILE=${2:-slow_10kbps}
python - <<PY
from link.shaper.tc_profiles import apply
apply("${1 if False else '${DEV}'}", "${2 if False else '${PROFILE}'}")
PY
