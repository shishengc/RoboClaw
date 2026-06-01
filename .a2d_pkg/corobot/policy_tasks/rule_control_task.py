from __future__ import annotations

import base64
import threading
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation as R
from websockets.sync import client as websocket_client

from corobot.envs.g01_env import G01Env
from corobot.policy_tasks.policy_task_base import PolicyTaskBase
from corobot.protocol.protocol_schemas import Action
from corobot.transport import msgpack_numpy
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
from mcp_control_demo.control.joint_units import normalize_head_joint_states_rad
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
    "target_waist_positions": [0.8901176920412174, 0.3298677062988281],
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
DEFAULT_CAMERA_FRAME = "head_camera_optical"


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
        policy_skill_cfg = self.config.get("policy_skill") or {}
        policy_cfg = self.config.get("policy") or {}
        self._policy_host = policy_skill_cfg.get("host") or policy_cfg.get("host") or "127.0.0.1"
        self._policy_timeout_s = float(
            policy_skill_cfg.get("timeout_s", policy_skill_cfg.get("timeout", policy_cfg.get("timeout", 30.0)))
        )
        self._policy_lock = threading.RLock()
        self._policy_stop_event = threading.Event()
        self._policy_thread: threading.Thread | None = None
        self._policy_ws = None
        self._policy_state = self._new_policy_state()

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
        self._stop_policy_thread()
        self._running = False

    def reset(self):
        self._stop_policy_thread(join_timeout_s=None)
        self._reset_robot_pose()

    def cleanup(self):
        self._stop_policy_thread(join_timeout_s=None)
        self._running = False
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

    @expose_api(method="POST", path="/skill/start_policy")
    def start_policy(self, prompt: str, port: int, chunk_count: int) -> dict[str, Any]:
        prompt = str(prompt or "").strip()
        try:
            port = int(port)
            chunk_count = int(chunk_count)
        except Exception:
            return self._policy_status_with_error("port and chunk_count must be integers")

        if not prompt:
            return self._policy_status_with_error("prompt must be a non-empty string")
        if port < 1 or port > 65535:
            return self._policy_status_with_error("port must be between 1 and 65535")
        if chunk_count < 1:
            return self._policy_status_with_error("chunk_count must be greater than or equal to 1")
        if self._env is None:
            return self._policy_status_with_error("G01Env is not initialized")

        policy_url = f"ws://{self._policy_host}:{port}"
        with self._policy_lock:
            if self._policy_thread is not None and self._policy_thread.is_alive():
                status = self._policy_status_unlocked()
                status["ok"] = False
                status["message"] = "policy run is already active"
                return status

            self._policy_stop_event.clear()
            self._policy_state = self._new_policy_state(
                running=True,
                prompt=prompt,
                policy_port=port,
                policy_url=policy_url,
                target_chunks=chunk_count,
            )
            self._running = True
            self._policy_ws = None
            self._policy_thread = threading.Thread(
                target=self._policy_loop,
                args=(prompt, chunk_count, policy_url),
                daemon=True,
                name="RuleControlTaskPolicySkill",
            )
            self._policy_thread.start()
            return self._policy_status_unlocked()

    @expose_api(method="GET", path="/skill/policy_status")
    def policy_status(self) -> dict[str, Any]:
        return self._policy_status()

    @expose_api(method="POST", path="/skill/reset_robot")
    def reset_robot(self) -> dict[str, Any]:
        return self._reset_robot_pose()

    @expose_api(method="POST", path="/skill/get_eef_pose")
    def get_eef_pose(self, arm: str) -> dict[str, Any]:
        arm = _validate_arm(arm)
        obs = self._observation()
        calibration = self._calibration_for_observation(obs)
        camera_frame = calibration.camera_frame or DEFAULT_CAMERA_FRAME
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
        target_orientation_camera_xyzw: list[float] | None = None,
        duration_s: float = 1.0,
        gripper_value: float | None = None,
    ) -> dict[str, Any]:
        obs = self._observation()
        calibration = self._calibration_for_observation(obs)
        camera_frame = calibration.camera_frame or DEFAULT_CAMERA_FRAME
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
        duration_s: float = 1.0,
    ) -> dict[str, Any]:
        obs = self._observation()
        calibration = self._calibration_for_observation(obs)
        camera_frame = calibration.camera_frame or DEFAULT_CAMERA_FRAME
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
        duration_s: float = 1.0,
        open_after_down: bool = True,
    ) -> dict[str, Any]:
        obs = self._observation()
        calibration = self._calibration_for_observation(obs)
        camera_frame = calibration.camera_frame or DEFAULT_CAMERA_FRAME
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
    ) -> dict[str, Any]:
        obs = self._observation()
        action, meta = build_gripper_action(obs, arm=arm, gripper_value=gripper_value, duration_s=duration_s)
        self._execute(action, meta["actual_duration_s"])
        return {"action": action, "meta": meta}

    @expose_api(method="POST", path="/skill/switch_scene")
    def switch_scene(
        self,
        arm: str = "right",
        button_tag_id: int = 20,
        base_offset_m: list[float] | None = None,
        move_duration_s: float = 2.0,
        gripper_duration_s: float = 0.5,
        press_interval_s: float = 3.0,
    ) -> dict[str, Any]:
        arm = _validate_arm(arm)
        button_tag_id = int(button_tag_id)
        base_offset = np.asarray(_float_list(base_offset_m or [0.0, 0.0, 0.0], 3), dtype=np.float64)
        lift_dz_base_m = 0.10
        press_hold_s = 0.5
        close_gripper_value = 1.0

        obs = self._observation()
        detection = self._perception_service().detect_from_observation(obs)
        if not detection.get("ok"):
            return {
                "ok": False,
                "message": "switch_scene failed before motion: AprilTag detection failed",
                "error_type": "tag_detection_failed",
                "detection": detection,
            }

        detections = detection.get("detections") or []
        button_tag = next((item for item in detections if int(item.get("tag_id", -1)) == button_tag_id), None)
        if button_tag is None:
            return {
                "ok": False,
                "message": f"switch_scene failed before motion: button tag {button_tag_id} is not visible",
                "error_type": "tag_not_visible",
                "missing_tag_ids": [button_tag_id],
                "visible_tag_ids": sorted(int(item["tag_id"]) for item in detections if "tag_id" in item),
                "detections": detections,
            }

        calibration = self._calibration_for_observation(obs)
        camera_frame = calibration.camera_frame or DEFAULT_CAMERA_FRAME
        tag_camera = np.asarray(_tag_position_camera(button_tag), dtype=np.float64).reshape(3)
        tag_base = calibration.camera_to_exec_point(tag_camera, camera_frame)
        button_base = tag_base + base_offset
        button_above_base = button_base + np.asarray([0.0, 0.0, lift_dz_base_m], dtype=np.float64)
        button_camera = calibration.exec_to_camera_point(button_base, camera_frame)
        button_above_camera = calibration.exec_to_camera_point(button_above_base, camera_frame)

        targets = {
            "tag_position_camera_m": _round_list(tag_camera),
            "tag_position_base_m": _round_list(tag_base),
            "button_contact_base_m": _round_list(button_base),
            "button_contact_camera_m": _round_list(button_camera),
            "button_above_base_m": _round_list(button_above_base),
            "button_above_camera_m": _round_list(button_above_camera),
        }

        segments: list[dict[str, Any]] = []

        def execute_gripper(name: str) -> None:
            action_obs = self._observation()
            action, meta = build_gripper_action(
                action_obs,
                arm=arm,
                gripper_value=close_gripper_value,
                duration_s=gripper_duration_s,
            )
            self._execute(action, meta["actual_duration_s"])
            segments.append({"name": name, "action": action, "meta": meta})

        def execute_move(name: str, target_camera: np.ndarray) -> None:
            action_obs = self._observation()
            action_calibration = self._calibration_for_observation(action_obs)
            action, meta = build_move_eef_action(
                action_obs,
                action_calibration,
                arm=arm,
                camera_frame=camera_frame,
                target_position_camera_m=_round_list(target_camera),
                duration_s=move_duration_s,
            )
            self._execute(action, meta["actual_duration_s"])
            segments.append({"name": name, "action": action, "meta": meta})

        def wait_segment(name: str, duration_s: float) -> None:
            if duration_s <= 0.0:
                return
            time.sleep(duration_s)
            segments.append({"name": name, "wait_s": duration_s})

        execute_gripper("close_gripper")
        execute_move("move_to_button_above_1", button_above_camera)
        execute_move("move_down_to_button_press_1", button_camera)
        wait_segment("hold_after_press_1", press_hold_s)
        execute_move("lift_after_press_1", button_above_camera)
        wait_segment("wait_between_presses", press_interval_s)
        execute_move("move_down_to_button_press_2", button_camera)
        wait_segment("hold_after_press_2", press_hold_s)
        execute_move("lift_after_press_2", button_above_camera)

        return {
            "ok": True,
            "arm": arm,
            "button_tag_id": button_tag_id,
            "camera_frame": camera_frame,
            "base_offset_m": _round_list(base_offset),
            "lift_dz_base_m": lift_dz_base_m,
            "press_hold_s": press_hold_s,
            "press_interval_s": press_interval_s,
            "close_gripper_value": float(close_gripper_value),
            "button_tag": button_tag,
            "targets": targets,
            "sequence": [segment["name"] for segment in segments],
            "segments": segments,
        }

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

    def _policy_loop(self, prompt: str, chunk_count: int, policy_url: str) -> None:
        ws = None
        completed = False
        failed = False
        last_error = None
        reset_result = None
        try:
            logger.info(f"Connecting policy skill: {policy_url}")
            ws = websocket_client.connect(
                policy_url,
                compression=None,
                max_size=None,
                open_timeout=self._policy_timeout_s,
                close_timeout=self._policy_timeout_s,
            )
            with self._policy_lock:
                self._policy_ws = ws
            metadata = _unpack_policy_frame(ws.recv(), "metadata")
            with self._policy_lock:
                self._policy_state["metadata"] = _json_safe(metadata)

            for chunk_index in range(1, chunk_count + 1):
                if self._policy_stop_event.is_set():
                    break

                payload = self._policy_model_input(prompt)
                ws.send(msgpack_numpy.packb(_model_dump(payload)))
                response = _unpack_policy_frame(ws.recv(), "response")
                if isinstance(response, dict) and "error" in response:
                    raise RuntimeError(str(response["error"]))

                action = Action(**response)
                self._execute(action, action.trajectory_reference_time)
                with self._policy_lock:
                    self._policy_state["executed_chunks"] = chunk_index
                    self._policy_state["latest_action"] = _json_safe(_model_dump(action))
                    self._policy_state["latest_action_chunk_index"] = chunk_index

            completed = not self._policy_stop_event.is_set()

        except Exception as exc:
            if self._policy_stop_event.is_set():
                logger.info(f"Policy skill stopped: {exc}")
            else:
                failed = True
                last_error = str(exc)
                logger.error(f"Policy skill execution failed: {exc}")
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception as exc:
                    logger.warning(f"Failed to close policy websocket: {exc}")
                with self._policy_lock:
                    if self._policy_ws is ws:
                        self._policy_ws = None

            if completed or failed:
                try:
                    reset_result = self._reset_robot_pose()
                except Exception as exc:
                    reset_result = {"ok": False, "error": str(exc)}
                    failed = True
                    completed = False
                    last_error = (
                        f"{last_error}; reset failed: {exc}" if last_error else f"reset failed: {exc}"
                    )

            with self._policy_lock:
                self._policy_state["running"] = False
                self._policy_state["completed"] = completed
                self._policy_state["failed"] = failed
                self._policy_state["last_error"] = last_error
                self._policy_state["reset_result"] = _json_safe(reset_result)
                self._running = False

    def _policy_model_input(self, prompt: str):
        if self._env is None:
            raise RuntimeError("G01Env is not initialized")
        if not hasattr(self._env, "get_std_model_input"):
            raise RuntimeError("G01Env does not expose get_std_model_input")

        payload = self._env.get_std_model_input()
        if payload is None:
            raise RuntimeError("policy input is unavailable")
        ready, reason = _policy_input_ready(payload)
        if not ready:
            raise RuntimeError(f"policy input is not ready: {reason}")
        payload.prompt = prompt
        return payload

    def _new_policy_state(
        self,
        *,
        running: bool = False,
        prompt: str | None = None,
        policy_port: int | None = None,
        policy_url: str | None = None,
        target_chunks: int = 0,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "running": running,
            "completed": False,
            "failed": False,
            "prompt": prompt,
            "policy_port": policy_port,
            "policy_url": policy_url,
            "target_chunks": int(target_chunks),
            "executed_chunks": 0,
            "latest_action": None,
            "latest_action_chunk_index": None,
            "metadata": None,
            "last_error": None,
            "reset_result": None,
        }

    def _policy_status(self) -> dict[str, Any]:
        with self._policy_lock:
            return self._policy_status_unlocked()

    def _policy_status_unlocked(self) -> dict[str, Any]:
        status = _json_safe(self._policy_state)
        status["ok"] = not bool(status.get("failed"))
        return status

    def _policy_status_with_error(self, message: str) -> dict[str, Any]:
        status = self._policy_status()
        status["ok"] = False
        status["message"] = message
        return status

    def _stop_policy_thread(self, join_timeout_s: float | None = 5.0) -> None:
        thread = self._policy_thread
        if thread is None:
            return
        if thread.is_alive():
            self._policy_stop_event.set()
            ws = self._policy_ws
            if ws is not None:
                try:
                    ws.close()
                except Exception as exc:
                    logger.warning(f"Failed to close policy websocket while stopping: {exc}")
            if thread is not threading.current_thread():
                thread.join(timeout=join_timeout_s)
        if not thread.is_alive():
            with self._policy_lock:
                self._policy_state["running"] = False

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
        head = normalize_head_joint_states_rad(head)

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


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def _unpack_policy_frame(frame: Any, frame_name: str) -> Any:
    if isinstance(frame, str):
        raise RuntimeError(f"Policy returned a text {frame_name} frame instead of msgpack bytes:\n{frame}")
    if not isinstance(frame, (bytes, bytearray, memoryview)):
        raise TypeError(f"Policy returned unsupported {frame_name} frame type: {type(frame).__name__}")
    return msgpack_numpy.unpackb(frame)


