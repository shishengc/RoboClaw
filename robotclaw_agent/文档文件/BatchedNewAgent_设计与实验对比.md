# BatchedNewAgent 设计与实验对比

本文记录新版 `BatchedNewAgent` 的设计理念，以及一次原版 Agent 与 batch 新版 Agent 的轨迹耗时对比。

## 1. 背景

原版 Agent 使用原子工具驱动机器人任务，整体流程清晰、可解释，但在实际运行中存在一个明显问题：很多确定性连续动作之间都需要再次调用 LLM。

例如 AprilTag pick-and-place 任务中，原版常见流程是：

```text
LLM -> detect_tags
工具执行
LLM -> get_apriltag_pose
工具执行
LLM -> compute_tag_grasp_targets
工具执行
LLM -> open_gripper
工具执行
LLM -> move_eef
工具执行
...
```

这样保持了较强的逐步决策能力，但 LLM 往返次数多，导致任务整体耗时偏长。

新版 `BatchedNewAgent` 的目标不是把原子工具合并成高层复合工具，而是在保持原子动作可解释性的前提下，减少不必要的 LLM 往返。

## 2. 设计原则

新版遵循以下原则：

1. 保留所有原子工具。
2. 要求 LLM 在“确定性连续动作”时一次返回多个 tool calls。
3. Agent 顺序执行这一批 tool calls。
4. 如果任何一步失败，停止剩余动作，自动感知，下一轮恢复。
5. 自动感知从“每个动作后”改成“每批动作后”，但关键高风险节点保留感知。
6. 轨迹里继续记录每个原子 tool call，保持可解释性。

核心思想：

```text
减少 LLM 决策频率
不降低原子工具粒度
```

也就是说，新版不是：

```text
pick_tag_and_place_on_tag(source, target)
```

而是：

```text
LLM -> [
  open_gripper,
  move_eef(approach_camera_m),
  move_eef(grasp_camera_m)
]

Agent 顺序执行每个原子工具
轨迹中仍然逐个记录 tool_call
```

## 3. 新增代码入口

新版没有覆盖原版代码，而是新增独立文件：

```text
src/new_agent/core/batched_agent.py
src/new_agent/batched_tui.py
```

原版入口仍然是：

```bash
cd /home/ck/RoboClaw/robotclaw_agent
PYTHONPATH=src .venv/bin/python -m new_agent.tui --mcp-control-tools
```

新版入口是：

```bash
cd /home/ck/RoboClaw/robotclaw_agent
PYTHONPATH=src .venv/bin/python -m new_agent.batched_tui --mcp-control-tools
```

## 4. BatchedNewAgent 的运行机制

新版继承原版 `NewAgent`，但重写了 `run_once()`。

### 4.1 批量工具执行

当 LLM 一次返回多个 `tool_calls` 时，新版 Agent 会按顺序执行：

```text
assistant_tool_calls(batch_size=N)
  -> tool_call 1
  -> tool_call 2
  -> tool_call 3
  -> perception(post_batch_verification)
```

如果工具执行成功，会继续执行后续工具。

### 4.2 失败即停

如果某个工具失败：

```text
tool_call failed
remaining tool_calls -> tool_call_deferred
batch_aborted
perception(tool_failure_recovery)
```

剩余工具不会继续执行，避免机器人在错误状态下继续动作。

### 4.3 关键 checkpoint

对于高风险动作，新版会强制暂停后续 batch 并感知。

当前关键 checkpoint 包括：

```text
close_gripper
GraspAtCurrent
PlaceHeldObject
```

例如 LLM 一次返回：

```text
close_gripper
move_eef(lift_camera_m)
```

新版可能执行为：

```text
close_gripper
move_eef -> tool_call_deferred
batch_checkpoint
perception(critical_checkpoint_after_close_gripper)
下一轮 LLM 再决定是否 lift
```

这样比完全连续执行慢一些，但更安全。

### 4.4 自动感知策略

原版是每次非感知工具后通常自动感知。

新版改为：

```text
每批工具执行后感知一次
关键 checkpoint 后感知一次
工具失败后感知一次
```

这减少了工具调用数量和上下文膨胀。

