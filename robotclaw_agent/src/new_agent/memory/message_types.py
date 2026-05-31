"""
新 Agent 的记忆消息类型

设计目标：轻量、清晰、支持感知结果和原子工具返回。
"""

from dataclasses import dataclass
import json
from typing import Any
from enum import Enum


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    PERCEPTION = "perception"   # 新增：环境感知结果


@dataclass
class MemoryMessage:
    """
    统一的消息结构
    """
    role: MessageRole
    content: str | dict[str, Any] | None
    tool_call_id: str | None = None      # tool message 专用
    tool_name: str | None = None         # tool / perception 专用
    tool_calls: list[dict[str, Any]] | None = None
    timestamp: str | None = None

    def to_openai_format(self) -> dict[str, Any]:
        """转换为 OpenAI 兼容的消息格式"""
        if self.role == MessageRole.ASSISTANT and self.tool_calls:
            return {
                "role": "assistant",
                "content": self.content,
                "tool_calls": self.tool_calls,
            }
        elif self.role == MessageRole.PERCEPTION:
            # 感知结果作为 tool message 或 assistant 描述
            return {
                "role": "assistant",
                "content": f"[Perception Result] {self.content}"
            }
        elif self.role == MessageRole.TOOL:
            return {
                "role": "tool",
                "tool_call_id": self.tool_call_id,
                "name": self.tool_name,
                "content": self._content_to_string()
            }
        else:
            return {
                "role": self.role.value,
                "content": self._content_to_string()
            }

    def _content_to_string(self) -> str | None:
        if self.content is None:
            return None
        if isinstance(self.content, str):
            return self.content
        return json.dumps(self.content, ensure_ascii=False)
