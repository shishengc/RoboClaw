# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

from corobot.transport.websocket_app import WebSocketApp
from corobot.utils.dds_setting import dds_env_set
from corobot.utils.log_setting import CoLogger as logger
from corobot.utils.yaml_utils import load_yaml


class RemoteEnv:
    """统一应用入口，根据配置自动选择协议"""

    def __init__(self, config_path: str):
        """
        初始化统一应用

        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self.config = None
        self.app_instance = None

        # 加载配置
        self._load_config()

        # 设置日志
        logger.log_level_set(self.config)

        # 设置机器人环境
        dds_env_set()

        # 创建应用实例
        self._create_app_instance()

    def _load_config(self) -> None:
        """加载配置文件"""
        # 确保路径是绝对路径
        if not os.path.isabs(self.config_path):
            self.config_path = os.path.abspath(self.config_path)

        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")

        self.config = load_yaml(self.config_path)

    def _create_app_instance(self) -> None:
        """根据配置创建相应的应用实例"""
        server_type = self.config.get("server_type", "websocket").lower()

        logger.info(f"创建{server_type.upper()}应用实例")
        logger.info(f"配置文件: {self.config_path}")

        self.app_instance = WebSocketApp(self.config)

    async def start(self) -> None:
        """启动应用"""
        if self.app_instance:
            await self.app_instance.start()

    async def stop(self) -> None:
        """停止应用"""
        if self.app_instance:
            await self.app_instance.stop()

    async def run_forever(self) -> None:
        """运行应用直到收到停止信号"""
        if self.app_instance:
            await self.app_instance.run_forever()

    def run(self) -> None:
        """同步运行应用（阻塞）"""
        try:
            # 使用asyncio.run来管理事件循环
            asyncio.run(self.run_forever())
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在关闭服务器...")
        except Exception as e:
            logger.error(f"服务器运行出错: {e}")
            sys.exit(1)
        finally:
            # 确保所有资源都被清理
            logger.info("应用已关闭")


def signal_handler(signum, frame):
    """处理中断信号"""
    sys.exit(0)


def main():
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="Genie高性能机器人数据处理服务器")
    parser.add_argument(
        "--config", default="~/.cache/agibot/corobot/remote_env.yml", help="配置文件路径"
    )
    args = parser.parse_args()
    args.config = str(Path(args.config).expanduser())
    try:
        # 创建并运行应用
        app = RemoteEnv(args.config)
        app.run()
    except KeyboardInterrupt:
        print("\n收到中断信号，正在关闭服务器...")
    except Exception as e:
        print(f"服务器运行出错: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        print("服务器已关闭")


if __name__ == "__main__":
    main()
