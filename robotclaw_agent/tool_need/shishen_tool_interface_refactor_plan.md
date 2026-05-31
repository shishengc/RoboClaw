# shisheng 下游工具接口重构方案

> 本文给 `/home/easyai/桌面/shisheng` 的实现同学使用。目标是在不新增机器人能力的前提下，把当前规则式代码拆成 Agent 可调用的原子工具后端。

---

## 1. 当前代码结构事实

`shisheng` 当前代码很集中：

```text
shisheng/
├── scripts/
│   ├── 01_before_app.sh
│   ├── 02_after_app_init.sh
│   ├── 03_run_grpc_command.sh
│   ├── 04_cleanup.sh
│   └── grpc_red_block.sh
└── ros/grpc/
    ├── proto/robot_service.proto
    ├── server/grpc_robot_server.py
    ├── client/grpc_robot_client.py
    ├── perception/red_block_perception.py
    ├── geometry/coordinate_localizer.py
    └── skills/
        ├── coordinate_servo_client.py
        ├── jacobian_coordinate_servo_client.py
        └── red_block_grasp_client.py
```

真实能力分布：

- gRPC server 只暴露 3 个 RPC：`GetObservation`、`ResetRobot`、`SetRightArmPose`
- `red_block_perception.py` 负责 RGB-D 红块检测
- `coordinate_localizer.py` 负责红块 RGB-D/geometry 定位
- `coordinate_servo_client.py` 和 `jacobian_coordinate_servo_client.py` 是关节空间闭环靠近目标
- `red_block_grasp_client.py` 约 4464 行，混合了 pose、检测、几何、多色块定位、右手相机抓取检查、搜索、abs pose、抓取、放置、分拣区、CLI 参数解析
- shell 脚本负责容器文件复制、服务启动、bridge 控制和调用 Python CLI

当前问题不是“没有功能”，而是功能没有形成稳定的库接口。

---

## 2. 重构目标

本轮只做接口层重构：

- 保留现有算法和参数默认值
- 保留现有 shell 入口作为人工调试工具
- 把 CLI 中的业务逻辑抽成 Python 函数
- 所有工具函数返回统一结构，不在核心函数里 `print()` 或 `raise SystemExit`
- 让 Agent 可以直接通过 Python adapter 调用，不解析 stdout

本轮不做：

- 不新增新的感知算法
- 不重写 gRPC server 架构
- 不要求一次性泛化全部物体
- 不要求 Agent 管理服务生命周期
- 不把整段 `abs-pose-red-block` 作为唯一工具

---

## 3. 建议新增目录

在 `shisheng/ros/grpc/` 下新增：

```text
agent_tools/
├── __init__.py
├── types.py
├── runtime.py
├── targets.py
├── poses.py
├── observation.py
├── perception.py
├── localization.py
├── motion.py
├── alignment.py
├── search.py
├── grasp.py
├── placement.py
├── sense.py
└── cli.py
```

职责：

- `types.py`: `ToolResult`、`ToolStatus`、错误类型常量
- `runtime.py`: bridge 路径、gRPC stub、status/observation 读取、freshness 工具函数
- `targets.py`: `red_block`、`yellow_block` 等 target 归一化
- `poses.py`: 从 `red_block_grasp_client.py` 拆出的 `POSES`、gripper 常量、joint limit
- `observation.py`: `GetObservation`
- `perception.py`: `DetectTarget`
- `localization.py`: `LocalizeTarget`
- `motion.py`: `ResetRobot`、`MoveToNamedPose`、`SetRightArmPose`、`SetGripper`、`MoveEndEffectorToWorld`、`ApproachTarget`
- `alignment.py`: `AlignTarget`
- `search.py`: `SearchGraspPose`
- `grasp.py`: `CheckGraspReady`、`GraspAtCurrent`
- `placement.py`: `PlaceHeldObject`
- `sense.py`: `SenseEnvironment`
- `cli.py`: 可选薄 CLI，只负责参数解析和打印工具结果

---

## 4. 统一类型

建议在 `agent_tools/types.py` 放：

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class ToolResult:
    status: ToolStatus
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""
    tool_name: str = ""

    @property
    def success(self) -> bool:
        return self.status == ToolStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "message": self.message,
            "data": self.data,
            "raw_output": self.raw_output,
            "tool_name": self.tool_name,
        }
