# RuleControlTask / mcp_control_demo Test Guide

本文档合并记录今天的代码改动、当前配置链路，以及从离线检查到真机小步移动的测试步骤。默认在 RoboClaw 仓库根目录执行：

```bash
cd /home/ck/RoboClaw
```

## 0. 今日改动总结

核心迁移：

- 新增 CoRobot 内部 PolicyTask：`corobot.policy_tasks.rule_control_task.RuleControlTask`。
- `RuleControlTask` 现在是统一管理入口，持有 `G01Env`，通过 CoRobot app 生命周期加载和重置。
- `mcp_control_demo` 仍保留 AprilTag、calibration、30Hz action builder 等外部功能模块。
- 旧入口 `mcp_control_demo.corobot_skill_task.skill_task.McpControlSkillTask` 保留为兼容 shim，继承 `RuleControlTask`。

接口变化：

- 保持已有 `/skill/...` HTTP API 不变。
- 新增 `/skill/get_eef_pose`，用于读取当前左/右臂 EEF 位姿。
- MCP tool schema 同步新增 `get_eef_pose`。
- 没有保留 `move_eef_delta_exec`；小步移动仍通过 `move_eef` 和用户提供的 calibration 完成。

测试辅助：

- 新增 `scripts/test_small_move_eef.sh`。
- 脚本会先读取当前 EEF 的 camera-frame 坐标，再加一个小的 camera-frame delta，默认只打印 payload；设置 `EXECUTE_MOVE=1` 才执行。

当前重要结论：

- URDF 能算头部 link 相对 `base_link` 的位姿，但当前 `G1.urdf` 没有相机/optical link，不能仅凭 URDF 得到真实相机光心外参。
- 真正的相机外参仍需要 `T_exec_camera`。当前可用 identity 仅用于小范围链路测试。

## 1. 当前配置链路

推荐启动命令：

```bash
make run_corobot_app COROBOT_APP_ARGS="--config ./src/mcp_control_demo/config/rule_control_app_config.yaml"
```

实际加载链路：

```text
src/mcp_control_demo/config/rule_control_app_config.yaml
  -> .a2d_pkg/corobot/config/rule_control_task_config.yml
    -> src/mcp_control_demo/config/mcp_control_calibration.yaml
```

当前 app config 应为：

```yaml
default_policytask:
  policy_module: corobot.policy_tasks.rule_control_task
  class_name: RuleControlTask
  config_path: /home/ck/RoboClaw/.a2d_pkg/corobot/config/rule_control_task_config.yml
```

当前 task config 应至少包含：

```yaml
calibration_path: /home/ck/RoboClaw/src/mcp_control_demo/config/mcp_control_calibration.yaml
perception:
  camera_name: head
  camera_frame: head_camera_optical
  tag_family: tag25h9
  tag_size_m: 0.0384
```

当前 calibration 文件为：

```text
src/mcp_control_demo/config/mcp_control_calibration.yaml
```

用于小步测试时，`T_exec_camera` 可以临时设为 identity：

```yaml
T_exec_camera:
  - [1.0, 0.0, 0.0, 0.0]
  - [0.0, 1.0, 0.0, 0.0]
  - [0.0, 0.0, 1.0, 0.0]
  - [0.0, 0.0, 0.0, 1.0]
```

注意：identity 只表示 `position_camera_m == position_exec_m`，用于验证链路和小范围移动，不代表真实相机外参。

## 2. 离线检查

### 2.1 运行环境检查

```bash
make check_corobot_runtime
```

期望包含：

```text
pupil_apriltags ok
RuleControlTask ok
corobot runtime ok
```

### 2.2 编译检查

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/home/ck/RoboClaw/src:/home/ck/RoboClaw/.a2d_pkg:${PYTHONPATH:-} \
/home/ck/miniconda3/envs/robot/bin/python -m py_compile \
  .a2d_pkg/corobot/policy_tasks/rule_control_task.py \
  src/mcp_control_demo/corobot_skill_task/skill_task.py \
  src/mcp_control_demo/mcp_server/schemas.py \
  src/mcp_server_demo/corobot_mcp_server/src/server.py
