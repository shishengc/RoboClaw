# CoRobot mcp_control 工具实现说明

本文说明 `PYTHONPATH=src PYTHONUNBUFFERED=1 .venv/bin/python -m new_agent.batched_tui --mcp-control-tools --demo-ui` 模式下，Agent 可见的几个 CoRobot 控制工具在后端是怎么实现的。

范围只覆盖这些真实 CoRobot HTTP skill 工具：

- `reset_robot`
- `detect_tags`
- `get_apriltag_pose`
- `move_eef`
- `place_down`
- `open_gripper`
- `close_gripper`

## 工具功能总览

| 工具 | 功能描述 |
|---|---|
| `reset_robot` | 将机器人恢复到 `RuleControlTask` 配置里的安全初始位姿。它先复位左右夹爪，再调用 `G01Env.reset()` 复位双臂、头部和腰部，适合演示前初始化或任务结束后回到已知状态。 |
| `detect_tags` | 从当前 CoRobot observation 里读取配置相机的图像和内参，运行 AprilTag 检测，返回当前画面中所有可见 tag 的相机坐标系位姿，并刷新内部 tag cache。 |
| `get_apriltag_pose` | 根据 `tag_id` 读取某一个 AprilTag 的最近位姿。优先查 cache；如果 cache 不存在或过期，会重新获取 observation 并运行一次 `detect_tags`。 |
| `move_eef` | 将指定机械臂的夹爪中心 TCP 移动到给定的 camera-frame 三维目标点。内部会根据当前机器人状态计算相机到执行坐标系的变换，再生成 wrist/link7 的 30Hz EEF_ABS 轨迹并执行。 |
| `place_down` | 让指定机械臂沿配置的 `camera_place_down_axis` 下放一段距离，并可选在下放后打开夹爪。它是“相对当前位置下放 + 可选释放”的复合 primitive。 |
| `open_gripper` | 打开指定夹爪。Agent 侧会把它转换成 `/skill/gripper` 请求，并自动注入 `gripper_value=0.0`。 |
| `close_gripper` | 闭合指定夹爪。Agent 侧会把它转换成 `/skill/gripper` 请求，并自动注入 `gripper_value=1.0`；在 pick-place recipe 流程里还会做抓取顺序检查。 |

## 1. 总调用链

Agent 并不直接控制电机，也不直接读取相机。它只把 LLM 的工具调用包装成 HTTP 请求。

整体链路是：

```text
LLM tool call
-> ToolRegistry.execute(tool_name, **tool_args)
-> McpControlAgentTool.execute(...)
-> HTTP request: http://localhost:8765/skill/...
-> CoRobot RuleControlTask 对应方法
-> G01Env.get_observation() / G01Env.execute_action() / G01Env.reset()
```

Agent 工具适配层：

```text
/home/ck/RoboClaw/robotclaw_agent/src/new_agent/tools/mcp_control_tools.py
```

CoRobot 后端 skill 实现：

```text
/home/ck/RoboClaw/.a2d_pkg/corobot/policy_tasks/rule_control_task.py
```

底层动作构造：

```text
/home/ck/RoboClaw/src/mcp_control_demo/control/action_builder.py
```

AprilTag 感知：

```text
/home/ck/RoboClaw/src/mcp_control_demo/perception/apriltag.py
```

## 2. Agent 侧如何把工具变成 HTTP 请求

工具到 HTTP endpoint 的映射在：

```python
# /home/ck/RoboClaw/robotclaw_agent/src/new_agent/tools/mcp_control_tools.py
SKILL_TOOL_ENDPOINTS = {
    "get_skill_status": ("GET", "/skill/status"),
    "reset_robot": ("POST", "/skill/reset_robot"),
    "get_eef_pose": ("POST", "/skill/get_eef_pose"),
    "detect_tags": ("POST", "/skill/detect_tags"),
    "get_apriltag_pose": ("POST", "/skill/get_tag_pose"),
    "move_eef": ("POST", "/skill/move_eef"),
    "lift_eef": ("POST", "/skill/lift_eef"),
    "place_down": ("POST", "/skill/place_down"),
    "open_gripper": ("POST", "/skill/gripper"),
    "close_gripper": ("POST", "/skill/gripper"),
}
```

执行入口是：

```text
McpControlAgentTool.execute()
```

它主要做四件事：

1. 拷贝 LLM 给出的参数，形成 HTTP JSON payload。
2. 对 `open_gripper` 自动补充 `gripper_value=0.0`。
3. 对 `close_gripper` 自动补充 `gripper_value=1.0`，并可做抓取顺序检查。
4. 调 `_request()`，用 `urllib.request.urlopen()` 发 HTTP 请求到 CoRobot。

默认 CoRobot 地址是：

```text
http://localhost:8765
```

可以用环境变量或启动参数覆盖：

```bash
COROBOT_URL=http://192.168.x.x:8765
```

或：

```bash
--corobot-url http://192.168.x.x:8765
```

## 3. CoRobot 如何暴露这些 API

CoRobot 使用 `@expose_api` 装饰器标记要暴露的函数。装饰器定义在：

```text
/home/ck/RoboClaw/.a2d_pkg/corobot/utils/api_decorators.py
```

例如：

```python
@expose_api(method="POST", path="/skill/move_eef")
def move_eef(...):
    ...
```

CoRobot app 加载 `RuleControlTask` 后，会扫描所有带 `_exposed_api` 标记的方法，然后注册成 Flask route。

POST 请求参数来自 JSON body。返回值会被包装成：

```json
{
  "success": true,
  "data": {
    "...": "..."
  }
}
```

如果函数内部抛异常，则通常返回：

```json
{
  "success": false,
  "error": "..."
}
```

Agent 收到后会再转换成自己的 `ToolResult`。

## 4. RuleControlTask 初始化了什么

真实工具都在 `RuleControlTask` 里运行。初始化函数是：

```text
RuleControlTask.initialize()
```

它会创建三类核心对象：

