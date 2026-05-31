from __future__ import annotations

import base64
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation as R

from corobot.envs.g01_env import G01Env
from corobot.policy_tasks.policy_task_base import PolicyTaskBase
from corobot.protocol.protocol_schemas import Action
from corobot.utils.api_decorators import expose_api
from corobot.utils.log_setting import CoLogger as logger

from mcp_control_demo.calibration import CalibrationConfig
from mcp_control_demo.control import (
    GRIPPER_CENTER_OFFSET_LINK7_M,
    build_gripper_action,
    build_lift_eef_action,
    build_move_eef_action,
    build_place_down_sequence,
    wrist_to_gripper_center_exec,
)
from mcp_control_demo.control.timing import validate_no_control_hz
from mcp_control_demo.perception import AprilTagPerceptionService


DEFAULT_RESET_POSE = {
    "target_grippers_positions": [1.0, 1.0],
    "target_arm_joint_positions": [
        -0.7776767611503601,
        0.6110292077064514,
        0.0,
        -1.2839710712432861,
        0.7304046154022217,
        1.4953951835632324,
        -0.18760496377944946,
        0.7775720357894897,
        -0.6110292077064514,
        0.0,
        1.284005880355835,
        -0.7304570078849792,
        -1.4953428506851196,
        0.18762239813804626,
    ],
    "target_head_positions": [0.0, 0.43633230555555524],
    "target_waist_positions": [0.8901176920412174, 0.4598677062988281],
}

RESET_CONFIG_KEY_MAP = {
    "target_grippers_positions": "target_grippers_positions",
    "target_arm_joint_positions": "target_arm_joint_positions",
    "target_head_positions": "target_head_positions",
    "target_waist_positions": "target_waist_positions",
    "init_grippers_positions": "target_grippers_positions",
    "init_arm_joint_positions": "target_arm_joint_positions",
    "init_head_positions": "target_head_positions",
    "init_waist_positions": "target_waist_positions",
}

_FK_SOLVER = None


