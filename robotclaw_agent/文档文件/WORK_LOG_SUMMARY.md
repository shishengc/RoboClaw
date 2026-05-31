# New Agent 工作日志总结

## 精简版

本阶段我们围绕 `new_agent` 从 0 搭建了一个面向机器人协作任务的新 Agent 原型。整体目标不是简单复刻旧代码，而是重新设计一个更清晰、更容易扩展、更适合接入真实机器人原子工具的 Agent 框架。

当前新版 Agent 已经完成了以下核心建设：

- 搭建了 Agent 主体结构，包括状态机、ReAct 主循环、LLM Client、MemoryManager、ToolRegistry、Prompt 系统和 CLI 入口。
- 定义了统一工具契约 `BaseTool / ToolResult / ToolSchema`，为后续真实机器人原子工具接入提供标准接口。
- 按 OpenAI tool calling 格式重构了消息链路，保证 `assistant.tool_calls -> tool result` 的上下文顺序合法。
- 将 system prompt 真正接入运行上下文，并补充了工作流、校验反馈和错误恢复规则。
- 建立了行动后感知机制，每次动作后通过 `SenseEnvironment` 更新环境状态。
- 定义了结构化感知结果格式，包括 `objects / robot_state / gripper_state / task_progress / safety_status / uncertainties / raw`。
- 增加了基础失败处理能力，包括工具 timeout、失败重试、失败记录、失败后感知、Finalize 阻断。
- 增加了轻量任务计划和任务状态跟踪，用于记录执行进度、失败历史和当前任务状态。
- 引入配置文件，支持模型、最大步数、LLM timeout、工具 timeout、重试次数、轨迹目录等参数配置。
- 增加了执行轨迹记录和落盘能力，便于调试、复盘和后续测试。
- 实现了简单交互式 CLI，支持开发者在终端里直接输入任务并查看状态、工具列表和轨迹文件。

本阶段主要解决的问题：

- Agent 从固定 demo 脚本升级为可交互、可配置、可追踪的运行框架。
- 修复了 tool calling 上下文格式不完整的问题。
- 修复了 system prompt 写了但实际未进入 LLM 上下文的问题。
- 补齐了失败处理、验证反馈和 Finalize 前检查的基础机制。
- 为后续真实工具、真实感知和机器人安全层接入预留了清晰接口。

后续重点工作：

- 接入真实机器人原子工具。
- 完善真实 `SenseEnvironment` 感知结果。
- 在工具层/机器人控制层实现安全执行机制。
- 增强任务级验证逻辑，例如确认物体是否真的到达目标位置。
- 将轻量固定计划升级为更智能的任务规划和验证流程。
- 使用真实 LLM 跑完整 tool calling 链路。
- 在工具链路稳定后补充系统化测试。

## 详细版

### 1. 背景与目标

`new_agent` 是一个新的 Agent 代码仓库，可以视为从 0 开始搭建的机器人协作 Agent 原型。它的目标不是只写一个能跑通 demo 的脚本，而是建立一套后续可以持续扩展的 Agent 基础设施。

我们在设计时重点考虑了几个问题：

1. Agent 如何与 LLM 协作决策。
2. Agent 如何通过原子工具控制机器人。
3. Agent 如何在每次动作后观察环境。
4. Agent 如何判断任务是否真的完成。
5. Agent 如何处理工具失败、感知不确定和执行异常。
6. Agent 如何记录完整执行过程，方便调试和复盘。
7. 后续真实工具、真实感知和安全层如何接入。

因此，本阶段开发重点放在 Agent 的骨架、协议、状态管理、工具接口、感知结构、错误恢复、Prompt 和交互入口上。

### 2. 整体架构搭建

我们搭建了新版 Agent 的基础目录和核心模块：

```text
new_agent/
├── config/
│   └── agent_config.json
├── debug/
├── src/new_agent/
│   ├── components/
│   │   └── tool_registry.py
│   ├── core/
│   │   ├── agent.py
│   │   ├── config.py
│   │   └── state_machine.py
│   ├── llm/
│   │   ├── llm_client.py
│   │   └── llm_response.py
│   ├── memory/
│   │   ├── memory_manager.py
│   │   ├── message_types.py
│   │   ├── perception.py
│   │   └── task_node.py
│   ├── prompt/
│   │   ├── prompt_builder.py
│   │   └── system_prompt.py
│   ├── tools/
│   │   └── tool_types.py
│   └── cli.py
├── run_cli.py
└── run_demo.py
```