def _policy_input_ready(payload: Any) -> tuple[bool, str]:
    obs = _get(payload, "observation")
    if obs is None:
        return False, "observation is unavailable"

    images = _get(obs, "images")
    missing_images = [
        name
        for name in ("head", "hand_left", "hand_right")
        if _image_to_array(_get(images, name)) is None
    ]
    if missing_images:
        return False, f"missing images: {', '.join(missing_images)}"

    states = _get(obs, "states")
    state_requirements = {
        "arm_joint_states": 14,
        "gripper_states": 2,
        "head_joint_states": 2,
        "waist_joint_states": 2,
    }
    missing_states = []
    for state_name, min_len in state_requirements.items():
        actual_len = _sequence_len(_get(states, state_name))
        if actual_len < min_len:
            missing_states.append(f"{state_name} has {actual_len} value(s), expected at least {min_len}")
    if missing_states:
        return False, "; ".join(missing_states)

    return True, "ok"


def _sequence_len(value: Any) -> int:
    if value is None:
        return 0
    try:
        return len(value)
    except Exception:
        return 1


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
    head = normalize_head_joint_states_rad(head[:2])
    poses = _fk_solver().get_eef_pos(
        [float(value) for value in waist[:2]],
        head,
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


def _tag_position_camera(tag_pose: dict[str, Any]) -> list[float]:
    for key in ("position_camera_m", "translation_m"):
        value = tag_pose.get(key)
        if value is not None:
            return _float_list(value, 3) or []
    camera_pose = tag_pose.get("camera_pose") or {}
    value = _get(camera_pose, "position_m")
    if value is not None:
        return _float_list(value, 3) or []
    raise ValueError("tag pose does not contain a camera-frame position")


def _round_list(values: Any) -> list[float]:
    return [round(float(value), 6) for value in np.asarray(values, dtype=np.float64).reshape(-1)]


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
