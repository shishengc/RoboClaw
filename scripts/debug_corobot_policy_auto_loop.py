from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable

from websockets.sync.client import connect

from corobot.envs.g01_env import G01Env
from corobot.protocol.protocol_schemas import Action
from corobot.transport import msgpack_numpy
from corobot.utils.dds_setting import dds_env_set


DEFAULT_PROMPT = "Push close the drawer"
# Pull open the drawer Push close the drawer
LATEST_ACTION_FILENAME = "latest_action.json"


def _json_safe(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


def _make_env_config(enable_cameras: bool = True) -> dict[str, Any]:
    return {
        "dataloader": {
            "data_source": {"ALIGNED_ROBOT": {"enabled": True}},
            "enabled_streams": {
                "camera": {
                    "head": enable_cameras,
                    "hand_left": enable_cameras,
                    "hand_right": enable_cameras,
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


def close_env_safely(env: G01Env | None) -> None:
    if env is None:
        return
    try:
        env.close()
    except Exception as exc:
        print(f"Warning: failed to close env: {exc}", file=sys.stderr)


def _default_reset_pose() -> dict[str, Any]:
    return {
        "target_grippers_positions": [0.0, 0.0],
        "target_arm_joint_positions": [
            -1.072617, 
            0.609581, 
            0.279060, 
            -1.281563,
            0.729096, 
            1.492464, 
            -0.187081,
            1.074292, 
            -0.611099, 
            -0.279601,
            1.283866, 
            -0.730509, 
            -1.495465, 
            0.187605,
        ],
        "target_head_positions": [0.0, 0.43633230555555524],
        # "target_waist_positions": [0.40441, 0.2598677062988281],
        "target_waist_positions": [0.8901176920412174, 0.3598677062988281]
    }


def reset_robot_pose(env: G01Env) -> None:
    reset_pose = _default_reset_pose()
    grippers = reset_pose.get("target_grippers_positions")
    if grippers and len(grippers) >= 2:
        gripper_action = Action(
            timestamps=int(time.time() * 1e9),
            trajectory_reference_time=1.0,
            base_link="base_link",
            left_effector=[[float(grippers[0])]],
            right_effector=[[float(grippers[1])]],
        )
        print(f"Resetting grippers first: {grippers[:2]}")
        env.execute_action(gripper_action, wait_action_time=gripper_action.trajectory_reference_time)

    arm_reset_pose = dict(reset_pose)
    arm_reset_pose["target_grippers_positions"] = None
    print("Resetting robot pose...")
    env.reset(**arm_reset_pose)


def reset_robot_pose_safely(
    env: G01Env | None,
    *,
    reason: str = "interrupt",
) -> bool:
    if env is None:
        print(f"Skip reset after {reason}: env is not available.", file=sys.stderr)
        return False

    print(f"Resetting robot after {reason}; temporary Ctrl+C is ignored until reset finishes.")
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:
        previous_sigint = None

    try:
        reset_robot_pose(env)
        print("Reset finished.")
        return True
    except Exception as exc:
        print(f"Reset after {reason} failed: {exc}", file=sys.stderr)
        return False
    finally:
        if previous_sigint is not None:
            try:
                signal.signal(signal.SIGINT, previous_sigint)
            except ValueError:
                pass


def _observation_ready(payload: Any) -> tuple[bool, str]:
    if payload is None:
        return False, "payload is None"

    obs = payload.observation
    images = obs.images
    states = obs.states

    missing_images = [
        name
        for name in ("head", "hand_left", "hand_right")
        if getattr(images, name, None) is None
    ]
    if missing_images:
        return False, f"missing images: {', '.join(missing_images)}"

    arm = states.arm_joint_states or []
    if len(arm) < 14:
        return False, f"arm_joint_states has {len(arm)} value(s), expected at least 14"

    gripper = states.gripper_states or []
    if len(gripper) < 2:
        return False, f"gripper_states has {len(gripper)} value(s), expected at least 2"

    return True, "ok"


def read_ready_model_input(env: G01Env, attempts: int, interval: float) -> Any:
    for attempt in range(1, attempts + 1):
        candidate = env.get_std_model_input()
        ready, reason = _observation_ready(candidate)
        if ready:
            print(f"Observation ready on attempt {attempt}/{attempts}")
            return candidate
        print(f"[{attempt}/{attempts}] observation not ready: {reason}")
        time.sleep(interval)
    raise RuntimeError("No complete observation was captured.")


def _unpack_policy_frame(frame: Any, frame_name: str) -> Any:
    if isinstance(frame, str):
        raise RuntimeError(
            f"Policy returned a text {frame_name} frame instead of msgpack bytes:\n{frame}"
        )
    if not isinstance(frame, (bytes, bytearray, memoryview)):
        raise TypeError(f"Policy returned unsupported {frame_name} frame type: {type(frame).__name__}")
    return msgpack_numpy.unpackb(frame)


def infer_once(payload: Any, args: argparse.Namespace) -> tuple[Action, dict[str, Any], float]:
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

        start = time.monotonic()
        ws.send(msgpack_numpy.packb(_model_dump(payload)))
        response = _unpack_policy_frame(ws.recv(), "response")
        elapsed_ms = (time.monotonic() - start) * 1000.0

    if isinstance(response, dict) and "error" in response:
        raise RuntimeError(response["error"])

    return Action(**response), metadata if isinstance(metadata, dict) else {}, elapsed_ms


def trajectory_len(action: Action) -> int:
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


def slice_action(action: Action, start: int, end: int) -> Action:
    horizon = trajectory_len(action)
    if horizon <= 0:
        raise RuntimeError("Returned action has no executable steps.")
    if start < 0 or end <= start:
        raise ValueError("invalid action slice")

    end = min(end, horizon)
    step_count = end - start
    per_step_duration = (
        float(action.trajectory_reference_time) / float(horizon)
        if action.trajectory_reference_time and horizon > 0
        else 1.0 / 30.0
    )

    data = _model_dump(action)
    data["timestamps"] = int(time.time() * 1e9)
    data["trajectory_reference_time"] = per_step_duration * step_count

    for name in ("left_arm", "right_arm"):
        if data.get(name) is not None:
            data[name]["values"] = data[name]["values"][start:end]
            if not data[name]["values"]:
                data[name] = None

    for name in ("head", "waist", "left_effector", "right_effector", "wheels"):
        if data.get(name) is not None:
            data[name] = data[name][start:end]
            if not data[name]:
                data[name] = None

    return Action(**data)


def print_observation_summary(payload: Any) -> None:
    obs = payload.observation
    print("Observation:")
    print(f"  head image:       {_shape(obs.images.head)}")
    print(f"  hand_left image:  {_shape(obs.images.hand_left)}")
    print(f"  hand_right image: {_shape(obs.images.hand_right)}")
    print(f"  arm joints:       {len(obs.states.arm_joint_states or [])}")
    print(f"  gripper states:   {len(obs.states.gripper_states or [])}")
    print(f"  head joints:      {len(obs.states.head_joint_states or [])}")
    print(f"  waist joints:     {len(obs.states.waist_joint_states or [])}")


def print_action_summary(action: Action, label: str = "Action") -> None:
    left_values = action.left_arm.values if action.left_arm is not None else []
    right_values = action.right_arm.values if action.right_arm is not None else []
    left_effector = action.left_effector or []
    right_effector = action.right_effector or []
    print(f"{label}:")
    print(f"  trajectory_reference_time: {action.trajectory_reference_time:.3f}s")
    print(f"  left_arm:       steps={len(left_values)}, dim={len(left_values[0]) if left_values else 0}")
    print(f"  right_arm:      steps={len(right_values)}, dim={len(right_values[0]) if right_values else 0}")
    print(f"  left_effector:  steps={len(left_effector)}, dim={len(left_effector[0]) if left_effector else 0}")
    print(f"  right_effector: steps={len(right_effector)}, dim={len(right_effector[0]) if right_effector else 0}")


def _local_time_string(timestamp_s: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(timestamp_s))


def _file_time_string(timestamp_s: float) -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime(timestamp_s))


def create_run_action_log(save_dir: Path, script_name: str, args: argparse.Namespace, initial_prompt: str) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    started_at_s = time.time()
    run_log_path = save_dir / f"{script_name}_actions_{_file_time_string(started_at_s)}.json"
    payload = {
        "script_name": script_name,
        "started_at": _local_time_string(started_at_s),
        "started_at_s": started_at_s,
        "initial_prompt": initial_prompt,
        "latest_action_path": str(save_dir / LATEST_ACTION_FILENAME),
        "args": _json_safe(vars(args)),
        "chunks": [],
    }
    run_log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Run action log: {run_log_path}")
    print(f"Latest action log: {save_dir / LATEST_ACTION_FILENAME}")
    return run_log_path


def save_action_logs(
    *,
    save_dir: Path,
    run_log_path: Path,
    chunk_index: int,
    prompt: str,
    action: Action,
    horizon_steps: int,
    executed_steps: int,
    inference_elapsed_ms: float,
) -> None:
    timestamp_s = time.time()
    action_record = {
        "chunk_index": chunk_index,
        "executed_at": _local_time_string(timestamp_s),
        "executed_at_s": timestamp_s,
        "prompt": prompt,
        "inference_elapsed_ms": inference_elapsed_ms,
        "horizon_steps": horizon_steps,
        "executed_steps": executed_steps,
        "action": _json_safe(_model_dump(action)),
    }

    latest_path = save_dir / LATEST_ACTION_FILENAME
    latest_payload = {
        "updated_at": action_record["executed_at"],
        "updated_at_s": action_record["executed_at_s"],
        "source_run_log": str(run_log_path),
        **action_record,
    }
    latest_path.write_text(json.dumps(latest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if run_log_path.exists():
        run_payload = json.loads(run_log_path.read_text(encoding="utf-8"))
    else:
        run_payload = {"chunks": []}
    run_payload.setdefault("chunks", []).append(action_record)
    run_payload["updated_at"] = action_record["executed_at"]
    run_payload["updated_at_s"] = action_record["executed_at_s"]
    run_payload["latest_chunk_index"] = chunk_index
    run_log_path.write_text(json.dumps(run_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Saved latest returned action to {latest_path}")
    print(f"Appended returned action chunk to {run_log_path}")


def _shape(value: Any) -> str:
    if value is None:
        return "None"
    shape = getattr(value, "shape", None)
    if shape is not None:
        return "x".join(str(part) for part in shape)
    try:
        import numpy as np

        return "x".join(str(part) for part in np.asarray(value).shape)
    except Exception:
        return type(value).__name__


def read_prompt_file(prompt_file: str | None, current_prompt: str) -> str:
    if not prompt_file:
        return current_prompt
    path = Path(prompt_file)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        print(f"Prompt file not found: {path}; keeping current prompt.", file=sys.stderr)
        return current_prompt
    if text and text != current_prompt:
        print(f"Prompt updated from {path}: {text!r}")
        return text
    return current_prompt


def execute_inferred_chunk(
    env: G01Env,
    args: argparse.Namespace,
    *,
    prompt: str,
    chunk_index: int,
    save_dir: Path,
    run_log_path: Path,
    action_modifier: Callable[[Action], Action] | None = None,
) -> int:
    print(f"\n=== Inference/execution chunk {chunk_index} ===")
    print(f"Prompt: {prompt!r}")
    payload = read_ready_model_input(env, args.attempts, args.interval)
    payload.prompt = prompt
    print_observation_summary(payload)

    action, _metadata, elapsed_ms = infer_once(payload, args)
    print(f"Inference round trip: {elapsed_ms:.1f} ms")
    print_action_summary(action, "Returned action")
    if action_modifier is not None:
        action = action_modifier(action)
        print_action_summary(action, "Modified returned action")

    horizon = trajectory_len(action)
    end = min(args.steps_per_chunk, horizon)
    chunk_action = slice_action(action, 0, end)
    print_action_summary(chunk_action, "Executing chunk")
    save_action_logs(
        save_dir=save_dir,
        run_log_path=run_log_path,
        chunk_index=chunk_index,
        prompt=prompt,
        action=action,
        horizon_steps=horizon,
        executed_steps=end,
        inference_elapsed_ms=elapsed_ms,
    )

    env.execute_action(chunk_action, wait_action_time=chunk_action.trajectory_reference_time)
    return end


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automatic receding-horizon CoRobot policy loop: infer from prompt, execute the leading chunk, repeat."
    )
    parser.add_argument("--policy-host", default="127.0.0.1")
    parser.add_argument("--policy-port", type=int, default=8999)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--prompt-file", default=None, help="Optional file polled before every inference.")
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--save-dir", default="artifacts/corobot_policy_auto_loop")
    parser.add_argument("--steps-per-chunk", type=int, default=30)
    parser.add_argument("--chunks-per-round", type=int, default=30)
    parser.add_argument("--reset-pause", type=float, default=1.0)
    parser.add_argument("--max-chunks", type=int, default=0, help="0 means run until interrupted.")
    parser.add_argument("--no-reset-on-interrupt", action="store_true")
    args = parser.parse_args()

    if args.steps_per_chunk <= 0:
        print("--steps-per-chunk must be positive.", file=sys.stderr)
        return 2
    if args.chunks_per_round <= 0:
        print("--chunks-per-round must be positive.", file=sys.stderr)
        return 2

    dds_env_set()
    save_dir = Path(args.save_dir)
    run_log_path = create_run_action_log(save_dir, "auto_loop", args, args.prompt)
    env: G01Env | None = None
    prompt = args.prompt
    chunk_index = 0
    round_chunk_count = 0
    executed_any = False

    try:
        env = G01Env(_make_env_config(enable_cameras=True))
        env.setup()

        print("Auto execution mode enabled. The robot will move without confirmation.")
        print(f"Initial prompt: {prompt!r}")
        print("Press Ctrl+C to stop.")

        while args.max_chunks <= 0 or chunk_index < args.max_chunks:
            prompt = read_prompt_file(args.prompt_file, prompt)
            executed_steps = execute_inferred_chunk(
                env,
                args,
                prompt=prompt,
                chunk_index=chunk_index,
                save_dir=save_dir,
                run_log_path=run_log_path,
            )
            print(f"Executed chunk {chunk_index}: {executed_steps} step(s).")
            executed_any = True
            chunk_index += 1
            round_chunk_count += 1

            if round_chunk_count >= args.chunks_per_round:
                print(f"\n=== Completed {round_chunk_count} chunk(s); resetting for next round ===")
                if not reset_robot_pose_safely(env, reason="round"):
                    raise RuntimeError("robot reset failed")
                if args.reset_pause > 0:
                    time.sleep(args.reset_pause)
                round_chunk_count = 0

        if executed_any and not args.no_reset_on_interrupt:
            reset_robot_pose_safely(env, reason="normal exit")
        return 0
    except KeyboardInterrupt:
        print("\nStopped by user.")
        if not args.no_reset_on_interrupt:
            reset_robot_pose_safely(env, reason="interrupt")
        return 130
    except Exception as exc:
        print(f"Auto loop failed: {exc}", file=sys.stderr)
        if not args.no_reset_on_interrupt:
            reset_robot_pose_safely(env, reason="failure")
        return 1
    finally:
        close_env_safely(env)


if __name__ == "__main__":
    raise SystemExit(main())
