from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from corobot.envs.g01_env import G01Env
from corobot.protocol.protocol_schemas import Action
from corobot.utils.dds_setting import dds_env_set


DEFAULT_ACTION_JSON = Path("artifacts/corobot_policy_step_debug/left_arm_grasp.json")
DEFAULT_EXECUTED_ACTIONS_JSON = Path(
    "artifacts/corobot_policy_receding_continuous_reverse_debug/executed_actions.json"
)
GRIPPER_STATE_OPEN = 0.0
GRIPPER_STATE_CLOSE = 120.0
EXECUTION_BATCH_STEPS = 1


def _make_env_config() -> dict[str, Any]:
    return {
        "dataloader": {
            "data_source": {"ALIGNED_ROBOT": {"enabled": True}},
            "enabled_streams": {
                "camera": {
                    "head": False,
                    "hand_left": False,
                    "hand_right": False,
                    "head_depth": False,
                    "hand_left_depth": False,
                    "hand_right_depth": False,
                },
                "robot_states": {
                    "arm_joint_states": True,
                    "gripper_states": True,
                    "head_joint_states": True,
                    "waist_joint_states": True,
                },
            },
        },
        "motion_controller": {
            "enabled": True,
            "left_arm_dofs": 7,
            "right_arm_dofs": 7,
            "execution_timeout_s": 10.0,
        },
        "logging": {"level": "INFO"},
    }


def _load_action(path: Path) -> Action:
    if not path.exists():
        raise FileNotFoundError(f"Action JSON not found: {path}")
    return Action(**json.loads(path.read_text(encoding="utf-8")))


def _print_action_summary(action: Action) -> None:
    left_values = action.left_arm.values if action.left_arm is not None else []
    right_values = action.right_arm.values if action.right_arm is not None else []
    left_effector = action.left_effector or []
    right_effector = action.right_effector or []

    print("Action chunk:")
    print(f"  trajectory_reference_time: {action.trajectory_reference_time:.3f}s")
    print(f"  left_arm:       steps={len(left_values)}, dim={len(left_values[0]) if left_values else 0}")
    print(f"  right_arm:      steps={len(right_values)}, dim={len(right_values[0]) if right_values else 0}")
    print(f"  left_effector:  steps={len(left_effector)}, dim={len(left_effector[0]) if left_effector else 0}")
    print(f"  right_effector: steps={len(right_effector)}, dim={len(right_effector[0]) if right_effector else 0}")
    if left_values:
        print(f"  first left_arm:  {[round(v, 4) for v in left_values[0]]}")
    if right_values:
        print(f"  first right_arm: {[round(v, 4) for v in right_values[0]]}")
    if left_effector and right_effector:
        print(f"  first grippers:  {[round(left_effector[0][0], 4), round(right_effector[0][0], 4)]}")


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


def _read_ready_model_input(env: G01Env, attempts: int, interval: float) -> Any | None:
    for attempt in range(1, attempts + 1):
        payload = env.get_std_model_input()
        ready, reason = _robot_state_ready(payload)
        if ready:
            print(f"Robot state ready on attempt {attempt}/{attempts}")
            return payload
        print(f"[{attempt}/{attempts}] robot state not ready: {reason}")
        time.sleep(interval)
    return None


def _robot_state_ready(payload: Any) -> tuple[bool, str]:
    if payload is None:
        return False, "payload is None"

    states = payload.observation.states
    arm = states.arm_joint_states or []
    gripper = states.gripper_states or []

    if len(arm) < 14:
        return False, f"arm_joint_states has {len(arm)} value(s), expected at least 14"
    if len(gripper) < 2:
        return False, f"gripper_states has {len(gripper)} value(s), expected at least 2"

    return True, "ok"


def _max_abs_delta(current: list[float], target: list[float]) -> float:
    if not current or not target:
        return 0.0
    n = min(len(current), len(target))
    return float(np.max(np.abs(np.asarray(current[:n], dtype=np.float32) - np.asarray(target[:n], dtype=np.float32))))


def _rad_to_deg(value: float) -> float:
    return value * 180.0 / np.pi


def _first_row(values: list[list[float]] | None) -> list[float]:
    return values[0] if values else []


def _last_row(values: list[list[float]] | None) -> list[float]:
    return values[-1] if values else []


