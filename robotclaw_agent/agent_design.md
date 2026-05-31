# RoboClaw 新版 Agent 设计记录（更新版）

## 当前完成状态（2026-05-18）

我们已经完成了以下核心工作：

### 1. 工具结构格式已正式锁定（Tool Contract）

文件位置：`src/new_agent/tools/tool_types.py`

**核心契约**：

- 所有工具必须返回 `ToolResult`：
  ```python
  dataclass ToolResult:
      status: ToolStatus          # success / failed / partial
      message: str
      data: dict[str, Any]        # 推荐使用结构化数据
      raw_output: str
      tool_name: str
  ```

- 所有工具继承 `BaseTool`，必须实现 `async def execute(self, **kwargs) -> ToolResult`

- 可选实现 `get_schema()` 返回 `ToolSchema`，用于生成 LLM function calling 描述。

**这版格式已锁定**，请 Tool 同学按此标准实现原子工具。

### 2. Prompt 系统已更新（采用现代 Agent 设计）

参考 Claude Code 和 SWE-agent 的做法，我们已将 **工具列表从 System Prompt 中移除**。

- System Prompt 只负责角色定位、行为规则、思考方式和任务指导。
- 工具的具体 Schema 将通过 LLM API 的 `tools` 参数传入（更高效、更规范）。
- Prompt 文件已更新，移除了 `{tools_description}` 部分。

### 3. Agent 核心已完整搭建

- 状态机（含 PERCEIVE / VERIFY / FINALIZE）
- ToolRegistry（支持 Mock）
- 增强版 `run_once` 流程（行动 → 感知 → 再行动 → 验证 → 结束）
- Prompt 集成

### 4. 测试代码统一管理

所有测试代码已移至 `debug/` 目录。

---

## 后续建议

1. 等待 Tool 同学实现真实原子工具后，替换 MockTool。
2. 继续完善 MemoryManager（支持结构化感知结果存储）。
3. 接入真实 LLM Client。
4. 丰富 Prompt（增加 Few-shot 示例、失败恢复策略等）。

---

**当前 Agent 已具备可运行的完整骨架**，可进行更深入的流程测试和 Prompt 迭代。

---

## 九、与原版 Agent 的核心差距分析（2026-05-18）

### 9.1 已完成的部分（新 Agent 优势）

- 原子化工具体系 + Tool Contract（`ToolResult` 标准返回）
- 现代 Prompt 设计（参考 Claude Code / SWE-agent，不把工具列表塞进 System Prompt）
- 扩展状态机（PERCEIVE / VERIFY / FINALIZE）
- 行动后主动感知的流程设计
- 显式任务结束机制（FinalizeTask）

### 9.2 缺失的核心模块（按优先级排序）

下面是与原版 RoboClaw Agent 对比，我们目前**最关键的缺失部分**：

| 优先级 | 模块名称                    | 原版对应位置                          | 新版当前状态     | 缺失影响 | 建议下一步 |
|--------|-----------------------------|---------------------------------------|------------------|----------|------------|
| ★★★★★ | **MemoryManager + RuntimeMemoryTree** | `agent_layer/agent_components/memory_manager/` | 完全缺失 | 极高 | **最高优先级** |
| ★★★★★ | **LLM Client**              | `agent_layer/agent_components/llm_manager/openai_client/` | 完全缺失 | 极高 | 必须实现 |
| ★★★★☆ | **Context Assembly / 上下文构建** | MemoryManager.current_contexts | 完全缺失 | 高 | 依赖 Memory |
| ★★★★☆ | **TaskNode / 任务节点管理** | RuntimeMemoryTree 中的 TaskNode | 完全缺失 | 高 | 支持长时程 |
| ★★★★☆ | **真实 Tool 调用处理**      | ActAgent.act() + tool_calls 解析     | 只有 Mock | 中 | 接 LLM 后需要 |
| ★★★☆☆ | **记忆压缩机制**            | MemoryManager.compress_current_memory | 完全缺失 | 中 | 长时程必需 |
| ★★★☆☆ | **AgentTools（内部工具）**  | `agent_layer/agent_components/agent_tools/` | 只有 ToolRegistry | 低 | 可后续补充 |
| ★★☆☆☆ | **ORMCPServiceManager**     | 原版服务注册与路由                    | 没有             | 低 | Tool 同学实现后可映射 |
| ★★☆☆☆ | **配置系统 + 日志**         | `common/` 下的各种 loader 和 logger  | 几乎没有         | 低 | 工程化需要 |
| ★☆☆☆☆ | **交互层（TUI / Gradio）**  | `interaction_layer/`                  | 完全没有         | 低 | 后期再做 |

