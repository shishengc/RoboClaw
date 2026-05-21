from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from websockets.sync.client import connect

from corobot.dataloader import DataLoaderFactory
from corobot.protocol.protocol_schemas import Action
from corobot.transport import msgpack_numpy
from corobot.utils.dds_setting import dds_env_set


def _shape(value: Any) -> str:
    if value is None:
        return "None"
    return "x".join(str(part) for part in np.asarray(value).shape)


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _unpack_policy_frame(frame: Any, frame_name: str) -> Any:
    if isinstance(frame, str):
        raise RuntimeError(
            f"Policy returned a text {frame_name} frame instead of msgpack bytes. "
            "This usually means the policy server raised an exception:\n"
            f"{frame}"
        )
    if not isinstance(frame, (bytes, bytearray, memoryview)):
        raise TypeError(f"Policy returned unsupported {frame_name} frame type: {type(frame).__name__}")
    return msgpack_numpy.unpackb(frame)


def _make_dataloader_config() -> dict[str, Any]:
    return {
        "data_source": {"ALIGNED_ROBOT": {"enabled": True}},
        "enabled_streams": {
            "camera": {
                "head": True,
                "hand_left": True,
                "hand_right": True,
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
    }


def _observation_ready(payload: Any) -> tuple[bool, str]:
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


def _save_images(payload: Any, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    images = payload.observation.images
    for name in ("head", "hand_left", "hand_right"):
        image = getattr(images, name, None)
        if image is not None:
            cv2.imwrite(str(output_dir / f"{name}.jpg"), np.asarray(image))


def _print_observation_summary(payload: Any) -> None:
    obs = payload.observation
    print("Observation:")
    print(f"  head image:       {_shape(obs.images.head)}")
    print(f"  hand_left image:  {_shape(obs.images.hand_left)}")
    print(f"  hand_right image: {_shape(obs.images.hand_right)}")
    print(f"  arm joints:       {len(obs.states.arm_joint_states or [])}")
    print(f"  gripper states:   {len(obs.states.gripper_states or [])}")
    print(f"  head joints:      {len(obs.states.head_joint_states or [])}")
    print(f"  waist joints:     {len(obs.states.waist_joint_states or [])}")


def _print_action_summary(action: Action) -> None:
    left_values = action.left_arm.values if action.left_arm is not None else []
    right_values = action.right_arm.values if action.right_arm is not None else []
    left_effector = action.left_effector or []
    right_effector = action.right_effector or []

    print("Returned action:")
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run a remote CoRobot policy: capture one observation, request one inference, do not execute."
    )
    parser.add_argument("--policy-host", default="127.0.0.1", help="Local websocket host, usually the SSH tunnel host.")
    parser.add_argument("--policy-port", type=int, default=8999, help="Local websocket port.")
    parser.add_argument("--prompt", default="Grasp the Target", help="Prompt sent to the policy.")
    parser.add_argument("--attempts", type=int, default=20, help="Observation fetch attempts.")
    parser.add_argument("--interval", type=float, default=0.25, help="Seconds between observation fetch attempts.")
    parser.add_argument("--timeout", type=float, default=30.0, help="WebSocket timeout in seconds.")
    parser.add_argument("--save-dir", default="artifacts/test_infer_policy", help="Directory for dry-run artifacts.")
    parser.add_argument("--no-save-images", action="store_true", help="Do not save the captured images.")
    args = parser.parse_args()

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    dds_env_set()

    dataloader = None
    ws = None
    try:
        dataloader = DataLoaderFactory.create_data_loader(_make_dataloader_config())

        payload = None
        for attempt in range(1, args.attempts + 1):
            candidate = dataloader.get_payload()
            ready, reason = _observation_ready(candidate)
            if ready:
                payload = candidate
                print(f"Observation ready on attempt {attempt}/{args.attempts}")
                break
            print(f"[{attempt}/{args.attempts}] observation not ready: {reason}")
            time.sleep(args.interval)

        if payload is None:
            print("No complete observation was captured.", file=sys.stderr)
            return 1

        payload.prompt = args.prompt
        _print_observation_summary(payload)

        url = f"ws://{args.policy_host}:{args.policy_port}"
        print(f"Connecting policy: {url}")
        ws = connect(
            url,
            compression=None,
            max_size=None,
            open_timeout=args.timeout,
            close_timeout=args.timeout,
        )

        metadata = _unpack_policy_frame(ws.recv(), "metadata")
        print(f"Policy metadata: {json.dumps(_json_safe(metadata), ensure_ascii=False)}")
        if isinstance(metadata, dict) and "camera_names" not in metadata:
            print(
                "Warning: policy metadata has no 'camera_names'. "
                "You may be connected to the default OpenPI websocket server, "
                "not serve_corobotpolicy.py.",
                file=sys.stderr,
            )

        start = time.monotonic()
        ws.send(msgpack_numpy.packb(payload.model_dump()))
        try:
            response = _unpack_policy_frame(ws.recv(), "response")
        except Exception as e:
            print(e, file=sys.stderr)
            return 1
        elapsed_ms = (time.monotonic() - start) * 1000.0

        if isinstance(response, dict) and "error" in response:
            print(response["error"], file=sys.stderr)
            return 1

        action = Action(**response)
        _print_action_summary(action)
        print(f"Inference round trip: {elapsed_ms:.1f} ms")

        action_path = save_dir / "returned_action.json"
        action_path.write_text(
            json.dumps(_json_safe(action.model_dump()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Saved returned action to {action_path}")
        print("Infer policy complete. No action was executed on the robot.")
        return 0

    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if dataloader is not None:
            dataloader.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