1. `CalibrationConfig`

   从配置里加载相机内参、固定外参 `T_head_pitch_camera`、控制方向轴等。当前 `mcp_control` 使用 `dynamic_fk`，也就是每次动作前根据当前头部和腰部关节状态动态合成：

   ```text
   T_exec_camera(q) = T_base_head_pitch(q) * T_head_pitch_camera
   ```

2. `AprilTagPerceptionService`

   用于从 CoRobot observation 中拿相机图像，运行 AprilTag 检测，并按 `tag_id` 缓存结果。

3. `G01Env`

   真正的机器人环境对象。后续所有 observation、动作执行、reset 都从这里走。

## 5. reset_robot

功能描述：把机器人恢复到配置里的已知 reset pose。这个工具不做物体检测，也不根据当前任务目标判断是否完成；它只负责让机器人回到预设的安全/初始姿态。当前 agent 已经配置为任务 `FinalizeTask` 成功后自动调用一次 `reset_robot`。

### 5.1 Agent 工具

工具名：

```text
reset_robot
```

HTTP：

```text
POST /skill/reset_robot
```

典型请求：

```json
{}
```

### 5.2 CoRobot 实现入口

文件：

```text
/home/ck/RoboClaw/.a2d_pkg/corobot/policy_tasks/rule_control_task.py
```

函数：

```python
@expose_api(method="POST", path="/skill/reset_robot")
def reset_robot(self) -> dict[str, Any]:
    return self._reset_robot_pose()
```

### 5.3 内部步骤

真正执行逻辑在：

```text
RuleControlTask._reset_robot_pose()
```

流程：

```text
reset_robot()
-> _reset_robot_pose()
   -> self.stop()
   -> copy 当前 reset pose
   -> _reset_grippers_pose(target_grippers_positions)
   -> arm_reset_pose["target_grippers_positions"] = None
   -> self._env.reset(**arm_reset_pose)
```

它分两段复位：

1. 先复位夹爪。

   `_reset_grippers_pose()` 会构造一个 `Action`：

   ```python
   Action(
       timestamps=...,
       trajectory_reference_time=1.0,
       base_link="base_link",
       left_effector=[[target_left]],
       right_effector=[[target_right]],
   )
   ```

   然后调用：

   ```python
   self._env.execute_action(action, action.trajectory_reference_time)
   ```

2. 再复位双臂、头部、腰部。

   ```python
   self._env.reset(**arm_reset_pose)
   ```

   这里特意把 `target_grippers_positions` 设成 `None`，避免 `G01Env.reset()` 再处理一次夹爪。

### 5.4 reset pose 从哪里来

默认 reset pose 定义在 `rule_control_task.py` 顶部的 `DEFAULT_RESET_POSE`。

同时 `_configure_reset()` 会从 config 覆盖默认值，支持这些字段：

```text
target_grippers_positions
target_arm_joint_positions
target_head_positions
target_waist_positions
init_grippers_positions
init_arm_joint_positions
init_head_positions
init_waist_positions
```

### 5.5 返回内容

成功时大致返回：

```json
{
  "ok": true,
  "reset_on_initialize": false,
  "gripper_reset": {
    "executed": true,
    "target_grippers_positions": [1.0, 1.0],
    "duration_s": 1.0
  },
  "target_arm_joint_positions": [...],
  "target_head_positions": [...],
  "target_waist_positions": [...]
}
```

如果你看到：

```text
CoRobot HTTP 502 for reset_robot
```

这通常不是 Agent 侧失败，而是 CoRobot 后端执行 `_reset_robot_pose()` 或更底层 `G01Env.reset()` 时出错。

## 6. detect_tags

功能描述：刷新 AprilTag 感知。它从当前 observation 里拿指定相机图像和相机内参，运行 AprilTag 检测，返回当前画面中所有可见 tag 的相机坐标系位姿，并把这些结果写入 `AprilTagPerceptionService` 的 cache，供后续 `get_apriltag_pose` 使用。

### 6.1 Agent 工具

工具名：

```text
detect_tags
```

HTTP：

```text
POST /skill/detect_tags
```

典型请求：

```json
{}
```

### 6.2 CoRobot 实现入口

```python
@expose_api(method="POST", path="/skill/detect_tags")
def detect_tags(self) -> dict[str, Any]:
    obs = self._observation()
    return self._perception_service().detect_from_observation(obs)
```

### 6.3 内部步骤

调用链：

```text
detect_tags()
-> _observation()
   -> self._env.get_observation()
-> AprilTagPerceptionService.detect_from_observation(obs)
   -> _get_camera_image(observation, camera_name)
   -> _get_camera_params(observation, camera_name)
   -> detect_tags_from_image(...)
   -> 写入 self._cache[tag_id]
```

`AprilTagPerceptionService` 默认配置：

```text
camera_name = head
camera_frame = head_camera_optical
tag_family = tag25h9
tag_size_m = 0.018 或配置覆盖值
stale_after_s = 1.0
```

`detect_tags_from_image()` 使用：

```python
from pupil_apriltags import Detector
```

Detector 参数：

```python
Detector(
    families=tag_family,
    nthreads=2,
    quad_decimate=1.5,
    quad_sigma=0.8,
    refine_edges=True,
    decode_sharpening=0.25,
)
```

检测时会启用 pose 估计：

```python
detector.detect(
    gray,
    estimate_tag_pose=True,
    camera_params=(fx, fy, cx, cy),
    tag_size=float(tag_size_m),
)
```

如果相机内参里有畸变参数，会先做 undistort，再用修正后的内参估计 pose。

### 6.4 返回内容

典型返回：

```json
{
  "ok": true,
  "camera_name": "head",
  "camera_frame": "head_camera_optical",
  "tag_family": "tag25h9",
  "tag_size_m": 0.019,
  "detections": [
    {
      "tag_id": 4,
      "position_camera_m": [0.2087, -0.1860, 0.6080],
      "translation_m": [0.2087, -0.1860, 0.6080],
      "distance_m": 0.6692,
      "rotation_matrix": [[...], [...], [...]],
      "euler_rpy_deg": [46.4, 35.98, 9.37],
      "center_px": [863.69, 166.20],
      "corners_px": [[...], [...], [...], [...]],
      "timestamp_s": 1780040498.90
    }
  ]
}
```