这些模块分别承担以下职责：

- `agent.py`：Agent 主类，负责 ReAct 主循环、工具调用、感知、失败处理、Finalize 和轨迹保存。
- `state_machine.py`：维护 Agent 当前状态，例如 `READY / CHAT / ACT / PERCEIVE / FINALIZE`。
- `tool_registry.py`：管理工具注册、工具执行和 OpenAI tools schema 输出。
- `tool_types.py`：定义统一工具契约。
- `llm_client.py`：封装 mock LLM 和真实 OpenAI 调用。
- `memory_manager.py / task_node.py`：管理任务上下文、消息历史、任务计划、失败历史和感知结果。
- `perception.py`：定义结构化感知结果。
- `system_prompt.py`：定义 Agent 的行为规则、工作流和错误恢复策略。
- `cli.py`：提供交互式终端入口。

### 3. Agent 主循环设计

新版 Agent 采用 ReAct 风格主循环：

1. 读取当前任务上下文和记忆。
2. 将 system prompt、历史消息和工具 schema 发给 LLM。
3. 如果 LLM 返回 tool calls，则执行工具。
4. 将工具结果写入 memory。
5. 对动作类工具执行后主动感知环境。
6. 根据工具结果和感知结果继续下一轮决策。
7. 只有当 LLM 调用 `FinalizeTask` 且验证通过时，任务才算结束。

这个设计解决了一个关键问题：机器人任务不能只靠 LLM 说“完成了”就结束，必须通过工具反馈和环境感知来闭环确认。

### 4. 工具契约设计

我们定义了统一的工具接口：

- `BaseTool`
- `ToolResult`
- `ToolSchema`
- `ToolStatus`

工具执行结果统一返回：

```python
ToolResult(
    status=ToolStatus.SUCCESS | ToolStatus.FAILED | ToolStatus.PARTIAL,
    message="...",
    data={...},
    raw_output="...",
    tool_name="..."
)
```

这样做的目的：

- 避免不同工具返回格式混乱。
- 让 Agent 可以统一判断成功、失败、部分成功。
- 让失败恢复逻辑可以基于标准字段工作。
- 让后续真实机器人工具只需按同一接口接入。

当前真实工具还没有实现，因此我们保留了 mock 工具：

- `LocateObject`
- `GraspObject`
- `PlaceObject`
- `SenseEnvironment`
- `FinalizeTask`

同时，`ToolRegistry` 已支持后续通过 `register(tool)` 注册真实 `BaseTool` 实例。

### 5. OpenAI Tool Calling 消息格式修正

我们重构了 Agent 的消息存储方式，使其符合 OpenAI tool calling 的上下文要求。

正确链路应该是：

```text
assistant: tool_calls
tool: result for tool_call_id
assistant: next decision
```

之前的问题是：Agent 只记录了 `tool` 消息，没有记录对应的 `assistant.tool_calls`，真实 OpenAI 多轮 tool calling 时可能出现上下文不合法。

现在已经解决：

- Memory 中会保存 assistant 的 `tool_calls`。
- 每个 tool result 都带 `tool_call_id`。
- `FinalizeTask` 也会先写入对应 tool result，再结束任务。

这为后续真实 LLM 调用打下了协议基础。

### 6. System Prompt 设计

最初 system prompt 已经有角色定位和基本规则，但实际主循环没有真正使用它。我们修复了这一点，让正式 system prompt 进入每轮 LLM 上下文。

随后又补充了三类规则：

#### 6.1 Workflow

要求模型按以下方式工作：

- 理解用户任务。
- 维护短任务计划。
- 根据最新工具结果和感知结果选择下一步。
- 每次动作后检查反馈。
- 只有确认目标达成且安全时才调用 `FinalizeTask`。

#### 6.2 Validation and Feedback

要求模型把工具输出和感知输出当作环境反馈，并关注：

- `objects`
- `robot_state`
- `gripper_state`
- `task_progress`
- `safety_status`
- `uncertainties`

如果任务进度未知、感知不确定或安全状态异常，不能直接结束任务。

#### 6.3 Error Recovery

针对不同错误类型给出恢复方向：

