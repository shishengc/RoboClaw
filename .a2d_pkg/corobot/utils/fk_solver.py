# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

import sysconfig
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation as R

from corobot.utils.kinematics import Kinematics
from corobot.utils.log_setting import CoLogger as logger


def _find_urdf_solver_dir() -> Path:
    """Locate urdf_solver directory.

    Priority:
    1) Cache directory ~/.cache/agibot/corobot/urdf_solver (if initialized by corobot_init)
    2) Project root "urdf_solver" (editable/development installs)
    3) Package resource corobot/urdf_solver (if present)
    4) System data dir: <sysconfig data>/corobot/urdf_solver (if configured via data-files)
    """

    candidates: list[Path] = []

    # 1) Cache directory (if initialized by corobot_init)
    cache_dir = Path.home() / ".cache" / "agibot" / "corobot" / "urdf_solver"
    candidates.append(cache_dir)

    # 2) repo root: ../../.. from this file -> project root
    this_file = Path(__file__).resolve()
    try:
        project_root = this_file.parents[3]
        candidates.append(project_root / "urdf_solver")
    except Exception:
        pass

    # 3) inside package resources (corobot/urdf_solver)
    try:
        from importlib.resources import files as pkg_files

        pkg_urdf = pkg_files("corobot").joinpath("urdf_solver")
        candidates.append(Path(str(pkg_urdf)))
    except Exception:
        pass

    # 4) installed data files location (if pyproject configured with tool.setuptools.data-files)
    try:
        data_dir = Path(sysconfig.get_paths()["data"]) / "corobot" / "urdf_solver"
        candidates.append(data_dir)
    except Exception:
        pass

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    raise FileNotFoundError("未找到urdf_solver目录")


def xyzquat_to_xyzrpy(xyzquat):
    """将xyz+四元数格式转换为xyz+rpy格式"""
    xyz = xyzquat[:3]
    rpy = R.from_quat(xyzquat[3:], scalar_first=True).as_euler("xyz", degrees=False)
    xyzrpy = np.concatenate([xyz, rpy])
    return xyzrpy


def xyzrpy_to_xyzquat(xyzrpy):
    """将xyz+rpy格式转换为xyz+四元数格式"""
    xyz = xyzrpy[:3]
    quat = R.from_euler("xyz", xyzrpy[3:]).as_quat(scalar_first=True)
    xyzquat = np.concatenate([xyz, quat])
    return xyzquat


class FKTransform:
    def __init__(self):
        urdf_solver_dir = _find_urdf_solver_dir()
        urdf_path = str(urdf_solver_dir / "A2D_viz.urdf")
        self.fk = Kinematics(urdf_path)

    def get_eef_pos(self, waist_pos, head_pos, left_joints, right_joints, base_link="base_link"):
        """计算末端执行器位置

        Args:
            waist_pos: 腰部关节位置 [yaw, lift]
            head_pos: 头部关节位置 [yaw, pitch]
            left_joints: 左臂关节角度 [7个关节]
            right_joints: 右臂关节角度 [7个关节]
            base_link: 基座链接名称

        Returns:
            list: [left_pose, right_pose] 每个pose包含position和orientation
        """
        if base_link == "base_link":
            left_pose, right_pose = self.fk.compute_arm_fk(left_joints + right_joints, waist_pos[0], waist_pos[1])
        elif base_link == "arm_base_link":
            left_pose, right_pose = self.fk.compute_arm_fk_wrt_arm_base(
                left_joints + right_joints, waist_pos[0], waist_pos[1]
            )
        else:
            logger.error(f"Invalid base_link for fk solver: {base_link}")
            return None
        # 获取末端执行器位姿

        left_pose = {
            "position": left_pose[0:3].tolist(),
            "orientation": left_pose[3:].tolist(),  # xyzw格式
        }

        right_pose = {
            "position": right_pose[0:3].tolist(),
            "orientation": right_pose[3:].tolist(),  # xyzw格式
        }
        return {"left_arm": left_pose, "right_arm": right_pose}
