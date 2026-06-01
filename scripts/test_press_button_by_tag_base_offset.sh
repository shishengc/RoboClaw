#!/usr/bin/env bash
set -euo pipefail

COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
ARM="${ARM:-right}"
CAMERA_FRAME="${CAMERA_FRAME:-head_camera_optical}"
TASK_CONFIG_PATH="${TASK_CONFIG_PATH:-/home/ck/RoboClaw/.a2d_pkg/corobot/config/rule_control_task_config.yml}"
PYTHON_BIN="${PYTHON_BIN:-/home/ck/miniconda3/envs/robot/bin/python}"
MOVE_DURATION_S="${MOVE_DURATION_S:-2.0}"
GRIPPER_DURATION_S="${GRIPPER_DURATION_S:-0.5}"
BUTTON_TAG_ID="${BUTTON_TAG_ID:-20}"
LIFT_DZ_BASE_M="${LIFT_DZ_BASE_M:-0.10}"
PRESS_HOLD_S="${PRESS_HOLD_S:-0.5}"
PRESS_INTERVAL_S="${PRESS_INTERVAL_S:-1.0}"
CLOSE_GRIPPER_VALUE="${CLOSE_GRIPPER_VALUE:-1.0}"
EXECUTE_BUTTON_PRESS="${EXECUTE_BUTTON_PRESS:-0}"

usage() {
  cat >&2 <<'EOF'
Usage:
  ARM=right bash scripts/test_press_button_by_tag_base_offset.sh

  ARM=right bash scripts/test_press_button_by_tag_base_offset.sh <tag_id>

  ARM=right bash scripts/test_press_button_by_tag_base_offset.sh \
    <dx_base_m> <dy_base_m> <dz_base_m>

  ARM=right bash scripts/test_press_button_by_tag_base_offset.sh \
    <tag_id> <dx_base_m> <dy_base_m> <dz_base_m>

Example dry-run (plans two presses with 1s between them):
  ARM=right bash scripts/test_press_button_by_tag_base_offset.sh
  ARM=right bash scripts/test_press_button_by_tag_base_offset.sh 20 0.00 0.00 0.00

Execute on robot (move above, press down, lift, wait 1s, press down again, lift):
  ARM=right PRESS_INTERVAL_S=1.0 EXECUTE_BUTTON_PRESS=1 bash scripts/test_press_button_by_tag_base_offset.sh \
    21 0.00 0.00 0.01

Behavior:
  1. Detect AprilTags and read the requested button tag, default tag 20.
  2. Close the selected gripper.
  3. Move the configured gripper-center TCP to the point above the button.
  4. Move down to the button press point, hold for PRESS_HOLD_S, then lift back above.
  5. Wait PRESS_INTERVAL_S, then press down and lift once more.

Environment:
  COROBOT_URL             default http://localhost:8765
  ARM                     default right
  BUTTON_TAG_ID           default 20, overridden by CLI tag_id
  CAMERA_FRAME            default head_camera_optical
  TASK_CONFIG_PATH        default .a2d_pkg/corobot/config/rule_control_task_config.yml
  MOVE_DURATION_S         default 2.0
  GRIPPER_DURATION_S      default 0.5
  LIFT_DZ_BASE_M          default 0.10, button-above height and base_link +Z after pressing
  PRESS_HOLD_S            default 0.5, seconds to wait between press and lift
  PRESS_INTERVAL_S        default 1.0, seconds to wait between the two presses
  CLOSE_GRIPPER_VALUE     default 1.0
  EXECUTE_BUTTON_PRESS    default 0; set 1 to execute

Notes:
  /skill/move_eef treats target_position_camera_m as the configured
  gripper-center TCP target. If the physical button contact point is not exactly
  at the tag center, tune the base-link offset arguments.
  Base-link offsets are converted by composing
  T_base_head_pitch(reset_pose) * T_head_pitch_camera from the task config.
  Keep head/waist at reset_pose for this planning helper, or prefer direct
  camera-frame targets.
EOF
}

case "$#" in
  0)
    set -- 0.0 0.0 0.0
    ;;
  1)
    BUTTON_TAG_ID="$1"
    set -- 0.0 0.0 0.0
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

export PYTHONPATH="/home/ck/RoboClaw/src:/home/ck/RoboClaw/.a2d_pkg:${PYTHONPATH:-}"
export COROBOT_URL ARM CAMERA_FRAME TASK_CONFIG_PATH
export MOVE_DURATION_S GRIPPER_DURATION_S BUTTON_TAG_ID
export LIFT_DZ_BASE_M PRESS_HOLD_S PRESS_INTERVAL_S CLOSE_GRIPPER_VALUE EXECUTE_BUTTON_PRESS

"${PYTHON_BIN}" - "$1" "$2" "$3" <<'PY'
from __future__ import annotations

import json
import os
import sys
import time
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
from mcp_control_demo.control.joint_units import normalize_head_joint_states_rad


base_offset = np.asarray([float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])], dtype=np.float64)

