#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
ROBOT_PYTHON_BIN="${ROBOT_PYTHON_BIN:-${PYTHON_BIN:-/home/ck/miniconda3/envs/robot/bin/python}}"

ARM="${ARM:-right}"
CAMERA_FRAME="${CAMERA_FRAME:-head_camera_optical}"
TASK_CONFIG_PATH="${TASK_CONFIG_PATH:-${REPO_ROOT}/.a2d_pkg/corobot/config/rule_control_task_config.yml}"
MOVE_DURATION_S="${MOVE_DURATION_S:-2.0}"
GRIPPER_DURATION_S="${GRIPPER_DURATION_S:-0.5}"
APPROACH_DISTANCE_M="${APPROACH_DISTANCE_M:-0.03}"
PRE_GRASP_NEG_X_DISTANCE_M="${PRE_GRASP_NEG_X_DISTANCE_M:-${APPROACH_DISTANCE_M}}"

SOURCE_TAG_ID="${SOURCE_TAG_ID:-5}"
DEST_TAG_ID="${DEST_TAG_ID:-6}"
GRASP_OFFSET_X="${GRASP_OFFSET_X:-0.0}"
GRASP_OFFSET_Y="${GRASP_OFFSET_Y:-0.0}"
GRASP_OFFSET_Z="${GRASP_OFFSET_Z:--0.04}"
PLACE_OFFSET_X="${PLACE_OFFSET_X:-0.0}"
PLACE_OFFSET_Y="${PLACE_OFFSET_Y:-0.0}"
PLACE_OFFSET_Z="${PLACE_OFFSET_Z:-0.20}"
PLACE_DESCEND_DZ_BASE_M="${PLACE_DESCEND_DZ_BASE_M:-0.15}"
EXECUTE_PICK_PLACE="${EXECUTE_PICK_PLACE:-0}"

