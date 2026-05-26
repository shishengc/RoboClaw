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
- 对外 `move_eef` 的目标点现在按 Omnipicker 夹爪中心 TCP 解释；底层 A2D
  仍然接收 wrist/link7 目标，控制层会自动减去固定偏移 `[0, 0, 0.14308]`。

接口变化：

- 保持已有 `/skill/...` HTTP API 不变。
- 新增 `/skill/get_eef_pose`，用于读取当前左/右臂 EEF 位姿。
- MCP tool schema 同步新增 `get_eef_pose`。
- 没有保留 `move_eef_delta_exec`；小步移动仍通过 `move_eef` 和用户提供的 calibration 完成。

测试辅助：

- 旧的 `scripts/test_small_move_eef.sh` 已移除；小范围移动测试改为直接调用
  `/skill/get_eef_pose` 与 `/skill/move_eef`，避免脚本承担核心控制逻辑。

当前重要结论：

- 最新 head 相机内参来自 `/home/ck/robot_test/parameters/head_intrinsic_params.json`。
- 最新 head 相机外参来自 `/home/ck/robot_test/parameters/head_extrinsic_params.json`。
- 这份外参不是直接的 `base_link <- camera`，而是：

```text
p_head_pitch_link = T_head_pitch_camera * p_camera
```

- 当前 `mcp_control_calibration.yaml` 已经按固定复位姿态预合成：

```text
T_exec_camera = T_base_head_pitch(reset) * T_head_pitch_camera
p_base_link = T_exec_camera * p_camera
```

- 后续运动测试默认 head 和 waist 保持复位固定状态；如果 head/waist 运动了，当前静态 `T_exec_camera` 就不再准确，需要改成运行时动态 FK 合成。

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
  tag_size_m: 0.019
reset_pose:
  target_head_positions: [0.0, 0.43633230555555524]
  target_waist_positions: [0.8901176920412174, 0.4598677062988281]
```

当前 calibration 文件为：

```text
src/mcp_control_demo/config/mcp_control_calibration.yaml
```

当前 calibration 使用 head 相机新内参：

```yaml
intrinsics:
  source: /home/ck/robot_test/parameters/head_intrinsic_params.json
  camera_name: head
  camera_model: Realsense-D455
  image_size:
    width: 1280
    height: 720
  fx: 645.2637329101562
  fy: 644.3807373046875
  cx: 642.1536865234375
  cy: 362.27099609375
  distortion_coefficients:
    model: plumb bob
    opencv_order: k1,k2,p1,p2,k3
```

当前 calibration 使用固定复位姿态下的 `base_link <- camera` 变换：

```yaml
extrinsics:
  source: /home/ck/robot_test/parameters/head_extrinsic_params.json
  source_transform: T_head_pitch_camera
  fixed_robot_pose_for_T_exec_camera:
    # [head_yaw_rad, head_pitch_rad]
    target_head_positions: [0.0, 0.43633230555555524]
    # [waist_pitch_rad, waist_lift_m]
    target_waist_positions: [0.8901176920412174, 0.4598677062988281]

T_exec_camera:
  - [-0.013334748, -0.978382107, 0.206374993, 0.613127464]
  - [-0.999874275, 0.011276128, -0.011148197, -0.015927399]
  - [0.008580085, -0.206497705, -0.978409464, 1.003947753]
  - [0.0, 0.0, 0.0, 1.0]
```

注意：这个 `T_exec_camera` 不是 identity，也不是动态外参。它只在 head/waist 保持上述复位姿态时成立。该矩阵使用 CoRobot/Pinocchio FK 合成；不要用 `robot_test/head_eye_calibration.py` 里的手写 FK 重新生成，否则相机深度轴会被翻错。

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
}
print(len(MCP_CONTROL_TOOL_SCHEMAS), "schemas ok")
PY
```

期望：

```text
10 schemas ok
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

当前 `/skill/get_tag_pose` 返回的 tag 位置仍以 `position_camera_m` 为主；控制层会在 `move_eef`、`lift_eef`、`place_down` 构造 action 时使用 `T_exec_camera` 转到 `base_link`。

手动理解转换关系：

```text
position_base_link_m = T_exec_camera * position_camera_m
```

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
- 当前 `position_exec_m` / `position_camera_m` 表示夹爪中心 TCP。
- `wrist_position_exec_m` / `wrist_position_camera_m` 表示底层 A2D 实际控制的 wrist/link7 frame。
- `position_camera_m` 是通过 `T_exec_camera` 转出的 camera-frame 位置。
- 当前 `T_exec_camera` 是基于固定 head/waist 复位姿态合成的真实 head 相机外参，因此 `position_camera_m` 和 `position_exec_m` 不会再相等。

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
- `T_exec_camera` 已配置为最新 head 相机外参合成结果。
- head/waist 已复位并在测试期间保持固定。
- 首次移动使用 `0.002m` 到 `0.005m`。
- `duration_s` 建议设为 `2.0` 或更长。

先读取当前右臂夹爪中心 TCP 的 camera-frame 坐标：

```bash
curl -sS -X POST http://localhost:8765/skill/get_eef_pose \
  -H "Content-Type: application/json" \
  -d '{"arm": "right", "camera_frame": "head_camera_optical"}' \
  | python3 -m json.tool
