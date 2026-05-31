"""
新 Agent 的 MemoryManager

核心职责：
- 管理多个 TaskNode
- 提供统一的上下文构建接口
- 支持添加用户消息、工具结果、感知结果
- 支持任务进度跟踪
"""

import uuid
import logging
from typing import Any
from .task_node import TaskNode
from .message_types import MessageRole

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    记忆管理器

    采用简洁的 TaskNode 模型，支持多任务和感知结果存储。
    """

    def __init__(self):
        self._task_nodes: dict[str, TaskNode] = {}
        self._current_task_id: str | None = None

    # ========== 任务管理 ==========

    def create_task(self, task_brief: str, action_guidance: str = "") -> str:
        """创建一个新任务节点"""
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        node = TaskNode(
            task_id=task_id,
            task_brief=task_brief,
            action_guidance=action_guidance
        )
        self._task_nodes[task_id] = node
        self._current_task_id = task_id
        logger.info(f"[MemoryManager] 创建新任务: {task_id} - {task_brief}")
        return task_id

    def switch_to_task(self, task_id: str) -> None:
        if task_id not in self._task_nodes:
            raise ValueError(f"任务不存在: {task_id}")
        self._current_task_id = task_id

    @property
    def current_task(self) -> TaskNode | None:
        if self._current_task_id is None:
            return None
        return self._task_nodes.get(self._current_task_id)

    # ========== 消息写入 ==========

    def add_user_message(self, content: str) -> None:
        if self.current_task:
            self.current_task.add_user_message(content)

    def add_assistant_message(self, content: str) -> None:
        if self.current_task:
            self.current_task.add_assistant_message(content)

    def add_assistant_tool_calls(
        self,
        content: str | None,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        if self.current_task:
            self.current_task.add_assistant_tool_calls(content, tool_calls)

    def add_tool_result(self, tool_name: str, content: Any, tool_call_id: str | None = None) -> None:
        if self.current_task:
            self.current_task.add_tool_result(tool_name, content, tool_call_id)

    def add_perception_result(self, content: dict[str, Any] | str) -> None:
        """添加环境感知结果（新设计核心）"""
        if self.current_task:
            self.current_task.add_perception_result(content)
            logger.info("[MemoryManager] 已记录感知结果")

    def add_subtask(self, subtask: str) -> None:
        """记录子任务进度"""
        if self.current_task:
            self.current_task.add_subtask(subtask)
            logger.info(f"[MemoryManager] 新增子任务: {subtask}")

    def set_task_plan(self, steps: list[str]) -> None:
        if self.current_task:
            self.current_task.set_task_plan(steps)
            logger.info(f"[MemoryManager] 已设置任务计划，共 {len(steps)} 步")

    def update_plan_for_tool(self, tool_name: str, success: bool) -> None:
        if self.current_task:
            self.current_task.update_plan_for_tool(tool_name, success)

    def add_failure(self, failure: dict[str, Any]) -> None:
        if self.current_task:
            self.current_task.add_failure(failure)
            logger.warning(f"[MemoryManager] 已记录失败: {failure}")

    # ========== 上下文构建 ==========

    def get_current_contexts(self, system_prompt: str | None = None) -> list[dict[str, Any]]:
        """
        获取当前任务的完整上下文（供 LLM 使用）
        """
        if not self.current_task:
            return [{"role": "system", "content": "No active task."}]

        return self.current_task.get_context_messages(system_prompt=system_prompt)

    # ========== 任务状态 ==========

    def get_current_task_progress(self) -> float:
        if self.current_task:
            return self.current_task.get_completion_status()
        return 0.0

    def mark_task_completed(self, summary: str) -> None:
        if self.current_task:
            self.current_task.completion_status = 100.0
            self.add_assistant_message(f"[Task Completed] {summary}")
            logger.info(f"[MemoryManager] 任务完成: {summary}")

    def compress_current_memory(self, max_recent: int = 8) -> None:
        """触发当前任务的记忆压缩"""
        if self.current_task:
            self.current_task.compress_memory(max_recent)
            logger.info("[MemoryManager] 已执行记忆压缩")

    def get_progress_summary(self) -> str:
        if self.current_task:
            return self.current_task.get_progress_summary()
        return "无活动任务"