```

失败时统一：

```python
ToolResult(
    status=ToolStatus.FAILED,
    message="runtime geometry is stale or app is not running",
    data={
        "error_type": "stale_observation",
        "freshness": freshness,
    },
    tool_name="LocalizeTarget",
)
```

---

## 5. 必须先拆的代码

### 5.1 `red_block_grasp_client.py`

这是最需要拆的文件。建议按以下方式搬迁：

| 当前内容 | 新位置 |
|---|---|
| `POSES`、`GRIPPER_OPEN`、`GRIPPER_CLOSE`、joint limit | `agent_tools/poses.py` |
| `make_stub`、`call_pose`、`call_reset` | `agent_tools/runtime.py` / `agent_tools/motion.py` |
| `_read_latest_observation`、`current_right_arm_joints`、`_current_eef_xyzrpy` | `agent_tools/runtime.py` |
| `observe_head_alignment*` | `agent_tools/alignment.py` |
| `observe_probe_score`、right-depth score | `agent_tools/search.py` |
| geometry block lookup | `agent_tools/localization.py` |
| `_abs_pose_for_world_target`、`_write_manual_action` | `agent_tools/motion.py` |
| `observe_right_grasp`、`_brief_right_grasp` | `agent_tools/grasp.py` |
| `run_right_camera_grasp_align` | `agent_tools/alignment.py` |
| `_sort_zone_targets_*`、table geometry | `agent_tools/placement.py` |
| close/wait/lift 子流程 | `agent_tools/grasp.py` |
| place_after_lift 子流程 | `agent_tools/placement.py` |
| argparse 和 subcommand dispatch | 保留在旧 CLI 或迁到 `agent_tools/cli.py` |

原则：

- 核心函数不 `print`
- 核心函数不 `raise SystemExit`
- CLI 捕获 `ToolResult` 后再决定退出码

### 5.2 `coordinate_servo_client.py`

当前 `run_coordinate_approach(args)` 直接打印 JSON 和退出。需要抽成：

```python
def align_coordinate(
    target: str = "red_block",
    camera: str = "right",
    max_iters: int = 4,
    target_distance_m: float = 0.14,
    target: str = "127.0.0.1:50051",
) -> ToolResult:
    ...
```

保留 CLI：

```python
def main():
    result = align_coordinate(...)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.success else 1)
```

### 5.3 `jacobian_coordinate_servo_client.py`

同理抽成：

```python
def align_jacobian(
    target: str = "red_block",
    camera: str = "head",
    joints: str = "0,1,2,3",
    max_iters: int = 3,
    target_distance_m: float = 0.12,
) -> ToolResult:
    ...
```

### 5.4 `red_block_perception.py`

保留检测算法，新增 adapter：

```python
def detect_target(target: str = "red_block", camera: str = "auto", min_pixels: int = 80) -> ToolResult:
    normalized = normalize_target(target)
    if normalized["name"] != "red_block":
        return failed("unsupported_target", ...)
    result = detect_with_fallback() if camera == "auto" else detect_red(camera, min_pixels=min_pixels)
    return normalize_detection_result(result)
```

### 5.5 `coordinate_localizer.py`

保留 `localize_red()`，但正式工具字段要通用：

```python
def localize_target(target: str = "red_block", camera: str = "geometry", scene_instance_id: int = 0) -> ToolResult:
    ...
```

正式输出用：

- `target_world_m`
- `right_end_effector_world_m`
- `delta_world_m`
- `distance_m`
- `source`

旧字段放入 `raw`。

---

## 6. 工具实现要求

### 6.1 `GetObservation`

实现位置：`agent_tools/observation.py`

要求：

- 调 gRPC `GetObservation`
- 解析 `status_json`
- 尝试读取 `manual_policy/latest_observation.json`
- 返回 image path、bridge phase、freshness

### 6.2 `SenseEnvironment`

实现位置：`agent_tools/sense.py`

要求：

1. 调 `GetObservation`
2. 调 `DetectTarget`
3. 调 `LocalizeTarget`
4. 可选调 `CheckGraspReady`
5. 汇总成 Agent 可读状态

不要只把原始 dict 原样丢给 Agent。

### 6.3 `ResetRobot`

实现位置：`agent_tools/motion.py`

要求：

- 调 gRPC `ResetRobot`
- 捕获 gRPC 异常、timeout
- 返回 `duration`、`reset_sent`

### 6.4 `MoveToNamedPose`

实现位置：`agent_tools/motion.py`

要求：

- 从 `poses.py` 读取 pose
- 调 `SetRightArmPose`
- 返回实际 joints、gripper、duration

### 6.5 `SetRightArmPose`

实现位置：`agent_tools/motion.py`

要求：

- 参数校验：`len(joints) == 7`
- gripper 范围校验
- duration 大于 0
- 调 gRPC `SetRightArmPose`

### 6.6 `SetGripper`

实现位置：`agent_tools/motion.py`

要求：

- `hold_pose="current"` 时读取当前右臂 joints
- 如果当前状态不可读，允许传 named pose fallback
- 统一 Agent 侧 gripper 语义，内部处理 gRPC 和 AbsPoseEnv 差异

### 6.7 `MoveEndEffectorToWorld`

实现位置：`agent_tools/motion.py`

要求：

- 使用 `_current_eef_xyzrpy`
- 使用 `_abs_pose_for_world_target`
- 使用 `_write_manual_action`
- 可选等待 `_wait_for_right_eef_world_target`
- 返回是否到达目标和最终误差

### 6.8 `ApproachTarget`

实现位置：`agent_tools/motion.py`

要求：

- 调 `LocalizeTarget`
- 计算 `target_world + approach_offset`
- 调 `MoveEndEffectorToWorld`
- 不闭合夹爪

### 6.9 `AlignTarget`

实现位置：`agent_tools/alignment.py`

要求：

根据 `method` 分发：

| method | 当前实现 |
|---|---|
| `coordinate` | `coordinate_servo_client` |
| `jacobian` | `jacobian_coordinate_servo_client` |
| `head_coordinate` | `run_head_servo_red_block(method=coordinate)` |
| `head_jacobian` | `run_head_jacobian_servo` |
| `right_camera_grasp` | `run_right_camera_grasp_align` |
| `right_depth_probe` | `run_right_depth_probe` |
| `right_depth_grid` | `run_right_depth_grid` |
| `right_depth_explore` | `run_right_depth_explore` |

返回统一字段：

- `aligned`
- `ready_to_close`
- `iterations`
- `distance_m`
- `distance_px`
- `final_pose`
- `final_observation`

### 6.10 `SearchGraspPose`

实现位置：`agent_tools/search.py`

要求：

- 只作为失败恢复工具
- 包装 probe/grid/explore
- 返回 best pose、score、close_ready、tried

### 6.11 `CheckGraspReady`

实现位置：`agent_tools/grasp.py`

要求：

- 包装 `observe_right_grasp`
- 不移动机器人
- 只判断当前是否适合闭合

### 6.12 `GraspAtCurrent`

实现位置：`agent_tools/grasp.py`

要求：

- 可选先调 `CheckGraspReady`
- 闭合夹爪
- 等待夹爪闭合状态
- 可选按世界坐标 lift，或者用旧 `POSES["lift"]` fallback
- 不做 approach，不做 place

### 6.13 `PlaceHeldObject`

实现位置：`agent_tools/placement.py`

要求：

- 只处理“已经抓住物体之后”的放置
- 支持 `sort_zone`、`target_block`、`relative_offset`、`world`
- 从 `run_abs_pose_red_block` 中拆出 place 子流程
- 返回 `placed`、`released`、hover/descend/release observation

---

## 7. CLI 保留方式

旧命令可以保留，但要变成 adapter 的薄包装。例如：

```python
if args.command == "detect-red":
    result = detect_target(target="red_block", camera=args.camera)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.success else 1)
