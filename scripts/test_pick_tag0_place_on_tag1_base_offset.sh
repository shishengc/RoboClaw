#!/usr/bin/env bash
set -euo pipefail

COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
ARM="${ARM:-right}"
CAMERA_FRAME="${CAMERA_FRAME:-head_camera_optical}"
TASK_CONFIG_PATH="${TASK_CONFIG_PATH:-/home/ck/RoboClaw/.a2d_pkg/corobot/config/rule_control_task_config.yml}"
PYTHON_BIN="${PYTHON_BIN:-/home/ck/miniconda3/envs/robot/bin/python}"
MOVE_DURATION_S="${MOVE_DURATION_S:-2.0}"
GRIPPER_DURATION_S="${GRIPPER_DURATION_S:-0.5}"
APPROACH_DISTANCE_M="${APPROACH_DISTANCE_M:-0.06}"
LIFT_DZ_BASE_M="${LIFT_DZ_BASE_M:-0.10}"
PLACE_HOVER_DZ_BASE_M="${PLACE_HOVER_DZ_BASE_M:-0.10}"
SOURCE_TAG_ID="${SOURCE_TAG_ID:-0}"
DEST_TAG_ID="${DEST_TAG_ID:-1}"
EXECUTE_PICK_PLACE="${EXECUTE_PICK_PLACE:-0}"

usage() {
  cat >&2 <<'EOF'
Usage:
  ARM=right bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
    <source_tag_id> <dest_tag_id> \
    <grasp_dx_base_m> <grasp_dy_base_m> <grasp_dz_base_m>

  ARM=right bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
    <source_tag_id> <dest_tag_id> \
    <grasp_dx_base_m> <grasp_dy_base_m> <grasp_dz_base_m> \
    <place_dx_base_m> <place_dy_base_m> <place_dz_base_m>

Legacy usage, still supported through SOURCE_TAG_ID / DEST_TAG_ID env:
  ARM=right bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
    <grasp_dx_base_m> <grasp_dy_base_m> <grasp_dz_base_m>

  ARM=right bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
    <grasp_dx_base_m> <grasp_dy_base_m> <grasp_dz_base_m> \
    <place_dx_base_m> <place_dy_base_m> <place_dz_base_m>

Example dry-run:
  ARM=right bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh 0 2 0.00 0.00 -0.07

Execute on robot:
  ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
    0 1 0.00 0.00 -0.045 0.0 -0.0015 0.02

  ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
    2 3 0.00 0.00 -0.06 0 0 0.10

  ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
    0 4 -0.01 0.01 -0.07 0 0 0.10

  ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
    17 2 0.015 0.00 -0.05 0.015 -0.01 0.02

Behavior:
  1. Detect AprilTags.
  2. Read source tag and destination tag from CLI args, or env defaults.
  3. Grasp the object at source tag with the provided base_link offset.
  4. Lift in base_link +Z.
  5. Move above destination tag, descend to the placement target, and open gripper.

Environment:
  COROBOT_URL             default http://localhost:8765
  ARM                     default right
  SOURCE_TAG_ID           default 0, overridden by CLI source_tag_id
  DEST_TAG_ID             default 1, overridden by CLI dest_tag_id
  CAMERA_FRAME            default head_camera_optical
  TASK_CONFIG_PATH        default .a2d_pkg/corobot/config/rule_control_task_config.yml
  MOVE_DURATION_S         default 2.0
  GRIPPER_DURATION_S      default 0.5
  APPROACH_DISTANCE_M     default 0.06, applied along configured camera_approach_axis
  LIFT_DZ_BASE_M          default 0.10, applied as +Z in base_link after closing
  PLACE_HOVER_DZ_BASE_M   default 0.10, applied as +Z in base_link above tag 1
  EXECUTE_PICK_PLACE      default 0; set 1 to execute

Notes:
  If only one offset is provided, it is used for both grasping the source tag
  and placing relative to the destination tag. This keeps the held object's
  tag-relative grasp geometry consistent during placement.
  Base-link offsets in this helper are converted by dynamically composing
  T_base_head_pitch(reset_pose) * T_head_pitch_camera from the task config.
  Keep head/waist at reset_pose for this planning helper, or prefer direct
  camera-frame targets.
EOF
}

case "$#" in
  3|6)
    ;;
  5|8)
    SOURCE_TAG_ID="$1"
    DEST_TAG_ID="$2"
    shift 2
    ;;
  *)
    usage
    exit 2
    ;;
esac

if [[ $# -ne 3 && $# -ne 6 ]]; then
  usage
  exit 2
fi

export PYTHONPATH="/home/ck/RoboClaw/src:/home/ck/RoboClaw/.a2d_pkg:${PYTHONPATH:-}"
export COROBOT_URL ARM CAMERA_FRAME TASK_CONFIG_PATH
export MOVE_DURATION_S GRIPPER_DURATION_S APPROACH_DISTANCE_M
export LIFT_DZ_BASE_M PLACE_HOVER_DZ_BASE_M SOURCE_TAG_ID DEST_TAG_ID EXECUTE_PICK_PLACE

"${PYTHON_BIN}" - "$@" <<'PY'
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


grasp_offset = np.asarray([float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])], dtype=np.float64)
if len(sys.argv) == 7:
    place_offset = np.asarray([float(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6])], dtype=np.float64)
