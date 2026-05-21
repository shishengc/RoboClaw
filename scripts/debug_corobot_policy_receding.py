from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from websockets.sync.client import connect

from corobot.envs.g01_env import G01Env
from corobot.protocol.protocol_schemas import Action
from corobot.transport import msgpack_numpy
from corobot.utils.dds_setting import dds_env_set

from debug_corobot_policy_step import (
    _make_env_config,
    _print_step_preview,
    _read_ready_model_input,
    _reset_robot_pose,
    _single_step_action,
    _step_delta_summary,
    _trajectory_len,
    _validate_deltas,
    _verify_step_error,
)
from test_infer_policy import (
    _json_safe,
    _print_action_summary,
    _print_observation_summary,
    _save_images,
    _unpack_policy_frame,
)


def _infer_once(payload: Any, args: argparse.Namespace) -> tuple[Action, dict[str, Any], float]:
    url = f"ws://{args.policy_host}:{args.policy_port}"
    print(f"Connecting policy: {url}")

    with connect(
        url,
        compression=None,
        max_size=None,
        open_timeout=args.timeout,
        close_timeout=args.timeout,
    ) as ws:
        metadata = _unpack_policy_frame(ws.recv(), "metadata")
        print(f"Policy metadata: {json.dumps(_json_safe(metadata), ensure_ascii=False)}")
        if isinstance(metadata, dict) and "camera_names" not in metadata:
            print(
                "Warning: policy metadata has no 'camera_names'. "
                "You may be connected to the default OpenPI websocket server, not serve_corobotpolicy.py.",
                file=sys.stderr,
            )

        start = time.monotonic()
        ws.send(msgpack_numpy.packb(payload.model_dump()))
        response = _unpack_policy_frame(ws.recv(), "response")
        elapsed_ms = (time.monotonic() - start) * 1000.0

    if isinstance(response, dict) and "error" in response:
        raise RuntimeError(response["error"])

    return Action(**response), metadata, elapsed_ms


def _save_action(action: Action, save_dir: Path, chunk_index: int) -> None:
    action_data = json.dumps(_json_safe(action.model_dump()), ensure_ascii=False, indent=2)
    chunk_path = save_dir / f"action_chunk_{chunk_index:04d}.json"
    latest_path = save_dir / "latest_action.json"
    chunk_path.write_text(action_data, encoding="utf-8")
    latest_path.write_text(action_data, encoding="utf-8")
    print(f"Saved action chunk to {chunk_path}")


def _save_chunk_images(payload: Any, save_dir: Path, chunk_index: int, no_save_images: bool) -> None:
    if no_save_images:
        return
    chunk_dir = save_dir / f"chunk_{chunk_index:04d}_observation"
    _save_images(payload, chunk_dir)
    print(f"Saved observation images to {chunk_dir}")


def _confirm_step(args: argparse.Namespace, chunk_index: int, step_index: int, global_step: int) -> bool:
    if not args.execute:
        print("  preview only: add --execute to allow robot motion.")
        return False
    if args.yes:
        return True

    answer = input(
        f"Press Enter to run chunk {chunk_index} step {step_index} "
        f"(global {global_step}), type 'skip' to skip, or 'quit' to stop: "
    ).strip().lower()
    if answer == "quit":
        raise KeyboardInterrupt
    if answer == "skip":
        return False
    return answer in ("", "execute", "run", "y", "yes")


def _should_continue(args: argparse.Namespace, chunk_index: int, total_steps_seen: int) -> bool:
    if args.max_chunks > 0 and chunk_index >= args.max_chunks:
        return False
    if args.max_total_steps > 0 and total_steps_seen >= args.max_total_steps:
        return False
    return True


