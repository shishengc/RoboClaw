# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
ServicePolicyTask - 基于远程推理服务的PolicyTask实现
"""

import threading
import time
from typing import Any

from websockets.sync import client

from corobot.envs.g01_env import G01Env
from corobot.policy_tasks.policy_task_base import PolicyTaskBase
from corobot.protocol.protocol_schemas import STD_MODEL_INPUT, Action
from corobot.transport import msgpack_numpy
from corobot.utils.api_decorators import expose_api
from corobot.utils.log_setting import CoLogger as logger


class ServicePolicyTask(PolicyTaskBase):
    """
    服务型PolicyTask - 整合远程推理策略和任务执行

    功能:
    1. 连接远程推理服务
    2. 管理G01机器人环境
    3. 执行感知-决策-执行循环
    4. 提供外部控制接口
    """

    def __init__(self, config_path: str):
        super().__init__(config_path)

        # Policy相关
        self._ws = None
        self._model_config: dict[str, Any] = {}
        self._policy_host = self.config.get("policy", {}).get("host", "127.0.0.1")
        self._policy_port = self.config.get("policy", {}).get("port", 8763)
        self._policy_url = f"ws://{self._policy_host}:{self._policy_port}"

        # Task相关
        self._env: G01Env | None = None
        self._task_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 运行时参数
        self._prompt = self.config.get("task", {}).get("prompt", "")
        self._step_interval = self.config.get("task", {}).get("step_interval", 1)
        self._max_steps = self.config.get("task", {}).get("max_steps", 0)  # 0表示无限制
        self._step_count = 0

    def initialize(self) -> bool:
        """初始化PolicyTask"""
        try:
            logger.info("初始化ServicePolicyTask...")

            # 1. 初始化Policy（连接远程服务）
            if not self._initialize_policy():
                return False

            # 2. 初始化环境
            if not self._initialize_environment():
                return False

            self._initialized = True
            logger.info("ServicePolicyTask初始化成功")
            return True

        except Exception as e:
            logger.error(f"初始化失败: {e}")
            return False

    def _initialize_policy(self) -> bool:
        """初始化远程推理策略"""
        try:
            logger.info(f"连接远程推理服务: {self._policy_url}")

            # 使用同步WebSocket客户端连接
            self._ws = client.connect(self._policy_url, compression=None, max_size=None)

            # 接收服务器配置
            config_data = self._ws.recv()
            self._model_config = msgpack_numpy.unpackb(config_data)

            logger.info("远程推理服务连接成功")
            return True

        except Exception as e:
            logger.error(f"连接远程推理服务失败: {e}")
            return False

    def _initialize_environment(self) -> bool:
        """初始化机器人环境"""
        try:
            logger.info("初始化G01环境...")

            # 构建环境配置
            env_config = self._create_env_config()

            # 创建环境
            self._env = G01Env(env_config)
            self._env.setup()

            # 重置到初始位置
            self._reset_robot_pose()

            logger.info("G01环境初始化成功")
            return True

        except Exception as e:
            logger.error(f"G01环境初始化失败: {e}")
            return False

    def _create_env_config(self) -> dict[str, Any]:
        """创建环境配置"""
        # 默认环境配置
        default_config = {
            "dataloader": {
                "data_source": {"ALIGNED_ROBOT": {"enabled": True}},
                "enabled_streams": {
                    "camera": {
                        "head": True,
                        "hand_left": True,
                        "hand_right": True,
                        "head_depth": False,
                        "hand_left_depth": False,
                        "hand_right_depth": False,
                    },
                },
            }
        }

        # 如果Policy提供了环境配置需求，则合并
        if self._model_config and "camera_names" in self._model_config:
            camera_config = {}
            for camera_name in self._model_config["camera_names"]:
                camera_config[camera_name] = True
            default_config["dataloader"]["enabled_streams"]["camera"] = camera_config

        if self._model_config and "joint_names" in self._model_config:
            joint_config = {}
            for joint_name in self._model_config["joint_names"]:
                joint_config[joint_name] = True
            default_config["dataloader"]["enabled_streams"]["robot_states"] = joint_config

        # 合并用户配置
        user_env_config = self.config.get("environment", {})
        if user_env_config:
            self._deep_merge(default_config, user_env_config)

        return default_config

    def _deep_merge(self, base_dict: dict, update_dict: dict):
        """深度合并字典"""
        for key, value in update_dict.items():
            if key in base_dict and isinstance(base_dict[key], dict) and isinstance(value, dict):
                self._deep_merge(base_dict[key], value)
            else:
                base_dict[key] = value

    def _reset_robot_pose(self):
        """重置机器人姿态"""
        # 从模型配置或默认值获取初始姿态
        init_pose = {}

        if self._model_config:
            init_pose.update(
                {
                    "target_grippers_positions": self._model_config.get("init_grippers_positions"),
                    "target_arm_joint_positions": self._model_config.get("init_arm_joint_positions"),
                    "target_head_positions": self._model_config.get("init_head_positions"),
                    "target_waist_positions": self._model_config.get("init_waist_positions"),
                }
            )

        # 使用默认值填充缺失的配置
        default_pose = {
            "target_grippers_positions": [0.0, 0.0],
            "target_arm_joint_positions": [
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
            ],
            "target_head_positions": [0.0, 0.43633230555555524],
            "target_waist_positions": [0.8901176920412174, 0.4598677062988281],
        }

        for key, default_value in default_pose.items():
            if init_pose.get(key) is None:
                init_pose[key] = default_value

        self._reset_grippers_pose(init_pose.get("target_grippers_positions"))

        arm_reset_pose = dict(init_pose)
        arm_reset_pose["target_grippers_positions"] = None
        self._env.reset(**arm_reset_pose)

    def _reset_grippers_pose(self, target_grippers_positions: list[float] | None):
        """先单独复位夹爪，避免手臂回初始位时夹爪仍保持抓取状态"""
        if target_grippers_positions is None:
            return
        if len(target_grippers_positions) < 2:
            logger.warning(f"夹爪复位目标数量不足，跳过夹爪优先复位: {target_grippers_positions}")
            return

        action = Action(
            timestamps=int(time.time() * 1e9),
            trajectory_reference_time=1.0,
            base_link="base_link",
            left_effector=[[float(target_grippers_positions[0])]],
            right_effector=[[float(target_grippers_positions[1])]],
        )
        logger.info(f"先复位夹爪: {target_grippers_positions[:2]}")
        self._env.execute_action(action, action.trajectory_reference_time)

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
        self._step_count = 0

        # 在新线程中运行任务循环
        self._task_thread = threading.Thread(target=self._task_loop, daemon=True)
        self._task_thread.start()

    def reset(self):
        """重置任务"""

        self.stop()
        self._reset_robot_pose()
        logger.info("机器人已重置")

    def stop(self):
        """停止任务执行"""
        if not self._running:
            return

        logger.info("停止任务执行")
        self._running = False
        self._stop_event.set()

        if self._task_thread and self._task_thread.is_alive():
            self._task_thread.join(timeout=5.0)

    def cleanup(self):
        """清理资源"""
        logger.info("清理ServicePolicyTask资源")

        # 停止任务
        self.stop()

        # 关闭环境
        if self._env:
            try:
                self._env.close()
            except Exception as e:
                logger.warning(f"关闭环境时出错: {e}")
            self._env = None

        # 关闭WebSocket连接
        if self._ws:
            try:
                self._ws.close()
            except Exception as e:
                logger.warning(f"关闭WebSocket连接时出错: {e}")
            self._ws = None

        self._initialized = False

    def _task_loop(self):
        """主任务执行循环"""
        logger.info("任务执行循环已启动")

        while self._running and not self._stop_event.is_set():
            try:
                # 检查步数限制
                if self._max_steps > 0 and self._step_count >= self._max_steps:
                    logger.info(f"达到最大步数限制: {self._max_steps}")
                    break

                # 执行单步
                self._execute_step()

                # 控制执行频率
                # self._stop_event.wait(self._step_interval)

            except Exception as e:
                logger.error(f"任务执行出错: {e}")
                break

        self._running = False
        logger.info("任务执行循环已结束")

    def _execute_step(self):
        """执行单步"""
        # 1. 获取观察
        obs = self._env.get_observation()

        # 2. 构建推理输入
        std_model_input = STD_MODEL_INPUT(observation=obs, prompt=self._prompt)

        # 3. 远程推理
        action = self._predict_action(std_model_input)

        # 临时调试：每次只执行 action chunk 的前一半，便于观察连续策略输出。
        step_duration = 0.06
        wait_extra = 0.25

        horizon = max(
            len(action.left_arm.values) if action.left_arm is not None else 0,
            len(action.right_arm.values) if action.right_arm is not None else 0,
            len(action.head) if action.head is not None else 0,
            len(action.waist) if action.waist is not None else 0,
            len(action.left_effector) if action.left_effector is not None else 0,
            len(action.right_effector) if action.right_effector is not None else 0,
            len(action.wheels) if action.wheels is not None else 0,
        )

        if horizon <= 0:
            logger.warning("远程推理返回的action为空，跳过执行")
            return

        step_count = max(1, horizon // 2)

        data = action.model_dump()
        for name in ("left_arm", "right_arm"):
            if data.get(name) is not None:
                data[name]["values"] = data[name]["values"][:step_count]

        for name in ("head", "waist", "left_effector", "right_effector", "wheels"):
            if data.get(name) is not None:
                data[name] = data[name][:step_count]

        data["timestamps"] = int(time.time() * 1e9)
        data["trajectory_reference_time"] = step_duration * step_count
        chunk_action = Action(**data)

        wait_action_time = chunk_action.trajectory_reference_time + wait_extra
        logger.info(
            f"执行半个action chunk: {step_count}/{horizon} steps, "
            f"trajectory_reference_time={chunk_action.trajectory_reference_time:.3f}s, "
            f"wait_action_time={wait_action_time:.3f}s"
        )

        # 4. 执行动作：这里会阻塞等待半个chunk执行完成，然后下一轮重新推理
        self._env.execute_action(chunk_action, wait_action_time)
        self._step_count += 1
        # 临时调试结束

        # # 4. 执行动作
        # self._env.execute_action(action, 0)

        # self._step_count += 1

    def _predict_action(self, std_model_input: STD_MODEL_INPUT) -> Action:
        """调用远程推理服务"""
        if self._ws is None:
            raise RuntimeError("WebSocket连接未建立，请先初始化策略服务")

        try:
            # 发送数据
            data = std_model_input.model_dump()
            self._ws.send(msgpack_numpy.packb(data))

            # 接收响应
            response_data = self._ws.recv()
            action_data = msgpack_numpy.unpackb(response_data)

            return Action(**action_data)

        except Exception as e:
            logger.error(f"远程推理失败: {e}")
            raise

    # === 对外API接口 ===
    @expose_api(method="POST", path="/set_prompt")
    def set_prompt(self, prompt: str) -> dict[str, Any]:
        """
        设置任务提示词

        Args:
            prompt: 新的提示词

        Returns:
            Dict: 操作结果
        """
        old_prompt = self._prompt
        self._prompt = prompt

        logger.info(f"提示词已更新: '{old_prompt}' -> '{prompt}'")

        return {"old_prompt": old_prompt, "new_prompt": prompt}

    @expose_api(method="GET", path="/obs")
    def get_obs(self) -> dict[str, Any]:
        """获取标准模型输入"""
        return self._env.get_observation()

    @expose_api(method="GET", path="/get_prompt")
    def get_prompt(self) -> str:
        """获取当前提示词"""
        return self._prompt

    @expose_api(method="GET", path="/status")
    def get_status(self) -> dict[str, Any]:
        """获取详细状态"""
        base_status = super().get_status()

        # 添加任务特定状态
        task_status = {
            "prompt": self._prompt,
            "step_count": self._step_count,
            "max_steps": self._max_steps,
            "step_interval": self._step_interval,
            "policy_connected": self._ws is not None,
            "environment_ready": self._env is not None,
        }

        base_status.update(task_status)
        return base_status

    @expose_api(method="POST", path="/set_max_steps")
    def set_max_steps(self, max_steps: int) -> dict[str, Any]:
        """设置最大步数"""
        old_max_steps = self._max_steps
        self._max_steps = max(0, max_steps)

        logger.info(f"最大步数已更新: {old_max_steps} -> {self._max_steps}")

        return {"old_max_steps": old_max_steps, "new_max_steps": self._max_steps}

    @expose_api(method="GET", path="/execution_stats")
    def get_execution_stats(self) -> dict[str, Any]:
        """获取执行统计信息"""
        return {
            "step_count": self._step_count,
            "max_steps": self._max_steps,
            "progress": self._step_count / self._max_steps if self._max_steps > 0 else 0.0,
            "running": self._running,
            "step_interval": self._step_interval,
        }

    @expose_api(method="POST", path="/set_evaluate_params")
    def set_evaluate_params(self, evaluate_params: dict[str, Any]) -> dict[str, Any]:
        """
        {
        "policy": {
            "id": 1,
            "name": "Policy_A",
            "host": "10.0.0.12",
            "port": 9001,
            "description": "policy description"
        },
        "prompt": "Please evaluate grasp on the red cube."
        "step_interval": 1
        }
        """
        logger.info("cleanup")
        self.cleanup()
        logger.info(f"set_evaluate_params: {evaluate_params}")
        self._policy_host = evaluate_params.get("policy", {}).get("host")
        self._policy_port = evaluate_params.get("policy", {}).get("port")
        self._policy_url = f"ws://{self._policy_host}:{self._policy_port}"
        self._prompt = evaluate_params.get("prompt")
        self._step_interval = evaluate_params.get("step_interval")

        logger.info("initialize")
        self.initialize()

        return {
            "policy_host": self._policy_host,
            "policy_port": self._policy_port,
            "policy_url": self._policy_url,
            "step_interval": self._step_interval,
            "prompt": self._prompt,
        }


"""
1. 获取提示词
curl -X GET http://0.0.0.0:8765/get_prompt