- 定位失败：重新观察或使用更具体的对象描述。
- 抓取失败：检查物体位姿、夹爪状态和物体是否移动。
- 放置失败：检查目标位置、持物状态和空间是否可用。
- 感知不确定：继续感知，不盲目执行。
- 多次失败：停止盲目重试，并报告任务未确认完成。

这样 Prompt 侧和代码侧的错误恢复机制保持一致。

### 7. 结构化感知设计

机器人任务高度依赖环境反馈，因此我们定义了标准感知结构：

```python
{
    "objects": [],
    "robot_state": {},
    "gripper_state": {},
    "task_progress": {
        "overall_completion": None,
        "status": "unknown",
        "completed_subtasks": [],
        "pending_subtasks": []
    },
    "safety_status": {
        "is_safe": True,
        "issues": []
    },
    "uncertainties": [],
    "raw": ...
}
```

这个设计解决的问题：

- Agent 不再依赖任意格式的感知返回。
- Finalize 验证可以读取统一字段。
- 后续真实 `SenseEnvironment` 可以直接对齐这个 schema。
- 安全层虽然暂时不在 Agent 内实现，但可以通过 `safety_status` 接入。

当前 mock 感知也会被归一化为这个结构。

### 8. 任务计划和任务状态跟踪

为了让 Agent 不只是“LLM 连续调用工具”，我们增加了轻量任务计划：

```text
1. locate relevant objects and confirm the scene state
2. grasp or manipulate the target object with an atomic action
3. place or move the object to the requested target
4. verify the environment state with perception
5. finalize only after the result is verified
```

`TaskNode` 现在会记录：

- `task_plan`
- `completion_status`
- `last_perception_result`
- `subtask_history`
- `failure_history`

工具执行后，Agent 会更新对应 plan step 的状态。

这解决的问题是：Agent 不再完全依赖隐式对话历史，而是有一个最小任务状态模型，可以用于进度摘要、失败记录和后续恢复。

### 9. 失败处理与恢复机制

我们加入了基础失败处理策略。

当前支持：

- 工具调用 timeout。
- 工具失败重试。
- 捕获工具异常。
- 记录失败历史。
- 失败后触发 `SenseEnvironment`。
- 失败后不允许直接当作成功继续。
- 如果存在未解决失败，`FinalizeTask` 会被阻断。
- 如果模型停止调用工具但仍有失败，Agent 会返回“任务未确认完成”。

这一部分解决了机器人 Agent 中非常关键的问题：真实机器人动作不一定成功，Agent 必须能识别失败并进入恢复或中止流程，而不是继续生成看似完成的回复。

### 10. Finalize 验证设计

`FinalizeTask` 不再只是一个普通结束信号。现在它会经过验证：

- 必须存在最近一次感知结果。
- `safety_status.is_safe` 不能为 false。
- 如果感知提供了 `task_progress.overall_completion`，必须达到配置阈值。
- 如果存在失败历史，但最近感知没有明确完成度，则阻止 Finalize。

这样解决了一个常见风险：LLM 可能过早宣布任务完成。现在 Agent 会通过感知和失败历史进行基本拦截。

### 11. 配置系统

新增配置文件：

```text
config/agent_config.json
```

当前支持：

```json
{
  "model": "gpt-4o",
  "max_steps": 12,
  "max_tool_retries": 1,
  "llm_timeout_seconds": 60.0,
  "tool_timeout_seconds": 30.0,
  "finalize_min_completion": 0.9,
  "trajectory_dir": "trajectories"
}
```

配置化解决的问题：

- 避免关键运行参数硬编码。
- 后续可以按环境调整模型、timeout、最大步数和重试次数。
- CLI 和 Agent 初始化都可以传入配置文件。

### 12. 轨迹记录和复盘能力

Agent 现在会记录完整执行轨迹，包括：

- 用户输入。
- LLM tool calls。
- 工具参数和结果。
- 感知结果。
- 失败记录。
- Finalize 阻断或成功结束。
- 当前状态和时间戳。

每次运行后会保存 JSON 文件到：

```text
new_agent/trajectories/
```

这解决了调试和复盘问题。后续如果真实机器人执行异常，可以通过轨迹文件定位是 LLM 决策问题、工具执行问题、感知问题还是验证逻辑问题。

### 13. CLI 交互入口

新增了简单终端交互界面：

```bash
cd /home/easyai/桌面/RoboClaw-main/new_agent
source .venv/bin/activate
python run_cli.py
```

