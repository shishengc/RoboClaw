# shishen-master 下游机器人控制能力梳理

> 目的：基于当前 `shishen-master` 代码仓库，说明下游已经实现了哪些能力、哪些能力是 Agent 联调刚需、哪些能力可以后续再补。本文不修改 `agent_atomic_tools.md` 原始工具设计，只用于阶段性对齐下游实现状态。

## 1. 当前结论

`shishen-master` 已经具备一条可用于简单 case 联调的基础机器人控制链路：

```text
启动 bridge / gRPC 服务
  -> 获取 observation
  -> reset robot
  -> 设置右臂关节 pose
  -> 检测红块
  -> 定位红块
  -> 执行若干红块抓取/对齐/搜索脚本
```

这意味着：即使下游还没有完整实现所有标准 Agent 工具，我们也已经可以先在简单任务上测试 NewAgent 的主循环、tool calling、失败恢复、感知写入和轨迹记录。

但当前代码还不能直接作为稳定 Agent Tool 后端使用。主要原因是：

- gRPC 正式接口很薄，只暴露了 3 个 RPC。
- 很多高层能力藏在 shell 脚本或专用 Python client 里。
- 返回格式不统一，有些是 stdout，有些是局部 JSON。
- 现有能力强绑定 red block，不是通用目标工具。
- 缺少统一 `success / failed / partial` 工具状态。
- 缺少 Agent 需要的统一结构化感知和任务验证结果。

因此，本阶段建议先做一个轻量 adapter，把当前已实现能力包装成 Agent 可调用的最小工具集。完整泛化、放置、任务验证和安全执行层可以后续逐步补齐。

## 2. 下游当前已经实现的功能

### 2.1 服务启动与桥接控制

相关文件：

```text
shishen-master/scripts/01_before_app.sh
shishen-master/scripts/02_after_app_init.sh
shishen-master/scripts/03_run_grpc_command.sh
shishen-master/scripts/grpc_red_block.sh
```

已实现能力：

- 启动 manual policy server。
- 启动临时 gRPC server。
- 向 GenieSim bridge 发送 start/stop/control 指令。
- 检查 bridge phase。
- 拷贝相机图片到 outputs。
- 清理临时进程和 bridge 控制文件。

对 Agent 的价值：

- 可以作为人工启动/联调入口。
- Agent 暂时不需要直接管理这些服务，但工具后端需要依赖它们已启动。

当前不足：

- 服务生命周期主要由 shell 脚本管理。
- Agent 侧无法通过稳定 API 查询“工具后端是否 ready”。
- 错误主要通过脚本退出码和 stdout/stderr 表达，不适合直接进入 Agent memory。

建议：

- 短期：继续人工启动服务，Agent 只调用工具。
- 后续：提供 `ReportStatus` 或 `BackendStatus` 工具，返回 bridge、gRPC、相机、geometry 是否 ready。

### 2.2 gRPC 基础控制

相关文件：

```text
shishen-master/ros/grpc/proto/robot_service.proto
shishen-master/ros/grpc/server/grpc_robot_server.py
shishen-master/ros/grpc/client/grpc_robot_client.py
```

当前 proto 已实现：

```proto
rpc GetObservation(ObservationRequest) returns (ObservationReply);
rpc ResetRobot(ResetRequest) returns (CommandReply);
rpc SetRightArmPose(SetRightArmPoseRequest) returns (CommandReply);
```

已实现能力：

- `GetObservation`
  - 返回 bridge state。
  - 返回 head/left/right 图像路径。
  - 返回 status json 路径和 status json 字符串。

- `ResetRobot`
  - 将双臂、头部、腰部、夹爪恢复到固定 reset 状态。
  - 写 manual action。
  - 发布 ROS joint command。

- `SetRightArmPose`
  - 接收 7 个右臂关节值。
  - 接收 gripper 值。
  - 按 duration 发布右臂和夹爪命令。

对 Agent 的价值：

- 已经足够支撑最小工具：`get_observation`、`reset_robot`、`move_to_pose`。
- 可以用于测试 Agent 的“调用工具 -> 记录结果 -> 行动后感知”链路。

当前不足：

- `CommandReply` 只有 `ok` 和 `message`，信息太少。
- 没有统一 `ToolResult` 格式。
- 没有返回最终关节、末端位姿、安全状态、错误类型。
- 没有 timeout / cancel / request_id。
- 没有通用 `ExecuteTool` RPC。

刚需程度：

- 对简单 Agent 联调来说：够用。
- 对稳定 Agent Tool 后端来说：需要改造。

### 2.3 图像感知：红块检测

