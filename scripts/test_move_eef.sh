#!/usr/bin/env bash
set -euo pipefail

COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
ARM="${ARM:-right}"
DURATION_S="${DURATION_S:-1.0}"

if [[ $# -ne 3 ]]; then
  echo "Usage: ARM=right bash scripts/test_move_eef.sh <x_camera_m> <y_camera_m> <z_camera_m>" >&2
  exit 2
fi

curl -sS -X POST "${COROBOT_URL}/skill/move_eef" \
  -H "Content-Type: application/json" \
  -d "{
    \"arm\": \"${ARM}\",
    \"target_position_camera_m\": [$1, $2, $3],
    \"duration_s\": ${DURATION_S}
  }"
