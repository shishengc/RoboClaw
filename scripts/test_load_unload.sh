#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
CURL_NO_PROXY="${CURL_NO_PROXY:-*}"
CURL_MAX_TIME_S="${CURL_MAX_TIME_S:-30}"
JSON_PYTHON_BIN="${JSON_PYTHON_BIN:-${PYTHON_BIN:-python3}}"
ROBOT_PYTHON_BIN="${ROBOT_PYTHON_BIN:-${PYTHON_BIN:-/home/ck/miniconda3/envs/robot/bin/python}}"

ARM="${ARM:-right}"
CAMERA_FRAME="${CAMERA_FRAME:-head_camera_optical}"
TASK_CONFIG_PATH="${TASK_CONFIG_PATH:-${REPO_ROOT}/.a2d_pkg/corobot/config/rule_control_task_config.yml}"
MOVE_DURATION_S="${MOVE_DURATION_S:-2.0}"
GRIPPER_DURATION_S="${GRIPPER_DURATION_S:-0.5}"
APPROACH_DISTANCE_M="${APPROACH_DISTANCE_M:-0.03}"
PRE_GRASP_NEG_X_DISTANCE_M="${PRE_GRASP_NEG_X_DISTANCE_M:-${APPROACH_DISTANCE_M}}"
PRE_GRASP_LIFT_Z_M="${PRE_GRASP_LIFT_Z_M:-0.05}"
PULL_PROMPT="${PULL_PROMPT:-Pull open the drawer}"
PUSH_PROMPT="${PUSH_PROMPT:-Push close the drawer}"
PULL_POLICY_PORT="${PULL_POLICY_PORT:-8998}"
PUSH_POLICY_PORT="${PUSH_POLICY_PORT:-8999}"
PULL_CHUNK_COUNT="${PULL_CHUNK_COUNT:-20}"
PUSH_CHUNK_COUNT="${PUSH_CHUNK_COUNT:-30}"
POLICY_POLL_INTERVAL_S="${POLICY_POLL_INTERVAL_S:-1.0}"
POLICY_TIMEOUT_S="${POLICY_TIMEOUT_S:-300}"
EXPECT_PULL_PREPOSE="${EXPECT_PULL_PREPOSE:-1}"

SOURCE_TAG_ID="${SOURCE_TAG_ID:-5}"
DEST_TAG_ID="${DEST_TAG_ID:-6}"
GRASP_OFFSET_X="${GRASP_OFFSET_X:-0.0}"
GRASP_OFFSET_Y="${GRASP_OFFSET_Y:-0.0}"
GRASP_OFFSET_Z="${GRASP_OFFSET_Z:--0.04}"
PLACE_OFFSET_X="${PLACE_OFFSET_X:-0.0}"
PLACE_OFFSET_Y="${PLACE_OFFSET_Y:-0.00}"
PLACE_OFFSET_Z="${PLACE_OFFSET_Z:-0.20}"
PLACE_DESCEND_DZ_BASE_M="${PLACE_DESCEND_DZ_BASE_M:-0.15}"
EXECUTE_PICK_PLACE="${EXECUTE_PICK_PLACE:-1}"
LOAD_UNLOAD_STAGE="${LOAD_UNLOAD_STAGE:-all}"

usage() {
  cat >&2 <<'EOF'
Usage:
  bash scripts/test_load_unload.sh
  PULL_CHUNK_COUNT=25 PUSH_CHUNK_COUNT=25 bash scripts/test_load_unload.sh


curl -sS -X POST http://localhost:8765/skill/start_policy \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Pull open the drawer",
    "port": 8998,
    "chunk_count": 25
  }' | python3 -m json.tool
  
curl -sS -X POST http://localhost:8765/skill/start_policy \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Push close the drawer",
    "port": 8999,
    "chunk_count": 25
  }' | python3 -m json.tool

This script runs a four-stage load/unload flow:
  1. POST /skill/start_policy to pull open the drawer.
  2. Pick source tag 5 after opening the gripper and moving from the current
     EEF pose to a base_link -X/Y pre-grasp point with a Z lift,
     move vertically to the tag 6 lift Z, move horizontally to tag 6 X/Y,
     descend in Z, and open the gripper.
  3. Reset the robot.
  4. POST /skill/start_policy to push close the drawer.