相关文件：

```text
shishen-master/ros/grpc/perception/red_block_perception.py
```

已实现能力：

- 从 right/head camera 读取 RGB 和 depth。
- 检查 bridge status 和图像 freshness。
- 用颜色阈值检测红色区域。
- 计算 bbox、像素中心、面积、深度统计。
- 支持 right camera 和 head camera。
- 支持 `detect_with_fallback()`，right 不可靠时 fallback 到 head。

关键函数：

```python
detect_red(camera="right", min_pixels=80)
detect_with_fallback()
```

对 Agent 的价值：

- 已经可以包装成 `detect_target(target="red_block", camera="auto")`。
- 可以用于 `SenseEnvironment` 的第一版实现。

当前不足：

- 只支持 red block。
- 输出字段是红块检测内部格式，不完全等同 Agent 标准感知结构。
- 失败类型没有统一枚举，例如 `perception_not_found`、`stale_observation`。
- 没有通用目标参数，例如 red/yellow/blue/purple/green。

刚需程度：

- 对红块简单 case：已经够用。
- 对通用 Agent：后续需要多目标化和统一错误格式。

### 2.4 几何定位：红块世界坐标

相关文件：

```text
shishen-master/ros/grpc/geometry/coordinate_localizer.py
```

已实现能力：

- 读取 `latest_geometry.json`。
- 从相机内参和深度将像素点转成相机坐标。
- 将相机坐标变换到世界坐标。
- 从 geometry 中提取右臂末端位姿。
- 计算红块与右臂末端的 delta 和距离。
- 当 RGB-D 深度不可用时，可以使用 geometry object 作为 fallback。

关键函数：

```python
localize_red(camera="right")
```

返回中已有信息：

- `red_block_point_world_m`
- `right_end_effector_world_m`
- `delta_red_from_right_ee_world_m`
- `distance_red_from_right_ee_m`
- `nearest_objects`
- `detection`
- `freshness`

对 Agent 的价值：

- 已经可以包装成 `localize_target(target="red_block")`。
- 对接近、抓取、错误恢复都有用。

当前不足：

- 函数和字段名强绑定 red block。
- 输出字段没有统一成通用 `target_world_m / delta_world_m / distance_m`。
- 没有统一失败类型。
- 如果 geometry 不新鲜，Agent 需要明确知道是 `stale_observation` 还是 `localization_failed`。

刚需程度：

- 对红块简单 case：已经够用。
- 对多目标和长期任务：需要通用化。

### 2.5 右臂 pose 和命名 pose

相关文件：

```text
shishen-master/ros/grpc/skills/red_block_grasp_client.py
```

已实现能力：

- 定义多组右臂固定 pose：
  - `pregrasp`
  - `approach`
  - `default`
  - `red-near`
  - `lift`
- 支持发送右臂 joints。
- 支持发送 gripper 值。
- 支持通过脚本命令调用：
  - `pose`
  - `named-pose`
  - `gripper`

对 Agent 的价值：

- 可以包装成 `move_to_pose(pose_name=...)`。
- 可以作为接近/抓取工具内部的低层动作。

当前不足：

- pose 主要围绕红块任务调参。
- 没有统一安全检查返回。
- 没有返回执行后的 observation 或最终 joints。

刚需程度：

- 对简单 case：够用。
- 对真实多任务：需要补安全检查和结构化返回。

### 2.6 视觉伺服、搜索和接近

相关文件：

```text
shishen-master/ros/grpc/skills/coordinate_servo_client.py
shishen-master/ros/grpc/skills/jacobian_coordinate_servo_client.py
shishen-master/ros/grpc/skills/red_block_grasp_client.py
```

已实现或部分实现能力：

- `coordinate-approach`
- `jacobian-approach`
- `servo-red-block`
- `head-servo-red-block`
- `right-camera-grasp-align`
- `right-depth-probe`
- `right-depth-grid`
- `right-depth-explore`

这些能力已经覆盖：

- 基于 3D 距离的接近。
- 基于 Jacobian 的局部伺服。
- 基于右手相机 RGB-D 的接近评分。
- 局部 probing / grid / explore 搜索。
- 失败时寻找更好抓取 pose。

对 Agent 的价值：

- 可以作为 `align_with_visual_servo`、`approach_target`、`search_around` 的底层实现。
- 失败恢复能力已经有基础。

当前不足：

- 多数能力以脚本命令形式暴露。
- 输出不是统一 `ToolResult`。
- 部分命令是调试/实验性质，稳定性和适用条件需要下游标注。
- 语义上仍围绕 red block。

刚需程度：

