"""Backward-compatible CoRobot PolicyTask import paths."""

__all__ = ["McpControlSkillTask", "RuleControlTask"]


def __getattr__(name):
    if name == "McpControlSkillTask":
        from .skill_task import McpControlSkillTask

        return McpControlSkillTask
    if name == "RuleControlTask":
        from corobot.policy_tasks.rule_control_task import RuleControlTask

        return RuleControlTask
    raise AttributeError(name)
