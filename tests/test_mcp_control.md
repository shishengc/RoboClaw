# RuleControlTask / mcp_control_demo Test Guide

默认在 RoboClaw 仓库根目录执行：

```bash
cd /home/ck/RoboClaw
export COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
export TASK_CONFIG="${TASK_CONFIG:-/home/ck/RoboClaw/.a2d_pkg/corobot/config/rule_control_task_config.yml}"
```

## 1. 当前配置链路

CoRobot 默认 app config 保持原始 `ServicePolicyTask`。Rule control 按 CoRobot 原有动态加载方式切换 PolicyTask：

```text
.a2d_pkg/corobot/config/rule_control_task_config.yml
  -> corobot.policy_tasks.rule_control_task.RuleControlTask
```

先正常启动 app：

```bash
make run_corobot_app
```

再加载 `RuleControlTask`：

```bash
curl -sS -X POST http://localhost:8765/system/load_policytask \
  -H "Content-Type: application/json" \
  -d '{
    "policy_module": "corobot.policy_tasks.rule_control_task",
    "class_name": "RuleControlTask",
    "config_path": "/home/ck/RoboClaw/.a2d_pkg/corobot/config/rule_control_task_config.yml"
  }' | python3 -m json.tool
```

使用上面的环境变量可以写成：

```bash
curl -sS -X POST "${COROBOT_URL}/system/load_policytask" \
  -H "Content-Type: application/json" \
  -d "{
    \"policy_module\": \"corobot.policy_tasks.rule_control_task\",
    \"class_name\": \"RuleControlTask\",
    \"config_path\": \"${TASK_CONFIG}\"
  }" | python3 -m json.tool
```

`rule_control_task_config.yml` 中的 control 配置只保存固定参数：

```yaml
mcp_control:
  transform_mode: dynamic_fk
  camera_frame: head_camera_optical
  exec_frame: base_link
  intrinsics: {...}
  extrinsics:
    T_head_pitch_camera: [...]
  camera_approach_axis: [0.0, 0.0, -1.0]
  camera_lift_axis: [0.0, -1.0, 0.0]
  camera_place_down_axis: [0.0, 1.0, 0.0]
```

不再使用 `src/mcp_control_demo/config`，不再使用 `calibration_path`，也不再在配置里保存预合成的 `T_exec_camera`。

## 2. 动态外参逻辑

head 相机固定安装外参为：

```text
p_head_pitch_link = T_head_pitch_camera * p_camera
```

运行时每次控制前根据当前 observation 合成：

```text
T_exec_camera(q) = T_base_head_pitch(q) * T_head_pitch_camera
p_base_link = T_exec_camera(q) * p_camera
```

其中 `q` 来自：

```text
head_joint_states = [head_yaw_rad, head_pitch_rad]
waist_joint_states = [waist_pitch_rad, waist_lift_m]
```

`T_base_head_pitch(q)` 必须使用 CoRobot/Pinocchio FK，也就是 `corobot.utils.kinematics.Kinematics.compute_head_fk()`。

注意：RobotDds fallback 读到的 head pitch 可能是 degree，例如 `24.995...`。RuleControlTask 在进入 FK 前会把超出 URDF 弧度范围的 head 读数归一化为 rad；waist 仍按 `[waist_pitch_rad, waist_lift_m]` 使用。

## 3. 离线检查

运行环境检查：

```bash
make check_corobot_runtime
```

编译检查：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/home/ck/RoboClaw/src:/home/ck/RoboClaw/.a2d_pkg:${PYTHONPATH:-} \
/home/ck/miniconda3/envs/robot/bin/python -m py_compile \
  .a2d_pkg/corobot/policy_tasks/rule_control_task.py \
  src/mcp_control_demo/calibration/config.py \
  src/mcp_control_demo/control/action_builder.py \
  src/mcp_control_demo/control/joint_units.py \
  scripts/generate_mcp_control_calibration.py
```

单元测试：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/home/ck/RoboClaw/src:/home/ck/RoboClaw/.a2d_pkg:${PYTHONPATH:-} \
/home/ck/RoboClaw/.venv/bin/python -m pytest -q tests/mcp_control_demo
```

## 4. 启动后检查和管理

系统状态：

```bash
curl -sS "${COROBOT_URL}/system/status" | python3 -m json.tool
```

重点检查：

- `current_policytask.class_name: RuleControlTask`
- `policytask_status.initialized: true`

skill 状态：

```bash
curl -sS "${COROBOT_URL}/skill/status" | python3 -m json.tool
```

重点检查：

- `environment_ready: true`
- `calibration.transform_mode: dynamic_fk`
- `calibration.can_compute_T_exec_camera: true`
- `calibration.has_T_head_pitch_camera: true`
- `calibration.has_intrinsics: true`

如果 `can_compute_T_exec_camera=false`，检查 `rule_control_task_config.yml` 的 `mcp_control.extrinsics.T_head_pitch_camera`。

启动/停止当前 PolicyTask：

```bash
curl -sS -X POST "${COROBOT_URL}/system/start_policytask" | python3 -m json.tool
curl -sS -X POST "${COROBOT_URL}/system/stop_policytask" | python3 -m json.tool
```

系统层 reset 当前 PolicyTask。对 `RuleControlTask` 来说会调用同一套机器人复位流程：

