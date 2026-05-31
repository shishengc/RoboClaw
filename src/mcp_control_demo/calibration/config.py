from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
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
    t_head_pitch_camera: np.ndarray | None = None
    transform_mode: str = "dynamic_fk"
    urdf_path: str | None = None
    intrinsics: dict[str, Any] | None = None
    camera_approach_axis: np.ndarray = field(default_factory=lambda: np.asarray([0.0, 0.0, -1.0]))
    camera_lift_axis: np.ndarray = field(default_factory=lambda: np.asarray([0.0, -1.0, 0.0]))
    camera_place_down_axis: np.ndarray = field(default_factory=lambda: np.asarray([0.0, 1.0, 0.0]))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CalibrationConfig":
        source = _calibration_section(data)
        extrinsics = source.get("extrinsics") or {}
        if not isinstance(extrinsics, dict):
            extrinsics = {}
        source_transform = str(extrinsics.get("source_transform") or "T_head_pitch_camera")
        t_head_pitch_camera = _first_present(
            source,
            "T_head_pitch_camera",
            (extrinsics, source_transform),
            (extrinsics, "T_head_pitch_camera"),
        )
        urdf_path = _first_present(source, "urdf_path", (extrinsics, "urdf_source"))
        transform_mode = str(source.get("transform_mode") or "dynamic_fk")
        if transform_mode != "dynamic_fk":
            raise ValueError(f"mcp_control only supports transform_mode='dynamic_fk', got {transform_mode!r}")
        if t_head_pitch_camera is None:
            raise ValueError("dynamic_fk calibration requires fixed T_head_pitch_camera")
        return cls(
            camera_frame=str(source.get("camera_frame") or "head_camera_optical"),
            exec_frame=str(source.get("exec_frame") or "base_link"),
            t_head_pitch_camera=None if t_head_pitch_camera is None else as_matrix4(t_head_pitch_camera),
            transform_mode=transform_mode,
            urdf_path=None if urdf_path is None else str(urdf_path),
            intrinsics=source.get("intrinsics"),
            camera_approach_axis=normalize_vector(source.get("camera_approach_axis", [0.0, 0.0, -1.0])),
            camera_lift_axis=normalize_vector(source.get("camera_lift_axis", [0.0, -1.0, 0.0])),
            camera_place_down_axis=normalize_vector(source.get("camera_place_down_axis", [0.0, 1.0, 0.0])),
        )

    @classmethod
    def identity_for_tests(cls) -> "CalibrationConfig":
        return cls(t_exec_camera=np.eye(4, dtype=np.float64), t_head_pitch_camera=np.eye(4, dtype=np.float64))

    def require_camera_frame(self, camera_frame: str | None) -> None:
        requested = camera_frame or self.camera_frame
        if requested != self.camera_frame:
            raise ValueError(f"unsupported camera_frame={requested!r}; configured frame is {self.camera_frame!r}")

    def require_transform(self) -> np.ndarray:
        if self.t_exec_camera is None:
            raise ValueError("missing runtime T_exec_camera; dynamic_fk transform must be computed from observation")
        return self.t_exec_camera

    def with_t_exec_camera(self, t_exec_camera: np.ndarray) -> "CalibrationConfig":
        return replace(self, t_exec_camera=as_matrix4(t_exec_camera))

    @property
    def can_provide_transform(self) -> bool:
        return self.t_exec_camera is not None or (
            self.transform_mode == "dynamic_fk"
            and self.t_head_pitch_camera is not None
        )

    @property
    def has_dynamic_fk(self) -> bool:
        return (
            self.transform_mode == "dynamic_fk"
            and self.t_head_pitch_camera is not None
        )

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


def _calibration_section(data: dict[str, Any]) -> dict[str, Any]:
    section = data.get("mcp_control")
    if isinstance(section, dict):
        return section
    raise ValueError("RuleControlTask config must contain an mcp_control section")


def _first_present(mapping: dict[str, Any], *keys: Any) -> Any:
    for key in keys:
        if isinstance(key, str):
            if key in mapping and mapping[key] is not None:
                return mapping[key]
        else:
            nested_mapping, nested_key = key
            if (
                isinstance(nested_mapping, dict)
                and nested_key in nested_mapping
                and nested_mapping[nested_key] is not None
            ):
                return nested_mapping[nested_key]
    return None