def _reset_after_interrupt(env: G01Env | None, args: argparse.Namespace, motion_started: bool) -> None:
    if env is None or not args.execute or not motion_started or args.no_reset_on_interrupt:
        return

    print("Interrupt received; resetting robot pose before exit...")
    try:
        _reset_robot_pose(env)
        print("Reset pose command finished.")
    except Exception as e:
        print(f"Reset after interrupt failed: {e}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Receding-horizon policy debug: repeatedly infer a 50-step action chunk, "
            "execute the first N steps one by one, then infer again from the latest observation."
        )
    )
    parser.add_argument("--policy-host", default="127.0.0.1", help="Local websocket host, usually the SSH tunnel host.")
    parser.add_argument("--policy-port", type=int, default=8999, help="Local websocket port.")
    parser.add_argument("--prompt", default="Grasp the target", help="Prompt reused unchanged for every inference.")
    parser.add_argument("--attempts", type=int, default=20, help="Observation fetch attempts.")
    parser.add_argument("--interval", type=float, default=0.25, help="Seconds between observation fetch attempts.")
    parser.add_argument("--timeout", type=float, default=30.0, help="WebSocket timeout in seconds.")
    parser.add_argument(
        "--save-dir",
        default="artifacts/corobot_policy_receding_debug",
        help="Directory for receding debug artifacts.",
    )
    parser.add_argument(
        "--steps-per-chunk",
        type=int,
        default=30,
        help="How many leading steps to process from each inferred action chunk before re-inference.",
    )
    parser.add_argument("--step-duration", type=float, default=0.35, help="Trajectory duration for each single-step action.")
    parser.add_argument("--pause", type=float, default=0.25, help="Sleep after each executed step.")
    parser.add_argument("--max-joint-delta", type=float, default=0.15, help="Safety limit for one-step arm joint delta.")
    parser.add_argument("--verify-joint-error-deg", type=float, default=5.0, help="Post-step arm joint error limit.")
    parser.add_argument("--verify-gripper-error", type=float, default=10.0, help="Post-step gripper error limit.")
    parser.add_argument("--max-chunks", type=int, default=0, help="Maximum inference chunks. Use 0 for unlimited.")
    parser.add_argument("--max-total-steps", type=int, default=0, help="Maximum processed step indices. Use 0 for unlimited.")
    parser.add_argument("--skip-effectors", action="store_true", help="Do not include gripper/effectors in executed steps.")
    parser.add_argument("--execute", action="store_true", help="Allow robot motion. Without this, the script only previews.")
    parser.add_argument("--yes", action="store_true", help="Execute allowed steps without interactive confirmation.")
    parser.add_argument("--force", action="store_true", help="Ignore delta safety limits.")
    parser.add_argument("--no-save-images", action="store_true", help="Do not save captured observation images.")
    parser.add_argument("--no-reset-on-interrupt", action="store_true", help="Do not reset robot pose after Ctrl+C or 'quit'.")
    args = parser.parse_args()

    if args.steps_per_chunk <= 0:
        print("--steps-per-chunk must be positive.", file=sys.stderr)
        return 2
    if args.max_chunks < 0:
        print("--max-chunks must be non-negative.", file=sys.stderr)
        return 2
    if args.max_total_steps < 0:
        print("--max-total-steps must be non-negative.", file=sys.stderr)
        return 2
    if args.yes and not args.execute:
        print("--yes has no effect without --execute.", file=sys.stderr)
        return 2
    if not args.execute and args.max_chunks == 0:
        args.max_chunks = 1
        print("Preview mode without --max-chunks would run forever; limiting preview to one chunk.")

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    fixed_prompt = args.prompt
    dds_env_set()

    env = None
    chunk_index = 0
    total_steps_seen = 0
    executed = 0
    verified = 0
    failed_verifications = 0
    motion_started = False

    try:
        env = G01Env(_make_env_config(enable_cameras=True))
        env.setup()

        print(f"Fixed prompt for all inference rounds: {fixed_prompt!r}")
        if args.execute:
            print("Execute mode enabled. Each step requires manual confirmation.")
        else:
            print("Preview mode only. No action will be executed.")

        while _should_continue(args, chunk_index, total_steps_seen):
            print(f"\n=== Inference chunk {chunk_index} ===")
            payload = _read_ready_model_input(env, args.attempts, args.interval)
            if payload is None:
                print("No complete observation was captured.", file=sys.stderr)
                return 1

            payload.prompt = fixed_prompt
            _print_observation_summary(payload)
            _save_chunk_images(payload, save_dir, chunk_index, args.no_save_images)

            action, _, elapsed_ms = _infer_once(payload, args)
            print(f"Inference round trip: {elapsed_ms:.1f} ms")
            _print_action_summary(action)
            _save_action(action, save_dir, chunk_index)

            horizon = _trajectory_len(action)
            if horizon == 0:
                print("Returned action has no executable steps.", file=sys.stderr)
                return 1

            steps_this_chunk = min(args.steps_per_chunk, horizon)
            print(f"Processing steps 0..{steps_this_chunk - 1} from chunk {chunk_index} of horizon {horizon}")

            for step_index in range(steps_this_chunk):
                if args.max_total_steps > 0 and total_steps_seen >= args.max_total_steps:
                    break

                current_payload = _read_ready_model_input(env, args.attempts, args.interval)
                if current_payload is None:
                    print("Could not refresh observation before this step; stopping.", file=sys.stderr)
                    return 1

                step_action = _single_step_action(
                    action,
                    step_index,
                    args.step_duration,
                    include_effectors=not args.skip_effectors,
                )
                deltas = _step_delta_summary(current_payload, step_action)
                _print_step_preview(step_index, step_action, deltas)

                if not _validate_deltas(args, deltas):
                    total_steps_seen += 1
                    continue
                if not _confirm_step(args, chunk_index, step_index, total_steps_seen):
                    print("  skipped.")
                    total_steps_seen += 1
                    continue

                motion_started = True
                env.execute_action(step_action, wait_action_time=args.step_duration + args.pause)
                executed += 1
                total_steps_seen += 1
                print(f"  executed chunk {chunk_index} step {step_index}; reading state for verification...")

                after_payload = _read_ready_model_input(env, args.attempts, args.interval)
                if after_payload is None:
                    print("  verification failed: could not read observation after execution")
                    failed_verifications += 1
                    continue

                if _verify_step_error(
                    after_payload,
                    step_action,
                    args.verify_joint_error_deg,
                    args.verify_gripper_error,
                ):
                    verified += 1
                else:
                    failed_verifications += 1

            chunk_index += 1

        print(
            f"Done. Inferred {chunk_index} chunk(s), processed {total_steps_seen} step index(es), "
            f"executed {executed} step(s), verified {verified}, failed verifications {failed_verifications}."
        )
        return 0

    except KeyboardInterrupt:
        print("\nStopped by user.")
        _reset_after_interrupt(env, args, motion_started)
        return 130
    finally:
        if env is not None:
            try:
                env.close()
            except Exception as e:
                print(f"Warning: failed to close env: {e}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