return:  {"data":"Please evaluate grasp on the red cube.","success":true}


2. 设置提示词
curl -X POST http://0.0.0.0:8765/set_prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "grasp the red cube"}'

return: {
    "data": {
        "new_prompt": "grasp the red cube",
        "old_prompt": "Please evaluate grasp on the red cube."
    },
    "success": true
}

3. 获取详细状态
curl -X GET http://0.0.0.0:8765/status

return: {
    "data": {
        "config_path": "~/.cache/agibot/corobot/policy_task_config.yml",
        "environment_ready": true,
        "initialized": true,
        "max_steps": 0,
        "policy_connected": true,
        "prompt": "grasp the red cube",
        "running": false,
        "step_count": 0,
        "step_interval": 0.033
    },
    "success": true
}

4. 设置最大步数
curl -X POST http://0.0.0.0:8765/set_max_steps \
  -H "Content-Type: application/json" \
  -d '{"max_steps": 100}'

return: {"data":{"new_max_steps":100,"old_max_steps":0},"success":true}

5. 获取执行统计信息
curl -X GET http://0.0.0.0:8765/execution_stats

return: {
    "data": {
        "max_steps": 100,
        "progress": 0.0,
        "running": false,
        "step_count": 0,
        "step_interval": 0.033
    },
    "success": true
}

6. 设置评估参数（切换策略服务器）
curl -X POST http://0.0.0.0:8765/set_evaluate_params \
  -H "Content-Type: application/json" \
  -d '{
    "evaluate_params": {
      "policy": {
        "host": "127.0.0.1",
        "port": 8001
      },
      "prompt": "Please evaluate grasp on the red cube.",
      "step_interval": 0.33
    }
  }'

return:  {"data":{"policy_host":"127.0.0.1","policy_port":8001,"policy_url":"ws://127.0.0.1:8001"},"success":true}

7. 启动任务

curl -X POST http://0.0.0.0:8765/system/start_policytask

return: {"message":"PolicyTask\u542f\u52a8\u4e2d...","success":true}

8. 停止任务

curl -X POST http://0.0.0.0:8765/system/stop_policytask

return: {"message":"PolicyTask\u5df2\u505c\u6b62","success":true}

9. 重置机器人
curl -X POST http://0.0.0.0:8765/system/reset_policytask

return: {"message":"PolicyTask\u5df2\u91cd\u7f6e","success":true}

"""
