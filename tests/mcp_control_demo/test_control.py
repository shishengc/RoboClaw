from __future__ import annotations

import math

import numpy as np
import pytest

from corobot.protocol.protocol_schemas import Action
from mcp_control_demo.calibration import CalibrationConfig
from mcp_control_demo.control import (
    CONTROL_HZ,
    GRIPPER_CENTER_OFFSET_LINK7_M,
    build_gripper_action,
    build_move_eef_action,
)
from mcp_control_demo.control.joint_units import normalize_head_joint_states_rad
from mcp_control_demo.control.timing import make_timing, validate_no_control_hz


def _obs():
    return {
        "states": {
            "end_pose": {
                "base_link": {
                    "left_arm": {"position": [0.1, 0.2, 0.3], "orientation": [0.0, 0.0, 0.0, 1.0]},
                    "right_arm": {"position": [0.4, 0.5, 0.6], "orientation": [0.0, 0.0, 0.0, 1.0]},
                }
            },
            "gripper_states": [0.0, 0.0],
        }
    }


def _assert_action_schema(action: dict):
    if hasattr(Action, "model_validate"):
        Action.model_validate(action)
    else:
        Action(**action)


def test_duration_is_quantized_to_30hz():
    timing = make_timing(0.034)
    assert timing.control_hz == CONTROL_HZ
    assert timing.num_steps == 2
    assert math.isclose(timing.actual_duration_s, 2.0 / 30.0)


def test_move_eef_right_arm_action_is_30hz_and_schema_valid():
    action, meta = build_move_eef_action(
        _obs(),
        CalibrationConfig.identity_for_tests(),
        arm="right",
        target_position_camera_m=[0.7, 0.8, 0.9],
        duration_s=0.05,
    )
    _assert_action_schema(action)
    rows = action["right_arm"]["values"]
    assert "left_arm" not in action
    assert len(rows) == 2
    assert len(rows[-1]) == 6
    assert math.isclose(action["trajectory_reference_time"], len(rows) / 30.0)
    assert rows[-1][:3] == pytest.approx([0.7, 0.8, 0.9 - GRIPPER_CENTER_OFFSET_LINK7_M[2]])
    assert meta["target_position_exec_m"] == pytest.approx([0.7, 0.8, 0.9])
    assert meta["target_wrist_position_exec_m"] == pytest.approx(
        [0.7, 0.8, 0.9 - GRIPPER_CENTER_OFFSET_LINK7_M[2]]
    )
    assert meta["control_hz"] == 30.0
    assert meta["num_steps"] == 2


def test_camera_target_is_transformed_to_exec_frame():
    calibration = CalibrationConfig(
        t_exec_camera=np.asarray(
            [
                [1.0, 0.0, 0.0, 1.0],
                [0.0, 1.0, 0.0, 2.0],
                [0.0, 0.0, 1.0, 3.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        ),
        t_head_pitch_camera=np.eye(4, dtype=np.float64),
    )
    action, _ = build_move_eef_action(
        _obs(),
        calibration,
        arm="left",
        target_position_camera_m=[0.1, 0.2, 0.3],
        duration_s=1.0 / 30.0,
    )
    assert action["left_arm"]["values"][-1][:3] == pytest.approx(
        [1.1, 2.2, 3.3 - GRIPPER_CENTER_OFFSET_LINK7_M[2]]
    )


def test_dynamic_calibration_holds_intrinsics_without_runtime_transform():
    calibration = CalibrationConfig.from_dict(
        {
            "mcp_control": {
                "transform_mode": "dynamic_fk",
                "extrinsics": {"T_head_pitch_camera": np.eye(4).tolist()},
                "intrinsics": {
                    "camera_matrix": {
                        "data": [
                            [641.6347338250084, 0.0, 652.2551428790697],
                            [0.0, 638.6652075681625, 362.8615784851012],
                            [0.0, 0.0, 1.0],
                        ]
                    }
                }
            },
        }
    )
    assert calibration.intrinsics is not None
    assert calibration.t_exec_camera is None
    with pytest.raises(ValueError, match="runtime T_exec_camera"):
        calibration.require_transform()


def test_calibration_reads_merged_dynamic_fk_config():
    calibration = CalibrationConfig.from_dict(
        {
            "mcp_control": {
                "transform_mode": "dynamic_fk",
                "camera_frame": "head_camera_optical",
                "exec_frame": "base_link",
                "extrinsics": {
                    "T_head_pitch_camera": np.eye(4).tolist(),
                },
            }
        }
    )

    assert calibration.has_dynamic_fk is True
    assert calibration.can_provide_transform is True
    assert calibration.t_head_pitch_camera is not None
    assert calibration.t_exec_camera is None


def test_legacy_static_calibration_is_rejected():
    with pytest.raises(ValueError, match="mcp_control"):
        CalibrationConfig.from_dict({"T_exec_camera": np.eye(4).tolist()})


def test_degree_head_joint_observation_is_normalized_to_radians():
    assert normalize_head_joint_states_rad([0.0, 24.99526934901729]) == pytest.approx(
        [0.0, math.radians(24.99526934901729)]
    )


def test_radian_head_joint_observation_is_left_unchanged():
    assert normalize_head_joint_states_rad([0.1, 0.43633230555555524]) == pytest.approx(
        [0.1, 0.43633230555555524]
    )


def test_gripper_action_is_30hz_and_sets_both_effector_fields():
    action, meta = build_gripper_action(_obs(), arm="right", gripper_value=1.0, duration_s=0.5)
    _assert_action_schema(action)
    assert len(action["left_effector"]) == 15
    assert len(action["right_effector"]) == 15
    assert action["left_effector"][-1] == [0.0]
    assert action["right_effector"][-1] == [1.0]
    assert math.isclose(action["trajectory_reference_time"], 15 / 30.0)
    assert meta["control_hz"] == 30.0


def test_control_hz_override_is_rejected():
    with pytest.raises(ValueError, match="30Hz"):
        validate_no_control_hz({"control_hz": 60})