```

期望：无输出，退出码为 0。

### 2.3 Schema 检查

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/ck/RoboClaw/src \
/home/ck/miniconda3/envs/robot/bin/python - <<'PY'
from mcp_control_demo.mcp_server.schemas import MCP_CONTROL_TOOL_SCHEMAS

text = str(MCP_CONTROL_TOOL_SCHEMAS)
assert "control_hz" not in text
assert "control_frequency_hz" not in text
assert {s["name"] for s in MCP_CONTROL_TOOL_SCHEMAS} >= {
    "get_skill_status",
    "reset_robot",
    "get_eef_pose",
    "detect_tags",
    "get_apriltag_pose",
    "move_eef",
    "lift_eef",
    "place_down",
    "open_gripper",
    "close_gripper",
    "grasp_by_tag",
}
print(len(MCP_CONTROL_TOOL_SCHEMAS), "schemas ok")
PY
```

期望：

```text
11 schemas ok
```

如果环境有 `pytest`，可运行：

```bash
PYTHONPATH=/home/ck/RoboClaw/src:/home/ck/RoboClaw/.a2d_pkg:${PYTHONPATH:-} \
python3 -m pytest -q tests/mcp_control_demo
```

当前 `robot` 环境如果提示 `No module named pytest`，先使用上面的手动检查即可。

## 3. 启动 CoRobot App

启动：

```bash
make run_corobot_app COROBOT_APP_ARGS="--config ./src/mcp_control_demo/config/rule_control_app_config.yaml"
```

另开终端检查系统状态：

```bash
curl -sS http://localhost:8765/system/status | python3 -m json.tool
```

重点检查：

- `app_status` 不是 `error`。
- `current_policytask.class_name` 是 `RuleControlTask`。
- `policytask_status.initialized` 为 `true`。

检查 skill 状态：

```bash
curl -sS http://localhost:8765/skill/status | python3 -m json.tool
```

重点检查：

- `environment_ready: true`
- `calibration.camera_frame: head_camera_optical`
- `calibration.exec_frame: base_link`
- `calibration.has_intrinsics: true`
- `calibration.has_T_exec_camera: true`

如果 `has_T_exec_camera=false`，说明当前加载的 calibration 里 `T_exec_camera` 仍是 `null`，或改完文件后没有重启 CoRobot app。

## 4. 复位测试

`RuleControlTask` 提供两条复位路径：

- `POST /skill/reset_robot`
- `POST /system/reset_policytask`

两者都会走同一套安全复位逻辑：

1. 停止当前 task。
2. 先复位左右夹爪，默认 `[0.0, 0.0]`。
3. 再调用 `G01Env.reset()` 复位双臂、头部、腰部。

显式复位：

```bash
curl -sS -X POST http://localhost:8765/skill/reset_robot \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

系统级复位：

```bash
curl -sS -X POST http://localhost:8765/system/reset_policytask | python3 -m json.tool
```

注意：当前 task config 里如果设置 `reset_on_initialize: true`，启动 CoRobot app 时会自动复位机器人。真机调试时不想启动即动作，可以改成 `false` 并重启 app。

## 5. AprilTag 感知测试

刷新检测：

```bash
bash scripts/test_apriltag_detection.sh
```

等价 curl：

```bash
curl -sS -X POST http://localhost:8765/skill/detect_tags \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

按 tag 查询，例如 `tag_id=0`：

```bash
bash scripts/test_apriltag_detection.sh 0
```

等价 curl：

```bash
curl -sS -X POST http://localhost:8765/skill/get_tag_pose \
  -H "Content-Type: application/json" \
  -d '{"tag_id": 0}' | python3 -m json.tool
```

重点检查字段：

- `position_camera_m`
- `translation_m`
- `rotation_matrix`
- `distance_m`
- `camera_frame`
- `tag_size_m`

如果返回 stale，先重新执行 `detect_tags`。

## 6. 当前 EEF 位姿读取

读取右臂当前 EEF：

```bash
curl -sS -X POST http://localhost:8765/skill/get_eef_pose \
  -H "Content-Type: application/json" \
  -d '{"arm": "right", "camera_frame": "head_camera_optical"}' | python3 -m json.tool
```

期望：

```json
{
  "success": true,
  "data": {
    "ok": true,
    "arm": "right",
    "exec_frame": "base_link",
    "position_exec_m": [...],
    "camera_pose_available": true,
    "position_camera_m": [...]
  }
}
```

说明：

