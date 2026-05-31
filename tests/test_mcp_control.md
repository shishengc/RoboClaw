# RuleControlTask / mcp_control_demo Test Guide

默认在 RoboClaw 仓库根目录执行：

```bash
cd /home/ck/RoboClaw
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
  scripts/generate_mcp_control_calibration.py
```

单元测试：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/home/ck/RoboClaw/src:/home/ck/RoboClaw/.a2d_pkg:${PYTHONPATH:-} \
/home/ck/RoboClaw/.venv/bin/python -m pytest -q tests/mcp_control_demo
```

## 4. 启动后检查

系统状态：

```bash
curl -sS http://localhost:8765/system/status | python3 -m json.tool
```

重点检查：

- `current_policytask.class_name: RuleControlTask`
- `policytask_status.initialized: true`

skill 状态：

```bash
curl -sS http://localhost:8765/skill/status | python3 -m json.tool
```

重点检查：

- `environment_ready: true`
- `calibration.transform_mode: dynamic_fk`
- `calibration.can_compute_T_exec_camera: true`
- `calibration.has_T_head_pitch_camera: true`
- `calibration.has_intrinsics: true`

如果 `can_compute_T_exec_camera=false`，检查 `rule_control_task_config.yml` 的 `mcp_control.extrinsics.T_head_pitch_camera`。

## 5. 常用接口

三视角相机：

```bash
curl -sS "http://localhost:8765/skill/camera_views?include_images=false" \
  | python3 -m json.tool
```

AprilTag 检测：

```bash
curl -sS -X POST http://localhost:8765/skill/detect_tags \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

读取当前 EEF：

```bash
curl -sS -X POST http://localhost:8765/skill/get_eef_pose \
  -H "Content-Type: application/json" \
  -d '{"arm": "right", "camera_frame": "head_camera_optical"}' \
  | python3 -m json.tool
```

小步移动前确认：

- 有人看护急停。
- `/skill/status` 显示 `can_compute_T_exec_camera: true`。
- observation 中有 `head_joint_states` 和 `waist_joint_states`。
- 首次移动使用 `0.002m` 到 `0.005m`。

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
