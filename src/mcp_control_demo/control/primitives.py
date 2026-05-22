from __future__ import annotations

from typing import Any

import numpy as np

from mcp_control_demo.calibration import CalibrationConfig

from .action_builder import (
    CLOSE_GRIPPER,
    OPEN_GRIPPER,
    build_gripper_action,
    build_move_eef_action,
    build_move_eef_between_camera_points,
)


def build_grasp_by_tag_sequence(
    observation: Any,
    calibration: CalibrationConfig,
    *,
    arm: str,
    tag_id: int,
    tag_pose: dict[str, Any],
    camera_frame: str | None = None,
    approach_distance_m: float = 0.06,
    lift_height_m: float = 0.10,
    move_duration_s: float = 1.0,
    gripper_duration_s: float = 0.5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grasp_point = calibration.grasp_point_from_tag(tag_id, tag_pose)
    approach_point = grasp_point + calibration.camera_approach_axis * float(approach_distance_m)
    lift_point = grasp_point + calibration.camera_lift_axis * float(lift_height_m)

    actions: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []

    for action, meta in (
        build_gripper_action(observation, arm=arm, gripper_value=OPEN_GRIPPER, duration_s=gripper_duration_s),
        build_move_eef_action(
            observation,
            calibration,
            arm=arm,
            target_position_camera_m=approach_point.tolist(),
            camera_frame=camera_frame,
            duration_s=move_duration_s,
        ),
        build_move_eef_between_camera_points(
            observation,
            calibration,
            arm=arm,
            start_position_camera_m=approach_point.tolist(),
            target_position_camera_m=grasp_point.tolist(),
            camera_frame=camera_frame,
            duration_s=move_duration_s,
        ),
        build_gripper_action(observation, arm=arm, gripper_value=CLOSE_GRIPPER, duration_s=gripper_duration_s),
        build_move_eef_between_camera_points(
            observation,
            calibration,
            arm=arm,
            start_position_camera_m=grasp_point.tolist(),
            target_position_camera_m=lift_point.tolist(),
            camera_frame=camera_frame,
            duration_s=move_duration_s,
        ),
    ):
        actions.append(action)
        segments.append(meta)

    return actions, {
        "arm": arm,
        "tag_id": int(tag_id),
        "camera_frame": camera_frame or calibration.camera_frame,
        "grasp_point_camera_m": _round_list(grasp_point),
        "approach_point_camera_m": _round_list(approach_point),
        "lift_point_camera_m": _round_list(lift_point),
        "segments": segments,
    }


def _round_list(values: Any) -> list[float]:
    return [round(float(v), 6) for v in np.asarray(values, dtype=np.float64).reshape(-1)]