```

这样人工调试命令不变，但 Agent 不再依赖 stdout。

---

## 8. gRPC 是否需要扩展

第一阶段不强制扩展 proto。推荐先做 Python adapter，因为现在高层能力本来就在 Python client 侧。

后续如果要跨进程稳定调用，再考虑增加：

```proto
rpc ExecuteTool(ExecuteToolRequest) returns (ExecuteToolReply);
```

但不要第一步就做通用 RPC。当前主要风险是工具边界和返回格式还没稳定，过早固化 proto 会增加返工。

---

## 9. 推荐实施顺序

第一批，先让 Agent 能跑基本闭环：

1. `types.py`、`runtime.py`、`targets.py`、`poses.py`
2. `GetObservation`
3. `DetectTarget`
4. `LocalizeTarget`
5. `MoveToNamedPose`
6. `SetRightArmPose`
7. `SetGripper`
8. `SenseEnvironment`

第二批，接入真实动作闭环：

1. `MoveEndEffectorToWorld`
2. `ApproachTarget`
3. `AlignTarget(method=right_camera_grasp)`
4. `CheckGraspReady`
5. `GraspAtCurrent`

第三批，支持排序和恢复：

1. `PlaceHeldObject`
2. `SearchGraspPose`
3. `AlignTarget` 其他 method

---

## 10. 验收标准

每个工具至少有一个本地可调用函数：

```python
from ros.grpc.agent_tools.perception import detect_target

result = detect_target(target="red_block", camera="auto")
assert result.tool_name == "DetectTarget"
assert result.status.value in {"success", "failed", "partial"}
```

验收要求：

- 核心工具函数不直接 `print`
- 核心工具函数不直接 `raise SystemExit`
- 失败结果包含 `data.error_type`
- CLI 输出为 `ToolResult.to_dict()`
- Agent 不需要知道 shell 脚本名
- Agent 不需要解析旧字段名，例如 `delta_red_from_right_ee_world_m`
- `SenseEnvironment` 能在动作后给出可决策的摘要

---

## 11. 当前风险

1. Gripper 语义有两套。
gRPC 路线是 `0.8=open, 0.0=closed`；AbsPoseEnv/manual_action 路线在当前脚本中是 `0=open, 1=closed`。adapter 必须统一。

2. RGB-D 检测和 geometry 定位支持范围不同。
红块检测走 RGB-D，彩色块定位更多依赖 geometry/static fallback。工具返回必须标明 source。

3. `red_block_grasp_client.py` 同时承担 CLI 和库职责。
如果不拆，Agent 后端会被 `print`、`SystemExit`、stdout JSON 和 argparse 绑住。

4. 服务生命周期目前靠 shell。
第一阶段可以人工启动服务，但工具要能清楚报告 `bridge_not_running` 和 `stale_observation`。

---

*更新时间：2026-05-22*
