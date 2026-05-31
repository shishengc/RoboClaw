# NewAgent 架构介绍

本文说明 `/home/ck/RoboClaw/robotclaw_agent` 中新版机器人 Agent 的实现方式。这个 Agent 的核心目标是：让大模型负责高层决策和任务推进，让工具负责真实感知、运动、抓取、放置和验证。

## 1. 总体定位

NewAgent 是一个面向机器人长程操作任务的 Agent 框架。它采用 ReAct 式循环：

```text
用户任务 -> 构造上下文 -> LLM 决策工具调用 -> 执行原子工具
       -> 记录结果 -> 行动后感知验证 -> 再次决策 -> 显式结束任务
```

它不是底层控制器。底层控制、坐标计算、视觉感知、HTTP API 调用、仿真器脚本等都被封装成工具。Agent 只决定下一步应该调用什么工具，以及如何根据工具结果继续推进任务。

## 2. 代码入口和核心模块

核心源码位于：

```text
robotclaw_agent/src/new_agent/
```

主要模块如下：

```text
core/agent.py                 NewAgent 主类和 ReAct 主循环
core/state_machine.py         Agent 状态机
core/config.py                运行配置加载
llm/llm_client.py             LLM 客户端，支持 mock 和真实 OpenAI 调用
llm/llm_response.py           LLM 返回结构和 ToolCall 类型
components/tool_registry.py   工具注册、schema 生成、工具执行
tools/tool_types.py           工具统一契约 ToolResult / ToolSchema / BaseTool
tools/shisheng_tools.py       shisheng/GenieSim 工具适配
tools/mcp_control_tools.py    CoRobot mcp_control 工具适配
memory/memory_manager.py      任务记忆管理
memory/task_node.py           单个任务节点
memory/perception.py          感知结果标准化
prompt/system_prompt.py       系统提示词模板
prompt/prompt_builder.py      prompt 构建器
cli.py                        普通命令行入口
tui.py                        真实机器人任务 TUI 入口
```

## 3. NewAgent 初始化流程

`NewAgent` 主类在 `src/new_agent/core/agent.py` 中。

初始化时会创建以下对象：

- `AgentConfig`：读取模型、最大步数、工具超时、LLM 超时、轨迹保存目录等配置。
- `StateMachine`：记录 Agent 当前状态，例如 `READY`、`CHAT`、`ACT`、`PERCEIVE`、`FINALIZE`。
- `ToolRegistry`：统一保存 mock 工具和真实工具。
- `MemoryManager`：保存当前任务、消息历史、工具结果、感知结果和失败记录。
- `LLMClient`：负责调用 mock LLM 或真实 OpenAI 模型。

`init_agent()` 的核心流程：

1. 加载配置。
2. 状态切换到 `READY`。
3. 根据参数注册工具：
   - 默认注册 mock 工具。
   - `use_shisheng_tools=True` 时注册 shisheng 工具。
   - `use_mcp_control_tools=True` 时注册 CoRobot mcp_control 工具。
4. 创建当前任务节点。
5. 设置默认任务计划。
6. 初始化 LLM 客户端。

默认配置文件是：

```text
robotclaw_agent/config/agent_config.json
```

其中包括：

- `model`
- `max_steps`
- `max_tool_retries`
- `llm_timeout_seconds`
- `tool_timeout_seconds`
- `finalize_min_completion`
- `trajectory_dir`

## 4. ReAct 主循环

Agent 的核心执行逻辑是 `NewAgent.run_once(user_input)`。

每轮循环大致如下：

1. 将用户输入写入当前任务记忆。
2. 状态切换为 `CHAT`。
3. 从 `MemoryManager` 获取当前上下文。
4. 从 `ToolRegistry` 获取 OpenAI tools schema。
5. 调用 `LLMClient.chat()`。
6. 如果 LLM 没有返回工具调用：
   - 将模型文本作为最终回复。
   - 如果存在失败记录，则改为“任务未确认完成”。
   - 保存轨迹并结束。
7. 如果 LLM 返回 `tool_calls`：
   - 逐个执行工具。
   - 将工具结果写入记忆。
   - 更新任务计划状态。
   - 记录执行轨迹。
   - 如果工具失败，写入失败记录。