Environment:
  COROBOT_URL             default http://localhost:8765
  CURL_NO_PROXY           default *, passed to curl --noproxy for local calls
  JSON_PYTHON_BIN         default ${PYTHON_BIN:-python3}, used only for JSON formatting/parsing
  ROBOT_PYTHON_BIN        default ${PYTHON_BIN:-/home/ck/miniconda3/envs/robot/bin/python}
  ARM                     default right
  CAMERA_FRAME            default head_camera_optical
  TASK_CONFIG_PATH        default .a2d_pkg/corobot/config/rule_control_task_config.yml
  MOVE_DURATION_S         default 2.0
  GRIPPER_DURATION_S      default 0.5
  APPROACH_DISTANCE_M     default 0.03, fallback default for pre-grasp move distances
  PRE_GRASP_NEG_X_DISTANCE_M
                          default APPROACH_DISTANCE_M, base_link -X distance before grasp
  PRE_GRASP_LIFT_Z_M      default APPROACH_DISTANCE_M, base_link +Z lift before grasp

  PULL_PROMPT             default "Pull open the drawer"
  PUSH_PROMPT             default "Push close the drawer"
  PULL_POLICY_PORT        default 8998
  PUSH_POLICY_PORT        default 8999
  PULL_CHUNK_COUNT        default 20
  PUSH_CHUNK_COUNT        default 30
  POLICY_TIMEOUT_S        default 300
  POLICY_POLL_INTERVAL_S  default 1.0
  EXPECT_PULL_PREPOSE     default 1; verify Pull open drawer triggered pre-policy waist pose

  SOURCE_TAG_ID           default 5
  DEST_TAG_ID             default 6
  GRASP_OFFSET_X/Y/Z      default 0.0, 0.0, -0.04 in base_link meters
  PLACE_OFFSET_X/Y/Z      default 0.0, 0.0, 0.03 in base_link meters; this is the tag 6 lift point
  PLACE_DESCEND_DZ_BASE_M default 0.03, positive base_link Z distance to move down before release
  EXECUTE_PICK_PLACE      default 1; set 0 to dry-run only for the tag pick-place stage
  LOAD_UNLOAD_STAGE       default all; one of all, pull, place, push

Example:
  ARM=right bash scripts/test_load_unload.sh

  ARM=right POLICY_TIMEOUT_S=420 GRASP_OFFSET_Z=-0.05 bash scripts/test_load_unload.sh
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$#" -ne 0 ]]; then
  usage
  exit 2
fi

case "${LOAD_UNLOAD_STAGE}" in
  all|pull|place|push)
    ;;
  *)
    echo "LOAD_UNLOAD_STAGE must be one of: all, pull, place, push" >&2
    exit 2
    ;;
esac

if ! command -v "${JSON_PYTHON_BIN}" >/dev/null 2>&1; then
  echo "JSON_PYTHON_BIN is not executable: ${JSON_PYTHON_BIN}" >&2
  exit 1
fi

if ! command -v "${ROBOT_PYTHON_BIN}" >/dev/null 2>&1; then
  echo "ROBOT_PYTHON_BIN is not executable: ${ROBOT_PYTHON_BIN}" >&2
  exit 1
fi

TMP_FILES=()
cleanup() {
  if [[ "${#TMP_FILES[@]}" -gt 0 ]]; then
    rm -f "${TMP_FILES[@]}"
  fi
}
trap cleanup EXIT

make_tmp() {
  local path
  path="$(mktemp)"
  TMP_FILES+=("${path}")
  printf '%s\n' "${path}"
}

print_json_or_raw() {
  local response_file="$1"
  if ! "${JSON_PYTHON_BIN}" -m json.tool <"${response_file}"; then
    echo "Response is not valid JSON. Raw response begins:" >&2
    sed -n '1,80p' "${response_file}" >&2
    return 1
  fi
}

