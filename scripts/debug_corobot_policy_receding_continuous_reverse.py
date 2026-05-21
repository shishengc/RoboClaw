from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from corobot.envs.g01_env import G01Env
from corobot.protocol.protocol_schemas import Action
from corobot.utils.dds_setting import dds_env_set

from debug_corobot_policy_receding import _reset_after_interrupt
from debug_corobot_policy_receding_continuous import (
    _chunk_delta_summary,
    _confirm_chunk_execution,
    _infer_once,
    _print_chunk_preview,
    _should_continue,
    _validate_chunk,
    _verify_chunk_final_state,
    _without_effectors,
)
from debug_corobot_policy_step import (
    _make_env_config,
    _read_ready_model_input,
    _trajectory_len,
)
from execute_corobot_action_chunk import _slice_action
from test_infer_policy import (
    _json_safe,
    _print_action_summary,
    _print_observation_summary,
)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(data), ensure_ascii=False, indent=2), encoding="utf-8")


def _save_latest_action(action: Action, save_dir: Path) -> Path:
    path = save_dir / "latest_action.json"
    _write_json(path, action.model_dump())
    print(f"Saved latest inferred action to {path}")
    return path


def _empty_execution_record(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "policy": {
            "host": args.policy_host,
            "port": args.policy_port,
        },
        "step_duration": args.step_duration,
        "wait_extra": args.wait_extra,
        "steps_per_chunk": args.steps_per_chunk,
        "skip_effectors": bool(args.skip_effectors),
        "created_at_ns": int(time.time() * 1e9),
        "updated_at_ns": int(time.time() * 1e9),
        "executed_chunks": 0,
        "executed_steps": 0,
        "chunks": [],
        "executed_action": None,
    }


def _model_dump_action(action: Action) -> dict[str, Any]:
    return _json_safe(action.model_dump())


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


def _executed_actions_from_record(record: dict[str, Any]) -> list[Action]:
    return [Action(**chunk["action"]) for chunk in record.get("chunks", [])]


def _refresh_combined_executed_action(record: dict[str, Any]) -> None:
    actions = _executed_actions_from_record(record)
    total_time = sum(float(action.trajectory_reference_time) for action in actions)
    combined = _combine_action_chunks(actions, total_time)
    record["executed_action"] = _model_dump_action(combined) if combined is not None else None


def _append_executed_chunk(
    record: dict[str, Any],
    *,
    chunk_index: int,
    source_horizon: int,
    executed_steps: int,
    action: Action,
) -> None:
    record["chunks"].append(
        {
            "chunk_index": chunk_index,
            "source_horizon": source_horizon,
            "executed_steps": executed_steps,
            "trajectory_reference_time": action.trajectory_reference_time,
            "executed_at_ns": int(time.time() * 1e9),
            "action": _model_dump_action(action),
        }
    )
    record["executed_chunks"] = len(record["chunks"])
    record["executed_steps"] = sum(int(chunk["executed_steps"]) for chunk in record["chunks"])
    record["updated_at_ns"] = int(time.time() * 1e9)
    _refresh_combined_executed_action(record)


def _write_execution_record(record: dict[str, Any], save_dir: Path) -> Path:
    path = save_dir / "executed_actions.json"
    _write_json(path, record)
    print(f"Saved executed action record to {path}")
    return path


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


