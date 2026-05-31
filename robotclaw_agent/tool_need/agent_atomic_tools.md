# Agent 原子工具设计（基于 shisheng 当前实现）

> 本文是 Agent 侧工具契约。它参考 `/home/easyai/桌面/shisheng` 当前实现，但不直接照搬脚本命令名。Agent 看到的是稳定的机器人语义动作；下游负责把现有 gRPC、感知、几何、伺服、抓取和放置代码封装成这些工具。

---

## 1. 对 shisheng 当前实现的判断

`shisheng` 现在不是单纯的 demo 脚本，已经有一条规则式机器人任务链：

- gRPC 基础控制：`GetObservation`、`ResetRobot`、`SetRightArmPose`
- 图像感知：`red_block_perception.detect_red()` / `detect_with_fallback()`
- 几何定位：`coordinate_localizer.localize_red()`、`red_block_grasp_client.localize_block_from_geometry()`
- 关节伺服：`coordinate_servo_client.py`、`jacobian_coordinate_servo_client.py`
- 右手相机近距离对齐和抓取检查：`observe_right_grasp()`、`run_right_camera_grasp_align()`
- 搜索和探测：`run_probe_red_block()`、`run_right_depth_grid()`、`run_right_depth_explore()`
- 绝对位姿抓取、抬升、放置和分拣区动作：`run_abs_pose_red_block()`

当前主要问题不是能力缺失，而是能力被塞在 CLI 和长流程脚本里。Agent 工具应该把这些能力拆成可组合的原子动作，而不是暴露 `abs-pose-red-block` 这种整段流程。

---

## 2. 工具设计原则

1. Agent 决策，不做数值计算。
Agent 只选择工具和策略参数，例如目标颜色、相机、对齐方法、放置目标；像素阈值、坐标变换、Jacobian、IK offset、搜索评分都在下游工具内部完成。

2. 工具按闭环语义切分。
工具边界应该对应机器人执行闭环里的自然阶段：观察、检测、定位、接近、对齐、抓取、抬升、放置、验证。

3. 失败必须结构化。
每个工具返回 `success / failed / partial`，并明确错误类型，Agent 才能决定重试、换相机、换策略、重新感知或中止。

4. 高层“一把梭”流程不作为第一版主工具。
`abs-pose-red-block --close --sort-zone` 这种整段流程可以保留为人工调试入口，但正式工具要拆成 `ApproachTarget`、`GraspAtCurrent`、`PlaceHeldObject` 等可组合动作。

---

## 3. 统一 ToolResult

所有工具返回结构必须兼容 `src/new_agent/tools/tool_types.py`：

```python
ToolResult(
    status=ToolStatus.SUCCESS | ToolStatus.FAILED | ToolStatus.PARTIAL,
    message="short human readable summary",
    data={...},
    raw_output="optional raw payload or stdout for debugging",
    tool_name="DetectTarget",
)
```

推荐 `data.error_type`：

```text
invalid_argument
unsupported_target
unsupported_mode
bridge_not_running
stale_observation
grpc_error
perception_failed
detection_failed
localization_failed
motion_failed
alignment_failed
grasp_not_ready
grasp_failed
place_failed
timeout
```

状态含义：

- `success`: 工具主目标达成，结果可用于下一步
- `partial`: 有可用中间结果，但没有完全达成，例如检测到目标但深度无效、对齐改善但未达阈值
- `failed`: 主目标失败，当前结果不足以继续推进

---

## 4. 目标对象参数规范

第一版可以用字符串简写：

```json
"red_block"
```

但工具内部应尽快归一化成结构：

```json
{
  "type": "block",
  "color": "red",
  "name": "red_block"
}
```

当前支持边界：

- RGB-D 检测当前只稳定支持 `red_block`
- geometry 定位已有 `red/yellow/purple/green/blue` 的实现基础
- 如果某工具只支持红块，必须返回 `unsupported_target`，不要静默当成红块执行

---

## 5. Agent 可见工具清单

### 5.1 `GetObservation`

用途：获取 bridge 状态、图像路径、status、最新 observation 摘要。

当前来源：

- `grpc_robot_server.py: GetObservation`
- `grpc_robot_client.py observation`

输入：

```json
{
  "include_images": true,
  "include_status": true,
  "include_observation": true
}
```

输出 `data`：

```json
{
  "bridge_state": "running",
  "images": {
    "head": "/geniesim/main/.geniesim_bridge/manual_policy/latest_top_head.jpg",
    "right": "/geniesim/main/.geniesim_bridge/manual_policy/latest_hand_right.jpg",
    "left": "/geniesim/main/.geniesim_bridge/latest_left_hand.jpg"
  },
  "status_json_path": "/geniesim/main/.geniesim_bridge/status.json",
  "status": {},
  "observation": {},
  "freshness": {
    "fresh": true,
    "status_age_sec": 0.2
  }
}
```