class RuleControlTask(PolicyTaskBase):
    """CoRobot-native PolicyTask exposing deterministic rule-control skill APIs."""

    def __init__(self, config_path: str):
        super().__init__(config_path)
        self._env: G01Env | None = None
        self._calibration: CalibrationConfig | None = None
        self._perception: AprilTagPerceptionService | None = None
        self._reset_pose = _copy_reset_pose(DEFAULT_RESET_POSE)
        self._reset_on_initialize = False
        self._kinematics = None
        self._kinematics_urdf_path: str | None = None

    def initialize(self) -> bool:
        env_config = self.config.get("env_config") or self.config.get("environment") or None
        self._configure_reset()
        self._calibration = CalibrationConfig.from_dict(self.config)

        perception_cfg = self.config.get("perception") or {}
        self._perception = AprilTagPerceptionService(
            camera_name=perception_cfg.get("camera_name", "head"),
            camera_frame=perception_cfg.get("camera_frame", self._calibration.camera_frame),
            tag_family=perception_cfg.get("tag_family", "tag25h9"),
            tag_size_m=perception_cfg.get("tag_size_m", 0.018),
            stale_after_s=perception_cfg.get("stale_after_s", 1.0),
            fallback_camera_params=self._calibration.intrinsics,
        )
        self._env = G01Env(env_config)
        self._env.setup()
        if self._reset_on_initialize:
            self._reset_robot_pose()
        self._initialized = True
        return True

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    def reset(self):
        self._reset_robot_pose()

    def cleanup(self):
        self.stop()
        if self._env is not None:
            self._env.close()
            self._env = None
        self._initialized = False

    @expose_api(method="GET", path="/skill/status")
    def skill_status(self) -> dict[str, Any]:
        return {
            **self.get_status(),
            "environment_ready": self._env is not None,
            "calibration": None
            if self._calibration is None
            else {
                "camera_frame": self._calibration.camera_frame,
                "exec_frame": self._calibration.exec_frame,
                "transform_mode": self._calibration.transform_mode,
                "can_compute_T_exec_camera": self._calibration.can_provide_transform,
                "has_T_head_pitch_camera": self._calibration.t_head_pitch_camera is not None,
                "has_intrinsics": self._calibration.intrinsics is not None,
            },
            "perception": None if self._perception is None else self._perception.status(),
            "reset": {
                "reset_on_initialize": self._reset_on_initialize,
                "has_target_grippers_positions": self._reset_pose.get("target_grippers_positions") is not None,
                "has_target_arm_joint_positions": self._reset_pose.get("target_arm_joint_positions") is not None,
                "has_target_head_positions": self._reset_pose.get("target_head_positions") is not None,
                "has_target_waist_positions": self._reset_pose.get("target_waist_positions") is not None,
            },
        }

    @expose_api(method="POST", path="/skill/reset_robot")
    def reset_robot(self) -> dict[str, Any]:
        return self._reset_robot_pose()

    @expose_api(method="POST", path="/skill/get_eef_pose")
    def get_eef_pose(self, arm: str, camera_frame: str = "head_camera_optical") -> dict[str, Any]:
        arm = _validate_arm(arm)
        obs = self._observation()
        calibration = self._calibration_for_observation(obs)
        pose_exec = _current_eef_pose(obs, arm, calibration.exec_frame)
        if pose_exec is None:
            return {
                "ok": False,
                "message": f"current {arm} EEF pose is unavailable in {calibration.exec_frame}",
                "arm": arm,
                "exec_frame": calibration.exec_frame,
            }

        wrist_position_exec = _float_list(_get(pose_exec, "position"), 3)
        orientation_exec = _float_list(_get(pose_exec, "orientation"), 4)
        if wrist_position_exec is None:
            return {
                "ok": False,
                "message": f"current {arm} EEF pose in {calibration.exec_frame} does not contain position",
                "arm": arm,
                "exec_frame": calibration.exec_frame,
            }
        if orientation_exec is not None:
            position_exec = [
                float(value)
                for value in wrist_to_gripper_center_exec(wrist_position_exec, orientation_exec).tolist()
            ]
        else:
            position_exec = wrist_position_exec
        result: dict[str, Any] = {
            "ok": True,
            "arm": arm,
            "exec_frame": calibration.exec_frame,
            "eef_target_frame": "gripper_center",
            "position_exec_m": position_exec,
            "wrist_position_exec_m": wrist_position_exec,
            "orientation_exec_xyzw": orientation_exec,
            "gripper_center_offset_link7_m": [
                float(value) for value in np.asarray(GRIPPER_CENTER_OFFSET_LINK7_M, dtype=np.float64).reshape(3)
            ],
            "camera_frame": camera_frame or calibration.camera_frame,
        }

        try:
            result["position_camera_m"] = [
                float(value) for value in calibration.exec_to_camera_point(position_exec, camera_frame).tolist()
            ]
            result["wrist_position_camera_m"] = [
                float(value) for value in calibration.exec_to_camera_point(wrist_position_exec, camera_frame).tolist()
            ]
            result["camera_pose_available"] = True
        except Exception as exc:
            result["camera_pose_available"] = False
            result["camera_pose_error"] = str(exc)
        return result

    @expose_api(method="POST", path="/skill/detect_tags")
    def detect_tags(self) -> dict[str, Any]:
        obs = self._observation()
        return self._perception_service().detect_from_observation(obs)

    @expose_api(method="POST", path="/skill/get_tag_pose")
    def get_tag_pose(self, tag_id: int, allow_stale: bool = False) -> dict[str, Any]:
        result = self._perception_service().get_tag_pose(int(tag_id), allow_stale=allow_stale)
        if result.get("ok"):
            return result
        obs = self._observation()
        refresh = self._perception_service().detect_from_observation(obs)
        if not refresh.get("ok"):
            return refresh
        return self._perception_service().get_tag_pose(int(tag_id), allow_stale=allow_stale)

    @expose_api(method="GET", path="/skill/camera_views")
    def camera_views(
        self,
        cameras: str = "head,hand_left,hand_right",
        format: str = "jpg",
        include_images: bool = True,
        concatenate: bool = True,
        jpeg_quality: int = 85,
        save_images: bool = True,
        save_dir: str = "/home/ck/RoboClaw/artifacts/test_camera",
    ) -> dict[str, Any]:
        obs = self._observation()
        return _camera_views_from_observation(
            obs,
            cameras=cameras,
            image_format=format,
            include_images=_coerce_bool(include_images),
            concatenate=_coerce_bool(concatenate),
            jpeg_quality=jpeg_quality,
            save_images=_coerce_bool(save_images),
            save_dir=save_dir,
        )

    @expose_api(method="POST", path="/skill/move_eef")
    def move_eef(
        self,
        arm: str,
        target_position_camera_m: list[float],
        camera_frame: str = "head_camera_optical",
        target_orientation_camera_xyzw: list[float] | None = None,
        duration_s: float = 1.0,
        gripper_value: float | None = None,
        control_hz: float | None = None,
        control_frequency_hz: float | None = None,
    ) -> dict[str, Any]:
        self._reject_control_frequency(control_hz, control_frequency_hz)
        obs = self._observation()
        calibration = self._calibration_for_observation(obs)
        action, meta = build_move_eef_action(
            obs,
            calibration,
            arm=arm,
            camera_frame=camera_frame,
            target_position_camera_m=target_position_camera_m,
            target_orientation_camera_xyzw=target_orientation_camera_xyzw,
            duration_s=duration_s,
            gripper_value=gripper_value,
        )
        self._execute(action, meta["actual_duration_s"])
        return {"action": action, "meta": meta}

    @expose_api(method="POST", path="/skill/lift_eef")
    def lift_eef(
        self,
        arm: str,
        distance_m: float,
        camera_frame: str = "head_camera_optical",
        duration_s: float = 1.0,
        control_hz: float | None = None,
        control_frequency_hz: float | None = None,
    ) -> dict[str, Any]:
        self._reject_control_frequency(control_hz, control_frequency_hz)
        obs = self._observation()
        calibration = self._calibration_for_observation(obs)
        action, meta = build_lift_eef_action(
            obs,
            calibration,
            arm=arm,
            camera_frame=camera_frame,
            distance_m=distance_m,
            duration_s=duration_s,
        )
        self._execute(action, meta["actual_duration_s"])
        return {"action": action, "meta": meta}

    @expose_api(method="POST", path="/skill/place_down")
    def place_down(
        self,
        arm: str,
        down_distance_m: float,
        camera_frame: str = "head_camera_optical",
        duration_s: float = 1.0,
        open_after_down: bool = True,
        control_hz: float | None = None,
        control_frequency_hz: float | None = None,
    ) -> dict[str, Any]:
        self._reject_control_frequency(control_hz, control_frequency_hz)
        obs = self._observation()
        calibration = self._calibration_for_observation(obs)
        actions, meta = build_place_down_sequence(
            obs,
            calibration,
            arm=arm,
            camera_frame=camera_frame,
            down_distance_m=down_distance_m,
            duration_s=duration_s,
            open_after_down=open_after_down,
        )
        self._execute_sequence(actions)
        return {"actions": actions, "meta": meta}

    @expose_api(method="POST", path="/skill/gripper")
    def gripper(
        self,
        arm: str,
        gripper_value: float,
        duration_s: float = 0.5,
        control_hz: float | None = None,
        control_frequency_hz: float | None = None,
    ) -> dict[str, Any]:
        self._reject_control_frequency(control_hz, control_frequency_hz)
        obs = self._observation()
        action, meta = build_gripper_action(obs, arm=arm, gripper_value=gripper_value, duration_s=duration_s)
        self._execute(action, meta["actual_duration_s"])
        return {"action": action, "meta": meta}

    def _observation(self):
        if self._env is None:
            raise RuntimeError("G01Env is not initialized")
        return self._env.get_observation()

    def _execute(self, action: dict[str, Any], wait_action_time: float):
        if self._env is None:
            raise RuntimeError("G01Env is not initialized")
        self._env.execute_action(action, wait_action_time=wait_action_time)

    def _execute_sequence(self, actions: list[dict[str, Any]]):
        for action in actions:
            self._execute(action, float(action["trajectory_reference_time"]))

    def _calibration_config(self) -> CalibrationConfig:
        if self._calibration is None:
            raise RuntimeError("calibration is not initialized")
        return self._calibration

    def _calibration_for_observation(self, observation: Any) -> CalibrationConfig:
        calibration = self._calibration_config()
        if calibration.t_exec_camera is not None:
            return calibration
        if not calibration.has_dynamic_fk:
            return calibration
        return calibration.with_t_exec_camera(self._dynamic_t_exec_camera(observation, calibration))

    def _dynamic_t_exec_camera(self, observation: Any, calibration: CalibrationConfig) -> np.ndarray:
        if calibration.exec_frame != "base_link":
            raise ValueError("dynamic_fk transform currently supports exec_frame='base_link' only")
        if calibration.t_head_pitch_camera is None:
            raise ValueError("dynamic_fk requires fixed T_head_pitch_camera calibration")

        states = _observation_states(observation)
        head = _float_list(_get(states, "head_joint_states"), 2)
        waist = _float_list(_get(states, "waist_joint_states"), 2)
        if head is None or waist is None:
            raise ValueError("dynamic_fk requires current head_joint_states and waist_joint_states in observation")

        xyzquat = self._kinematics_for_calibration(calibration).compute_head_fk(
            float(head[0]),
            float(head[1]),
            float(waist[0]),
            float(waist[1]),
        )
        t_base_head_pitch = np.eye(4, dtype=np.float64)
        t_base_head_pitch[:3, :3] = R.from_quat(xyzquat[3:]).as_matrix()
        t_base_head_pitch[:3, 3] = np.asarray(xyzquat[:3], dtype=np.float64)
        return t_base_head_pitch @ calibration.t_head_pitch_camera

    def _kinematics_for_calibration(self, calibration: CalibrationConfig):
        urdf_path = str(self._dynamic_fk_urdf_path(calibration))
        if self._kinematics is None or self._kinematics_urdf_path != urdf_path:
            from corobot.utils.kinematics import Kinematics

            with redirect_stdout(StringIO()):
                self._kinematics = Kinematics(urdf_path)
            self._kinematics_urdf_path = urdf_path
        return self._kinematics

    def _dynamic_fk_urdf_path(self, calibration: CalibrationConfig) -> Path:
        if calibration.urdf_path is not None:
            return self._resolve_config_path(calibration.urdf_path)
        from corobot.utils.fk_solver import _find_urdf_solver_dir

        return (_find_urdf_solver_dir() / "A2D_viz.urdf").resolve()

    def _resolve_config_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        return (Path(self.config_path).expanduser().resolve().parent / path).resolve()

    def _perception_service(self) -> AprilTagPerceptionService:
        if self._perception is None:
            raise RuntimeError("perception is not initialized")
        return self._perception

    def _configure_reset(self):
        pose = _copy_reset_pose(DEFAULT_RESET_POSE)
        reset_section = self.config.get("reset") or {}
        reset_pose_section = self.config.get("reset_pose") or {}
        model_config_section = self.config.get("model_config") or {}

        reset_pose_nested = reset_section.get("pose") or {}
        for source in (model_config_section, reset_section, reset_pose_nested, reset_pose_section):
            if source:
                _merge_reset_pose(pose, source)

        self._reset_pose = pose
        self._reset_on_initialize = bool(
            self.config.get("reset_on_initialize", reset_section.get("on_initialize", False))
        )

    def _reset_robot_pose(self) -> dict[str, Any]:
        if self._env is None:
            raise RuntimeError("G01Env is not initialized")

        self.stop()
        init_pose = _copy_reset_pose(self._reset_pose)
        gripper_result = self._reset_grippers_pose(init_pose.get("target_grippers_positions"))

        arm_reset_pose = dict(init_pose)
        arm_reset_pose["target_grippers_positions"] = None
        logger.info("Resetting robot pose through G01Env.reset")
        self._env.reset(**arm_reset_pose)

        return {
            "ok": True,
            "reset_on_initialize": self._reset_on_initialize,
            "gripper_reset": gripper_result,
            "target_arm_joint_positions": init_pose.get("target_arm_joint_positions"),
            "target_head_positions": init_pose.get("target_head_positions"),
            "target_waist_positions": init_pose.get("target_waist_positions"),
        }

    def _reset_grippers_pose(self, target_grippers_positions: list[float] | None) -> dict[str, Any]:
        if self._env is None:
            raise RuntimeError("G01Env is not initialized")
        if target_grippers_positions is None:
            return {"executed": False, "reason": "target_grippers_positions is not configured"}
        if len(target_grippers_positions) < 2:
            logger.warning(f"Gripper reset target count is insufficient: {target_grippers_positions}")
            return {
                "executed": False,
                "reason": "target_grippers_positions must contain left and right gripper values",
                "target_grippers_positions": target_grippers_positions,
            }

        target = [float(target_grippers_positions[0]), float(target_grippers_positions[1])]
        action = Action(
            timestamps=int(time.time() * 1e9),
            trajectory_reference_time=1.0,
            base_link="base_link",
            left_effector=[[target[0]]],
            right_effector=[[target[1]]],
        )
        logger.info(f"Resetting grippers before arm reset: {target}")
        self._env.execute_action(action, action.trajectory_reference_time)
        return {
            "executed": True,
            "target_grippers_positions": target,
            "duration_s": action.trajectory_reference_time,
        }

    def _reject_control_frequency(self, control_hz: float | None, control_frequency_hz: float | None):
        payload = {}
        if control_hz is not None:
            payload["control_hz"] = control_hz
        if control_frequency_hz is not None:
            payload["control_frequency_hz"] = control_frequency_hz
        validate_no_control_hz(payload)


