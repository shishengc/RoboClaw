# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
管理模块 - 用于管理和查找合适的数据源策略
"""

from typing import Any

from corobot.dataloader.data_source.base_loader import BaseLoader


class DataSourceManager:
    """管理和查找数据源策略"""

    def __init__(self, config: dict[str, Any]):
        """
        初始化策略管理器

        Args:
            config: 配置字典
        """
        self.config = config

    def get_data_source(self, data_source_config: dict[str, Any]) -> BaseLoader:
        """
        获取适合数据源（懒惰导入模式）

        Args:
            data_source_config: 数据源配置

        Returns:
            策略实例

        Raises:
            ValueError: 如果找不到适合的策略
        """
        if data_source_config.get("ALIGNED_ROBOT", {}).get("enabled", False):
            from corobot.dataloader.data_source.aligned_robot_loader import AlignedRobotLoader

            strategy = AlignedRobotLoader(self.config)
            strategy.setup()
            return strategy

        raise ValueError(f"找不到适合的数据源: {data_source_config}")
