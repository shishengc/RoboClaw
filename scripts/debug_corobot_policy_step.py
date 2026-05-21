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

from test_infer_policy import (
    _json_safe,
    _make_dataloader_config,
    _observation_ready,
    _print_action_summary,
    _print_observation_summary,
    _save_images,
)


def _make_env_config(enable_cameras: bool = True) -> dict[str, Any]:
    dataloader_config = _make_dataloader_config()
    for camera_name in ("head", "hand_left", "hand_right"):
        dataloader_config["enabled_streams"]["camera"][camera_name] = enable_cameras

    return {
        "dataloader": dataloader_config,
        "motion_controller": {
            "enabled": True,
            "left_arm_dofs": 7,
            "right_arm_dofs": 7,
            "execution_timeout_s": 10.0,
        },
        "logging": {"level": "INFO"},
    }


def _default_reset_pose() -> dict[str, Any]:
    return {
        "target_grippers_positions": [0.0, 0.0],
        "target_arm_joint_positions": [
            -0.7776767611503601,
            0.6110292077064514,
            0.0,
            -1.2839710712432861,
            0.7304046154022217,
            1.4953951835632324,
            -0.18760496377944946,
            0.7775720357894897,
            -0.6110292077064514,
            0.0,
            1.284005880355835,
            -0.7304570078849792,
            -1.4953428506851196,
            0.18762239813804626,
        ],
        "target_head_positions": [0.0, 0.43633230555555524],
        "target_waist_positions": [0.8901176920412174, 0.4598677062988281],
    }


def _reset_grippers_pose(env: G01Env, target_grippers_positions: list[float] | None) -> None:
    if target_grippers_positions is None:
        return
    if len(target_grippers_positions) < 2:
        print(f"Reset gripper target has fewer than 2 values, skipped: {target_grippers_positions}")
        return

    action = Action(
        timestamps=int(time.time() * 1e9),
        trajectory_reference_time=1.0,
        base_link="base_link",
        left_effector=[[float(target_grippers_positions[0])]],
        right_effector=[[float(target_grippers_positions[1])]],
    )
    print(f"Resetting grippers first: {target_grippers_positions[:2]}")
    env.execute_action(action, wait_action_time=action.trajectory_reference_time)


def _reset_robot_pose(env: G01Env) -> None:
    reset_pose = _default_reset_pose()

    _reset_grippers_pose(env, reset_pose.get("target_grippers_positions"))

    arm_reset_pose = dict(reset_pose)
    arm_reset_pose["target_grippers_positions"] = None
    print("Resetting robot pose using ServicePolicyTask default pose...")
    env.reset(**arm_reset_pose)


def _read_ready_model_input(env: G01Env, attempts: int, interval: float) -> Any | None:
    for attempt in range(1, attempts + 1):
        candidate = env.get_std_model_input()
        ready, reason = _observation_ready(candidate)
        if ready:
            print(f"Observation ready on attempt {attempt}/{attempts}")
            return candidate
        print(f"[{attempt}/{attempts}] observation not ready: {reason}")
        time.sleep(interval)
    return None


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
        metadata = msgpack_numpy.unpackb(ws.recv())
        print(f"Policy metadata: {json.dumps(_json_safe(metadata), ensure_ascii=False)}")

        start = time.monotonic()
        ws.send(msgpack_numpy.packb(payload.model_dump()))
        response = msgpack_numpy.unpackb(ws.recv())
        elapsed_ms = (time.monotonic() - start) * 1000.0

    if isinstance(response, dict) and "error" in response:
        raise RuntimeError(response["error"])

    return Action(**response), metadata, elapsed_ms


def _load_action(path: Path) -> Action:
    if not path.exists():
        raise FileNotFoundError(f"Action JSON not found: {path}")
    return Action(**json.loads(path.read_text(encoding="utf-8")))