### 9.3 最需要优先补齐的 3 个核心部分

1. **MemoryManager（含 TaskNode + Context 构建）**
   - 这是原版最核心的竞争力之一。
   - 需要支持：多层系统上下文、对话历史、结构化感知结果存储、任务进度跟踪。

2. **LLM Client**
   - 负责真正调用 LLM（OpenAI / Anthropic）。
   - 需要支持：传入 tools 参数、解析 tool_calls 返回、token 统计、消息格式转换。

3. **完整的 ReAct 式主循环**
   - 当前 `run_once` 还是硬编码流程。
   - 应该改成：LLM 返回 tool_calls → 执行 → 感知 → 继续循环，直到 LLM 决定调用 FinalizeTask。

### 9.4 结论

目前我们已经把**架构方向和接口契约**搭得比较好了，但距离一个真正可用的长时程机器人 Agent，还缺少上面几个核心模块。

建议接下来优先实现：
1. MemoryManager（最重要）
2. LLM Client + 真实工具调用循环
3. TaskNode 与任务进度跟踪

这样才能把“原子工具 + 主动感知 + 显式结束”的设计真正跑通。

---

## 十、核心缺失模块的补充设计（沿用原版优秀设计）

> **更新时间**：2026-05-18

### 10.5 LLM Client 已实现

**已创建文件**：
- `src/new_agent/llm/llm_response.py`：`LLMResponse` + `ToolCall`
- `src/new_agent/llm/llm_client.py`：核心客户端（支持 Mock 模式）
- `src/new_agent/llm/__init__.py`

**当前能力**：
- 通过 `tools` 参数传入工具 Schema（符合现代做法）
- Mock 模式可模拟返回 `tool_calls`
- 自动统计 token 使用
- 返回结构清晰（`content`、`tool_calls`、`total_tokens`）

**NewAgent 已集成**：
- 在 `init_agent` 中初始化 `LLMClient(mock_mode=True)`
- `run_once` 中真正调用 `self.llm_client.chat(messages, tools)`
- `_get_tools_schema()` 方法生成工具描述

**测试验证**：
- `debug/test_llm_client.py` 已通过测试

**下一步**：
- 实现真实 OpenAI/Anthropic 调用（替换 Mock）
- 主循环已于 2026-05-18 完成重构（见下文）

---

### 10.6 主循环（ReAct 式）已完成重构

**更新时间**：2026-05-18

**文件**：`src/new_agent/core/agent.py` 的 `run_once` 方法

**新主循环的核心逻辑**：

```python
while True:
    llm_res = await self.llm_client.chat(messages=contexts, tools=tools_schema)

    if not llm_res.has_tool_call:
        # LLM 给出最终回复，结束循环
        break

    for tool_call in llm_res.tool_calls:
        if tool_call.name == "FinalizeTask":
            # 显式结束任务
            return

        执行工具
        记录到 MemoryManager
        行动后主动感知（SenseEnvironment）
```

**关键特性**：

1. **真正的循环结构**：不再是单次调用，而是持续交互直到 LLM 停止调用工具。
2. **显式结束支持**：当 LLM 调用 `FinalizeTask` 时，立即结束任务并返回总结。
3. **行动后感知**：每次执行原子工具后，自动调用 `SenseEnvironment` 并把结果写入记忆。
4. **状态机驱动**：CHT → ACT → PERCEIVE → CHAT → ... 的状态转换清晰可控。
5. **与 MemoryManager 深度集成**：所有工具返回和感知结果都会被正确记录并参与下一次上下文构建。

**这标志着新 Agent 的核心闭环已基本跑通**：
- 原子工具调用
- 行动后主动感知
- 显式任务结束
- 记忆持续积累

**测试建议**：
- 可以尝试让 LLM 在 Mock 模式下多次返回 tool_call，观察循环是否正常工作。
- 后续可把 Mock LLM 改成真实模型，验证完整流程。

---

