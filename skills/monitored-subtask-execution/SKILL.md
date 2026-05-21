---
name: monitored-subtask-execution
description: "Monitored single-subtask execution workflow. Use the repository MCP service `corobot_mcp_server` to start, monitor, stop, and reset one prompt-driven robot rollout through the sequence `set_evaluate_params`, poll `get_status`, then `stop/reset`. Use this skill whenever a larger workflow needs one repeatable, safe execution unit with timeout handling and deterministic cleanup."
---

# Monitored Subtask Execution

## Overview

Use a standardized procedure to call `corobot_mcp_server` MCP tools and execute one monitored robot subtask. In practice this is one CoRobot PolicyTask run defined by a prompt, an optional policy host/port, and an optional `step_interval`, followed by deterministic monitoring and cleanup.

## Inputs (provide per run)

- `prompt`: task instruction to execute; keep it directly executable and unambiguous
- `policy.host / policy.port`: optional policy server; if omitted, the service default `127.0.0.1:8999` is used
- `step_interval`: optional step interval; service default is `1.5`
- `timeout_s`: maximum time to wait for this run
- `poll_interval_s`: interval for polling `get_status`, for example `0.5` to `2.0`
- `reset_after`: whether to call `reset_task` after completion and return to the home pose; collection workflows usually want `true`
- `max_retries`: number of retries after failure; usually `0` to `2`

## Tools (MCP)

Core tools, named as `{service_name}___{tool_name}`:

- `corobot_mcp_server___set_evaluate_params`: set the prompt, policy, and `step_interval`, then auto-start the task after a short delay
- `corobot_mcp_server___get_status`: retrieve PolicyTask status to determine running, success, or failure state
- `corobot_mcp_server___stop_task`: stop the current task; prefer this before manual intervention
- `corobot_mcp_server___reset_task`: stop and reset the robot to the initial pose

Optional tools, usually not needed:

- `corobot_mcp_server___start_task`: explicit start; usually unnecessary because `set_evaluate_params` already auto-starts
- `corobot_mcp_server___get_prompt` / `corobot_mcp_server___set_prompt`: inspect or set the current prompt

For exact argument structure, see `references/mcp-tool-map.md`.

## Quick Actions

### Hard reset (no prompt)

When no prompt should be executed and you only need to return the robot to a reusable start state:

1. Call `corobot_mcp_server___stop_task` if a task might still be running.
2. Call `corobot_mcp_server___reset_task`.

## Workflow: Execute One Subtask (single prompt)

### Step 0: Safety + Preconditions

- Confirm the robot workspace is safe, emergency stop is available, and human intervention is possible.
- Confirm the MCP service is enabled and reachable. `corobot_mcp_server` is configured in `src/agent_demo/config/ormcp_services.json`.
- Decide whether this run should preserve its terminal state or return to a reusable start state:
- If only the action result matters, use `reset_after=false`.
- If the next round needs a reusable start state, use `reset_after=true`.

### Step 1: Start (set params + auto-start)

Call `corobot_mcp_server___set_evaluate_params`.

Argument template:

```json
{
  "evaluate_params": {
    "policy": {"host": "127.0.0.1", "port": 8999},
    "prompt": "<prompt>",
    "step_interval": 1.5
  }
}
```

Important behavior:

- On success, `set_evaluate_params` waits briefly and auto-calls `start_task`. Do not call `start_task` again unless you have a specific reason.

### Step 2: Monitor (poll get_status with a timeout)

- Poll `corobot_mcp_server___get_status` every `poll_interval_s` until one of these conditions is met:
- the returned status clearly indicates completion, success, or failure
- `timeout_s` is reached
- behavior becomes unsafe or manual intervention is required
- Save the raw status text from each poll as a log record even if it cannot be parsed into a structured schema.

### Step 3: Handle success / failure deterministically

Success path:

- If `reset_after=true`, call `corobot_mcp_server___reset_task`.
- Record the run metadata: `prompt`, `policy host/port`, `step_interval`, start time, end time, and final status text.

Failure, timeout, or uncertain-status path:

1. Call `corobot_mcp_server___stop_task`.
2. Call `corobot_mcp_server___reset_task`.
3. Record the failure reason, including timeout, connection failure, malformed status, or manual intervention.
4. If `max_retries` allows another attempt, return to Step 1. Otherwise mark the run as failed and exit.

## Troubleshooting

- If the result indicates a CoRobot connection failure or request failure, verify that the local CoRobot HTTP service is reachable at `http://localhost:8765` and that the robot control stack is healthy.
- If the task gets stuck, follow the failure path `stop_task -> reset_task`. Pause and restore the environment manually if needed.

## References

- `references/mcp-tool-map.md`: `corobot_mcp_server` tool list, naming rules, and argument structure
