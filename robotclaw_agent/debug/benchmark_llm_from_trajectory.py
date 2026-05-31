#!/usr/bin/env python3
"""
Benchmark an OpenAI-compatible chat API with reconstructed trajectory input.

The trajectory files do not store every exact LLM request. This script
reconstructs an approximate request by replaying messages up to a selected
assistant_tool_calls step.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from new_agent.components.tool_registry import ToolRegistry
from new_agent.prompt.prompt_builder import build_system_prompt
from new_agent.tools.mcp_control_tools import register_mcp_control_agent_tools


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark an OpenAI-compatible API using reconstructed trajectory input."
    )
    parser.add_argument("trajectory", help="Path to a trajectory JSON file.")
    parser.add_argument("--list", action="store_true", help="List assistant_tool_calls in the trajectory and exit.")
    parser.add_argument("--call-index", type=int, default=None, help="1-based assistant_tool_calls index to replay.")
    parser.add_argument("--assistant-step", type=int, default=None, help="Trajectory step number of assistant_tool_calls to replay.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o"), help="Model name.")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL"), help="OpenAI-compatible base URL.")
    parser.add_argument("--api-key", default=None, help="API key. Overrides --api-key-env.")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable containing API key.")
    parser.add_argument("--timeout", type=float, default=120.0, help="API timeout seconds.")
    parser.add_argument("--max-completion-tokens", type=int, default=512)
    parser.add_argument("--repeat", type=int, default=1, help="Number of timing runs.")
    parser.add_argument(
        "--tools",
        choices=["mcp", "none"],
        default="mcp",
        help="Whether to include registered mcp_control tool schemas.",
    )
    parser.add_argument("--dump-input", default=None, help="Write reconstructed request JSON to this path.")
    parser.add_argument("--dry-run", action="store_true", help="Only reconstruct input; do not call the API.")
    return parser


def load_trajectory(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def assistant_tool_events(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        event
        for event in data.get("trajectory", [])
        if event.get("type") == "assistant_tool_calls"
    ]


def list_calls(data: dict[str, Any]) -> None:
    for index, event in enumerate(assistant_tool_events(data), start=1):
        calls = event.get("detail", {}).get("tool_calls", [])
        call_names = ", ".join(call.get("name", "") for call in calls)
        content = event.get("detail", {}).get("content")
        print(
            f"{index:02d} step={event.get('step')} "
            f"time={event.get('timestamp')} calls=[{call_names}] "
            f"content={shorten(content, 90)}"
        )


def select_event(data: dict[str, Any], call_index: int | None, assistant_step: int | None) -> dict[str, Any]:
    events = assistant_tool_events(data)
    if assistant_step is not None:
        for event in events:
            if event.get("step") == assistant_step:
                return event
        raise SystemExit(f"assistant_tool_calls step not found: {assistant_step}")
    if call_index is None:
        raise SystemExit("Specify --call-index or --assistant-step, or use --list.")
    if call_index < 1 or call_index > len(events):
        raise SystemExit(f"--call-index must be in 1..{len(events)}")
    return events[call_index - 1]


def reconstruct_messages(data: dict[str, Any], target_step: int) -> list[dict[str, Any]]:
    events = data.get("trajectory", [])
    target_index = next(
        (idx for idx, event in enumerate(events) if event.get("step") == target_step),
        None,
    )
    if target_index is None:
        raise SystemExit(f"step not found: {target_step}")

    start_index = 0
    for idx in range(target_index - 1, -1, -1):
        if events[idx].get("type") == "user_input":
            start_index = idx
            break

    task_brief = events[start_index].get("detail", {}).get("input") or data.get("task_brief", "")
    action_guidance = data.get("action_guidance", "")
    system_prompt = build_system_prompt(
        task_brief=task_brief,
        action_guidance=action_guidance,
    )
    system_prompt += (
        "\nReconstruction note: this request was reconstructed from a trajectory. "
        "It is intended for latency testing and may not be byte-identical to the original runtime request.\n"
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    pending_tool_ids: list[tuple[str, str]] = []

    for event in events[start_index:target_index]:
        event_type = event.get("type")
        detail = event.get("detail", {})

        if event_type == "user_input":
            messages.append({"role": "user", "content": detail.get("input", "")})
        elif event_type == "assistant_tool_calls":
            openai_tool_calls = []
            for call in detail.get("tool_calls", []):
                call_id = call.get("id") or f"call_reconstructed_{len(pending_tool_ids)}"
                call_name = call.get("name", "")
                args = call.get("arguments") or {}
                pending_tool_ids.append((call_id, call_name))
                openai_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": call_name,
                            "arguments": json.dumps(args, ensure_ascii=False),
                        },
                    }
                )
            messages.append(
                {
                    "role": "assistant",
                    "content": detail.get("content"),
                    "tool_calls": openai_tool_calls,
                }
            )
        elif event_type in {"tool_call", "tool_call_deferred"}:
            call_id, call_name = pop_tool_id(pending_tool_ids, detail.get("tool", ""))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": call_name or detail.get("tool", ""),
                    "content": json.dumps(
                        {
                            "status": detail.get("status"),
                            "message": detail.get("message"),
                            "data": detail.get("result", {}),
                            "tool_name": detail.get("tool"),
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        elif event_type == "perception":
            messages.append(
                {
                    "role": "assistant",
                    "content": f"[Perception Result] {detail.get('result', {})}",
                }
            )
        elif event_type in {"batch_checkpoint", "batch_aborted"}:
            messages.append(
                {
                    "role": "assistant",
                    "content": f"[Runtime Event] {event_type}: {detail}",
                }
            )
        elif event_type == "final_response":
            messages.append({"role": "assistant", "content": detail.get("content", "")})

    return messages


def pop_tool_id(pending: list[tuple[str, str]], tool_name: str) -> tuple[str, str]:
    for idx, item in enumerate(pending):
        if item[1] == tool_name:
            return pending.pop(idx)
    if pending:
        return pending.pop(0)
    return f"call_missing_{tool_name}", tool_name


def build_tools(mode: str) -> list[dict[str, Any]] | None:
    if mode == "none":
        return None
    registry = ToolRegistry()
    register_mcp_control_agent_tools(registry)
    return registry.get_openai_tools()


async def call_api(
    *,
    api_key: str,
    base_url: str | None,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    timeout: float,
    max_completion_tokens: int,
) -> tuple[float, Any]:
    client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = AsyncOpenAI(**client_kwargs)

    request_args: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_completion_tokens,
    }
    if tools:
        request_args["tools"] = tools
        request_args["tool_choice"] = "auto"

    started = time.perf_counter()
    response = await client.chat.completions.create(**request_args)
    elapsed = time.perf_counter() - started
    return elapsed, response


def summarize_response(response: Any) -> dict[str, Any]:
    message = response.choices[0].message
    tool_calls = []
    for tool_call in message.tool_calls or []:
        tool_calls.append(
            {
                "id": tool_call.id,
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments,
            }
        )
    usage = response.usage
    return {
        "model": response.model,
        "finish_reason": response.choices[0].finish_reason,
        "content": message.content,
        "tool_calls": tool_calls,
        "usage": None
        if usage is None
        else {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        },
    }


def shorten(value: Any, max_len: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


async def main_async() -> None:
    args = build_parser().parse_args()
    data = load_trajectory(args.trajectory)

    if args.list:
        list_calls(data)
        return

    selected = select_event(data, args.call_index, args.assistant_step)
    target_step = int(selected["step"])
    messages = reconstruct_messages(data, target_step)
    tools = build_tools(args.tools)
    request_payload = {
        "model": args.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto" if tools else None,
        "max_completion_tokens": args.max_completion_tokens,
        "trajectory": str(args.trajectory),
        "selected_assistant_step": target_step,
        "historical_output": selected.get("detail", {}),
    }

    if args.dump_input:
        Path(args.dump_input).write_text(
            json.dumps(request_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote reconstructed request: {args.dump_input}")

    print(f"trajectory: {args.trajectory}")
    print(f"selected assistant_tool_calls step: {target_step}")
    print(f"messages: {len(messages)}")
    print(f"tools: {0 if tools is None else len(tools)}")
    print(f"approx message chars: {sum(len(str(message)) for message in messages)}")
    print(
        "historical calls:",
        [call.get("name") for call in selected.get("detail", {}).get("tool_calls", [])],
    )

    if args.dry_run:
        return

    api_key = args.api_key or os.getenv(args.api_key_env)
    if not api_key:
        raise SystemExit(f"Missing API key. Set {args.api_key_env} or pass --api-key.")

    latencies: list[float] = []
    for run_index in range(1, args.repeat + 1):
        elapsed, response = await call_api(
            api_key=api_key,
            base_url=args.base_url,
            model=args.model,
            messages=messages,
            tools=tools,
            timeout=args.timeout,
            max_completion_tokens=args.max_completion_tokens,
        )
        latencies.append(elapsed)
        summary = summarize_response(response)
        print(f"\nrun {run_index}: {elapsed:.3f}s")
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    if len(latencies) > 1:
        print(
            "\nlatency summary:",
            json.dumps(
                {
                    "count": len(latencies),
                    "min_s": min(latencies),
                    "max_s": max(latencies),
                    "avg_s": sum(latencies) / len(latencies),
                },
                indent=2,
            ),
        )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
