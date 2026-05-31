"""
真实 LLM 调用测试（GPT-4o）

使用方法：
1. 先运行一次环境初始化：
   ./setup_env.sh

2. 激活虚拟环境：
   source .venv/bin/activate

3. 设置 API Key：
   export OPENAI_API_KEY="sk-xxx"

4. 运行测试：
   PYTHONPATH=src python debug/test_real_llm.py
"""

import asyncio
import sys
import os
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# 自动设置本地代理（避免每次手动 export）
os.environ.setdefault("http_proxy", "http://172.16.2.254:8808")
os.environ.setdefault("https_proxy", "http://172.16.2.254:8808")
os.environ.setdefault("all_proxy", "http://172.16.2.254:8808")

try:
    from new_agent.llm.llm_client import LLMClient
except ImportError as e:
    print("导入失败，请先运行：")
    print("  source .venv/bin/activate")
    print("  或确保已安装 openai 库")
    print(f"错误详情: {e}")
    sys.exit(1)


async def main():
    print("=" * 60)
    print("真实 GPT-4o 调用测试")
    print("=" * 60)

    # 方式1：使用环境变量（推荐）
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        print("未检测到 OPENAI_API_KEY 环境变量，请设置后重试。")
        return

    client = LLMClient(model="gpt-4o", mock_mode=False, api_key=api_key)

    messages = [
        {"role": "system", "content": "你是一个专业的机器人控制助手，请使用原子工具完成任务。"},
        {"role": "user", "content": "把红色方块放到旁边的蓝色盒子里"}
    ]

    tools = [
        {
            "type": "function",
            "function": {
                "name": "LocateObject",
                "description": "定位场景中的物体",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "object_name": {"type": "string", "description": "物体名称"}
                    },
                    "required": ["object_name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "SenseEnvironment",
                "description": "感知当前环境状态",
                "parameters": {"type": "object", "properties": {}}
            }
        }
    ]

    print("\n正在调用 GPT-4o ...")
    response = await client.chat(messages=messages, tools=tools)

    print(f"\n模型回复: {response.content}")
    print(f"Token 使用: {response.total_tokens}")
    print(f"是否调用工具: {response.has_tool_call}")

    if response.has_tool_call:
        for tc in response.tool_calls:
            print(f"  - 工具: {tc.name}, 参数: {tc.arguments}")

    print("\n真实 LLM 调用测试完成！")


if __name__ == "__main__":
    asyncio.run(main())