```

确认当前点安全后，手动在 `position_camera_m` 上加一个很小的 delta，例如
`x + 0.002m`，再调用 `/skill/move_eef`。示例中的坐标需要替换为你刚刚读到
并加完 delta 的目标坐标：

```bash
curl -sS -X POST http://localhost:8765/skill/move_eef \
  -H "Content-Type: application/json" \
  -d '{
    "arm": "right",
    "camera_frame": "head_camera_optical",
    "target_position_camera_m": [0.582, -0.33, 0.74],
    "duration_s": 2.0
  }' | python3 -m json.tool
```

注意：不传 orientation 时，控制层会保留当前 EEF 姿态。

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

对外 `target_position_camera_m` 是夹爪中心 TCP。构造 action 时会先换算成底层
`arm_left_link7/arm_right_link7` wrist 目标，再写入 `EEF_ABS`：

```text
wrist_target = gripper_center_target - R_wrist * [0, 0, 0.14308]
```

`get_eef_pose` 返回的当前姿态仍是 `orientation_exec_xyzw` 四元数；`move_eef`
构造 action 时会转换成底层需要的 rpy。

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

如果需要在闭合夹爪前人为加入一个 `base_link` 坐标系下的偏移量，不改底层控制逻辑，使用脚本：

```bash
ARM=right bash scripts/test_grasp_by_tag_base_offset.sh 0 0.00 0.00 0.02
```

这个命令默认只 dry-run，会打印：

- tag 的 `position_camera_m`
- tag 转到 `base_link` 后的位置
- 加上 base offset 后的 grasp 点，即期望夹爪中心 TCP 到达的位置
- approach 点
- 闭合后沿 `base_link +Z` 抬升后的目标点
- 每一步实际要调用的 `/skill/...` payload

确认点位安全后再执行：

```bash
ARM=right EXECUTE_GRASP=1 bash scripts/test_grasp_by_tag_base_offset.sh 0 0.00 0.00 0.02
```

参数含义：

```text
<tag_id> <dx_base_m> <dy_base_m> <dz_base_m>
```

脚本序列：

1. `/skill/detect_tags`
2. `/skill/get_tag_pose`
3. 将 tag camera 坐标转换到 `base_link`
4. 在 `base_link` 下加人工偏移
5. 转回 camera 坐标调用 `/skill/move_eef`；控制层自动把夹爪中心目标换算成 wrist/link7 目标
6. `/skill/gripper` 闭合
7. 使用 `base_link +Z` 计算 lift 目标，再调用 `/skill/move_eef`

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

## 10. URDF、head 外参与静态 base_link 转换

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

但当前 URDF 中没有 `camera`、`rgb`、`depth`、`optical` 等相机 link，所以不能仅凭 URDF 得到相机光心外参。最新固定安装外参由下面文件提供：

```text
/home/ck/robot_test/parameters/head_extrinsic_params.json
```

该文件提供：

```text
T_head_pitch_camera
p_head_pitch_link = T_head_pitch_camera * p_camera
```

`mcp_control` 当前采用静态合成方案。假设 head/waist 在运动期间保持复位姿态：

```text
head = [0.0, 0.43633230555555524]
waist = [0.8901176920412174, 0.4598677062988281]
T_exec_camera = T_base_head_pitch_link(reset) * T_head_pitch_camera
```

这里的 `T_base_head_pitch_link(reset)` 必须使用 CoRobot/Pinocchio FK，也就是 `corobot.utils.kinematics.Kinematics.compute_head_fk()` 对应的 frame convention。之前用 `robot_test/head_eye_calibration.py` 的手写 FK 合成时，位置基本接近，但旋转和 CoRobot 实际 frame 不一致，会导致 `base_link -> camera` 后相机 `z` 深度为负。

因此 AprilTag 检测出的相机坐标可以直接转为 `base_link`：

```text
p_base_link = T_exec_camera * p_camera
```

如果未来允许 head/waist 在任务过程中移动，需要把这一步改成动态 FK：

```text
T_exec_camera(q) = T_base_head_pitch_link(q) * T_head_pitch_camera
```

并且每次构造控制 action 前使用当前 `head_joint_states`、`waist_joint_states` 更新外参。

重新提供内参、外参或 URDF 时，需要重新生成
`src/mcp_control_demo/config/mcp_control_calibration.yaml`，并同步
`.a2d_pkg/corobot/config/rule_control_task_config.yml` 的 `calibration_path`、
`perception.camera_frame`、`perception.tag_size_m` 和 reset pose。生成逻辑必须仍然满足：

```text
T_exec_camera = T_base_head_pitch_link(reset) * T_head_pitch_camera
```

如果外参文件给的是 `camera <- head_pitch`，需要先求逆再写入。生成后必须重启 CoRobot app。

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
- head/waist 是否保持和 `fixed_robot_pose_for_T_exec_camera` 一致
- `camera_approach_axis`
- `camera_lift_axis`
- `camera_place_down_axis`

不要再用 identity 补偿真实相机外参；当前配置已经使用 head 相机外参合成到 `base_link`。

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
