from __future__ import annotations

import argparse
import sys
from pathlib import Path

from corobot.envs.g01_env import G01Env
from corobot.protocol.protocol_schemas import Action
from corobot.utils.dds_setting import dds_env_set

from debug_corobot_policy_auto_loop import (
    _model_dump,
    close_env_safely,
    create_run_action_log,
    execute_inferred_chunk,
    read_prompt_file,
    reset_robot_pose_safely,
    trajectory_len,
    _make_env_config,
)

DEFAULT_PROMPT = "Pull open the drawer"


def _format_prompt(prompt: str, limit: int = 120) -> str:
    one_line = " ".join(prompt.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3] + "..."


def _prompt_for_next_chunk(current_prompt: str) -> tuple[str, bool]:
    while True:
        try:
            answer = input(
                "\nNext chunk. Current prompt: "
                f"{_format_prompt(current_prompt)!r}\n"
                "Press Enter to infer/execute next chunk, "
                "type 'prompt <new prompt>' to switch, "
                "type 'prompt' to enter a new prompt, or 'quit' to stop: "
            ).strip()
        except EOFError:
            return current_prompt, False

        lowered = answer.lower()
        if lowered in {"", "continue", "next", "run", "y", "yes"}:
            return current_prompt, True
        if lowered in {"quit", "exit", "stop"}:
            return current_prompt, False
        if lowered in {"show", "s"}:
            print(f"Current prompt: {current_prompt!r}")
            continue

        next_prompt: str | None = None
        if lowered.startswith("prompt "):
            next_prompt = answer[len("prompt ") :].strip()
        elif lowered.startswith("prompt="):
            next_prompt = answer[len("prompt=") :].strip()
        elif lowered in {"prompt", "p", "edit"}:
            try:
                next_prompt = input("New prompt: ").strip()
            except EOFError:
                return current_prompt, False

        if next_prompt is None:
            print("Unrecognized command.")
            continue
        if not next_prompt:
            print("Prompt unchanged.")
            continue
        return next_prompt, True
 

def _force_gripper_closed(action: Action, side: str) -> Action:
    horizon = trajectory_len(action)
    if horizon <= 0:
        raise RuntimeError("Cannot force gripper: returned action has no trajectory steps.")

    data = _model_dump(action)
    data[f"{side}_effector"] = [[1.0] for _ in range(horizon)]
    return Action(**data)


def _prompt_gripper_override(action: Action) -> Action:
    while True:
        try:
            answer = input(
                "Returned action ready. Press Enter to execute unchanged, "
                "or type left/right to force that gripper closed: "
            ).strip().lower()
        except EOFError:
            print("No gripper override provided; executing unchanged action.")
            return action

        if answer == "":
            print("No gripper override selected; executing unchanged action.")
            return action

        if answer in {"left", "l"}:
            side = "left"
            break
        if answer in {"right", "r"}:
            side = "right"
            break
        print("Please type left or right.")

    modified_action = _force_gripper_closed(action, side)
    print(f"Forced {side} gripper to 1.0 for {trajectory_len(modified_action)} step(s).")
    return modified_action


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Interactive receding-horizon CoRobot policy loop. It infers from the "
            "current prompt, executes one leading action chunk, then lets the "
            "operator keep or change the prompt before the next chunk."
        )
    )
    parser.add_argument("--policy-host", default="127.0.0.1")
    parser.add_argument("--policy-port", type=int, default=8999)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--prompt-file", default=None, help="Optional file polled before every chunk prompt.")
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--save-dir", default="artifacts/corobot_policy_prompt_loop")
    parser.add_argument("--steps-per-chunk", type=int, default=30)
    parser.add_argument("--max-chunks", type=int, default=0, help="0 means run until the operator stops.")
    parser.add_argument("--no-reset-on-interrupt", action="store_true")
    args = parser.parse_args()

    if args.steps_per_chunk <= 0:
        print("--steps-per-chunk must be positive.", file=sys.stderr)
        return 2

    dds_env_set()
    save_dir = Path(args.save_dir)
    run_log_path = create_run_action_log(save_dir, "prompt_loop", args, args.prompt)
    env: G01Env | None = None
    prompt = args.prompt
    chunk_index = 0
    executed_any = False

    try:
        env = G01Env(_make_env_config(enable_cameras=True))
        env.setup()

        print("Interactive prompt loop started. The robot executes one chunk per inference.")
        print(f"Initial prompt: {prompt!r}")

        while args.max_chunks <= 0 or chunk_index < args.max_chunks:
            prompt = read_prompt_file(args.prompt_file, prompt)
            if chunk_index > 0:
                prompt, should_continue = _prompt_for_next_chunk(prompt)
                if not should_continue:
                    break

            executed_steps = execute_inferred_chunk(
                env,
                args,
                prompt=prompt,
                chunk_index=chunk_index,
                save_dir=save_dir,
                run_log_path=run_log_path,
                action_modifier=_prompt_gripper_override,
            )
            print(f"Executed chunk {chunk_index}: {executed_steps} step(s).")
            executed_any = True
            chunk_index += 1

        if executed_any and not args.no_reset_on_interrupt:
            reset_robot_pose_safely(env, reason="normal exit")
        return 0
    except KeyboardInterrupt:
        print("\nStopped by user.")
        if not args.no_reset_on_interrupt:
            reset_robot_pose_safely(env, reason="interrupt")
        return 130
    except Exception as exc:
        print(f"Prompt loop failed: {exc}", file=sys.stderr)
        if not args.no_reset_on_interrupt:
            reset_robot_pose_safely(env, reason="failure")
        return 1
    finally:
        close_env_safely(env)


if __name__ == "__main__":
    raise SystemExit(main())
