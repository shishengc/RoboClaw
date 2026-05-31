"""
Adapters that expose shisheng/ros/grpc/agent_tools as NewAgent tools.

The shisheng tools already own the simulator-specific control logic. This
module only bridges NewAgent's BaseTool contract to the existing CLI entrypoints.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .tool_types import BaseTool, ToolResult, ToolSchema, ToolStatus


DEFAULT_SHISHENG_ROOT = Path("/home/easyai/桌面/shisheng")
CONTAINER_TOOL_NAMES = {
    "GetObservation",
    "SenseEnvironment",
    "DetectTarget",
    "LocalizeTarget",
    "CheckGraspReady",
    "ApproachTarget",
    "AlignTarget",
    "SearchGraspPose",
    "GraspAtCurrent",
    "MoveEndEffectorToWorld",
    "PlaceHeldObject",
    "ReturnToDefaultAbsPose",
    "VerifyTaskState",
}
LOCAL_TOOL_NAMES = {
    "BackendStatus",
    "StartBackendServices",
    "StartGrpcServer",
    "StartBridge",
    "SetupBackend",
    "CleanupBackend",
}


def _json_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": True,
    }


SHISHENG_TOOL_SCHEMAS: dict[str, ToolSchema] = {
    "EnsureAbsPoseRunning": ToolSchema(
        name="EnsureAbsPoseRunning",
        description="确认 GenieSim bridge 处于 abs_pose running 状态；如果 bridge 为 ready，会发送 start。",
        parameters=_json_schema(),
        returns={},
    ),
    "GetObservation": ToolSchema(
        name="GetObservation",
        description="读取 bridge 状态、图像路径、status.json 和 latest_observation.json 快照，不改变机器人状态。",
        parameters=_json_schema(
            {
                "include_images": {"type": "boolean"},
                "include_status": {"type": "boolean"},
                "include_observation": {"type": "boolean"},
                "grpc_target": {"type": "string"},
            }
        ),
        returns={},
    ),
    "SenseEnvironment": ToolSchema(
        name="SenseEnvironment",
        description="聚合当前环境快照，包括目标定位、右臂状态、夹爪状态和可选抓取检查；未指定 target/targets 时返回全局可观察方块。",
        parameters=_json_schema(
            {
                "target": {"type": ["string", "object"]},
                "targets": {"type": "array", "items": {"type": ["string", "object"]}},
                "include_grasp_check": {"type": "boolean"},
                "scene_instance_id": {"type": "integer"},
                "grpc_target": {"type": "string"},
            }
        ),
        returns={},
    ),
    "LocalizeTarget": ToolSchema(
        name="LocalizeTarget",
        description="定位目标方块，返回目标世界坐标、右末端世界坐标和两者偏差。",
        parameters=_json_schema(
            {
                "target": {"type": ["string", "object"]},
                "camera": {"type": "string", "enum": ["geometry", "right", "head", "auto"]},
                "scene_instance_id": {"type": "integer"},
            },
            ["target"],
        ),
        returns={},
    ),
    "ApproachTarget": ToolSchema(
        name="ApproachTarget",
        description="移动右末端到目标方块附近的接近点，通常保持夹爪打开。",
        parameters=_json_schema(
            {
                "target": {"type": ["string", "object"]},
                "approach_offset_m": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "gripper": {"type": ["string", "number"]},
                "timeout": {"type": "number"},
                "tolerance_m": {"type": "number"},
                "settle_interval": {"type": "number"},
                "scene_instance_id": {"type": "integer"},
            },
            ["target"],
        ),
        returns={},
    ),
    "AlignTarget": ToolSchema(
        name="AlignTarget",
        description="把右末端对齐到目标方块的抓取点，默认使用 geometry_direct 方法。",
        parameters=_json_schema(
            {
                "target": {"type": ["string", "object"]},
                "method": {"type": "string"},
                "grasp_offset_m": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "gripper": {"type": ["string", "number"]},
                "timeout": {"type": "number"},
                "tolerance_m": {"type": "number"},
                "scene_instance_id": {"type": "integer"},
            },
            ["target"],
        ),
        returns={},
    ),
    "CheckGraspReady": ToolSchema(
        name="CheckGraspReady",
        description="检查当前右末端是否已到达目标方块抓取点，可用于决定是否闭合夹爪。",
        parameters=_json_schema(
            {
                "target": {"type": ["string", "object"]},
                "grasp_offset_m": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "distance_tol_m": {"type": "number"},
                "xy_tol_m": {"type": "number"},
                "z_tol_m": {"type": "number"},
                "scene_instance_id": {"type": "integer"},
            },
            ["target"],
        ),
        returns={},
    ),
    "GraspAtCurrent": ToolSchema(
        name="GraspAtCurrent",
        description="在当前右末端位置闭合夹爪抓取目标；可选择是否要求抓取就绪检查通过。",
        parameters=_json_schema(
            {
                "target": {"type": ["string", "object"]},
                "require_grasp_ready": {"type": "boolean"},
                "lift_after_grasp": {"type": "boolean"},
                "grasp_offset_m": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "distance_tol_m": {"type": "number"},
                "xy_tol_m": {"type": "number"},
                "z_tol_m": {"type": "number"},
                "timeout": {"type": "number"},
                "closed_gripper_raw_threshold": {"type": "number"},
                "scene_instance_id": {"type": "integer"},
            },
            ["target"],
        ),
        returns={},
    ),
    "MoveEndEffectorToWorld": ToolSchema(
        name="MoveEndEffectorToWorld",
        description="移动右末端到绝对世界坐标，或按 relative_delta_m 从当前位置相对移动。",
        parameters=_json_schema(
            {
                "target_world_m": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "relative_delta_m": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "gripper": {"type": ["string", "number"]},
                "timeout": {"type": "number"},
                "tolerance_m": {"type": "number"},
                "settle_interval": {"type": "number"},
            }
        ),
        returns={},
    ),
    "PlaceHeldObject": ToolSchema(
        name="PlaceHeldObject",
        description="将当前夹持的物体放到目标方块、世界坐标、相对偏移或排序区，并可打开夹爪释放。",
        parameters=_json_schema(
            {
                "held_object": {"type": ["string", "object"]},
                "target": {"type": "object"},
                "held_object_grasp_offset_m": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "hover_height_m": {"type": "number"},
                "release": {"type": "boolean"},
                "release_pause": {"type": "number"},
                "return_after_place": {"type": "boolean"},
                "timeout": {"type": "number"},
                "tolerance_m": {"type": "number"},
                "place_target_z_offset_m": {"type": "number"},
                "scene_instance_id": {"type": "integer"},
            },
            ["held_object", "target"],
        ),
        returns={},
    ),
    "ReturnToDefaultAbsPose": ToolSchema(
        name="ReturnToDefaultAbsPose",
        description="把 abs_pose 任务中的左右末端恢复到内置默认姿态，并按需打开夹爪。",
        parameters=_json_schema(
            {
                "gripper": {"type": ["string", "number"]},
                "timeout": {"type": "number"},
                "tolerance_m": {"type": "number"},
                "settle_interval": {"type": "number"},
                "left_xyzrpy": {"type": "array", "items": {"type": "number"}},
                "right_xyzrpy": {"type": "array", "items": {"type": "number"}},
                "target_world_m": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            }
        ),
        returns={},
    ),
    "VerifyTaskState": ToolSchema(
        name="VerifyTaskState",
        description="验证当前环境是否满足任务目标，例如 source block 是否已经放到 target block 上。",
        parameters=_json_schema(
            {
                "goal": {
                    "type": "object",
                    "description": "目标描述。block_on_block 必须显式提供 source 和 target；gripper_empty 用于验证右夹爪打开。",
                    "properties": {
                        "type": {"type": "string", "enum": ["block_on_block", "gripper_empty"]},
                        "source": {"type": ["string", "object"]},
                        "target": {"type": ["string", "object"]},
                        "xy_tolerance_m": {"type": "number"},
                        "z_error_tolerance_m": {"type": "number"},
                        "min_z_delta_m": {"type": "number"},
                        "place_target_z_offset_m": {"type": "number"},
                    },
                    "required": ["type"],
                    "additionalProperties": True,
                },
                "scene_instance_id": {"type": "integer"},
            },
            ["goal"],
        ),
        returns={},
    ),
}

for _name in LOCAL_TOOL_NAMES:
    SHISHENG_TOOL_SCHEMAS.setdefault(
        _name,
        ToolSchema(
            name=_name,
            description=f"shisheng 后端生命周期工具：{_name}。",
            parameters=_json_schema(),
            returns={},
        ),
    )

for _name in ("DetectTarget", "SearchGraspPose"):
    SHISHENG_TOOL_SCHEMAS.setdefault(
        _name,
        ToolSchema(
            name=_name,
            description=f"shisheng 目标感知/恢复工具：{_name}。",
            parameters=_json_schema(),
            returns={},
        ),
    )


class ShishengAgentTool(BaseTool):
    """BaseTool wrapper around shisheng's existing tool CLI."""

    def __init__(
        self,
        name: str,
        *,
        repo_root: str | Path = DEFAULT_SHISHENG_ROOT,
        auto_setup: str = "0",
        timeout_seconds: float = 180.0,
    ) -> None:
        self.name = name
        self.repo_root = Path(repo_root)
        self.auto_setup = str(auto_setup)
        self.timeout_seconds = float(timeout_seconds)
        self.description = SHISHENG_TOOL_SCHEMAS.get(name, self._fallback_schema(name)).description

    def get_schema(self) -> ToolSchema:
        return SHISHENG_TOOL_SCHEMAS.get(self.name, self._fallback_schema(self.name))

    async def execute(self, **kwargs: Any) -> ToolResult:
        if self.name == "EnsureAbsPoseRunning":
            return await self._run_ensure_abs_pose_running()

        script_name = "run_local_tool.sh" if self.name in LOCAL_TOOL_NAMES else "run_tool_in_container.sh"
        script_path = self.repo_root / "ros" / "grpc" / "agent_tools" / "tests" / script_name
        if not script_path.exists():
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"shisheng tool runner not found: {script_path}",
                data={"error_type": "missing_runner", "script": str(script_path)},
                tool_name=self.name,
            )

        payload = json.dumps(kwargs, ensure_ascii=False, separators=(",", ":"))
        env = os.environ.copy()
        env.setdefault("AUTO_SETUP", self.auto_setup)

        return await self._run_command([str(script_path), self.name, payload], env=env)

    async def _run_ensure_abs_pose_running(self) -> ToolResult:
        script_path = self.repo_root / "ros" / "grpc" / "agent_tools" / "tests" / "ensure_abs_pose_running.sh"
        if not script_path.exists():
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"ensure script not found: {script_path}",
                data={"error_type": "missing_runner", "script": str(script_path)},
                tool_name=self.name,
            )
        result = await self._run_command([str(script_path)], parse_json=False)
        if result.status == ToolStatus.SUCCESS:
            result.message = result.message or "abs_pose bridge is running"
        return result

    async def _run_command(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        parse_json: bool = True,
    ) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self.repo_root),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"shisheng tool timed out after {self.timeout_seconds:.1f}s",
                data={"error_type": "timeout", "argv": argv},
                tool_name=self.name,
            )
        except Exception as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"failed to execute shisheng tool: {exc}",
                data={"error_type": "exception", "argv": argv, "error": str(exc)},
                tool_name=self.name,
            )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        raw_output = stdout + (("\n" + stderr) if stderr else "")

        if proc.returncode != 0:
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"shisheng tool exited with code {proc.returncode}",
                data={"error_type": "process_failed", "returncode": proc.returncode, "stderr": stderr},
                raw_output=raw_output,
                tool_name=self.name,
            )

        if not parse_json:
            return ToolResult(
                status=ToolStatus.SUCCESS,
                message="command completed",
                data={"stdout": stdout.strip(), "stderr": stderr.strip()},
                raw_output=raw_output,
                tool_name=self.name,
            )

        parsed = _extract_json_object(stdout)
        if parsed is None:
            return ToolResult(
                status=ToolStatus.FAILED,
                message="shisheng tool did not return a JSON object",
                data={"error_type": "invalid_output", "stdout": stdout, "stderr": stderr},
                raw_output=raw_output,
                tool_name=self.name,
            )

        status_value = str(parsed.get("status", "success"))
        try:
            status = ToolStatus(status_value)
        except ValueError:
            status = ToolStatus.FAILED

        return ToolResult(
            status=status,
            message=str(parsed.get("message", "")),
            data=parsed.get("data") if isinstance(parsed.get("data"), dict) else {},
            raw_output=raw_output,
            tool_name=str(parsed.get("tool_name") or self.name),
        )

    @staticmethod
    def _fallback_schema(name: str) -> ToolSchema:
        return ToolSchema(
            name=name,
            description=f"shisheng agent tool: {name}",
            parameters=_json_schema(),
            returns={},
        )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def register_shisheng_tools(
    registry: Any,
    *,
    repo_root: str | Path = DEFAULT_SHISHENG_ROOT,
    auto_setup: str = "0",
    include_backend: bool = True,
    timeout_seconds: float = 180.0,
) -> None:
    """Register shisheng tools on a NewAgent ToolRegistry."""
    names = ["EnsureAbsPoseRunning"]
    if include_backend:
        names.extend(sorted(LOCAL_TOOL_NAMES))
    names.extend(sorted(CONTAINER_TOOL_NAMES))

    for name in names:
        registry.register(
            ShishengAgentTool(
                name,
                repo_root=repo_root,
                auto_setup=auto_setup,
                timeout_seconds=timeout_seconds,
            )
        )