def _trajectory_len(action: Action) -> int:
    components = [
        action.left_arm.values if action.left_arm is not None else None,
        action.right_arm.values if action.right_arm is not None else None,
        action.waist,
        action.head,
        action.left_effector,
        action.right_effector,
        action.wheels,
    ]
    return max((len(item) for item in components if item is not None), default=0)


def _slice_rows(values: list[list[float]] | None, step_index: int) -> list[list[float]] | None:
    if values is None or step_index >= len(values):
        return None
    return [values[step_index]]


def _single_step_action(
    action: Action,
    step_index: int,
    step_duration: float,
    include_effectors: bool = True,
) -> Action:
    data: dict[str, Any] = {
        "timestamps": int(time.time() * 1e9),
        "trajectory_reference_time": step_duration,
        "base_link": action.base_link or "base_link",
    }

    if action.left_arm is not None and step_index < len(action.left_arm.values):
        data["left_arm"] = {
            "kind": action.left_arm.kind,
            "values": [action.left_arm.values[step_index]],
        }
    if action.right_arm is not None and step_index < len(action.right_arm.values):
        data["right_arm"] = {
            "kind": action.right_arm.kind,
            "values": [action.right_arm.values[step_index]],
        }

    names = ("head", "waist", "wheels")
    if include_effectors:
        names = names + ("left_effector", "right_effector")

    for name in names:
        rows = _slice_rows(getattr(action, name), step_index)
        if rows is not None:
            data[name] = rows

    return Action(**data)


def _max_abs_delta(current: list[float], target: list[float]) -> float:
    if not current or not target:
        return 0.0
    n = min(len(current), len(target))
    return float(np.max(np.abs(np.asarray(current[:n], dtype=np.float32) - np.asarray(target[:n], dtype=np.float32))))


def _step_delta_summary(
    observation_payload: Any,
    step_action: Action,
) -> dict[str, float]:
    states = observation_payload.observation.states
    arm = states.arm_joint_states or []
    grippers = states.gripper_states or []

    left_target = step_action.left_arm.values[0] if step_action.left_arm is not None else []
    right_target = step_action.right_arm.values[0] if step_action.right_arm is not None else []
    left_gripper_target = step_action.left_effector[0] if step_action.left_effector else []
    right_gripper_target = step_action.right_effector[0] if step_action.right_effector else []

    return {
        "left_arm": _max_abs_delta(arm[:7], left_target),
        "right_arm": _max_abs_delta(arm[7:14], right_target),
        "left_effector": _max_abs_delta(grippers[:1], left_gripper_target),
        "right_effector": _max_abs_delta(grippers[1:2], right_gripper_target),
    }


def _rad_to_deg(value: float) -> float:
    return value * 180.0 / np.pi


def _verify_step_error(
    observation_payload: Any,
    step_action: Action,
    joint_error_limit_deg: float,
    gripper_error_limit: float,
) -> bool:
    states = observation_payload.observation.states
    arm = states.arm_joint_states or []
    grippers = states.gripper_states or []

    checks: list[tuple[str, float, float, str]] = []

    if step_action.left_arm is not None and len(arm) >= 7:
        err = _max_abs_delta(arm[:7], step_action.left_arm.values[0])
        checks.append(("left_arm", _rad_to_deg(err), joint_error_limit_deg, "deg"))

    if step_action.right_arm is not None and len(arm) >= 14:
        err = _max_abs_delta(arm[7:14], step_action.right_arm.values[0])
        checks.append(("right_arm", _rad_to_deg(err), joint_error_limit_deg, "deg"))

    if step_action.left_effector and len(grippers) >= 1:
        err = _max_abs_delta(grippers[:1], step_action.left_effector[0])
        checks.append(("left_effector", err, gripper_error_limit, "unit"))

    if step_action.right_effector and len(grippers) >= 2:
        err = _max_abs_delta(grippers[1:2], step_action.right_effector[0])
        checks.append(("right_effector", err, gripper_error_limit, "unit"))

    ok = True
    print("  verification after execution:")
    for name, err, limit, unit in checks:
        passed = err <= limit
        ok = ok and passed
        status = "PASS" if passed else "FAIL"
        print(f"    [{status}] {name:14s} error={err:.4f} {unit}, limit={limit:.4f} {unit}")

    if not checks:
        print("    no comparable states found")
        return False

    return ok


