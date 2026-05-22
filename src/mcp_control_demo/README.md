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
- 控制执行前通过 `T_exec_camera` 转成 CoRobot 支持的执行坐标系，默认 `exec_frame=base_link`。
- 所有轨迹固定 `30Hz`，不允许通过 tool 参数覆盖。
- MCP server 不持有 `G01Env`，真实机器人环境只在 CoRobot skill task 中管理。
- 第一版只面向真实机器人，不依赖 shishen gRPC、GenIEsim bridge 或仿真 geometry。

## Runtime Config

CoRobot task 配置中需要提供 calibration 文件：

```yaml
calibration_path: /path/to/mcp_control_calibration.yaml
perception:
  camera_name: head
  camera_frame: head_camera_optical
  tag_family: tag25h9
  tag_size_m: 0.018
  stale_after_s: 1.0
reset_on_initialize: false
reset_pose:
  target_grippers_positions: [0.0, 0.0]
env_config:
  dataloader:
    data_source:
      ALIGNED_ROBOT:
        enabled: true
    enabled_streams:
      camera:
        head: true
        hand_left: true
        hand_right: true
  motion_controller:
    enabled: true
    left_arm_dofs: 7
    right_arm_dofs: 7
```

仓库内已有一个根据头部相机标定生成的配置入口：

```text
src/mcp_control_demo/config/mcp_control_calibration_head_camera_20260520.yaml
src/mcp_control_demo/config/mcp_control_task_head_camera.yaml
src/mcp_control_demo/config/corobot_app_mcp_control.yaml
```

其中 calibration 已填入相机内参和畸变参数，但 `T_exec_camera` 仍为空；真实控制前必须补齐外参。

CoRobot app 可加载：

```yaml
default_policytask:
  policy_module: corobot.policy_tasks.rule_control_task
  class_name: RuleControlTask
  config_path: /path/to/mcp_control_task.yaml
```

The legacy `mcp_control_demo.corobot_skill_task.skill_task.McpControlSkillTask`
import path remains available as a compatibility shim. New configs should use
`corobot.policy_tasks.rule_control_task.RuleControlTask`.

## Modules

- `perception`: AprilTag 检测和按 `tag_id` 查询。
- `calibration`: `T_exec_camera`、camera-frame 方向轴和 tag offset。
- `control`: 30Hz EEF/gripper/action 构造。
- `corobot_skill_task`: 兼容旧 `McpControlSkillTask` import path；核心 task 已迁入 `corobot.policy_tasks.rule_control_task`。
- `mcp_server`: Agent tool schema。

## Reset

`RuleControlTask.reset()` 和 `/skill/reset_robot` 使用同一套复位流程：

1. 停止当前 task。
2. 先把左右夹爪复位到配置值，默认 `[0.0, 0.0]`。
3. 再调用 `G01Env.reset()` 复位双臂、头部和腰部。

默认复位位姿与 CoRobot `ServicePolicyTask` 保持一致。可以通过 `reset_pose`
覆盖 `target_grippers_positions`、`target_arm_joint_positions`、
`target_head_positions`、`target_waist_positions`。如果需要启动 task 时自动复位，
设置 `reset_on_initialize: true`。