def _copy_reset_pose(pose: dict[str, Any]) -> dict[str, list[float] | None]:
    copied: dict[str, list[float] | None] = {}
    for key, value in pose.items():
        copied[key] = _float_list_or_none(value)
    return copied


def _merge_reset_pose(target: dict[str, list[float] | None], source: dict[str, Any]) -> None:
    for source_key, target_key in RESET_CONFIG_KEY_MAP.items():
        if source_key in source:
            target[target_key] = _float_list_or_none(source[source_key])


def _float_list_or_none(value: Any) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"reset pose value must be a list or tuple, got {type(value).__name__}")
    return [float(item) for item in value]


def _current_eef_pose(observation: Any, arm: str, frame: str) -> Any | None:
    states = _observation_states(observation)
    end_pose = _get(states, "end_pose")
    frame_pose = _get(end_pose, frame)
    pose = _get(frame_pose, f"{_validate_arm(arm)}_arm")
    if pose is not None:
        return pose
    return _fk_eef_pose_from_joint_states(states, arm, frame)


def _observation_states(observation: Any) -> Any | None:
    obs = _get(observation, "observation") or observation
    return _get(obs, "states")


def _fk_eef_pose_from_joint_states(states: Any, arm: str, frame: str) -> Any | None:
    arm_joints = _get(states, "arm_joint_states") or []
    waist = _get(states, "waist_joint_states") or []
    head = _get(states, "head_joint_states") or [0.0, 0.0]
    if len(arm_joints) < 14 or len(waist) < 2:
        return None
    if len(head) < 2:
        head = [0.0, 0.0]
    poses = _fk_solver().get_eef_pos(
        [float(value) for value in waist[:2]],
        [float(value) for value in head[:2]],
        [float(value) for value in arm_joints[:7]],
        [float(value) for value in arm_joints[7:14]],
        base_link=frame,
    )
    return _get(poses, f"{_validate_arm(arm)}_arm")


