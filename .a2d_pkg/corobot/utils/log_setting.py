import os
import sys
from typing import Any

from loguru import logger

# 设置日志目录
log_dir = os.path.expanduser("~/.cache/agibot/corobot/logs")
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
os.environ["DYLOG_log_dir"] = log_dir


class CoLogger:
    @staticmethod
    def log_level_set(config: dict[str, Any]) -> None:
        log_level = config.get("logging", {}).get("level", "INFO")
        os.environ["LOGURU_LEVEL"] = log_level
        logger.remove()
        logger.add(sys.stderr, level=os.environ.get("LOGURU_LEVEL", "INFO"))

    @staticmethod
    def error(message: str) -> None:
        logger.opt(depth=1).error(message)

    @staticmethod
    def warning(message: str) -> None:
        logger.opt(depth=1).warning(message)

    @staticmethod
    def info(message: str) -> None:
        logger.opt(depth=1).info(message)

    @staticmethod
    def debug(message: str) -> None:
        logger.opt(depth=1).debug(message)