- `position_exec_m` 是当前 EEF 在 `base_link` 下的位置。
- `position_camera_m` 是通过 `T_exec_camera` 转出的 camera-frame 位置。
- identity 外参下，`position_camera_m` 会约等于 `position_exec_m`。

如果出现：

```text
camera_pose_available: false
camera_pose_error: missing T_exec_camera calibration
```

处理方式：

1. 检查 `src/mcp_control_demo/config/mcp_control_calibration.yaml` 的 `T_exec_camera`。
2. 确认 `.a2d_pkg/corobot/config/rule_control_task_config.yml` 的 `calibration_path` 指向的是这份文件。
3. 重启 CoRobot app，配置不会热加载。

## 7. 小范围 Move 测试

真机测试前确认：

- 有人看护急停。
- `T_exec_camera` 已配置。若只做链路测试，可以临时 identity。
- 首次移动使用 `0.002m` 到 `0.005m`。
- `duration_s` 建议设为 `2.0` 或更长。

先 dry-run，只打印当前点和目标 payload，不执行运动：

```bash
ARM=right DURATION_S=2.0 bash scripts/test_small_move_eef.sh 0.002 0 0
```

确认目标安全后执行 2mm：

```bash
ARM=right DURATION_S=2.0 EXECUTE_MOVE=1 bash scripts/test_small_move_eef.sh 0.002 0 0
```

如果方向合理，再执行 5mm：

```bash
ARM=right DURATION_S=2.0 EXECUTE_MOVE=1 bash scripts/test_small_move_eef.sh 0.005 0 0
```

反向回退示例：

```bash
ARM=right DURATION_S=2.0 EXECUTE_MOVE=1 bash scripts/test_small_move_eef.sh -0.002 0 0
```

脚本逻辑：

1. 调 `/skill/get_eef_pose` 读取当前 EEF camera-frame 坐标。
2. 将输入 delta 加到当前 `position_camera_m`。
3. 调 `/skill/move_eef`。
4. 不传 orientation，因此控制层保留当前 EEF 姿态。

直接调用 `move_eef` 示例：

```bash
curl -sS -X POST http://localhost:8765/skill/move_eef \
  -H "Content-Type: application/json" \
  -d '{
    "arm": "right",
    "camera_frame": "head_camera_optical",
    "target_position_camera_m": [0.58, -0.33, 0.74],
    "duration_s": 2.0
  }' | python3 -m json.tool
```

返回中的 `meta` 应包含：

```json
{
  "control_hz": 30.0,
  "num_steps": 60,
  "actual_duration_s": 2.0
}
```

内部发给底层 controller 的 `EEF_ABS` 每行是 6 维：

```text
[x, y, z, roll, pitch, yaw]
```

`get_eef_pose` 返回的当前姿态仍是 `orientation_exec_xyzw` 四元数；`move_eef` 构造 action 时会转换成底层需要的 rpy。

## 8. 夹爪、抬升、放置、抓取测试

打开/关闭右夹爪：

```bash
curl -sS -X POST http://localhost:8765/skill/gripper \
  -H "Content-Type: application/json" \
  -d '{"arm": "right", "gripper_value": 0.0, "duration_s": 0.5}' | python3 -m json.tool

curl -sS -X POST http://localhost:8765/skill/gripper \
  -H "Content-Type: application/json" \
  -d '{"arm": "right", "gripper_value": 1.0, "duration_s": 0.5}' | python3 -m json.tool
```

小距离抬升：

```bash
curl -sS -X POST http://localhost:8765/skill/lift_eef \
  -H "Content-Type: application/json" \
  -d '{
    "arm": "right",
    "camera_frame": "head_camera_optical",
    "distance_m": 0.01,
    "duration_s": 2.0
  }' | python3 -m json.tool
```

小距离下降并打开夹爪：

```bash
curl -sS -X POST http://localhost:8765/skill/place_down \
  -H "Content-Type: application/json" \
  -d '{
    "arm": "right",
    "camera_frame": "head_camera_optical",
    "down_distance_m": 0.01,
    "duration_s": 2.0,
    "open_after_down": true
  }' | python3 -m json.tool
```

按 tag 抓取前先确认 tag 可见：

```bash
bash scripts/test_apriltag_detection.sh 0
```

再执行抓取：