def _fk_solver():
    global _FK_SOLVER
    if _FK_SOLVER is None:
        from corobot.utils.fk_solver import FKTransform

        _FK_SOLVER = FKTransform()
    return _FK_SOLVER


def _validate_arm(arm: str) -> str:
    if arm not in ("left", "right"):
        raise ValueError("arm must be 'left' or 'right'")
    return arm


def _float_list(value: Any, expected_len: int) -> list[float] | None:
    if value is None:
        return None
    result = [float(item) for item in value]
    if len(result) != expected_len:
        raise ValueError(f"expected {expected_len} values, got {len(result)}")
    return result


def _camera_views_from_observation(
    observation: Any,
    *,
    cameras: str,
    image_format: str,
    include_images: bool,
    concatenate: bool,
    jpeg_quality: int,
    save_images: bool,
    save_dir: str,
) -> dict[str, Any]:
    camera_names = [name.strip() for name in cameras.split(",") if name.strip()]
    if not camera_names:
        raise ValueError("cameras must contain at least one camera name")

    encoding = _normalize_image_format(image_format)
    obs = _get(observation, "observation") or observation
    images = _get(obs, "images")
    timestamps = _get(obs, "timestamps") or {}

    camera_results: dict[str, Any] = {}
    decoded_images: dict[str, Any] = {}

    for camera_name in camera_names:
        raw_image = _get(images, camera_name)
        image = _image_to_array(raw_image)
        if image is None:
            camera_results[camera_name] = {
                "ok": False,
                "message": f"camera {camera_name} image is unavailable",
                "timestamp_ns": _int_or_none(_get(timestamps, camera_name)),
            }
            continue

        decoded_images[camera_name] = image
        camera_payload = {
            "ok": True,
            "timestamp_ns": _int_or_none(_get(timestamps, camera_name)),
            "shape": [int(value) for value in image.shape],
            "height": int(image.shape[0]),
            "width": int(image.shape[1]) if image.ndim >= 2 else None,
            "channels": int(image.shape[2]) if image.ndim >= 3 else 1,
            "encoding": encoding,
        }
        if include_images:
            camera_payload["image_base64"] = _encode_image_base64(image, encoding, jpeg_quality)
        camera_results[camera_name] = camera_payload

    result: dict[str, Any] = {
        "ok": bool(decoded_images),
        "complete": len(decoded_images) == len(camera_names),
        "requested_cameras": camera_names,
        "available_cameras": list(decoded_images.keys()),
        "missing_cameras": [name for name in camera_names if name not in decoded_images],
        "image_format": encoding,
        "include_images": include_images,
        "cameras": camera_results,
    }

    concatenated = _concatenate_images(decoded_images, camera_names) if (concatenate or save_images) else None
    if concatenate:
        if concatenated is None:
            result["concatenated"] = {
                "ok": False,
                "message": "not enough camera images are available to concatenate",
                "order": [name for name in camera_names if name in decoded_images],
            }
        else:
            concat_payload = {
                "ok": True,
                "order": [name for name in camera_names if name in decoded_images],
                "shape": [int(value) for value in concatenated.shape],
                "height": int(concatenated.shape[0]),
                "width": int(concatenated.shape[1]),
                "channels": int(concatenated.shape[2]) if concatenated.ndim >= 3 else 1,
                "encoding": encoding,
            }
            if include_images:
                concat_payload["image_base64"] = _encode_image_base64(concatenated, encoding, jpeg_quality)
            result["concatenated"] = concat_payload

    if save_images:
        result["saved_images"] = _save_camera_view_images(
            decoded_images,
            concatenated,
            camera_names,
            encoding=encoding,
            save_dir=save_dir,
            jpeg_quality=jpeg_quality,
        )

    return result