else:
    place_offset = grasp_offset.copy()

corobot_url = os.environ["COROBOT_URL"].rstrip("/")
arm = os.environ["ARM"]
camera_frame = os.environ["CAMERA_FRAME"]
task_config_path = os.environ["TASK_CONFIG_PATH"]
move_duration_s = float(os.environ["MOVE_DURATION_S"])
gripper_duration_s = float(os.environ["GRIPPER_DURATION_S"])
approach_distance_m = float(os.environ["APPROACH_DISTANCE_M"])
lift_dz_base_m = float(os.environ["LIFT_DZ_BASE_M"])
place_hover_dz_base_m = float(os.environ["PLACE_HOVER_DZ_BASE_M"])
source_tag_id = int(os.environ["SOURCE_TAG_ID"])
dest_tag_id = int(os.environ["DEST_TAG_ID"])
execute = os.environ["EXECUTE_PICK_PLACE"] == "1"


def post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{corobot_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30.0) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        raise RuntimeError(f"HTTP {exc.code} from {path}: {text}") from exc
    data = json.loads(text)
    if not data.get("success", False):
        raise RuntimeError(f"{path} failed: {json.dumps(data, ensure_ascii=False)}")
    return data.get("data", data)


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

source_tag_camera = np.asarray(source_tag["position_camera_m"], dtype=np.float64).reshape(3)
dest_tag_camera = np.asarray(dest_tag["position_camera_m"], dtype=np.float64).reshape(3)
source_tag_base = calibration.camera_to_exec_point(source_tag_camera, camera_frame)
dest_tag_base = calibration.camera_to_exec_point(dest_tag_camera, camera_frame)

grasp_base = source_tag_base + grasp_offset
grasp_camera = calibration.exec_to_camera_point(grasp_base, camera_frame)
approach_camera = grasp_camera + calibration.camera_approach_axis * approach_distance_m
approach_base = calibration.camera_to_exec_point(approach_camera, camera_frame)
lift_base = grasp_base + np.asarray([0.0, 0.0, lift_dz_base_m], dtype=np.float64)
lift_camera = calibration.exec_to_camera_point(lift_base, camera_frame)

place_base = dest_tag_base + place_offset
place_camera = calibration.exec_to_camera_point(place_base, camera_frame)
place_hover_base = place_base + np.asarray([0.0, 0.0, place_hover_dz_base_m], dtype=np.float64)
place_hover_camera = calibration.exec_to_camera_point(place_hover_base, camera_frame)

payloads = [
    (
        "open_gripper",
        "/skill/gripper",
        {"arm": arm, "gripper_value": 0.0, "duration_s": gripper_duration_s},
    ),
    (
        "move_to_grasp_approach",
        "/skill/move_eef",
        {
            "arm": arm,
            "target_position_camera_m": rounded(approach_camera),
            "duration_s": move_duration_s,
        },
    ),
    (
        "move_to_grasp",
        "/skill/move_eef",
        {
            "arm": arm,
            "target_position_camera_m": rounded(grasp_camera),
            "duration_s": move_duration_s,
        },
    ),
    (
        "close_gripper",
        "/skill/gripper",
        {"arm": arm, "gripper_value": 1.0, "duration_s": gripper_duration_s},
    ),
    (
        "lift_after_grasp",
        "/skill/move_eef",
        {
            "arm": arm,
            "target_position_camera_m": rounded(lift_camera),
            "duration_s": move_duration_s,
        },
    ),
    (
        "move_to_place_hover",
        "/skill/move_eef",
        {
            "arm": arm,
            "target_position_camera_m": rounded(place_hover_camera),
            "duration_s": move_duration_s,
        },
    ),
    (
        "move_to_place",
        "/skill/move_eef",
        {
            "arm": arm,
            "target_position_camera_m": rounded(place_camera),
            "duration_s": move_duration_s,
        },
    ),
    (
        "open_gripper_release",
        "/skill/gripper",
        {"arm": arm, "gripper_value": 0.0, "duration_s": gripper_duration_s},
    ),
]

plan = {
    "execute": execute,
    "arm": arm,
    "source_tag_id": source_tag_id,
    "dest_tag_id": dest_tag_id,
    "camera_frame": camera_frame,
    "move_eef_target_semantics": "target_position_camera_m is desired gripper-center TCP; control layer sends wrist/link7 target to A2D",
    "grasp_offset_base_m": rounded(grasp_offset),
    "place_offset_base_m": rounded(place_offset),
    "source_tag_camera_m": rounded(source_tag_camera),
    "source_tag_base_m": rounded(source_tag_base),
    "dest_tag_camera_m": rounded(dest_tag_camera),
    "dest_tag_base_m": rounded(dest_tag_base),
    "grasp_base_m": rounded(grasp_base),
    "grasp_camera_m": rounded(grasp_camera),
    "approach_base_m": rounded(approach_base),
    "approach_camera_m": rounded(approach_camera),
    "lift_base_m": rounded(lift_base),
    "lift_camera_m": rounded(lift_camera),
    "place_hover_base_m": rounded(place_hover_base),
    "place_hover_camera_m": rounded(place_hover_camera),
    "place_base_m": rounded(place_base),
    "place_camera_m": rounded(place_camera),
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