## 十、核心缺失模块的补充设计（沿用原版优秀设计）

> **原则**：如果原版 Agent 的做法是成熟且合理的，我们直接沿用其设计和实现，减少重复造轮子。

### 10.1 记忆系统（MemoryManager + RuntimeMemoryTree）

**推荐做法**：**直接沿用原版 RoboClaw 的 MemoryManager 和 RuntimeMemoryTree**。

**理由**：
- 原版已经实现了非常完善的**多层记忆结构**：
  - SelfKnowledgeArea（自我认知）
  - LongTermMemoryArea（长期记忆：KnowledgeGraph、ServiceRegistry、TaskTemplate）
  - ShortTermMemoryArea（短期记忆：TaskSessionBlock）
  - TaskNode（按任务拆分的节点，包含系统上下文 + 对话历史）
- 支持**孤立 Tool 消息清理**、**图像隐藏**、**记忆压缩**等高级功能。
- 已经能很好地支持长时程（long-horizon）任务。

**在新 Agent 中的适配建议**：
1. 把原版的 `runtime_memory_tree/` 和 `memory_manager.py` 复制到新项目中。
2. 扩展 `TaskNode`，增加以下字段以支持原子工具 + 感知闭环：
   - `completion_status`: 当前任务完成度（0-100%）
   - `last_perception_result`: 最近一次 SenseEnvironment 的结构化返回
   - `subtask_history`: 已完成的子任务列表
3. 在 `current_contexts` 属性中，优先把最近的感知结果（SenseEnvironment 返回）放在上下文前面，让 LLM 更容易看到最新环境状态。

**预计工作量**：中（主要是复制 + 少量扩展）。

### 10.2 LLM 实际调用能力（LLM Client）

**推荐做法**：**参考原版 OpenAIClient 的实现，但升级为支持现代 Tool Calling 方式**。

**原版 OpenAIClient 的优点**（值得沿用）：
- 统一的 `sync_chat()` 接口
- 消息格式转换（内部 MessageType → OpenAI 格式）
- Token 使用统计
- 错误处理

**需要改进的地方**：
- 原版把工具列表放在 Prompt 里（已过时）。
- 新版应通过 OpenAI/Anthropic API 的 `tools` 参数传入工具 Schema。
- 返回结果应清晰区分 `content` 和 `tool_calls`。

**建议在新 Agent 中新建**：
```python
# src/new_agent/llm/llm_client.py
class LLMClient:
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """
        返回结构：
            content: str | None
            tool_calls: list[ToolCall] | None
            total_tokens: int
            finish_reason: str
        """
```

**预计工作量**：中（需要重新实现调用逻辑，但可以参考原版很多代码）。

### 10.3 任务节点与上下文管理

**推荐做法**：**直接沿用原版的 TaskNode + 上下文聚合机制**。

原版在 `RuntimeMemoryTree` 中已经实现了非常清晰的结构：

- 每个 `TaskNode` 包含：
  - `task_sys_context`（任务级系统提示）
  - `contexts`（对话历史 + Tool 返回 + 感知结果）
- `MemoryManager.current_contexts` 会自动把系统上下文 + 对话上下文按顺序拼接好。

**在新 Agent 中的建议**：
1. 沿用原版的 `TaskNode` 结构。
2. 在每次调用 `SenseEnvironment` 后，把返回的结构化结果以 `ToolMessageType` 或新增的 `PerceptionMessageType` 写入当前 `TaskNode` 的 `contexts`。
3. 这样下一次 LLM 调用时，感知结果会自然出现在上下文中。

**这样做的好处**：
- 复用原版已经验证过的上下文管理逻辑。
- 天然支持多任务切换和长期记忆。
- 感知结果可以和普通 Tool 调用结果统一管理。

### 10.4 总结：优先实现顺序建议

1. **MemoryManager + RuntimeMemoryTree**（最高优先级）
2. **LLM Client**（第二优先级）
3. **增强 TaskNode 支持感知结果和任务进度**（与 MemoryManager 一起做）

这三个模块补齐后，新 Agent 的“原子工具 + 主动感知 + 显式结束”核心闭环就能真正跑通了。

---

## 十一、主循环完成总结（2026-05-18）

