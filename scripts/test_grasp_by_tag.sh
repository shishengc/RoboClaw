#!/usr/bin/env bash
set -euo pipefail

COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
ARM="${ARM:-right}"
CAMERA_FRAME="${CAMERA_FRAME:-head_camera_optical}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -ne 1 ]]; then
  echo "Usage: ARM=right bash scripts/test_grasp_by_tag.sh <tag_id>" >&2
  exit 2
fi

export COROBOT_URL ARM CAMERA_FRAME
export MOVE_DURATION_S="${MOVE_DURATION_S:-1.0}"
export GRIPPER_DURATION_S="${GRIPPER_DURATION_S:-0.5}"
export APPROACH_DISTANCE_M="${APPROACH_DISTANCE_M:-0.06}"
export LIFT_DZ_BASE_M="${LIFT_DZ_BASE_M:-0.10}"
export EXECUTE_GRASP="${EXECUTE_GRASP:-1}"

bash "${SCRIPT_DIR}/test_grasp_by_tag_base_offset.sh" "$1" 0.0 0.0 0.0