def _normalize_image_format(image_format: str) -> str:
    normalized = (image_format or "jpg").strip().lower()
    if normalized == "jpeg":
        normalized = "jpg"
    if normalized not in {"jpg", "png"}:
        raise ValueError("format must be 'jpg', 'jpeg', or 'png'")
    return normalized


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def _image_to_array(raw_image: Any):
    if isinstance(raw_image, list):
        raw_image = raw_image[0] if raw_image else None
    if raw_image is None:
        return None

    if isinstance(raw_image, dict):
        from corobot.utils.packet_convert import dict_to_image

        return dict_to_image(raw_image)

    try:
        image = np.asarray(raw_image)
    except Exception:
        return None
    if image.size == 0 or image.ndim < 2:
        return None
    return image


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _encode_image_base64(image: Any, encoding: str, jpeg_quality: int) -> str:
    import cv2

    encode_target = _rgb_to_cv2_image(_to_rgb_image(image))
    ext = ".jpg" if encoding == "jpg" else ".png"
    params = []
    if encoding == "jpg":
        quality = max(1, min(int(jpeg_quality), 100))
        params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    ok, buffer = cv2.imencode(ext, encode_target, params)
    if not ok:
        raise RuntimeError(f"failed to encode image as {encoding}")
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _to_uint8_image(image: Any):
    image_array = np.asarray(image)
    if image_array.dtype == np.uint8:
        return image_array
    if np.issubdtype(image_array.dtype, np.integer):
        max_value = float(np.iinfo(image_array.dtype).max)
        if max_value <= 0:
            return image_array.astype(np.uint8)
        return np.clip((image_array.astype(np.float64) / max_value) * 255.0, 0, 255).astype(np.uint8)
    return np.clip(image_array, 0, 255).astype(np.uint8)


