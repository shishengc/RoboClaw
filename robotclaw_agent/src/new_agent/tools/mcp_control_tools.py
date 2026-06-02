"""
NewAgent tool wrappers for the CoRobot mcp_control_demo skill APIs.

These tools intentionally stay thin: they expose the existing /skill/... HTTP
surface as BaseTool instances and normalize CoRobot responses into ToolResult.
The real robot environment remains owned by RuleControlTask/G01Env.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .mcp_control_recipes import (
    DEFAULT_SWITCH_SCENE_RECIPE,
    get_tag_pick_place_recipe,
    object_mapping,
    object_name_choices,
    recipe_names,
    resolve_pick_place_request,
)
from .mcp_control_task_helpers import DEFAULT_TASK_CONFIG_PATH, build_tag_grasp_targets, build_tag_place_targets
from .tool_types import BaseTool, ToolResult, ToolSchema, ToolStatus


DEFAULT_COROBOT_BASE_URL = os.environ.get("COROBOT_URL", "http://localhost:8765")

SKILL_TOOL_ENDPOINTS: dict[str, tuple[str, str]] = {
    "get_skill_status": ("GET", "/skill/status"),
    "reset_robot": ("POST", "/skill/reset_robot"),
    "get_eef_pose": ("POST", "/skill/get_eef_pose"),
    "detect_tags": ("POST", "/skill/detect_tags"),
    "get_apriltag_pose": ("POST", "/skill/get_tag_pose"),
    "move_eef": ("POST", "/skill/move_eef"),
    "lift_eef": ("POST", "/skill/lift_eef"),
    "place_down": ("POST", "/skill/place_down"),
    "open_gripper": ("POST", "/skill/gripper"),
    "close_gripper": ("POST", "/skill/gripper"),
    "switch_scene": ("POST", "/skill/switch_scene"),
}

DEFAULT_MCP_CONTROL_TOOL_NAMES = tuple(SKILL_TOOL_ENDPOINTS)
DEFAULT_MCP_LLM_TOOL_NAMES = (
    "reset_robot",
    "prepare_tag_pick_place",
    "detect_tags",
    "get_apriltag_pose",
    "resolve_tag_pick_place_recipe",
    "open_gripper",
    "close_gripper",
    "move_eef",
    "place_down",
    "switch_scene",
    "open_drawer_for_loading",
    "place_workpiece_in_drawer",
    "close_drawer_after_loading",
    "load_workpiece_to_drawer",
)
DEFAULT_CALIBRATION_PATH = DEFAULT_TASK_CONFIG_PATH
GRASP_TARGET_TOLERANCE_M = 0.003
DEFAULT_LOAD_UNLOAD_SCRIPT_PATH = Path("/home/ck/RoboClaw/scripts/test_load_unload.sh")
DEFAULT_LOAD_UNLOAD_ARM = "right"
DEFAULT_LOAD_UNLOAD_PULL_PROMPT = "Pull open the drawer"
DEFAULT_LOAD_UNLOAD_PUSH_PROMPT = "Push close the drawer"
DEFAULT_LOAD_UNLOAD_PULL_POLICY_PORT = 8998
DEFAULT_LOAD_UNLOAD_PUSH_POLICY_PORT = 8999
DEFAULT_LOAD_UNLOAD_PULL_POLICY_CHUNK_COUNT = 20
DEFAULT_LOAD_UNLOAD_PUSH_POLICY_CHUNK_COUNT = 25
DEFAULT_LOAD_UNLOAD_POLICY_TIMEOUT_S = 300
DEFAULT_LOAD_UNLOAD_SOURCE_TAG_ID = 5
DEFAULT_LOAD_UNLOAD_DEST_TAG_ID = 6
DEFAULT_LOAD_UNLOAD_GRASP_OFFSET_M = [0.0, 0.0, -0.03]
DEFAULT_LOAD_UNLOAD_PLACE_LIFT_OFFSET_M = [0.0, 0.0, 0.20]
DEFAULT_LOAD_UNLOAD_PLACE_DESCEND_DZ_BASE_M = 0.15
DEFAULT_LOAD_UNLOAD_PRE_GRASP_NEG_X_DISTANCE_M = 0.03
DEFAULT_LOAD_UNLOAD_PRE_GRASP_LIFT_Z_M = 0.05
DEFAULT_LOAD_UNLOAD_TIMEOUT_SECONDS = 900.0

LOAD_UNLOAD_STAGE_TO_SEQUENCE: dict[str, list[str]] = {
    "pull": ["start_policy: Pull open the drawer"],
    "place": ["pick source tag 5 and place at drawer tag 6", "reset_robot after tag place"],
    "push": ["start_policy: Push close the drawer"],
    "all": [
        "start_policy: Pull open the drawer",
        "pick source tag 5 and place at drawer tag 6",
        "reset_robot after tag place",
        "start_policy: Push close the drawer",
    ],
}


def _int_seconds(value: Any, default: int) -> str:
    seconds = int(round(float(value)))
    if seconds <= 0:
        seconds = int(default)
    return str(seconds)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return parsed if parsed > 0 else int(default)


def _json_schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


ARM_SCHEMA = {"type": "string", "enum": ["left", "right"]}
RECIPE_NAME_SCHEMA = {
    "type": "string",
    "enum": recipe_names(),
    "default": "bearing_on_base",
}
OBJECT_REF_SCHEMA = {
    "type": "string",
    "enum": object_name_choices(),
    "description": "Object noun or alias from the current object-tag map.",
}
XYZ_SCHEMA = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 3,
    "maxItems": 3,
}
QUAT_XYZW_SCHEMA = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 4,
    "maxItems": 4,
}


MCP_CONTROL_TOOL_SCHEMAS: dict[str, ToolSchema] = {
    "get_skill_status": ToolSchema(
        name="get_skill_status",
        description="Get mcp_control_demo deterministic skill API status.",
        parameters=_json_schema(),
        returns={},
    ),
    "reset_robot": ToolSchema(
        name="reset_robot",
        description="Reset the real robot through RuleControlTask: grippers first, then arms/head/waist.",
        parameters=_json_schema(),
        returns={},
    ),
    "get_eef_pose": ToolSchema(
        name="get_eef_pose",
        description="Read the current left/right gripper-center TCP pose and wrist/link7 pose.",
        parameters=_json_schema(
            {
                "arm": ARM_SCHEMA,
            },
            ["arm"],
        ),
        returns={},
    ),
    "detect_tags": ToolSchema(
        name="detect_tags",
        description="Refresh AprilTag detections from the current CoRobot observation.",
        parameters=_json_schema(),
        returns={},
    ),
    "get_apriltag_pose": ToolSchema(
        name="get_apriltag_pose",
        description="Get one AprilTag pose in the configured camera frame by tag_id.",
        parameters=_json_schema(
            {
                "tag_id": {"type": "integer"},
                "allow_stale": {"type": "boolean", "default": False},
            },
            ["tag_id"],
        ),
        returns={},
    ),
    "move_eef": ToolSchema(
        name="move_eef",
        description=(
            "Move a left/right gripper-center TCP target in camera coordinates. "
            "The controller builds fixed-30Hz wrist/link7 EEF_ABS actions."
        ),
        parameters=_json_schema(
            {
                "arm": ARM_SCHEMA,
                "target_position_camera_m": XYZ_SCHEMA,
                "target_orientation_camera_xyzw": QUAT_XYZW_SCHEMA,
                "duration_s": {"type": "number", "default": 1.0},
                "gripper_value": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            ["arm", "target_position_camera_m"],
        ),
        returns={},
    ),
    "lift_eef": ToolSchema(
        name="lift_eef",
        description="Lift a left/right EEF along calibration.camera_lift_axis at fixed 30Hz.",
        parameters=_json_schema(
            {
                "arm": ARM_SCHEMA,
                "distance_m": {"type": "number"},
                "duration_s": {"type": "number", "default": 1.0},
            },
            ["arm", "distance_m"],
        ),
        returns={},
    ),
    "place_down": ToolSchema(
        name="place_down",
        description="Move an EEF down along calibration.camera_place_down_axis and optionally open the gripper.",
        parameters=_json_schema(
            {
                "arm": ARM_SCHEMA,
                "down_distance_m": {"type": "number"},
                "duration_s": {"type": "number", "default": 1.0},
                "open_after_down": {"type": "boolean", "default": True},
            },
            ["arm", "down_distance_m"],
        ),
        returns={},
    ),
    "open_gripper": ToolSchema(
        name="open_gripper",
        description="Open the selected left/right gripper through /skill/gripper at fixed 30Hz.",
        parameters=_json_schema(
            {
                "arm": ARM_SCHEMA,
                "duration_s": {"type": "number", "default": 0.5},
            },
            ["arm"],
        ),
        returns={},
    ),
    "close_gripper": ToolSchema(
        name="close_gripper",
        description="Close the selected left/right gripper through /skill/gripper at fixed 30Hz.",
        parameters=_json_schema(
            {
                "arm": ARM_SCHEMA,
                "duration_s": {"type": "number", "default": 0.5},
            },
            ["arm"],
        ),
        returns={},
    ),
    "switch_scene": ToolSchema(
        name="switch_scene",
        description=(
            "Press the configured scene-switch button by AprilTag. Defaults are centralized "
            "in mcp_control_recipes.DEFAULT_SWITCH_SCENE_RECIPE; call with no arguments "
            "unless the operator explicitly overrides the recipe."
        ),
        parameters=_json_schema(
            {
                "arm": {**ARM_SCHEMA, "default": DEFAULT_SWITCH_SCENE_RECIPE.arm},
                "button_tag_id": {
                    "type": "integer",
                    "default": DEFAULT_SWITCH_SCENE_RECIPE.button_tag_id,
                },
                "base_offset_m": {
                    **XYZ_SCHEMA,
                    "default": list(DEFAULT_SWITCH_SCENE_RECIPE.base_offset_m),
                },
                "move_duration_s": {
                    "type": "number",
                    "default": DEFAULT_SWITCH_SCENE_RECIPE.move_duration_s,
                },
                "gripper_duration_s": {
                    "type": "number",
                    "default": DEFAULT_SWITCH_SCENE_RECIPE.gripper_duration_s,
                },
                "press_interval_s": {
                    "type": "number",
                    "default": DEFAULT_SWITCH_SCENE_RECIPE.press_interval_s,
                },
            },
        ),
        returns={},
    ),
}


MCP_COMPOSITE_TOOL_SCHEMAS: dict[str, ToolSchema] = {
    "prepare_tag_pick_place": ToolSchema(
        name="prepare_tag_pick_place",
        description=(
            "Prepare an AprilTag pick-and-place task in one deterministic step: refresh tag "
            "detections, map source/destination object nouns to tag ids, read fresh tag poses, "
            "resolve the object-pair recipe, and compute grasp/place targets. This tool does "
            "not move the robot. Prefer source_object/destination_object over raw tag ids."
        ),
        parameters=_json_schema(
            {
                "source_object": OBJECT_REF_SCHEMA,
                "destination_object": OBJECT_REF_SCHEMA,
                "relation": {
                    "type": "string",
                    "enum": ["on", "inside", "assembly", "sorting"],
                    "description": "Requested spatial relation between source and destination objects.",
                },
                "recipe_name": RECIPE_NAME_SCHEMA,
                "retry_detection": {"type": "boolean", "default": True},
            },
            ["source_object", "destination_object", "relation"],
        ),
        returns={
            "object_mapping": "current noun-to-tag mapping",
            "recipe": "canonical recipe parameters",
            "source_pose": "fresh detected source tag pose",
            "destination_pose": "fresh detected destination tag pose",
            "grasp_targets": "approach/grasp/lift camera-frame targets",
            "place_targets": "place/hover camera-frame targets",
        },
    ),
    "resolve_tag_pick_place_recipe": ToolSchema(
        name="resolve_tag_pick_place_recipe",
        description=(
            "Resolve the canonical AprilTag pick-and-place recipe from object nouns and relation. "
            "Use this before computing grasp/place targets so numeric offsets come from code. "
            "Prefer source_object/destination_object over raw tag ids."
        ),
        parameters=_json_schema(
            {
                "source_object": OBJECT_REF_SCHEMA,
                "destination_object": OBJECT_REF_SCHEMA,
                "relation": {
                    "type": "string",
                    "enum": ["on", "inside", "assembly", "sorting"],
                    "description": "Requested spatial relation between source and destination objects.",
                },
                "recipe_name": RECIPE_NAME_SCHEMA,
            },
        ),
        returns={
            "object_mapping": "current noun-to-tag mapping",
            "recipe_name": "canonical recipe name",
            "source_base_offset_m": "base_link offset for source grasp target",
            "place_base_offset_m": "base_link offset for destination placement target",
            "approach_distance_m": "camera approach distance",
            "lift_height_m": "base_link lift height",
            "hover_height_m": "base_link hover height",
        },
    ),
    "compute_tag_grasp_targets": ToolSchema(
        name="compute_tag_grasp_targets",
        description=(
            "Compute approach/grasp/lift camera-frame targets from an AprilTag pose and a "
            "recipe-derived base_link grasp offset. This tool does not move the robot."
        ),
        parameters=_json_schema(
            {
                "tag_id": {"type": "integer", "default": 0},
                "tag_pose": {
                    "type": "object",
                    "description": "The data object returned by get_apriltag_pose.",
                },
                "recipe_name": RECIPE_NAME_SCHEMA,
            },
            ["tag_pose"],
        ),
        returns={
            "approach_camera_m": "camera-frame gripper-center TCP approach target",
            "grasp_camera_m": "camera-frame gripper-center TCP grasp target",
            "lift_camera_m": "camera-frame gripper-center TCP lift target",
        },
    ),
    "compute_tag_place_targets": ToolSchema(
        name="compute_tag_place_targets",
        description=(
            "Compute destination place/hover camera-frame targets from an AprilTag pose and a "
            "base_link placement offset. This tool does not move the robot."
        ),
        parameters=_json_schema(
            {
                "tag_id": {"type": "integer"},
                "tag_pose": {
                    "type": "object",
                    "description": "The data object returned by get_apriltag_pose for the destination tag.",
                },
                "recipe_name": RECIPE_NAME_SCHEMA,
            },
            ["tag_pose"],
        ),
        returns={
            "place_camera_m": "camera-frame gripper-center TCP placement target",
            "place_hover_camera_m": "camera-frame gripper-center TCP hover target above placement",
        },
    ),
    "SenseEnvironment": ToolSchema(
        name="SenseEnvironment",
        description="Read mcp_control robot/tool status for post-action verification.",
        parameters=_json_schema(
            {
                "include_tags": {"type": "boolean", "default": False},
            }
        ),
        returns={
            "robot_state": "CoRobot skill status summary",
            "safety_status": "basic tool-level safety status",
        },
    ),
    "load_workpiece_to_drawer": ToolSchema(
        name="load_workpiece_to_drawer",
        description=(
            "Run the full fixed drawer-magazine loading task in one tool call: use the VLA policy to pull open "
            "the drawer, pick the workpiece at source tag 5 and place it at drawer tag 6, "
            "reset the robot, then use the VLA policy to push close the drawer. The VLA "
            "prompts are fixed in code and should not be generated by the LLM. For visual demos, prefer the "
            "three stage tools open_drawer_for_loading -> place_workpiece_in_drawer -> close_drawer_after_loading."
        ),
        parameters=_json_schema(
            {
                "arm": {**ARM_SCHEMA, "default": DEFAULT_LOAD_UNLOAD_ARM},
                "source_tag_id": {"type": "integer", "default": DEFAULT_LOAD_UNLOAD_SOURCE_TAG_ID},
                "destination_tag_id": {"type": "integer", "default": DEFAULT_LOAD_UNLOAD_DEST_TAG_ID},
                "pull_policy_chunk_count": {
                    "type": "integer",
                    "default": DEFAULT_LOAD_UNLOAD_PULL_POLICY_CHUNK_COUNT,
                },
                "push_policy_chunk_count": {
                    "type": "integer",
                    "default": DEFAULT_LOAD_UNLOAD_PUSH_POLICY_CHUNK_COUNT,
                },
                "policy_timeout_s": {"type": "number", "default": DEFAULT_LOAD_UNLOAD_POLICY_TIMEOUT_S},
                "execute_pick_place": {"type": "boolean", "default": True},
            }
        ),
        returns={
            "sequence": "fixed drawer loading stages",
            "stdout_tail": "tail of the referenced script stdout",
            "stderr_tail": "tail of the referenced script stderr",
            "script_path": "referenced script path",
        },
    ),
    "open_drawer_for_loading": ToolSchema(
        name="open_drawer_for_loading",
        description=(
            "Stage 1 of drawer-magazine loading. Run the fixed VLA policy prompt "
            "'Pull open the drawer' and wait for it to finish. Workpiece/drawer tags are "
            "centralized in the recipe map: 工件/workpiece -> tag 5, 抽屉式料仓/drawer_magazine -> tag 6."
        ),
        parameters=_json_schema(
            {
                "arm": {**ARM_SCHEMA, "default": DEFAULT_LOAD_UNLOAD_ARM},
                "pull_policy_chunk_count": {
                    "type": "integer",
                    "default": DEFAULT_LOAD_UNLOAD_PULL_POLICY_CHUNK_COUNT,
                },
                "policy_timeout_s": {"type": "number", "default": DEFAULT_LOAD_UNLOAD_POLICY_TIMEOUT_S},
            }
        ),
        returns={"sequence": "drawer opening stage result"},
    ),
    "place_workpiece_in_drawer": ToolSchema(
        name="place_workpiece_in_drawer",
        description=(
            "Stage 2/3 of drawer-magazine loading. Pick the workpiece at tag 5, place it at drawer tag 6, "
            "release it, then reset the robot before drawer closing. Uses the validated offsets from "
            "scripts/test_load_unload.sh."
        ),
        parameters=_json_schema(
            {
                "arm": {**ARM_SCHEMA, "default": DEFAULT_LOAD_UNLOAD_ARM},
                "source_tag_id": {"type": "integer", "default": DEFAULT_LOAD_UNLOAD_SOURCE_TAG_ID},
                "destination_tag_id": {"type": "integer", "default": DEFAULT_LOAD_UNLOAD_DEST_TAG_ID},
                "execute_pick_place": {"type": "boolean", "default": True},
            }
        ),
        returns={"sequence": "workpiece placement and reset stage result"},
    ),
    "close_drawer_after_loading": ToolSchema(
        name="close_drawer_after_loading",
        description=(
            "Final stage of drawer-magazine loading. Run the fixed VLA policy prompt "
            "'Push close the drawer' and wait for it to finish."
        ),
        parameters=_json_schema(
            {
                "arm": {**ARM_SCHEMA, "default": DEFAULT_LOAD_UNLOAD_ARM},
                "push_policy_chunk_count": {
                    "type": "integer",
                    "default": DEFAULT_LOAD_UNLOAD_PUSH_POLICY_CHUNK_COUNT,
                },
                "policy_timeout_s": {"type": "number", "default": DEFAULT_LOAD_UNLOAD_POLICY_TIMEOUT_S},
            }
        ),
        returns={"sequence": "drawer closing stage result"},
    ),
}


class McpControlRecipeState:
    """Track recipe-critical targets so the LLM cannot skip required motion."""

    def __init__(self) -> None:
        self.expected_grasp_target_camera_m: list[float] | None = None
        self.expected_lift_target_camera_m: list[float] | None = None
        self.expected_grasp_tag_id: int | None = None
        self.last_move_target_by_arm: dict[str, list[float]] = {}

    def set_expected_grasp(
        self,
        *,
        tag_id: int,
        grasp_camera_m: list[float],
        lift_camera_m: list[float],
    ) -> None:
        self.expected_grasp_tag_id = int(tag_id)
        self.expected_grasp_target_camera_m = [float(v) for v in grasp_camera_m]
        self.expected_lift_target_camera_m = [float(v) for v in lift_camera_m]

    def record_move(self, *, arm: str, target_camera_m: list[float]) -> None:
        self.last_move_target_by_arm[arm] = [float(v) for v in target_camera_m]

    def clear_expected_grasp(self) -> None:
        self.expected_grasp_target_camera_m = None
        self.expected_lift_target_camera_m = None
        self.expected_grasp_tag_id = None

    def validate_move_eef(
        self,
        *,
        arm: str,
        target_camera_m: list[float],
    ) -> ToolResult | None:
        expected_grasp = self.expected_grasp_target_camera_m
        expected_lift = self.expected_lift_target_camera_m
        if expected_grasp is None or expected_lift is None:
            return None
        if not _vectors_close(target_camera_m, expected_lift, GRASP_TARGET_TOLERANCE_M):
            return None

        return ToolResult(
            status=ToolStatus.FAILED,
            message=(
                "move_eef blocked: this target matches lift_camera_m, but the gripper has "
                "not closed at grasp_camera_m yet. Move to grasp_camera_m and close first."
            ),
            data={
                "error_type": "recipe_order_violation",
                "arm": arm,
                "expected_tag_id": self.expected_grasp_tag_id,
                "blocked_target_camera_m": target_camera_m,
                "required_grasp_camera_m": expected_grasp,
                "required_close_tool": "close_gripper",
                "tolerance_m": GRASP_TARGET_TOLERANCE_M,
                "required_next_action": {
                    "tool": "move_eef",
                    "arguments": {
                        "arm": arm,
                        "target_position_camera_m": expected_grasp,
                    },
                },
            },
            tool_name="move_eef",
        )

    def validate_close_gripper(self, *, arm: str) -> ToolResult | None:
        expected = self.expected_grasp_target_camera_m
        if expected is None:
            return None

        last_target = self.last_move_target_by_arm.get(arm)
        if last_target is not None and _vectors_close(last_target, expected, GRASP_TARGET_TOLERANCE_M):
            return None

        return ToolResult(
            status=ToolStatus.FAILED,
            message=(
                "close_gripper blocked: the selected arm has not moved to grasp_camera_m. "
                "Call move_eef with the required_grasp_camera_m target before closing."
            ),
            data={
                "error_type": "recipe_order_violation",
                "arm": arm,
                "expected_tag_id": self.expected_grasp_tag_id,
                "required_previous_tool": "move_eef",
                "required_grasp_camera_m": expected,
                "last_move_target_camera_m": last_target,
                "tolerance_m": GRASP_TARGET_TOLERANCE_M,
                "required_next_action": {
                    "tool": "move_eef",
                    "arguments": {
                        "arm": arm,
                        "target_position_camera_m": expected,
                    },
                },
            },
            tool_name="close_gripper",
        )


class McpControlAgentTool(BaseTool):
    """BaseTool wrapper around one mcp_control_demo /skill endpoint."""

    def __init__(
        self,
        name: str,
        *,
        base_url: str = DEFAULT_COROBOT_BASE_URL,
        timeout_seconds: float = 180.0,
        recipe_state: McpControlRecipeState | None = None,
    ) -> None:
        if name not in SKILL_TOOL_ENDPOINTS:
            raise ValueError(f"unsupported mcp_control tool: {name}")
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.recipe_state = recipe_state
        self.description = MCP_CONTROL_TOOL_SCHEMAS[name].description

    def get_schema(self) -> ToolSchema:
        return MCP_CONTROL_TOOL_SCHEMAS[self.name]

    async def execute(self, **kwargs: Any) -> ToolResult:
        payload = dict(kwargs or {})
        if "control_hz" in payload or "control_frequency_hz" in payload:
            return ToolResult(
                status=ToolStatus.FAILED,
                message="control frequency is fixed at 30Hz and cannot be overridden",
                data={"error_type": "invalid_argument", "payload": payload},
                tool_name=self.name,
            )

        if self.name == "open_gripper":
            payload["gripper_value"] = 0.0
        elif self.name == "close_gripper":
            if self.recipe_state is not None:
                arm = str(payload.get("arm") or "")
                order_error = self.recipe_state.validate_close_gripper(arm=arm)
                if order_error is not None:
                    return order_error
            payload["gripper_value"] = 1.0
        elif self.name == "move_eef" and self.recipe_state is not None:
            target = payload.get("target_position_camera_m")
            if isinstance(target, list) and len(target) == 3:
                order_error = self.recipe_state.validate_move_eef(
                    arm=str(payload.get("arm") or ""),
                    target_camera_m=_vector(target, 3),
                )
                if order_error is not None:
                    return order_error
        elif self.name == "switch_scene":
            payload.update({**DEFAULT_SWITCH_SCENE_RECIPE.to_tool_defaults(), **payload})

        _drop_fixed_skill_arguments(payload)

        method, path = SKILL_TOOL_ENDPOINTS[self.name]
        result = await asyncio.to_thread(self._request, method, path, payload)

        if result.success and self.recipe_state is not None:
            if self.name == "move_eef":
                arm = str(payload.get("arm") or "")
                target = payload.get("target_position_camera_m")
                if arm and isinstance(target, list) and len(target) == 3:
                    self.recipe_state.record_move(
                        arm=arm,
                        target_camera_m=_vector(target, 3),
                    )
            elif self.name == "close_gripper":
                self.recipe_state.clear_expected_grasp()

        return result

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> ToolResult:
        url = f"{self.base_url}{path}"
        body = None if method == "GET" else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                http_status = int(response.status)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            parsed = _try_parse_json(raw)
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"CoRobot HTTP {exc.code} for {self.name}",
                data={
                    "error_type": "http_error",
                    "http_status": exc.code,
                    "url": url,
                    "payload": payload,
                    "response": parsed if parsed is not None else raw,
                },
                raw_output=raw,
                tool_name=self.name,
            )
        except urllib.error.URLError as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"failed to reach CoRobot at {self.base_url}: {exc.reason}",
                data={
                    "error_type": "connection_error",
                    "base_url": self.base_url,
                    "url": url,
                    "payload": payload,
                    "error": str(exc.reason),
                },
                tool_name=self.name,
            )
        except Exception as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"failed to call CoRobot skill API: {exc}",
                data={
                    "error_type": "exception",
                    "base_url": self.base_url,
                    "url": url,
                    "payload": payload,
                    "error": str(exc),
                },
                tool_name=self.name,
            )

        parsed = _try_parse_json(raw)
        if parsed is None:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"CoRobot response for {self.name} is not JSON",
                data={"error_type": "invalid_json", "http_status": http_status, "url": url},
                raw_output=raw,
                tool_name=self.name,
            )

        status, message, data = _normalize_corobot_response(parsed)
        data.setdefault("_http_status", http_status)
        data.setdefault("_url", url)
        data.setdefault("_request_payload", payload)
        return ToolResult(
            status=status,
            message=message or f"{self.name} completed",
            data=data,
            raw_output=raw,
            tool_name=self.name,
        )


class ResolveTagPickPlaceRecipeTool(BaseTool):
    """Return canonical recipe parameters for an AprilTag pick-and-place task."""

    name = "resolve_tag_pick_place_recipe"
    description = MCP_COMPOSITE_TOOL_SCHEMAS[name].description

    def get_schema(self) -> ToolSchema:
        return MCP_COMPOSITE_TOOL_SCHEMAS[self.name]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            resolution = {}
            has_object_ref = any(
                kwargs.get(key) is not None
                for key in (
                    "source_object",
                    "destination_object",
                    "source_name",
                    "destination_name",
                    "source_tag_id",
                    "destination_tag_id",
                )
            )
            if has_object_ref:
                resolution = resolve_pick_place_request(
                    source_object=kwargs.get("source_object") or kwargs.get("source_name"),
                    destination_object=kwargs.get("destination_object") or kwargs.get("destination_name"),
                    source_tag_id=kwargs.get("source_tag_id"),
                    destination_tag_id=kwargs.get("destination_tag_id"),
                )
            recipe = get_tag_pick_place_recipe(
                kwargs.get("recipe_name"),
                relation=kwargs.get("relation"),
                source_object=resolution.get("source_object") or kwargs.get("source_object"),
                destination_object=resolution.get("destination_object") or kwargs.get("destination_object"),
                source_tag_id=resolution.get("source_tag_id") or kwargs.get("source_tag_id"),
                destination_tag_id=resolution.get("destination_tag_id") or kwargs.get("destination_tag_id"),
            )
        except ValueError as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=str(exc),
                data={"error_type": "invalid_recipe", "args": kwargs},
                tool_name=self.name,
            )

        data = recipe.to_dict()
        data["object_mapping"] = object_mapping()
        data.update(resolution)
        if "source_object" in kwargs:
            data["requested_source_object"] = kwargs["source_object"]
        if "destination_object" in kwargs:
            data["requested_destination_object"] = kwargs["destination_object"]
        for key in ("source_tag_id", "destination_tag_id", "relation"):
            if key in kwargs and key not in data:
                data[key] = kwargs[key]
        return ToolResult(
            status=ToolStatus.SUCCESS,
            message=f"resolved mcp_control recipe {recipe.name}",
            data=data,
            tool_name=self.name,
        )


class PrepareTagPickPlaceTool(BaseTool):
    """Composite preparation tool for AprilTag pick-and-place tasks."""

    name = "prepare_tag_pick_place"
    description = MCP_COMPOSITE_TOOL_SCHEMAS[name].description

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_COROBOT_BASE_URL,
        timeout_seconds: float = 180.0,
        calibration_path: str | Path = DEFAULT_CALIBRATION_PATH,
        recipe_state: McpControlRecipeState | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = float(timeout_seconds)
        self.calibration_path = str(calibration_path)
        self.recipe_state = recipe_state

    def get_schema(self) -> ToolSchema:
        return MCP_COMPOSITE_TOOL_SCHEMAS[self.name]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            resolution = resolve_pick_place_request(
                source_object=kwargs.get("source_object") or kwargs.get("source_name"),
                destination_object=kwargs.get("destination_object") or kwargs.get("destination_name"),
                source_tag_id=kwargs.get("source_tag_id"),
                destination_tag_id=kwargs.get("destination_tag_id"),
            )
            source_tag_id = int(resolution["source_tag_id"])
            destination_tag_id = int(resolution["destination_tag_id"])
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=str(exc),
                data={"error_type": "invalid_argument", "args": kwargs},
                tool_name=self.name,
            )

        try:
            recipe = get_tag_pick_place_recipe(
                kwargs.get("recipe_name"),
                relation=kwargs.get("relation"),
                source_object=resolution.get("source_object"),
                destination_object=resolution.get("destination_object"),
                source_tag_id=source_tag_id,
                destination_tag_id=destination_tag_id,
            )
        except ValueError as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=str(exc),
                data={"error_type": "invalid_recipe", "args": kwargs},
                tool_name=self.name,
            )

        detection = await self._detect_tags()
        if not detection.success and bool(kwargs.get("retry_detection", True)):
            detection = await self._detect_tags()
        if not detection.success:
            return detection

        detections = detection.data.get("detections") or []
        source_pose = _find_detected_tag(detections, source_tag_id)
        destination_pose = _find_detected_tag(detections, destination_tag_id)

        if (source_pose is None or destination_pose is None) and bool(kwargs.get("retry_detection", True)):
            detection = await self._detect_tags()
            if not detection.success:
                return detection
            detections = detection.data.get("detections") or []
            source_pose = _find_detected_tag(detections, source_tag_id)
            destination_pose = _find_detected_tag(detections, destination_tag_id)

        missing = []
        if source_pose is None:
            missing.append(source_tag_id)
        if destination_pose is None:
            missing.append(destination_tag_id)
        if missing:
            visible = sorted(
                int(item["tag_id"])
                for item in detections
                if isinstance(item, dict) and item.get("tag_id") is not None
            )
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"required tag ids are not visible after fresh detection: {missing}",
                data={
                    "error_type": "tag_not_visible",
                    "missing_tag_ids": missing,
                    "visible_tag_ids": visible,
                    "object_mapping": object_mapping(),
                    **resolution,
                    "source_tag_id": source_tag_id,
                    "destination_tag_id": destination_tag_id,
                    "detections": detections,
                },
                tool_name=self.name,
            )

        grasp_targets = build_tag_grasp_targets(
            calibration_path=self.calibration_path,
            tag=source_pose,
            base_offset_m=list(recipe.source_base_offset_m),
            approach_distance_m=recipe.approach_distance_m,
            lift_height_m=recipe.lift_height_m,
        )
        place_targets = build_tag_place_targets(
            calibration_path=self.calibration_path,
            tag=destination_pose,
            base_offset_m=list(recipe.place_base_offset_m),
            hover_height_m=recipe.hover_height_m,
        )

        if self.recipe_state is not None:
            self.recipe_state.set_expected_grasp(
                tag_id=source_tag_id,
                grasp_camera_m=grasp_targets["grasp_camera_m"],
                lift_camera_m=grasp_targets["lift_camera_m"],
            )

        return ToolResult(
            status=ToolStatus.SUCCESS,
            message=(
                f"prepared tag pick-and-place: source {resolution.get('source_display_name') or source_tag_id}, "
                f"destination {resolution.get('destination_display_name') or destination_tag_id}, "
                f"recipe {recipe.name}"
            ),
            data={
                "object_mapping": object_mapping(),
                **resolution,
                "source_tag_id": source_tag_id,
                "destination_tag_id": destination_tag_id,
                "relation": kwargs.get("relation"),
                "recipe": recipe.to_dict(),
                "recipe_name": recipe.name,
                "source_pose": source_pose,
                "destination_pose": destination_pose,
                "grasp_targets": grasp_targets,
                "place_targets": place_targets,
                "recommended_grasp_batch": [
                    {"tool": "open_gripper", "arguments": {"arm": "right"}},
                    {
                        "tool": "move_eef",
                        "arguments": {
                            "arm": "right",
                            "target_position_camera_m": grasp_targets["approach_camera_m"],
                        },
                    },
                    {
                        "tool": "move_eef",
                        "arguments": {
                            "arm": "right",
                            "target_position_camera_m": grasp_targets["grasp_camera_m"],
                        },
                    },
                    {"tool": "close_gripper", "arguments": {"arm": "right"}},
                    {
                        "tool": "move_eef",
                        "arguments": {
                            "arm": "right",
                            "target_position_camera_m": grasp_targets["lift_camera_m"],
                        },
                    },
                ],
                "recommended_place_batch": [
                    {
                        "tool": "move_eef",
                        "arguments": {
                            "arm": "right",
                            "target_position_camera_m": place_targets["place_hover_camera_m"],
                        },
                    },
                    {
                        "tool": "move_eef",
                        "arguments": {
                            "arm": "right",
                            "target_position_camera_m": place_targets["place_camera_m"],
                        },
                    },
                    {"tool": "open_gripper", "arguments": {"arm": "right"}},
                ],
                "detections": detections,
            },
            tool_name=self.name,
        )

    async def _detect_tags(self) -> ToolResult:
        return await McpControlAgentTool(
            "detect_tags",
            base_url=self.base_url,
            timeout_seconds=self.timeout_seconds,
        ).execute()


class ComputeTagGraspTargetsTool(BaseTool):
    """Pure computation tool for tag grasp targets; it does not move the robot."""

    name = "compute_tag_grasp_targets"
    description = MCP_COMPOSITE_TOOL_SCHEMAS[name].description

    def __init__(
        self,
        *,
        calibration_path: str | Path = DEFAULT_CALIBRATION_PATH,
        recipe_state: McpControlRecipeState | None = None,
    ) -> None:
        self.calibration_path = str(calibration_path)
        self.recipe_state = recipe_state

    def get_schema(self) -> ToolSchema:
        return MCP_COMPOSITE_TOOL_SCHEMAS[self.name]

    async def execute(self, **kwargs: Any) -> ToolResult:
        tag_id = int(kwargs.get("tag_id", 0))
        tag_pose = kwargs.get("tag_pose")
        if not isinstance(tag_pose, dict):
            return ToolResult(
                status=ToolStatus.FAILED,
                message="tag_pose must be the data object returned by get_apriltag_pose",
                data={"error_type": "invalid_argument"},
                tool_name=self.name,
            )
        try:
            recipe = get_tag_pick_place_recipe(kwargs.get("recipe_name"))
        except ValueError as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=str(exc),
                data={"error_type": "invalid_recipe", "args": kwargs},
                tool_name=self.name,
            )
        base_offset_m = _vector(kwargs.get("base_offset_m", recipe.source_base_offset_m), 3)
        approach_distance_m = float(kwargs.get("approach_distance_m", recipe.approach_distance_m))
        lift_height_m = float(kwargs.get("lift_height_m", recipe.lift_height_m))

        targets = build_tag_grasp_targets(
            calibration_path=self.calibration_path,
            tag=tag_pose,
            base_offset_m=base_offset_m,
            approach_distance_m=approach_distance_m,
            lift_height_m=lift_height_m,
        )
        if self.recipe_state is not None:
            self.recipe_state.set_expected_grasp(
                tag_id=tag_id,
                grasp_camera_m=targets["grasp_camera_m"],
                lift_camera_m=targets["lift_camera_m"],
            )
        return ToolResult(
            status=ToolStatus.SUCCESS,
            message=f"computed tag {tag_id} grasp targets",
            data={
                "tag_id": tag_id,
                "recipe_name": recipe.name,
                "base_offset_m": base_offset_m,
                "approach_distance_m": approach_distance_m,
                "lift_height_m": lift_height_m,
                **targets,
            },
            tool_name=self.name,
        )


class ComputeTagPlaceTargetsTool(BaseTool):
    """Pure computation tool for tag placement targets; it does not move the robot."""

    name = "compute_tag_place_targets"
    description = MCP_COMPOSITE_TOOL_SCHEMAS[name].description

    def __init__(
        self,
        *,
        calibration_path: str | Path = DEFAULT_CALIBRATION_PATH,
    ) -> None:
        self.calibration_path = str(calibration_path)

    def get_schema(self) -> ToolSchema:
        return MCP_COMPOSITE_TOOL_SCHEMAS[self.name]

    async def execute(self, **kwargs: Any) -> ToolResult:
        tag_id = int(kwargs.get("tag_id", 0))
        tag_pose = kwargs.get("tag_pose")
        if not isinstance(tag_pose, dict):
            return ToolResult(
                status=ToolStatus.FAILED,
                message="tag_pose must be the data object returned by get_apriltag_pose",
                data={"error_type": "invalid_argument"},
                tool_name=self.name,
            )
        try:
            recipe = get_tag_pick_place_recipe(kwargs.get("recipe_name"))
        except ValueError as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=str(exc),
                data={"error_type": "invalid_recipe", "args": kwargs},
                tool_name=self.name,
            )
        base_offset_m = _vector(kwargs.get("base_offset_m", recipe.place_base_offset_m), 3)
        hover_height_m = float(kwargs.get("hover_height_m", recipe.hover_height_m))

        targets = build_tag_place_targets(
            calibration_path=self.calibration_path,
            tag=tag_pose,
            base_offset_m=base_offset_m,
            hover_height_m=hover_height_m,
        )
        return ToolResult(
            status=ToolStatus.SUCCESS,
            message=f"computed tag {tag_id} place targets",
            data={
                "tag_id": tag_id,
                "recipe_name": recipe.name,
                "base_offset_m": base_offset_m,
                "hover_height_m": hover_height_m,
                **targets,
            },
            tool_name=self.name,
        )


class McpSenseEnvironmentTool(BaseTool):
    """Minimal perception/status tool for the generic ReAct loop."""

    name = "SenseEnvironment"
    description = MCP_COMPOSITE_TOOL_SCHEMAS[name].description

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_COROBOT_BASE_URL,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = float(timeout_seconds)

    def get_schema(self) -> ToolSchema:
        return MCP_COMPOSITE_TOOL_SCHEMAS[self.name]

    async def execute(self, **kwargs: Any) -> ToolResult:
        status = await McpControlAgentTool(
            "get_skill_status",
            base_url=self.base_url,
            timeout_seconds=self.timeout_seconds,
        ).execute()
        tags = None
        if bool(kwargs.get("include_tags", False)):
            tags = await McpControlAgentTool(
                "detect_tags",
                base_url=self.base_url,
                timeout_seconds=self.timeout_seconds,
            ).execute()
        if not status.success:
            return status
        return ToolResult(
            status=ToolStatus.SUCCESS,
            message="mcp_control environment status read",
            data={
                "robot_state": {
                    "environment_ready": status.data.get("environment_ready"),
                    "initialized": status.data.get("initialized"),
                    "running": status.data.get("running"),
                    "calibration": status.data.get("calibration"),
                },
                "safety_status": {"is_safe": True, "issues": []},
                "task_progress": {"overall_completion": None, "status": "unknown"},
                "detections": None if tags is None else tags.data.get("detections"),
                "raw": {"status": status.data, "tags": None if tags is None else tags.data},
            },
            tool_name=self.name,
        )


class LoadWorkpieceToDrawerTool(BaseTool):
    """Composite tool that delegates the fixed drawer loading demo to the validated script."""

    name = "load_workpiece_to_drawer"
    description = MCP_COMPOSITE_TOOL_SCHEMAS[name].description
    stage = "all"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_COROBOT_BASE_URL,
        timeout_seconds: float = DEFAULT_LOAD_UNLOAD_TIMEOUT_SECONDS,
        script_path: str | Path = DEFAULT_LOAD_UNLOAD_SCRIPT_PATH,
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = max(float(timeout_seconds), DEFAULT_LOAD_UNLOAD_TIMEOUT_SECONDS)
        self.script_path = Path(script_path)

    def get_schema(self) -> ToolSchema:
        return MCP_COMPOSITE_TOOL_SCHEMAS[self.name]

    async def execute(self, **kwargs: Any) -> ToolResult:
        if not self.script_path.exists():
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"load/unload script not found: {self.script_path}",
                data={"error_type": "script_not_found", "script_path": str(self.script_path)},
                tool_name=self.name,
            )

        try:
            env = self._build_env(kwargs, stage=self.stage)
        except (TypeError, ValueError) as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=str(exc),
                data={"error_type": "invalid_argument", "args": kwargs},
                tool_name=self.name,
            )

        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                ["bash", str(self.script_path)],
                cwd=str(self.script_path.resolve().parents[1]),
                env=env,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"load_workpiece_to_drawer timed out after {self.timeout_seconds:.0f}s",
                data={
                    "error_type": "timeout",
                    "timeout_seconds": self.timeout_seconds,
                    "script_path": str(self.script_path),
                    "stdout_tail": _tail_text(exc.stdout),
                    "stderr_tail": _tail_text(exc.stderr),
                },
                tool_name=self.name,
            )
        except Exception as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"failed to run load_workpiece_to_drawer script: {exc}",
                data={
                    "error_type": "exception",
                    "script_path": str(self.script_path),
                    "error": str(exc),
                },
                tool_name=self.name,
            )

        success = completed.returncode == 0
        parameters = {
            "arm": env["ARM"],
            "source_tag_id": int(env["SOURCE_TAG_ID"]),
            "destination_tag_id": int(env["DEST_TAG_ID"]),
            "grasp_offset_base_m": [
                float(env["GRASP_OFFSET_X"]),
                float(env["GRASP_OFFSET_Y"]),
                float(env["GRASP_OFFSET_Z"]),
            ],
            "place_lift_offset_base_m": [
                float(env["PLACE_OFFSET_X"]),
                float(env["PLACE_OFFSET_Y"]),
                float(env["PLACE_OFFSET_Z"]),
            ],
            "place_descend_dz_base_m": float(env["PLACE_DESCEND_DZ_BASE_M"]),
            "pull_policy_chunk_count": int(env["PULL_CHUNK_COUNT"]),
            "push_policy_chunk_count": int(env["PUSH_CHUNK_COUNT"]),
            "policy_timeout_s": float(env["POLICY_TIMEOUT_S"]),
            "pull_policy_port": int(env["PULL_POLICY_PORT"]),
            "push_policy_port": int(env["PUSH_POLICY_PORT"]),
        }
        return ToolResult(
            status=ToolStatus.SUCCESS if success else ToolStatus.FAILED,
            message=(
                "load_workpiece_to_drawer completed"
                if success
                else f"load_workpiece_to_drawer failed with exit code {completed.returncode}"
            ),
            data={
                "script_path": str(self.script_path),
                "returncode": completed.returncode,
                "stage": self.stage,
                "sequence": LOAD_UNLOAD_STAGE_TO_SEQUENCE[self.stage],
                "fixed_vla_prompts": {
                    "pull": DEFAULT_LOAD_UNLOAD_PULL_PROMPT,
                    "push": DEFAULT_LOAD_UNLOAD_PUSH_PROMPT,
                },
                "parameters": parameters,
                "stdout_tail": _tail_text(completed.stdout),
                "stderr_tail": _tail_text(completed.stderr),
            },
            raw_output=completed.stdout,
            tool_name=self.name,
        )

    def _build_env(self, kwargs: dict[str, Any], *, stage: str) -> dict[str, str]:
        env = os.environ.copy()
        grasp_offset = _vector(kwargs.get("grasp_offset_base_m", DEFAULT_LOAD_UNLOAD_GRASP_OFFSET_M), 3)
        place_lift_offset = _vector(
            kwargs.get("place_lift_offset_base_m", DEFAULT_LOAD_UNLOAD_PLACE_LIFT_OFFSET_M),
            3,
        )
        pull_chunk_count = _positive_int(
            kwargs.get("pull_policy_chunk_count", DEFAULT_LOAD_UNLOAD_PULL_POLICY_CHUNK_COUNT),
            DEFAULT_LOAD_UNLOAD_PULL_POLICY_CHUNK_COUNT,
        )
        push_chunk_count = _positive_int(
            kwargs.get("push_policy_chunk_count", DEFAULT_LOAD_UNLOAD_PUSH_POLICY_CHUNK_COUNT),
            DEFAULT_LOAD_UNLOAD_PUSH_POLICY_CHUNK_COUNT,
        )
        env.update(
            {
                "COROBOT_URL": self.base_url,
                "ARM": str(kwargs.get("arm") or DEFAULT_LOAD_UNLOAD_ARM),
                "PULL_PROMPT": DEFAULT_LOAD_UNLOAD_PULL_PROMPT,
                "PUSH_PROMPT": DEFAULT_LOAD_UNLOAD_PUSH_PROMPT,
                "PULL_POLICY_PORT": str(int(kwargs.get("pull_policy_port", DEFAULT_LOAD_UNLOAD_PULL_POLICY_PORT))),
                "PUSH_POLICY_PORT": str(int(kwargs.get("push_policy_port", DEFAULT_LOAD_UNLOAD_PUSH_POLICY_PORT))),
                "PULL_CHUNK_COUNT": str(pull_chunk_count),
                "PUSH_CHUNK_COUNT": str(push_chunk_count),
                "POLICY_TIMEOUT_S": _int_seconds(
                    kwargs.get("policy_timeout_s", DEFAULT_LOAD_UNLOAD_POLICY_TIMEOUT_S),
                    DEFAULT_LOAD_UNLOAD_POLICY_TIMEOUT_S,
                ),
                "SOURCE_TAG_ID": str(int(kwargs.get("source_tag_id", DEFAULT_LOAD_UNLOAD_SOURCE_TAG_ID))),
                "DEST_TAG_ID": str(int(kwargs.get("destination_tag_id", DEFAULT_LOAD_UNLOAD_DEST_TAG_ID))),
                "EXECUTE_PICK_PLACE": "1" if bool(kwargs.get("execute_pick_place", True)) else "0",
                "LOAD_UNLOAD_STAGE": stage,
                "GRASP_OFFSET_X": str(grasp_offset[0]),
                "GRASP_OFFSET_Y": str(grasp_offset[1]),
                "GRASP_OFFSET_Z": str(grasp_offset[2]),
                "PLACE_OFFSET_X": str(place_lift_offset[0]),
                "PLACE_OFFSET_Y": str(place_lift_offset[1]),
                "PLACE_OFFSET_Z": str(place_lift_offset[2]),
                "PLACE_DESCEND_DZ_BASE_M": str(
                    float(kwargs.get("place_descend_dz_base_m", DEFAULT_LOAD_UNLOAD_PLACE_DESCEND_DZ_BASE_M))
                ),
                "PRE_GRASP_NEG_X_DISTANCE_M": str(
                    float(
                        kwargs.get(
                            "pre_grasp_neg_x_distance_m",
                            DEFAULT_LOAD_UNLOAD_PRE_GRASP_NEG_X_DISTANCE_M,
                        )
                    )
                ),
                "PRE_GRASP_LIFT_Z_M": str(
                    float(kwargs.get("pre_grasp_lift_z_m", DEFAULT_LOAD_UNLOAD_PRE_GRASP_LIFT_Z_M))
                ),
            }
        )
        return env


class LoadUnloadStageTool(LoadWorkpieceToDrawerTool):
    """One visible stage of the drawer loading flow."""

    def __init__(
        self,
        name: str,
        *,
        stage: str,
        base_url: str = DEFAULT_COROBOT_BASE_URL,
        timeout_seconds: float = DEFAULT_LOAD_UNLOAD_TIMEOUT_SECONDS,
        script_path: str | Path = DEFAULT_LOAD_UNLOAD_SCRIPT_PATH,
    ) -> None:
        self.name = name
        self.stage = stage
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            script_path=script_path,
        )
        self.description = MCP_COMPOSITE_TOOL_SCHEMAS[name].description

    def get_schema(self) -> ToolSchema:
        return MCP_COMPOSITE_TOOL_SCHEMAS[self.name]


def register_mcp_control_tools(
    registry: Any,
    *,
    base_url: str = DEFAULT_COROBOT_BASE_URL,
    include_tools: list[str] | tuple[str, ...] | None = None,
    timeout_seconds: float = 180.0,
) -> None:
    """Register mcp_control_demo skill tools on a NewAgent ToolRegistry."""
    names = include_tools or DEFAULT_MCP_CONTROL_TOOL_NAMES
    for name in names:
        registry.register(
            McpControlAgentTool(
                name,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
            )
        )


def register_mcp_control_agent_tools(
    registry: Any,
    *,
    base_url: str = DEFAULT_COROBOT_BASE_URL,
    timeout_seconds: float = 180.0,
    calibration_path: str | Path = DEFAULT_CALIBRATION_PATH,
) -> None:
    """Register the mcp_control tools that should be visible to the LLM agent."""
    recipe_state = McpControlRecipeState()
    for name in DEFAULT_MCP_LLM_TOOL_NAMES:
        if name == "prepare_tag_pick_place":
            registry.register(
                PrepareTagPickPlaceTool(
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                    calibration_path=calibration_path,
                    recipe_state=recipe_state,
                )
            )
            continue
        if name == "resolve_tag_pick_place_recipe":
            registry.register(ResolveTagPickPlaceRecipeTool())
            continue
        if name == "load_workpiece_to_drawer":
            registry.register(
                LoadWorkpieceToDrawerTool(
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                )
            )
            continue
        if name in {
            "open_drawer_for_loading",
            "place_workpiece_in_drawer",
            "close_drawer_after_loading",
        }:
            stage = {
                "open_drawer_for_loading": "pull",
                "place_workpiece_in_drawer": "place",
                "close_drawer_after_loading": "push",
            }[name]
            registry.register(
                LoadUnloadStageTool(
                    name,
                    stage=stage,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                )
            )
            continue
        registry.register(
            McpControlAgentTool(
                name,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                recipe_state=recipe_state,
            )
        )
    registry.register(
        ComputeTagGraspTargetsTool(
            calibration_path=calibration_path,
            recipe_state=recipe_state,
        )
    )
    registry.register(
        ComputeTagPlaceTargetsTool(
            calibration_path=calibration_path,
        )
    )
    registry.register(
        McpSenseEnvironmentTool(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
    )


def _vector(value: Any, expected_len: int) -> list[float]:
    result = [float(item) for item in value]
    if len(result) != expected_len:
        raise ValueError(f"expected {expected_len} values, got {len(result)}")
    return result


def _drop_fixed_skill_arguments(payload: dict[str, Any]) -> None:
    for key in (
        "camera_frame",
        "close_gripper_value",
        "lift_dz_base_m",
        "press_hold_s",
    ):
        payload.pop(key, None)


def _vectors_close(a: list[float], b: list[float], tolerance: float) -> bool:
    if len(a) != len(b):
        return False
    return all(abs(float(x) - float(y)) <= tolerance for x, y in zip(a, b))


def _find_detected_tag(detections: Any, tag_id: int) -> dict[str, Any] | None:
    if not isinstance(detections, list):
        return None
    for item in detections:
        if not isinstance(item, dict):
            continue
        try:
            item_tag_id = int(item.get("tag_id", -1))
        except (TypeError, ValueError):
            continue
        if item_tag_id == int(tag_id):
            return item
    return None


def _try_parse_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _tail_text(text: Any, *, limit: int = 6000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = str(text)
    if len(text) <= limit:
        return text
    return text[-limit:]


def _normalize_corobot_response(payload: Any) -> tuple[ToolStatus, str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return ToolStatus.SUCCESS, "CoRobot returned a non-object JSON value", {"value": payload}

    envelope_success = payload.get("success")
    response_data = payload.get("data") if "data" in payload else payload
    data = dict(response_data) if isinstance(response_data, dict) else {"value": response_data}

    if "data" in payload:
        data.setdefault("_corobot_success", envelope_success)
        if "message" in payload:
            data.setdefault("_corobot_message", payload.get("message"))
        if "error" in payload:
            data.setdefault("_corobot_error", payload.get("error"))

    failed = envelope_success is False
    if isinstance(response_data, dict) and response_data.get("ok") is False:
        failed = True
    if payload.get("error"):
        failed = True

    message = ""
    if isinstance(response_data, dict):
        message = str(response_data.get("message") or "")
    if not message:
        message = str(payload.get("message") or payload.get("error") or "")

    return (ToolStatus.FAILED if failed else ToolStatus.SUCCESS), message, data