### 5.2 `SenseEnvironment`

用途：Agent 每次动作后使用的聚合感知工具。它不是新算法，而是组合 `GetObservation`、`DetectTarget`、`LocalizeTarget`、`CheckGraspReady` 的结果。

当前来源：

- adapter 组合现有能力

输入：

```json
{
  "target": "red_block",
  "camera": "auto",
  "include_grasp_check": true
}
```

输出 `data`：

```json
{
  "objects": [
    {
      "name": "red_block",
      "visible": true,
      "world_m": [0.044, -0.050, 0.885],
      "confidence": 0.9,
      "source": "geometry"
    }
  ],
  "robot_state": {
    "bridge_state": "running",
    "right_arm_joints": [],
    "right_end_effector_world_m": []
  },
  "gripper_state": {
    "value": 0.0,
    "closed": true,
    "holding": "unknown"
  },
  "task_progress": {
    "overall_completion": null,
    "status": "observed",
    "completed_subtasks": [],
    "pending_subtasks": []
  },
  "safety_status": {
    "is_safe": true,
    "issues": []
  },
  "raw": {}
}
```

### 5.3 `ResetRobot`

用途：恢复到安全初始状态。

当前来源：

- `grpc_robot_server.py: ResetRobot`

输入：

```json
{
  "duration": 2.0
}
```

输出 `data`：

```json
{
  "duration": 2.0,
  "reset_sent": true,
  "final_joints": null,
  "gripper": {
    "left": 0.8,
    "right": 0.8
  }
}
```

### 5.4 `DetectTarget`

用途：检测目标在图像中的像素位置、bbox、深度和 freshness。

当前来源：

- `red_block_perception.detect_red()`
- `red_block_perception.detect_with_fallback()`

输入：

```json
{
  "target": "red_block",
  "camera": "auto",
  "min_pixels": 80
}
```

输出 `data`：

```json
{
  "target": "red_block",
  "camera": "right",
  "detected": true,
  "center_px": [322.1, 251.4],
  "center_norm": [0.50, 0.58],
  "bbox": [288, 214, 356, 292],
  "pixels": 1240,
  "area_ratio": 0.02,
  "depth_m": 0.31,
  "depth_stats": {},
  "rgb_path": "...",
  "depth_path": "...",
  "freshness": {}
}
```

### 5.5 `LocalizeTarget`

用途：把目标定位到世界坐标，并给出目标相对右臂末端的误差。

当前来源：

- `coordinate_localizer.localize_red()`
- `red_block_grasp_client.localize_block_from_geometry()`

输入：

```json
{
  "target": "red_block",
  "camera": "geometry",
  "scene_instance_id": 0
}
```

输出 `data`：

```json
{
  "target": "red_block",
  "target_world_m": [0.044, -0.050, 0.885],
  "right_end_effector_world_m": [0.092, -0.012, 0.932],
  "right_end_effector_matrix_world": null,
  "delta_world_m": [-0.048, -0.038, -0.047],
  "distance_m": 0.077,
  "source": "runtime_geometry_object",
  "detection": {},
  "nearest_objects": []
}
```

字段要求：

- 正式 contract 使用 `target_world_m`、`delta_world_m`、`distance_m`
- `red_block_point_world_m`、`delta_red_from_right_ee_world_m` 只能放在 `raw` 中兼容旧代码

### 5.6 `MoveToNamedPose`

用途：移动右臂到预定义关节 pose。

当前来源：

- `red_block_grasp_client.POSES`
- `call_pose()`

输入：

```json
{
  "pose_name": "approach",
  "gripper": 0.8,
  "duration": 1.0
}
```

输出 `data`：

```json
{
  "pose_name": "approach",
  "executed_joints": [1.0734, -1.65, 0.0, 1.2846, -0.7313, -1.4957, 0.1868],
  "gripper": 0.8,
  "duration": 1.0
}
```

### 5.7 `SetRightArmPose`

用途：底层关节控制工具。Agent 默认不应频繁直接使用，但它是调试、恢复和 adapter 内部实现所必需的原子能力。

当前来源：

- `grpc_robot_server.py: SetRightArmPose`

输入：

```json
{
  "joints": [1.0734, -1.60, 0.0, 1.2846, -0.7313, -1.4957, 0.1868],
  "gripper": 0.8,
  "duration": 1.0
}
```

输出 `data`：

```json
{
  "executed_joints": [],
  "gripper": 0.8,
  "duration": 1.0
}
```

### 5.8 `SetGripper`

用途：保持当前或指定右臂姿态，单独控制夹爪开合。

当前来源：

- `red_block_grasp_client` 中 `gripper` 命令逻辑
- 底层仍调用 `SetRightArmPose`

输入：

```json
{
  "value": 0.0,
  "hold_pose": "current",
  "duration": 1.0
}
```

