from __future__ import annotations

import base64

import numpy as np
import pytest

from corobot.policy_tasks.rule_control_task import RuleControlTask
from mcp_control_demo.calibration import CalibrationConfig
from mcp_control_demo.control import GRIPPER_CENTER_OFFSET_LINK7_M
from mcp_control_demo.corobot_skill_task.skill_task import McpControlSkillTask


class FakeEnv:
    def __init__(self, observation=None):
        self.calls = []
        self.observation = observation

    def execute_action(self, action, wait_action_time: float):
        self.calls.append(("execute_action", action, wait_action_time))

    def reset(self, **kwargs):
        self.calls.append(("reset", kwargs))

    def get_observation(self):
        return self.observation


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