注意：这里输出的是相机坐标系下的 tag pose，不是 `base_link` 坐标系。

## 7. get_apriltag_pose

功能描述：读取单个 tag 的位姿。它不是重新检测所有物体的通用感知工具，而是“按 `tag_id` 查询 AprilTag pose”的工具；它会优先使用最近一次 `detect_tags` 写入的 cache，cache 不可用时再自动触发一次新的检测。

### 7.1 Agent 工具

工具名：

```text
get_apriltag_pose
```

HTTP：

```text
POST /skill/get_tag_pose
```

典型请求：

```json
{
  "tag_id": 4
}
```

可选参数：

```json
{
  "tag_id": 4,
  "allow_stale": true
}
```

### 7.2 CoRobot 实现入口

```python
@expose_api(method="POST", path="/skill/get_tag_pose")
def get_tag_pose(self, tag_id: int, allow_stale: bool = False) -> dict[str, Any]:
    result = self._perception_service().get_tag_pose(int(tag_id), allow_stale=allow_stale)
    if result.get("ok"):
        return result
    obs = self._observation()
    refresh = self._perception_service().detect_from_observation(obs)
    if not refresh.get("ok"):
        return refresh
    return self._perception_service().get_tag_pose(int(tag_id), allow_stale=allow_stale)
```

### 7.3 内部步骤

逻辑是：

```text
get_tag_pose(tag_id)
-> 先查 AprilTagPerceptionService._cache[tag_id]
-> 如果 cache 命中且没有过期，直接返回
-> 如果没有命中或过期，重新 self._env.get_observation()
-> detect_from_observation(obs)
-> 再查一次 _cache[tag_id]
```

这里的 cache freshness 由 `stale_after_s` 控制，默认 1 秒。

如果 cache 中有 tag 但超过 1 秒，且 `allow_stale=False`，会返回：

```json
{
  "ok": false,
  "message": "tag_id 4 is stale",
  "tag_id": 4,
  "age_s": 79.79,
  "stale_after_s": 1.0,
  "last_detection": {...}
}
```

如果重新 detect 后没有看到这个 tag，也会继续失败。

### 7.4 使用建议

对于演示任务，最好优先用 `prepare_tag_pick_place` 或先 `detect_tags`，再使用检测结果里的 tag pose。这样可以减少 `get_apriltag_pose` 因 stale 或重复 detect 带来的延迟。

## 8. move_eef

功能描述：移动指定机械臂末端。对外输入的是夹爪中心 TCP 在相机坐标系下的目标点；CoRobot 内部会把它转换到底层控制器需要的 wrist/link7 目标轨迹。它只执行几何运动，不检查目标点是不是来自可见 tag，也不判断是否会碰撞或是否抓住物体。

### 8.1 Agent 工具

工具名：

```text
move_eef
```

HTTP：

```text
POST /skill/move_eef
```

典型请求：

```json
{
  "arm": "right",
  "camera_frame": "head_camera_optical",
  "target_position_camera_m": [0.20, -0.10, 0.45],
  "duration_s": 1.0
}
```

可选：

```json
{
  "target_orientation_camera_xyzw": [0.0, 0.0, 0.0, 1.0],
  "gripper_value": 1.0
}
```

`control_hz` 和 `control_frequency_hz` 不允许传。控制频率固定 30Hz。

### 8.2 CoRobot 实现入口

```python
@expose_api(method="POST", path="/skill/move_eef")
def move_eef(
    self,
    arm: str,
    target_position_camera_m: list[float],
    camera_frame: str = "head_camera_optical",
    target_orientation_camera_xyzw: list[float] | None = None,
    duration_s: float = 1.0,
    gripper_value: float | None = None,
    control_hz: float | None = None,
    control_frequency_hz: float | None = None,
) -> dict[str, Any]:
    self._reject_control_frequency(control_hz, control_frequency_hz)
    obs = self._observation()
    calibration = self._calibration_for_observation(obs)
    action, meta = build_move_eef_action(...)
    self._execute(action, meta["actual_duration_s"])
    return {"action": action, "meta": meta}
```

### 8.3 内部步骤

调用链：

```text
move_eef()
-> _reject_control_frequency()
-> _observation()
   -> self._env.get_observation()
-> _calibration_for_observation(obs)
   -> 若没有固定 T_exec_camera，则根据 head/waist 当前关节状态动态计算
-> build_move_eef_action(...)
-> _execute(action, actual_duration_s)
   -> self._env.execute_action(action, wait_action_time=actual_duration_s)
```

`build_move_eef_action()` 在：

```text
/home/ck/RoboClaw/src/mcp_control_demo/control/action_builder.py
```

关键步骤：

1. 读取当前 EEF pose。

   ```text
   current wrist/link7 pose
   ```

2. 把当前 wrist/link7 pose 转成夹爪中心 TCP。

   ```text
   gripper_center = wrist_position + R_wrist * gripper_center_offset_link7_m
   ```

   固定偏移：

   ```text
   gripper_center_offset_link7_m = [0.0, 0.0, 0.14308]
   ```

3. 把起点和目标点从 camera frame 转成 exec frame。

   ```text
   target_exec = T_exec_camera * target_camera
   ```

4. 如果没有给 `target_orientation_camera_xyzw`，默认保持当前 EEF 姿态。

5. 因为底层 A2D 控制的是 wrist/link7，不是夹爪中心，所以再把目标夹爪中心转回 wrist/link7 目标。

   ```text
   wrist_target = desired_gripper_center - R_wrist * gripper_center_offset_link7_m
   ```

6. 按 30Hz 插值生成 EEF_ABS rows。

7. 构造 CoRobot action：

   ```python
   {
       "timestamps": ...,
       "trajectory_reference_time": actual_duration_s,
       "base_link": exec_frame,
       "right_arm": {
           "kind": "EEF_ABS",
           "values": [
               [x, y, z, roll, pitch, yaw],
               ...
           ]
       }
   }
   ```

