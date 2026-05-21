# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
base strategy module - defines the basic interface for all data sources
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseLoader(ABC):
    """抽象基类，定义获取 BaseLoader 的策略接口"""

    def __init__(self, config: dict[str, Any]):
        """
        初始化基础策略

        Args:
            config: 策略配置
        """
        self.config = config

    @abstractmethod
    def setup(self):
        """设置数据源"""
        pass

    @abstractmethod
    def get_payload(self, filter_args: dict | None = None) -> dict[str, Any]:
        """获取数据"""
        pass

    @abstractmethod
    def can_handle(self, data_source_config: dict[str, Any]) -> bool:
        """检查是否可以处理指定的数据源配置"""
        pass
    
    @abstractmethod
    def shutdown(self):
        """关闭策略资源"""
        pass