assert_skill_response_ok() {
  local response_file="$1"
  "${JSON_PYTHON_BIN}" - "${response_file}" <<'PY'
from __future__ import annotations

import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = json.load(f)

if isinstance(data, dict) and data.get("success") is False:
    print(data.get("error") or data.get("message") or "request returned success=false", file=sys.stderr)
    sys.exit(1)

payload = data.get("data", data) if isinstance(data, dict) else data
if isinstance(payload, dict) and payload.get("ok") is False:
    print(payload.get("message") or payload.get("last_error") or "skill returned ok=false", file=sys.stderr)
    sys.exit(1)
PY
}

build_policy_payload() {
  local prompt="$1"
  local port="$2"
  local chunk_count="$3"
  "${JSON_PYTHON_BIN}" - "${prompt}" "${port}" "${chunk_count}" <<'PY'
from __future__ import annotations

import json
import sys

payload = {
    "prompt": sys.argv[1],
    "port": int(sys.argv[2]),
    "chunk_count": int(sys.argv[3]),
}
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY
}

start_policy() {
  local label="$1"
  local prompt="$2"
  local port="$3"
  local chunk_count="$4"
  local payload_file
  local response_file
  local http_code

  payload_file="$(make_tmp)"
  response_file="$(make_tmp)"
  build_policy_payload "${prompt}" "${port}" "${chunk_count}" >"${payload_file}"

  echo
  echo "== ${label}: /skill/start_policy =="
  cat "${payload_file}"

  if ! http_code="$(
    curl --noproxy "${CURL_NO_PROXY}" --max-time "${CURL_MAX_TIME_S}" -sS \
      -o "${response_file}" -w "%{http_code}" \
      -X POST "${COROBOT_URL}/skill/start_policy" \
      -H "Content-Type: application/json" \
      -d @"${payload_file}"
  )"; then
    echo "Request failed before receiving an HTTP response: ${COROBOT_URL}/skill/start_policy" >&2
    exit 1
  fi

  if [[ ! "${http_code}" =~ ^2 ]]; then
    echo "Request failed: HTTP ${http_code} from ${COROBOT_URL}/skill/start_policy" >&2
    if [[ -s "${response_file}" ]]; then
      print_json_or_raw "${response_file}" || true
    else
      echo "Response body is empty." >&2
    fi
    exit 1
  fi

  if [[ ! -s "${response_file}" ]]; then
    echo "Request succeeded with HTTP ${http_code}, but response body is empty." >&2
    exit 1
  fi

  print_json_or_raw "${response_file}"
  assert_skill_response_ok "${response_file}"
}

wait_policy_done() {
  local label="$1"
  local response_file
  local http_code
  local started_s
  local now_s
  local elapsed_s
  local status_text
  local status_rc

  response_file="$(make_tmp)"
  started_s="$(date +%s)"

  echo
  echo "== Waiting for ${label} to finish =="
  while true; do
    now_s="$(date +%s)"
    elapsed_s="$((now_s - started_s))"
    if (( elapsed_s > POLICY_TIMEOUT_S )); then
      echo "Timed out waiting for ${label} after ${POLICY_TIMEOUT_S}s." >&2
      exit 1
    fi

    if ! http_code="$(
      curl --noproxy "${CURL_NO_PROXY}" --max-time "${CURL_MAX_TIME_S}" -sS \
        -o "${response_file}" -w "%{http_code}" \
        -X GET "${COROBOT_URL}/skill/policy_status"
    )"; then
      echo "Could not query policy status from ${COROBOT_URL}/skill/policy_status" >&2
      exit 1
    fi

    if [[ ! "${http_code}" =~ ^2 ]]; then
      echo "Policy status request failed: HTTP ${http_code}" >&2
      if [[ -s "${response_file}" ]]; then
        print_json_or_raw "${response_file}" || true
      fi
      exit 1
    fi

    set +e
    status_text="$(
      "${JSON_PYTHON_BIN}" - "${response_file}" <<'PY'
from __future__ import annotations

import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = json.load(f)

if isinstance(data, dict) and data.get("success") is False:
    print(data.get("error") or data.get("message") or "status request returned success=false")
    sys.exit(2)

status = data.get("data", data) if isinstance(data, dict) else {}
if not isinstance(status, dict):
    print("status response is not an object")
    sys.exit(2)

