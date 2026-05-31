"""Small helpers for deterministic mcp_control_demo tag target math."""

from __future__ import annotations

import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any


ROBOCLAW_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_TASK_CONFIG_PATH = ROBOCLAW_ROOT / ".a2d_pkg/corobot/config/rule_control_task_config.yml"


def build_tag_grasp_targets(
    *,
    calibration_path: str,
    tag: dict[str, Any],
    base_offset_m: list[float],
    approach_distance_m: float,
    lift_height_m: float,
) -> dict[str, list[float]]:
    t_exec_camera, approach_axis = load_calibration(calibration_path)
    tag_camera = vector3(tag.get("position_camera_m") or tag.get("translation_m"))
    tag_base = transform_point(t_exec_camera, tag_camera)
    grasp_base = add3(tag_base, base_offset_m)
    grasp_camera = inverse_rigid_transform_point(t_exec_camera, grasp_base)
    approach_camera = add3(grasp_camera, scale3(approach_axis, approach_distance_m))
    lift_base = add3(grasp_base, [0.0, 0.0, lift_height_m])
    lift_camera = inverse_rigid_transform_point(t_exec_camera, lift_base)
    return {
        "tag_camera_m": rounded(tag_camera),
        "tag_base_m": rounded(tag_base),
        "grasp_camera_m": rounded(grasp_camera),
        "grasp_base_m": rounded(grasp_base),
        "approach_camera_m": rounded(approach_camera),
        "lift_camera_m": rounded(lift_camera),
    }


def build_tag_place_targets(
    *,
    calibration_path: str,
    tag: dict[str, Any],
    base_offset_m: list[float],
    hover_height_m: float,
) -> dict[str, list[float]]:
    t_exec_camera, _approach_axis = load_calibration(calibration_path)
    tag_camera = vector3(tag.get("position_camera_m") or tag.get("translation_m"))
    tag_base = transform_point(t_exec_camera, tag_camera)
    place_base = add3(tag_base, base_offset_m)
    place_camera = inverse_rigid_transform_point(t_exec_camera, place_base)
    place_hover_base = add3(place_base, [0.0, 0.0, hover_height_m])
    place_hover_camera = inverse_rigid_transform_point(t_exec_camera, place_hover_base)
    return {
        "tag_camera_m": rounded(tag_camera),
        "tag_base_m": rounded(tag_base),
        "place_camera_m": rounded(place_camera),
        "place_base_m": rounded(place_base),
        "place_hover_camera_m": rounded(place_hover_camera),
        "place_hover_base_m": rounded(place_hover_base),
    }


def load_calibration(path: str) -> tuple[list[list[float]], list[float]]:
    return load_task_config_transform(path)


def load_task_config_transform(path: str | Path) -> tuple[list[list[float]], list[float]]:
    _ensure_roboclaw_runtime_paths()

    import numpy as np
    import yaml
    from corobot.utils.fk_solver import _find_urdf_solver_dir
    from corobot.utils.kinematics import Kinematics
    from mcp_control_demo.calibration import load_calibration_config
    from mcp_control_demo.control.joint_units import normalize_head_joint_states_rad
    from scipy.spatial.transform import Rotation as R

    config_path = Path(path).expanduser()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"task config must contain a mapping: {config_path}")

    calibration = load_calibration_config(config_path)
    if calibration.t_head_pitch_camera is None:
        raise ValueError("task config must contain mcp_control.extrinsics.T_head_pitch_camera")

    reset_pose = config.get("reset_pose") or {}
    head = reset_pose.get("target_head_positions")
    waist = reset_pose.get("target_waist_positions")
    if head is None or waist is None:
        raise ValueError("task config must contain reset_pose target_head_positions and target_waist_positions")

    head_rad = normalize_head_joint_states_rad([float(value) for value in head[:2]])
    waist_values = [float(value) for value in waist[:2]]
    if len(head_rad) != 2 or len(waist_values) != 2:
        raise ValueError("reset_pose head/waist positions must each contain 2 values")

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
    t_exec_camera = t_base_head @ calibration.t_head_pitch_camera
    approach_axis = calibration.camera_approach_axis.tolist()
    return matrix4_to_lists(t_exec_camera), normalize3(approach_axis)


def _ensure_roboclaw_runtime_paths() -> None:
    a2d_pkg = ROBOCLAW_ROOT / ".a2d_pkg"
    candidate_paths = [
        ROBOCLAW_ROOT / "src",
        a2d_pkg,
        a2d_pkg / "site-packages",
    ]
    cmeel_lib = a2d_pkg / "site-packages/cmeel.prefix/lib"
    candidate_paths.extend(sorted(cmeel_lib.glob("python*/site-packages")))

    for path in reversed(candidate_paths):
        path_text = str(path)
        if path.exists() and path_text not in sys.path:
            sys.path.insert(0, path_text)


def vector3(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("expected 3 values")
    return [float(v) for v in value]


def add3(a: list[float], b: list[float]) -> list[float]:
    return [a[i] + b[i] for i in range(3)]


def scale3(v: list[float], scale: float) -> list[float]:
    return [x * scale for x in v]


def normalize3(v: list[float]) -> list[float]:
    norm = sum(x * x for x in v) ** 0.5
    if norm <= 1e-12:
        raise ValueError("zero vector")
    return [x / norm for x in v]


def transform_point(matrix: list[list[float]], point: list[float]) -> list[float]:
    return [sum(matrix[row][col] * point[col] for col in range(3)) + matrix[row][3] for row in range(3)]


def inverse_rigid_transform_point(matrix: list[list[float]], point: list[float]) -> list[float]:
    shifted = [point[i] - matrix[i][3] for i in range(3)]
    return [sum(matrix[row][col] * shifted[row] for row in range(3)) for col in range(3)]


def rounded(v: list[float]) -> list[float]:
    return [round(float(x), 6) for x in v]


def matrix4_to_lists(matrix: Any) -> list[list[float]]:
    rows = matrix.tolist() if hasattr(matrix, "tolist") else matrix
    if not isinstance(rows, list) or len(rows) != 4:
        raise ValueError("expected 4x4 matrix")
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError("expected 4x4 matrix")
        result.append([float(value) for value in row])
    return result
