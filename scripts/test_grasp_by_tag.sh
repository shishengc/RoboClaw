#!/usr/bin/env bash
set -euo pipefail

COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
ARM="${ARM:-right}"
CAMERA_FRAME="${CAMERA_FRAME:-head_camera_optical}"

if [[ $# -ne 1 ]]; then
  echo "Usage: ARM=right bash scripts/test_grasp_by_tag.sh <tag_id>" >&2
  exit 2
fi

curl -sS -X POST "${COROBOT_URL}/skill/grasp_by_tag" \
  -H "Content-Type: application/json" \
  -d "{
    \"arm\": \"${ARM}\",
    \"tag_id\": $1,
    \"camera_frame\": \"${CAMERA_FRAME}\",
    \"approach_distance_m\": 0.06,
    \"lift_height_m\": 0.10,
    \"move_duration_s\": 1.0,
    \"gripper_duration_s\": 0.5
  }"
