from __future__ import annotations

import base64
import inspect
import math

import numpy as np
import pytest

from corobot.policy_tasks.rule_control_task import RuleControlTask
from mcp_control_demo.calibration import CalibrationConfig
from mcp_control_demo.control import GRIPPER_CENTER_OFFSET_LINK7_M
from mcp_control_demo.corobot_skill_task.skill_task import McpControlSkillTask
from corobot.protocol.protocol_schemas import Action, STD_MODEL_INPUT
from corobot.transport import msgpack_numpy


class FakeEnv:
    def __init__(self, observation=None, std_model_inputs=None):
        self.calls = []
        self.observation = observation
        self.std_model_inputs = list(std_model_inputs or [])

    def execute_action(self, action, wait_action_time: float):
        self.calls.append(("execute_action", action, wait_action_time))

    def reset(self, **kwargs):
        self.calls.append(("reset", kwargs))

    def get_observation(self):
        return self.observation

    def get_std_model_input(self):
        if self.std_model_inputs:
            return self.std_model_inputs.pop(0)
        return STD_MODEL_INPUT(observation=self.observation or {})


class FakePolicyWebSocket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = False

    def recv(self):
        frame = self.frames.pop(0)
        if isinstance(frame, Exception):
            raise frame
        return frame

    def send(self, data):
        self.sent.append(msgpack_numpy.unpackb(data))

    def close(self):
        self.closed = True


class FakePerception:
    def __init__(self, detections):
        self.detections = detections

    def detect_from_observation(self, observation):
        return {"ok": True, "detections": self.detections}

    def status(self):
        return {"cached_tag_ids": [int(item["tag_id"]) for item in self.detections]}


def _complete_policy_input() -> STD_MODEL_INPUT:
    return STD_MODEL_INPUT(
        observation={
            "timestamps": {"head": 1, "hand_left": 1, "hand_right": 1},
            "images": {
                "head": np.zeros((4, 4, 3), dtype=np.uint8),
                "hand_left": np.zeros((4, 4, 3), dtype=np.uint8),
                "hand_right": np.zeros((4, 4, 3), dtype=np.uint8),
            },
            "states": {
                "arm_joint_states": [0.0] * 14,
                "gripper_states": [0.0, 0.0],
                "head_joint_states": [0.0, 0.0],
                "waist_joint_states": [0.0, 0.0],
            },
        }
    )


def _packed(value):
    return msgpack_numpy.packb(value)


def test_reset_robot_is_exposed_and_uses_safe_order(tmp_path):
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
reset_on_initialize: false
reset_pose:
  target_grippers_positions: [0.2, 0.3]
  target_arm_joint_positions: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
  target_head_positions: [0.1, 0.2]
  target_waist_positions: [0.3, 0.4]
