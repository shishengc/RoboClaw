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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .mcp_control_recipes import get_tag_pick_place_recipe, recipe_names
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
)
DEFAULT_CALIBRATION_PATH = DEFAULT_TASK_CONFIG_PATH
GRASP_TARGET_TOLERANCE_M = 0.003


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
    "default": "assembly_on",
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
            "Press a scene-switch button by AprilTag: detect the button tag, close the gripper, "
            "move above it, press, lift, wait press_interval_s, then press and lift again."
        ),
        parameters=_json_schema(
            {
                "arm": {**ARM_SCHEMA, "default": "right"},
                "button_tag_id": {"type": "integer", "default": 20},
                "base_offset_m": {**XYZ_SCHEMA, "default": [0.0, 0.0, 0.0]},
                "move_duration_s": {"type": "number", "default": 2.0},
                "gripper_duration_s": {"type": "number", "default": 0.5},
                "press_interval_s": {"type": "number", "default": 3.0},
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
            "detections, read fresh source/destination tag poses, resolve the recipe, and "
            "compute grasp/place targets. This tool does not move the robot."
        ),
        parameters=_json_schema(
            {
                "source_tag_id": {"type": "integer"},
                "destination_tag_id": {"type": "integer"},
                "relation": {
                    "type": "string",
                    "enum": ["on", "inside", "assembly", "sorting"],
                    "description": "Requested spatial relation between source and destination tags.",
                },
                "recipe_name": RECIPE_NAME_SCHEMA,
                "retry_detection": {"type": "boolean", "default": True},
            },
            ["source_tag_id", "destination_tag_id", "relation"],
        ),
        returns={
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
            "Resolve the canonical AprilTag pick-and-place recipe for a requested relation. "
            "Use this before computing grasp/place targets so numeric offsets come from code."
        ),
        parameters=_json_schema(
            {
                "source_tag_id": {"type": "integer"},
                "destination_tag_id": {"type": "integer"},
                "relation": {
                    "type": "string",
                    "enum": ["on", "inside", "assembly", "sorting"],
                    "description": "Requested spatial relation between source and destination tags.",
                },
                "recipe_name": RECIPE_NAME_SCHEMA,
            },
        ),
        returns={
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
            recipe = get_tag_pick_place_recipe(
                kwargs.get("recipe_name"),
                relation=kwargs.get("relation"),
                source_tag_id=kwargs.get("source_tag_id"),
                destination_tag_id=kwargs.get("destination_tag_id"),
            )
        except ValueError as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=str(exc),
                data={"error_type": "invalid_recipe", "args": kwargs},
                tool_name=self.name,
            )

        data = recipe.to_dict()
        for key in ("source_tag_id", "destination_tag_id", "relation"):
            if key in kwargs:
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
            source_tag_id = int(kwargs["source_tag_id"])
            destination_tag_id = int(kwargs["destination_tag_id"])
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message="source_tag_id and destination_tag_id are required integers",
                data={"error_type": "invalid_argument", "args": kwargs},
                tool_name=self.name,
            )

        try:
            recipe = get_tag_pick_place_recipe(
                kwargs.get("recipe_name"),
                relation=kwargs.get("relation"),
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
                f"prepared tag pick-and-place: source {source_tag_id}, "
                f"destination {destination_tag_id}, recipe {recipe.name}"
            ),
            data={
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
