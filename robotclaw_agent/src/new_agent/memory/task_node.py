"""
TaskNode：单个任务的记忆节点

负责管理该任务的系统上下文 + 对话历史 + 感知结果。
"""

from dataclasses import dataclass, field
from typing import Any
from .perception import normalize_perception_result
from .message_types import MemoryMessage, MessageRole


@dataclass
class TaskNode:
    """
    单个任务的记忆容器
    """
    task_id: str
    task_brief: str
    action_guidance: str = ""

    # 系统级上下文（任务开始时设定）
    system_context: dict[str, Any] = field(default_factory=dict)

    # 对话历史（包含用户、助手、工具返回、感知结果）
    messages: list[MemoryMessage] = field(default_factory=list)

    # 新增：任务进度与感知结果
    completion_status: float = 0.0          # 0~100
    last_perception_result: dict[str, Any] | None = None
    subtask_history: list[str] = field(default_factory=list)
    task_plan: list[dict[str, Any]] = field(default_factory=list)
    failure_history: list[dict[str, Any]] = field(default_factory=list)

    def set_task_plan(self, steps: list[str]) -> None:
        self.task_plan = [
            {
                "id": f"step_{idx + 1}",
                "description": step,
                "status": "pending",
            }
            for idx, step in enumerate(steps)
        ]

    def update_plan_for_tool(self, tool_name: str, success: bool) -> None:
        if not self.task_plan:
            return

        expected = {
            "LocateObject": "locate",
            "GraspObject": "grasp",
            "PlaceObject": "place",
            "SenseEnvironment": "verify",
            "FinalizeTask": "finalize",
        }.get(tool_name, tool_name.lower())

        for step in self.task_plan:
            description = step["description"].lower()
            if step["status"] == "completed":
                continue
            if expected in description or tool_name.lower() in description:
                step["status"] = "completed" if success else "blocked"
                return

        for step in self.task_plan:
            if step["status"] == "pending":
                step["status"] = "completed" if success else "blocked"
                return

    def add_failure(self, failure: dict[str, Any]) -> None:
        self.failure_history.append(failure)

    def add_user_message(self, content: str) -> None:
        self.messages.append(MemoryMessage(role=MessageRole.USER, content=content))

    def add_assistant_message(self, content: str) -> None:
        self.messages.append(MemoryMessage(role=MessageRole.ASSISTANT, content=content))

    def add_assistant_tool_calls(
        self,
        content: str | None,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        self.messages.append(
            MemoryMessage(
                role=MessageRole.ASSISTANT,
                content=content,
                tool_calls=tool_calls,
            )
        )

    def add_tool_result(self, tool_name: str, content: Any, tool_call_id: str | None = None) -> None:
        self.messages.append(
            MemoryMessage(
                role=MessageRole.TOOL,
                content=content,
                tool_name=tool_name,
                tool_call_id=tool_call_id
            )
        )

    def add_perception_result(self, content: dict[str, Any] | str) -> None:
        """添加环境感知结果"""
        normalized = normalize_perception_result(content)
        self.messages.append(
            MemoryMessage(role=MessageRole.PERCEPTION, content=normalized)
        )
        # 更新最新感知结果
        self.last_perception_result = normalized
        progress = normalized["task_progress"].get("overall_completion")
        if isinstance(progress, (int, float)):
            self.completion_status = float(progress) * 100

    def add_subtask(self, subtask: str) -> None:
        """记录子任务（用于任务拆分跟踪）"""
        self.subtask_history.append(subtask)
        self.messages.append(
            MemoryMessage(role=MessageRole.ASSISTANT, content=f"[Subtask] {subtask}")
        )

    def compress_memory(self, max_recent: int = 8) -> None:
        """简单记忆压缩：保留最近消息 + 所有感知结果，避免上下文过长"""
        if len(self.messages) <= max_recent:
            return
        # 保留最近 max_recent 条 + 所有感知消息
        recent = self.messages[-max_recent:]
        perceptions = [m for m in self.messages if m.role == MessageRole.PERCEPTION]
        # 去重合并
        kept = []
        seen = set()
        for m in perceptions + recent:
            key = (m.role, str(m.content)[:100])
            if key not in seen:
                kept.append(m)
                seen.add(key)
        self.messages = kept[-max_recent*2:]  # 控制总量

    def get_progress_summary(self) -> str:
        """返回当前进度摘要，便于调试和 Prompt 使用"""
        subs = len(self.subtask_history)
        last_p = self.last_perception_result or {}
        plan = ", ".join(
            f"{step['id']}={step['status']}"
            for step in self.task_plan
        ) or "no plan"
        failures = len(self.failure_history)
        return (
            f"完成度: {self.completion_status:.0f}%, 子任务数: {subs}, "
            f"计划: {plan}, 失败次数: {failures}, 最近感知: {str(last_p)[:80]}"
        )

    def get_context_messages(self, system_prompt: str | None = None) -> list[dict[str, Any]]:
        """
        返回适合发给 LLM 的消息列表（感知结果优先 + 进度摘要）
        """
        contexts: list[dict[str, Any]] = []

        if system_prompt is None:
            system_prompt = f"Current Task: {self.task_brief}\n"
            if self.action_guidance:
                system_prompt += f"Guidance: {self.action_guidance}\n"

        contexts.append({"role": "system", "content": system_prompt})

        runtime_state = f"Current Progress:\n{self.get_progress_summary()}\n"
        if self.last_perception_result:
            runtime_state += f"Last Perception: {self.last_perception_result}\n"
        contexts.append({"role": "system", "content": runtime_state})

        # 历史消息（最近感知已体现在 system 中，保持时间顺序）
        for msg in self.messages:
            contexts.append(msg.to_openai_format())

        return contexts

    def get_completion_status(self) -> float:
        return self.completion_status
