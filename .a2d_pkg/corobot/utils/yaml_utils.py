from typing import Any

import yaml

from corobot.utils.log_setting import CoLogger as logger


def load_yaml(path: str) -> dict[str, Any]:
    """Load YAML configuration file"""
    try:
        with open(path, encoding="utf-8") as f:
            config = yaml.load(f, Loader=yaml.FullLoader) or {}
            logger.info(f"Successfully loaded configuration file: {path}")
            return config
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {path}")
        raise
    except yaml.YAMLError as e:
        logger.error(f"Configuration file format error: {e}")
        raise
