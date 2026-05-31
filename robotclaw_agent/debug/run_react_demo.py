"""
ReAct 主循环演示（最简洁版本）

推荐运行方式（在 new_agent 目录下）：
    PYTHONPATH=src python debug/run_react_demo.py
"""

import asyncio
from new_agent.core.agent import NewAgent


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

    await agent.shutdown()
    print("\n演示完成！")


if __name__ == "__main__":
    asyncio.run(main())