def _execute_reverse(
    env: G01Env,
    args: argparse.Namespace,
    record: dict[str, Any],
) -> bool:
    if not args.execute:
        print("Reverse requested, but --execute is not enabled; no robot motion will run.")
        return False

    combined_data = record.get("executed_action")
    if not combined_data:
        print("No executed actions have been recorded; nothing to reverse.")
        return False

    combined = Action(**combined_data)
    horizon = _trajectory_len(combined)
    if horizon == 0:
        print("Recorded executed action has zero steps; nothing to reverse.")
        return False

    step_duration = args.reverse_step_duration if args.reverse_step_duration > 0 else args.step_duration
    reverse_action = _reverse_action(combined, step_duration * horizon)
    batch_steps = args.reverse_batch_steps if args.reverse_batch_steps > 0 else args.steps_per_chunk

    print(
        f"Reverse requested: executing {horizon} recorded step(s) backward "
        f"in batch(es) of {batch_steps}, step_duration={step_duration:.3f}s."
    )

    for start in range(0, horizon, batch_steps):
        end = min(horizon, start + batch_steps)
        batch = _slice_action(reverse_action, start, end, step_duration * (end - start))
        wait_action_time = batch.trajectory_reference_time + args.wait_extra
        print(
            f"  reverse batch {start}..{end - 1}, "
            f"trajectory_reference_time={batch.trajectory_reference_time:.3f}s"
        )
        env.execute_action(batch, wait_action_time=wait_action_time)

    print("Reverse execution finished.")
    return True


def _post_chunk_command(args: argparse.Namespace, chunk_index: int) -> str:
    if not args.execute:
        return "continue"
    if args.yes:
        return "continue"

    answer = input(
        f"Chunk {chunk_index} complete. Press Enter for next inference, "
        "type 'reverse' to replay executed actions backward, or 'quit' to stop and reset: "
    ).strip().lower()
    if answer in ("reverse", "r"):
        return "reverse"
    if answer == "quit":
        raise KeyboardInterrupt
    return "continue"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compact continuous receding-horizon policy debug: infer an action chunk, execute the first N "
            "steps continuously, save only latest_action.json plus one cumulative executed_actions.json, "
            "and allow typing 'reverse' after a chunk to replay executed actions backward."
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
        default="artifacts/corobot_policy_receding_continuous_reverse_debug",
        help="Directory for compact continuous receding debug artifacts.",
    )
    parser.add_argument("--steps-per-chunk", type=int, default=20, help="Number of leading action steps to execute per inference.")
    parser.add_argument("--step-duration", type=float, default=0.05, help="Trajectory duration for each action step in the chunk.")
    parser.add_argument("--wait-extra", type=float, default=0.1, help="Extra wait after each continuous chunk execution.")
    parser.add_argument("--reverse-step-duration", type=float, default=0.0, help="Reverse step duration. Use 0 to reuse --step-duration.")
    parser.add_argument("--reverse-batch-steps", type=int, default=0, help="Reverse execution batch size. Use 0 to reuse --steps-per-chunk.")
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
    if args.reverse_step_duration < 0:
        print("--reverse-step-duration must be non-negative.", file=sys.stderr)
        return 2
    if args.reverse_batch_steps < 0:
        print("--reverse-batch-steps must be non-negative.", file=sys.stderr)
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
    execution_record = _empty_execution_record(args, fixed_prompt)
    _write_execution_record(execution_record, save_dir)

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
            print("Execute mode enabled. After each chunk, type 'reverse' to replay recorded actions backward.")
        else:
            print("Preview mode only. No action will be executed.")

        while _should_continue(args, chunk_index, executed_steps):
            print(f"\n=== Continuous inference chunk {chunk_index} ===")
            payload = _read_ready_model_input(env, args.attempts, args.interval)
            if payload is None:
                print("No complete observation was captured.", file=sys.stderr)
                return 1

            payload.prompt = fixed_prompt
            _print_observation_summary(payload)

            action, _, elapsed_ms = _infer_once(payload, args)
            print(f"Inference round trip: {elapsed_ms:.1f} ms")
            _print_action_summary(action)
            _save_latest_action(action, save_dir)

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

            _append_executed_chunk(
                execution_record,
                chunk_index=chunk_index,
                source_horizon=horizon,
                executed_steps=step_count,
                action=chunk_action,
            )
            _write_execution_record(execution_record, save_dir)

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

            command = _post_chunk_command(args, chunk_index)
            if command == "reverse":
                _execute_reverse(env, args, execution_record)
                print("Reverse command handled; exiting without additional inference.")
                return 0

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