def _concatenate_images(images: dict[str, Any], camera_names: list[str]):
    import cv2

    ordered = [_to_color_image(images[name]) for name in camera_names if name in images]
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]

    min_height = min(image.shape[0] for image in ordered)
    resized = []
    for image in ordered:
        height, width = image.shape[:2]
        if height != min_height:
            next_width = max(1, int(width * (min_height / height)))
            image = cv2.resize(image, (next_width, min_height))
        resized.append(image)
    return cv2.hconcat(resized)


def _save_camera_view_images(
    images: dict[str, Any],
    concatenated: Any | None,
    camera_names: list[str],
    *,
    encoding: str,
    save_dir: str,
    jpeg_quality: int,
) -> dict[str, Any]:
    now = time.time()
    date_text = time.strftime("%Y-%m-%d", time.localtime(now))
    target_root = Path(save_dir).expanduser()
    target_dir = target_root / date_text
    target_dir.mkdir(parents=True, exist_ok=True)

    stamp = f"{time.strftime('%H%M%S', time.localtime(now))}_{int((now % 1) * 1000):03d}"
    saved: dict[str, str] = {}
    errors: dict[str, str] = {}

    for camera_name in camera_names:
        if camera_name not in images:
            continue
        path = target_dir / f"camera_views_{stamp}_{camera_name}.{encoding}"
        try:
            _write_image_file(path, images[camera_name], encoding, jpeg_quality)
            saved[camera_name] = str(path)
        except Exception as exc:
            errors[camera_name] = str(exc)

    concatenated_path = None
    if concatenated is not None:
        path = target_dir / f"camera_views_{stamp}_three_views.{encoding}"
        try:
            _write_image_file(path, concatenated, encoding, jpeg_quality)
            concatenated_path = str(path)
        except Exception as exc:
            errors["three_views"] = str(exc)

    return {
        "ok": bool(saved) or concatenated_path is not None,
        "save_dir": str(target_root),
        "date_dir": str(target_dir),
        "color_order": "RGB",
        "timestamp": stamp,
        "cameras": saved,
        "concatenated": concatenated_path,
        "errors": errors,
    }