""",
        encoding="utf-8",
    )
    task = RuleControlTask(str(config_path))
    task._configure_reset()
    env = FakeEnv()
    task._env = env
    task._running = True

    paths = {api["path"] for api in task.get_exposed_apis()}
    assert "/skill/reset_robot" in paths
    assert "/skill/get_eef_pose" in paths
    assert "/skill/camera_views" in paths
    assert "/skill/switch_scene" in paths
    assert "/skill/start_policy" in paths
    assert "/skill/policy_status" in paths

    result = task.reset_robot()

    assert result["ok"] is True
    assert task.is_running() is False
    assert env.calls[0][0] == "execute_action"
    assert env.calls[0][2] == 1.0
    assert env.calls[1][0] == "reset"
    reset_kwargs = env.calls[1][1]
    assert reset_kwargs["target_grippers_positions"] is None
    assert reset_kwargs["target_arm_joint_positions"] == [float(value) for value in range(1, 15)]
    assert reset_kwargs["target_head_positions"] == [0.1, 0.2]
    assert reset_kwargs["target_waist_positions"] == [0.3, 0.4]


def test_mcp_control_skill_task_is_compatible_alias():
    assert issubclass(McpControlSkillTask, RuleControlTask)


def test_rule_control_skill_apis_do_not_expose_control_frequency_arguments(tmp_path):
    config_path = tmp_path / "task.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    task = RuleControlTask(str(config_path))

    hidden_parameters = {
        "camera_frame",
        "control_hz",
        "control_frequency_hz",
        "close_gripper_value",
        "lift_dz_base_m",
        "press_hold_s",
    }
    for api in task.get_exposed_apis():
        parameters = inspect.signature(api["handler"]).parameters
        assert hidden_parameters.isdisjoint(parameters)


def test_start_policy_executes_chunks_records_latest_action_and_resets(tmp_path, monkeypatch):
    config_path = tmp_path / "task.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    returned_actions = [
        Action(timestamps=101, trajectory_reference_time=0.01, left_effector=[[0.1]], right_effector=[[0.2]]),
        Action(timestamps=102, trajectory_reference_time=0.02, left_effector=[[0.3]], right_effector=[[0.4]]),
    ]
    fake_ws = FakePolicyWebSocket(
        [
            _packed({"camera_names": ["head", "hand_left", "hand_right"]}),
            *[_packed(action.model_dump()) for action in returned_actions],
        ]
    )
    monkeypatch.setattr(
        "corobot.policy_tasks.rule_control_task.websocket_client.connect",
        lambda *args, **kwargs: fake_ws,
    )

    task = RuleControlTask(str(config_path))
    env = FakeEnv(std_model_inputs=[_complete_policy_input(), _complete_policy_input()])
    task._env = env

    start_result = task.start_policy(prompt="pick up the cube", port=8999, chunk_count=2)
    assert start_result["ok"] is True
    assert task._policy_thread is not None
    task._policy_thread.join(timeout=2.0)

    status = task.policy_status()
    assert status["ok"] is True
    assert status["running"] is False
    assert status["completed"] is True
    assert status["failed"] is False
    assert status["executed_chunks"] == 2
    assert status["latest_action_chunk_index"] == 2
    assert status["latest_action"]["timestamps"] == 102
    assert status["metadata"] == {"camera_names": ["head", "hand_left", "hand_right"]}
    assert status["reset_result"]["ok"] is True
    assert fake_ws.closed is True
    assert [payload["prompt"] for payload in fake_ws.sent] == ["pick up the cube", "pick up the cube"]

    policy_action_calls = [
        call for call in env.calls if call[0] == "execute_action" and getattr(call[1], "timestamps", None) in {101, 102}
    ]
    assert len(policy_action_calls) == 2
    assert [call[2] for call in policy_action_calls] == pytest.approx([0.01, 0.02])
    assert env.calls[-1][0] == "reset"


def test_start_policy_pull_drawer_moves_waist_before_policy_input_and_resets(tmp_path, monkeypatch):
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
reset_pose:
  target_waist_positions: [0.89, 0.33]
  pull_waist_positions: [0.40441, 0.2598677062988281]
""",
        encoding="utf-8",
    )
    returned_action = Action(timestamps=201, trajectory_reference_time=0.01, left_effector=[[0.1]])
    fake_ws = FakePolicyWebSocket(
        [
            _packed({"camera_names": ["head", "hand_left", "hand_right"]}),
            _packed(returned_action.model_dump()),
        ]
    )
    monkeypatch.setattr(
        "corobot.policy_tasks.rule_control_task.websocket_client.connect",
        lambda *args, **kwargs: fake_ws,
    )

    task = RuleControlTask(str(config_path))
    task._configure_reset()
    monkeypatch.setattr(task, "_wait_for_waist_position", lambda target: (True, target, 0.0))
    env = FakeEnv(
        observation={
            "states": {
                "arm_joint_states": [0.01] * 14,
                "waist_joint_states": [0.89, 0.33],
            }
        },
        std_model_inputs=[_complete_policy_input()],
    )
    task._env = env

    result = task.start_policy(prompt="Pull open the drawer", port=8999, chunk_count=1)
    assert result["ok"] is True
    assert result["pre_policy_waist_result"]["executed"] is True
    assert task._policy_thread is not None
    task._policy_thread.join(timeout=2.0)

    status = task.policy_status()
    assert status["ok"] is True
    assert status["pre_policy_waist_result"]["executed"] is True
    assert status["pre_policy_waist_result"]["target_waist_positions"] == pytest.approx(
        [0.40441, 0.2598677062988281]
    )
    assert fake_ws.sent[0]["prompt"] == "Pull open the drawer"

    assert env.calls[0][0] == "execute_action"
    pre_policy_action = env.calls[0][1]
    assert pre_policy_action.waist[-1] == pytest.approx([0.40441, 0.2598677062988281])
    assert len(pre_policy_action.waist) == 90
    assert env.calls[0][2] == pytest.approx(3.0)

    assert env.calls[-1][0] == "reset"
    final_reset = env.calls[-1][1]
    assert final_reset["target_waist_positions"] == pytest.approx([0.89, 0.33])


def test_start_policy_pull_drawer_prepose_runs_before_policy_metadata(tmp_path, monkeypatch):
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
reset_pose:
  target_waist_positions: [0.89, 0.33]
  pull_waist_positions: [0.40441, 0.2598677062988281]