running = bool(status.get("running"))
completed = bool(status.get("completed"))
failed = bool(status.get("failed"))
ok = bool(status.get("ok", not failed))
executed = status.get("executed_chunks", 0)
target = status.get("target_chunks", 0)
message = status.get("message") or status.get("last_error") or ""
prepose = status.get("pre_policy_waist_result")
if isinstance(prepose, dict):
    prepose_text = "yes" if prepose.get("executed") else f"no:{prepose.get('reason', 'not executed')}"
else:
    prepose_text = "none"
print(
    f"running={running} completed={completed} failed={failed} "
    f"chunks={executed}/{target} prepose={prepose_text}"
    + (f" message={message}" if message else "")
)

if failed or not ok:
    sys.exit(2)
if running:
    sys.exit(1)
sys.exit(0)
PY
    )"
    status_rc="$?"
    set -e

    echo "[${label}] ${status_text}"
    case "${status_rc}" in
      0)
        break
        ;;
      1)
        sleep "${POLICY_POLL_INTERVAL_S}"
        ;;
      *)
        exit 1
        ;;
    esac
  done
}

assert_pull_prepose_done() {
  local response_file
  local http_code

  if [[ "${EXPECT_PULL_PREPOSE}" != "1" ]]; then
    return 0
  fi

  response_file="$(make_tmp)"
  if ! http_code="$(
    curl --noproxy "${CURL_NO_PROXY}" --max-time "${CURL_MAX_TIME_S}" -sS \
      -o "${response_file}" -w "%{http_code}" \
      -X GET "${COROBOT_URL}/skill/policy_status"
  )"; then
    echo "Could not verify pull pre-pose from ${COROBOT_URL}/skill/policy_status" >&2
    exit 1
  fi

  if [[ ! "${http_code}" =~ ^2 ]]; then
    echo "Could not verify pull pre-pose: HTTP ${http_code}" >&2
    print_json_or_raw "${response_file}" || true
    exit 1
  fi

  "${JSON_PYTHON_BIN}" - "${response_file}" <<'PY'
from __future__ import annotations

import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = json.load(f)

status = data.get("data", data) if isinstance(data, dict) else {}
prepose = status.get("pre_policy_waist_result") if isinstance(status, dict) else None
if isinstance(prepose, dict) and prepose.get("executed") is True:
    print("Pull pre-policy waist pose executed:")
    print(json.dumps(prepose, ensure_ascii=False, indent=2))
    sys.exit(0)

print("Pull pre-policy waist pose did not execute.", file=sys.stderr)
print("Current policy status:", file=sys.stderr)
print(json.dumps(status, ensure_ascii=False, indent=2), file=sys.stderr)
print(
    "Check that the running RuleControlTask is reloaded, the prompt is exactly "
    "'Pull open the drawer', and the policy websocket returned metadata.",
    file=sys.stderr,
)
sys.exit(1)
PY
}