输出 `data`：

```json
{
  "value": 0.0,
  "closed": true,
  "hold_pose": "current",
  "duration": 1.0
}
```

注意：

- gRPC joint 命令语义中 `0.8=open`、`0.0=closed`
- AbsPoseEnv action 语义中当前脚本使用 `0=open`、`1=closed`
- adapter 必须统一对 Agent 暴露一个语义，并在内部转换

### 5.9 `MoveEndEffectorToWorld`

用途：把右臂末端移动到世界坐标目标点或相对位移。这个工具对应 shisheng 中 AbsPoseEnv/manual_action 路线，是比关节 pose 更适合 Agent 的空间动作原语。

当前来源：

- `_current_eef_xyzrpy()`
- `_abs_pose_for_world_target()`
- `_write_manual_action()`
- `_wait_for_right_eef_world_target()` / `_wait_for_direct_target()`

输入：

```json
{
  "target_world_m": [0.044, -0.050, 0.945],
  "relative_delta_m": null,
  "gripper": "keep",
  "timeout": 8.0,
  "tolerance_m": 0.05
}
```

输出 `data`：

```json
{
  "target_world_m": [0.044, -0.050, 0.945],
  "reached": true,
  "distance_m": 0.031,
  "right_xyzrpy": [],
  "right_end_effector_world_m": [],
  "frame": {}
}
```

### 5.10 `ApproachTarget`

用途：移动到目标上方或安全接近点，不闭合夹爪。

当前来源：

- `run_abs_pose_red_block()` 中 `abs_pose_direct_approach`
- `LocalizeTarget`
- `MoveEndEffectorToWorld`

输入：

```json
{
  "target": "red_block",
  "approach_offset_m": [0.0, 0.0, 0.06],
  "camera": "geometry",
  "timeout": 8.0
}
```

输出 `data`：

```json
{
  "target": "red_block",
  "approach_world_m": [0.044, -0.050, 0.945],
  "reached": true,
  "distance_m": 0.04,
  "localization": {}
}
```

### 5.11 `AlignTarget`

用途：执行闭环对齐，让夹爪或右手相机抓取点接近目标。

当前来源：

- `coordinate_servo_client.run_coordinate_approach()`
- `jacobian_coordinate_servo_client.run()`
- `run_head_servo_red_block()`
- `run_right_camera_grasp_align()`

输入：

```json
{
  "target": "red_block",
  "method": "right_camera_grasp",
  "camera": "right",
  "max_iters": 4,
  "target_distance_m": 0.035,
  "target_px": 65.0
}
```

输出 `data`：

```json
{
  "target": "red_block",
  "method": "right_camera_grasp",
  "aligned": true,
  "iterations": 3,
  "distance_m": 0.032,
  "distance_px": null,
  "ready_to_close": true,
  "final_pose": {},
  "final_observation": {}
}
```

推荐 `method`：

```text
coordinate
jacobian
head_coordinate
head_jacobian
right_camera_grasp
right_depth_probe
right_depth_grid
right_depth_explore
```

### 5.12 `SearchGraspPose`

用途：在当前或 seed pose 附近搜索更好的抓取候选。它是失败恢复工具，不是常规第一步。

当前来源：

- `run_probe_red_block()`
- `run_right_depth_probe()`
- `run_right_depth_grid()`
- `run_right_depth_explore()`

输入：

```json
{
  "target": "red_block",
  "strategy": "grid",
  "seed_pose": "default",
  "use_current": true,
  "max_iters": 4
}
```

输出 `data`：

```json
{
  "target": "red_block",
  "strategy": "grid",
  "best_joints": [],
  "best_score": 0.87,
  "close_ready": true,
  "tried": []
}
```

### 5.13 `CheckGraspReady`

用途：判断当前右手相机视角下是否可以闭合夹爪。

当前来源：

- `observe_right_grasp()`
- `run_right_grasp_check()`

输入：

```json
{
  "target": "red_block",
  "target_x": 0.50,
  "target_y": 0.58,
  "target_depth_m": 0.22,
  "center_tol": 0.08,
  "depth_tol_m": 0.06
}
```

输出 `data`：

```json
{
  "ready_to_close": true,
  "centered": true,
  "depth_ready": true,
  "distance_m": 0.028,
  "pixel_error_norm": [0.01, -0.02],
  "depth_error_m": 0.01,
  "observation": {}
}
```

### 5.14 `GraspAtCurrent`

用途：在当前位置闭合夹爪，并可选等待夹爪闭合和抬升。

当前来源：

- `SetGripper`
- `_wait_for_right_gripper_closed()`
- `run_abs_pose_red_block()` 中 close/lift 子流程
- `POSES["lift"]` 的旧关节抬升路径

输入：

