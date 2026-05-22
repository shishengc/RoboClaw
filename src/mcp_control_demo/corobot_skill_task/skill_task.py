from __future__ import annotations

from corobot.policy_tasks.rule_control_task import RuleControlTask


class McpControlSkillTask(RuleControlTask):
    """Backward-compatible import path for the CoRobot-native RuleControlTask."""


__all__ = ["McpControlSkillTask", "RuleControlTask"]
