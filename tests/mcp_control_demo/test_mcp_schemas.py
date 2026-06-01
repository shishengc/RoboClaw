from __future__ import annotations

from mcp_control_demo.mcp_server.schemas import MCP_CONTROL_TOOL_SCHEMAS


def _contains_key(node, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(item, key) for item in node)
    return False


def test_mcp_control_schemas_do_not_expose_control_hz():
    for schema in MCP_CONTROL_TOOL_SCHEMAS:
        assert not _contains_key(schema, "control_hz")
        assert not _contains_key(schema, "control_frequency_hz")
        assert not _contains_key(schema, "camera_frame")
        assert not _contains_key(schema, "close_gripper_value")
        assert not _contains_key(schema, "lift_dz_base_m")
        assert not _contains_key(schema, "press_hold_s")


def test_expected_primitive_tools_are_present():
    names = {schema["name"] for schema in MCP_CONTROL_TOOL_SCHEMAS}
    assert {
        "get_skill_status",
        "reset_robot",
        "get_eef_pose",
        "get_camera_views",
        "detect_tags",
        "get_apriltag_pose",
        "move_eef",
        "lift_eef",
        "place_down",
        "open_gripper",
        "close_gripper",
        "switch_scene",
    } <= names
    assert "grasp_by_tag" not in names
