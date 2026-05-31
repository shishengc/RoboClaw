"""
Terminal UI for BatchedNewAgent.

This entrypoint keeps the original TUI available. Use:
    PYTHONPATH=src python -m new_agent.batched_tui --mcp-control-tools
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from .core.batched_agent import BatchedNewAgent
from .demo_ui import run_demo_ui
from .tui import (
    build_mcp_guidance,
    build_mcp_task_plan,
    build_parser,
    build_shisheng_guidance,
    build_task_plan,
)


def print_banner(agent: BatchedNewAgent, mock_llm: bool, backend: str) -> None:
    print("=" * 72)
    print(f"BatchedNewAgent {backend} TUI")
    print(f"mode: {'mock-llm' if mock_llm else 'real-llm'}")
    print(f"state: {agent.state_machine.current_state.name}")
    print("Type a robot task in natural language, e.g.")
    print("  把tag 0放tag 1上，这是一个装配任务")
    print("Commands: /help, /tools, /status, /trajectory, /quit")
    print("=" * 72)


async def run_tui(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s:%(name)s:%(message)s",
    )

    if args.agent_name == "ShishengTUIAgent":
        args.agent_name = "BatchedShishengTUIAgent"

    api_key = args.api_key or os.getenv("OPENAI_API_KEY")
    backend = "mcp_control" if args.mcp_control_tools else "shisheng"
    guidance = (
        build_mcp_guidance(args.guidance)
        if args.mcp_control_tools
        else build_shisheng_guidance(args.shisheng_root, args.guidance)
    )
    agent = BatchedNewAgent(agent_name=args.agent_name, config_path=args.config)
    if args.model:
        agent.config.model = args.model

    await agent.init_agent(
        task_brief="等待操作者输入机器人任务",
        action_guidance=guidance,
        use_real_llm=not args.mock_llm,
        openai_api_key=api_key,
        config_path=args.config,
        use_shisheng_tools=not args.mcp_control_tools,
        shisheng_repo_root=args.shisheng_root,
        use_mcp_control_tools=args.mcp_control_tools,
        corobot_base_url=args.corobot_url,
    )

    if args.demo_ui:
        task_plan_builder = build_mcp_task_plan if args.mcp_control_tools else build_task_plan
        try:
            return await run_demo_ui(
                args,
                agent=agent,
                guidance=guidance,
                task_plan_builder=task_plan_builder,
            )
        finally:
            await agent.shutdown()

    print_banner(agent, args.mock_llm, backend)
    try:
        while True:
            try:
                user_input = input("\nTask > ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            command = user_input.lower()
            if command in {"/quit", "/exit", "quit", "exit"}:
                break
            if command == "/help":
                print("Commands:")
                print("  /tools       Show registered tools")
                print("  /status      Show state and task progress")
                print("  /trajectory  Show last trajectory path")
                print("  /quit        Exit")
                print("Any other input starts a fresh robot task.")
                continue
            if command == "/tools":
                print("\n".join(f"- {tool}" for tool in agent.tool_registry.list_tools()))
                continue
            if command == "/status":
                print(f"state: {agent.state_machine.current_state.name}")
                print(f"progress: {agent.memory_manager.get_current_task_progress():.1f}%")
                print(agent.memory_manager.get_progress_summary())
                continue
            if command == "/trajectory":
                print(agent.last_trajectory_path or "No trajectory saved yet.")
                continue

            agent.start_task(
                task_brief=user_input,
                action_guidance=guidance,
                task_plan=build_mcp_task_plan() if args.mcp_control_tools else build_task_plan(),
            )
            print("Batched agent is solving the task. Tool calls may take a while...")
            try:
                result = await agent.run_once(user_input)
            except KeyboardInterrupt:
                print("\nInterrupted by operator.")
                continue
            except Exception as exc:
                print(f"Agent error: {exc}")
                if agent.last_trajectory_path:
                    print(f"trajectory: {Path(agent.last_trajectory_path)}")
                continue

            print(f"\nAgent > {result}")
            if agent.last_trajectory_path:
                print(f"trajectory: {Path(agent.last_trajectory_path)}")
    finally:
        await agent.shutdown()

    print("bye")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(run_tui(args)))
    except KeyboardInterrupt:
        print("\nbye")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
