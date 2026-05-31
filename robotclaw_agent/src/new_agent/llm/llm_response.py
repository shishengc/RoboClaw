"""
LLM 调用返回结构

统一封装模型返回，便于 Agent 使用。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """模型要求调用的单个工具"""
    id: str
    name: str                    # 格式: "service|tool" 或直接工具名
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """
    LLM 调用返回结果
    """
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    model: str = ""

    @property
    def has_tool_call(self) -> bool:
        return bool(self.tool_calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in (self.tool_calls or [])
            ],
            "total_tokens": self.total_tokens,
            "finish_reason": self.finish_reason,
        }