run_tag_load_place() {
  export PYTHONPATH="${REPO_ROOT}/src:${REPO_ROOT}/.a2d_pkg:${REPO_ROOT}/.a2d_pkg/site-packages:${PYTHONPATH:-}"
  export COROBOT_URL CURL_NO_PROXY ARM CAMERA_FRAME TASK_CONFIG_PATH
  export MOVE_DURATION_S GRIPPER_DURATION_S APPROACH_DISTANCE_M PRE_GRASP_NEG_X_DISTANCE_M PRE_GRASP_LIFT_Z_M
  export SOURCE_TAG_ID DEST_TAG_ID EXECUTE_PICK_PLACE
  export GRASP_OFFSET_X GRASP_OFFSET_Y GRASP_OFFSET_Z
  export PLACE_OFFSET_X PLACE_OFFSET_Y PLACE_OFFSET_Z PLACE_DESCEND_DZ_BASE_M

  "${ROBOT_PYTHON_BIN}" <<'PY'
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.spatial.transform import Rotation as R

from corobot.utils.fk_solver import _find_urdf_solver_dir
from corobot.utils.kinematics import Kinematics
from mcp_control_demo.calibration import load_calibration_config


corobot_url = os.environ["COROBOT_URL"].rstrip("/")
arm = os.environ["ARM"]
camera_frame = os.environ["CAMERA_FRAME"]
task_config_path = os.environ["TASK_CONFIG_PATH"]
move_duration_s = float(os.environ["MOVE_DURATION_S"])
gripper_duration_s = float(os.environ["GRIPPER_DURATION_S"])
pre_grasp_neg_x_distance_m = float(os.environ["PRE_GRASP_NEG_X_DISTANCE_M"])
pre_grasp_lift_z_m = float(os.environ["PRE_GRASP_LIFT_Z_M"])
source_tag_id = int(os.environ["SOURCE_TAG_ID"])
dest_tag_id = int(os.environ["DEST_TAG_ID"])
execute = os.environ["EXECUTE_PICK_PLACE"] == "1"
grasp_offset = np.asarray(
    [float(os.environ["GRASP_OFFSET_X"]), float(os.environ["GRASP_OFFSET_Y"]), float(os.environ["GRASP_OFFSET_Z"])],
    dtype=np.float64,
)
place_offset = np.asarray(
    [float(os.environ["PLACE_OFFSET_X"]), float(os.environ["PLACE_OFFSET_Y"]), float(os.environ["PLACE_OFFSET_Z"])],
    dtype=np.float64,
)
place_descend_dz_base_m = float(os.environ["PLACE_DESCEND_DZ_BASE_M"])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{corobot_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=60.0) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        raise RuntimeError(f"HTTP {exc.code} from {path}: {text}") from exc

    data = json.loads(text)
    if not data.get("success", False):
        raise RuntimeError(f"{path} failed: {json.dumps(data, ensure_ascii=False)}")
    result = data.get("data", data)
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(f"{path} returned ok=false: {json.dumps(result, ensure_ascii=False)}")
    return result


def rounded(values: Any) -> list[float]:
    return [round(float(v), 6) for v in np.asarray(values, dtype=np.float64).reshape(-1)]


def calibration_from_reset_pose(path: str):
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    calibration = load_calibration_config(path)
    reset_pose = config.get("reset_pose") or {}
    head = reset_pose.get("target_head_positions")
    waist = reset_pose.get("target_waist_positions")
    if head is None or waist is None:
        raise RuntimeError("task config must contain reset_pose target_head_positions and target_waist_positions")
    if calibration.t_head_pitch_camera is None:
        raise RuntimeError("task config must contain mcp_control.extrinsics.T_head_pitch_camera")

    with redirect_stdout(StringIO()):
        kinematics = Kinematics(str(_find_urdf_solver_dir() / "A2D_viz.urdf"))
    xyzquat = kinematics.compute_head_fk(float(head[0]), float(head[1]), float(waist[0]), float(waist[1]))
    t_base_head = np.eye(4, dtype=np.float64)
    t_base_head[:3, :3] = R.from_quat(xyzquat[3:]).as_matrix()
    t_base_head[:3, 3] = np.asarray(xyzquat[:3], dtype=np.float64)
    return calibration.with_t_exec_camera(t_base_head @ calibration.t_head_pitch_camera)


def get_tag(tag_id: int) -> dict[str, Any]:
    tag = post("/skill/get_tag_pose", {"tag_id": tag_id})
    if not tag.get("ok", False):
        raise RuntimeError(f"tag {tag_id} is unavailable: {json.dumps(tag, ensure_ascii=False)}")
    return tag


calibration = calibration_from_reset_pose(task_config_path)

post("/skill/detect_tags", {})
source_tag = get_tag(source_tag_id)
dest_tag = get_tag(dest_tag_id)
current_eef = post("/skill/get_eef_pose", {"arm": arm})
if not current_eef.get("ok", False):
    raise RuntimeError(f"current EEF pose is unavailable: {json.dumps(current_eef, ensure_ascii=False)}")

source_tag_camera = np.asarray(source_tag["position_camera_m"], dtype=np.float64).reshape(3)
dest_tag_camera = np.asarray(dest_tag["position_camera_m"], dtype=np.float64).reshape(3)
current_eef_base = np.asarray(current_eef["position_exec_m"], dtype=np.float64).reshape(3)
current_eef_camera = np.asarray(current_eef["position_camera_m"], dtype=np.float64).reshape(3)
source_tag_base = calibration.camera_to_exec_point(source_tag_camera, camera_frame)
dest_tag_base = calibration.camera_to_exec_point(dest_tag_camera, camera_frame)

grasp_base = source_tag_base + grasp_offset
grasp_camera = calibration.exec_to_camera_point(grasp_base, camera_frame)
pre_grasp_neg_x_base = np.asarray(
    [current_eef_base[0] - pre_grasp_neg_x_distance_m, grasp_base[1], current_eef_base[2] + pre_grasp_lift_z_m],
    dtype=np.float64,
)
pre_grasp_neg_x_camera = calibration.exec_to_camera_point(pre_grasp_neg_x_base, camera_frame)

place_lift_base = dest_tag_base + place_offset
place_lift_camera = calibration.exec_to_camera_point(place_lift_base, camera_frame)

# After grasping, keep the grasp X/Y and change only base_link Z to the tag-6 lift point.
place_lift_z_base = np.asarray([grasp_base[0], grasp_base[1], place_lift_base[2]], dtype=np.float64)
place_lift_z_camera = calibration.exec_to_camera_point(place_lift_z_base, camera_frame)
place_final_base = place_lift_base + np.asarray([0.0, 0.0, -place_descend_dz_base_m], dtype=np.float64)
place_final_camera = calibration.exec_to_camera_point(place_final_base, camera_frame)

payloads = [
    (
        "open_gripper",
        "/skill/gripper",
        {"arm": arm, "gripper_value": 0.0, "duration_s": gripper_duration_s},
    ),
    (
        "move_to_grasp_neg_x_prealign",
        "/skill/move_eef",
        {"arm": arm, "target_position_camera_m": rounded(pre_grasp_neg_x_camera), "duration_s": move_duration_s},
    ),
    (
        "move_to_grasp",
        "/skill/move_eef",
        {"arm": arm, "target_position_camera_m": rounded(grasp_camera), "duration_s": move_duration_s},
    ),
    (
        "close_gripper",
        "/skill/gripper",
        {"arm": arm, "gripper_value": 1.0, "duration_s": gripper_duration_s},
    ),
    (
        "move_vertical_to_tag6_lift_z",
        "/skill/move_eef",
        {"arm": arm, "target_position_camera_m": rounded(place_lift_z_camera), "duration_s": move_duration_s},
    ),
    (
        "move_horizontal_to_tag6_lift_xy",
        "/skill/move_eef",
        {"arm": arm, "target_position_camera_m": rounded(place_lift_camera), "duration_s": move_duration_s},
    ),
    (
        "move_down_to_tag6_place",
        "/skill/move_eef",
        {"arm": arm, "target_position_camera_m": rounded(place_final_camera), "duration_s": move_duration_s},
    ),
    (
        "open_gripper_release",
        "/skill/gripper",
        {"arm": arm, "gripper_value": 0.0, "duration_s": gripper_duration_s},
    ),
    (
        "reset_robot_after_tag_place",
        "/skill/reset_robot",
        {},
    ),
]

plan = {
    "execute": execute,
    "arm": arm,
    "source_tag_id": source_tag_id,
    "dest_tag_id": dest_tag_id,
    "camera_frame": camera_frame,
    "move_eef_target_semantics": "target_position_camera_m is desired gripper-center TCP; control layer sends wrist/link7 target to A2D",
    "pre_grasp_strategy": "after opening the gripper, move in base_link to current_x - PRE_GRASP_NEG_X_DISTANCE_M, grasp Y, and current Z + PRE_GRASP_LIFT_Z_M",
    "place_strategy": "after grasp, move_eef changes base_link Z first while keeping grasp X/Y, then move_eef changes X/Y to tag 6 lift point, then move_eef descends in base_link Z, opens the gripper, and resets the robot before the next policy",
    "grasp_offset_base_m": rounded(grasp_offset),
    "pre_grasp_neg_x_distance_m": round(float(pre_grasp_neg_x_distance_m), 6),
    "pre_grasp_lift_z_m": round(float(pre_grasp_lift_z_m), 6),
    "place_lift_offset_base_m": rounded(place_offset),
    "place_descend_dz_base_m": round(float(place_descend_dz_base_m), 6),
    "source_tag_camera_m": rounded(source_tag_camera),
    "source_tag_base_m": rounded(source_tag_base),
    "dest_tag_camera_m": rounded(dest_tag_camera),
    "dest_tag_base_m": rounded(dest_tag_base),
    "current_eef_base_m": rounded(current_eef_base),
    "current_eef_camera_m": rounded(current_eef_camera),
    "grasp_base_m": rounded(grasp_base),
    "grasp_camera_m": rounded(grasp_camera),
    "pre_grasp_neg_x_base_m": rounded(pre_grasp_neg_x_base),
    "pre_grasp_neg_x_camera_m": rounded(pre_grasp_neg_x_camera),
    "place_lift_z_base_m": rounded(place_lift_z_base),
    "place_lift_z_camera_m": rounded(place_lift_z_camera),
    "place_lift_base_m": rounded(place_lift_base),
    "place_lift_camera_m": rounded(place_lift_camera),
    "place_final_base_m": rounded(place_final_base),
    "place_final_camera_m": rounded(place_final_camera),
    "payloads": [{"name": name, "path": path, "payload": payload} for name, path, payload in payloads],
}
print(json.dumps(plan, indent=2, ensure_ascii=False))

if not execute:
    print("\nDry-run only. Set EXECUTE_PICK_PLACE=1 to execute this sequence.", file=sys.stderr)
    sys.exit(0)

for name, path, payload in payloads:
    print(f"\n>>> {name}: {path}", file=sys.stderr)
    result = post(path, payload)
    print(json.dumps({"name": name, "result": result}, indent=2, ensure_ascii=False))
PY
}

