# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
基础数据加载器模块 - 定义了所有数据加载器的基础类
"""

from typing import Any

from corobot.dataloader.data_source.data_source_manager import DataSourceManager
from corobot.protocol.protocol_schemas import STD_MODEL_INPUT


class DataStreamConfig:
    """数据流配置类"""

    def __init__(self, config: dict):
        """
        初始化数据流配置

        Args:
            config: 配置字典
        """
        # 从配置中获取启用的数据流
        self.enabled_streams = config.get("enabled_streams", {})

        # 解析enabled_streams 获取支持的相机
        self.supported_cameras = [
            camera
            for camera in self.enabled_streams.get("camera", {}).keys()
            if self.enabled_streams.get("camera", {}).get(camera, False)
        ]

        # 获取 previous 和 delta 参数
        self.is_tracing = self.enabled_streams.get("tracing", {}).get("enabled", False)
        if self.is_tracing:
            self.previous = self.enabled_streams.get("tracing", {}).get("previous", 0)
            self.delta = self.enabled_streams.get("tracing", {}).get("delta", 0)
        else:
            self.previous = 0
            self.delta = 0

        # 验证 previous 和 delta 参数
        if self.previous < 0:
            raise ValueError("previous must be non-negative")
        if self.delta < 0:
            raise ValueError("delta must be non-negative")

        # 获取机器人状态数据流
        robot_states_config = self.enabled_streams.get("robot_states", {})
        self.robot_states = [state for state, enabled in robot_states_config.items() if enabled]


class DataLoader:
    """所有数据加载器的基类"""

    def __init__(self, config: dict[str, Any]):
        """
        初始化数据加载器

        Args:
            config: 配置字典
        """
        self.config = config
        self.stream_config = DataStreamConfig(config)

        # 创建策略管理器和配置管理器
        self.strategy_manager = DataSourceManager(
            {
                "supported_cameras": self.stream_config.supported_cameras,
                "robot_states": self.stream_config.robot_states,
                "data_source": self.config.get("data_source", {}),
            }
        )

        # 选择合适的策略
        self._select_strategy()
        # self._wrap_strategy()

    def _select_strategy(self):
        """选择数据源策略"""
        data_source_config = self.config.get("data_source", {})
        self.strategy = self.strategy_manager.get_data_source(data_source_config)

    def get_payload(self, *args, **kwargs) -> STD_MODEL_INPUT:
        """
        获取标准模型输入（STD_MODEL_INPUT）。

        - 若底层策略已返回 STD_MODEL_INPUT，则直接透传。
        - 否则将原始负载（扁平或结构化）转换为 Observation 并封装为 STD_MODEL_INPUT。
        """
        # 获取原始数据
        raw_payload = self.strategy.get_payload(*args, **kwargs)

        # 如果策略已直接返回 STD_MODEL_INPUT，则直接透传
        if isinstance(raw_payload, STD_MODEL_INPUT):
            return raw_payload

        # 将扁平/结构化的 raw_payload 转换为协议所需的 Observation 结构
        observation_dict = self._convert_raw_payload_to_observation(raw_payload)

        # 包装为标准输入协议
        return STD_MODEL_INPUT(observation=observation_dict)

    def _convert_raw_payload_to_observation(self, raw_payload: dict[str, Any]) -> dict[str, Any]:
        """将扁平键的原始负载转换为协议所需的 Observation 结构。

        Observation 结构:
        {
            "timestamps": List[int],
            "images": {camera_name: List[Any], ...},
            "states": {state_name: List[float], ...}  # 注意：状态为一帧的扁平列表
        }
        """
        if not isinstance(raw_payload, dict):
            return {"timestamps": 0, "images": {}, "states": {}}

        # 已是结构化 Observation 的情况，直接返回（做最小校验）
        if (
            "timestamps" in raw_payload
            and "images" in raw_payload
            and isinstance(raw_payload["images"], dict)
            and "states" in raw_payload
            and isinstance(raw_payload["states"], dict)
        ):
            return raw_payload

        timestamps_int: int = 0
        images: dict[str, Any] = {}
        states: dict[str, Any] = {}

        # 处理时间戳（扁平键）
        TIMESTAMP_KEY = "observation.timestamp"
        if TIMESTAMP_KEY in raw_payload:
            ts_value = raw_payload.get(TIMESTAMP_KEY)
            if ts_value is not None:
                timestamps_int = int(ts_value)

        # 处理图像（扁平键）: 根据启用的相机名从扁平键读取
        for camera_name in self.stream_config.supported_cameras:
            key = f"observation.images.{camera_name}"
            if key in raw_payload:
                img_value = raw_payload.get(key)
                if img_value is not None:
                    # 协议中为列表形式，这里做单帧 -> 序列 的封装
                    images[camera_name] = img_value if isinstance(img_value, list) else [img_value]

        # 处理机器人状态（扁平键）: 根据启用的状态名从扁平键读取（目标为 List[float]）
        for state_name in self.stream_config.robot_states:
            key = f"observation.states.{state_name}"
            if key in raw_payload:
                state_value = raw_payload.get(key)
                if state_value is not None:
                    if isinstance(state_value, list) and len(state_value) > 0:
                        states[state_name] = state_value
                    # 标量则封装为单元素列表
                    else:
                        states[state_name] = [state_value]

        return {
            "timestamps": timestamps_int,
            "images": images,
            "states": states,
        }

    def reset(self):
        """重置数据加载器状态"""
        if hasattr(self, "strategy") and self.strategy and hasattr(self.strategy, "reset"):
            try:
                self.strategy.reset()
            except Exception as e:
                print(f"重置策略出错: {e}")

    def shutdown(self):
        """关闭数据加载器，释放资源"""
        if hasattr(self, "strategy") and self.strategy and hasattr(self.strategy, "shutdown"):
            try:
                self.strategy.shutdown()
            except Exception as e:
                print(f"关闭策略出错: {e}")

        # 清理其他资源
        # ...
