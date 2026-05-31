"""
新 Agent 骨架测试脚本（集成 MemoryManager 版本）

运行方式：
    cd new_agent
    python debug/test_agent.py
"""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from new_agent.core.agent import NewAgent


async def main():
    print("=" * 60)
    print("新 Agent 骨架测试（含 MemoryManager）")
    print("=" * 60)

    agent = NewAgent(agent_name="TestAgent")
    await agent.init_agent(
        task_brief="把方块放到旁边的盒子里",
        action_guidance="使用原子工具逐步完成，行动后必须感知验证。"
    )

    print(f"\n当前状态: {agent.state_machine.current_state.name}")
    print(f"已注册工具: {agent.tool_registry.list_tools()}")
    print(f"当前任务进度: {agent.memory_manager.get_current_task_progress()}%")

    print("\n开始执行任务...")
    result = await agent.run_once("把方块放到旁边的盒子里")

    print(f"\n最终结果: {result}")
    print(f"结束状态: {agent.state_machine.current_state.name}")
    print(f"最终任务进度: {agent.memory_manager.get_current_task_progress()}%")

    # 查看记忆中的上下文
    contexts = agent.memory_manager.get_current_contexts()
    print(f"\n当前上下文消息数量: {len(contexts)}")

    await agent.shutdown()
    print("\n测试完成！")


if __name__ == "__main__":
    asyncio.run(main())