8. 调用：

   ```python
   self._env.execute_action(action, wait_action_time=actual_duration_s)
   ```

### 8.4 30Hz 固定规则

定义在：

```text
/home/ck/RoboClaw/src/mcp_control_demo/control/timing.py
```

规则：

```text
CONTROL_HZ = 30.0
num_steps = max(1, ceil(duration_s * 30))
actual_duration_s = num_steps / 30
```

所以 `duration_s=1.0` 会生成 30 个点，`duration_s=0.5` 会生成 15 个点。

### 8.5 返回内容

返回包含两部分：

```json
{
  "action": {
    "timestamps": 1780...,
    "trajectory_reference_time": 1.0,
    "base_link": "base_link",
    "right_arm": {
      "kind": "EEF_ABS",
      "values": [[...], [...]]
    }
  },
  "meta": {
    "arm": "right",
    "camera_frame": "head_camera_optical",
    "exec_frame": "base_link",
    "target_position_camera_m": [0.20, -0.10, 0.45],
    "target_position_exec_m": [...],
    "target_wrist_position_exec_m": [...],
    "control_hz": 30.0,
    "num_steps": 30,
    "requested_duration_s": 1.0,
    "actual_duration_s": 1.0
  }
}
```

注意：这个返回里可能包含完整轨迹数组，比较长。对 LLM 下一轮决策不一定都有必要。

## 9. place_down

功能描述：基于当前夹爪中心位置做相对下放。它不是“放到某个 tag 上”的全流程工具；目标 tag 的放置点需要先由 `prepare_tag_pick_place` 或 `compute_tag_place_targets` 算好，然后用 `move_eef(place_hover_camera_m)` 和 `move_eef(place_camera_m)` 到位。`place_down` 更适合简单地沿配置方向下压/下放并释放。

### 9.1 Agent 工具

工具名：

```text
place_down
```

HTTP：

```text
POST /skill/place_down
```

典型请求：

```json
{
  "arm": "right",
  "camera_frame": "head_camera_optical",
  "down_distance_m": 0.08,
  "duration_s": 1.0,
  "open_after_down": true
}
```

### 9.2 CoRobot 实现入口

```python
@expose_api(method="POST", path="/skill/place_down")
def place_down(
    self,
    arm: str,
    down_distance_m: float,
    camera_frame: str = "head_camera_optical",
    duration_s: float = 1.0,
    open_after_down: bool = True,
    control_hz: float | None = None,
    control_frequency_hz: float | None = None,
) -> dict[str, Any]:
    self._reject_control_frequency(control_hz, control_frequency_hz)
    obs = self._observation()
    calibration = self._calibration_for_observation(obs)
    actions, meta = build_place_down_sequence(...)
    self._execute_sequence(actions)
    return {"actions": actions, "meta": meta}
```

### 9.3 内部步骤

调用链：

```text
place_down()
-> _observation()
-> _calibration_for_observation(obs)
-> build_place_down_sequence(...)
   -> _build_offset_eef_action(...)
      -> 沿 calibration.camera_place_down_axis 移动 down_distance_m
      -> build_move_eef_between_camera_points(...)
   -> 如果 open_after_down=True:
      -> build_gripper_action(... gripper_value=OPEN_GRIPPER)
-> _execute_sequence(actions)
   -> 对每个 action 依次 self._env.execute_action(...)
```

`camera_place_down_axis` 默认来自 calibration 配置，代码默认值是：

```text
[0.0, 1.0, 0.0]
```

所以 `place_down` 不是直接按 base_link 的 z 轴下放，而是沿配置定义的 camera-frame 下放方向移动。

### 9.4 和 move_eef 的关系

`place_down` 本质上是一个小复合 primitive：

```text
move_eef offset down
+ optional open_gripper
```

它内部也会生成 EEF_ABS action，并通过 `G01Env.execute_action()` 执行。

如果 `open_after_down=True`，会多执行一个夹爪 action。

## 10. open_gripper

功能描述：打开指定机械臂夹爪。它只控制夹爪开合，不移动机械臂，也不检查夹爪里是否有物体。为了保持另一个夹爪不变，CoRobot 会从当前 observation 里读取当前左右夹爪状态。

### 10.1 Agent 工具

工具名：

```text
open_gripper
```

HTTP：

```text
POST /skill/gripper
```

LLM 通常只需要给：

```json
{
  "arm": "right",
  "duration_s": 0.5
}
```

Agent 侧会自动补：

```json
{
  "gripper_value": 0.0
}
```

### 10.2 CoRobot 实现入口

```python
@expose_api(method="POST", path="/skill/gripper")
def gripper(
    self,
    arm: str,
    gripper_value: float,
    duration_s: float = 0.5,
    control_hz: float | None = None,
    control_frequency_hz: float | None = None,
) -> dict[str, Any]:
    self._reject_control_frequency(control_hz, control_frequency_hz)
    obs = self._observation()
    action, meta = build_gripper_action(obs, arm=arm, gripper_value=gripper_value, duration_s=duration_s)
    self._execute(action, meta["actual_duration_s"])
    return {"action": action, "meta": meta}
```

### 10.3 内部步骤

`build_gripper_action()` 在 `action_builder.py` 里。

流程：

```text
build_gripper_action()
-> validate arm is left/right
-> value = clip(gripper_value, 0.0, 1.0)
-> make_timing(duration_s)
-> 读取当前左右 gripper command
-> 只修改指定 arm 的 target，另一个 arm 保持当前值
-> 对左右 gripper 分别插值
-> 构造 Action
```

open 的含义：

```text
OPEN_GRIPPER = 0.0
```

构造出来的 action 类似：

```json
{
  "timestamps": 1780...,
  "trajectory_reference_time": 0.5,
  "base_link": "base_link",
  "left_effector": [[current_left], ...],
  "right_effector": [[0.0], ...]
}
```

如果 `arm="right"`，只打开右夹爪，左夹爪保持当前状态。

## 11. close_gripper

