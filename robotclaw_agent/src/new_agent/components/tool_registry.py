"""
ToolRegistry（mini 版本）

为 NewAgent 提供简单的工具注册与执行能力。
当前阶段主要支持 Mock 工具，真实原子工具实现后可无缝替换。
参考 Claude Code / mini-SWE 的工具调用模式，保持接口简洁。
"""

import logging
from copy import deepcopy
from typing import Any, Callable, Awaitable
from ..tools.tool_types import BaseTool, ToolResult, ToolSchema, ToolStatus

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    工具注册表（轻量实现）

    支持：
    - register_mock(name): 注册一个模拟工具
    - execute(name, **kwargs): 异步执行工具并返回 ToolResult
    - list_tools(): 返回当前已注册的工具名列表
    """

    def __init__(self):
        self._mock_tools: dict[str, Callable[..., Awaitable[ToolResult]]] = {}
        self._tools: dict[str, BaseTool] = {}
        self._openai_tools_cache: list[dict[str, Any]] | None = None
        logger.info("[ToolRegistry] 初始化完成（Mock 模式）")

    def register(self, tool: BaseTool) -> None:
        """注册真实工具实例，后续由工具同学填入具体实现。"""
        if not tool.name:
            raise ValueError("工具必须定义 name")
        self._tools[tool.name] = tool
        self._openai_tools_cache = None
        logger.info(f"[ToolRegistry] 已注册工具: {tool.name}")

    def register_mock(self, name: str) -> None:
        """注册一个 Mock 工具"""
        if name in self._mock_tools:
            return

        async def _mock_executor(**kwargs: Any) -> ToolResult:
            logger.info(f"[ToolRegistry] Mock 执行: {name}，参数: {kwargs}")
            data = self._mock_result_data(name, kwargs)
            return ToolResult(
                status=ToolStatus.SUCCESS,
                message=f"[Mock] {name} 执行成功",
                data=data,
                raw_output=f"Mock result from {name}",
                tool_name=name
            )

        self._mock_tools[name] = _mock_executor
        self._openai_tools_cache = None
        logger.info(f"[ToolRegistry] 已注册 Mock 工具: {name}")

    def list_tools(self) -> list[str]:
        """返回当前所有已注册工具名称"""
        names = list(self._tools.keys())
        names.extend(name for name in self._mock_tools if name not in self._tools)
        return names

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """返回 OpenAI tools 参数所需的 function schema。"""
        if self._openai_tools_cache is None:
            self._openai_tools_cache = [
                self._to_openai_tool(name)
                for name in sorted(self.list_tools())
            ]
        return deepcopy(self._openai_tools_cache)

    async def execute(self, name: str, **kwargs: Any) -> ToolResult:
        """执行指定工具"""
        if name in self._tools:
            return await self._tools[name].execute(**kwargs)

        if name not in self._mock_tools:
            logger.warning(f"[ToolRegistry] 工具不存在: {name}，返回失败结果")
            return ToolResult(
                status=ToolStatus.FAILED,
                message=f"工具未注册: {name}",
                data={},
                raw_output="",
                tool_name=name
            )

        executor = self._mock_tools[name]
        return await executor(**kwargs)

    def _mock_result_data(self, name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if name == "SenseEnvironment":
            return {
                "objects": [
                    {
                        "name": "red_cube",
                        "visible": True,
                        "pose": None,
                        "confidence": 0.8,
                    }
                ],
                "robot_state": {
                    "mode": "mock",
                    "is_moving": False,
                },
                "gripper_state": {
                    "holding": "unknown",
                },
                "task_progress": {
                    "overall_completion": None,
                    "status": "unknown",
                    "completed_subtasks": [],
                    "pending_subtasks": [],
                },
                "safety_status": {
                    "is_safe": True,
                    "issues": [],
                },
                "uncertainties": ["mock perception does not verify physical state"],
                "raw": {"tool": name, "args": kwargs, "result": "模拟返回"},
            }
        return {"tool": name, "args": kwargs, "result": "模拟返回"}

    def _to_openai_tool(self, name: str) -> dict[str, Any]:
        if name in self._tools:
            schema = self._tools[name].get_schema()
        else:
            schema = self._mock_schema(name)

        return {
            "type": "function",
            "function": {
                "name": schema.name,
                "description": schema.description,
                "parameters": schema.parameters or {"type": "object", "properties": {}},
            },
        }

    def _mock_schema(self, name: str) -> ToolSchema:
        schemas = {
            "LocateObject": ToolSchema(
                name="LocateObject",
                description="定位场景中的目标物体，返回物体位姿、可见性和置信度。",
                parameters={
                    "type": "object",
                    "properties": {
                        "object_name": {
                            "type": "string",
                            "description": "需要定位的物体名称或语义标签。",
                        }
                    },
                    "required": ["object_name"],
                    "additionalProperties": False,
                },
                returns={},
            ),
            "GraspObject": ToolSchema(
                name="GraspObject",
                description="抓取指定物体。调用前应已确认物体位置和可抓取状态。",
                parameters={
                    "type": "object",
                    "properties": {
                        "object_name": {
                            "type": "string",
                            "description": "需要抓取的物体名称或语义标签。",
                        }
                    },
                    "required": ["object_name"],
                    "additionalProperties": False,
                },
                returns={},
            ),
            "PlaceObject": ToolSchema(
                name="PlaceObject",
                description="将当前抓取的物体放置到目标位置或目标容器。",
                parameters={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "放置目标，例如容器名称、区域名称或位置标签。",
                        }
                    },
                    "required": ["target"],
                    "additionalProperties": False,
                },
                returns={},
            ),
            "SenseEnvironment": ToolSchema(
                name="SenseEnvironment",
                description="感知当前环境状态，用于验证动作结果、更新物体状态和任务进度。",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                returns={},
            ),
            "FinalizeTask": ToolSchema(
                name="FinalizeTask",
                description="在确认任务完成后显式结束任务，并给出完成摘要。",
                parameters={
                    "type": "object",
                    "properties": {
                        "completion_summary": {
                            "type": "string",
                            "description": "对最终完成状态的简短说明。",
                        }
                    },
                    "required": ["completion_summary"],
                    "additionalProperties": False,
                },
                returns={},
            ),
        }
        return schemas.get(
            name,
            ToolSchema(
                name=name,
                description=f"原子工具: {name}",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                returns={},
            ),
        )
