#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def _bootstrap_paths() -> None:
    candidates = [
        REPO_ROOT / "src",
        REPO_ROOT / "robotclaw_agent/src",
        REPO_ROOT / ".a2d_pkg",
        REPO_ROOT / ".a2d_pkg/site-packages",
    ]
    cmeel_lib = REPO_ROOT / ".a2d_pkg/site-packages/cmeel.prefix/lib"
    candidates.extend(sorted(cmeel_lib.glob("python*/site-packages")))
    for path in reversed(candidates):
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


_bootstrap_paths()

from new_agent.tools.mcp_control_task_helpers import (  # noqa: E402
    DEFAULT_TASK_CONFIG_PATH,
    inverse_rigid_transform_point,
    load_task_config_transform,
    transform_point,
    vector3,
)
from new_agent.tools.mcp_control_tools import (  # noqa: E402
    DEFAULT_LOAD_UNLOAD_ARM,
    DEFAULT_LOAD_UNLOAD_DEST_TAG_ID,
    DEFAULT_LOAD_UNLOAD_GRASP_OFFSET_M,
    DEFAULT_LOAD_UNLOAD_PLACE_DESCEND_DZ_BASE_M,
    DEFAULT_LOAD_UNLOAD_PLACE_LIFT_OFFSET_M,
    DEFAULT_LOAD_UNLOAD_POLICY_TIMEOUT_S,
    DEFAULT_LOAD_UNLOAD_PRE_GRASP_LIFT_Z_M,
    DEFAULT_LOAD_UNLOAD_PRE_GRASP_NEG_X_DISTANCE_M,
    DEFAULT_LOAD_UNLOAD_PULL_POLICY_CHUNK_COUNT,
    DEFAULT_LOAD_UNLOAD_PULL_POLICY_PORT,
    DEFAULT_LOAD_UNLOAD_PULL_PROMPT,
    DEFAULT_LOAD_UNLOAD_PUSH_POLICY_CHUNK_COUNT,
    DEFAULT_LOAD_UNLOAD_PUSH_POLICY_PORT,
    DEFAULT_LOAD_UNLOAD_PUSH_PROMPT,
    DEFAULT_LOAD_UNLOAD_SOURCE_TAG_ID,
)


DEFAULT_RUNTIME_CONFIG_PATH = REPO_ROOT / "artifacts/fixed_ui_sequence/runtime_rule_control_task_config.yml"


def default_calibration_path() -> str:
    if DEFAULT_RUNTIME_CONFIG_PATH.exists():
        return str(DEFAULT_RUNTIME_CONFIG_PATH)
    return str(DEFAULT_TASK_CONFIG_PATH)


class CoRobotClient:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, method="GET")
        return self._send(request, path)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._send(request, path)

    def _send(self, request: urllib.request.Request, path: str) -> dict[str, Any]:
        try:
            with self.opener.open(request, timeout=self.timeout_s) as response:
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from {path}: {text}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"failed to reach CoRobot at {self.base_url}: {exc.reason}") from exc

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{path} returned non-JSON response: {text[:500]}") from exc

        if isinstance(data, dict) and data.get("success") is False:
            raise RuntimeError(f"{path} failed: {json.dumps(data, ensure_ascii=False)}")
        payload = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(payload, dict) and payload.get("ok") is False:
            raise RuntimeError(f"{path} returned ok=false: {json.dumps(payload, ensure_ascii=False)}")
        if not isinstance(payload, dict):
            return {"value": payload}
        return payload


def rounded(values: Any) -> list[float]:
    return [round(float(v), 6) for v in values]


def find_detected_tag(detections: list[Any], tag_id: int) -> dict[str, Any] | None:
    for item in detections:
        if isinstance(item, dict) and int(item.get("tag_id", -1)) == int(tag_id):
            return item
    return None


def detect_required_tags(client: CoRobotClient, tag_ids: list[int]) -> dict[int, dict[str, Any]]:
    detection = client.post("/skill/detect_tags", {})
    detections = detection.get("detections") or []
    if not isinstance(detections, list):
        detections = []

    result: dict[int, dict[str, Any]] = {}
    missing = []
    for tag_id in tag_ids:
        pose = find_detected_tag(detections, tag_id)
        if pose is None:
            try:
                pose = client.post("/skill/get_tag_pose", {"tag_id": tag_id})
            except RuntimeError:
                pose = None
        if pose is None or pose.get("ok") is False:
            missing.append(tag_id)
        else:
            result[int(tag_id)] = pose

    if missing:
        visible = sorted(
            int(item["tag_id"])
            for item in detections
            if isinstance(item, dict) and item.get("tag_id") is not None
        )
        raise RuntimeError(f"required tags are not visible: missing={missing}, visible={visible}")
    return result


