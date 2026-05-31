#!/usr/bin/env python3
"""Generate the CoRobot RuleControlTask config from camera params and URDF.

The generated runtime config keeps only fixed calibration values in YAML:
camera intrinsics, the fixed head-pitch<-camera extrinsic, and camera-frame
control axes.

At runtime RuleControlTask uses the current head/waist joint states:

    T_exec_camera(q) = T_base_head_pitch(q) * T_head_pitch_camera

The script still computes a reference T_exec_camera at the provided reset pose
to verify equivalence, but that matrix is not written to config. The input
camera extrinsic is expected to map camera coordinates into the head pitch frame
unless --invert-extrinsic is used.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.spatial.transform import Rotation as R


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URDF = REPO_ROOT / ".a2d_pkg/corobot/urdf_solver/A2D_viz.urdf"
DEFAULT_TASK_CONFIG = REPO_ROOT / ".a2d_pkg/corobot/config/rule_control_task_config.yml"
DEFAULT_HEAD = [0.0, 0.43633230555555524]
DEFAULT_WAIST = [0.8901176920412174, 0.4598677062988281]
DEFAULT_APPROACH_AXIS = [0.0, 0.0, -1.0]
DEFAULT_LIFT_AXIS = [0.0, -1.0, 0.0]
DEFAULT_PLACE_DOWN_AXIS = [0.0, 1.0, 0.0]
DEFAULT_TAG_FAMILY = "tag25h9"
DEFAULT_TAG_SIZE_M = 0.019


def main() -> int:
    args = parse_args()

    intrinsics_source = args.intrinsics.resolve()
    extrinsics_source = args.extrinsics.resolve()
    urdf_path = args.urdf.resolve()
    task_config_path = args.task_config.resolve()

    intrinsics = parse_intrinsics(
        load_mapping(intrinsics_source),
        camera_info_path=args.camera_info.resolve() if args.camera_info else None,
        camera_name=args.camera_name,
        image_size=args.image_size,
        camera_model=args.camera_model,
    )

    t_head_camera = parse_extrinsic(
        load_mapping(extrinsics_source),
        transform_name=args.source_transform_name,
    )
    if args.invert_extrinsic:
        t_head_camera = np.linalg.inv(t_head_camera)

    t_base_head, _fk_frame_name = compute_head_fk_transform(
        urdf_path=urdf_path,
        head_positions=args.head,
        waist_positions=args.waist,
    )
    t_exec_camera = t_base_head @ t_head_camera

    mcp_control = build_mcp_control_config(
        args=args,
        intrinsics=intrinsics,
        t_head_camera=t_head_camera,
    )
    task_config = build_rule_control_task_config(args=args, mcp_control=mcp_control)
    write_yaml(task_config_path, task_config, backup=not args.no_backup, header=task_config_header())

    print(f"wrote RuleControlTask config: {task_config_path}")
    print("reference T_exec_camera at provided head/waist pose:")
    for row in matrix_to_list(t_exec_camera, args.precision):
        print("  " + json.dumps(row))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate CoRobot RuleControlTask dynamic-FK config from new intrinsics, extrinsics, and URDF.",
    )
    parser.add_argument("--intrinsics", type=Path, required=True, help="Camera intrinsics JSON/YAML path.")
    parser.add_argument("--extrinsics", type=Path, required=True, help="Head camera extrinsics JSON/YAML path.")
    parser.add_argument("--urdf", type=Path, default=DEFAULT_URDF, help="URDF used by CoRobot/Pinocchio FK.")
    parser.add_argument(
        "--task-config",
        type=Path,
        default=DEFAULT_TASK_CONFIG,
        help="Output RuleControlTask config YAML path.",
    )
    parser.add_argument("--camera-info", type=Path, default=None, help="Optional Realsense camera info JSON/YAML.")
    parser.add_argument("--camera-name", default="head", help="Camera name stored in calibration/perception config.")
    parser.add_argument("--camera-model", default=None, help="Camera model override.")
    parser.add_argument("--camera-frame", default="head_camera_optical", help="External camera frame name.")
    parser.add_argument("--exec-frame", default="base_link", help="mcp_control execution frame.")
    parser.add_argument(
        "--source-parent-frame",
        default="head_pitch_link",
        help="Frame that the input extrinsic maps into.",
    )
    parser.add_argument(
        "--source-transform-name",
        default="T_head_pitch_camera",
        help="Name recorded for the input parent<-camera transform.",
    )
    parser.add_argument(
        "--invert-extrinsic",
        action="store_true",
        help="Use this if the input extrinsic is camera<-head instead of head<-camera.",
    )
    parser.add_argument("--head", nargs=2, type=float, default=DEFAULT_HEAD, metavar=("YAW", "PITCH"))
    parser.add_argument("--waist", nargs=2, type=float, default=DEFAULT_WAIST, metavar=("PITCH", "LIFT"))
    parser.add_argument("--image-size", nargs=2, type=int, default=None, metavar=("WIDTH", "HEIGHT"))
    parser.add_argument("--tag-family", default=DEFAULT_TAG_FAMILY, help="perception.tag_family value.")
    parser.add_argument("--tag-size-m", type=float, default=DEFAULT_TAG_SIZE_M, help="perception.tag_size_m value.")
    parser.add_argument("--stale-after-s", type=float, default=1.0, help="perception.stale_after_s value.")
    parser.add_argument(
        "--grippers",
        nargs=2,
        type=float,
        default=[0.0, 0.0],
        metavar=("LEFT", "RIGHT"),
        help="Reset gripper positions written under reset_pose.target_grippers_positions.",
    )
    parser.add_argument(
        "--no-reset-on-initialize",
        action="store_true",
        help="Write reset_on_initialize: false instead of the project default true.",
    )
    parser.add_argument("--log-level", default="INFO", help="logging.level value.")
    parser.add_argument("--camera-approach-axis", nargs=3, type=float, default=DEFAULT_APPROACH_AXIS)
    parser.add_argument("--camera-lift-axis", nargs=3, type=float, default=DEFAULT_LIFT_AXIS)
    parser.add_argument("--camera-place-down-axis", nargs=3, type=float, default=DEFAULT_PLACE_DOWN_AXIS)
    parser.add_argument("--precision", type=int, default=9, help="Decimal places for generated transforms.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak files before overwriting.")
    return parser.parse_args()


def load_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"expected a mapping in {path}")
    return data


def parse_intrinsics(
    data: dict[str, Any],
    *,
    camera_info_path: Path | None,
    camera_name: str,
    image_size: list[int] | None,
    camera_model: str | None,
) -> dict[str, Any]:
    node = data.get("intrinsic") if isinstance(data.get("intrinsic"), dict) else data
    matrix = camera_matrix_from_intrinsics(data, node)
    fx = float(matrix[0, 0])
    fy = float(matrix[1, 1])
    cx = float(matrix[0, 2])
    cy = float(matrix[1, 2])

    camera_info = load_camera_info(camera_info_path, camera_name) if camera_info_path else {}
    width, height = resolve_image_size(data, camera_info, image_size)
    model = camera_model or str(camera_info.get("model") or data.get("camera_model") or "")
    distortion, distortion_model, _source_order = distortion_from_intrinsics(data, node)

    result: dict[str, Any] = {
        "camera_name": camera_name,
    }
    if model:
        result["camera_model"] = model
    if width is not None and height is not None:
        result["image_size"] = {"width": int(width), "height": int(height)}

    result.update(
        {
            "fx": fx,
            "fy": fy,
            "cx": cx,
            "cy": cy,
            "camera_matrix": {
                "fx": fx,
                "fy": fy,
                "cx": cx,
                "cy": cy,
                "data": matrix_to_list(matrix, precision=15),
            },
        }
    )
    if distortion is not None:
        dist: dict[str, Any] = {
            "model": distortion_model,
            "opencv_order": "k1,k2,p1,p2,k3",
            "data": [float(v) for v in distortion],
        }
        result["distortion_coefficients"] = dist
    return result


def camera_matrix_from_intrinsics(data: dict[str, Any], node: dict[str, Any]) -> np.ndarray:
    camera_matrix = data.get("camera_matrix") or node.get("camera_matrix") or data.get("K") or node.get("K")
    matrix = matrix_from_camera_matrix_field(camera_matrix)
    if matrix is not None:
        return matrix

    fx = first_number(node, "fx", "f_x")
    fy = first_number(node, "fy", "f_y")
    cx = first_number(node, "cx", "ppx", "c_x")
    cy = first_number(node, "cy", "ppy", "c_y")
    if None in (fx, fy, cx, cy):
        raise ValueError("intrinsics must provide camera_matrix/K or fx, fy, cx/ppx, cy/ppy")
    return np.asarray([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=np.float64)


def matrix_from_camera_matrix_field(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    if isinstance(value, dict):
        if value.get("data") is not None:
            return np.asarray(value["data"], dtype=np.float64).reshape(3, 3)
        if all(key in value for key in ("fx", "fy", "cx", "cy")):
            return np.asarray(
                [[value["fx"], 0.0, value["cx"]], [0.0, value["fy"], value["cy"]], [0.0, 0.0, 1.0]],
                dtype=np.float64,
            )
    return np.asarray(value, dtype=np.float64).reshape(3, 3)


def distortion_from_intrinsics(data: dict[str, Any], node: dict[str, Any]) -> tuple[list[float] | None, str, str | None]:
    value = (
        data.get("distortion_coefficients")
        or node.get("distortion_coefficients")
        or data.get("D")
        or node.get("D")
        or data.get("dist")
        or node.get("dist")
    )
    model = str(node.get("distortion_model") or data.get("distortion_model") or "plumb bob")
    if isinstance(value, dict):
        model = str(value.get("model") or model)
        value = value.get("data")
    if value is not None:
        values = [float(v) for v in np.asarray(value, dtype=np.float64).reshape(-1).tolist()]
        if len(values) == 4:
            values.append(0.0)
        return values[:5], model, None

    keys = ("k1", "k2", "k3", "p1", "p2")
    if all(key in node for key in keys):
        return (
            [float(node["k1"]), float(node["k2"]), float(node["p1"]), float(node["p2"]), float(node["k3"])],
            model,
            "input stores k1,k2,k3,p1,p2",
        )
    return None, model, None


def resolve_image_size(
    data: dict[str, Any],
    camera_info: dict[str, Any],
    image_size: list[int] | None,
) -> tuple[int | None, int | None]:
    if image_size:
        return int(image_size[0]), int(image_size[1])
    raw = data.get("image_size") or data.get("resolution") or {}
    if isinstance(raw, dict) and raw.get("width") is not None and raw.get("height") is not None:
        return int(raw["width"]), int(raw["height"])
    if camera_info.get("width") is not None and camera_info.get("height") is not None:
        return int(camera_info["width"]), int(camera_info["height"])
    return None, None


def load_camera_info(path: Path | None, camera_name: str) -> dict[str, Any]:
    if path is None:
        return {}
    data = load_mapping(path)
    if data.get("name") == camera_name:
        return data
    for key, value in data.items():
        if key == camera_name and isinstance(value, dict):
            return value
        if isinstance(value, dict) and value.get("name") == camera_name:
            return value
    return {}


def parse_extrinsic(data: dict[str, Any], *, transform_name: str) -> np.ndarray:
    candidates = [data]
    for key in ("extrinsic", "extrinsics", "transform"):
        if isinstance(data.get(key), dict):
            candidates.append(data[key])

    for candidate in candidates:
        for key in (transform_name, "T_head_pitch_camera", "T_parent_camera", "T_camera", "T", "matrix"):
            if candidate.get(key) is not None:
                return np.asarray(candidate[key], dtype=np.float64).reshape(4, 4)

    for candidate in candidates:
        rotation = candidate.get("rotation_matrix") or candidate.get("R") or candidate.get("rotation")
        translation = (
            candidate.get("translation_vector")
            or candidate.get("translation")
            or candidate.get("t")
            or candidate.get("xyz")
        )
        if rotation is not None and translation is not None:
            result = np.eye(4, dtype=np.float64)
            result[:3, :3] = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
            result[:3, 3] = np.asarray(translation, dtype=np.float64).reshape(3)
            return result

    raise ValueError("extrinsics must provide a 4x4 transform or rotation_matrix + translation_vector")


def compute_head_fk_transform(
    *,
    urdf_path: Path,
    head_positions: list[float],
    waist_positions: list[float],
) -> tuple[np.ndarray, str]:
    sys.path.insert(0, str(REPO_ROOT / ".a2d_pkg"))
    from corobot.utils.kinematics import Kinematics

    with redirect_stdout(StringIO()):
        kinematics = Kinematics(str(urdf_path))
    xyzquat = kinematics.compute_head_fk(
        float(head_positions[0]),
        float(head_positions[1]),
        float(waist_positions[0]),
        float(waist_positions[1]),
    )
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = R.from_quat(xyzquat[3:]).as_matrix()
    transform[:3, 3] = np.asarray(xyzquat[:3], dtype=np.float64)
    frame_name = kinematics.model.frames[kinematics.head_frame_id].name
    return transform, frame_name


def build_mcp_control_config(
    *,
    args: argparse.Namespace,
    intrinsics: dict[str, Any],
    t_head_camera: np.ndarray,
) -> dict[str, Any]:
    return {
        "transform_mode": "dynamic_fk",
        "camera_frame": args.camera_frame,
        "exec_frame": args.exec_frame,
        "intrinsics": intrinsics,
        "extrinsics": {
            "parent_frame": args.source_parent_frame,
            "child_frame": args.camera_frame,
            "T_head_pitch_camera": matrix_to_list(t_head_camera, args.precision),
        },
        "camera_approach_axis": normalize_list(args.camera_approach_axis),
        "camera_lift_axis": normalize_list(args.camera_lift_axis),
        "camera_place_down_axis": normalize_list(args.camera_place_down_axis),
    }


def build_rule_control_task_config(
    *,
    args: argparse.Namespace,
    mcp_control: dict[str, Any],
) -> dict[str, Any]:
    return {
        "mcp_control": mcp_control,
        "perception": {
            "camera_name": args.camera_name,
            "camera_frame": args.camera_frame,
            "tag_family": args.tag_family,
            "tag_size_m": float(args.tag_size_m),
            "stale_after_s": float(args.stale_after_s),
        },
        "reset_on_initialize": not args.no_reset_on_initialize,
        "reset_pose": {
            "target_grippers_positions": [float(value) for value in args.grippers],
            "target_head_positions": [float(value) for value in args.head],
            "target_waist_positions": [float(value) for value in args.waist],
        },
        "environment": {
            "dataloader": {
                "data_source": {
                    "ALIGNED_ROBOT": {
                        "enabled": True,
                    },
                },
                "enabled_streams": {
                    "camera": {
                        "head": True,
                        "hand_left": True,
                        "hand_right": True,
                        "head_depth": False,
                        "hand_left_depth": False,
                        "hand_right_depth": False,
                    },
                },
            },
            "motion_controller": {
                "enabled": True,
                "execution_timeout_s": 2.0,
            },
        },
        "logging": {
            "level": args.log_level,
        },
    }


def write_yaml(path: Path, data: dict[str, Any], *, backup: bool, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)
    path.write_text(header + text, encoding="utf-8")


def task_config_header() -> str:
    return (
        "# Generated by scripts/generate_mcp_control_calibration.py.\n"
        "# RuleControlTask dynamic-FK control config.\n"
        "# Runtime transform convention:\n"
        "#   T_exec_camera(q) = T_base_head_pitch(q) * T_head_pitch_camera\n"
        "# T_head_pitch_camera is fixed camera installation calibration; q comes from current head/waist observation.\n"
    )


def matrix_to_list(matrix: Any, precision: int) -> list[list[float]]:
    array = np.asarray(matrix, dtype=np.float64)
    return [[round(float(value), precision) for value in row] for row in array.tolist()]


def normalize_list(values: list[float]) -> list[float]:
    array = np.asarray(values, dtype=np.float64).reshape(3)
    norm = float(np.linalg.norm(array))
    if norm <= 1e-12:
        raise ValueError("axis vector cannot be zero")
    return [round(float(value), 12) for value in (array / norm).tolist()]


def first_number(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if mapping.get(key) is not None:
            return float(mapping[key])
    return None


if __name__ == "__main__":
    raise SystemExit(main())