功能描述：闭合指定机械臂夹爪。它只执行夹爪闭合，不会自己移动到抓取点。当前 Agent 在 recipe 流程里会额外检查：必须先让对应 arm 移动到 `grasp_camera_m` 附近，才允许闭爪。

### 11.1 Agent 工具

工具名：

```text
close_gripper
```

HTTP：

```text
POST /skill/gripper
```

LLM 通常只需要给：

```json
{
  "arm": "right",
  "duration_s": 0.5
}
```

Agent 侧会自动补：

```json
{
  "gripper_value": 1.0
}
```

### 11.2 CoRobot 实现入口

`close_gripper` 和 `open_gripper` 共用同一个 CoRobot endpoint：

```text
POST /skill/gripper
```

区别只在 Agent 自动传入的 `gripper_value`。

close 的含义：

```text
CLOSE_GRIPPER = 1.0
```

### 11.3 Agent 侧额外顺序检查

当前 Agent 侧有一个 `McpControlRecipeState`，用于避免 LLM 跳过抓取动作顺序。

例如 `prepare_tag_pick_place` 或 `compute_tag_grasp_targets` 计算出：

```text
grasp_camera_m
lift_camera_m
```

之后如果 LLM 直接调用：

```text
move_eef(lift_camera_m)
```

但还没有先移动到 `grasp_camera_m` 并 `close_gripper`，Agent 会拒绝这个 `move_eef`。

同理，如果 LLM 调 `close_gripper`，但指定 arm 最近一次 `move_eef` 没有到达 `grasp_camera_m`，Agent 也会拒绝。

这部分检查发生在 Agent 侧：

```text
/home/ck/RoboClaw/robotclaw_agent/src/new_agent/tools/mcp_control_tools.py
McpControlRecipeState.validate_close_gripper()
McpControlRecipeState.validate_move_eef()
```

CoRobot 本身只负责按 `gripper_value` 执行夹爪动作。

## 12. 工具耗时主要来自哪里

这些工具的耗时来源不同：

| 工具 | 主要耗时 |
|---|---|
| `reset_robot` | `G01Env.reset()` 和夹爪 reset action |
| `detect_tags` | `G01Env.get_observation()`、图像读取、AprilTag 检测 |
| `get_apriltag_pose` | cache 命中时很快；未命中或 stale 时会额外跑一次 `detect_tags` |
| `move_eef` | observation、动态 FK、轨迹生成、`G01Env.execute_action()` 等待动作执行完成 |
| `place_down` | 一个下放动作；如果 `open_after_down=True`，再加一个夹爪动作 |
| `open_gripper` | 夹爪 action 的实际执行时间，默认约 0.5s |
| `close_gripper` | 夹爪 action 的实际执行时间，默认约 0.5s |

Agent/UI 本身对这些工具的额外开销通常很小。真实等待主要发生在 CoRobot observation、AprilTag 检测和 `G01Env.execute_action()`。

## 13. 演示任务里的推荐用法

对于 AprilTag pick-and-place 演示，推荐流程是：

```text
prepare_tag_pick_place(source_tag_id, destination_tag_id, relation)
-> open_gripper
-> move_eef(approach_camera_m)
-> move_eef(grasp_camera_m)
-> close_gripper
-> move_eef(lift_camera_m)
-> move_eef(place_hover_camera_m)
-> move_eef(place_camera_m)
-> open_gripper
```

这样做的好处：

1. `prepare_tag_pick_place` 会一次性 fresh detect 并计算抓取/放置目标，减少 LLM 多轮 `detect_tags + get_apriltag_pose + compute_*`。
2. `move_eef` 和 `gripper` 都走 CoRobot 的 deterministic skill API，LLM 不直接处理底层轨迹。
3. Agent 侧的顺序检查可以防止跳过“到达抓取点后再闭爪”这个关键步骤。

## 14. detect_tags 具体怎么检测到数据

`detect_tags` 的核心不是从数据库读 tag，而是从机器人当前 observation 中取最新相机图像，然后实时运行 AprilTag 检测。

完整数据流：

```text
Agent detect_tags
-> POST /skill/detect_tags {}
-> RuleControlTask.detect_tags()
-> self._observation()
   -> self._env.get_observation()
-> AprilTagPerceptionService.detect_from_observation(obs)
-> _get_camera_image(obs, camera_name)
-> _get_camera_params(obs, camera_name) 或 fallback intrinsics
-> detect_tags_from_image(image, camera_params, ...)
-> pupil_apriltags.Detector.detect(...)
-> detections 写入 self._cache[tag_id]
-> 返回 detections
```

### 14.1 图像从哪里来

代码读取路径是：

```python
images = (_get(observation, "observation") or observation).images
image = images[camera_name]
```

默认：

```text
camera_name = head
```

也就是默认从 CoRobot observation 的 `head` 相机图像里检测 tag。

如果 `head` 图像不存在，`detect_from_observation()` 会直接返回：

```json
{
  "ok": false,
  "message": "observation image not found: head"
}
```

### 14.2 相机内参从哪里来

代码先读：

```python
camera_params = observation.observation.camera_params[camera_name]
```

如果 observation 里没有这个相机的内参，会使用初始化 `AprilTagPerceptionService` 时传入的 fallback：

```text
fallback_camera_params = CalibrationConfig.intrinsics
```

也就是配置文件 `mcp_control.intrinsics` 里的内参。

内参支持两种常见形式：

```json
{
  "fx": 640.2,
  "fy": 640.3,
  "cx": 644.1,
  "cy": 362.0
}
```

或：

```json
{
  "K": {
    "data": [fx, 0, cx, 0, fy, cy, 0, 0, 1]
  }
}
```

如果内参完全缺失，AprilTag 仍可能检测到 tag 的像素角点，但无法可靠估计 3D pose；当前实现会报：

```text
camera_params are required for AprilTag pose estimation
```

这个异常会被 CoRobot Flask handler 包装成 HTTP 500 / `success=false`。

### 14.3 图像如何变成 tag pose

`detect_tags_from_image()` 做这些步骤：

