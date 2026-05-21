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
from openpi_client import websocket_client_policy
from PIL import Image
from typing_extensions import override

from corobot.envs.g01_env import G01Env
from corobot.policy_tasks.policy_task_base import PolicyTaskBase
from corobot.protocol.protocol_schemas import Action, Observation
from corobot.utils.log_setting import CoLogger as logger


class OpenpiPolicyTask(PolicyTaskBase):
    def __init__(self, config_path: str):
        super().__init__(config_path)

        # policy 相关
        self._ws_host = self.config.get("policy", {}).get("host", "127.0.0.1")
        self._ws_port = self.config.get("policy", {}).get("port", 8000)
        self.openpi_client: websocket_client_policy.WebsocketClientPolicy | None = None

        # env 相关
        self._env: G01Env | None = None
        self._env_config = self.config.get("environment", {})

        # Task相关
        self._task_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @override
    def initialize(self):
        """初始化PolicyTask"""
        try:
            logger.info("初始化OpenpiPolicyTask...")
            self.openpi_client = websocket_client_policy.WebsocketClientPolicy(
                host=self._ws_host,
                port=self._ws_port,
            )

            self._env = G01Env(self._env_config)
            self._env.setup()

            self._initialized = True
            logger.info("OpenpiPolicyTask初始化成功")
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
            -0.7776767611503601,
            0.6110292077064514,
            0.0,
            -1.2839710712432861,
            0.7304046154022217,
            1.4953951835632324,
            -0.18760496377944946,
            0.7775720357894897,
            -0.6110292077064514,
            0.0,
            1.284005880355835,
            -0.7304570078849792,
            -1.4953428506851196,
            0.18762239813804626,
        ]
        target_grippers_positions = [0.0, 0.0]
        target_head_positions = [0.0, 0.43633230555555524]
        target_waist_positions = [0.8901176920412174, 0.4598677062988281]
        self._env.reset(
            target_arm_joint_positions,
            target_grippers_positions,
            target_head_positions,
            target_waist_positions,
        )

    @override
    def cleanup(self):
        """清理资源"""
        logger.info("清理OpenpiPolicyTask资源")

        self.stop()

        if self._env:
            self._env.close()
            self._env = None

        if self.openpi_client:
            self.openpi_client = None

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

    def _execute_step(self):
        # 1. 获取观察
        obs = self._env.get_observation()
        # 2. 构建推理输入
        payload = self.data_preprocess(obs)
        # 3. 远程推理
        pi_actions = self.openpi_client.infer(payload)["actions"].copy()
        # 4. 后处理
        action = self.data_postprocess(pi_actions)
        # 5. 执行动作
        self._env.execute_action(action, action.trajectory_reference_time)

    def data_preprocess(self, obs: Observation):
        head_rs_image = Image.fromarray(obs.images.head).resize((640, 480))
        left_wrist_image = Image.fromarray(obs.images.hand_left).resize((640, 480))
        right_wrist_image = Image.fromarray(obs.images.hand_right).resize((640, 480))
        head_rs_image = np.array(head_rs_image)
        left_wrist_image = np.array(left_wrist_image)
        right_wrist_image = np.array(right_wrist_image)

        if head_rs_image.shape[0] != 3:
            head_rs_image = np.transpose(head_rs_image, (2, 0, 1))
        if left_wrist_image.shape[0] != 3:
            left_wrist_image = np.transpose(left_wrist_image, (2, 0, 1))
        if right_wrist_image.shape[0] != 3:
            right_wrist_image = np.transpose(right_wrist_image, (2, 0, 1))

        state = np.concatenate(
            [
                obs.states.arm_joint_states,
                obs.states.gripper_states,
                obs.states.head_joint_states,
                obs.states.waist_joint_states,
                obs.states.end_wrench_states,
            ],
            axis=0,
        )

        payload = {
            "images": {
                "top_head": head_rs_image,
                "hand_right": right_wrist_image,
                "hand_left": left_wrist_image,
            },
            "prompt": "fold the cloth",
            "state": state,
        }

        return payload

    def data_postprocess(self, pi_actions):
        # 分离左右臂的关节角度
        left_arms = pi_actions[:, :7].tolist()  # 左臂7个关节
        right_arms = pi_actions[:, 7:14].tolist()  # 右臂7个关节
        grippers = np.clip(pi_actions[:, 14:16], 0, 1)
        left_effectors = grippers[:, :1].tolist()  # 左末端执行器
        right_effectors = grippers[:, 1:2].tolist()  # 右末端执行器
        action_data = {
            "timestamps": int(time.time() * 1e9),
            "trajectory_reference_time": 0.033 * len(left_arms),
            "base_link": "base_link",
            "left_arm": {"kind": "JOINT_ABS", "values": left_arms},
            "right_arm": {"kind": "JOINT_ABS", "values": right_arms},
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