def _action_without_effectors(action: Action) -> Action:
    data = action.model_dump()
    data["left_effector"] = None
    data["right_effector"] = None
    return Action(**data)


def _action_rows(action: Action, name: str) -> list[list[float]]:
    if name in ("left_arm", "right_arm"):
        component = getattr(action, name)
        return component.values if component is not None else []
    return getattr(action, name) or []


def _first_kind(actions: list[Action], name: str) -> Any | None:
    for action in actions:
        component = getattr(action, name)
        if component is not None:
            return component.kind
    return None


def _combine_action_chunks(actions: list[Action], trajectory_reference_time: float) -> Action | None:
    if not actions:
        return None

    data: dict[str, Any] = {
        "timestamps": int(time.time() * 1e9),
        "trajectory_reference_time": trajectory_reference_time,
        "base_link": actions[0].base_link or "base_link",
    }

    for name in ("left_arm", "right_arm"):
        values: list[list[float]] = []
        for action in actions:
            values.extend(_action_rows(action, name))
        if values:
            data[name] = {
                "kind": _first_kind(actions, name),
                "values": values,
            }

    for name in ("head", "waist", "left_effector", "right_effector", "wheels"):
        values = []
        for action in actions:
            values.extend(_action_rows(action, name))
        if values:
            data[name] = values

    return Action(**data)


def _reverse_action(action: Action, trajectory_reference_time: float) -> Action:
    data = action.model_dump()
    data["timestamps"] = int(time.time() * 1e9)
    data["trajectory_reference_time"] = trajectory_reference_time

    for name in ("left_arm", "right_arm"):
        if data.get(name) is not None:
            data[name]["values"] = list(reversed(data[name]["values"]))

    for name in ("head", "waist", "left_effector", "right_effector", "wheels"):
        if data.get(name) is not None:
            data[name] = list(reversed(data[name]))

    return Action(**data)


def _refresh_action_timestamp(action: Action) -> Action:
    data = action.model_dump()
    data["timestamps"] = int(time.time() * 1e9)
    return Action(**data)


def _slice_action(action: Action, start: int, end: int, trajectory_reference_time: float) -> Action:
    data = action.model_dump()

    for name in ("left_arm", "right_arm"):
        if data.get(name) is not None:
            data[name]["values"] = data[name]["values"][start:end]

    for name in ("head", "waist", "left_effector", "right_effector", "wheels"):
        if data.get(name) is not None:
            data[name] = data[name][start:end]

    data["timestamps"] = int(time.time() * 1e9)
    data["trajectory_reference_time"] = trajectory_reference_time
    return Action(**data)


def _retime_action(action: Action, step_duration: float) -> Action:
    horizon = _trajectory_len(action)
    data = action.model_dump()
    data["timestamps"] = int(time.time() * 1e9)
    data["trajectory_reference_time"] = step_duration * horizon
    return Action(**data)


def _expected_gripper_state_from_command(command: list[float]) -> list[float]:
    cmd = np.asarray(command, dtype=np.float32)
    cmd = np.nan_to_num(cmd, nan=0.0, posinf=1.0, neginf=0.0)
    cmd = np.clip(cmd, 0.0, 1.0)
    return (GRIPPER_STATE_OPEN + cmd * (GRIPPER_STATE_CLOSE - GRIPPER_STATE_OPEN)).tolist()


def _final_gripper_closes(action: Action, close_threshold: float = 0.8) -> bool:
    final_commands = _last_row(action.left_effector) + _last_row(action.right_effector)
    return any(value >= close_threshold for value in final_commands)


def _print_robot_state(prefix: str, payload: Any) -> None:
    states = payload.observation.states
    arm = states.arm_joint_states or []
    grippers = states.gripper_states or []

    print(prefix)
    print(f"  arm joints:     {len(arm)}")
    print(f"  gripper states: {[round(v, 4) for v in grippers[:2]]}")
    if len(arm) >= 14:
        print(f"  current left:   {[round(v, 4) for v in arm[:7]]}")
        print(f"  current right:  {[round(v, 4) for v in arm[7:14]]}")