1. 把 observation 里的图像转成 numpy array。
2. 根据相机内参构造 camera matrix。
3. 如果内参里有畸变参数 `D` / `distortion_coefficients`，先对图像 undistort，并使用矫正后的内参。
4. 转成灰度图。
5. 创建 `pupil_apriltags.Detector`。
6. 调用：

```python
detector.detect(
    gray,
    estimate_tag_pose=True,
    camera_params=(fx, fy, cx, cy),
    tag_size=float(tag_size_m),
)
```

其中：

```text
tag_family = tag25h9
tag_size_m = 配置值，默认 0.018 或 0.019 左右
```

`pupil_apriltags` 会基于 tag 四个角点、相机内参和 tag 实际尺寸估计 3D pose，返回：

```text
det.pose_t  # tag 在相机坐标系下的平移
det.pose_R  # tag 在相机坐标系下的旋转矩阵
```

当前实现只保留 `pose_t` 和 `pose_R` 都存在的检测结果。

### 14.4 detect_tags 返回的 detections 是什么

每个 detection 的核心字段：

| 字段 | 实际含义 |
|---|---|
| `tag_id` | AprilTag 编码 ID，例如 0、1、2、3、4。 |
| `tag_family` | tag family，例如 `tag25h9`。 |
| `camera_name` | 图像来自哪个相机，默认 `head`。 |
| `camera_frame` | pose 所属坐标系，默认 `head_camera_optical`。 |
| `tag_size_m` | 估计 pose 时使用的 tag 边长，单位米。 |
| `position_camera_m` | tag 中心在相机坐标系下的位置 `[x, y, z]`，单位米。 |
| `translation_m` | 与 `position_camera_m` 同义，保留是为了兼容不同调用方。 |
| `distance_m` | tag 中心到相机原点的欧氏距离。 |
| `rotation_matrix` | tag 坐标系到相机坐标系的 3x3 旋转矩阵。 |
| `euler_rpy_deg` | 旋转矩阵转成 roll/pitch/yaw，单位度，只用于理解姿态。 |
| `center_px` | tag 中心在图像中的像素位置 `[u, v]`。 |
| `corners_px` | tag 四个角点在图像中的像素坐标。 |
| `camera_params` | 本次 pose 估计实际使用的 `fx/fy/cx/cy`，以及是否 undistorted。 |
| `timestamp_s` | 当前实现写入 cache 时的时间戳，用于判断 stale。 |

## 15. 每个工具输入和输出变量的实际含义

### 15.1 reset_robot

输入：

| 变量 | 含义 |
|---|---|
| 无 | 请求体通常是 `{}`。reset pose 来自 `RuleControlTask` 默认值和配置文件，不由 LLM 每次传入。 |

输出：

| 变量 | 含义 |
|---|---|
| `ok` | CoRobot 逻辑是否认为 reset 成功。 |
| `reset_on_initialize` | 该 PolicyTask 初始化时是否配置为自动 reset。 |
| `gripper_reset.executed` | 是否先执行了夹爪 reset action。 |
| `gripper_reset.target_grippers_positions` | 左右夹爪 reset 目标值。 |
| `gripper_reset.duration_s` | 夹爪 reset action 的执行时长。 |
| `target_arm_joint_positions` | 双臂关节 reset 目标。通常 14 个值，左右臂各 7 个。 |
| `target_head_positions` | 头部关节 reset 目标。 |
| `target_waist_positions` | 腰部关节 reset 目标。 |

### 15.2 detect_tags

输入：

| 变量 | 含义 |
|---|---|
| 无 | 请求体通常是 `{}`。相机名、tag family、tag size、stale 时间从 CoRobot 配置读取。 |

输出：

| 变量 | 含义 |
|---|---|
| `ok` | 是否成功读取图像并完成检测流程。注意 `ok=true` 但 `detections=[]` 表示流程成功但画面中没看到 tag。 |
| `camera_name` | 使用的相机名，默认 `head`。 |
| `camera_frame` | 返回 pose 的坐标系，默认 `head_camera_optical`。 |
| `tag_family` | 检测的 tag family。 |
| `tag_size_m` | 用于 3D pose 估计的 tag 实际尺寸。 |
| `detections` | 当前画面中检测到的 tag 列表；每个元素包含 `tag_id`、相机坐标位置、旋转、像素角点等。 |

### 15.3 get_apriltag_pose

输入：

| 变量 | 含义 |
|---|---|
| `tag_id` | 要查询的 AprilTag ID。 |
| `allow_stale` | 是否允许返回过期 cache。默认 `false`；演示中一般不要打开，除非操作者明确接受使用旧 pose。 |

输出：

| 变量 | 含义 |
|---|---|
| `ok` | 是否成功得到该 tag 的 pose。 |
| `tag_id` | 返回的 tag ID。 |
| `age_s` | 这条检测结果距离当前时间过去了多少秒。 |
| `stale` | 是否超过 `stale_after_s`。 |
| `stale_after_s` | freshness 阈值，默认 1 秒。 |
| `last_detection` | 如果失败原因是 stale，会带上最后一次检测数据，便于调试。 |
| `position_camera_m` / `translation_m` | tag 中心在相机坐标系下的位置。 |
| `rotation_matrix` / `euler_rpy_deg` | tag 姿态。 |
| `center_px` / `corners_px` | tag 在图像中的像素位置。 |

### 15.4 move_eef

输入：

| 变量 | 含义 |
|---|---|
| `arm` | `"left"` 或 `"right"`，指定移动哪只机械臂。 |
| `target_position_camera_m` | 目标夹爪中心 TCP 在相机坐标系下的位置 `[x, y, z]`，单位米。 |
| `camera_frame` | 输入目标点所属相机坐标系，默认 `head_camera_optical`。当前实现只支持配置里的相机 frame。 |
| `target_orientation_camera_xyzw` | 可选。目标 wrist/link7 姿态，四元数 `[x, y, z, w]`，相机坐标系。缺省时保留当前 EEF 姿态；如果当前姿态也不可用，会退化到单位四元数。 |
| `duration_s` | 期望运动时长。实际会按 30Hz 向上取整为 `actual_duration_s`。 |
| `gripper_value` | 可选。若提供，会在同一个 action 中附带夹爪目标值，`0.0=open`，`1.0=close`。通常演示中更推荐单独用 `open_gripper` / `close_gripper`。 |

