# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
数据加载器工厂模块 - 用于创建和管理数据加载器实例
"""


class DataLoaderFactory:
    """
    数据加载器工厂类
    用于创建数据加载器实例
    """

    @staticmethod
    def create_data_loader(dataloader_config):
        """
        创建数据加载器实例

        Args:
            dataloader_config: 数据加载器配置

        Returns:
            数据加载器实例
        """
        # 使用新的数据加载系统
        try:
            from corobot.dataloader.dataloader import DataLoader

            return DataLoader(dataloader_config)
        except (ImportError, Exception) as e:
            raise ValueError(f"无法创建数据加载器: {e}") from e


def get_supported_class():
    supported_class = ["H5DataLoader", "A2DReal", "A2DSim", "MockRobot", "REPLAY"]
    return supported_class