def _print_start_delta(payload: Any, action: Action) -> dict[str, float]:
    states = payload.observation.states
    arm = states.arm_joint_states or []
    grippers = states.gripper_states or []

    left_target = _first_row(action.left_arm.values if action.left_arm is not None else None)
    right_target = _first_row(action.right_arm.values if action.right_arm is not None else None)
    left_gripper_cmd = _first_row(action.left_effector)
    right_gripper_cmd = _first_row(action.right_effector)

    left_gripper_target = _expected_gripper_state_from_command(left_gripper_cmd) if left_gripper_cmd else []
    right_gripper_target = _expected_gripper_state_from_command(right_gripper_cmd) if right_gripper_cmd else []

    deltas = {
        "left_arm": _max_abs_delta(arm[:7], left_target),
        "right_arm": _max_abs_delta(arm[7:14], right_target),
        "left_effector_state": _max_abs_delta(grippers[:1], left_gripper_target),
        "right_effector_state": _max_abs_delta(grippers[1:2], right_gripper_target),
    }

    print("Start delta to first chunk row:")
    print(f"  left_arm:             {deltas['left_arm']:.4f} rad ({_rad_to_deg(deltas['left_arm']):.2f} deg)")
    print(f"  right_arm:            {deltas['right_arm']:.4f} rad ({_rad_to_deg(deltas['right_arm']):.2f} deg)")
    print(f"  left_effector state:  {deltas['left_effector_state']:.4f} /120")
    print(f"  right_effector state: {deltas['right_effector_state']:.4f} /120")

    return deltas


def _print_final_target(action: Action) -> None:
    left_target = _last_row(action.left_arm.values if action.left_arm is not None else None)
    right_target = _last_row(action.right_arm.values if action.right_arm is not None else None)
    left_gripper_cmd = _last_row(action.left_effector)
    right_gripper_cmd = _last_row(action.right_effector)

    print("Final target preview:")
    if left_target:
        print(f"  final left_arm:       {[round(v, 4) for v in left_target]}")
    if right_target:
        print(f"  final right_arm:      {[round(v, 4) for v in right_target]}")
    if left_gripper_cmd or right_gripper_cmd:
        left_state = _expected_gripper_state_from_command(left_gripper_cmd) if left_gripper_cmd else []
        right_state = _expected_gripper_state_from_command(right_gripper_cmd) if right_gripper_cmd else []
        print(f"  final gripper cmd:    {left_gripper_cmd + right_gripper_cmd}")
        print(f"  expected grip state:  {[round(v, 4) for v in left_state + right_state]}")


def _validate_start_delta(args: argparse.Namespace, deltas: dict[str, float]) -> bool:
    if args.force:
        return True

    max_arm_delta = max(deltas["left_arm"], deltas["right_arm"])
    if max_arm_delta > args.max_start_joint_delta:
        print(
            f"Blocked: first-row arm delta {max_arm_delta:.4f} rad "
            f"exceeds --max-start-joint-delta {args.max_start_joint_delta:.4f} rad."
        )
        return False

    return True


def _confirm_execution(args: argparse.Namespace, action: Action, horizon: int) -> bool:
    if args.dry_run:
        print("Dry run only. No action will be executed.")
        return False
    if args.yes:
        return True

    print(
        f"\nAbout to execute the full chunk: {horizon} step(s), "
        f"trajectory_reference_time={action.trajectory_reference_time:.3f}s."
    )
    answer = input("Type 'yes' to execute the full action chunk, anything else to cancel: ").strip().lower()
    return answer == "yes"


def _verify_final_state(
    payload: Any,
    action: Action,
    joint_error_limit_deg: float,
    gripper_error_limit_state: float,
) -> bool:
    states = payload.observation.states
    arm = states.arm_joint_states or []
    grippers = states.gripper_states or []

    checks: list[tuple[str, float, float, str]] = []

    if action.left_arm is not None and len(arm) >= 7:
        err = _max_abs_delta(arm[:7], _last_row(action.left_arm.values))
        checks.append(("left_arm", _rad_to_deg(err), joint_error_limit_deg, "deg"))

    if action.right_arm is not None and len(arm) >= 14:
        err = _max_abs_delta(arm[7:14], _last_row(action.right_arm.values))
        checks.append(("right_arm", _rad_to_deg(err), joint_error_limit_deg, "deg"))

    if action.left_effector and len(grippers) >= 1:
        expected = _expected_gripper_state_from_command(_last_row(action.left_effector))
        err = _max_abs_delta(grippers[:1], expected)
        checks.append(("left_effector_state", err, gripper_error_limit_state, "state"))

    if action.right_effector and len(grippers) >= 2:
        expected = _expected_gripper_state_from_command(_last_row(action.right_effector))
        err = _max_abs_delta(grippers[1:2], expected)
        checks.append(("right_effector_state", err, gripper_error_limit_state, "state"))

    ok = True
    print("Final verification:")
    for name, err, limit, unit in checks:
        passed = err <= limit
        ok = ok and passed
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name:20s} error={err:.4f} {unit}, limit={limit:.4f} {unit}")

    if not checks:
        print("  no comparable states found")
        return False

    return ok