输出：

| 变量 | 含义 |
|---|---|
| `action.timestamps` | 构造 action 的时间戳。 |
| `action.trajectory_reference_time` | 底层执行该 action 的参考时长。 |
| `action.base_link` | action 中轨迹数值所属执行坐标系，通常 `base_link`。 |
| `action.left_arm` / `action.right_arm` | 被控制 arm 的 EEF_ABS 轨迹。`values` 每行通常是 `[x, y, z, roll, pitch, yaw]`。 |
| `action.left_effector` / `action.right_effector` | 如果本次 action 附带 `gripper_value`，这里会包含夹爪轨迹。 |
| `meta.arm` | 实际控制的 arm。 |
| `meta.camera_frame` | 输入目标使用的 camera frame。 |
| `meta.exec_frame` | 执行坐标系。 |
| `meta.start_position_camera_m` | 本次规划的夹爪中心起点，camera frame。 |
| `meta.target_position_camera_m` | 输入目标点，camera frame。 |
| `meta.target_position_exec_m` | 目标夹爪中心点转换到 exec frame 后的位置。 |
| `meta.start_wrist_position_exec_m` | 起点 wrist/link7 在 exec frame 下的位置。 |
| `meta.target_wrist_position_exec_m` | 目标 wrist/link7 在 exec frame 下的位置。 |
| `meta.gripper_center_offset_link7_m` | 夹爪中心 TCP 相对 wrist/link7 的固定偏移。 |
| `meta.control_hz` | 固定 30Hz。 |
| `meta.num_steps` | 轨迹采样点数。 |
| `meta.requested_duration_s` | 请求的 `duration_s`。 |
| `meta.actual_duration_s` | 按 30Hz 对齐后的实际执行时长。 |

### 15.5 place_down

输入：

| 变量 | 含义 |
|---|---|
| `arm` | `"left"` 或 `"right"`。 |
| `down_distance_m` | 沿 `camera_place_down_axis` 移动的距离，单位米。 |
| `camera_frame` | 当前动作使用的相机 frame，默认 `head_camera_optical`。 |
| `duration_s` | 下放动作时长。 |
| `open_after_down` | 下放后是否自动打开夹爪，默认 `true`。 |

输出：

| 变量 | 含义 |
|---|---|
| `actions` | 一个或两个 action。第一个是下放 EEF_ABS action；如果 `open_after_down=true`，第二个是 gripper open action。 |
| `meta.arm` | 控制的 arm。 |
| `meta.camera_frame` | 使用的 camera frame。 |
| `meta.segments` | 每段 action 的 meta。第一段通常带 `primitive=place_down`、`axis_camera`、`distance_m`；第二段是打开夹爪 meta。 |

### 15.6 open_gripper

输入：

| 变量 | 含义 |
|---|---|
| `arm` | `"left"` 或 `"right"`，指定打开哪个夹爪。 |
| `duration_s` | 夹爪打开动作时长，默认 0.5 秒。 |
| `gripper_value` | LLM 不需要传。Agent 自动注入 `0.0`。 |

输出：

| 变量 | 含义 |
|---|---|
| `action.left_effector` / `action.right_effector` | 左右夹爪轨迹。指定 arm 会插值到 `0.0`，另一侧保持当前值。 |
| `meta.arm` | 控制的 arm。 |
| `meta.gripper_value` | 实际夹爪目标值，打开为 `0.0`。 |
| `meta.left_target` / `meta.right_target` | 左右夹爪目标值。 |
| `meta.control_hz` / `meta.num_steps` / `meta.actual_duration_s` | 30Hz 时序信息。 |

### 15.7 close_gripper

输入：

| 变量 | 含义 |
|---|---|
| `arm` | `"left"` 或 `"right"`，指定闭合哪个夹爪。 |
| `duration_s` | 夹爪闭合动作时长，默认 0.5 秒。 |
| `gripper_value` | LLM 不需要传。Agent 自动注入 `1.0`。 |

输出：

| 变量 | 含义 |
|---|---|
| `action.left_effector` / `action.right_effector` | 左右夹爪轨迹。指定 arm 会插值到 `1.0`，另一侧保持当前值。 |
| `meta.arm` | 控制的 arm。 |
| `meta.gripper_value` | 实际夹爪目标值，闭合为 `1.0`。 |
| `meta.left_target` / `meta.right_target` | 左右夹爪目标值。 |
| `meta.control_hz` / `meta.num_steps` / `meta.actual_duration_s` | 30Hz 时序信息。 |

## 16. 工具是否需要当前 observation，以及变量不可见时怎么办

这些工具不都需要“当前所有物体”和“当前机械臂位姿”。它们需要的状态粒度不同。

| 工具 | 是否调用 `G01Env.get_observation()` | 需要看到物体/tag 吗 | 需要机器人自身状态吗 | 变量缺失时的行为 |
|---|---:|---:|---:|---|
| `reset_robot` | 否 | 否 | 不依赖当前位姿；依赖 reset 配置和 `G01Env.reset()` 可用 | 如果 env 未初始化会报错；如果 reset 底层失败，Agent 看到 HTTP 错误或工具失败。 |
| `detect_tags` | 是 | 需要 tag 出现在配置相机画面里 | 不需要机械臂末端位姿 | 相机图像缺失返回 `ok=false`；内参缺失会导致 pose 估计错误；tag 不在画面里时通常 `ok=true, detections=[]`。 |
| `get_apriltag_pose` | cache 命中时不需要；cache miss/stale 时需要 | 需要目标 `tag_id` 可见，除非 `allow_stale=true` | 不需要机械臂末端位姿 | 未见过 tag 返回 `tag_id X not found`；过期返回 `tag_id X is stale`；刷新后仍不可见则失败。 |
| `move_eef` | 是 | 不需要。它只相信输入的 `target_position_camera_m` | 需要 head/waist 状态计算动态 `T_exec_camera`；最好有当前 EEF pose/orientation | head/waist 缺失会导致动态标定失败；当前 EEF pose 缺失时实现可能退化为从目标点开始规划，动作不可靠，应先 `get_eef_pose`/`reset_robot`/检查 observation。 |
| `place_down` | 是 | 不需要 | 需要当前 EEF pose/orientation 和 head/waist 状态；若自动打开夹爪，还要当前 gripper state | 当前 EEF pose 缺失会直接报 `current arm EEF pose is unavailable`；动态标定缺失也会失败。 |
| `open_gripper` | 是 | 不需要 | 需要当前 gripper state 来保持另一侧夹爪不变 | 如果 gripper state 缺失，代码默认按 open 值作为当前值继续构造 action；这能执行，但无法精确保持未知侧状态。 |
| `close_gripper` | 是 | 不需要；但 Agent recipe 检查需要知道之前是否到达 `grasp_camera_m` | 需要当前 gripper state 来保持另一侧夹爪不变 | gripper state 缺失时默认当前值为 open；recipe 顺序不满足时 Agent 会拒绝闭爪。 |

