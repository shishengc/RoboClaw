"""
相机配置管理

支持保存和加载不同的相机内参配置，便于在不同场景下使用
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class CameraConfig:
    """相机配置"""
    name: str  # 配置名称
    resolution_width: int  # 分辨率宽
    resolution_height: int  # 分辨率高
    fx: float  # x 方向焦距
    fy: float  # y 方向焦距
    cx: float  # 主点 x
    cy: float  # 主点 y
    description: str = ""  # 配置描述
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CameraConfig":
        return cls(**data)


class CameraConfigManager:
    """相机配置管理器"""
    
    def __init__(self, config_dir: str = "config/camera_configs"):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.configs: Dict[str, CameraConfig] = {}
        self._load_all_configs()
    
    def _load_all_configs(self):
        """加载所有配置文件"""
        for config_file in self.config_dir.glob("*.json"):
            try:
                with open(config_file, 'r') as f:
                    data = json.load(f)
                    config = CameraConfig.from_dict(data)
                    self.configs[config.name] = config
            except Exception as e:
                logger.error(f"无法加载配置文件 {config_file}: {e}")
    
    def save_config(self, config: CameraConfig):
        """保存配置"""
        config_file = self.config_dir / f"{config.name}.json"
        try:
            with open(config_file, 'w') as f:
                json.dump(config.to_dict(), f, indent=2)
            self.configs[config.name] = config
            logger.info(f"配置已保存: {config_file}")
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
    
    def load_config(self, name: str) -> Optional[CameraConfig]:
        """加载指定名称的配置"""
        return self.configs.get(name)
    
    def list_configs(self) -> Dict[str, CameraConfig]:
        """列出所有配置"""
        return self.configs.copy()
    
    def delete_config(self, name: str):
        """删除配置"""
        config_file = self.config_dir / f"{name}.json"
        try:
            if config_file.exists():
                config_file.unlink()
            if name in self.configs:
                del self.configs[name]
            logger.info(f"配置已删除: {name}")
        except Exception as e:
            logger.error(f"删除配置失败: {e}")


# 预定义的相机配置

# RealSense D435
REALSENSE_D435 = CameraConfig(
    name="realsense_d435",
    resolution_width=1280,
    resolution_height=720,
    fx=897.737,
    fy=897.737,
    cx=640.0,
    cy=360.0,
    description="Intel RealSense D435 深度摄像头"
)

# RealSense D455
REALSENSE_D455 = CameraConfig(
    name="realsense_d455",
    resolution_width=1280,
    resolution_height=720,
    fx=916.99,
    fy=916.99,
    cx=640.0,
    cy=360.0,
    description="Intel RealSense D455 深度摄像头"
)

# Azure Kinect
AZURE_KINECT = CameraConfig(
    name="azure_kinect",
    resolution_width=1280,
    resolution_height=720,
    fx=758.62,
    fy=758.62,
    cx=640.0,
    cy=360.0,
    description="Microsoft Azure Kinect DK"
)

# iPhone 12 Pro 超广角摄像头
IPHONE_12_PRO_ULTRA = CameraConfig(
    name="iphone_12_pro_ultra",
    resolution_width=1440,
    resolution_height=1080,
    fx=450.0,  # 约 120 度视场角
    fy=450.0,
    cx=720.0,
    cy=540.0,
    description="iPhone 12 Pro 超广角摄像头 (约120°)"
)

# 标准广角摄像头 (90度)
STANDARD_90_DEG = CameraConfig(
    name="standard_90deg",
    resolution_width=1280,
    resolution_height=960,
    fx=732.2,  # 计算自: w/2 / tan(45°) = 640 / 0.874 ≈ 732
    fy=732.2,
    cx=640.0,
    cy=480.0,
    description="标准 90 度视场角摄像头"
)

# AgiBot G01 机器人头部摄像头（需要根据实际调整）
AGIBOT_G01_HEAD = CameraConfig(
    name="agibot_g01_head",
    resolution_width=1280,
    resolution_height=960,
    fx=920.0,  # 需要实际标定
    fy=920.0,
    cx=640.0,
    cy=480.0,
    description="AgiBot G01 机器人头部摄像头"
)


def create_default_configs():
    """创建默认配置文件"""
    manager = CameraConfigManager()
    
    default_configs = [
        REALSENSE_D435,
        REALSENSE_D455,
        AZURE_KINECT,
        IPHONE_12_PRO_ULTRA,
        STANDARD_90_DEG,
        AGIBOT_G01_HEAD,
    ]
    
    for config in default_configs:
        if config.name not in manager.configs:
            manager.save_config(config)
    
    return manager


if __name__ == "__main__":
    # 创建默认配置
    manager = create_default_configs()
    
    # 列出所有配置
    print("\n已保存的相机配置:")
    print("="*60)
    for name, config in manager.list_configs().items():
        print(f"\n{name}:")
        print(f"  描述: {config.description}")
        print(f"  分辨率: {config.resolution_width}x{config.resolution_height}")
        print(f"  焦距: ({config.fx:.2f}, {config.fy:.2f})")
        print(f"  主点: ({config.cx:.2f}, {config.cy:.2f})")