def _verify_final_command_state(
    payload: Any,
    action: Action,
    joint_error_limit_deg: float,
    gripper_error_limit: float,
) -> bool:
    states = payload.observation.states
    arm = states.arm_joint_states or []
    grippers = states.gripper_states or []

    checks: list[tuple[str, float, float, str]] = []

    if action.left_arm is not None and len(arm) >= 7:
        err = _max_abs_delta(arm[:7], _last_row(action.left_arm.values))
        checks.append(("left_arm", _rad_to_deg(err), joint_error_limit_deg, "deg"))

    if action.right_arm is not None and len(arm) >= 14:
        err = _max_abs_delta(arm[7:14], _last_row(action.right_arm.values))
        checks.append(("right_arm", _rad_to_deg(err), joint_error_limit_deg, "deg"))

    if action.left_effector and len(grippers) >= 1:
        err = _max_abs_delta(grippers[:1], _last_row(action.left_effector))
        checks.append(("left_effector", err, gripper_error_limit, "unit"))

    if action.right_effector and len(grippers) >= 2:
        err = _max_abs_delta(grippers[1:2], _last_row(action.right_effector))
        checks.append(("right_effector", err, gripper_error_limit, "unit"))

    ok = True
    print("Final command-space verification:")
    for name, err, limit, unit in checks:
        passed = err <= limit
        ok = ok and passed
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name:16s} error={err:.4f} {unit}, limit={limit:.4f} {unit}")

    if not checks:
        print("  no comparable states found")
        return False

    return ok