echo "Load/unload sequence:"
echo "  stage selector: ${LOAD_UNLOAD_STAGE}"
echo "  1. Pull drawer policy: prompt='${PULL_PROMPT}', port=${PULL_POLICY_PORT}, chunks=${PULL_CHUNK_COUNT}"
echo "  2. Pick tag ${SOURCE_TAG_ID} and place on tag ${DEST_TAG_ID}"
echo "     grasp offset base_link m: [${GRASP_OFFSET_X}, ${GRASP_OFFSET_Y}, ${GRASP_OFFSET_Z}]"
echo "     pre-grasp base_link -X distance m: ${PRE_GRASP_NEG_X_DISTANCE_M}"
echo "     pre-grasp base_link +Z lift m: ${PRE_GRASP_LIFT_Z_M}"
echo "     place lift offset base_link m: [${PLACE_OFFSET_X}, ${PLACE_OFFSET_Y}, ${PLACE_OFFSET_Z}]"
echo "     place descend dz base_link m: ${PLACE_DESCEND_DZ_BASE_M}"
echo "  3. Reset robot after tag place"
echo "  4. Push drawer policy: prompt='${PUSH_PROMPT}', port=${PUSH_POLICY_PORT}, chunks=${PUSH_CHUNK_COUNT}"

if [[ "${LOAD_UNLOAD_STAGE}" == "all" || "${LOAD_UNLOAD_STAGE}" == "pull" ]]; then
  start_policy "Stage 1/4 pull open drawer" "${PULL_PROMPT}" "${PULL_POLICY_PORT}" "${PULL_CHUNK_COUNT}"
  wait_policy_done "pull open drawer policy"
  assert_pull_prepose_done
fi

if [[ "${LOAD_UNLOAD_STAGE}" == "all" || "${LOAD_UNLOAD_STAGE}" == "place" ]]; then
  echo
  echo "== Stage 2/4 pick tag ${SOURCE_TAG_ID} place on tag ${DEST_TAG_ID}; Stage 3/4 reset robot =="
  run_tag_load_place
fi

if [[ "${LOAD_UNLOAD_STAGE}" == "all" || "${LOAD_UNLOAD_STAGE}" == "push" ]]; then
  start_policy "Stage 4/4 push close drawer" "${PUSH_PROMPT}" "${PUSH_POLICY_PORT}" "${PUSH_CHUNK_COUNT}"
  wait_policy_done "push close drawer policy"
fi

echo
echo "Load/unload sequence finished."
