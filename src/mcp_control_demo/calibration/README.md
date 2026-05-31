# mcp_control_demo.calibration

Calibration 负责把对外相机坐标系目标转换为 CoRobot 执行坐标系目标。

当前只支持动态 FK：

```text
T_exec_camera(q) = T_base_head_pitch(q) * T_head_pitch_camera
```

`T_head_pitch_camera` 是固定安装外参，写在：

```text
/home/ck/RoboClaw/.a2d_pkg/corobot/config/rule_control_task_config.yml
```

`T_base_head_pitch(q)` 不写入配置；`RuleControlTask` 每次构造控制动作前从 observation 读取当前 `head_joint_states` 和 `waist_joint_states`，再用 CoRobot/Pinocchio FK 计算。

配置中不再保存预合成的 `T_exec_camera`，也不再支持 `calibration_path` 指向 standalone calibration 文件。
