from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from websockets.sync.client import connect

from corobot.envs.g01_env import G01Env
from corobot.protocol.protocol_schemas import Action
from corobot.transport import msgpack_numpy
from corobot.utils.dds_setting import dds_env_set

from debug_corobot_policy_receding import (
    _reset_after_interrupt,
    _save_action,
    _save_chunk_images,
)
from debug_corobot_policy_step import (
    _make_env_config,
    _max_abs_delta,
    _rad_to_deg,
    _read_ready_model_input,
    _trajectory_len,
)
from execute_corobot_action_chunk import _slice_action
from test_infer_policy import (
    _json_safe,
    _print_action_summary,
    _print_observation_summary,
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


def _without_effectors(action: Action) -> Action:
    data = action.model_dump()
    data["left_effector"] = None
    data["right_effector"] = None
    return Action(**data)


def _first_row(values: list[list[float]] | None) -> list[float]:
    return values[0] if values else []


def _last_row(values: list[list[float]] | None) -> list[float]:
    return values[-1] if values else []


def _chunk_delta_summary(observation_payload: Any, chunk_action: Action) -> dict[str, float]:
    states = observation_payload.observation.states
    arm = states.arm_joint_states or []

    left_first = _first_row(chunk_action.left_arm.values if chunk_action.left_arm is not None else None)
    right_first = _first_row(chunk_action.right_arm.values if chunk_action.right_arm is not None else None)
    left_values = chunk_action.left_arm.values if chunk_action.left_arm is not None else []
    right_values = chunk_action.right_arm.values if chunk_action.right_arm is not None else []

    left_adjacent = _max_adjacent_delta(left_values)
    right_adjacent = _max_adjacent_delta(right_values)

    return {
        "left_start": _max_abs_delta(arm[:7], left_first),
        "right_start": _max_abs_delta(arm[7:14], right_first),
        "left_adjacent": left_adjacent,
        "right_adjacent": right_adjacent,
    }


def _max_adjacent_delta(values: list[list[float]]) -> float:
    if len(values) < 2:
        return 0.0
    arr = np.asarray(values, dtype=np.float32)
    return float(np.max(np.abs(np.diff(arr, axis=0))))


def _print_chunk_preview(chunk_index: int, chunk_action: Action, deltas: dict[str, float]) -> None:
    left_values = chunk_action.left_arm.values if chunk_action.left_arm is not None else []
    right_values = chunk_action.right_arm.values if chunk_action.right_arm is not None else []
    left_effector = chunk_action.left_effector or []
    right_effector = chunk_action.right_effector or []

    print(f"\nContinuous chunk {chunk_index} preview:")
    print(f"  duration: {chunk_action.trajectory_reference_time:.3f}s")
    print(f"  left_arm steps:        {len(left_values)}")
    print(f"  right_arm steps:       {len(right_values)}")
    print(f"  start delta left_arm:  {deltas['left_start']:.4f} rad ({_rad_to_deg(deltas['left_start']):.2f} deg)")
    print(f"  start delta right_arm: {deltas['right_start']:.4f} rad ({_rad_to_deg(deltas['right_start']):.2f} deg)")
    print(f"  max step left_arm:     {deltas['left_adjacent']:.4f} rad ({_rad_to_deg(deltas['left_adjacent']):.2f} deg)")
    print(f"  max step right_arm:    {deltas['right_adjacent']:.4f} rad ({_rad_to_deg(deltas['right_adjacent']):.2f} deg)")
    if left_values:
        print(f"  first left_arm:        {[round(v, 4) for v in left_values[0]]}")
        print(f"  final left_arm:        {[round(v, 4) for v in left_values[-1]]}")
    if right_values:
        print(f"  first right_arm:       {[round(v, 4) for v in right_values[0]]}")
        print(f"  final right_arm:       {[round(v, 4) for v in right_values[-1]]}")
    if left_effector and right_effector:
        print(f"  first grippers:        {left_effector[0] + right_effector[0]}")
        print(f"  final grippers:        {left_effector[-1] + right_effector[-1]}")


def _validate_chunk(args: argparse.Namespace, deltas: dict[str, float]) -> bool:
    if args.force:
        return True

    max_start_delta = max(deltas["left_start"], deltas["right_start"])
    if max_start_delta > args.max_start_joint_delta:
        print(
            f"  blocked: chunk start delta {max_start_delta:.4f} exceeds "
            f"--max-start-joint-delta {args.max_start_joint_delta:.4f}."
        )
        return False

    max_adjacent_delta = max(deltas["left_adjacent"], deltas["right_adjacent"])
    if max_adjacent_delta > args.max_chunk_step_delta:
        print(
            f"  blocked: in-chunk adjacent delta {max_adjacent_delta:.4f} exceeds "
            f"--max-chunk-step-delta {args.max_chunk_step_delta:.4f}."
        )
        return False

    return True


def _confirm_chunk_execution(args: argparse.Namespace, chunk_index: int, step_count: int) -> bool:
    if not args.execute:
        print("  preview only: add --execute to allow robot motion.")
        return False
    if args.yes:
        return True

    answer = input(
        f"Press Enter to execute continuous chunk {chunk_index} ({step_count} steps), "
        "type 'skip' to skip, or 'quit' to stop: "
    ).strip().lower()
    if answer == "quit":
        raise KeyboardInterrupt
    if answer == "skip":
        return False
    return answer in ("", "execute", "run", "y", "yes")


def _confirm_next_inference(args: argparse.Namespace, chunk_index: int) -> bool:
    if args.yes:
        return True

    answer = input(
        f"Press Enter to start inference for next continuous chunk {chunk_index}, "
        "or type 'quit' to stop and reset: "
    ).strip().lower()
    if answer == "quit":
        raise KeyboardInterrupt
    return answer in ("", "continue", "next", "run", "y", "yes")


def _verify_chunk_final_state(
    observation_payload: Any,
    chunk_action: Action,
    joint_error_limit_deg: float,
    gripper_error_limit: float,
) -> bool:
    states = observation_payload.observation.states
    arm = states.arm_joint_states or []
    grippers = states.gripper_states or []

    checks: list[tuple[str, float, float, str]] = []

    if chunk_action.left_arm is not None and len(arm) >= 7:
        err = _max_abs_delta(arm[:7], _last_row(chunk_action.left_arm.values))
        checks.append(("left_arm", _rad_to_deg(err), joint_error_limit_deg, "deg"))

    if chunk_action.right_arm is not None and len(arm) >= 14:
        err = _max_abs_delta(arm[7:14], _last_row(chunk_action.right_arm.values))
        checks.append(("right_arm", _rad_to_deg(err), joint_error_limit_deg, "deg"))

    if chunk_action.left_effector and len(grippers) >= 1:
        err = _max_abs_delta(grippers[:1], _last_row(chunk_action.left_effector))
        checks.append(("left_effector", err, gripper_error_limit, "unit"))

    if chunk_action.right_effector and len(grippers) >= 2:
        err = _max_abs_delta(grippers[1:2], _last_row(chunk_action.right_effector))
        checks.append(("right_effector", err, gripper_error_limit, "unit"))

    ok = True
    print("  final verification after continuous chunk:")
    for name, err, limit, unit in checks:
        passed = err <= limit
        ok = ok and passed
        status = "PASS" if passed else "FAIL"
        print(f"    [{status}] {name:14s} error={err:.4f} {unit}, limit={limit:.4f} {unit}")

    if not checks:
        print("    no comparable states found")
        return False

    return ok


def _should_continue(args: argparse.Namespace, chunk_index: int, executed_steps: int) -> bool:
    if args.max_chunks > 0 and chunk_index >= args.max_chunks:
        return False
    if args.max_total_steps > 0 and executed_steps >= args.max_total_steps:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Continuous receding-horizon policy debug: infer an action chunk, execute the first N steps "
            "continuously, then wait for Enter before re-inferring from the latest observation."
        )
    )
    parser.add_argument("--policy-host", default="127.0.0.1", help="Local websocket host, usually the SSH tunnel host.")
    parser.add_argument("--policy-port", type=int, default=8999, help="Local websocket port.")
    parser.add_argument("--prompt", default="The left arm picks up the cylinder on the table")
    parser.add_argument("--attempts", type=int, default=20, help="Observation fetch attempts.")
    parser.add_argument("--interval", type=float, default=0.25, help="Seconds between observation fetch attempts.")
    parser.add_argument("--timeout", type=float, default=30.0, help="WebSocket timeout in seconds.")
    parser.add_argument(
        "--save-dir",
        default="artifacts/corobot_policy_receding_continuous_debug",
        help="Directory for continuous receding debug artifacts.",
    )
    parser.add_argument("--steps-per-chunk", type=int, default=20, help="Number of leading action steps to execute per inference.")
    parser.add_argument("--step-duration", type=float, default=0.05, help="Trajectory duration for each action step in the chunk.")
    parser.add_argument("--wait-extra", type=float, default=0.1, help="Extra wait after each continuous chunk execution.")
    parser.add_argument("--max-start-joint-delta", type=float, default=0.15, help="Max allowed current-to-first-row arm delta.")
    parser.add_argument("--max-chunk-step-delta", type=float, default=0.20, help="Max allowed adjacent arm delta inside a chunk.")
    parser.add_argument("--verify-joint-error-deg", type=float, default=8.0, help="Final arm joint error limit.")
    parser.add_argument("--verify-gripper-error", type=float, default=0.20, help="Final gripper error limit.")
    parser.add_argument("--max-chunks", type=int, default=0, help="Maximum inference chunks. Use 0 for unlimited.")
    parser.add_argument("--max-total-steps", type=int, default=0, help="Maximum executed action steps. Use 0 for unlimited.")
    parser.add_argument("--skip-effectors", action="store_true", help="Execute arms only; drop gripper commands.")
    parser.add_argument("--execute", action="store_true", help="Allow robot motion. Without this, the script only previews.")
    parser.add_argument("--yes", action="store_true", help="Execute/continue without interactive confirmation.")
    parser.add_argument("--force", action="store_true", help="Ignore chunk safety limits.")
    parser.add_argument("--no-save-images", action="store_true", help="Do not save captured observation images.")
    parser.add_argument("--no-verify", action="store_true", help="Do not read and verify final state after each chunk.")
    parser.add_argument("--no-reset-on-interrupt", action="store_true", help="Do not reset robot pose after Ctrl+C or 'quit'.")
    args = parser.parse_args()

    if args.steps_per_chunk <= 0:
        print("--steps-per-chunk must be positive.", file=sys.stderr)
        return 2
    if args.step_duration <= 0:
        print("--step-duration must be positive.", file=sys.stderr)
        return 2
    if args.wait_extra < 0:
        print("--wait-extra must be non-negative.", file=sys.stderr)
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
    executed_chunks = 0
    executed_steps = 0
    verified_chunks = 0
    failed_verifications = 0
    motion_started = False

    try:
        env = G01Env(_make_env_config(enable_cameras=True))
        env.setup()

        print(f"Fixed prompt for all inference rounds: {fixed_prompt!r}")
        if args.execute:
            print("Execute mode enabled. Each inference round executes a continuous chunk.")
        else:
            print("Preview mode only. No action will be executed.")

        while _should_continue(args, chunk_index, executed_steps):
            if chunk_index > 0 and args.execute:
                if not _confirm_next_inference(args, chunk_index):
                    continue

            print(f"\n=== Continuous inference chunk {chunk_index} ===")
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

            step_count = min(args.steps_per_chunk, horizon)
            if args.max_total_steps > 0:
                step_count = min(step_count, max(0, args.max_total_steps - executed_steps))
            if step_count <= 0:
                break

            chunk_duration = args.step_duration * step_count
            chunk_action = _slice_action(action, 0, step_count, chunk_duration)
            if args.skip_effectors:
                chunk_action = _without_effectors(chunk_action)

            current_payload = _read_ready_model_input(env, args.attempts, args.interval)
            if current_payload is None:
                print("Could not refresh observation before chunk execution; stopping.", file=sys.stderr)
                return 1

            deltas = _chunk_delta_summary(current_payload, chunk_action)
            _print_chunk_preview(chunk_index, chunk_action, deltas)

            if not _validate_chunk(args, deltas):
                return 1

            if not _confirm_chunk_execution(args, chunk_index, step_count):
                print("  skipped continuous chunk.")
                chunk_index += 1
                continue

            motion_started = True
            wait_action_time = chunk_action.trajectory_reference_time + args.wait_extra
            print(
                f"Executing continuous chunk {chunk_index}: {step_count} step(s), "
                f"trajectory_reference_time={chunk_action.trajectory_reference_time:.3f}s, "
                f"wait_action_time={wait_action_time:.3f}s"
            )
            env.execute_action(chunk_action, wait_action_time=wait_action_time)
            executed_chunks += 1
            executed_steps += step_count

            if not args.no_verify:
                after_payload = _read_ready_model_input(env, args.attempts, args.interval)
                if after_payload is None:
                    print("  verification failed: could not read observation after chunk execution")
                    failed_verifications += 1
                elif _verify_chunk_final_state(
                    after_payload,
                    chunk_action,
                    args.verify_joint_error_deg,
                    args.verify_gripper_error,
                ):
                    verified_chunks += 1
                else:
                    failed_verifications += 1

            chunk_index += 1

        print(
            f"Done. Inferred {chunk_index} chunk(s), executed {executed_chunks} continuous chunk(s), "
            f"executed {executed_steps} step(s), verified chunks {verified_chunks}, "
            f"failed verifications {failed_verifications}."
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
