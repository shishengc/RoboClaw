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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CalibrationConfig":
        return cls(
            camera_frame=str(data.get("camera_frame") or "head_camera_optical"),
            exec_frame=str(data.get("exec_frame") or "base_link"),
            t_exec_camera=None if data.get("T_exec_camera") is None else as_matrix4(data["T_exec_camera"]),
            intrinsics=data.get("intrinsics"),
            camera_approach_axis=normalize_vector(data.get("camera_approach_axis", [0.0, 0.0, -1.0])),
            camera_lift_axis=normalize_vector(data.get("camera_lift_axis", [0.0, -1.0, 0.0])),
            camera_place_down_axis=normalize_vector(data.get("camera_place_down_axis", [0.0, 1.0, 0.0])),
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
