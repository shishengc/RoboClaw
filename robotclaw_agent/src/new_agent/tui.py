"""
Lightweight terminal UI for running NewAgent with real robot tools.

This intentionally uses only the Python standard library. It is a practical TUI
loop: type a natural-language task, let the agent decide and call tools, inspect
status/trajectory, then submit another task.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from .core.agent import NewAgent
from .demo_ui import run_demo_ui


DEFAULT_SHISHENG_ROOT = "/home/easyai/桌面/shisheng"


def build_shisheng_guidance(shisheng_root: str, extra_guidance: str = "") -> str:
    recipe = """
You are controlling the shisheng GenieSim robot through registered tools.

The concrete tool list and schemas are supplied through the OpenAI tools API.
Do not rely on a fixed recipe document. Choose tools from the registered tool
schemas the same way a code agent chooses shell/editor tools: inspect the goal,
choose the smallest applicable tool call, read the result, then decide the next
tool call from the updated state.

Before choosing tools, infer task roles from the user's request:
- manipulated object: the object that must be moved, grasped, lifted, opened, pushed, or otherwise changed.
- destination/support object: the object, area, pose, or relation that defines where the manipulated object should end up.
- post-condition: required final state, such as released gripper, raised hand, default pose, or verified object relation.

For pick-and-place or block-on-block tasks:
- Use the manipulated object as the target for LocalizeTarget, ApproachTarget, AlignTarget,
  CheckGraspReady, and GraspAtCurrent until it is successfully grasped.
- The destination/support object may be localized before grasping, but do not approach,
  align to, or grasp it unless the user specifically asks to manipulate that object.
- After GraspAtCurrent succeeds, lift the held object with MoveEndEffectorToWorld,
  then use PlaceHeldObject with held_object set to the manipulated object and target set
  from the destination/support object or requested placement relation.
- Use geometry localization unless there is a strong reason to use a camera.
- Use grasp_offset_m [0.015, 0.015, -0.02] and approach_offset_m [0.015, 0.015, 0.02]
  for block-like tabletop objects unless perception indicates a better grasp point.
- If CheckGraspReady is false, retry AlignTarget or re-observe before closing.
- Lift after grasp with MoveEndEffectorToWorld relative_delta_m [0, 0, 0.17].
- Place with hover_height_m 0.08 and place_target_z_offset_m 0.005 for block support targets.
- After placement, call VerifyTaskState with a goal derived from the user's requested relation.
  For a block-on-block relation, use:
  {"goal":{"type":"block_on_block","source":"<manipulated_object>","target":"<support_object>"}}

Do not invent unsupported high-level tools. Use the registered shisheng tools.
Treat partial tool results as not fully successful; inspect their data and retry
or recover before moving to risky steps.
"""
    if extra_guidance:
        recipe += "\nOperator guidance:\n" + extra_guidance
    return recipe


def build_task_plan() -> list[str]:
    return [
        "ensure abs_pose bridge is running",
        "localize the source and relevant target objects",
        "approach the source object with the gripper open",
        "align to the grasp point and verify grasp readiness",
        "close the gripper and verify grasp state",
        "lift the held object",
        "place the held object at the requested target",
        "return to a safe/default pose if appropriate",
        "sense the environment and verify the user goal",
        "finalize only after the goal is verified",
    ]


def build_mcp_guidance(extra_guidance: str = "") -> str:
    recipe = """
You are controlling the real CoRobot/RoboClaw robot through mcp_control tools.

The concrete tool list and schemas are supplied through the OpenAI tools API.
For AprilTag grasp tasks, compose atomic tools in this order:
detect_tags, get_apriltag_pose, resolve_tag_pick_place_recipe,
compute_tag_grasp_targets, open_gripper,
move_eef to approach_camera_m, move_eef to grasp_camera_m, close_gripper,
move_eef to lift_camera_m.

For AprilTag pick-and-place tasks, first infer the source tag, destination tag,
and relation from the user's wording. Use relation="on" for assembly/on-top
tasks and relation="inside" for sorting/inside/container tasks. For these normal
pick-and-place tasks, prefer prepare_tag_pick_place as the first tool call. It
refreshes detections, reads fresh source/destination poses, resolves the recipe,
and computes grasp/place targets in one deterministic step.