def _write_image_file(path: Path, image: Any, encoding: str, jpeg_quality: int) -> None:
    import cv2

    image_array = _rgb_to_cv2_image(_to_rgb_image(image))
    params = []
    if encoding == "jpg":
        params = [int(cv2.IMWRITE_JPEG_QUALITY), max(1, min(int(jpeg_quality), 100))]
    if not cv2.imwrite(str(path), image_array, params):
        raise RuntimeError(f"failed to write image file: {path}")


def _to_rgb_image(image: Any):
    import cv2

    image_array = _to_uint8_image(image)
    if image_array.ndim == 2:
        return cv2.cvtColor(image_array, cv2.COLOR_GRAY2RGB)
    if image_array.ndim == 3 and image_array.shape[2] == 1:
        return cv2.cvtColor(image_array[:, :, 0], cv2.COLOR_GRAY2RGB)
    if image_array.ndim == 3 and image_array.shape[2] in {3, 4}:
        # CoRobot observations are consumed as RGB by policy/perception code.
        # Keep that convention internally; only convert for OpenCV at encode/write time.
        return image_array
    return image_array


def _rgb_to_cv2_image(image: Any):
    import cv2

    image_array = _to_uint8_image(image)
    if image_array.ndim == 3 and image_array.shape[2] == 3:
        return cv2.cvtColor(image_array, cv2.COLOR_RGB2BGR)
    if image_array.ndim == 3 and image_array.shape[2] == 4:
        return cv2.cvtColor(image_array, cv2.COLOR_RGBA2BGRA)
    return image_array


def _to_color_image(image: Any):
    import cv2

    image_array = _to_uint8_image(image)
    if image_array.ndim == 2:
        return cv2.cvtColor(image_array, cv2.COLOR_GRAY2RGB)
    if image_array.ndim == 3 and image_array.shape[2] == 1:
        return cv2.cvtColor(image_array[:, :, 0], cv2.COLOR_GRAY2RGB)
    if image_array.ndim == 3 and image_array.shape[2] == 4:
        return cv2.cvtColor(image_array, cv2.COLOR_RGBA2RGB)
    return image_array


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
