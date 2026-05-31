"""
Interactive terminal interface for NewAgent.
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path

from .core.agent import NewAgent


DEFAULT_GUIDANCE = "使用原子工具逐步完成任务，行动后必须感知验证。"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run NewAgent in an interactive terminal.")
    parser.add_argument("--agent-name", default="CLIAgent", help="Name used in logs and trajectory files.")
    parser.add_argument("--task-brief", default="完成用户指定的机器人操作任务", help="Initial task context.")
    parser.add_argument("--guidance", default=DEFAULT_GUIDANCE, help="Action guidance injected into the system prompt.")
    parser.add_argument("--config", default=None, help="Path to agent_config.json.")
    parser.add_argument("--real-llm", action="store_true", help="Use real OpenAI calls instead of mock LLM.")
    parser.add_argument("--api-key", default=None, help="OpenAI API key. Defaults to OPENAI_API_KEY.")
    parser.add_argument("--shisheng-tools", action="store_true", help="Register real shisheng robot tools instead of mock tools.")
    parser.add_argument("--shisheng-root", default="/home/easyai/桌面/shisheng", help="Path to the shisheng repository.")
    parser.add_argument("--run-full-task-1", action="store_true", help="Run the full_task_1.md tool chain once, then exit.")
    parser.add_argument("--source-color", default="red", choices=["red", "yellow", "purple", "green", "blue"])
    parser.add_argument("--target-color", default="yellow", choices=["", "red", "yellow", "purple", "green", "blue"])
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


async def run_cli(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s:%(name)s:%(message)s",
    )

    api_key = args.api_key or os.getenv("OPENAI_API_KEY")
    agent = NewAgent(agent_name=args.agent_name, config_path=args.config)
    await agent.init_agent(
        task_brief=args.task_brief,
        action_guidance=args.guidance,
        use_real_llm=args.real_llm,
        openai_api_key=api_key,
        config_path=args.config,
        use_shisheng_tools=args.shisheng_tools or args.run_full_task_1,
        shisheng_repo_root=args.shisheng_root,
    )

    if args.run_full_task_1:
        try:
            result = await agent.run_full_task_1(
                source_color=args.source_color,
                target_color=args.target_color or None,
            )
            print(result)
            if agent.last_trajectory_path:
                print(f"trajectory: {Path(agent.last_trajectory_path)}")
            return 0
        finally:
            await agent.shutdown()

    print("NewAgent CLI")
    print(f"mode: {'real-llm' if args.real_llm else 'mock'}")
    print(f"state: {agent.state_machine.current_state.name}")
    print(f"tools: {', '.join(agent.tool_registry.list_tools())}")
    print("commands: /help, /status, /tools, /trajectory, /quit")
    print()

    try:
        while True:
            user_input = input("User > ").strip()
            if not user_input:
                continue

            command = user_input.lower()
            if command in {"/quit", "/exit", "quit", "exit"}:
                break
            if command == "/help":
                print("Commands:")
                print("  /status      Show current state and task progress")
                print("  /tools       Show registered tools")
                print("  /trajectory  Show last saved trajectory path")
                print("  /quit        Exit CLI")
                print("Any other input is sent to agent.run_once().")
                continue
            if command == "/status":
                print(f"state: {agent.state_machine.current_state.name}")
                print(f"progress: {agent.memory_manager.get_current_task_progress():.1f}%")
                print(f"summary: {agent.memory_manager.get_progress_summary()}")
                continue
            if command == "/tools":
                for tool_name in agent.tool_registry.list_tools():
                    print(f"- {tool_name}")
                continue
            if command == "/trajectory":
                print(agent.last_trajectory_path or "No trajectory saved yet.")
                continue

            print("Agent is running...")
            try:
                result = await agent.run_once(user_input)
            except KeyboardInterrupt:
                print("\nInterrupted.")
                continue
            except Exception as exc:
                print(f"Agent error: {exc}")
                continue

            print(f"Agent > {result}")
            print(f"state: {agent.state_machine.current_state.name}")
            if agent.last_trajectory_path:
                print(f"trajectory: {Path(agent.last_trajectory_path)}")
            print()
    finally:
        await agent.shutdown()

    print("bye")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(run_cli(args)))


if __name__ == "__main__":
    main()