corobot_url = os.environ["COROBOT_URL"].rstrip("/")
arm = os.environ["ARM"]
camera_frame = os.environ["CAMERA_FRAME"]
task_config_path = os.environ["TASK_CONFIG_PATH"]
move_duration_s = float(os.environ["MOVE_DURATION_S"])
gripper_duration_s = float(os.environ["GRIPPER_DURATION_S"])
button_tag_id = int(os.environ["BUTTON_TAG_ID"])
lift_dz_base_m = float(os.environ["LIFT_DZ_BASE_M"])
press_hold_s = float(os.environ["PRESS_HOLD_S"])
press_interval_s = float(os.environ["PRESS_INTERVAL_S"])
close_gripper_value = float(os.environ["CLOSE_GRIPPER_VALUE"])
execute = os.environ["EXECUTE_BUTTON_PRESS"] == "1"


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

    head_rad = normalize_head_joint_states_rad([float(value) for value in head[:2]])
    waist_values = [float(value) for value in waist[:2]]
    if len(head_rad) != 2 or len(waist_values) != 2:
        raise RuntimeError("reset_pose head/waist positions must each contain 2 values")

    with redirect_stdout(StringIO()):
        kinematics = Kinematics(str(_find_urdf_solver_dir() / "A2D_viz.urdf"))
    xyzquat = kinematics.compute_head_fk(
        float(head_rad[0]),
        float(head_rad[1]),
        float(waist_values[0]),
        float(waist_values[1]),
    )
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
button_tag = get_tag(button_tag_id)

tag_camera = np.asarray(button_tag["position_camera_m"], dtype=np.float64).reshape(3)
tag_base = calibration.camera_to_exec_point(tag_camera, camera_frame)

button_base = tag_base + base_offset
button_above_base = button_base + np.asarray([0.0, 0.0, lift_dz_base_m], dtype=np.float64)

button_camera = calibration.exec_to_camera_point(button_base, camera_frame)
button_above_camera = calibration.exec_to_camera_point(button_above_base, camera_frame)

payloads = [
    (
        "close_gripper",
        "/skill/gripper",
        {"arm": arm, "gripper_value": close_gripper_value, "duration_s": gripper_duration_s},
    ),
    (
        "move_to_button_above_1",
        "/skill/move_eef",
        {
            "arm": arm,
            "target_position_camera_m": rounded(button_above_camera),
            "duration_s": move_duration_s,
        },
    ),
    (
        "move_down_to_button_press_1",
        "/skill/move_eef",
        {
            "arm": arm,
            "target_position_camera_m": rounded(button_camera),
            "duration_s": move_duration_s,
        },
    ),
    (
        "lift_after_press_1",
        "/skill/move_eef",
        {
            "arm": arm,
            "target_position_camera_m": rounded(button_above_camera),
            "duration_s": move_duration_s,
        },
    ),
    (
        "move_down_to_button_press_2",
        "/skill/move_eef",
        {
            "arm": arm,
            "target_position_camera_m": rounded(button_camera),
            "duration_s": move_duration_s,
        },
    ),
    (
        "lift_after_press_2",
        "/skill/move_eef",
        {
            "arm": arm,
            "target_position_camera_m": rounded(button_above_camera),
            "duration_s": move_duration_s,
        },
    ),
]

plan = {
    "execute": execute,
    "arm": arm,
    "button_tag_id": button_tag_id,
    "camera_frame": camera_frame,
    "move_eef_target_semantics": "target_position_camera_m is desired gripper-center TCP; control layer sends wrist/link7 target to A2D",
    "base_offset_m": rounded(base_offset),
    "lift_dz_base_m": lift_dz_base_m,
    "press_hold_s": press_hold_s,
    "press_interval_s": press_interval_s,
    "tag_position_camera_m": rounded(tag_camera),
    "tag_position_base_m": rounded(tag_base),
    "button_contact_base_m": rounded(button_base),
    "button_contact_camera_m": rounded(button_camera),
    "button_above_base_m": rounded(button_above_base),
    "button_above_camera_m": rounded(button_above_camera),
    "lift_position_base_m": rounded(button_above_base),
    "lift_position_camera_m": rounded(button_above_camera),
    "payloads": [{"name": name, "path": path, "payload": payload} for name, path, payload in payloads],
    "sequence": [
        "close_gripper",
        "move_to_button_above_1",
        "move_down_to_button_press_1",
        f"hold {press_hold_s:.3f}s",
        "lift_after_press_1",
        f"wait {press_interval_s:.3f}s",
        "move_down_to_button_press_2",
        f"hold {press_hold_s:.3f}s",
        "lift_after_press_2",
    ],
}
print(json.dumps(plan, indent=2, ensure_ascii=False))

if not execute:
    print("\nDry-run only. Set EXECUTE_BUTTON_PRESS=1 to execute this sequence.", file=sys.stderr)
    sys.exit(0)

for name, path, payload in payloads:
    print(f"\n>>> {name}: {path}", file=sys.stderr)
    result = post(path, payload)
    print(json.dumps({"name": name, "result": result}, indent=2, ensure_ascii=False))
    if name.startswith("move_down_to_button_press_") and press_hold_s > 0:
        print(f"\n>>> hold_after_press: {press_hold_s:.3f}s", file=sys.stderr)
        time.sleep(press_hold_s)
    if name == "lift_after_press_1" and press_interval_s > 0:
        print(f"\n>>> wait_between_presses: {press_interval_s:.3f}s", file=sys.stderr)
        time.sleep(press_interval_s)
PY
