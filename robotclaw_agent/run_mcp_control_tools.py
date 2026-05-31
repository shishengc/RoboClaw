#!/usr/bin/env python3
"""Minimal NewAgent tool-call script for tag grasp."""

from __future__ import annotations

import argparse
import asyncio
import ast
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
ROBOCLAW_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from new_agent.components.tool_registry import ToolRegistry
from new_agent.tools.mcp_control_recipes import (
    DEFAULT_TAG_PICK_PLACE_RECIPE,
    get_tag_pick_place_recipe,
    recipe_names,
)
from new_agent.tools.mcp_control_tools import DEFAULT_COROBOT_BASE_URL, register_mcp_control_tools


DEFAULT_CALIBRATION_PATH = ROBOCLAW_ROOT / "src/mcp_control_demo/config/mcp_control_calibration.yaml"


def parse_args() -> argparse.Namespace:
    argv = sys.argv[1:]
    parser = argparse.ArgumentParser(description="Grasp tag 0 through NewAgent tools.")
    parser.add_argument("--arm", choices=["left", "right"], default="right")
    parser.add_argument("--tag-id", type=int, default=0)
    parser.add_argument("--base-url", default=os.environ.get("COROBOT_URL", DEFAULT_COROBOT_BASE_URL))
    parser.add_argument("--camera-frame", default="head_camera_optical")
    parser.add_argument("--calibration-path", default=str(DEFAULT_CALIBRATION_PATH))
    parser.add_argument("--recipe-name", choices=recipe_names(), default=DEFAULT_TAG_PICK_PLACE_RECIPE)
    parser.add_argument("--base-dx", type=float, default=None)
    parser.add_argument("--base-dy", type=float, default=None)
    parser.add_argument("--base-dz", type=float, default=None)
    parser.add_argument("--approach-distance", type=float, default=None)
    parser.add_argument("--lift-height", type=float, default=None)
    parser.add_argument("--move-duration", type=float, default=1.0)
    parser.add_argument("--gripper-duration", type=float, default=0.5)
    parser.add_argument("--allow-stale", action="store_true")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args(argv)


async def main() -> int:
    args = parse_args()
    registry = ToolRegistry()
    register_mcp_control_tools(registry, base_url=args.base_url)

    async def tool(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        print(f"\n>>> {name}")
        print(json.dumps(payload, ensure_ascii=False))
        result = await registry.execute(name, **payload)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        if not result.success:
            raise RuntimeError(f"{name} failed: {result.message}")
        return result.data

    await tool("detect_tags", {})
    tag = await tool("get_apriltag_pose", {"tag_id": args.tag_id, "allow_stale": args.allow_stale})

    source_offset, approach_distance, lift_height = effective_grasp_recipe(args)
    grasp_camera, approach_camera, lift_camera = build_targets(args, tag)
    print("\ncomputed targets")
    print(json.dumps({
        "recipe_name": args.recipe_name,
        "base_offset_m": source_offset,
        "approach_distance_m": approach_distance,
        "lift_height_m": lift_height,
        "approach_camera_m": approach_camera,
        "grasp_camera_m": grasp_camera,
        "lift_camera_m": lift_camera,
    }, ensure_ascii=False, indent=2))

    if not args.execute:
        print("\nDry run only. Add --execute to move the robot.")
        return 0

    await tool("open_gripper", {"arm": args.arm, "duration_s": args.gripper_duration})
    await tool("move_eef", {"arm": args.arm, "camera_frame": args.camera_frame, "target_position_camera_m": approach_camera, "duration_s": args.move_duration})
    await tool("move_eef", {"arm": args.arm, "camera_frame": args.camera_frame, "target_position_camera_m": grasp_camera, "duration_s": args.move_duration})
    await tool("close_gripper", {"arm": args.arm, "duration_s": args.gripper_duration})
    await tool("move_eef", {"arm": args.arm, "camera_frame": args.camera_frame, "target_position_camera_m": lift_camera, "duration_s": args.move_duration})
    return 0


def effective_grasp_recipe(args: argparse.Namespace) -> tuple[list[float], float, float]:
    recipe = get_tag_pick_place_recipe(args.recipe_name)
    source_offset = list(recipe.source_base_offset_m)
    if args.base_dx is not None:
        source_offset[0] = args.base_dx
    if args.base_dy is not None:
        source_offset[1] = args.base_dy
    if args.base_dz is not None:
        source_offset[2] = args.base_dz
    approach_distance = args.approach_distance if args.approach_distance is not None else recipe.approach_distance_m
    lift_height = args.lift_height if args.lift_height is not None else recipe.lift_height_m
    return source_offset, approach_distance, lift_height


def build_targets(args: argparse.Namespace, tag: dict[str, Any]) -> tuple[list[float], list[float], list[float]]:
    source_offset, approach_distance, lift_height = effective_grasp_recipe(args)
    t_exec_camera, approach_axis = load_calibration(args.calibration_path)
    tag_camera = vector3(tag.get("position_camera_m") or tag.get("translation_m"))
    tag_base = transform_point(t_exec_camera, tag_camera)
    grasp_base = add3(tag_base, source_offset)
    grasp_camera = inverse_rigid_transform_point(t_exec_camera, grasp_base)
    approach_camera = add3(grasp_camera, scale3(approach_axis, approach_distance))
    lift_base = add3(grasp_base, [0.0, 0.0, lift_height])
    lift_camera = inverse_rigid_transform_point(t_exec_camera, lift_base)
    return rounded(grasp_camera), rounded(approach_camera), rounded(lift_camera)


def load_calibration(path: str) -> tuple[list[list[float]], list[float]]:
    text = Path(path).read_text(encoding="utf-8")
    return find_matrix4(text, "T_exec_camera"), normalize3(find_vector(text, "camera_approach_axis") or [0.0, 0.0, -1.0])


def find_vector(text: str, key: str) -> list[float] | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            return vector3(ast.literal_eval(stripped.split(":", 1)[1].strip()))
    return None


def find_matrix4(text: str, key: str) -> list[list[float]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            rows = []
            for row_line in lines[index + 1:]:
                stripped = row_line.strip()
                if not stripped:
                    continue
                if not stripped.startswith("- "):
                    break
                rows.append([float(v) for v in ast.literal_eval(stripped[2:].strip())])
                if len(rows) == 4:
                    break
            if len(rows) == 4 and all(len(row) == 4 for row in rows):
                return rows
    raise ValueError(f"{key} not found or not a 4x4 matrix")


def vector3(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("expected 3 values")
    return [float(v) for v in value]


def add3(a: list[float], b: list[float]) -> list[float]:
    return [a[i] + b[i] for i in range(3)]


def scale3(v: list[float], scale: float) -> list[float]:
    return [x * scale for x in v]


def normalize3(v: list[float]) -> list[float]:
    norm = sum(x * x for x in v) ** 0.5
    if norm <= 1e-12:
        raise ValueError("zero vector")
    return [x / norm for x in v]


def transform_point(matrix: list[list[float]], point: list[float]) -> list[float]:
    return [sum(matrix[row][col] * point[col] for col in range(3)) + matrix[row][3] for row in range(3)]


def inverse_rigid_transform_point(matrix: list[list[float]], point: list[float]) -> list[float]:
    shifted = [point[i] - matrix[i][3] for i in range(3)]
    return [sum(matrix[row][col] * shifted[row] for row in range(3)) for col in range(3)]


def rounded(v: list[float]) -> list[float]:
    return [round(float(x), 6) for x in v]


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
