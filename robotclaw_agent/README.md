# NewAgent - 机器人任务执行 Agent 框架

> **新一代机器人智能体框架**，专为 RoboClaw 项目设计，支持原子化工具调用、行动后主动感知和显式任务完成确认。

---

## 项目简介

NewAgent 是一个面向机器人任务的智能 Agent 框架，采用现代 Agent 设计理念（参考 Claude Code / SWE-agent），核心目标是：

- 让 Agent 专注于**决策与规划**，底层负责所有数值计算和控制细节
- 通过**原子化工具**实现可复用、可泛化的机器人操作
- 支持 **ReAct 式主循环**：思考 → 行动 → 感知 → 再思考，直到任务完成
- 提供 **Mock 模式** 和 **真实 LLM 调用**（OpenAI GPT-4o）两种运行方式

---

## 核心特性

| 特性 | 说明 |
|------|------|
| **原子化工具体系** | 所有工具遵循 `ToolResult` 标准返回格式，易于扩展和验证 |
| **行动后主动感知** | 每次执行工具后自动调用 `SenseEnvironment`，让 Agent 实时了解环境状态 |
| **显式任务结束** | 支持 `FinalizeTask` 工具，LLM 可主动宣布任务完成 |
| **现代 Prompt 设计** | 工具 Schema 通过 API `tools` 参数传入，不污染 System Prompt |
| **ReAct 主循环** | 真正的循环交互，直到 LLM 停止调用工具或显式结束 |
| **双模式运行** | Mock 模式（快速调试） + 真实 OpenAI GPT-4o 调用 |
| **记忆管理** | 集成 MemoryManager，支持任务节点和上下文构建 |

---

## 目录结构

```
new_agent/
├── README.md                    # 本文件
├── agent_design.md              # 详细设计文档与迭代记录
├── requirements.txt             # 核心依赖
├── setup_env.sh                 # 环境初始化脚本
├── run_demo.py                  # 一键演示入口
├── debug/                       # 测试与演示脚本
│   ├── run_react_demo.py        # ReAct 主循环演示
│   ├── test_real_llm.py         # 真实 LLM 调用测试
│   └── ...
└── src/
    └── new_agent/               # 核心源码
        ├── core/
        │   ├── agent.py         # NewAgent 主类
        │   └── state_machine.py # 状态机
        ├── llm/
        │   └── llm_client.py    # LLM 客户端（支持 Mock / 真实调用）
        ├── memory/
        │   └── memory_manager.py# 记忆管理
        ├── components/
        │   └── tool_registry.py # 工具注册与管理
        ├── prompt/
        │   └── system_prompt.py # 系统提示词构建
        └── tools/
            └── tool_types.py    # ToolResult / ToolStatus 定义
```

---

## 快速开始

### 1. 环境准备

推荐使用独立虚拟环境运行本框架：

```bash
cd new_agent

# 使用 setup_env.sh 快速初始化（推荐）
chmod +x setup_env.sh
./setup_env.sh

# 或手动创建虚拟环境
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 OpenAI API Key（可选）

如需使用真实 GPT-4o 调用，请设置环境变量：

```bash
export OPENAI_API_KEY="sk-xxx"
```

或在代码中传入 `openai_api_key` 参数。

### 3. 运行演示

```bash
# 最简单的方式（推荐）
python run_demo.py

# 或手动指定 PYTHONPATH
PYTHONPATH=src python debug/run_react_demo.py
```

演示会启动一个完整的 ReAct 主循环，模拟“把方块放到盒子里”任务。

### 4. 运行真实 CoRobot / mcp_control_demo 工具

先启动 CoRobot app：

```bash
cd /home/ck/RoboClaw
make run_corobot_app COROBOT_APP_ARGS="--config ./src/mcp_control_demo/config/rule_control_app_config.yaml"
```

再启动 TUI，让 LLM 根据自然语言任务选择工具：

```bash
cd /home/ck/RoboClaw/robotclaw_agent
export OPENAI_API_KEY="sk-xxx"
PYTHONPATH=src python3 -m new_agent.tui --mcp-control-tools
```

如果 CoRobot skill API 不在默认地址 `http://localhost:8765`，显式指定：

```bash
PYTHONPATH=src python3 -m new_agent.tui \
  --mcp-control-tools \
  --corobot-url http://localhost:8765
```