1. If the operator asks for a reset, call reset_robot first and continue only
   after it succeeds.
2. For pick-and-place, call prepare_tag_pick_place with source_tag_id,
   destination_tag_id, and relation. Use the returned grasp_targets and
   place_targets. Do not hand-code offsets in normal tasks; recipe parameters
   are owned by the mcp_control recipe registry.
3. If prepare_tag_pick_place reports missing tags, do not move blindly; ask the
   operator to make the missing tag visible.
4. Execute the grasp sequence with right arm:
   open_gripper -> move_eef(approach_camera_m) -> move_eef(grasp_camera_m) ->
   close_gripper -> move_eef(lift_camera_m). Never move to lift_camera_m before
   close_gripper succeeds at grasp_camera_m.
5. Execute the placement sequence with right arm:
   move_eef(place_hover_camera_m) -> move_eef(place_camera_m) ->
   open_gripper.
6. After release, call SenseEnvironment with include_tags=true. Finalize only
   if tool outputs show the sequence executed and fresh perception does not
   contradict the requested tag relation.

Use reset_robot only when the user asks to reset or when a safe restart is
needed. After FinalizeTask succeeds, the runtime will automatically call
reset_robot; do not add an extra final reset yourself. Use SenseEnvironment after
motion to inspect robot/tool status. If AprilTag detection is stale or missing,
do not move blindly; ask the operator to make the tag visible unless they
explicitly permit stale poses.
"""
    if extra_guidance:
        recipe += "\nOperator guidance:\n" + extra_guidance
    return recipe


def build_mcp_task_plan() -> list[str]:
    return [
        "understand whether the user wants reset, grasp, release, or status",
        "use prepare_tag_pick_place to refresh tags and compute AprilTag motion targets",
        "execute pick and place with open_gripper, move_eef, close_gripper, move_eef, and open_gripper",
        "verify status with SenseEnvironment after motion",
        "finalize only after tool output confirms the requested action",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run NewAgent as a lightweight robot TUI.")
    parser.add_argument("--agent-name", default="ShishengTUIAgent")
    parser.add_argument("--mcp-control-tools", action="store_true", help="Use real CoRobot mcp_control tools.")
    parser.add_argument("--corobot-url", default=None, help="CoRobot base URL. Defaults to COROBOT_URL or http://localhost:8765.")
    parser.add_argument("--shisheng-root", default=DEFAULT_SHISHENG_ROOT)
    parser.add_argument("--config", default=None)
    parser.add_argument("--api-key", default=None, help="OpenAI API key. Defaults to OPENAI_API_KEY.")
    parser.add_argument("--model", default=None, help="Override configured model.")
    parser.add_argument("--mock-llm", action="store_true", help="Use mock LLM. Useful only for framework debugging.")
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--guidance", default="", help="Additional operator guidance appended to the system prompt.")
    parser.add_argument("--demo-ui", action="store_true", help="Start the browser demo UI instead of the terminal loop.")
    parser.add_argument("--host", default="127.0.0.1", help="Host for --demo-ui.")
    parser.add_argument("--port", type=int, default=7860, help="Port for --demo-ui.")
    return parser


def print_banner(agent: NewAgent, mock_llm: bool, backend: str) -> None:
    print("=" * 72)
    print(f"NewAgent {backend} TUI")
    print(f"mode: {'mock-llm' if mock_llm else 'real-llm'}")
    print(f"state: {agent.state_machine.current_state.name}")
    print("Type a robot task in natural language, e.g.")
    print("  把红色方块放到黄色方块上")
    print("Commands: /help, /tools, /status, /trajectory, /quit")
    print("=" * 72)


async def run_tui(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s:%(name)s:%(message)s",
    )

    api_key = args.api_key or os.getenv("OPENAI_API_KEY")
    backend = "mcp_control" if args.mcp_control_tools else "shisheng"
    guidance = (
        build_mcp_guidance(args.guidance)
        if args.mcp_control_tools
        else build_shisheng_guidance(args.shisheng_root, args.guidance)
    )
    agent = NewAgent(agent_name=args.agent_name, config_path=args.config)
    if args.model:
        agent.config.model = args.model

    await agent.init_agent(
        task_brief="等待操作者输入 shisheng 机器人任务",
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
            print("Agent is solving the task. Tool calls may take a while...")
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