- 对最小 Agent 联调：不是第一刚需。
- 对提高抓取成功率：重要。
- 对复杂失败恢复：后续必须整理。

### 2.7 红块抓取流程

相关文件：

```text
shishen-master/ros/grpc/skills/red_block_grasp_client.py
```

已实现能力：

- `grasp-red-block`
- `visual-grasp-red-block`
- `abs-pose-red-block`
- 多颜色 block 的部分 geometry/static preset 支持：
  - red
  - yellow
  - purple
  - green
  - blue
- 右手相机抓取检查。
- abs pose / IK action layout 相关能力。
- 抓取前后观察和部分验证逻辑。

对 Agent 的价值：

- 可以包装成第一版 `grasp_at_current` 或 `GraspObject(target="red_block")`。
- 对红块抓取简单 case，可以先用现有能力测试 Agent。

当前不足：

- 功能集中在一个很大的脚本里，接口边界不清晰。
- 很多返回通过 print 输出，不适合 Agent 直接解析。
- 抓取是否成功的验证还没有统一成标准字段 `grasped / verified / holding_object`。
- 放置任务不是当前主要实现目标。

刚需程度：

- 对“抓红块”简单 case：可先包装现有命令使用。
- 对标准 Agent 工具：需要拆分和结构化返回。

## 3. 当前刚需缺口

这里的“刚需”指：如果不做，Agent 很难稳定调用下游能力；或者结果无法进入 Agent 的 memory / 失败恢复 / finalize 验证。

### 3.1 统一工具返回格式

当前下游最需要补的是统一返回格式，而不是马上新增大量机器人能力。

建议返回格式对齐 Agent：

```json
{
  "status": "success",
  "message": "localized red block",
  "data": {},
  "raw_output": "",
  "tool_name": "localize_target"
}
```

失败也必须结构化：

```json
{
  "status": "failed",
  "message": "red block not detected",
  "data": {
    "error_type": "perception_not_found",
    "recoverable": true,
    "retry_suggestion": "try camera=head or run search_around",
    "safety_status": {
      "is_safe": true,
      "issues": []
    }
  },
  "tool_name": "detect_target"
}
```

优先级：必须先做。

### 3.2 Agent Tool Adapter

当前不建议 Agent 直接调用 shell 脚本。至少需要一个 Python adapter 层，把现有函数或脚本能力包装成稳定接口。

建议先新增：

```text
shishen-master/ros/grpc/agent_tools/
├── __init__.py
├── results.py
├── observation.py
├── perception.py
├── localization.py
├── motion.py
└── grasp.py
```

第一版不一定要重构所有底层代码，只要 adapter 能稳定输出统一 JSON 即可。

优先级：必须先做。

### 3.3 SenseEnvironment

Agent 当前每次动作后会主动调用感知工具。如果没有统一的 `SenseEnvironment`，Agent 只能看到零散检测/定位结果，无法稳定判断任务状态。

第一版 `SenseEnvironment` 可以由以下能力拼出来：

- `GetObservation`
- `detect_red`
- `localize_red`
- bridge status
- 当前右臂 joints / gripper 状态，如果能读到则加入

返回需要对齐：

```json
{
  "objects": [],
  "robot_state": {},
  "gripper_state": {},
  "task_progress": {},
  "safety_status": {},
  "uncertainties": [],
  "raw": {}
}
```

优先级：必须先做。

### 3.4 工具错误类型

Agent 已经有失败恢复逻辑，但需要下游明确告诉它失败原因。

建议下游统一使用：

- `bridge_not_running`
- `stale_observation`
- `perception_not_found`
- `localization_failed`
- `invalid_arguments`
- `unsupported_target`
- `safety_blocked`
- `motion_timeout`
- `motion_failed`
- `grasp_failed`
- `verification_failed`
- `internal_error`

优先级：必须先做。

### 3.5 基础工具包装

建议第一批包装以下工具：

| Agent 工具 | 下游已有基础 | 是否刚需 |
|---|---|---|
| `reset_robot` | `ResetRobot` | 是 |
| `get_observation` | `GetObservation` | 是 |
| `detect_target` | `detect_red` | 是 |
| `localize_target` | `localize_red` | 是 |
| `move_to_pose` | `SetRightArmPose` / named pose | 是 |
| `grasp_at_current` 或 `grasp_object` | `grasp-red-block` / `abs-pose-red-block` | 简单抓取 case 需要 |
| `report_status` | bridge status + gRPC status | 是 |

这些足够让 Agent 做简单 case：

```text
reset -> observe -> detect red block -> localize red block -> move/approach -> grasp -> observe
```