def _print_step_preview(step_index: int, step_action: Action, deltas: dict[str, float]) -> None:
    left_values = step_action.left_arm.values[0] if step_action.left_arm is not None else []
    right_values = step_action.right_arm.values[0] if step_action.right_arm is not None else []
    left_effector = step_action.left_effector[0] if step_action.left_effector else []
    right_effector = step_action.right_effector[0] if step_action.right_effector else []

    print(f"\nStep {step_index} preview:")
    print(f"  duration: {step_action.trajectory_reference_time:.3f}s")
    print(f"  max delta left_arm:       {deltas['left_arm']:.4f}")
    print(f"  max delta right_arm:      {deltas['right_arm']:.4f}")
    print(f"  max delta left_effector:  {deltas['left_effector']:.4f}")
    print(f"  max delta right_effector: {deltas['right_effector']:.4f}")
    if left_values:
        print(f"  target left_arm:          {[round(v, 4) for v in left_values]}")
    if right_values:
        print(f"  target right_arm:         {[round(v, 4) for v in right_values]}")
    if left_effector or right_effector:
        print(f"  target grippers:          {left_effector + right_effector}")


def _confirm_step(args: argparse.Namespace, step_index: int) -> bool:
    if not args.execute:
        print("  preview only: add --execute to allow robot motion.")
        return False
    if args.yes:
        return True

    answer = input(f"Press Enter to run step {step_index}, type 'skip' to skip, or 'quit' to stop: ").strip().lower()
    if answer == "quit":
        raise KeyboardInterrupt
    if answer == "skip":
        return False
    return answer in ("", "execute", "run", "y", "yes")


