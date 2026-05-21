# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

from enum import IntEnum
from typing import Any

from corobot.motion_control.motion_control_base import MotionControlBase
from corobot.protocol.protocol_schemas import STD_MODEL_OUTPUT, Action, ArmControl
from corobot.robots.g01_robot import G01Robot


def build_robot_states(std_model_output: STD_MODEL_OUTPUT) -> dict[str, Any]:
    obs = std_model_output.observation  # Observation
    s = obs.states  # States
    return {
        k: v
        for k, v in {
            "head": s.head_joint_states,
            "waist": s.waist_joint_states,
            "arm": s.arm_joint_states,
            "gripper": s.gripper_states,
            "hand": s.hand_joint_states,
        }.items()
        if v is not None and v != []
    }


def action_to_robot_actions(action: Action, left_arm_dofs=7, right_arm_dofs=7) -> list[dict]:
    def arm_type_from_kind(k: str) -> str:
        return {
            "EEF_ABS": "ABS_POSE",
            "EEF_DELTA": "DELTA_POSE",
            "JOINT_ABS": "ABS_JOINT",
            "JOINT_DELTA": "DELTA_JOINT",
        }[k]

    def add_part_if(
        step: dict,
        name: str,
        seq: list[list[float]] | None,
        i: int,
        ctrl: str,
        limit: list[list[float]] | None = None,
    ):
        if not seq:  # None 或 []
            return
        if i < len(seq) and seq[i] is not None:
            if limit is not None:
                data = [min(max(x, limit[i][0]), limit[i][1]) for i, x in enumerate(seq[i])]
            else:
                data = seq[i]
            step[name] = {"action_data": data, "control_type": ctrl}

    def add_arm_if(
        step: dict,
        name: str,
        arm_control: ArmControl | None,
        i: int,
    ):
        if arm_control is None:
            return
        if i < len(arm_control.values) and arm_control.values[i] is not None:
            arm_type = arm_type_from_kind(arm_control.kind)
            step[name] = {"action_data": arm_control.values[i], "control_type": arm_type}

    # 确定轨迹长度 - 找到所有非空组件中的最大长度
    components = [
        action.left_arm.values if action.left_arm is not None else None,
        action.right_arm.values if action.right_arm is not None else None,
        action.waist,
        action.head,
        action.left_effector,
        action.right_effector,
        action.wheels,
    ]

    T = max(len(comp) for comp in components if comp is not None) if any(comp is not None for comp in components) else 0

    # 没有控制数据时返回空列表
    if T == 0:
        return []

    ras: list[dict] = []

    for i in range(T):
        step: dict[str, Any] = {}

        # 处理左右臂控制
        add_arm_if(step, "left_arm", action.left_arm, i)
        add_arm_if(step, "right_arm", action.right_arm, i)

        # 其它部位：有才加键
        add_part_if(step, "waist", action.waist, i, "ABS_JOINT")
        add_part_if(step, "head", action.head, i, "ABS_JOINT")
        # add_part_if(step, "left_effector", action.left_effector, i, "ABS_JOINT")
        # add_part_if(step, "right_effector", action.right_effector, i, "ABS_JOINT")
        left_effector = action.left_effector[i] if action.left_effector and i < len(action.left_effector) else None
        right_effector = action.right_effector[i] if action.right_effector and i < len(action.right_effector) else None

        if left_effector and right_effector:
            step["gripper"] = {
                "action_data": [float(left_effector[0]), float(right_effector[0])],
                "control_type": "ABS_JOINT",
            }
        
        # add_part_if(step, "chassis",  action.wheels,   i, "JOINT_SPEED") # Not supported yet
        if step != {}:
            ras.append(step)

    return ras


class MotionControlMode(IntEnum):
    STOP = 0
    SERVO = 1
    PLANNING = 2


class AsyncMotionController(MotionControlBase):
    def __init__(self, config):
        self._controller = G01Robot().instance_controller()

    def start(self, ts=None):
        self._controller.set_motion_control_mode(MotionControlMode.SERVO)

    def stop(self):
        self._controller.set_motion_control_mode(MotionControlMode.STOP)

    def execute(self, std_model_output: STD_MODEL_OUTPUT):
        action = std_model_output.action

        # 对action.waist  waist 是 list[list]的第二个数据 * 100  输入为m 转cm, (N,2) shape
        if action.waist is not None:
            action.waist = [[x, y * 100] for x, y in action.waist]

        infer_timestamp = action.timestamps
        base_link = action.base_link or "base_link"
        trajectory_reference_time = action.trajectory_reference_time

        robot_states = build_robot_states(std_model_output)
        robot_actions = action_to_robot_actions(action)
        if len(robot_actions) == 0:
            return

        self._controller.trajectory_tracking_control(
            infer_timestamp,
            robot_states,
            robot_actions,
            base_link,
            trajectory_reference_time,
        )
        data_to_vaild = {
            "infer_timestamp": infer_timestamp,
            "robot_states": robot_states,
            "robot_actions": robot_actions,
            "base_link": base_link,
            "trajectory_reference_time": trajectory_reference_time,
        }
        return data_to_vaild