```json
{
  "target": "red_block",
  "require_grasp_ready": true,
  "lift_after_grasp": true,
  "lift_height_m": 0.10,
  "timeout": 5.0
}
```

输出 `data`：

```json
{
  "target": "red_block",
  "grasped": true,
  "gripper_closed": true,
  "lift_executed": true,
  "lift_world_m": [],
  "gripper_status": {},
  "final_observation": {}
}
```

### 5.15 `PlaceHeldObject`

用途：把当前夹持物放到指定目标，然后打开夹爪。它是“放置”原语，不包含抓取前置流程。

当前来源：

- `run_abs_pose_red_block()` 中 `place_after_lift`、`target_color`、`sort_zone`、固定 offset 放置逻辑
- `_sort_zone_targets_for_args()`
- `_best_geometry_table_object()`
- `_wait_for_right_eef_world_target()`

输入：

```json
{
  "held_object": "red_block",
  "target": {
    "type": "sort_zone",
    "zone": "1"
  },
  "hover_height_m": 0.08,
  "release": true,
  "return_after_place": false
}
```

目标类型：

```json
{"type": "sort_zone", "zone": "1"}
{"type": "block", "target": "yellow_block"}
{"type": "relative_offset", "offset_m": [0.0, 0.10, -0.08]}
{"type": "world", "target_world_m": [0.1, 0.2, 0.9]}
```

输出 `data`：

```json
{
  "held_object": "red_block",
  "target": {},
  "placed": true,
  "released": true,
  "place_world_m": [],
  "hover_world_m": [],
  "final_observation": {}
}
```

### 5.16 `FinalizeTask`

用途：Agent 内部结束任务。下游不需要实现。

输入：

```json
{
  "completion_summary": "red block placed into sort zone 1"
}
```

---

## 6. 不建议作为第一版 Agent 主工具的能力

这些能力可以保留作内部实现或人工调试，但不应直接暴露给 LLM：

- `abs-pose-red-block` 整段流程
- `grasp-red-block` 固定序列
- `visual-grasp-red-block` 固定序列
- `test-sort-zones` 测试命令
- `scripts/01_before_app.sh`、`02_after_app_init.sh`、`03_run_grpc_command.sh`、`04_cleanup.sh`
- 任意 `docker exec` 或 shell 命令

原因：

- 粒度过粗，Agent 无法在中间失败时恢复
- stdout JSON 不稳定，不适合作为工具 contract
- 会把环境启动、调试和任务执行混在一起

---

## 7. 推荐的最小可用工具集

第一阶段 P0：

```text
GetObservation
SenseEnvironment
ResetRobot
DetectTarget
LocalizeTarget
MoveToNamedPose
SetRightArmPose
SetGripper
MoveEndEffectorToWorld
ApproachTarget
AlignTarget
CheckGraspReady
GraspAtCurrent
PlaceHeldObject
```

第二阶段 P1：

```text
SearchGraspPose
BackendStatus
StartBridge
StopBridge
```

`BackendStatus / StartBridge / StopBridge` 属于运维工具，不建议默认给 LLM 自由调用。可以由 CLI 或外层任务 runner 管理。

---

## 8. 与 shisheng 的映射总表

| Agent 工具 | shisheng 当前来源 | 重构要求 |
|---|---|---|
| `GetObservation` | `RobotService.GetObservation` | 包装 proto reply，解析 status/observation |
| `SenseEnvironment` | 组合工具 | 聚合 observation/detect/localize/grasp check |
| `ResetRobot` | `RobotService.ResetRobot` | 统一 ToolResult |
| `DetectTarget` | `detect_red` / `detect_with_fallback` | target dispatch，第一版只支持 red |
| `LocalizeTarget` | `localize_red` / `localize_block_from_geometry` | 字段改成通用 target 命名 |
| `MoveToNamedPose` | `POSES` / `call_pose` | 从大脚本拆出 pose 配置 |
| `SetRightArmPose` | `RobotService.SetRightArmPose` | 参数校验和返回结构化 |
| `SetGripper` | `gripper` CLI 逻辑 | 统一 gripper 语义 |
| `MoveEndEffectorToWorld` | `_abs_pose_for_world_target` / `_write_manual_action` | 抽成独立空间移动工具 |
| `ApproachTarget` | `abs_pose_direct_approach` | 从 `run_abs_pose_red_block` 拆出 |
| `AlignTarget` | coordinate/jacobian/head/right-camera loops | CLI 循环改成函数返回 |
| `SearchGraspPose` | probe/grid/explore | 作为失败恢复工具 |
| `CheckGraspReady` | `observe_right_grasp` | 独立感知工具 |
| `GraspAtCurrent` | close/wait/lift 子流程 | 不包含 approach/place |
| `PlaceHeldObject` | `place_after_lift` 子流程 | 不包含 grasp |

---

*更新时间：2026-05-22*
