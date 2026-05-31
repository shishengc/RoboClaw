"""
Structured perception result helpers.
"""

from typing import Any


def normalize_perception_result(content: dict[str, Any] | str) -> dict[str, Any]:
    """
    Normalize perception output into the shape the agent expects.

    Real SenseEnvironment tools should return this schema directly. Mock or legacy
    tools are wrapped so the rest of the agent can still reason over stable keys.
    """
    raw = content
    if not isinstance(content, dict):
        content = {"raw_observation": content}

    task_progress = content.get("task_progress")
    if not isinstance(task_progress, dict):
        task_progress = {
            "overall_completion": None,
            "status": "unknown",
            "completed_subtasks": [],
            "pending_subtasks": [],
        }

    safety_status = content.get("safety_status")
    if not isinstance(safety_status, dict):
        safety_status = {
            "is_safe": True,
            "issues": [],
        }

    return {
        "objects": content.get("objects", []),
        "robot_state": content.get("robot_state", {}),
        "gripper_state": content.get("gripper_state", {}),
        "task_progress": task_progress,
        "safety_status": safety_status,
        "uncertainties": content.get("uncertainties", []),
        "raw": raw,
    }