8. 如果执行了非感知工具，Agent 自动调用 `SenseEnvironment` 做行动后验证。
9. 如果 LLM 调用 `FinalizeTask`，Agent 会先检查是否允许结束任务。

这个循环最多执行 `max_steps` 轮，防止无限运行。

## 5. 行动后主动感知

行动后感知是这个 Agent 的关键设计。

当 LLM 调用了动作类工具，例如定位、抓取、移动、放置之后，Agent 不会直接进入下一步，而是自动调用 `SenseEnvironment`。这样下一轮 LLM 决策时能看到最新环境状态。

自动感知会记录：

- 当前环境对象。
- 机器人状态。
- 夹爪状态。
- 任务完成度。
- 安全状态。
- 不确定性信息。
- 原始工具返回。

感知结果会被标准化成稳定结构，避免不同后端返回格式不一致影响 Agent 判断。

## 6. 显式任务结束

Agent 不允许模型只用一句“任务完成了”来结束真实任务。正确结束方式是调用 `FinalizeTask` 工具。

但是 `FinalizeTask` 也不是无条件通过。Agent 会检查：

- 是否存在最近一次感知结果。
- `safety_status.is_safe` 是否为真。
- 如果感知给出了完成度，是否达到 `finalize_min_completion`。
- 如果有失败历史，但最近感知没有明确完成度，则阻止结束。

这能减少模型在没有验证的情况下提前宣布任务完成。

## 7. 工具系统

所有工具都遵循统一契约，定义在 `src/new_agent/tools/tool_types.py`。

标准工具返回格式是：

```python
ToolResult(
    status=ToolStatus.SUCCESS,
    message="human readable message",
    data={},
    raw_output="",
    tool_name="ToolName",
)
```

工具状态包括：

- `success`
- `failed`
- `partial`

每个真实工具继承 `BaseTool`，实现：

```python
async def execute(self, **kwargs) -> ToolResult
```

工具可以提供 `get_schema()`，用于生成 OpenAI function calling schema。`ToolRegistry.get_openai_tools()` 会把所有工具转换成 OpenAI `tools` 参数。

## 8. Mock 工具和真实工具

默认 mock 模式会注册：

- `LocateObject`
- `GraspObject`
- `PlaceObject`
- `SenseEnvironment`
- `FinalizeTask`

mock LLM 会按固定顺序模拟：

```text
LocateObject -> GraspObject -> PlaceObject -> FinalizeTask
```

真实工具目前有两类适配。

### 8.1 shisheng / GenieSim 工具

实现文件：

```text
src/new_agent/tools/shisheng_tools.py
```

这层适配不会重新实现机器人控制逻辑，而是包装 shisheng 仓库已有的脚本入口：

- `run_tool_in_container.sh`
- `run_local_tool.sh`
- `ensure_abs_pose_running.sh`

典型工具包括：

- `EnsureAbsPoseRunning`
- `LocalizeTarget`
- `ApproachTarget`
- `AlignTarget`
- `CheckGraspReady`
- `GraspAtCurrent`
- `MoveEndEffectorToWorld`
- `PlaceHeldObject`
- `ReturnToDefaultAbsPose`
- `VerifyTaskState`
- `SenseEnvironment`

### 8.2 CoRobot mcp_control 工具

实现文件：

```text
src/new_agent/tools/mcp_control_tools.py
```

这层适配把 CoRobot `/skill/...` HTTP API 包装成 Agent 工具。典型工具包括：

- `reset_robot`
- `detect_tags`
- `get_apriltag_pose`
- `open_gripper`
- `close_gripper`
- `move_eef`
- `place_down`
- `compute_tag_grasp_targets`
- `compute_tag_place_targets`
- `SenseEnvironment`

其中 `compute_tag_grasp_targets` 和 `compute_tag_place_targets` 是纯计算工具，不直接移动机器人。它们根据 AprilTag 位姿和标定参数计算抓取、靠近、抬升、放置目标。

mcp_control 适配里还实现了简单的顺序约束，例如防止 LLM 在夹爪还没有到达抓取位并闭合之前直接移动到 lift 目标。

