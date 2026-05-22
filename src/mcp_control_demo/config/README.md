# mcp_control_demo.config

这里放真实机器人运行时会用到的默认配置。

- `mcp_control_calibration_head_camera_20260520.yaml`: 根据
  `/home/ck/robot_test/calib_output/camera_params_20260520_111317.yaml`
  填入相机内参、畸变和图像尺寸。
- `mcp_control_task_head_camera.yaml`: `RuleControlTask` 配置，引用上面的 calibration。
- `corobot_app_mcp_control.yaml`: CoRobot app 启动入口示例，默认加载 `corobot.policy_tasks.rule_control_task.RuleControlTask`。

注意：当前 calibration 只具备相机内参，不具备 `T_exec_camera` 外参。
感知可以使用这份配置；真实控制前必须补齐 `T_exec_camera`，否则控制层会拒绝执行。
