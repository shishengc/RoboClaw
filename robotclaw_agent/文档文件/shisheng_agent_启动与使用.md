# shisheng 仿真环境与 NewAgent 使用记录

本文记录当前 `shisheng` 仿真环境启动方式，以及 `robotclaw_agent` 调用真实 shisheng agent tools 的常用命令。

## 1. 重启仿真环境

建议开两个终端。

### 终端 1：启动后端服务

```bash
cd /home/easyai/桌面/shisheng
bash scripts/01_before_app.sh
```

这个脚本会启动：

- manual policy server
- gRPC robot server
- 临时 bridge/gRPC 运行文件

### 终端 2：启动 GenieSim app

```bash
cd /home/easyai/桌面/shisheng
bash start_abs_pose_sim.sh
```

这个命令会占用当前终端。等仿真启动完成，并进入等待 bridge start command 的状态后，回到终端 1。

### 终端 1：发送 bridge start

```bash
cd /home/easyai/桌面/shisheng
bash scripts/02_after_app_init.sh
```

### 检查状态

```bash
cd /home/easyai/桌面/shisheng
bash scripts/03_run_grpc_command.sh status
```

如果看到类似下面字段，说明环境可用：

```text
"model_arc": "abs_pose"
"phase": "running"
```

## 2. 清理后重启

如果环境状态异常、bridge 卡住、或者容器里有多组 app 进程，先清理：

```bash
cd /home/easyai/桌面/shisheng
bash scripts/grpc_red_block.sh cleanup
```

然后重新执行：

```bash
bash scripts/01_before_app.sh
bash start_abs_pose_sim.sh
bash scripts/02_after_app_init.sh
```

注意：`start_abs_pose_sim.sh` 需要单独终端运行。

## 3. 直接运行基于规则的 shisheng 任务

红块放到黄块上：

```bash
cd /home/easyai/桌面/shisheng
SOURCE_COLOR=red TARGET_COLOR=yellow bash run_abs_pose_red_block.sh
```

完整排序序列：

```bash
cd /home/easyai/桌面/shisheng
bash run_sorting_sequence.sh
```

单个方块放到排序区：

```bash
cd /home/easyai/桌面/shisheng
bash sort_one_block.sh green 1
```

## 4. 通过 NewAgent 固定工具链 runner 跑 full_task_1

这个方式会通过 `NewAgent.tool_registry` 调用真实 shisheng tools，但工具顺序是代码固定的，不调用 LLM/API。

```bash
cd /home/easyai/桌面/robotclaw_agent
PYTHONPATH=src python3 -m new_agent.cli \
  --run-full-task-1 \
  --shisheng-root /home/easyai/桌面/shisheng \
  --source-color red \
  --target-color yellow \
  --log-level INFO
```

这条命令不需要 `OPENAI_API_KEY`。

它执行的主要工具链是：

```text
EnsureAbsPoseRunning
LocalizeTarget
ApproachTarget
AlignTarget
CheckGraspReady
GraspAtCurrent
MoveEndEffectorToWorld
PlaceHeldObject
ReturnToDefaultAbsPose
SenseEnvironment
```

执行完成后会保存轨迹到：

```text
/home/easyai/桌面/robotclaw_agent/trajectories/
```

## 5. 使用 NewAgent TUI

TUI 方式是自然语言入口。你输入任务，agent 通过 LLM 自己选择工具和参数。

启动：

```bash
cd /home/easyai/桌面/robotclaw_agent
export OPENAI_API_KEY="你的 API key"

PYTHONPATH=src python3 -m new_agent.tui \
  --shisheng-root /home/easyai/桌面/shisheng \
  --log-level INFO
```

进入后可以输入：

```text
把红色方块放到黄色方块上
把红色方块放到黄色方块上，然后松开手保证红色方块被平稳放在黄方块上    
```

TUI 常用命令：

```text
/tools
/status
/trajectory
/quit
```

说明：

- `new_agent.tui` 默认需要真实 LLM，所以需要 API key。
- `new_agent.tui --mock-llm` 只适合检查 TUI 是否能启动，不适合真实任务规划。
- 如果不想用 API，但想用固定链路执行任务，用上一节的 `--run-full-task-1`。

## 6. 常用排查命令

查看 shisheng bridge 状态：

```bash
cd /home/easyai/桌面/shisheng
bash scripts/03_run_grpc_command.sh status
```

抓取当前相机图像到 `outputs/captures`：

```bash
cd /home/easyai/桌面/shisheng
bash scripts/03_run_grpc_command.sh cameras
```

测试目标定位：

```bash
cd /home/easyai/桌面/shisheng
bash scripts/03_run_grpc_command.sh localize-red --camera right
```

查看 Docker 容器是否存在：

```bash
docker ps --format '{{.Names}} {{.Status}}'
```

查看容器里的 GenieSim/gRPC 进程：

```bash
docker exec genie_sim_benchmark bash -lc \
  "pgrep -af 'source/geniesim/app/app.py|geniesim --config|grpc_robot_server.py' || true"
```

## 7. 固定 runner 与 TUI 的区别

| 方式 | 是否需要 API | 是否调用真实工具 | 是否自主规划 |
|---|---:|---:|---:|
| `shisheng/run_abs_pose_red_block.sh` | 否 | 是 | 否，规则链路 |
| `new_agent.cli --run-full-task-1` | 否 | 是 | 否，固定工具链 |
| `new_agent.tui --mock-llm` | 否 | 可注册真实工具 | 基本不可用于真实规划 |
| `new_agent.tui` | 是 | 是 | 是，由 LLM 选择工具 |