## 5. 实验轨迹

本次对比使用同类任务：

```text
确定好tag0和tag 1的位置，然后把tag 0放到tag 1上，这是一个装配任务。
```

原版轨迹：

```text
/home/ck/RoboClaw/robotclaw_agent/trajectories/ShishengTUIAgent_20260528_113910.json
```

新版 batch 轨迹：

```text
/home/ck/RoboClaw/robotclaw_agent/trajectories/BatchedShishengTUIAgent_20260528_113205.json
```

两条轨迹最后都成功调用 `FinalizeTask`。

## 6. 总体结果

| 版本 | 开始时间 | 结束时间 | 总耗时 |
|---|---:|---:|---:|
| 原版 | 03:37:41 | 03:39:10 | 89 秒 |
| batch 新版 | 03:31:17 | 03:32:05 | 48 秒 |

新版节省：

```text
89s - 48s = 41s
```

相对减少：

```text
约 46%
```

## 7. 耗时拆分

| 版本 | LLM 等待 | 工具执行 | 感知记录 | 总耗时 |
|---|---:|---:|---:|---:|
| 原版 | 约 83 秒 | 约 6 秒 | 约 0 秒 | 89 秒 |
| batch 新版 | 约 44 秒 | 约 4 秒 | 约 0 秒 | 48 秒 |

说明：

- 当前轨迹时间戳只有秒级精度。
- `perception` 显示为 0 秒不代表没有耗时，只是被秒级时间戳低估。
- 主要耗时仍然来自 LLM API 等待。

## 8. 调用次数对比

| 版本 | assistant_tool_calls | tool_call | perception |
|---|---:|---:|---:|
| 原版 | 16 次 | 17 次 | 13 次 |
| batch 新版 | 11 次 | 16 次 | 8 次 |

新版减少：

```text
LLM 决策轮数减少 5 次
perception 次数减少 5 次
```

这是总耗时降低的主要原因。

## 9. 原版运行特点

原版大部分时候一次 LLM 调用只返回一个工具：

```text
detect_tags
get_apriltag_pose, get_apriltag_pose
resolve_tag_pick_place_recipe
compute_tag_grasp_targets
open_gripper
SenseEnvironment
move_eef
move_eef
close_gripper
move_eef
compute_tag_place_targets
move_eef
move_eef
open_gripper
SenseEnvironment
FinalizeTask
```

虽然也偶尔一次返回两个工具，但整体仍然接近“一步一问 LLM”。

原版统计：

```text
总耗时: 89 秒
LLM 等待: 约 83 秒
工具执行: 约 6 秒
```

## 10. batch 新版运行特点

新版中 LLM 多次返回多个原子工具：

```text
get_apriltag_pose + get_apriltag_pose
open_gripper + move_eef + move_eef
close_gripper + move_eef
move_eef + move_eef + open_gripper
```

其中 `close_gripper + move_eef` 触发了关键 checkpoint：

```text
close_gripper 执行
move_eef 被 deferred
batch_checkpoint
perception
下一轮再继续
```

新版统计：

```text
总耗时: 48 秒
LLM 等待: 约 44 秒
工具执行: 约 4 秒
```

## 11. 实验结论

这组测试中，batch 新版明显更快：

```text
原版: 89 秒
新版: 48 秒
节省: 41 秒
```

主要原因是：

```text
LLM 决策轮数减少
自动感知次数减少
确定性连续原子动作在同一轮 LLM 输出中批量提交
```

机器人动作本身不是主要瓶颈。工具执行只占整体时间的一小部分。

## 12. 仍然存在的问题

虽然 batch 新版更快，但 LLM 等待仍然占大头：

```text
44s / 48s ≈ 92%
```

说明下一步优化重点不是机器人动作，而是：

1. 压缩上下文。
2. 精简工具返回。
3. 减少长 JSON 在上下文中反复传递。
4. 记录毫秒级 profiling。
5. 根据任务阶段调整 checkpoint 策略。

## 13. 下一步建议

### 13.1 加毫秒级 profiling

当前轨迹只有秒级时间戳，建议新增：

