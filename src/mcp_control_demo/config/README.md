# mcp_control_demo.config

这里放真实机器人运行时会用到的默认配置。

- `mcp_control_calibration.yaml`: 当前 head 相机内参、静态 `base_link <- camera`
  外参和 camera-frame 控制轴。
- `rule_control_app_config.yaml`: CoRobot app 启动入口示例，默认加载
  `corobot.policy_tasks.rule_control_task.RuleControlTask`。

## 重新生成 mcp_control calibration

如果更换了相机内参、head 相机外参或 URDF，需要重新生成
`mcp_control_calibration.yaml`，不要手写 FK。生成逻辑应按当前静态控制约定合成：

```text
T_exec_camera = T_base_head_pitch(reset) * T_head_pitch_camera
```

其中 `T_base_head_pitch(reset)` 来自 CoRobot/Pinocchio
`Kinematics.compute_head_fk()`，`T_head_pitch_camera` 来自外参文件。重新生成后也要同步
`.a2d_pkg/corobot/config/rule_control_task_config.yml` 中的 `calibration_path`、
`perception.camera_frame`、`perception.tag_size_m` 和
`reset_pose.target_head_positions/target_waist_positions`。

生成后需要重启 CoRobot app，配置不会热加载。