usage() {
  cat >&2 <<'EOF'
Usage:
  ARM=right bash scripts/test_pick_tag5_place_on_tag6_base_offset.sh

  ARM=right bash scripts/test_pick_tag5_place_on_tag6_base_offset.sh \
    <grasp_dx_base_m> <grasp_dy_base_m> <grasp_dz_base_m>

  ARM=right bash scripts/test_pick_tag5_place_on_tag6_base_offset.sh \
    <grasp_dx_base_m> <grasp_dy_base_m> <grasp_dz_base_m> \
    <place_dx_base_m> <place_dy_base_m> <place_dz_base_m>

  ARM=right bash scripts/test_pick_tag5_place_on_tag6_base_offset.sh \
    <grasp_dx_base_m> <grasp_dy_base_m> <grasp_dz_base_m> \
    <place_dx_base_m> <place_dy_base_m> <place_dz_base_m> \
    <place_descend_dz_base_m>

Execute on robot:
  ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag5_place_on_tag6_base_offset.sh

Behavior:
  1. Detect AprilTags.
  2. Pick the object at tag 5.
     After opening the gripper, move horizontally to a pre-grasp point at
     current_x - PRE_GRASP_NEG_X_DISTANCE_M, grasp_y, current_z in base_link.
  3. Move with /skill/move_eef to the tag 6 lift Z while keeping the grasp X/Y in base_link.
  4. Move with /skill/move_eef horizontally to tag 6 X/Y at that lift Z.
  5. Move with /skill/move_eef vertically down by PLACE_DESCEND_DZ_BASE_M.
  6. Open the gripper.
  7. Reset the robot through /skill/reset_robot.

Environment:
  COROBOT_URL             default http://localhost:8765
  ROBOT_PYTHON_BIN        default ${PYTHON_BIN:-/home/ck/miniconda3/envs/robot/bin/python}
  ARM                     default right
  SOURCE_TAG_ID           default 5
  DEST_TAG_ID             default 6
  CAMERA_FRAME            default head_camera_optical
  TASK_CONFIG_PATH        default .a2d_pkg/corobot/config/rule_control_task_config.yml
  MOVE_DURATION_S         default 2.0
  GRIPPER_DURATION_S      default 0.5
  APPROACH_DISTANCE_M     default 0.10, fallback default for PRE_GRASP_NEG_X_DISTANCE_M
  PRE_GRASP_NEG_X_DISTANCE_M
                          default APPROACH_DISTANCE_M, base_link -X distance before grasp
  GRASP_OFFSET_X/Y/Z      default 0.0, 0.0, -0.04 in base_link meters
  PLACE_OFFSET_X/Y/Z      default 0.0, 0.0, 0.10 in base_link meters; this is the tag 6 lift point
  PLACE_DESCEND_DZ_BASE_M default 0.07, positive base_link Z distance to move down before release
  EXECUTE_PICK_PLACE      default 0; set 1 to execute

Notes:
  The motion path after grasping is intentionally split into two move_eef calls:
  first a base_link-Z-only target, then a base_link-XY target at the same lift Z,
  then a base_link-Z-only descent target before opening the gripper.
  Keep head/waist at the reset pose used by TASK_CONFIG_PATH for this planning helper.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

case "$#" in
  0)
    ;;
  3)
    GRASP_OFFSET_X="$1"
    GRASP_OFFSET_Y="$2"
    GRASP_OFFSET_Z="$3"
    ;;
  6)
    GRASP_OFFSET_X="$1"
    GRASP_OFFSET_Y="$2"
    GRASP_OFFSET_Z="$3"
    PLACE_OFFSET_X="$4"
    PLACE_OFFSET_Y="$5"
    PLACE_OFFSET_Z="$6"
    ;;
  7)
    GRASP_OFFSET_X="$1"
    GRASP_OFFSET_Y="$2"
    GRASP_OFFSET_Z="$3"
    PLACE_OFFSET_X="$4"
    PLACE_OFFSET_Y="$5"
    PLACE_OFFSET_Z="$6"
    PLACE_DESCEND_DZ_BASE_M="$7"
    ;;
  *)
    usage
    exit 2
    ;;
esac

if ! command -v "${ROBOT_PYTHON_BIN}" >/dev/null 2>&1; then
  echo "ROBOT_PYTHON_BIN is not executable: ${ROBOT_PYTHON_BIN}" >&2
  exit 1
fi

export PYTHONPATH="${REPO_ROOT}/src:${REPO_ROOT}/.a2d_pkg:${REPO_ROOT}/.a2d_pkg/site-packages:${PYTHONPATH:-}"
export COROBOT_URL ARM CAMERA_FRAME TASK_CONFIG_PATH
export MOVE_DURATION_S GRIPPER_DURATION_S APPROACH_DISTANCE_M PRE_GRASP_NEG_X_DISTANCE_M
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
    [current_eef_base[0] - pre_grasp_neg_x_distance_m, grasp_base[1], current_eef_base[2]],
    dtype=np.float64,
)
pre_grasp_neg_x_camera = calibration.exec_to_camera_point(pre_grasp_neg_x_base, camera_frame)

place_lift_base = dest_tag_base + place_offset
place_lift_camera = calibration.exec_to_camera_point(place_lift_base, camera_frame)

# Keep base_link X/Y from the grasp target and change only Z to the tag-6 lift point.
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
        "reset_robot",
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
    "pre_grasp_strategy": "after opening the gripper, move horizontally in base_link to current_x - PRE_GRASP_NEG_X_DISTANCE_M and grasp Y while keeping current Z",
    "place_strategy": "after grasp, move_eef changes base_link Z first while keeping grasp X/Y, then move_eef changes X/Y to tag 6 lift point, then move_eef descends in base_link Z before opening gripper",
    "grasp_offset_base_m": rounded(grasp_offset),
    "pre_grasp_neg_x_distance_m": round(float(pre_grasp_neg_x_distance_m), 6),
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
