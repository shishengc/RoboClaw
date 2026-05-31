# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
G01 environment class, directly call dataloader and motion_control
"""

import os
import time
from typing import Any

import numpy as np
import ruckig

from corobot.dataloader import DataLoaderFactory
from corobot.motion_control.async_motion_controller import AsyncMotionController
from corobot.motion_control.mock_motion_controller import MockMotionController
from corobot.protocol.protocol_schemas import STD_MODEL_INPUT, STD_MODEL_OUTPUT, Action
from corobot.utils.log_setting import CoLogger as logger
from corobot.utils.yaml_utils import load_yaml


_A2D_EMPTY_JOINT_CALLBACK_PATCHED = False


def _patch_a2d_sdk_empty_joint_callbacks() -> None:
    """Ignore malformed empty DDS head/waist joint frames from a2d_sdk.

    Some robot DDS sessions publish empty head state frames during startup. The
    upstream a2d_sdk callback indexes motor_states[0] and motor_states[1]
    unconditionally, which raises repeated exceptions from the ctypes callback
    thread. Patch before RobotDds is instantiated so subscribers receive guarded
    callbacks.
    """
    global _A2D_EMPTY_JOINT_CALLBACK_PATCHED
    if _A2D_EMPTY_JOINT_CALLBACK_PATCHED:
        return

    try:
        from a2d_sdk.robot import RobotDds
    except Exception as exc:
        logger.warning(f"Could not patch a2d_sdk RobotDds callbacks: {exc}")
        return

    original_head_callback = getattr(RobotDds, "head_joint_state_callback", None)
    original_waist_callback = getattr(RobotDds, "waist_joint_state_callback", None)
    if original_head_callback is None or original_waist_callback is None:
        return
    if getattr(original_head_callback, "_corobot_empty_joint_guard", False):
        _A2D_EMPTY_JOINT_CALLBACK_PATCHED = True
        return

    def guarded_head_joint_state_callback(self, msg):
        motor_states = getattr(msg, "motor_states", [])
        if len(motor_states) < 2:
            try:
                del self._head_joint_state.motor_states[:]
                self._head_joint_state.motor_states.extend(motor_states)
            except Exception:
                pass
            return
        return original_head_callback(self, msg)

    def guarded_waist_joint_state_callback(self, msg):
        motor_states = getattr(msg, "motor_states", [])
        names = getattr(msg, "name", [])
        if len(motor_states) == 0:
            try:
                del self._waist_joint_state.motor_states[:]
            except Exception:
                pass
            return
        if len(names) < len(motor_states):
            try:
                del self._waist_joint_state.motor_states[:]
                self._waist_joint_state.motor_states.extend(motor_states)
                for index, state in enumerate(motor_states):
                    name = names[index] if index < len(names) else ""
                    if name == "joint_body_pitch":
                        self._body_pose_joint_states[2] = state.position
                    elif name == "joint_lift_body":
                        self._body_pose_joint_states[3] = state.position
                timestamp = int(msg.header.stamp.sec * 1e9 + msg.header.stamp.nanosec)
                self._waist_buffer.write(self._body_pose_joint_states[2:4], timestamp=timestamp)
            except Exception:
                pass
            return
        return original_waist_callback(self, msg)

    guarded_head_joint_state_callback._corobot_empty_joint_guard = True
    RobotDds.head_joint_state_callback = guarded_head_joint_state_callback
    RobotDds.waist_joint_state_callback = guarded_waist_joint_state_callback
    _A2D_EMPTY_JOINT_CALLBACK_PATCHED = True


class G01Env:
    def __init__(self, config: str | dict[str, Any] | None = None):
        """
        Initialize G01 environment

        Args:
            config: 可以是以下几种类型:
                - str: 配置文件路径
                - Dict: 配置字典
                - None: 使用默认配置
        """
        self.config_path = None
        self.config = None
        self._started = False

        self._load_config(config)
        logger.log_level_set(self.config)
        self._dl_cfg = self.config.get("dataloader", {})
        self._mc_cfg = self.config.get("motion_controller", {})
        self._mc_timeout_s = float(self._mc_cfg.get("execution_timeout_s", 2.0))

        _patch_a2d_sdk_empty_joint_callbacks()
        self._dataloader = DataLoaderFactory.create_data_loader(self._dl_cfg)
        # motion controller
        if self._mc_cfg.get("enabled", False):
            self._controller = AsyncMotionController(self._mc_cfg)
            logger.info("MotionController initialized")
        else:
            logger.info("MotionController disabled")
            self._controller = MockMotionController()

    def _load_config(self, config: str | dict[str, Any] | None) -> None:
        """Load configuration from various sources"""
        if config is None:
            # 使用默认配置
            self.config = self._get_default_config()
        elif isinstance(config, str):
            # 从文件路径加载
            self.config_path = config
            if not os.path.isabs(self.config_path):
                self.config_path = os.path.abspath(self.config_path)

            if not os.path.exists(self.config_path):
                raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

            self.config = load_yaml(self.config_path)
        elif isinstance(config, dict):
            # 直接使用配置字典
            self.config = config
        else:
            raise ValueError(f"Unsupported config type: {type(config)}")

    def _get_default_config(self) -> dict[str, Any]:
        """获取默认配置"""
        return {
            "dataloader": {
                "data_source": {"ALIGNED_ROBOT": {"enabled": True}},
                "enabled_streams": {
                    "camera": {"head": True, "hand_left": True, "hand_right": True},
                },
            },
            "motion_controller": {
                "enabled": True,
                "left_arm_dofs": 7,
                "right_arm_dofs": 7,
                "execution_timeout_s": 2.0,
            },
        }

    def setup(self):
        """Start environment"""
        # dataloader needs to wait for a while to ensure the data source is ready
        time.sleep(1)
        try:
            self._dataloader.reset()
            self._controller.start()
        except Exception as e:
            logger.error(f"Start environment failed: {e}")
            raise e
        self._started = True
        logger.info("Environment started successfully")

    def close(self):
        """Stop environment"""
        if not self._started:
            logger.warning("Environment not started")
            return
        try:
            self._controller.stop()
            self._dataloader.reset()
            self._dataloader.shutdown()
        except Exception as e:
            logger.error(f"Stop environment failed: {e}")
            raise e
        self._started = False
        logger.info("Environment stopped successfully")

    def get_observation(self):
        return self.get_std_model_input().observation

    def execute_action(self, action: Action | dict[str, Any], wait_action_time: float = 1.0) -> None:
        if isinstance(action, dict):
            action = Action(**action)
        std_model_output = STD_MODEL_OUTPUT(action=action)
        self.execute_std_model_output(std_model_output, wait_action_time)

    def get_std_model_input(self) -> STD_MODEL_INPUT:
        """Get standard model input"""
        if not self._started:
            logger.warning("Environment not started")
            return None
        try:
            return self._dataloader.get_payload()
        except Exception as e:
            logger.error(f"Get standard model input failed: {e}")
            raise e

    def execute_std_model_output(self, std_model_output: STD_MODEL_OUTPUT, wait_action_time: float = 1.0) -> None:
        """Execute standard model output"""
        if not self._started:
            logger.warning("Environment not started")
            return
        try:
            self._controller.execute(std_model_output)
            time.sleep(wait_action_time)
        except Exception as e:
            logger.error(f"Execute standard model output failed: {e}")
            raise e

    def _build_target_pose_action(
        self,
        obs: dict[str, Any],
        target_arm_joint_positions: list[float],
        target_grippers_positions: list[float] = None,
        target_head_positions: list[float] = None,
        target_waist_positions: list[float] = None,
    ) -> dict[str, Any]:
        # ----------------------------arm---------------------------------#
        if target_arm_joint_positions is None or len(target_arm_joint_positions) == 0:
            raise Exception("Target arm joint positions is None or empty, build init pose action failed")

        current_arm_joint_positions = obs["states"]["arm_joint_states"]
        if len(current_arm_joint_positions) == 0:
            raise Exception("Arm joint states not ready, build init pose action failed")

        interval = 0.01
        qpos = list(current_arm_joint_positions)
        dof = 14
        rk = ruckig.Ruckig(dof, interval)
        rk_input = ruckig.InputParameter(dof)
        rk_output = ruckig.OutputParameter(dof)
        rk_input.current_position = qpos
        rk_input.current_velocity = [0.0] * 14
        rk_input.current_acceleration = [0.0] * 14

        rk_input.target_position = target_arm_joint_positions[:14]
        rk_input.target_velocity = [0.0] * 14
        rk_input.target_acceleration = [0.0] * 14

        rk_input.max_velocity = [2.0] * 14
        rk_input.max_acceleration = [1.0] * 14
        rk_input.max_jerk = [5.0] * 14

        trajs = []
        while rk.update(rk_input, rk_output) == ruckig.Result.Working:
            trajs.append(rk_output.new_position)
            rk_output.pass_to_input(rk_input)

        # 分离左右臂的关节角度
        left_arm_trajs = [traj[:7] for traj in trajs]  # 左臂7个关节
        right_arm_trajs = [traj[7:14] for traj in trajs]  # 右臂7个关节

        action = {
            "timestamps": int(time.time() * 1e9),
            "trajectory_reference_time": interval * len(trajs),
            "base_link": "base_link",
            "left_arm": {"kind": "JOINT_ABS", "values": left_arm_trajs},
            "right_arm": {"kind": "JOINT_ABS", "values": right_arm_trajs},
        }

        # ----------------------------effectors---------------------------------#
        if target_grippers_positions is not None:
            current_grippers_positions = obs["states"]["gripper_states"]
            if len(current_grippers_positions) == 0:
                raise Exception("Gripper states not ready, build init pose action failed")

            grippers_trajs = np.linspace(current_grippers_positions, target_grippers_positions, len(trajs))
            # 分离左右末端执行器
            left_effector_trajs = grippers_trajs[:, :1].tolist()  # 左末端执行器
            right_effector_trajs = grippers_trajs[:, 1:2].tolist()  # 右末端执行器
            action["left_effector"] = left_effector_trajs
            action["right_effector"] = right_effector_trajs

        # ----------------------------head---------------------------------#
        if target_head_positions is not None:
            current_head_positions = obs["states"]["head_joint_states"]
            if len(current_head_positions) == 0:
                raise Exception("Head joint states not ready, build init pose action failed")

            head_trajs = np.linspace(current_head_positions, target_head_positions, len(trajs))
            action["head"] = head_trajs.tolist()

        # ----------------------------waist---------------------------------#
        if target_waist_positions is not None:
            current_waist_positions = obs["states"]["waist_joint_states"]
            if len(current_waist_positions) == 0:
                raise Exception("Waist joint states not ready, build init pose action failed")

            waist_trajs = np.linspace(current_waist_positions, target_waist_positions, len(trajs))
            action["waist"] = waist_trajs.tolist()
            action["trajectory_reference_time"] = max(3.0, interval * len(trajs))  # waist take longer time to move

        return action

    def reset(
        self,
        target_arm_joint_positions: list[float] = [0] * 14,
        target_grippers_positions: list[float] | None = None,
        target_head_positions: list[float] | None = None,
        target_waist_positions: list[float] | None = None,
    ):
        obs = self.get_observation()
        obs_data = obs.model_dump()
        if obs is None:
            raise Exception("Get observation failed")

        action_data_dict = self._build_target_pose_action(
            obs_data,
            target_arm_joint_positions,
            target_grippers_positions,
            target_head_positions,
            target_waist_positions,
        )

        if hasattr(Action, "model_validate"):
            action = Action.model_validate(action_data_dict)
        else:
            action = Action(**action_data_dict)

        self.execute_action(action, action.trajectory_reference_time)

        return self.get_observation()

    def step(self, action: Action) -> dict[str, Any]:
        """Step"""
        if not self._started:
            logger.warning("Environment not started")
            return None
        try:
            self.execute_action(action)
            return self.get_observation()
        except Exception as e:
            logger.error(f"Step failed: {e}")
            raise e
