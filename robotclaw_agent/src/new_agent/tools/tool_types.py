"""
Tool 相关类型定义

这是我们与 Tool 实现同学对接的正式契约。
请 Tool 同学严格按照此格式实现所有原子工具。
"""

from dataclasses import dataclass, field
from typing import Any, Literal
from enum import Enum


class ToolStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class ToolResult:
    """
    所有工具必须返回的标准结果格式

    属性:
        status: 执行状态 (success / failed / partial)
        message: 人类可读的执行说明
        data: 结构化返回数据（推荐使用）
        raw_output: 原始输出（可选）
    """
    status: ToolStatus
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""
    tool_name: str = ""

    @property
    def success(self) -> bool:
        return self.status == ToolStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "message": self.message,
            "data": self.data,
            "tool_name": self.tool_name,
        }


@dataclass
class ToolSchema:
    """
    工具的 JSON Schema 定义（用于 LLM function calling）
    """
    name: str
    description: str
    parameters: dict[str, Any]   # JSON Schema object
    returns: dict[str, Any]      # 返回结构描述（供文档使用）


class BaseTool:
    """
    所有原子工具的基类

    子类必须实现：
        name, description, execute()
    可选实现：
        get_schema()
    """

    name: str = "base_tool"
    description: str = ""

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行工具，子类必须重写"""
        raise NotImplementedError

    def get_schema(self) -> ToolSchema:
        """返回工具的 Schema 定义"""
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={},
            returns={},
        )
