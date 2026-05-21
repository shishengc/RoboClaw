# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

try:
    # Python 3.8+ uses importlib.metadata in stdlib; backport not needed for 3.10+
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("corobot")
except Exception:  # pragma: no cover - fallback during editable installs without metadata
    __version__ = "0.0.0"

from . import (
    app,
    cli,
    dataloader,
    envs,
    motion_control,
    policy_tasks,
    protocol,
    replay,
    robots,
    transport,
    utils,
)
from .envs.g01_env import G01Env

__all__ = [
    "dataloader",
    "envs",
    "app",
    "motion_control",
    "policy_tasks",
    "protocol",
    "replay",
    "robots",
    "transport",
    "utils",
    "G01Env",
    "cli",
]
