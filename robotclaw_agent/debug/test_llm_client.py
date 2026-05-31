"""
LLMClient 专项测试

测试 LLMClient 的 Mock 调用能力。
"""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src" / "new_agent"))

from llm.llm_client import LLMClient


async def test_llm_client():
    print("=" * 50)
    print("LLMClient 专项测试（Mock 模式）")
    print("=" * 50)

    client = LLMClient(mock_mode=True)

    messages = [
        {"role": "system", "content": "你是一个机器人控制 Agent"},
        {"role": "user", "content": "把方块放到旁边的盒子里"}
    ]

    tools = [
        {
            "type": "function",
            "function": {
                "name": "LocateObject",
                "description": "定位物体",
                "parameters": {"type": "object", "properties": {}}
            }
        }
    ]

    response = await client.chat(messages=messages, tools=tools)

    print(f"LLM 回复内容: {response.content}")
    print(f"是否包含 tool_call: {response.has_tool_call}")
    print(f"Token 使用: {response.total_tokens}")
    print(f"模型: {response.model}")

    if response.has_tool_call:
        for tc in response.tool_calls:
            print(f"  - ToolCall: {tc.name}({tc.arguments})")

    print("\nLLMClient 测试通过！")


if __name__ == "__main__":
    asyncio.run(test_llm_client())