```text
llm_latency_ms
tool_latency_ms
context_message_count
context_serialized_bytes
prompt_tokens
completion_tokens
```

这样可以更准确地区分：

```text
LLM 慢
工具慢
上下文变长导致慢
感知慢
```

### 13.2 压缩工具结果

尤其是 `move_eef` 的返回中包含完整轨迹数组，对 LLM 决策帮助有限。可以只保留：

```text
tool
status
target_position_camera_m
actual_duration_s
key meta
```

### 13.3 压缩 AprilTag pose

LLM 后续多数只需要：

```text
tag_id
position_camera_m
rotation_matrix
stale
timestamp_s
```

不一定需要反复看到：

```text
camera_params
corners_px
raw status
完整 action trajectory
```

### 13.4 优化 checkpoint 策略

当前 `close_gripper` 后会触发 checkpoint。安全性较好，但会增加一次 LLM 往返。

可以后续提供两种模式：

```text
conservative: close_gripper 后感知，再 lift
fast: close_gripper + lift 同批执行
```

让实验时可以对比速度与安全性。

## 14. 总结

`BatchedNewAgent` 的设计不是把机器人能力封装成高层复合工具，而是在保持原子工具的前提下，让 LLM 一次规划多个确定性原子动作，Agent 顺序执行并保留完整轨迹。

本次实验显示，这种设计能显著减少 LLM 往返：

```text
16 次 LLM 决策 -> 11 次 LLM 决策
89 秒 -> 48 秒
```

因此，batch 原子工具调用是一个符合原子化 Agent 设计理念、同时能明显提速的方向。

## 15. 单条 batch 轨迹运行示例

示例轨迹：

```text
/home/ck/RoboClaw/robotclaw_agent/trajectories/BatchedShishengTUIAgent_20260528_113205.json
```

任务：

```text
确定好tag0和tag 1的位置，然后把tag 0放到tag 1上，这是一个装配任务。
```

这条轨迹的核心执行链如下：

```text
1. detect_tags
   刷新当前相机中的 AprilTag 检测结果。

2. get_apriltag_pose(tag0) + get_apriltag_pose(tag1)
   一次 LLM 返回两个原子工具，分别获取源 tag 和目标 tag 的位姿。

3. resolve_tag_pick_place_recipe(source=0, destination=1, relation="on")
   解析任务配方，得到 assembly_on。

4. compute_tag_grasp_targets(tag0, recipe_name="assembly_on")
   根据 tag0 位姿和配方计算抓取目标：
   approach_camera_m, grasp_camera_m, lift_camera_m。

5. open_gripper + move_eef(approach_camera_m) + move_eef(grasp_camera_m)
   一次 LLM 返回三个原子工具，Agent 顺序执行，完成张爪、接近、到达抓取位。

6. close_gripper + move_eef(lift_camera_m)
   LLM 希望闭爪后直接抬起，但 close_gripper 是关键 checkpoint。
   Agent 执行 close_gripper 后，将 lift 的 move_eef 记为 tool_call_deferred，
   先执行 perception，再进入下一轮恢复。

7. SenseEnvironment(include_tags=true)
   LLM 主动请求带 tag 检测的环境感知。

8. compute_tag_place_targets(tag1, recipe_name="assembly_on")
   根据 tag1 位姿和配方计算放置目标：
   place_hover_camera_m, place_camera_m。

9. move_eef(place_hover_camera_m) + move_eef(place_camera_m) + open_gripper
   一次 LLM 返回三个原子工具，Agent 顺序执行，完成悬停、放置、释放。

10. SenseEnvironment(include_tags=true)
    最终带 tag 感知，用于确认是否可以结束。

11. FinalizeTask
    LLM 判断任务完成，Agent 通过 finalize gate 后记录 finalize。
```

这条轨迹体现了新版设计的两个重点：

```text
确定性连续动作批量提交
关键风险节点保留 checkpoint
```

其中最明显的 batch 是：

```text
open_gripper -> move_eef(approach) -> move_eef(grasp)
move_eef(place_hover) -> move_eef(place) -> open_gripper
```

同时，轨迹中仍然逐条保存了每个原子 `tool_call`，因此不会丢失可解释性。