`--mcp-control-tools` 适合 AprilTag 任务，例如：

```text
把tag 0放到tag 1上，这是一个装配任务
把tag 3放到tag 4里面
```

不要用默认 shisheng backend 跑 tag 任务；不加 `--mcp-control-tools`
时，TUI 会注册 shisheng/GenieSim 方块操作工具。

当前 mcp_control 工具会暴露给 LLM，包括 `reset_robot`、
`detect_tags`、`get_apriltag_pose`、`resolve_tag_pick_place_recipe`、
`compute_tag_grasp_targets`、`open_gripper`、`move_eef`、
`close_gripper`、`place_down`、`compute_tag_place_targets`、
`SenseEnvironment`、`FinalizeTask`。

相关代码入口：

- `src/new_agent/tui.py`：解析 `--mcp-control-tools`，选择 mcp_control guidance，并初始化 agent。
- `src/new_agent/core/agent.py`：`init_agent(..., use_mcp_control_tools=True)` 注册工具，`run_once()` 执行 ReAct 循环。
- `src/new_agent/tools/mcp_control_tools.py`：把 LLM 可调用工具映射到 CoRobot `/skill/...` HTTP API。
- `/home/ck/RoboClaw/src/mcp_control_demo`：真实 mcp_control_demo 后端包，包含 AprilTag 感知、标定和 RuleControlTask 配置。

如果只想复现已测试通过的固定链路，可以运行极简脚本：

```bash
python run_mcp_control_tools.py --arm right --tag-id 0
python run_mcp_control_tools.py --arm right --tag-id 0 --execute
```

---

## 使用示例

### 基础用法（Mock 模式）

```python
import asyncio
from new_agent.core.agent import NewAgent

async def main():
    agent = NewAgent(agent_name="DemoAgent")

    await agent.init_agent(
        task_brief="把红色方块放到旁边的蓝色盒子里",
        action_guidance="使用原子工具逐步完成任务，每次行动后建议感知环境。"
    )

    # 运行主循环
    result = await agent.run_once("把红色方块放到盒子里")

    print("任务结果:", result)
    await agent.shutdown()

asyncio.run(main())
```

### 使用真实 LLM（GPT-4o）

```python
await agent.init_agent(
    task_brief="把方块放到盒子里",
    use_real_llm=True,
    openai_api_key="sk-xxx"   # 或通过环境变量 OPENAI_API_KEY
)
```

### 自定义工具注册

```python
from new_agent.components.tool_registry import ToolRegistry
from new_agent.tools.tool_types import ToolResult, ToolStatus

# 自定义工具示例
class LocateObjectTool:
    async def execute(self, target: str, camera: str = "head") -> ToolResult:
        # 实际实现：调用底层感知模块
        return ToolResult(
            status=ToolStatus.SUCCESS,
            message=f"已定位目标 {target}",
            data={"position": [0.5, 0.3, 0.2]},
            tool_name="LocateObject"
        )

# 注册工具
registry = ToolRegistry()
registry.register("LocateObject", LocateObjectTool())
```

---

## 原子动作工具（推荐）

本框架设计了一套面向 Agent 的原子动作工具，详见：

**`tool_need/agent_atomic_tools.md`**

这些工具让 Agent 只需输出“意图”和“策略”，底层负责所有数值计算和控制逻辑。

---

## 设计理念

1. **Agent 做决策，代码做计算**
   - Agent 不需要输出精确的关节角度或 Jacobian 矩阵
   - 所有数值计算、坐标转换、视觉伺服都封装在原子工具内部

2. **行动后主动感知**
   - 每次执行工具后自动调用 `SenseEnvironment`
   - 让 Agent 始终拥有最新的环境状态信息

3. **显式任务结束**
   - LLM 可主动调用 `FinalizeTask` 宣布任务完成
   - 避免无限循环，提高可控性

4. **现代工具调用规范**
   - 工具 Schema 通过 API `tools` 参数传入
   - 符合 OpenAI / Anthropic 官方 Function Calling 标准

---

## 相关文档

- `agent_design.md`：详细设计迭代记录与架构说明
- `tool_need/agent_atomic_tools.md`：推荐的原子动作工具列表

---

## 贡献与反馈

本项目仍在快速迭代中，欢迎提出改进建议或提交 PR。

---

**当前版本**：2026-05-19  
**维护者**：RoboClaw 新 Agent 开发组