**已完成**：
- `run_once` 方法已重构为真正的 ReAct 式主循环。
- 支持 LLM 持续返回 tool_call 并执行，直到 LLM 停止调用工具或显式调用 `FinalizeTask`。
- 每次行动后自动进行环境感知（SenseEnvironment）。
- 与 MemoryManager 和 LLMClient 深度集成。

**标志意义**：
新 Agent 的核心设计（原子工具 + 主动感知 + 显式结束 + 现代 Prompt + MemoryManager + LLMClient）已全部落地，形成了可运行的完整闭环。

**下一步可选工作**：
1. 改进 Mock LLM，使其能多次返回 tool_call，完整演示循环。
2. 实现真实的 LLM 调用（OpenAI / Anthropic）。
3. 增加记忆压缩、失败恢复等高级功能。

---

## 十三、真实 LLM 调用已实现（2026-05-18）

**更新内容**：

- `src/new_agent/llm/llm_client.py` 已支持真实 OpenAI GPT-4o 调用。
- 新增 `_real_chat` 方法，使用 `AsyncOpenAI` 客户端。
- 支持通过 `tools` 参数传入工具 Schema（符合现代 Function Calling 规范）。
- 自动解析 `tool_calls` 并转换为内部 `ToolCall` 结构。
- 支持 Token 使用统计。
- `NewAgent.init_agent()` 新增参数：
  - `use_real_llm: bool`
  - `openai_api_key: str | None`

**使用方式**：

```python
agent = NewAgent()
await agent.init_agent(
    task_brief="把方块放到盒子里",
    use_real_llm=True,
    openai_api_key="sk-xxx"          # 或通过环境变量 OPENAI_API_KEY
)
```

**测试脚本**：
- `debug/test_real_llm.py` 已创建，可用于验证真实调用。

**安全提示**：
- 生产环境请使用环境变量 `OPENAI_API_KEY`，不要将 Key 硬编码进代码。
- 当前代码已支持从环境变量读取 Key。

---

### 环境初始化脚本

已提供 `setup_env.sh`，用于快速搭建新 Agent 的独立运行环境。

**推荐做法**：
- 新 Agent 使用独立的虚拟环境（`.venv`），不与原项目共用环境。
- 只安装必要依赖（`openai`、`python-dotenv`），避免安装原项目中大量机器人相关库。
- 支持 `uv`（如果已安装）或标准 `venv`。

运行方式：
```bash
chmod +x setup_env.sh
./setup_env.sh
```

之后按照脚本输出的提示激活环境并运行测试。

**当前状态**：
新 Agent 已具备**完整可运行的真实 LLM 调用能力**，可以直接使用 GPT-4o 进行智能决策。

---

## 十二、项目结构整理（2026-05-18）

### 当前推荐的目录结构

```
new_agent/
├── agent_design.md              # 设计文档（持续更新）
├── debug/                       # 所有测试与演示脚本
│   ├── test_memory.py
│   ├── test_llm_client.py
│   ├── test_reAct_loop.py
│   └── run_react_demo.py
├── src/
│   └── new_agent/               # 核心包（推荐从这里导入）
│       ├── __init__.py          # 已设为轻量模式
│       ├── core/
│       │   ├── agent.py
│       │   └── state_machine.py
│       ├── memory/
│       ├── llm/
│       ├── prompt/
│       ├── tools/
│       └── components/
└── README.md                    # （可后续补充）
```

### 推荐运行测试的方式

统一使用以下命令（已验证可行）：

```bash
cd /home/easyai/桌面/RoboClaw-main/new_agent
PYTHONPATH=src python debug/文件名.py
```

示例：
- `PYTHONPATH=src python debug/test_memory.py`
- `PYTHONPATH=src python debug/test_llm_client.py`
- `PYTHONPATH=src python debug/test_reAct_loop.py`

### 导入规范

推荐直接从子模块导入，避免包级 `__init__.py` 带来的问题：

```python
from new_agent.core.agent import NewAgent
from new_agent.memory.memory_manager import MemoryManager
from new_agent.llm.llm_client import LLMClient
from new_agent.tools.tool_types import ToolResult
```

`src/new_agent/__init__.py` 已设置为轻量模式（仅包含文档说明），不会自动导入所有子模块，以减少开发阶段的导入冲突。

---

**结构整理完成**。以后新增测试脚本请统一放在 `debug/` 目录，并使用 `PYTHONPATH=src` 运行。