```bash
ARM=right bash scripts/test_grasp_by_tag.sh 0
```

抓取序列为：

1. open gripper
2. move to approach point
3. descend to grasp point
4. close gripper
5. lift

## 9. MCP Server 测试

MCP server 只转发到 CoRobot HTTP API，不直接持有 `G01Env`。

启动：

```bash
cd /home/ck/RoboClaw/src/mcp_server_demo/corobot_mcp_server
PYTHONPATH=/home/ck/RoboClaw/src:/home/ck/RoboClaw/.a2d_pkg:${PYTHONPATH:-} \
python3 src/server.py
```

tool list 应包含：

```text
get_skill_status
reset_robot
get_eef_pose
detect_tags
get_apriltag_pose
move_eef
lift_eef
place_down
open_gripper
close_gripper
grasp_by_tag
```

MCP 调用 `move_eef` 示例：

```json
{
  "name": "move_eef",
  "arguments": {
    "arm": "right",
    "camera_frame": "head_camera_optical",
    "target_position_camera_m": [0.58, -0.33, 0.74],
    "duration_s": 2.0
  }
}
```

固定频率拒绝测试：

```json
{
  "name": "move_eef",
  "arguments": {
    "arm": "right",
    "target_position_camera_m": [0.58, -0.33, 0.74],
    "duration_s": 2.0,
    "control_hz": 60
  }
}
```

期望 MCP 直接返回：

```text
控制频率固定为 30Hz，不能通过工具参数覆盖
```

## 10. URDF 与相机外参说明

`G1.urdf` 中能看到头部关节链：

```text
base_link
 -> waist_lift_link
 -> waist_pitch_link
 -> head_yaw_link
 -> head_pitch_link
```

因此可以根据当前状态计算：

```text
T_base_head_pitch_link(q)
```

但当前 URDF 中没有 `camera`、`rgb`、`depth`、`optical` 等相机 link，所以不能仅凭 URDF 得到：

```text
T_base_head_camera_optical(q)
```

真实外参需要额外提供固定安装关系：

```text
T_head_pitch_link_camera_optical
```

真实运行时应使用：

```text
T_base_camera(q) = T_base_head_pitch_link(q) * T_head_pitch_link_camera_optical
```

当前 identity `T_exec_camera` 只是临时测试方式，不是最终标定方案。

## 11. 常见问题

### `has_T_exec_camera=false`

检查当前实际加载的 calibration：

```bash
grep -n "calibration_path" .a2d_pkg/corobot/config/rule_control_task_config.yml
grep -n "T_exec_camera" -A5 src/mcp_control_demo/config/mcp_control_calibration.yaml
```

改完 calibration 后必须重启 CoRobot app。

### `camera_pose_available=false`

常见原因：

- `T_exec_camera` 是 `null`。
- 改的是错误的 calibration 文件。
- CoRobot app 未重启。

先确认：

```bash
curl -sS http://localhost:8765/skill/status | python3 -m json.tool
```

### `camera_params are required`

AprilTag pose estimation 需要相机内参。当前链路优先读 observation 里的 camera params；若 observation 没有内参，则使用 calibration 里的 `intrinsics` fallback。

检查：

```bash
curl -sS http://localhost:8765/skill/status | python3 -m json.tool
```

确认：

- `calibration.has_intrinsics: true`
- `perception.has_fallback_camera_params: true`

### 当前 EEF pose 读不到

`get_eef_pose` 会优先读 `states.end_pose`。如果 observation 没有 `end_pose`，会尝试用 `arm_joint_states + waist_joint_states + head_joint_states` 做 FK。

如果仍然读不到，说明底层状态里缺少关节数据，需要查看 CoRobot app 日志中的 dataloader 初始化和状态读取错误。

### 真机动作方向不对

优先检查：

- `T_exec_camera`
- `camera_approach_axis`
- `camera_lift_axis`
- `camera_place_down_axis`
- `tag_offsets`

不要长期用 identity 补偿真实相机外参；identity 只用于小步链路测试。

### 轨迹步数不是预期值

所有 primitive 固定 30Hz：

```text
num_steps = ceil(duration_s * 30)
actual_duration_s = num_steps / 30
```

例如：

- `duration_s=0.5` -> `15` steps
- `duration_s=1.0` -> `30` steps
- `duration_s=2.0` -> `60` steps