""",
        encoding="utf-8",
    )
    fake_ws = FakePolicyWebSocket([RuntimeError("metadata unavailable")])
    monkeypatch.setattr(
        "corobot.policy_tasks.rule_control_task.websocket_client.connect",
        lambda *args, **kwargs: fake_ws,
    )

    task = RuleControlTask(str(config_path))
    task._configure_reset()
    monkeypatch.setattr(task, "_wait_for_waist_position", lambda target: (True, target, 0.0))
    env = FakeEnv(
        observation={
            "states": {
                "arm_joint_states": [0.01] * 14,
                "waist_joint_states": [0.89, 0.33],
            }
        },
        std_model_inputs=[_complete_policy_input()],
    )
    task._env = env

    result = task.start_policy(prompt="Pull open the drawer", port=8999, chunk_count=1)
    assert result["ok"] is True
    assert result["pre_policy_waist_result"]["executed"] is True
    assert task._policy_thread is not None
    task._policy_thread.join(timeout=2.0)

    status = task.policy_status()
    assert status["ok"] is False
    assert status["failed"] is True
    assert "metadata unavailable" in status["last_error"]
    assert status["pre_policy_waist_result"]["executed"] is True
    assert status["pre_policy_waist_result"]["target_waist_positions"] == pytest.approx(
        [0.40441, 0.2598677062988281]
    )
    assert env.calls[0][0] == "execute_action"
    assert env.calls[0][1].waist[-1] == pytest.approx([0.40441, 0.2598677062988281])
    assert len(env.calls[0][1].waist) == 90


def test_start_policy_failure_marks_failed_and_resets(tmp_path, monkeypatch):
    config_path = tmp_path / "task.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    fake_ws = FakePolicyWebSocket(
        [
            _packed({"camera_names": ["head", "hand_left", "hand_right"]}),
            _packed({"error": "policy refused request"}),
        ]
    )
    monkeypatch.setattr(
        "corobot.policy_tasks.rule_control_task.websocket_client.connect",
        lambda *args, **kwargs: fake_ws,
    )

    task = RuleControlTask(str(config_path))
    env = FakeEnv(std_model_inputs=[_complete_policy_input()])
    task._env = env

    result = task.start_policy(prompt="pick up the cube", port=8999, chunk_count=1)
    assert result["ok"] is True
    assert task._policy_thread is not None
    task._policy_thread.join(timeout=2.0)

    status = task.policy_status()
    assert status["ok"] is False
    assert status["running"] is False
    assert status["completed"] is False
    assert status["failed"] is True
    assert status["executed_chunks"] == 0
    assert "policy refused request" in status["last_error"]
    assert status["reset_result"]["ok"] is True
    assert fake_ws.closed is True
    assert env.calls[-1][0] == "reset"


def test_start_policy_rejects_invalid_arguments_without_thread(tmp_path):
    config_path = tmp_path / "task.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    task = RuleControlTask(str(config_path))
    task._env = FakeEnv(std_model_inputs=[_complete_policy_input()])

    assert task.start_policy(prompt="", port=8999, chunk_count=1)["ok"] is False
    assert task.start_policy(prompt="valid", port=0, chunk_count=1)["ok"] is False
    assert task.start_policy(prompt="valid", port=8999, chunk_count=0)["ok"] is False
    assert task._policy_thread is None


def test_get_eef_pose_returns_exec_and_camera_position(tmp_path):
    config_path = tmp_path / "task.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    task = RuleControlTask(str(config_path))
    task._calibration = CalibrationConfig.identity_for_tests()
    task._env = FakeEnv(
        {
            "states": {
                "end_pose": {
                    "base_link": {
                        "right_arm": {
                            "position": [0.1, 0.2, 0.3],
                            "orientation": [0.0, 0.0, 0.0, 1.0],
                        }
                    }
                }
            }
        }
    )

    result = task.get_eef_pose("right")

    assert result["ok"] is True
    assert result["position_exec_m"] == pytest.approx([0.1, 0.2, 0.3 + GRIPPER_CENTER_OFFSET_LINK7_M[2]])
    assert result["position_camera_m"] == pytest.approx([0.1, 0.2, 0.3 + GRIPPER_CENTER_OFFSET_LINK7_M[2]])
    assert result["camera_pose_available"] is True


def test_dynamic_fk_converts_degree_head_observation_to_radians(tmp_path):
    class FakeKinematics:
        def __init__(self):
            self.calls = []

        def compute_head_fk(self, head_yaw, head_pitch, waist_pitch, waist_lift):
            self.calls.append((head_yaw, head_pitch, waist_pitch, waist_lift))
            return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    config_path = tmp_path / "task.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    task = RuleControlTask(str(config_path))
    fake_kinematics = FakeKinematics()
    task._kinematics_for_calibration = lambda calibration: fake_kinematics
    calibration = CalibrationConfig(t_head_pitch_camera=np.eye(4, dtype=np.float64))

    task._dynamic_t_exec_camera(
        {
            "states": {
                "head_joint_states": [0.0, 24.99526934901729],
                "waist_joint_states": [0.4044099405259314, 0.3092010498046875],
            }
        },
        calibration,
    )

    assert len(fake_kinematics.calls) == 1
    assert fake_kinematics.calls[0] == pytest.approx(
        (0.0, math.radians(24.99526934901729), 0.4044099405259314, 0.3092010498046875)
    )


def test_camera_views_returns_three_view_metadata_and_base64(tmp_path):
    config_path = tmp_path / "task.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    task = RuleControlTask(str(config_path))
    task._env = FakeEnv(
        {
            "timestamps": {"head": 100, "hand_left": 90, "hand_right": 95},
            "images": {
                "head": np.zeros((6, 8, 3), dtype=np.uint8),
                "hand_left": np.full((4, 6, 3), 127, dtype=np.uint8),
                "hand_right": np.full((5, 7, 3), 255, dtype=np.uint8),
            },
            "states": {},
        }
    )

    save_dir = tmp_path / "camera"
    result = task.camera_views(format="png", save_dir=str(save_dir))

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["requested_cameras"] == ["head", "hand_left", "hand_right"]
    assert result["cameras"]["head"]["shape"] == [6, 8, 3]
    assert result["cameras"]["hand_left"]["timestamp_ns"] == 90
    assert base64.b64decode(result["cameras"]["head"]["image_base64"])
    assert result["concatenated"]["ok"] is True
    assert base64.b64decode(result["concatenated"]["image_base64"])
    assert result["saved_images"]["ok"] is True
    assert result["saved_images"]["save_dir"] == str(save_dir)
    assert result["saved_images"]["date_dir"].startswith(str(save_dir))
    assert result["saved_images"]["color_order"] == "RGB"
    assert set(result["saved_images"]["cameras"]) == {"head", "hand_left", "hand_right"}
    assert result["saved_images"]["concatenated"] is not None
    for path in [*result["saved_images"]["cameras"].values(), result["saved_images"]["concatenated"]]:
        assert path is not None
        assert path.startswith(str(save_dir))
        assert (tmp_path / "camera" / path.split("/")[-2] / path.split("/")[-1]).exists()


def test_camera_views_saves_rgb_observation_as_correct_jpg_colors(tmp_path):
    import cv2

    config_path = tmp_path / "task.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    task = RuleControlTask(str(config_path))

    red_rgb = np.zeros((16, 16, 3), dtype=np.uint8)
    red_rgb[:, :] = [255, 0, 0]
    task._env = FakeEnv(
        {
            "timestamps": {"head": 100},
            "images": {"head": red_rgb},
            "states": {},
        }
    )

    result = task.camera_views(cameras="head", format="jpg", include_images=True, save_dir=str(tmp_path / "camera"))

    saved_path = result["saved_images"]["cameras"]["head"]
    saved_bgr = cv2.imread(saved_path, cv2.IMREAD_COLOR)
    assert saved_bgr is not None
    assert int(saved_bgr[0, 0, 2]) > 240
    assert int(saved_bgr[0, 0, 0]) < 20

    encoded = np.frombuffer(base64.b64decode(result["cameras"]["head"]["image_base64"]), dtype=np.uint8)
    encoded_bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    assert encoded_bgr is not None
    assert int(encoded_bgr[0, 0, 2]) > 240
    assert int(encoded_bgr[0, 0, 0]) < 20


def test_switch_scene_moves_above_then_presses_twice(tmp_path, monkeypatch):
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
reset_pose:
  target_grippers_positions: [0.0, 0.0]
  target_arm_joint_positions: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
  target_head_positions: [0.5, 0.6]
  target_waist_positions: [0.3, 0.4]
""",
        encoding="utf-8",
    )
    observation = {
        "states": {
            "arm_joint_states": [0.01] * 14,
            "waist_joint_states": [0.1, 0.2],
            "end_pose": {
                "base_link": {
                    "right_arm": {
                        "position": [0.0, 0.0, 0.0],
                        "orientation": [0.0, 0.0, 0.0, 1.0],
                    }
                }
            },
            "gripper_states": [0.0, 0.0],
        }
    }
    task = RuleControlTask(str(config_path))
    task._configure_reset()
    task._calibration = CalibrationConfig.identity_for_tests()
    task._perception = FakePerception(
        [{"tag_id": 20, "position_camera_m": [0.2, 0.3, 0.4], "timestamp_s": 1.0}]
    )
    env = FakeEnv(observation)
    task._env = env
    sleeps = []
    monkeypatch.setattr("corobot.policy_tasks.rule_control_task.time.sleep", lambda duration: sleeps.append(duration))
    monkeypatch.setattr(task, "_publish_wbc_head_command", lambda target, duration: 1)
    monkeypatch.setattr(task, "_send_body_pose_waist_command", lambda target, duration: 1)
    monkeypatch.setattr(task, "_wait_for_waist_position", lambda target: (True, target, 0.0))

    result = task.switch_scene(
        arm="right",
        button_tag_id=20,
        base_offset_m=[0.01, -0.02, 0.03],
        move_duration_s=0.1,
        gripper_duration_s=0.1,
        press_interval_s=1.25,
    )

    assert result["ok"] is True
    assert result["targets"]["button_contact_camera_m"] == pytest.approx([0.21, 0.28, 0.43])
    assert result["targets"]["button_above_camera_m"] == pytest.approx([0.21, 0.28, 0.53])
    assert result["sequence"] == [
        "close_gripper",
        "move_down_to_button_press_1",
        "hold_after_press_1",
        "lift_after_press_1",
        "wait_between_presses",
        "move_down_to_button_press_2",
        "hold_after_press_2",
        "lift_after_press_2",
    ]
    assert len(env.calls) == 7
    assert env.calls[-2][0] == "reset"
    assert env.calls[-2][1]["target_arm_joint_positions"] == pytest.approx([float(value) for value in range(1, 15)])
    assert env.calls[-2][1]["target_grippers_positions"] is None
    assert env.calls[-2][1]["target_head_positions"] is None
    assert env.calls[-2][1]["target_waist_positions"] is None
    assert env.calls[-1][0] == "execute_action"
    assert result["switch_scene_restore"]["arm_reset"]["target_arm_joint_positions"] == pytest.approx(
        [float(value) for value in range(1, 15)]
    )
    assert result["switch_scene_restore"]["rest_reset"]["target_waist_positions"] == pytest.approx([0.3, 0.4])
    move_targets = [
        segment["meta"]["target_position_camera_m"]
        for segment in result["segments"]
        if segment["name"].startswith(("move_", "lift_"))
    ]
    expected_targets = [
        [0.21, 0.28, 0.43],
        [0.21, 0.28, 0.53],
        [0.21, 0.28, 0.43],
        [0.21, 0.28, 0.53],
    ]
    for target, expected in zip(move_targets, expected_targets, strict=True):
        assert target == pytest.approx(expected)
    assert result["press_interval_s"] == pytest.approx(1.25)
    assert sleeps == pytest.approx([0.5, 1.25, 0.5])


