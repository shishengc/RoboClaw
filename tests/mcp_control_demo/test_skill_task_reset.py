from __future__ import annotations

from corobot.policy_tasks.rule_control_task import RuleControlTask
from mcp_control_demo.calibration import CalibrationConfig
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
calibration_path: /tmp/unused.yaml
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
    config_path.write_text("calibration_path: /tmp/unused.yaml\n", encoding="utf-8")
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
    assert result["position_exec_m"] == [0.1, 0.2, 0.3]
    assert result["position_camera_m"] == [0.1, 0.2, 0.3]
    assert result["camera_pose_available"] is True
