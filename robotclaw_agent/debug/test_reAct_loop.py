"""
ReAct 主循环演示测试

演示新 Agent 的完整 ReAct 流程（使用 Mock LLM）。
"""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src" / "new_agent"))

from core.agent import NewAgent


async def main():
    print("=" * 60)
    print("新 Agent ReAct 主循环演示")
    print("=" * 60)

    agent = NewAgent(agent_name="ReActAgent")
    await agent.init_agent(
        task_brief="把方块放到旁边的盒子里",
        action_guidance="使用原子工具，行动后感知验证。"
    )

    print(f"\n当前状态: {agent.state_machine.current_state.name}")
    print(f"已注册工具: {agent.tool_registry.list_tools()}")

    print("\n开始执行 ReAct 循环...")
    result = await agent.run_once("把方块放到旁边的盒子里")

    print(f"\n最终结果: {result}")
    print(f"结束状态: {agent.state_machine.current_state.name}")
    print(f"最终任务进度: {agent.memory_manager.get_current_task_progress()}%")

    # 查看记忆上下文
    contexts = agent.memory_manager.get_current_contexts()
    print(f"\n最终上下文消息数量: {len(contexts)}")

    await agent.shutdown()
    print("\nReAct 主循环演示完成！")


if __name__ == "__main__":
    asyncio.run(main())