def test_switch_scene_does_not_move_when_button_tag_missing(tmp_path, monkeypatch):
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
reset_pose:
  target_grippers_positions: [0.0, 0.0]
  target_arm_joint_positions: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
  target_head_positions: [0.5, 0.6]
  target_waist_positions: [0.3, 0.4]
""",
        encoding="utf-8",
    )
    task = RuleControlTask(str(config_path))
    task._configure_reset()
    task._calibration = CalibrationConfig.identity_for_tests()
    task._perception = FakePerception([{"tag_id": 21, "position_camera_m": [0.0, 0.0, 0.5]}])
    env = FakeEnv(
        {
            "states": {
                "arm_joint_states": [0.01] * 14,
                "waist_joint_states": [0.1, 0.2],
            }
        }
    )
    task._env = env
    monkeypatch.setattr(task, "_publish_wbc_head_command", lambda target, duration: 1)
    monkeypatch.setattr(task, "_send_body_pose_waist_command", lambda target, duration: 1)
    monkeypatch.setattr(task, "_wait_for_waist_position", lambda target: (True, target, 0.0))

    result = task.switch_scene(button_tag_id=20)

    assert result["ok"] is False
    assert result["error_type"] == "tag_not_visible"
    assert result["missing_tag_ids"] == [20]
    assert result["visible_tag_ids"] == [21]
    assert [call[0] for call in env.calls] == ["reset", "execute_action"]
    assert env.calls[0][1]["target_arm_joint_positions"] == pytest.approx([float(value) for value in range(1, 15)])
    assert env.calls[0][1]["target_waist_positions"] is None
