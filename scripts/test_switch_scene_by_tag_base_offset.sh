#!/usr/bin/env bash
set -euo pipefail

COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
CURL_NO_PROXY="${CURL_NO_PROXY:-*}"
ARM="${ARM:-right}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MOVE_DURATION_S="${MOVE_DURATION_S:-2.0}"
GRIPPER_DURATION_S="${GRIPPER_DURATION_S:-0.5}"
PRESS_INTERVAL_S="${PRESS_INTERVAL_S:-3.0}"
BUTTON_TAG_ID="${BUTTON_TAG_ID:-21}"
EXECUTE_SWITCH_SCENE="${EXECUTE_SWITCH_SCENE:-0}"

usage() {
  cat >&2 <<'EOF'
Usage:
  ARM=right bash scripts/test_switch_scene_by_tag_base_offset.sh

  ARM=right bash scripts/test_switch_scene_by_tag_base_offset.sh <tag_id>

  ARM=right bash scripts/test_switch_scene_by_tag_base_offset.sh \
    <dx_base_m> <dy_base_m> <dz_base_m>

  ARM=right bash scripts/test_switch_scene_by_tag_base_offset.sh \
    <tag_id> <dx_base_m> <dy_base_m> <dz_base_m>

Example dry-run:
  ARM=right bash scripts/test_switch_scene_by_tag_base_offset.sh
  ARM=right bash scripts/test_switch_scene_by_tag_base_offset.sh 21 0.00 0.00 -0.01

Execute on robot:
  ARM=right PRESS_INTERVAL_S=3.0 EXECUTE_SWITCH_SCENE=1 bash scripts/test_switch_scene_by_tag_base_offset.sh \
    19 0.00 0.00 -0.01

This calls /skill/switch_scene. The skill itself:
  1. Detects the requested button tag, default tag 21.
  2. Closes the selected gripper.
  3. Moves above the button, presses down, lifts up.
  4. Waits PRESS_INTERVAL_S.
  5. Presses down and lifts up once more.

Environment:
  COROBOT_URL             default http://localhost:8765
  CURL_NO_PROXY           default *, passed to curl --noproxy to avoid local proxy timeouts
  ARM                     default right
  BUTTON_TAG_ID           default 21, overridden by CLI tag_id
  MOVE_DURATION_S         default 2.0
  GRIPPER_DURATION_S      default 0.5
  PRESS_INTERVAL_S        default 3.0, seconds to wait between the two presses
  EXECUTE_SWITCH_SCENE    default 0; set 1 to POST the skill request
EOF
}

case "$#" in
  0)
    set -- 0.0 0.0 -0.01
    ;;
  1)
    BUTTON_TAG_ID="$1"
    set -- 0.0 0.0 -0.01
    ;;
  3)
    ;;
  4)
    BUTTON_TAG_ID="$1"
    shift
    ;;
  *)
    usage
    exit 2
    ;;
esac

if [[ $# -ne 3 ]]; then
  usage
  exit 2
fi

export ARM BUTTON_TAG_ID
export MOVE_DURATION_S GRIPPER_DURATION_S PRESS_INTERVAL_S

payload="$(
  "${PYTHON_BIN}" - "$1" "$2" "$3" <<'PY'
from __future__ import annotations

import json
import os
import sys

payload = {
    "arm": os.environ["ARM"],
    "button_tag_id": int(os.environ["BUTTON_TAG_ID"]),
    "base_offset_m": [float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])],
    "move_duration_s": float(os.environ["MOVE_DURATION_S"]),
    "gripper_duration_s": float(os.environ["GRIPPER_DURATION_S"]),
    "press_interval_s": float(os.environ["PRESS_INTERVAL_S"]),
}
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY
)"

echo "${payload}"

if [[ "${EXECUTE_SWITCH_SCENE}" != "1" ]]; then
  echo
  echo "Dry-run only. Set EXECUTE_SWITCH_SCENE=1 to call /skill/switch_scene." >&2
  exit 0
fi

route_probe_file="$(mktemp)"
response_file="$(mktemp)"
trap 'rm -f "${route_probe_file}" "${response_file}"' EXIT

if ! route_status="$(
  curl --noproxy "${CURL_NO_PROXY}" -sS -o "${route_probe_file}" -w "%{http_code}" \
    -X OPTIONS "${COROBOT_URL}/skill/switch_scene"
)"; then
  echo "Could not reach CoRobot service: ${COROBOT_URL}" >&2
  exit 1
fi

if [[ "${route_status}" == "404" ]]; then
  echo "Route is not registered: ${COROBOT_URL}/skill/switch_scene" >&2
  echo "Reload RuleControlTask or restart CoRobot after pulling the latest code." >&2
  echo "Example reload command:" >&2
  echo "curl --noproxy '*' -sS -X POST ${COROBOT_URL}/system/load_policytask \\" >&2
  echo "  -H 'Content-Type: application/json' \\" >&2
  echo "  -d '{\"policy_module\":\"corobot.policy_tasks.rule_control_task\",\"class_name\":\"RuleControlTask\",\"config_path\":\"/home/ck/RoboClaw/.a2d_pkg/corobot/config/rule_control_task_config.yml\"}' | python3 -m json.tool" >&2
  exit 1
fi

if ! http_code="$(
  curl --noproxy "${CURL_NO_PROXY}" -sS -o "${response_file}" -w "%{http_code}" \
    -X POST "${COROBOT_URL}/skill/switch_scene" \
    -H "Content-Type: application/json" \
    -d "${payload}"
)"; then
  echo "Request failed before receiving an HTTP response: ${COROBOT_URL}/skill/switch_scene" >&2
  exit 1
fi

if [[ ! "${http_code}" =~ ^2 ]]; then
  echo "Request failed: HTTP ${http_code} from ${COROBOT_URL}/skill/switch_scene" >&2
  if [[ -s "${response_file}" ]]; then
    "${PYTHON_BIN}" -m json.tool <"${response_file}" 2>/dev/null || sed -n '1,80p' "${response_file}" >&2
  else
    echo "Response body is empty." >&2
  fi
  exit 1
fi

if [[ ! -s "${response_file}" ]]; then
  echo "Request succeeded with HTTP ${http_code}, but response body is empty." >&2
  echo "Check whether the running CoRobot service exposes /skill/switch_scene." >&2
  exit 1
fi

if ! "${PYTHON_BIN}" -m json.tool <"${response_file}"; then
  echo "Response is not valid JSON. Raw response begins:" >&2
  sed -n '1,80p' "${response_file}" >&2
  exit 1
fi