### 16.1 是否需要观测当前所有物体

不需要。

当前这套 mcp_control 工具不是通用物体识别系统，只认识 AprilTag 和机器人自身状态：

```text
detect_tags / get_apriltag_pose:
  只关心可见 AprilTag。

move_eef / place_down / gripper:
  不识别物体，只执行输入的几何目标或夹爪目标。

reset_robot:
  不识别物体。
```

因此，`move_eef` 不会检查“目标点附近有没有物体”，也不会检查“tag 是否仍在原地”。如果目标点来自旧的 tag pose，机器人仍会尝试按这个旧坐标移动。

### 16.2 是否需要观测机械臂自身位置

动作类工具需要程度不同：

- `move_eef` 需要当前机器人 observation 来计算动态相机外参，并尽量从当前 EEF pose 规划到目标点。
- `place_down` 更依赖当前 EEF pose，因为它是相对当前位置下放。
- `open_gripper` / `close_gripper` 需要当前 gripper state 来让未指定的另一侧夹爪保持原状态。
- `reset_robot` 不需要先知道当前 arm pose，它直接走 reset。

### 16.3 有些变量看不到怎么办

按变量类型处理：

1. tag 看不到。

   现象：

   ```text
   detections=[] 或 required tag ids are not visible
   ```

   处理：

   - 不要直接 `move_eef` 到旧坐标。
   - 调整 tag 到 `head` 相机可见区域。
   - 重新 `detect_tags` 或重新调用 `prepare_tag_pick_place`。
   - 只有操作者明确允许时才使用 `allow_stale=true`。

2. tag pose stale。

   现象：

   ```text
   tag_id X is stale
   ```

   处理：

   - 重新 `detect_tags`。
   - 如果重新检测后目标 tag 不在 `detections`，应让操作者调整视野。
   - 演示流程中优先使用 `prepare_tag_pick_place`，它会 fresh detect 并在缺 tag 时直接失败。

3. 相机图像看不到。

   现象：

   ```text
   observation image not found: head
   ```

   处理：

   - 调 `/skill/status` 看 CoRobot env 是否 ready。
   - 调 `/skill/camera_views?include_images=false` 检查相机是否有图像。
   - 检查 CoRobot/G01Env/camera pipeline 是否启动。

4. 相机内参缺失。

   现象：

   ```text
   camera_params are required for AprilTag pose estimation
   ```

   处理：

   - 检查 observation 是否带 `camera_params[head]`。
   - 检查 `rule_control_task_config.yml` 的 `mcp_control.intrinsics` 是否配置。
   - 没有内参时不应该执行基于 tag 3D pose 的 pick-place。

5. 机器人 head/waist 状态缺失。

   现象：

   ```text
   dynamic_fk requires current head_joint_states and waist_joint_states in observation
   ```

   处理：

   - 这会影响 camera frame 到 base_link 的变换。
   - 应检查 `G01Env.get_observation()` 的 states 是否正常。
   - 可先 `reset_robot`，再检查 `/skill/status` 和 `get_eef_pose`。

6. 当前 EEF pose 缺失。

   现象：

   ```text
   current right EEF pose is unavailable in base_link
   ```

   处理：

   - `place_down` 会直接失败，因为它必须知道当前位置才能相对下放。
   - `move_eef` 的实现有退化路径，但不建议依赖；更安全的是先检查 `get_eef_pose` 或 reset。

7. gripper state 缺失。

   处理：

   - `build_gripper_action()` 会把缺失的 gripper state 当作 open。
   - 这能让 action 继续生成，但如果另一侧夹爪真实状态未知，返回结果不能证明另一侧状态被保持。

总结：如果感知或机器人状态变量缺失，Agent 应该走“重新感知 / reset / 人工调整视野 / 检查 CoRobot 状态”的恢复路径，而不是让 LLM 猜坐标继续运动。

## 17. 关键文件索引

```text
Agent HTTP 工具适配:
/home/ck/RoboClaw/robotclaw_agent/src/new_agent/tools/mcp_control_tools.py

Agent recipe 参数:
/home/ck/RoboClaw/robotclaw_agent/src/new_agent/tools/mcp_control_recipes.py

Agent grasp/place target 计算:
/home/ck/RoboClaw/robotclaw_agent/src/new_agent/tools/mcp_control_task_helpers.py

CoRobot skill API:
/home/ck/RoboClaw/.a2d_pkg/corobot/policy_tasks/rule_control_task.py

CoRobot API 装饰器和 Flask handler:
/home/ck/RoboClaw/.a2d_pkg/corobot/utils/api_decorators.py

动作构造:
/home/ck/RoboClaw/src/mcp_control_demo/control/action_builder.py

30Hz timing:
/home/ck/RoboClaw/src/mcp_control_demo/control/timing.py

AprilTag 感知:
/home/ck/RoboClaw/src/mcp_control_demo/perception/apriltag.py

标定配置解析:
/home/ck/RoboClaw/src/mcp_control_demo/calibration/config.py
```
