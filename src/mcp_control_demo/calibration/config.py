from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .transforms import as_matrix4, invert_transform, normalize_vector, transform_point


@dataclass(frozen=True)
class CalibrationConfig:
    """Runtime calibration used to plan in camera frame and execute in CoRobot frame."""

    camera_frame: str = "head_camera_optical"
    exec_frame: str = "base_link"
    t_exec_camera: np.ndarray | None = None
    intrinsics: dict[str, Any] | None = None
    camera_approach_axis: np.ndarray = field(default_factory=lambda: np.asarray([0.0, 0.0, -1.0]))
    camera_lift_axis: np.ndarray = field(default_factory=lambda: np.asarray([0.0, -1.0, 0.0]))
    camera_place_down_axis: np.ndarray = field(default_factory=lambda: np.asarray([0.0, 1.0, 0.0]))
    tag_grasp_offsets_m: dict[int, np.ndarray] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CalibrationConfig":
        tag_offsets: dict[int, np.ndarray] = {}
        for raw_id, raw_cfg in (data.get("tag_offsets") or {}).items():
            tag_id = int(raw_id)
            if isinstance(raw_cfg, dict):
                offset = raw_cfg.get("grasp_point_tag_m") or raw_cfg.get("grasp_offset_m") or [0.0, 0.0, 0.0]
            else:
                offset = raw_cfg
            tag_offsets[tag_id] = np.asarray(offset, dtype=np.float64).reshape(3)

        return cls(
            camera_frame=str(data.get("camera_frame") or "head_camera_optical"),
            exec_frame=str(data.get("exec_frame") or "base_link"),
            t_exec_camera=None if data.get("T_exec_camera") is None else as_matrix4(data["T_exec_camera"]),
            intrinsics=data.get("intrinsics"),
            camera_approach_axis=normalize_vector(data.get("camera_approach_axis", [0.0, 0.0, -1.0])),
            camera_lift_axis=normalize_vector(data.get("camera_lift_axis", [0.0, -1.0, 0.0])),
            camera_place_down_axis=normalize_vector(data.get("camera_place_down_axis", [0.0, 1.0, 0.0])),
            tag_grasp_offsets_m=tag_offsets,
        )

    @classmethod
    def identity_for_tests(cls) -> "CalibrationConfig":
        return cls(t_exec_camera=np.eye(4, dtype=np.float64))

    def require_camera_frame(self, camera_frame: str | None) -> None:
        requested = camera_frame or self.camera_frame
        if requested != self.camera_frame:
            raise ValueError(f"unsupported camera_frame={requested!r}; configured frame is {self.camera_frame!r}")

    def require_transform(self) -> np.ndarray:
        if self.t_exec_camera is None:
            raise ValueError("missing T_exec_camera calibration; refusing to execute camera-frame control")
        return self.t_exec_camera

    def camera_to_exec_point(self, point_camera_m: list[float] | np.ndarray, camera_frame: str | None = None) -> np.ndarray:
        self.require_camera_frame(camera_frame)
        return transform_point(self.require_transform(), point_camera_m)

    def exec_to_camera_point(self, point_exec_m: list[float] | np.ndarray, camera_frame: str | None = None) -> np.ndarray:
        self.require_camera_frame(camera_frame)
        return transform_point(invert_transform(self.require_transform()), point_exec_m)

    def grasp_point_from_tag(self, tag_id: int, tag_pose: dict[str, Any]) -> np.ndarray:
        tag_position = _tag_position_camera(tag_pose)
        offset = self.tag_grasp_offsets_m.get(int(tag_id))
        if offset is None:
            return tag_position

        rotation = tag_pose.get("rotation_matrix")
        if rotation is None:
            return tag_position + offset
        return tag_position + np.asarray(rotation, dtype=np.float64).reshape(3, 3) @ offset


def _tag_position_camera(tag_pose: dict[str, Any]) -> np.ndarray:
    for key in ("position_camera_m", "translation_m", "target_position_camera_m"):
        if key in tag_pose and tag_pose[key] is not None:
            return np.asarray(tag_pose[key], dtype=np.float64).reshape(3)
    camera_pose = tag_pose.get("camera_pose") or {}
    if camera_pose.get("position_m") is not None:
        return np.asarray(camera_pose["position_m"], dtype=np.float64).reshape(3)
    raise ValueError("tag pose does not contain a camera-frame position")


def load_calibration_config(path: str | Path) -> CalibrationConfig:
    path = Path(path).expanduser()
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"calibration file must contain a mapping: {path}")
    return CalibrationConfig.from_dict(data)
