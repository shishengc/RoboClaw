"""
新 Agent 包（开发阶段）

为了避免开发时相对导入问题，__init__.py 保持轻量。
推荐使用以下方式导入和运行：

推荐运行测试命令：
    cd new_agent
    PYTHONPATH=src python debug/xxx.py

直接导入示例：
    from new_agent.core.agent import NewAgent
    from new_agent.memory.memory_manager import MemoryManager
    from new_agent.llm.llm_client import LLMClient
"""

__all__ = []