支持参数：

```bash
python run_cli.py --real-llm
python run_cli.py --config config/agent_config.json
python run_cli.py --agent-name MyAgent
```

支持命令：

```text
/help
/status
/tools
/trajectory
/quit
```

CLI 解决的问题：

- 开发者不再只能运行固定 demo。
- 可以在终端中输入不同任务进行调试。
- 可以查看当前状态、工具列表和最近轨迹文件。
- 后续真实工具接入后，可以直接用 CLI 做联调。

### 14. 当前已验证内容

我们验证了 mock 链路：

```bash
source .venv/bin/activate && PYTHONPATH=src python debug/test_agent.py
```

结果：

- Agent 能初始化。
- mock 工具能注册。
- ReAct 主循环能执行。
- 行动后感知能触发。
- `FinalizeTask` 能完成任务。
- 轨迹文件能保存。

也做过失败路径 smoke test：

- 人为让 `GraspObject` 返回失败。
- Agent 能记录失败。
- Agent 能阻止 Finalize。
- Agent 最终返回任务未确认完成。

### 15. 当前仍未完成的部分

#### 15.1 真实工具接入

当前仍然是 mock 工具。后续工具同事需要实现真实工具：

```python
class RealTool(BaseTool):
    name = "..."
    description = "..."

    async def execute(self, **kwargs) -> ToolResult:
        ...

    def get_schema(self) -> ToolSchema:
        ...
```

然后通过：

```python
tool_registry.register(tool)
```

接入 Agent。

#### 15.2 机器人安全执行层

安全执行层暂时没有放在 Agent 里硬做。更合理的位置是在真实工具或机器人控制服务层。

后续需要实现：

- workspace 边界检查。
- 坐标系和单位校验。
- 速度、力、夹爪限制。
- 碰撞风险检测。
- 急停和取消接口。
- 危险状态下拒绝执行动作。

Agent 侧已经通过 `safety_status` 预留了接收安全反馈的接口。

#### 15.3 真实感知结果

后续 `SenseEnvironment` 需要真实返回标准结构，尤其是：

- 物体名称、位姿、可见性、置信度。
- 机器人当前状态。
- 夹爪是否持物。
- 任务完成度。
- 安全状态。
- 不确定性来源。

#### 15.4 更细的任务验证

现在 Finalize 只是做通用验证。后续应加入任务相关验证，例如：

- 红色方块是否真的在盒子里。
- 物体是否已释放。
- 目标区域是否正确。
- 机器人是否处于安全结束状态。

#### 15.5 更智能的规划器

当前任务计划是固定模板。后续可以升级为：

- LLM 先生成任务计划。
- 每个 step 包含 precondition 和 postcondition。
- 每步完成后由 verifier 判断是否达成。
- 失败后根据当前 step 选择恢复策略。

#### 15.6 真实 LLM 链路验证

虽然现在已经按 OpenAI tool calling 格式组织 messages，但仍需要用真实 API 跑完整链路，验证：

- tool schema 是否足够约束模型。
- tool result 消息是否完全兼容。
- timeout 是否生效。
- 失败恢复 prompt 是否能引导模型正确重试。
- Finalize 阻断后模型是否能继续修复。

#### 15.7 系统化测试

当前主要是 debug 脚本和 smoke test。后续工具链路稳定后，应补充：

- ToolRegistry schema 测试。
- OpenAI message 格式测试。
- 工具失败测试。
- Finalize 阻断测试。
- CLI smoke test。
- mock 端到端测试。
- 真实机器人集成测试。

## 总结

本阶段我们完成了新版 Agent 从 0 到可运行原型的搭建。它现在已经具备 Agent 的基本工作闭环：

```text
用户任务
  -> LLM 决策
  -> 原子工具调用
  -> 工具结果记录
  -> 环境感知
  -> 失败恢复/继续规划
  -> Finalize 验证
  -> 轨迹落盘
```

相比最初的固定 demo，当前版本已经具备更清晰的模块边界、更规范的工具协议、更可靠的上下文格式、更明确的错误恢复和更方便的交互调试入口。

下一阶段的核心工作应集中在真实机器人能力接入：

1. 实现真实原子工具。
2. 接入真实感知。
3. 在工具/控制层实现安全执行。
4. 完善任务级验证。
5. 用真实 LLM 和真实机器人链路进行端到端测试。

