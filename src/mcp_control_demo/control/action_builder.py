from __future__ import annotations

import time
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation as R

from mcp_control_demo.calibration import CalibrationConfig, transform_orientation_xyzw

from .timing import TrajectoryTiming, make_timing


OPEN_GRIPPER = 0.0
CLOSE_GRIPPER = 1.0
GRIPPER_STATE_CLOSE_UNITS = 120.0
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
    current_position = _current_eef_position(observation, arm, calibration.exec_frame)
    if current_position is None:
        target_exec = calibration.camera_to_exec_point(target_position_camera_m, camera_frame)
        current_position = target_exec.copy()
    current_camera = calibration.exec_to_camera_point(current_position, camera_frame)
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
    orientation_exec_rpy = _target_orientation_exec_rpy(
        observation,
        calibration,
        arm,
        camera_frame,
        target_orientation_camera_xyzw,
    )
    rows = _interpolate_pose_rows(start_exec, target_exec, orientation_exec_rpy, timing.num_steps)
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
    }


def build_lift_eef_action(
    observation: Any,
    calibration: CalibrationConfig,
    *,
    arm: str,
    distance_m: float,
    camera_frame: str | None = None,
    duration_s: float | None = 1.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _build_offset_eef_action(
        observation,
        calibration,
        arm=arm,
        axis_camera=calibration.camera_lift_axis,
        distance_m=distance_m,
        camera_frame=camera_frame,
        duration_s=duration_s,
        label="lift_eef",
    )


def build_place_down_sequence(
    observation: Any,
    calibration: CalibrationConfig,
    *,
    arm: str,
    down_distance_m: float,
    camera_frame: str | None = None,
    duration_s: float | None = 1.0,
    open_after_down: bool = True,
    gripper_duration_s: float | None = 0.5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    down_action, down_meta = _build_offset_eef_action(
        observation,
        calibration,
        arm=arm,
        axis_camera=calibration.camera_place_down_axis,
        distance_m=down_distance_m,
        camera_frame=camera_frame,
        duration_s=duration_s,
        label="place_down",
    )
    actions = [down_action]
    segments = [down_meta]
    if open_after_down:
        open_action, open_meta = build_gripper_action(
            observation,
            arm=arm,
            gripper_value=OPEN_GRIPPER,
            duration_s=gripper_duration_s,
        )
        actions.append(open_action)
        segments.append(open_meta)
    return actions, {
        "arm": _validate_arm(arm),
        "camera_frame": camera_frame or calibration.camera_frame,
        "segments": segments,
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


def _build_offset_eef_action(
    observation: Any,
    calibration: CalibrationConfig,
    *,
    arm: str,
    axis_camera: np.ndarray,
    distance_m: float,
    camera_frame: str | None,
    duration_s: float | None,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_exec = _current_eef_position(observation, arm, calibration.exec_frame)
    if current_exec is None:
        raise ValueError(f"current {arm} EEF pose is unavailable in {calibration.exec_frame}")
    start_camera = calibration.exec_to_camera_point(current_exec, camera_frame)
    target_camera = start_camera + axis_camera * float(distance_m)
    action, meta = build_move_eef_between_camera_points(
        observation,
        calibration,
        arm=arm,
        start_position_camera_m=start_camera.tolist(),
        target_position_camera_m=target_camera.tolist(),
        camera_frame=camera_frame,
        duration_s=duration_s,
    )
    return action, meta | {
        "primitive": label,
        "axis_camera": _round_list(axis_camera),
        "distance_m": float(distance_m),
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


def _target_orientation_exec_rpy(
    observation: Any,
    calibration: CalibrationConfig,
    arm: str,
    camera_frame: str | None,
    target_orientation_camera_xyzw: list[float] | None,
) -> list[float]:
    if target_orientation_camera_xyzw is not None:
        calibration.require_camera_frame(camera_frame)
        quat_exec = transform_orientation_xyzw(calibration.require_transform(), target_orientation_camera_xyzw)
        return _quat_xyzw_to_rpy(quat_exec)
    current = _current_eef_orientation(observation, arm, calibration.exec_frame)
    return _quat_xyzw_to_rpy(current) if current is not None else [0.0, 0.0, 0.0]


def _current_eef_position(observation: Any, arm: str, frame: str) -> np.ndarray | None:
    pose = _current_eef_pose(observation, arm, frame)
    if pose is None:
        return None
    position = _get(pose, "position")
    if position is None:
        return None
    return np.asarray(position, dtype=np.float64).reshape(3)


def _current_eef_orientation(observation: Any, arm: str, frame: str) -> list[float] | None:
    pose = _current_eef_pose(observation, arm, frame)
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
    poses = _fk_solver().get_eef_pos(
        [float(value) for value in waist[:2]],
        [float(value) for value in head[:2]],
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