```bash
curl -sS -X POST "${COROBOT_URL}/system/reset_policytask" | python3 -m json.tool
```

## 5. 常用命令速查

三视角相机：

```bash
curl -sS "${COROBOT_URL}/skill/camera_views?include_images=false" \
  | python3 -m json.tool
```

保存三视角图像，默认写入 `artifacts/test_camera/<date>/`：

```bash
curl -sS "${COROBOT_URL}/skill/camera_views?format=jpg&include_images=false&save_images=true" \
  | python3 -m json.tool
```

机器人复位，推荐优先用这个接口；它会先复位夹爪，再通过 `G01Env.reset` 复位 arm/head/waist：

```bash
curl -sS -X POST "${COROBOT_URL}/skill/reset_robot" \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

读取当前 EEF。返回 `position_exec_m` 和 `position_camera_m`，目标语义是 Omnipicker gripper-center TCP：

```bash
curl -sS -X POST "${COROBOT_URL}/skill/get_eef_pose" \
  -H "Content-Type: application/json" \
  -d '{"arm": "right", "camera_frame": "head_camera_optical"}' \
  | python3 -m json.tool

curl -sS -X POST "${COROBOT_URL}/skill/get_eef_pose" \
  -H "Content-Type: application/json" \
  -d '{"arm": "left", "camera_frame": "head_camera_optical"}' \
  | python3 -m json.tool
```

打开/关闭右夹爪：

```bash
curl -sS -X POST "${COROBOT_URL}/skill/gripper" \
  -H "Content-Type: application/json" \
  -d '{"arm": "right", "gripper_value": 0.0, "duration_s": 0.5}' \
  | python3 -m json.tool

curl -sS -X POST "${COROBOT_URL}/skill/gripper" \
  -H "Content-Type: application/json" \
  -d '{"arm": "right", "gripper_value": 1.0, "duration_s": 0.5}' \
  | python3 -m json.tool
```

小步移动 EEF。`target_position_camera_m` 是相机坐标系下的 gripper-center TCP 目标点：

```bash
curl -sS -X POST "${COROBOT_URL}/skill/move_eef" \
  -H "Content-Type: application/json" \
  -d '{
    "arm": "right",
    "camera_frame": "head_camera_optical",
    "target_position_camera_m": [0.30, 0.02, 0.55],
    "duration_s": 1.0
  }' | python3 -m json.tool
```

也可以用脚本包装同一接口：

```bash
ARM=right DURATION_S=1.0 bash scripts/test_move_eef.sh 0.30 0.02 0.55
```

沿相机 lift 方向抬升当前 EEF：

```bash
curl -sS -X POST "${COROBOT_URL}/skill/lift_eef" \
  -H "Content-Type: application/json" \
  -d '{"arm": "right", "camera_frame": "head_camera_optical", "distance_m": 0.02, "duration_s": 1.0}' \
  | python3 -m json.tool
```

放下并可选打开夹爪：

```bash
curl -sS -X POST "${COROBOT_URL}/skill/place_down" \
  -H "Content-Type: application/json" \
  -d '{
    "arm": "right",
    "camera_frame": "head_camera_optical",
    "down_distance_m": 0.02,
    "duration_s": 1.0,
    "open_after_down": true
  }' | python3 -m json.tool
```

AprilTag 检测：

```bash
curl -sS -X POST "${COROBOT_URL}/skill/detect_tags" \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

读取指定 tag：

```bash
curl -sS -X POST "${COROBOT_URL}/skill/get_tag_pose" \
  -H "Content-Type: application/json" \
  -d '{"tag_id": 0, "allow_stale": false}' \
  | python3 -m json.tool
```

也可以用脚本：

```bash
bash scripts/test_apriltag_detection.sh
bash scripts/test_apriltag_detection.sh 0
```

基于 AprilTag 的原子技能组合脚本：

```bash
ARM=right bash scripts/test_grasp_by_tag.sh 0

ARM=right EXECUTE_GRASP=0 bash scripts/test_grasp_by_tag_base_offset.sh 0 0.0 0.0 0.0

ARM=right bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh 0 1 0.0 0.0 0.0

ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
  0 1 0.00 0.00 -0.045 0.0 -0.0015 0.02
```

小步移动前确认：

- 有人看护急停。
- `/skill/status` 显示 `can_compute_T_exec_camera: true`。
- observation 中有 `head_joint_states` 和 `waist_joint_states`。
- 首次移动使用 `0.002m` 到 `0.005m`。
- 不要传 `control_hz` 或 `control_frequency_hz`；控制层固定 30Hz。

## 6. 重新生成配置

如果提供新的内参、外参和 URDF，使用脚本重写 CoRobot task config；默认不会修改 app config：

```bash
PYTHONPATH=/home/ck/RoboClaw/src:/home/ck/RoboClaw/.a2d_pkg:${PYTHONPATH:-} \
/home/ck/miniconda3/envs/robot/bin/python scripts/generate_mcp_control_calibration.py \
  --intrinsics /path/to/head_intrinsic_params.json \
  --extrinsics /path/to/head_extrinsic_params.json \
  --urdf /path/to/A2D_viz.urdf \
  --task-config /home/ck/RoboClaw/.a2d_pkg/corobot/config/rule_control_task_config.yml
```

如果外参文件给的是 `camera <- head_pitch`，加 `--invert-extrinsic`。生成后重启 CoRobot app，配置不会热加载。