def _load_execution_record(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Executed actions JSON not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Executed actions JSON must be an object: {path}")
    return data


def _actions_from_execution_record(record: dict[str, Any]) -> list[Action]:
    actions = []
    for chunk in record.get("chunks", []):
        action_data = chunk.get("action")
        if action_data is not None:
            actions.append(Action(**action_data))

    if actions:
        return actions

    executed_action = record.get("executed_action")
    if executed_action is not None:
        return [Action(**executed_action)]

    return []


def _max_adjacent_delta(values: list[list[float]]) -> float:
    if len(values) < 2:
        return 0.0
    arr = np.asarray(values, dtype=np.float32)
    return float(np.max(np.abs(np.diff(arr, axis=0))))


def _chunk_delta_summary(payload: Any, action: Action) -> dict[str, float]:
    states = payload.observation.states
    arm = states.arm_joint_states or []
    left_values = action.left_arm.values if action.left_arm is not None else []
    right_values = action.right_arm.values if action.right_arm is not None else []

    return {
        "left_start": _max_abs_delta(arm[:7], _first_row(left_values)),
        "right_start": _max_abs_delta(arm[7:14], _first_row(right_values)),
        "left_adjacent": _max_adjacent_delta(left_values),
        "right_adjacent": _max_adjacent_delta(right_values),
    }


def _print_record_summary(path: Path, record: dict[str, Any], actions: list[Action]) -> None:
    print(f"Loaded executed action record: {path}")
    print(f"  prompt:          {record.get('prompt')}")
    print(f"  recorded chunks: {len(record.get('chunks', []))}")
    print(f"  replay chunks:   {len(actions)}")
    print(f"  recorded steps:  {record.get('executed_steps')}")
    print(f"  record duration: {sum(float(action.trajectory_reference_time) for action in actions):.3f}s")
    if actions:
        _print_action_summary(actions[0])


def _retime_actions(actions: list[Action], step_duration: float, skip_effectors: bool) -> list[Action]:
    retimed = []
    for action in actions:
        replay_action = _action_without_effectors(action) if skip_effectors else action
        retimed.append(_retime_action(replay_action, step_duration))
    return retimed


def _validate_replay_start_and_steps(
    args: argparse.Namespace,
    payload: Any,
    actions: list[Action],
) -> bool:
    if not actions or args.force:
        return True

    first_deltas = _chunk_delta_summary(payload, actions[0])
    max_start_delta = max(first_deltas["left_start"], first_deltas["right_start"])
    print("Replay start delta to first recorded row:")
    print(f"  left_arm:  {first_deltas['left_start']:.4f} rad ({_rad_to_deg(first_deltas['left_start']):.2f} deg)")
    print(f"  right_arm: {first_deltas['right_start']:.4f} rad ({_rad_to_deg(first_deltas['right_start']):.2f} deg)")
    if max_start_delta > args.max_start_joint_delta:
        print(
            f"Blocked: replay start delta {max_start_delta:.4f} exceeds "
            f"--max-start-joint-delta {args.max_start_joint_delta:.4f}."
        )
        return False

    combined = _combine_action_chunks(actions, 0.0)
    max_adjacent = 0.0
    if combined is not None:
        left_values = combined.left_arm.values if combined.left_arm is not None else []
        right_values = combined.right_arm.values if combined.right_arm is not None else []
        max_adjacent = max(_max_adjacent_delta(left_values), _max_adjacent_delta(right_values))

    print(f"Max adjacent arm delta across replay: {max_adjacent:.4f} rad ({_rad_to_deg(max_adjacent):.2f} deg)")
    if max_adjacent > args.max_chunk_step_delta:
        print(
            f"Blocked: recorded adjacent delta {max_adjacent:.4f} exceeds "
            f"--max-chunk-step-delta {args.max_chunk_step_delta:.4f}."
        )
        return False

    return True


def _confirm_record_replay(args: argparse.Namespace, total_steps: int) -> bool:
    if not args.execute or args.dry_run:
        print("Preview only: add --execute to allow robot motion.")
        return False
    if args.yes:
        return True

    answer = input(
        f"Type 'yes' to execute recorded trajectory forward and then reverse "
        f"({total_steps} step(s) each way): "
    ).strip().lower()
    return answer == "yes"


def _execute_forward_actions(env: G01Env, args: argparse.Namespace, actions: list[Action]) -> None:
    print(f"Executing recorded trajectory forward in {len(actions)} chunk(s)...")
    for index, action in enumerate(actions):
        wait_action_time = action.trajectory_reference_time + args.wait_extra
        print(
            f"  forward chunk {index}: steps={_trajectory_len(action)}, "
            f"trajectory_reference_time={action.trajectory_reference_time:.3f}s, "
            f"wait_action_time={wait_action_time:.3f}s"
        )
        env.execute_action(action, wait_action_time=wait_action_time)


def _execute_reverse_actions(env: G01Env, args: argparse.Namespace, actions: list[Action]) -> Action:
    total_steps = sum(_trajectory_len(action) for action in actions)
    reverse_step_duration = args.reverse_step_duration if args.reverse_step_duration > 0 else args.step_duration
    combined = _combine_action_chunks(actions, args.step_duration * total_steps)
    if combined is None:
        raise ValueError("No actions to reverse.")

    reverse_action = _reverse_action(combined, reverse_step_duration * total_steps)
    batch_steps = args.reverse_batch_steps if args.reverse_batch_steps > 0 else args.steps_per_chunk

    print(
        f"Executing recorded trajectory backward: total_steps={total_steps}, "
        f"batch_steps={batch_steps}, step_duration={reverse_step_duration:.3f}s"
    )
    for start in range(0, total_steps, batch_steps):
        end = min(total_steps, start + batch_steps)
        batch = _slice_action(reverse_action, start, end, reverse_step_duration * (end - start))
        wait_action_time = batch.trajectory_reference_time + args.wait_extra
        print(
            f"  reverse batch {start}..{end - 1}, "
            f"trajectory_reference_time={batch.trajectory_reference_time:.3f}s, "
            f"wait_action_time={wait_action_time:.3f}s"
        )
        env.execute_action(batch, wait_action_time=wait_action_time)

    return reverse_action


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay a recorded multi-chunk executed_actions.json forward once and backward once. "
            "Pass --action-json to use the legacy single-action execution mode."
        )
    )
    parser.add_argument(
        "--executed-actions-json",
        type=Path,
        default=DEFAULT_EXECUTED_ACTIONS_JSON,
        help="Multi-chunk executed_actions.json to replay forward and backward.",
    )
    parser.add_argument(
        "--action-json",
        type=Path,
        default=None,
        help=f"Legacy single Action JSON to execute instead of --executed-actions-json. Default legacy path: {DEFAULT_ACTION_JSON}",
    )
    parser.add_argument("--attempts", type=int, default=20, help="Robot state fetch attempts.")
    parser.add_argument("--interval", type=float, default=0.25, help="Seconds between state fetch attempts.")
    parser.add_argument("--wait-extra", type=float, default=0.1, help="Extra wait after trajectory_reference_time.")
    parser.add_argument("--steps-per-chunk", type=int, default=20, help="Reverse replay batch size fallback.")
    parser.add_argument("--step-duration", type=float, default=0.05, help="Trajectory duration for each single step.")
    parser.add_argument("--reverse-step-duration", type=float, default=0.0, help="Reverse step duration. Use 0 to reuse --step-duration.")
    parser.add_argument("--reverse-batch-steps", type=int, default=0, help="Reverse execution batch size. Use 0 to reuse --steps-per-chunk.")
    parser.add_argument(
        "--final-gripper-duration",
        type=float,
        default=0.8,
        help="Legacy single-action only: duration for the final step when the final gripper command is closing.",
    )
    parser.add_argument("--max-start-joint-delta", type=float, default=0.15, help="Max allowed first-row arm delta in rad.")
    parser.add_argument("--max-chunk-step-delta", type=float, default=0.20, help="Max allowed adjacent arm delta inside replay chunks.")
    parser.add_argument("--verify-joint-error-deg", type=float, default=8.0, help="Final arm joint error limit.")
    parser.add_argument("--verify-gripper-error", type=float, default=0.20, help="Final gripper command-space error limit.")
    parser.add_argument(
        "--verify-gripper-error-state",
        type=float,
        default=20.0,
        help="Legacy single-action only: final gripper state error limit in 0..120 state units.",
    )
    parser.add_argument("--skip-effectors", action="store_true", help="Execute arms only; drop gripper commands.")
    parser.add_argument("--execute", action="store_true", help="Allow robot motion. Without this, the script only previews.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only; do not execute.")
    parser.add_argument("--yes", action="store_true", help="Execute without interactive confirmation. Requires --execute.")
    parser.add_argument("--force", action="store_true", help="Ignore start-delta safety limit.")
    parser.add_argument("--no-verify", action="store_true", help="Do not read and verify final state after execution.")
    args = parser.parse_args()

    if args.attempts <= 0:
        print("--attempts must be positive.", file=sys.stderr)
        return 2
    if args.interval <= 0:
        print("--interval must be positive.", file=sys.stderr)
        return 2
    if args.steps_per_chunk <= 0:
        print("--steps-per-chunk must be positive.", file=sys.stderr)
        return 2
    if args.wait_extra < 0:
        print("--wait-extra must be non-negative.", file=sys.stderr)
        return 2
    if args.step_duration <= 0:
        print("--step-duration must be positive.", file=sys.stderr)
        return 2
    if args.reverse_step_duration < 0:
        print("--reverse-step-duration must be non-negative.", file=sys.stderr)
        return 2
    if args.reverse_batch_steps < 0:
        print("--reverse-batch-steps must be non-negative.", file=sys.stderr)
        return 2
    if args.final_gripper_duration < args.step_duration:
        print("--final-gripper-duration must be >= --step-duration.", file=sys.stderr)
        return 2
    if args.yes and not args.execute:
        print("--yes has no effect without --execute.", file=sys.stderr)
        return 2

    record_mode = args.action_json is None
    if record_mode:
        record = _load_execution_record(args.executed_actions_json)
        actions = _actions_from_execution_record(record)
        actions = _retime_actions(actions, args.step_duration, args.skip_effectors)
        total_steps = sum(_trajectory_len(action) for action in actions)
        if not actions or total_steps == 0:
            print("Executed actions record has no executable steps.", file=sys.stderr)
            return 1
        _print_record_summary(args.executed_actions_json, record, actions)
    else:
        action = _load_action(args.action_json)
        if args.skip_effectors:
            action = _action_without_effectors(action)
        action = _refresh_action_timestamp(action)

        horizon = _trajectory_len(action)
        if horizon == 0:
            print("Action has no executable steps.", file=sys.stderr)
            return 1
        if action.trajectory_reference_time <= 0:
            print("Action trajectory_reference_time must be positive.", file=sys.stderr)
            return 1

        print(f"Loaded action: {args.action_json}")
        _print_action_summary(action)
        _print_final_target(action)

    dds_env_set()

    env = None
    try:
        env = G01Env(_make_env_config())
        env.setup()

        before_payload = _read_ready_model_input(env, args.attempts, args.interval)
        if before_payload is None:
            print("No complete robot state was captured before execution.", file=sys.stderr)
            return 1

        _print_robot_state("Current robot state:", before_payload)

        if record_mode:
            if not _validate_replay_start_and_steps(args, before_payload, actions):
                return 1

            if not _confirm_record_replay(args, total_steps):
                print("Cancelled.")
                return 0

            verification_failed = False
            _execute_forward_actions(env, args, actions)
            print("Forward replay finished.")

            forward_combined = _combine_action_chunks(actions, args.step_duration * total_steps)
            if forward_combined is None:
                print("Could not combine forward actions for verification.", file=sys.stderr)
                return 1

            if not args.no_verify:
                forward_payload = _read_ready_model_input(env, args.attempts, args.interval)
                if forward_payload is None:
                    print("Could not read robot state after forward replay.", file=sys.stderr)
                    return 1
                _print_robot_state("Robot state after forward replay:", forward_payload)
                if not _verify_final_command_state(
                    forward_payload,
                    forward_combined,
                    args.verify_joint_error_deg,
                    args.verify_gripper_error,
                ):
                    verification_failed = True
                    print("Forward replay verification failed; continuing to reverse replay.")

            reverse_action = _execute_reverse_actions(env, args, actions)
            print("Reverse replay finished.")

            if args.no_verify:
                return 0

            reverse_payload = _read_ready_model_input(env, args.attempts, args.interval)
            if reverse_payload is None:
                print("Could not read robot state after reverse replay.", file=sys.stderr)
                return 1
            _print_robot_state("Robot state after reverse replay:", reverse_payload)
            reverse_ok = _verify_final_command_state(
                reverse_payload,
                reverse_action,
                args.verify_joint_error_deg,
                args.verify_gripper_error,
            )
            return 0 if reverse_ok and not verification_failed else 1

        deltas = _print_start_delta(before_payload, action)
        if not _validate_start_delta(args, deltas):
            return 1

        if not args.execute:
            args.dry_run = True
        if not _confirm_execution(args, action, horizon):
            print("Cancelled.")
            return 0

        final_gripper_closes = _final_gripper_closes(action)
        print(f"Executing full chunk in {EXECUTION_BATCH_STEPS}-step batches ...")
        for chunk_start in range(0, horizon, EXECUTION_BATCH_STEPS):
            chunk_end = min(horizon, chunk_start + EXECUTION_BATCH_STEPS)
            chunk_duration = args.step_duration * (chunk_end - chunk_start)
            if final_gripper_closes and chunk_end == horizon:
                chunk_duration = max(chunk_duration, args.final_gripper_duration)
            chunk_action = _slice_action(action, chunk_start, chunk_end, chunk_duration)
            wait_action_time = chunk_action.trajectory_reference_time + args.wait_extra
            print(
                f"  executing steps {chunk_start}..{chunk_end - 1}, "
                f"wait_action_time={wait_action_time:.3f}s ..."
            )
            env.execute_action(chunk_action, wait_action_time=wait_action_time)
        print("Execution command sent and wait finished.")

        if args.no_verify:
            return 0

        after_payload = _read_ready_model_input(env, args.attempts, args.interval)
        if after_payload is None:
            print("Could not read robot state after execution.", file=sys.stderr)
            return 1

        _print_robot_state("Robot state after execution:", after_payload)
        return 0 if _verify_final_state(
            after_payload,
            action,
            args.verify_joint_error_deg,
            args.verify_gripper_error_state,
        ) else 1

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