## 4. 可以后续再补的功能

这些功能对完整机器人任务很重要，但不是当前 Agent 初步联调的阻塞项。

### 4.1 通用多目标检测

当前红块检测已可用。多颜色、多类别目标可以后续做。

原因：

- 简单 case 可以先固定 `target="red_block"`。
- Agent 主循环和工具调用协议不依赖多目标能力。

### 4.2 完整 `move_end_effector`

原工具设计里有 `move_end_effector`，但当前下游主要是 joint pose 和 abs pose/IK 相关能力。

短期可以先不用完整 EE 控制接口，使用：

- `move_to_pose`
- `coordinate-approach`
- `jacobian-approach`
- `abs-pose-red-block`

后续再整理成真正的 `move_end_effector`。

### 4.3 完整放置 `PlaceObject`

当前下游重点是抓红块，还没有清晰独立的放置工具。

简单 case 可以先测试：

- Agent 决策链路
- 感知链路
- 定位链路
- 抓取链路

“抓取后放入盒子”这种完整任务可以后续等放置工具实现后再测。

### 4.4 完整任务验证 `VerifyTaskState`

当前可以先通过 `SenseEnvironment` 和抓取结果做粗验证。

后续再补：

- 物体是否在目标容器内
- 夹爪是否释放
- 目标区域是否正确
- `task_progress.overall_completion`

### 4.5 高级搜索和恢复

`right-depth-probe/grid/explore` 已经有基础，但可以后续统一。

当前简单 case 可以先不依赖搜索工具，失败时由 Agent 记录失败并停止或重新感知。

## 5. 建议当前最小交付范围

为了尽快让 NewAgent 和 `shishen-master` 联调，建议下游第一版只交付以下内容：

### 5.1 必须交付

1. Python adapter 层，避免 Agent 直接调 shell。
2. 统一 `ToolResult` JSON。
3. `reset_robot` 包装。
4. `get_observation` 包装。
5. `detect_target(target="red_block")` 包装。
6. `localize_target(target="red_block")` 包装。
7. `move_to_pose` 包装。
8. `sense_environment` 聚合工具。
9. `report_status` 工具。

### 5.2 简单抓取 case 需要交付

1. `grasp_at_current` 或 `grasp_object(target="red_block")` 包装。
2. 抓取结果结构化字段：

```json
{
  "grasped": true,
  "verified": false,
  "holding_object": "unknown",
  "strategy": "abs_pose",
  "steps": []
}
```

第一版即使不能完全确认 `holding_object`，也应该明确返回 `unknown`，不要伪装成已验证成功。

### 5.3 暂不要求

- 多目标检测。
- 完整放置工具。
- 完整 EE pose 控制。
- 完整任务级 verifier。
- 动态工具发现。
- 多机器人/多臂泛化。

## 6. 对 Agent 侧的联调建议

在下游完成最小 adapter 前，Agent 可以继续使用 mock 工具测试：

- ReAct 主循环
- OpenAI tool calling 消息格式
- 失败处理
- Finalize 阻断
- CLI
- trajectory 保存

下游完成最小 adapter 后，Agent 第一轮真实联调建议只测：

```text
任务：观察并定位红块
工具链：get_observation -> detect_target -> localize_target -> sense_environment
```

第二轮再测：

```text
任务：移动到红块附近
工具链：reset_robot -> localize_target -> move_to_pose / approach_target -> sense_environment
```

第三轮再测：

```text
任务：尝试抓取红块
工具链：reset_robot -> sense_environment -> grasp_object -> sense_environment
```

暂时不要一开始就测完整“抓取并放入盒子”，因为下游放置和任务验证还未形成稳定工具。

## 7. 总结

下游 `shishen-master` 当前不是“没有能力”，而是“能力已经有不少，但还没有整理成 Agent 可稳定调用的工具接口”。

已经具备的基础能力：

- bridge/gRPC 启动脚本
- observation
- reset
- right arm pose
- 红块检测
- 红块定位
- 多种接近、伺服、搜索和抓取实验能力

当前刚需缺口：

- 统一 ToolResult 返回格式
- Python adapter 层
- SenseEnvironment 聚合工具
- 结构化错误类型
- 基础工具包装

可以后续再补：

- 多目标泛化
- 完整放置
- 完整任务验证
- 完整 EE pose 控制
- 高级搜索恢复标准化

因此，建议下游下一步不要先大规模重写控制算法，而是先把已有能力包装成稳定、语义化、结构化的工具接口。这样 NewAgent 就可以尽快接入真实下游代码，在简单红块 case 上开始联调。
