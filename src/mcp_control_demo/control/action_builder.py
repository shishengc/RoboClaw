from __future__ import annotations

import time
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation as R

from mcp_control_demo.calibration import CalibrationConfig, transform_orientation_xyzw

from .joint_units import normalize_head_joint_states_rad
from .timing import TrajectoryTiming, make_timing


OPEN_GRIPPER = 0.0
CLOSE_GRIPPER = 1.0
GRIPPER_STATE_CLOSE_UNITS = 120.0
GRIPPER_CENTER_OFFSET_LINK7_M = np.asarray([0.0, 0.0, 0.14308], dtype=np.float64)
_FK_SOLVER = None


def build_move_eef_action(
    observation: Any,
    calibration: CalibrationConfig,
    *,
    arm: str,
    target_position_camera_m: list[float],
    camera_frame: str | None = None,
    target_orientation_camera_xyzw: list[float] | None = None,
    duration_s: float | None = 1.0,
    gripper_value: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_pose = _current_eef_pose(observation, arm, calibration.exec_frame)
    current_position = _pose_position(current_pose)
    current_orientation = _pose_orientation(current_pose)
    if current_position is not None and current_orientation is not None:
        current_center = wrist_to_gripper_center_exec(current_position, current_orientation)
    else:
        target_exec = calibration.camera_to_exec_point(target_position_camera_m, camera_frame)
        current_center = target_exec.copy()
    current_camera = calibration.exec_to_camera_point(current_center, camera_frame)
    return build_move_eef_between_camera_points(
        observation,
        calibration,
        arm=arm,
        start_position_camera_m=current_camera.tolist(),
        target_position_camera_m=target_position_camera_m,
        camera_frame=camera_frame,
        target_orientation_camera_xyzw=target_orientation_camera_xyzw,
        duration_s=duration_s,
        gripper_value=gripper_value,
    )


def build_move_eef_between_camera_points(
    observation: Any,
    calibration: CalibrationConfig,
    *,
    arm: str,
    start_position_camera_m: list[float],
    target_position_camera_m: list[float],
    camera_frame: str | None = None,
    target_orientation_camera_xyzw: list[float] | None = None,
    duration_s: float | None = 1.0,
    gripper_value: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    arm = _validate_arm(arm)
    timing = make_timing(duration_s)
    start_exec = calibration.camera_to_exec_point(start_position_camera_m, camera_frame)
    target_exec = calibration.camera_to_exec_point(target_position_camera_m, camera_frame)
    orientation_exec_xyzw = _target_orientation_exec_xyzw(
        observation,
        calibration,
        arm,
        camera_frame,
        target_orientation_camera_xyzw,
    )
    orientation_exec_rpy = _quat_xyzw_to_rpy(orientation_exec_xyzw)
    start_wrist_exec = gripper_center_to_wrist_exec(start_exec, orientation_exec_xyzw)
    target_wrist_exec = gripper_center_to_wrist_exec(target_exec, orientation_exec_xyzw)
    rows = _interpolate_pose_rows(start_wrist_exec, target_wrist_exec, orientation_exec_rpy, timing.num_steps)
    action = _eef_action(calibration.exec_frame, arm, rows, timing)
    if gripper_value is not None:
        _attach_gripper_rows(action, observation, arm, float(gripper_value), timing.num_steps)
    return action, _timing_meta(timing) | {
        "arm": arm,
        "camera_frame": camera_frame or calibration.camera_frame,
        "exec_frame": calibration.exec_frame,
        "start_position_camera_m": _round_list(start_position_camera_m),
        "target_position_camera_m": _round_list(target_position_camera_m),
        "target_position_exec_m": _round_list(target_exec),
        "start_wrist_position_exec_m": _round_list(start_wrist_exec),
        "target_wrist_position_exec_m": _round_list(target_wrist_exec),
        "gripper_center_offset_link7_m": _round_list(GRIPPER_CENTER_OFFSET_LINK7_M),
        "a2d_target_frame": f"arm_{arm}_link7",
    }


def build_gripper_action(
    observation: Any,
    *,
    arm: str,
    gripper_value: float,
    duration_s: float | None = 0.5,
) -> tuple[dict[str, Any], dict[str, Any]]:
    arm = _validate_arm(arm)
    value = float(np.clip(float(gripper_value), OPEN_GRIPPER, CLOSE_GRIPPER))
    timing = make_timing(duration_s)
    left_start = _current_gripper_command(observation, 0)
    right_start = _current_gripper_command(observation, 1)
    left_target = value if arm == "left" else left_start
    right_target = value if arm == "right" else right_start
    left_rows = _interpolate_scalar_rows(left_start, left_target, timing.num_steps)
    right_rows = _interpolate_scalar_rows(right_start, right_target, timing.num_steps)
    action = {
        "timestamps": int(time.time() * 1e9),
        "trajectory_reference_time": timing.actual_duration_s,
        "base_link": "base_link",
        "left_effector": left_rows,
        "right_effector": right_rows,
    }
    return action, _timing_meta(timing) | {
        "arm": arm,
        "gripper_value": value,
        "left_target": left_target,
        "right_target": right_target,
    }


def _eef_action(exec_frame: str, arm: str, rows: list[list[float]], timing: TrajectoryTiming) -> dict[str, Any]:
    field = f"{arm}_arm"
    return {
        "timestamps": int(time.time() * 1e9),
        "trajectory_reference_time": timing.actual_duration_s,
        "base_link": exec_frame,
        field: {"kind": "EEF_ABS", "values": rows},
    }


def _attach_gripper_rows(action: dict[str, Any], observation: Any, arm: str, value: float, steps: int) -> None:
    left_value = _current_gripper_command(observation, 0)
    right_value = _current_gripper_command(observation, 1)
    if arm == "left":
        left_value = float(np.clip(value, 0.0, 1.0))
    else:
        right_value = float(np.clip(value, 0.0, 1.0))
    action["left_effector"] = [[left_value] for _ in range(steps)]
    action["right_effector"] = [[right_value] for _ in range(steps)]


def _interpolate_pose_rows(
    start_position: np.ndarray,
    target_position: np.ndarray,
    orientation_rpy: list[float],
    steps: int,
) -> list[list[float]]:
    rows: list[list[float]] = []
    for index in range(1, steps + 1):
        alpha = float(index) / float(steps)
        pos = start_position + (target_position - start_position) * alpha
        rows.append([*map(float, pos.tolist()), *map(float, orientation_rpy)])
    return rows


def _interpolate_scalar_rows(start: float, target: float, steps: int) -> list[list[float]]:
    return [[float(start + (target - start) * (index / steps))] for index in range(1, steps + 1)]


def _target_orientation_exec_xyzw(
    observation: Any,
    calibration: CalibrationConfig,
    arm: str,
    camera_frame: str | None,
    target_orientation_camera_xyzw: list[float] | None,
) -> list[float]:
    if target_orientation_camera_xyzw is not None:
        calibration.require_camera_frame(camera_frame)
        return transform_orientation_xyzw(calibration.require_transform(), target_orientation_camera_xyzw)
    current = _current_eef_orientation(observation, arm, calibration.exec_frame)
    return current if current is not None else [0.0, 0.0, 0.0, 1.0]


def wrist_to_gripper_center_exec(
    wrist_position_exec_m: list[float] | np.ndarray,
    wrist_orientation_exec_xyzw: list[float] | np.ndarray,
) -> np.ndarray:
    return np.asarray(wrist_position_exec_m, dtype=np.float64).reshape(3) + _tcp_offset_exec(
        wrist_orientation_exec_xyzw
    )


def gripper_center_to_wrist_exec(
    gripper_center_exec_m: list[float] | np.ndarray,
    wrist_orientation_exec_xyzw: list[float] | np.ndarray,
) -> np.ndarray:
    return np.asarray(gripper_center_exec_m, dtype=np.float64).reshape(3) - _tcp_offset_exec(
        wrist_orientation_exec_xyzw
    )


def _tcp_offset_exec(wrist_orientation_exec_xyzw: list[float] | np.ndarray) -> np.ndarray:
    return R.from_quat(np.asarray(wrist_orientation_exec_xyzw, dtype=np.float64).reshape(4)).apply(
        GRIPPER_CENTER_OFFSET_LINK7_M
    )


def _current_eef_position(observation: Any, arm: str, frame: str) -> np.ndarray | None:
    pose = _current_eef_pose(observation, arm, frame)
    return _pose_position(pose)


def _pose_position(pose: Any) -> np.ndarray | None:
    if pose is None:
        return None
    position = _get(pose, "position")
    if position is None:
        return None
    return np.asarray(position, dtype=np.float64).reshape(3)


def _current_eef_orientation(observation: Any, arm: str, frame: str) -> list[float] | None:
    pose = _current_eef_pose(observation, arm, frame)
    return _pose_orientation(pose)


def _pose_orientation(pose: Any) -> list[float] | None:
    if pose is None:
        return None
    orientation = _get(pose, "orientation")
    if orientation is None:
        return None
    return [float(v) for v in orientation]


def _quat_xyzw_to_rpy(quat_xyzw: list[float]) -> list[float]:
    return [float(value) for value in R.from_quat(quat_xyzw).as_euler("xyz", degrees=False)]


def _current_eef_pose(observation: Any, arm: str, frame: str) -> Any | None:
    obs = _get(observation, "observation") or observation
    states = _get(obs, "states")
    end_pose = _get(states, "end_pose")
    frame_pose = _get(end_pose, frame)
    pose = _get(frame_pose, f"{_validate_arm(arm)}_arm")
    if pose is not None:
        return pose
    return _fk_eef_pose_from_joint_states(states, arm, frame)


def _fk_eef_pose_from_joint_states(states: Any, arm: str, frame: str) -> Any | None:
    arm_joints = _get(states, "arm_joint_states") or []
    waist = _get(states, "waist_joint_states") or []
    head = _get(states, "head_joint_states") or [0.0, 0.0]
    if len(arm_joints) < 14 or len(waist) < 2:
        return None
    if len(head) < 2:
        head = [0.0, 0.0]
    head = normalize_head_joint_states_rad(head[:2])
    poses = _fk_solver().get_eef_pos(
        [float(value) for value in waist[:2]],
        head,
        [float(value) for value in arm_joints[:7]],
        [float(value) for value in arm_joints[7:14]],
        base_link=frame,
    )
    return _get(poses, f"{_validate_arm(arm)}_arm")


def _fk_solver():
    global _FK_SOLVER
    if _FK_SOLVER is None:
        from corobot.utils.fk_solver import FKTransform

        _FK_SOLVER = FKTransform()
    return _FK_SOLVER


def _current_gripper_command(observation: Any, index: int) -> float:
    obs = _get(observation, "observation") or observation
    states = _get(obs, "states")
    values = _get(states, "gripper_states") or []
    if len(values) <= index:
        return OPEN_GRIPPER
    raw = float(values[index])
    if abs(raw) > 1.0:
        raw = raw / GRIPPER_STATE_CLOSE_UNITS
    return float(np.clip(raw, OPEN_GRIPPER, CLOSE_GRIPPER))


def _validate_arm(arm: str) -> str:
    if arm not in ("left", "right"):
        raise ValueError("arm must be 'left' or 'right'")
    return arm


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _timing_meta(timing: TrajectoryTiming) -> dict[str, Any]:
    return {
        "control_hz": timing.control_hz,
        "num_steps": timing.num_steps,
        "requested_duration_s": timing.requested_duration_s,
        "actual_duration_s": timing.actual_duration_s,
    }


def _round_list(values: Any) -> list[float]:
    return [round(float(v), 6) for v in np.asarray(values, dtype=np.float64).reshape(-1)]