def _top_level_yaml_block(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == f"{key}:" or stripped.startswith(f"{key}: "):
            block = [line]
            for next_line in lines[index + 1 :]:
                if next_line.strip() and not next_line.startswith((" ", "\t", "#")):
                    break
                block.append(next_line)
            return block
    return []


def _reset_pose_grippers_from_text(text: str) -> list[float] | None:
    block = _top_level_yaml_block(text, "reset_pose")
    for index, line in enumerate(block):
        stripped = line.strip()
        if not stripped.startswith("target_grippers_positions:"):
            continue
        value = stripped.split(":", 1)[1].strip()
        if value.startswith("["):
            parsed = ast.literal_eval(value)
            return [float(parsed[0]), float(parsed[1])]
        values: list[float] = []
        for next_line in block[index + 1 :]:
            item = next_line.strip()
            if item.startswith("-"):
                values.append(float(item[1:].strip()))
                if len(values) == 2:
                    return values
            elif item and not item.startswith("#"):
                break
    return None


def ensure_reset_config_will_keep_gripper_open(client: CoRobotClient) -> None:
    status = client.get("/skill/status")
    config_path = status.get("config_path")
    if not config_path:
        return

    path = Path(config_path).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return

    gripper_targets = _reset_pose_grippers_from_text(text)
    if gripper_targets is None:
        raise RuntimeError(
            "CoRobot reset config does not contain reset_pose.target_grippers_positions. "
            f"Loaded config: {path}. Add reset_pose with target_grippers_positions: [0.0, 0.0], "
            "then restart the CoRobot service."
        )
    if any(abs(value) > 1e-6 for value in gripper_targets[:2]):
        raise RuntimeError(
            "CoRobot reset would close the gripper before arm reset: "
            f"target_grippers_positions={gripper_targets}. Set it to [0.0, 0.0], "
            "then restart the CoRobot service."
        )


def call_step(
    *,
    client: CoRobotClient,
    execute: bool,
    path: str,
    payload: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    record = {"name": name, "path": path, "payload": payload, "executed": execute}
    print(f">>> {name}: {path}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if execute:
        result = client.post(path, payload)
        record["result"] = result
        print(json.dumps({"result": result}, ensure_ascii=False, indent=2))
    return record


def start_policy(
    client: CoRobotClient,
    *,
    prompt: str,
    port: int,
    chunk_count: int,
    execute: bool,
    name: str,
) -> dict[str, Any]:
    payload = {"prompt": prompt, "port": int(port), "chunk_count": int(chunk_count)}
    return call_step(client=client, execute=execute, path="/skill/start_policy", payload=payload, name=name)


def wait_policy_done(
    client: CoRobotClient,
    *,
    label: str,
    timeout_s: float,
    poll_interval_s: float,
    execute: bool,
) -> dict[str, Any]:
    if not execute:
        return {"label": label, "skipped": True, "reason": "dry_run"}

    started = time.monotonic()
    snapshots = []
    while True:
        status = client.get("/skill/policy_status")
        snapshots.append(status)
        running = bool(status.get("running"))
        failed = bool(status.get("failed"))
        completed = bool(status.get("completed"))
        executed_chunks = status.get("executed_chunks", 0)
        target_chunks = status.get("target_chunks", 0)
        print(f"[{label}] running={running} completed={completed} failed={failed} chunks={executed_chunks}/{target_chunks}")
        if failed:
            raise RuntimeError(f"{label} policy failed: {json.dumps(status, ensure_ascii=False)}")
        if not running:
            return {"label": label, "final_status": status, "snapshots": snapshots[-5:]}
        if time.monotonic() - started > timeout_s:
            raise TimeoutError(f"timed out waiting for {label} after {timeout_s:.1f}s")
        time.sleep(poll_interval_s)


def prompt_for_start_policy(prompt: str, *, bypass_policy_waist_prepose: bool) -> str:
    if not bypass_policy_waist_prepose:
        return prompt
    normalized = " ".join(str(prompt or "").strip().lower().split())
    if normalized in {"pull open the drawer", "push close the drawer"}:
        return f"{prompt}."
    return prompt


def run_pull_stage(
    *,
    client: CoRobotClient,
    execute: bool,
    prompt: str,
    port: int,
    chunk_count: int,
    timeout_s: float,
    poll_interval_s: float,
) -> dict[str, Any]:
    print("\n=== pull drawer with VLA ===")
    records = [
        start_policy(
            client,
            prompt=prompt,
            port=port,
            chunk_count=chunk_count,
            execute=execute,
            name="open_drawer_for_loading",
        ),
        wait_policy_done(
            client,
            label="open_drawer_for_loading",
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            execute=execute,
        ),
    ]
    return {"stage": "pull", "prompt": prompt, "policy_port": port, "chunk_count": chunk_count, "records": records}


def run_push_stage(
    *,
    client: CoRobotClient,
    execute: bool,
    prompt: str,
    port: int,
    chunk_count: int,
    timeout_s: float,
    poll_interval_s: float,
) -> dict[str, Any]:
    print("\n=== push drawer with VLA ===")
    records = [
        start_policy(
            client,
            prompt=prompt,
            port=port,
            chunk_count=chunk_count,
            execute=execute,
            name="close_drawer_after_loading",
        ),
        wait_policy_done(
            client,
            label="close_drawer_after_loading",
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            execute=execute,
        ),
    ]
    return {"stage": "push", "prompt": prompt, "policy_port": port, "chunk_count": chunk_count, "records": records}


def run_place_stage(
    *,
    client: CoRobotClient,
    execute: bool,
    arm: str,
    calibration_path: str,
    move_duration_s: float,
    gripper_duration_s: float,
    source_tag_id: int,
    destination_tag_id: int,
    grasp_offset_base_m: list[float],
    place_lift_offset_base_m: list[float],
    place_descend_dz_base_m: float,
    pre_grasp_neg_x_distance_m: float,
    pre_grasp_lift_z_m: float,
) -> dict[str, Any]:
    print("\n=== place workpiece into drawer with atomic actions ===")
    t_exec_camera, _approach_axis = load_task_config_transform(calibration_path)
    poses = detect_required_tags(client, [source_tag_id, destination_tag_id])
    current_eef = client.post("/skill/get_eef_pose", {"arm": arm})

    source_tag_camera = vector3(poses[source_tag_id].get("position_camera_m") or poses[source_tag_id].get("translation_m"))
    dest_tag_camera = vector3(
        poses[destination_tag_id].get("position_camera_m") or poses[destination_tag_id].get("translation_m")
    )
    current_eef_base = vector3(current_eef["position_exec_m"])
    source_tag_base = transform_point(t_exec_camera, source_tag_camera)
    dest_tag_base = transform_point(t_exec_camera, dest_tag_camera)

    grasp_base = [source_tag_base[i] + grasp_offset_base_m[i] for i in range(3)]
    grasp_camera = inverse_rigid_transform_point(t_exec_camera, grasp_base)
    pre_grasp_base = [
        current_eef_base[0] - pre_grasp_neg_x_distance_m,
        grasp_base[1],
        current_eef_base[2] + pre_grasp_lift_z_m,
    ]
    pre_grasp_camera = inverse_rigid_transform_point(t_exec_camera, pre_grasp_base)

    place_lift_base = [dest_tag_base[i] + place_lift_offset_base_m[i] for i in range(3)]
    place_lift_camera = inverse_rigid_transform_point(t_exec_camera, place_lift_base)
    place_lift_z_base = [grasp_base[0], grasp_base[1], place_lift_base[2]]
    place_lift_z_camera = inverse_rigid_transform_point(t_exec_camera, place_lift_z_base)
    place_final_base = [place_lift_base[0], place_lift_base[1], place_lift_base[2] - place_descend_dz_base_m]
    place_final_camera = inverse_rigid_transform_point(t_exec_camera, place_final_base)

    steps = [
        ("open_gripper", "/skill/gripper", {"arm": arm, "gripper_value": 0.0, "duration_s": gripper_duration_s}),
        (
            "move_to_grasp_neg_x_prealign",
            "/skill/move_eef",
            {"arm": arm, "target_position_camera_m": rounded(pre_grasp_camera), "duration_s": move_duration_s},
        ),
        (
            "move_to_grasp",
            "/skill/move_eef",
            {"arm": arm, "target_position_camera_m": rounded(grasp_camera), "duration_s": move_duration_s},
        ),
        ("close_gripper", "/skill/gripper", {"arm": arm, "gripper_value": 1.0, "duration_s": gripper_duration_s}),
        (
            "move_vertical_to_drawer_lift_z",
            "/skill/move_eef",
            {"arm": arm, "target_position_camera_m": rounded(place_lift_z_camera), "duration_s": move_duration_s},
        ),
        (
            "move_horizontal_to_drawer_lift_xy",
            "/skill/move_eef",
            {"arm": arm, "target_position_camera_m": rounded(place_lift_camera), "duration_s": move_duration_s},
        ),
        (
            "move_down_to_drawer_place",
            "/skill/move_eef",
            {"arm": arm, "target_position_camera_m": rounded(place_final_camera), "duration_s": move_duration_s},
        ),
        ("open_gripper_release", "/skill/gripper", {"arm": arm, "gripper_value": 0.0, "duration_s": gripper_duration_s}),
        ("reset_robot_after_drawer_tag_place", "/skill/reset_robot", {}),
    ]
    records = [
        call_step(client=client, execute=execute, path=path, payload=payload, name=name)
        for name, path, payload in steps
    ]
    return {
        "stage": "place",
        "source_tag_id": source_tag_id,
        "destination_tag_id": destination_tag_id,
        "grasp_offset_base_m": grasp_offset_base_m,
        "place_lift_offset_base_m": place_lift_offset_base_m,
        "place_descend_dz_base_m": place_descend_dz_base_m,
        "pre_grasp_neg_x_distance_m": pre_grasp_neg_x_distance_m,
        "pre_grasp_lift_z_m": pre_grasp_lift_z_m,
        "computed_targets": {
            "source_tag_base_m": rounded(source_tag_base),
            "dest_tag_base_m": rounded(dest_tag_base),
            "current_eef_base_m": rounded(current_eef_base),
            "pre_grasp_camera_m": rounded(pre_grasp_camera),
            "grasp_camera_m": rounded(grasp_camera),
            "place_lift_z_camera_m": rounded(place_lift_z_camera),
            "place_lift_camera_m": rounded(place_lift_camera),
            "place_final_camera_m": rounded(place_final_camera),
        },
        "steps": records,
    }


def run_load_unload(args: argparse.Namespace, client: CoRobotClient) -> dict[str, Any]:
    stage = args.stage
    records: list[dict[str, Any]] = []

    if stage in ("all", "pull", "pull_place"):
        records.append(
            run_pull_stage(
                client=client,
                execute=args.execute,
                prompt=prompt_for_start_policy(
                    args.pull_prompt,
                    bypass_policy_waist_prepose=args.bypass_policy_waist_prepose,
                ),
                port=args.pull_policy_port,
                chunk_count=args.pull_chunk_count,
                timeout_s=args.policy_timeout_s,
                poll_interval_s=args.policy_poll_interval_s,
            )
        )

    if stage in ("all", "place", "pull_place"):
        records.append(
            run_place_stage(
                client=client,
                execute=args.execute,
                arm=args.arm,
                calibration_path=args.calibration_path,
                move_duration_s=args.move_duration_s,
                gripper_duration_s=args.gripper_duration_s,
                source_tag_id=args.source_tag_id,
                destination_tag_id=args.destination_tag_id,
                grasp_offset_base_m=args.grasp_offset_base_m,
                place_lift_offset_base_m=args.place_lift_offset_base_m,
                place_descend_dz_base_m=args.place_descend_dz_base_m,
                pre_grasp_neg_x_distance_m=args.pre_grasp_neg_x_distance_m,
                pre_grasp_lift_z_m=args.pre_grasp_lift_z_m,
            )
        )

    if stage in ("all", "push"):
        records.append(
            run_push_stage(
                client=client,
                execute=args.execute,
                prompt=prompt_for_start_policy(
                    args.push_prompt,
                    bypass_policy_waist_prepose=args.bypass_policy_waist_prepose,
                ),
                port=args.push_policy_port,
                chunk_count=args.push_chunk_count,
                timeout_s=args.policy_timeout_s,
                poll_interval_s=args.policy_poll_interval_s,
            )
        )

    return {
        "task": "direct_load_unload",
        "stage": stage,
        "fixed_vla_prompts": {
            "pull": prompt_for_start_policy(
                args.pull_prompt,
                bypass_policy_waist_prepose=args.bypass_policy_waist_prepose,
            ),
            "push": prompt_for_start_policy(
                args.push_prompt,
                bypass_policy_waist_prepose=args.bypass_policy_waist_prepose,
            ),
        },
        "bypass_policy_waist_prepose": bool(args.bypass_policy_waist_prepose),
        "policy_ports": {"pull": args.pull_policy_port, "push": args.push_policy_port},
        "chunk_counts": {"pull": args.pull_chunk_count, "push": args.push_chunk_count},
        "records": records,
    }


def parse_vector3(value: str) -> list[float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected three comma-separated numbers, for example 0,0,-0.03")
    try:
        return [float(part) for part in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the drawer load/unload task directly with atomic CoRobot skills and fixed VLA prompts."
    )
    parser.add_argument("--execute", action="store_true", help="Actually send motion/policy commands to the robot.")
    parser.add_argument("--stage", choices=["all", "pull", "place", "push", "pull_place"], default="all")
    parser.add_argument("--corobot-url", default=os.environ.get("COROBOT_URL", "http://localhost:8765"))
    parser.add_argument("--arm", default=DEFAULT_LOAD_UNLOAD_ARM, choices=["left", "right"])
    parser.add_argument("--calibration-path", default=default_calibration_path())
    parser.add_argument("--move-duration-s", type=float, default=2.0)
    parser.add_argument("--gripper-duration-s", type=float, default=0.5)
    parser.add_argument("--request-timeout-s", type=float, default=180.0)
    parser.add_argument("--policy-timeout-s", type=float, default=float(DEFAULT_LOAD_UNLOAD_POLICY_TIMEOUT_S))
    parser.add_argument("--policy-poll-interval-s", type=float, default=1.0)
    parser.add_argument("--pull-prompt", default=DEFAULT_LOAD_UNLOAD_PULL_PROMPT)
    parser.add_argument("--push-prompt", default=DEFAULT_LOAD_UNLOAD_PUSH_PROMPT)
    parser.add_argument(
        "--bypass-policy-waist-prepose",
        action="store_true",
        help=(
            "Avoid RuleControlTask's exact-match drawer waist pre/post pose branch by adding punctuation to "
            "the fixed drawer prompts before POST /skill/start_policy."
        ),
    )
    parser.add_argument("--pull-policy-port", type=int, default=DEFAULT_LOAD_UNLOAD_PULL_POLICY_PORT)
    parser.add_argument("--push-policy-port", type=int, default=DEFAULT_LOAD_UNLOAD_PUSH_POLICY_PORT)
    parser.add_argument("--pull-chunk-count", type=int, default=DEFAULT_LOAD_UNLOAD_PULL_POLICY_CHUNK_COUNT)
    parser.add_argument("--push-chunk-count", type=int, default=DEFAULT_LOAD_UNLOAD_PUSH_POLICY_CHUNK_COUNT)
    parser.add_argument("--source-tag-id", type=int, default=DEFAULT_LOAD_UNLOAD_SOURCE_TAG_ID)
    parser.add_argument("--destination-tag-id", type=int, default=DEFAULT_LOAD_UNLOAD_DEST_TAG_ID)
    parser.add_argument("--grasp-offset-base-m", type=parse_vector3, default=list(DEFAULT_LOAD_UNLOAD_GRASP_OFFSET_M))
    parser.add_argument(
        "--place-lift-offset-base-m",
        type=parse_vector3,
        default=list(DEFAULT_LOAD_UNLOAD_PLACE_LIFT_OFFSET_M),
    )
    parser.add_argument("--place-descend-dz-base-m", type=float, default=float(DEFAULT_LOAD_UNLOAD_PLACE_DESCEND_DZ_BASE_M))
    parser.add_argument(
        "--pre-grasp-neg-x-distance-m",
        type=float,
        default=float(DEFAULT_LOAD_UNLOAD_PRE_GRASP_NEG_X_DISTANCE_M),
    )
    parser.add_argument("--pre-grasp-lift-z-m", type=float, default=float(DEFAULT_LOAD_UNLOAD_PRE_GRASP_LIFT_Z_M))
    parser.add_argument("--output", default=None, help="Optional JSON log path.")
    return parser.parse_args()


def save_run_log(run_log: dict[str, Any], output_arg: str | None) -> Path:
    output = Path(output_arg) if output_arg else REPO_ROOT / "artifacts/load_unload/latest_run.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> int:
    args = parse_args()
    client = CoRobotClient(args.corobot_url, args.request_timeout_s)
    if not args.execute:
        print("Dry-run mode: perception and target computation run, but no motion/policy command is sent.")
        print("Add --execute to move the real robot.")
    else:
        try:
            ensure_reset_config_will_keep_gripper_open(client)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    run_log: dict[str, Any] = {
        "started_at_s": time.time(),
        "execute": bool(args.execute),
        "corobot_url": args.corobot_url,
        "arm": args.arm,
        "calibration_path": args.calibration_path,
        "stage": args.stage,
        "tasks": [],
    }

    try:
        run_log["tasks"].append(run_load_unload(args, client))
    except Exception as exc:
        run_log["error"] = str(exc)
        output = save_run_log(run_log, args.output)
        print(f"\nSaved run log: {output}")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    output = save_run_log(run_log, args.output)
    print(f"\nSaved run log: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
