# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
PolicyTask基类 - 整合Policy、Task、Env的一体化基类
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml


class PolicyTaskBase(ABC):
    """
    PolicyTask基类 - 整合策略、任务、环境管理

    子类需要实现:
    1. 生命周期方法: initialize, start, stop, cleanup
    2. 业务逻辑方法
    3. 使用装饰器暴露外部接口
    """

    def __init__(self, config_path: str):
        """
        初始化PolicyTask

        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self._running = False
        self._initialized = False

        # 存储暴露的API方法
        self._exposed_apis: list[dict[str, Any]] = []
        self._collect_exposed_apis()

    def _load_config(self, config_path: str) -> dict[str, Any]:
        """加载配置文件"""
        try:
            config_file = Path(config_path)
            if not config_file.exists():
                raise FileNotFoundError(f"配置文件不存在: {config_path}")

            with open(config_file, encoding="utf-8") as f:
                config = yaml.safe_load(f)

            return config or {}

        except Exception as e:
            print(f"加载配置文件失败 {config_path}: {e}")
            return {}

    def _collect_exposed_apis(self):
        """收集被装饰器标记的API方法"""
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if hasattr(attr, "_exposed_api"):
                api_info = attr._exposed_api
                api_info["method_name"] = attr_name
                api_info["handler"] = attr
                self._exposed_apis.append(api_info)

    def get_exposed_apis(self) -> list[dict[str, Any]]:
        """获取所有暴露的API"""
        return self._exposed_apis.copy()

    @abstractmethod
    def initialize(self) -> bool:
        """
        初始化PolicyTask

        在这里实现:
        1. Policy初始化
        2. 环境配置和创建
        3. 其他资源初始化

        Returns:
            bool: 初始化是否成功
        """
        pass

    @abstractmethod
    def start(self):
        """
        启动PolicyTask

        在这里实现:
        1. 启动主要的执行循环
        2. 开始处理任务
        """
        pass

    @abstractmethod
    def stop(self):
        """
        停止PolicyTask

        在这里实现:
        1. 停止执行循环
        2. 保存状态
        """
        pass

    @abstractmethod
    def reset(self):
        """
        重置PolicyTask

        在这里实现:
        1. 停止任务
        2. 重置机器人位姿
        """

        pass

    @abstractmethod
    def cleanup(self):
        """
        清理资源

        在这里实现:
        1. 关闭环境连接
        2. 释放资源
        3. 清理临时文件等
        """
        pass

    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running

    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._initialized

    def get_status(self) -> dict[str, Any]:
        """
        获取状态信息

        Returns:
            Dict: 状态信息
        """
        return {
            "initialized": self._initialized,
            "running": self._running,
            "config_path": self.config_path,
            "class_name": self.__class__.__name__,
        }