## 9. LLM 客户端

LLM 调用封装在：

```text
src/new_agent/llm/llm_client.py
```

支持两种模式：

- `mock_mode=True`：开发调试，不调用真实模型。
- `mock_mode=False`：调用 OpenAI API。

真实调用时，LLMClient 会：

1. 发送 `messages`。
2. 发送工具 schema 到 `tools` 参数。
3. 使用 `tool_choice="auto"`。
4. 解析模型返回的 `message.tool_calls`。
5. 转换成内部 `ToolCall` 对象。
6. 统计 token 使用。

内部统一返回 `LLMResponse`：

```python
LLMResponse(
    content="...",
    tool_calls=[ToolCall(...)],
    total_tokens=0,
    finish_reason="tool_calls",
    model="...",
)
```

## 10. 记忆系统

记忆系统由 `MemoryManager` 和 `TaskNode` 组成。

`MemoryManager` 负责：

- 创建任务。
- 切换任务。
- 添加用户消息。
- 添加 assistant 消息。
- 添加工具调用结果。
- 添加感知结果。
- 记录失败。
- 构建 LLM 上下文。

`TaskNode` 保存单个任务的信息：

- `task_id`
- `task_brief`
- `action_guidance`
- `messages`
- `completion_status`
- `last_perception_result`
- `subtask_history`
- `task_plan`
- `failure_history`

构造上下文时，`TaskNode` 会把当前进度摘要和最近感知结果附加到 system prompt 中，让模型优先看到最新状态。

## 11. Prompt 设计

系统提示词在：

```text
src/new_agent/prompt/system_prompt.py
```

它只描述 Agent 的角色和行为规则，不塞入具体工具列表。具体工具 schema 通过 LLM API 的 `tools` 参数传入。

主要规则包括：

- 使用原子工具一步一步完成任务。
- 动作后通常调用感知工具验证。
- 只能通过 `FinalizeTask` 显式结束任务。
- 工具失败或结果不明确时，要先感知和恢复，不能假装成功。
- 安全状态异常时停止正常推进。

这种设计更接近现代 function calling Agent，而不是把工具说明拼进长 prompt。

## 12. 状态机

状态机定义在：

```text
src/new_agent/core/state_machine.py
```

状态包括：

- `INIT`：初始化。
- `READY`：就绪。
- `CHAT`：正在请求 LLM 决策。
- `ACT`：正在执行工具。
- `PERCEIVE`：正在感知环境。
- `FINALIZE`：任务结束。

状态机主要用于日志、轨迹和调试，不承担复杂业务控制。

## 13. 轨迹记录

Agent 会把每一步执行过程记录到 `trajectory` 中，并在任务结束或异常时保存成 JSON 文件。

默认保存目录：

```text
robotclaw_agent/trajectories/
```

轨迹内容包括：

- agent 名称。
- 当前任务描述。
- action guidance。
- 最终响应。
- 每一步的类型、详情、状态、时间戳。
- 最终上下文快照。

这对调试 LLM 决策、工具失败和机器人执行过程很重要。

## 14. 交互入口

普通 CLI：

```bash
cd /home/ck/RoboClaw/robotclaw_agent
PYTHONPATH=src python -m new_agent.cli
```

真实机器人 TUI：

```bash
cd /home/ck/RoboClaw/robotclaw_agent
PYTHONPATH=src python -m new_agent.tui
```

使用 mcp_control 工具：

```bash
PYTHONPATH=src python -m new_agent.tui --mcp-control-tools
```

快速 mock demo：

```bash
python run_demo.py
```

## 15. 整体总结

这个 Agent 的实现重点是把机器人任务拆成可靠、可验证的工具调用闭环：

```text
LLM 负责决策
ToolRegistry 负责工具调度
真实工具负责机器人执行
MemoryManager 负责上下文和状态记忆
SenseEnvironment 负责行动后验证
FinalizeTask 负责显式任务结束
Trajectory 负责复盘和调试
```

因此，它更像一个机器人任务执行的决策层和编排层，而不是运动控制层。真正的控制细节由 shisheng 或 mcp_control 后端提供，Agent 通过统一工具契约接入这些后端。
