# mcp_control_demo

`mcp_control_demo` 是面向真实机器人的相机坐标系控制包。它把 AprilTag 感知结果、已标定外参、CoRobot 标准 `Action` 和 MCP tool 调用统一到一条链路：

```text
Agent MCP tool
  -> corobot_mcp_server
  -> CoRobot /skill/... HTTP API
  -> RuleControlTask
  -> G01Env.execute_action(Action)
```

核心约定：

- 对外 tool 坐标统一为相机坐标系，默认 `camera_frame=head_camera_optical`。
- 对外 `move_eef/lift_eef/place_down` 的 EEF 目标表示 Omnipicker
  夹爪中心 TCP；底层 A2D 仍控制 wrist/link7 frame，控制层会自动扣除固定
  `[0, 0, 0.14308]m` TCP 偏移。
- 控制执行前通过当前 `T_exec_camera(q)` 转成 CoRobot 支持的执行坐标系，默认 `exec_frame=base_link`。
- 所有轨迹固定 `30Hz`，不允许通过 tool 参数覆盖。
- MCP server 不持有 `G01Env`，真实机器人环境只在 CoRobot skill task 中管理。
- 第一版只面向真实机器人，不依赖 shishen gRPC、GenIEsim bridge 或仿真 geometry。
- `RuleControlTask` 额外提供 `GET /skill/camera_views`，默认读取 `head`、
  `hand_left`、`hand_right` 三视角图像，并可返回拼接图 base64，供 Agent
  做视觉确认或记录。

## Runtime Config

默认 CoRobot app config 保持原始 `ServicePolicyTask`，rule control 的 task config 单独放在：

```text
.a2d_pkg/corobot/config/rule_control_task_config.yml
```

先正常启动 CoRobot app：

```bash
make run_corobot_app
```

然后按 CoRobot 原有方式加载 `RuleControlTask`：

```bash
curl -sS -X POST http://localhost:8765/system/load_policytask \
  -H "Content-Type: application/json" \
  -d '{
    "policy_module": "corobot.policy_tasks.rule_control_task",
    "class_name": "RuleControlTask",
    "config_path": "/home/ck/RoboClaw/.a2d_pkg/corobot/config/rule_control_task_config.yml"
  }' | python3 -m json.tool
```

`rule_control_task_config.yml` 负责 control/perception/reset/environment：

```yaml
mcp_control:
  transform_mode: dynamic_fk
  extrinsics:
    T_head_pitch_camera: [...]
```

`mcp_control` 节只保存固定量：相机内参、固定安装外参
`T_head_pitch_camera` 和 camera-frame 控制方向轴。实际控制用的
`T_exec_camera(q)` 会在每次构造动作前根据当前 head/waist 状态动态合成：

```text
T_exec_camera(q) = T_base_head_pitch(q) * T_head_pitch_camera
```

The legacy `mcp_control_demo.corobot_skill_task.skill_task.McpControlSkillTask`
import path remains available as a compatibility shim. New configs should use
`corobot.policy_tasks.rule_control_task.RuleControlTask`.

## Modules

- `perception`: AprilTag 检测和按 `tag_id` 查询。
- `calibration`: 固定相机标定、动态 `T_exec_camera(q)` 配置解析和 camera-frame 方向轴。
- `control`: 30Hz EEF/gripper/action 构造。
- `corobot_skill_task`: 兼容旧 `McpControlSkillTask` import path；核心 task 已迁入 `corobot.policy_tasks.rule_control_task`。
- `mcp_server`: Agent tool schema。

## Camera Views

直接 HTTP 调用：

```bash
curl -sS "http://localhost:8765/skill/camera_views?include_images=false" \
  | python3 -m json.tool
```

默认相机顺序为 `head,hand_left,hand_right`。常用参数：

- `cameras`: 逗号分隔相机名，默认 `head,hand_left,hand_right`。
- `format`: `jpg` / `jpeg` / `png`，默认 `jpg`。
- `include_images`: 是否返回 base64 图像，默认 `true`。
- `concatenate`: 是否返回拼接图，默认 `true`。
- `jpeg_quality`: JPEG 质量，默认 `85`。
- `save_images`: 是否把单路图和拼接图保存到磁盘，默认 `true`。
- `save_dir`: 保存根目录，默认 `/home/ck/RoboClaw/artifacts/test_camera`。

响应中的 `saved_images` 会返回本次保存的文件路径。图片会按日期保存到
`save_dir/YYYY-MM-DD/` 子目录，默认包括三路单图和 `three_views` 拼接图。
保存文件使用 RGB 通道顺序，`saved_images.color_order` 返回 `RGB`。

对应 MCP tool 为 `get_camera_views`，只做 HTTP GET 转发，不直接持有相机或
`G01Env`。

## Reset

`RuleControlTask.reset()` 和 `/skill/reset_robot` 使用同一套复位流程：

1. 停止当前 task。
2. 先把左右夹爪复位到配置值，默认 `[0.0, 0.0]`。
3. 再调用 `G01Env.reset()` 复位双臂、头部和腰部。

默认复位位姿与 CoRobot `ServicePolicyTask` 保持一致。可以通过 `reset_pose`
覆盖 `target_grippers_positions`、`target_arm_joint_positions`、
`target_head_positions`、`target_waist_positions`。如果需要启动 task 时自动复位，
设置 `reset_on_initialize: true`。
