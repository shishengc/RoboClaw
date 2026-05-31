"""
Prompt 构建器

按照现代 Agent 设计（Claude Code / SWE-agent 风格），
System Prompt 中不再包含工具列表。
工具 Schema 通过 LLM API 的 `tools` 参数单独传入。
"""

from .system_prompt import SYSTEM_PROMPT_TEMPLATE


def build_system_prompt(
    task_brief: str = "完成用户指定的机器人操作任务",
    action_guidance: str = "请使用原子工具逐步完成任务，行动后建议进行环境感知验证。",
) -> str:
    """
    生成系统提示词（不包含工具描述）
    """
    return SYSTEM_PROMPT_TEMPLATE.format(
        task_brief=task_brief,
        action_guidance=action_guidance,
    )
