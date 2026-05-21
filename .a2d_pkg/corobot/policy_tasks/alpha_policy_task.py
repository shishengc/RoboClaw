# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
openpi_policy_task.py

This script is used to integrate the openpi policy into the robot.
openpi should be installed and running before using this script.

"""

import threading
import time

import numpy as np
import requests
from PIL import Image
from scipy.spatial.transform import Rotation as R
from typing_extensions import override

from corobot.envs.g01_env import G01Env
from corobot.policy_tasks.policy_task_base import PolicyTaskBase
from corobot.protocol.protocol_schemas import Action, Observation
from corobot.utils.api_decorators import expose_api
from corobot.utils.log_setting import CoLogger as logger


class AlphaPolicyTask(PolicyTaskBase):
    def __init__(self, config_path: str):
        super().__init__(config_path)

        # policy 相关
        self._policy_host = self.config.get("policy", {}).get("host", "127.0.0.1")
        self._policy_port = self.config.get("policy", {}).get("port", 8000)

        # env 相关
        self._env: G01Env | None = None
        self._env_config = self.config.get("environment", {})

        # Task相关
        self._task_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def infer(self, payload: dict) -> np.ndarray | None:
        response = requests.post(
            f"http://{self._policy_host}:{self._policy_port}/act",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code == 200:
            result = response.json()
            action = np.array(result["action"])
            logger.info(f"动作输出: {action}")
            logger.info(f"动作输出: {action.shape}")
            logger.info("请求成功!")
            return result
        else:
            logger.error(f"请求失败，状态码: {response.status_code}")
            logger.error(f"错误信息: {response.text}")
            return None

    @override
    def initialize(self):
        """初始化PolicyTask"""
        try:
            logger.info("初始化AlphaPolicyTask...")
            self._env = G01Env(self._env_config)
            self._env.setup()

            self._initialized = True
            logger.info("AlphaPolicyTask初始化成功")
            return True
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            return False

    @override
    def start(self):
        """启动任务执行"""
        if self._running:
            logger.warning("任务已在运行中")
            return

        if not self._initialized:
            logger.error("任务未初始化")
            return

        logger.info("启动任务执行循环")
        self._running = True
        self._stop_event.clear()

        self._task_thread = threading.Thread(target=self._task_loop, daemon=True)
        self._task_thread.start()

    @override
    def stop(self):
        """停止任务执行"""
        if not self._running:
            return

        logger.info("停止任务执行")
        self._running = False
        self._stop_event.set()

        if self._task_thread and self._task_thread.is_alive():
            self._task_thread.join(timeout=5.0)
            self._task_thread = None

    @override
    def reset(self):
        """重置任务"""
        self.stop()
        target_arm_joint_positions = [
            -1.0742219686508179,
            0.6111513376235962,
            0.2795141041278839,
            -1.284005880355835,
            0.7304918766021729,
            1.4953428506851196,
            -0.18760496377944946,
            1.0742219686508179,
            -0.6110990047454834,
            -0.2794268727302551,
            1.2841979265213013,
            -0.7304046154022217,
            -1.4960583448410034,
            0.18514449894428253,
        ]

        target_grippers_positions = [0.0, 0.21733333]
        target_head_positions = [0.0, 0.4363323152065277]
        target_waist_positions = [0.8901177048683167, 0.46000000834465027]

        self._env.reset(
            target_arm_joint_positions,
            target_grippers_positions,
            target_head_positions,
            target_waist_positions,
        )

    @override
    def cleanup(self):
        """清理资源"""
        logger.info("清理AlphaPolicyTask资源")

        self.stop()

        if self._env:
            self._env.close()
            self._env = None

        self._initialized = False
        self._running = False
        self._task_thread = None

    def _task_loop(self):
        """任务执行循环"""
        logger.info("任务执行循环已启动")

        while self._running and not self._stop_event.is_set():
            try:
                self._execute_step()
                # self._stop_event.wait(self._step_interval)

            except Exception as e:
                logger.error(f"任务执行出错: {e}")
                break

        self._running = False
        logger.info("任务执行循环已结束")

    @expose_api(method="GET", path="/infer")
    def _execute_step(self):
        try:
            # 1. 获取观察
            obs = self._env.get_observation()
            # 2. 构建推理输入
            payload = self.data_preprocess(obs)
            # 3. 远程推理
            action = self.infer(payload).get("action", None)
            if action is None:
                logger.error("推理失败")
                return
            # 4. 后处理
            action = self.data_postprocess(action)
            # 5. 执行动作
            self._env.execute_action(action, action.trajectory_reference_time)
        except Exception as e:
            logger.error(f"推理失败: {e}")
            return

    def data_preprocess(self, obs: Observation):
        # default is (800, 1280, 3)
        # (480, 848, 3)
        # (480, 848, 3)
        head_rs_image = Image.fromarray(obs.images.head).resize((224, 224))
        left_wrist_image = Image.fromarray(obs.images.hand_left).resize((224, 224))
        right_wrist_image = Image.fromarray(obs.images.hand_right).resize((224, 224))

        head_rs_image = np.array(head_rs_image)
        left_wrist_image = np.array(left_wrist_image)
        right_wrist_image = np.array(right_wrist_image)
        logger.debug(f"obs.states.gripper_states: {obs.states.gripper_states}")
        logger.debug(f"obs.states.end_pose[0].position: {obs.states.end_pose[0].position}")
        logger.debug(f"obs.states.end_pose[1].position: {obs.states.end_pose[1].position}")
        logger.debug(f"obs.states.end_velocity: {obs.states.end_velocity}")
        logger.debug(f"obs.states.end_pose[0].orientation: {obs.states.end_pose[0].orientation}")
        logger.debug(f"obs.states.end_pose[1].orientation: {obs.states.end_pose[1].orientation}")
        logger.debug(f"obs.states.arm_joint_states: {obs.states.arm_joint_states}")
        gripper = np.array(obs.states.gripper_states) / 120
        state = np.concatenate(
            [
                gripper,  # 2
                obs.states.end_pose[0].position,  # 3
                obs.states.end_pose[1].position,  # 3
                obs.states.end_velocity,  # 12
                obs.states.end_pose[0].orientation,  # 4
                obs.states.end_pose[1].orientation,  # 4
                obs.states.arm_joint_states,  # 14
            ],
            axis=0,
        )  # shape for [1,42]
        state = state.reshape(1, -1)

        payload = {
            "top": head_rs_image.tolist(),
            "right": right_wrist_image.tolist(),
            "left": left_wrist_image.tolist(),
            "instruction": "Fold short sleeves",
            "ctrl_freqs": [30],
            "state": state.tolist(),
            "use_new_data_chain": True,
            "state_type": "direct",
        }

        return payload

    def data_postprocess(self, action):
        action = np.array(action)
        grippers = np.clip(action[:, :2], 0, 1)
        left_effectors = grippers[:, :1].tolist()  # 左末端执行器
        right_effectors = grippers[:, 1:2].tolist()  # 右末端执行器

        # 提取左右臂的位置和方向
        left_positions = action[:, 2:5]  # [N, 3]
        left_orientations = action[:, 8:12]  # [N, 4]
        right_positions = action[:, 5:8]  # [N, 3]
        right_orientations = action[:, 12:16]  # [N, 4]

        # 将四元数转换为欧拉角
        left_rpy = R.from_quat(left_orientations).as_euler("xyz", degrees=False)  # [N, 3]
        right_rpy = R.from_quat(right_orientations).as_euler("xyz", degrees=False)  # [N, 3]
        # 分别创建左右臂的6维数据：位置(3) + 欧拉角(3)
        left_arms = np.concatenate([left_positions, left_rpy], axis=1)  # [N, 6]
        right_arms = np.concatenate([right_positions, right_rpy], axis=1)  # [N, 6]

        action_data = {
            "timestamps": int(time.time() * 1e9),
            "trajectory_reference_time": 0.033 * len(left_arms),
            "base_link": "base_link",
            "left_arm": {"kind": "EEF_ABS", "values": left_arms.tolist()},
            "right_arm": {"kind": "EEF_ABS", "values": right_arms.tolist()},
            "left_effector": left_effectors,
            "right_effector": right_effectors,
        }
        action = Action(**action_data)

        return action

    def _data_map(
        self,
        data: list[float] | float,
        origin_min: float,
        origin_max: float,
        float_min: float,
        float_max: float,
    ) -> list[float]:
        """
        Data mapping
        """
        if data is None:
            return None
        if isinstance(data, float):
            return (data - origin_min) / (origin_max - origin_min) * (float_max - float_min) + float_min

        return [(x - origin_min) / (origin_max - origin_min) * (float_max - float_min) + float_min for x in data]


class AlphaPolicyTaskJoint(AlphaPolicyTask):
    def __init__(self, config_path: str):
        super().__init__(config_path)

    def data_preprocess(self, obs: Observation):
        head_rs_image = Image.fromarray(obs.images.head).resize((224, 224))
        left_wrist_image = Image.fromarray(obs.images.hand_left).resize((224, 224))
        right_wrist_image = Image.fromarray(obs.images.hand_right).resize((224, 224))

        head_rs_image = np.array(head_rs_image)
        left_wrist_image = np.array(left_wrist_image)
        right_wrist_image = np.array(right_wrist_image)

        state = np.concatenate(
            [
                obs.states.arm_joint_states,  # 14
            ],
            axis=0,
        )  # shape for [1,14]
        state = state.reshape(1, -1)

        payload = {
            "top": head_rs_image.tolist(),
            "right": right_wrist_image.tolist(),
            "left": left_wrist_image.tolist(),
            "instruction": "Fold short sleeves",
            "ctrl_freqs": [30],
            "state": state.tolist(),
            "use_new_data_chain": True,
            "state_type": "direct",
        }

        return payload

    def data_postprocess(self, action):
        action = np.array(action)
        grippers = np.clip(action[:, :2], 0, 1)
        left_effectors = grippers[:, :1].tolist()  # 左末端执行器
        right_effectors = grippers[:, 1:2].tolist()  # 右末端执行器

        # 分离左右臂的关节角度
        left_arms = action[:, 2:9]  # 左臂7个关节
        right_arms = action[:, 9:16]  # 右臂7个关节

        action_data = {
            "timestamps": int(time.time() * 1e9),
            "trajectory_reference_time": 0.033 * len(left_arms),
            "base_link": "base_link",
            "left_arm": {"kind": "JOINT_ABS", "values": left_arms.tolist()},
            "right_arm": {"kind": "JOINT_ABS", "values": right_arms.tolist()},
            "left_effector": left_effectors,
            "right_effector": right_effectors,
        }
        action = Action(**action_data)

        return action