def _validate_deltas(args: argparse.Namespace, deltas: dict[str, float]) -> bool:
    if args.force:
        return True

    max_arm_delta = max(deltas["left_arm"], deltas["right_arm"])

    if max_arm_delta > args.max_joint_delta:
        print(
            f"  blocked: arm delta {max_arm_delta:.4f} exceeds --max-joint-delta {args.max_joint_delta:.4f}."
        )
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Infer once, then manually confirm and execute policy steps one by one with post-step verification."
    )
    parser.add_argument("--policy-host", default="127.0.0.1", help="Local websocket host, usually the SSH tunnel host.")
    parser.add_argument("--policy-port", type=int, default=8999, help="Local websocket port.")
    parser.add_argument("--prompt", default="Grasp the target", help="Prompt sent to the policy.")
    parser.add_argument(
        "--action-source",
        choices=("infer", "json"),
        default="json",
        help="infer=re-run remote policy inference, json=load action from --action-json.",
    )
    parser.add_argument(
        "--action-json",
        default="artifacts/corobot_policy_dryrun/returned_action.json",
        help="Existing action JSON path used when --action-source json.",
    )
    parser.add_argument("--attempts", type=int, default=20, help="Observation fetch attempts.")
    parser.add_argument("--interval", type=float, default=0.25, help="Seconds between observation fetch attempts.")
    parser.add_argument("--timeout", type=float, default=30.0, help="WebSocket timeout in seconds.")
    parser.add_argument("--save-dir", default="artifacts/corobot_policy_step_debug", help="Directory for debug artifacts.")
    parser.add_argument("--start-step", type=int, default=0, help="First action step to preview/execute.")
    parser.add_argument("--step-count", type=int, default=0, help="Number of steps to preview/execute. Use 0 for all remaining.")
    parser.add_argument("--step-duration", type=float, default=0.35, help="Trajectory duration for each single-step action.")
    parser.add_argument("--pause", type=float, default=0.25, help="Sleep after each executed step.")
    parser.add_argument("--max-joint-delta", type=float, default=0.15, help="Safety limit for one-step arm joint delta.")
    parser.add_argument("--verify-joint-error-deg", type=float, default=5.0, help="Post-step arm joint error limit.")
    parser.add_argument("--verify-gripper-error", type=float, default=10.0, help="Post-step gripper error limit.")
    parser.add_argument("--skip-effectors", action="store_true", help="Do not include gripper/effectors in executed steps.")
    parser.add_argument("--no-reset-after", action="store_true", help="Do not reset robot pose after the debug range completes.")
    parser.add_argument("--execute", action="store_true", help="Allow robot motion. Without this, the script only previews.")
    parser.add_argument("--yes", action="store_true", help="Execute allowed steps without interactive confirmation.")
    parser.add_argument("--force", action="store_true", help="Ignore delta safety limits.")
    parser.add_argument("--no-save-images", action="store_true", help="Do not save captured images.")
    args = parser.parse_args()

    if args.start_step < 0:
        print("--start-step must be non-negative.", file=sys.stderr)
        return 2
    if args.step_count < 0:
        print("--step-count must be non-negative.", file=sys.stderr)
        return 2
    if args.yes and not args.execute:
        print("--yes has no effect without --execute.", file=sys.stderr)
        return 2

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    dds_env_set()

    env = None
    executed = 0
    verified = 0
    failed_verifications = 0

    try:
        env = G01Env(_make_env_config(enable_cameras=True))
        env.setup()

        payload = _read_ready_model_input(env, args.attempts, args.interval)
        if payload is None:
            print("No complete observation was captured.", file=sys.stderr)
            return 1

        payload.prompt = args.prompt
        _print_observation_summary(payload)

        if not args.no_save_images:
            _save_images(payload, save_dir)
            print(f"Saved images to {save_dir}")

        if args.action_source == "json":
            action_json_path = Path(args.action_json)
            action = _load_action(action_json_path)
            print(f"Loaded action from {action_json_path}")
        else:
            action, _, elapsed_ms = _infer_once(payload, args)
            print(f"Inference round trip: {elapsed_ms:.1f} ms")

        _print_action_summary(action)

        action_path = save_dir / "returned_action.json"
        action_path.write_text(
            json.dumps(_json_safe(action.model_dump()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Saved returned action to {action_path}")

        horizon = _trajectory_len(action)
        if horizon == 0:
            print("Returned action has no executable steps.", file=sys.stderr)
            return 1
        if args.start_step >= horizon:
            print(f"--start-step {args.start_step} is outside action horizon {horizon}.", file=sys.stderr)
            return 2

        end_step = horizon if args.step_count == 0 else min(horizon, args.start_step + args.step_count)
        print(f"Debug range: steps {args.start_step}..{end_step - 1} of {horizon}")

        if args.execute:
            print("Execute mode enabled. Each step requires manual confirmation.")
        else:
            print("Preview mode only. No action will be executed.")

        for step_index in range(args.start_step, end_step):
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
                continue
            if not _confirm_step(args, step_index):
                print("  skipped.")
                continue

            env.execute_action(step_action, wait_action_time=args.step_duration + args.pause)
            executed += 1
            print(f"  executed step {step_index}; reading state for verification...")

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

        if args.execute and executed > 0 and not args.no_reset_after:
            print("Debug range completed; running reset pose...")
            _reset_robot_pose(env)
            print("Reset pose command finished.")

        print(
            f"Done. Executed {executed} step(s), "
            f"verified {verified}, failed verifications {failed_verifications}."
        )
        return 0

    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 130
    finally:
        if env is not None:
            try:
                env.close()
            except Exception as e:
                print(f"Warning: failed to close env: {e}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
