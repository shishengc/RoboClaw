# mcp_control_demo.calibration

Calibration 负责把对外相机坐标系目标转换为 CoRobot 执行坐标系目标。

最小配置：

```yaml
camera_frame: head_camera_optical
exec_frame: base_link
T_exec_camera:
  - [1.0, 0.0, 0.0, 0.0]
  - [0.0, 1.0, 0.0, 0.0]
  - [0.0, 0.0, 1.0, 0.0]
  - [0.0, 0.0, 0.0, 1.0]
camera_approach_axis: [0.0, 0.0, -1.0]
camera_lift_axis: [0.0, -1.0, 0.0]
camera_place_down_axis: [0.0, 1.0, 0.0]
```

如果只有相机内参、还没有相机到执行坐标系的外参，可以先写 `intrinsics`
并把 `T_exec_camera` 留空。此时 AprilTag 感知可以使用配置里的
camera params 作为 fallback，但 `move_eef/lift_eef/place_down`
这类真实控制 primitive 会拒绝执行，直到补齐 `T_exec_camera`。

当前已根据 `/home/ck/robot_test/calib_output/camera_params_20260520_111317.yaml`
生成配置：

```text
src/mcp_control_demo/config/mcp_control_calibration_head_camera_20260520.yaml
src/mcp_control_demo/config/mcp_control_task_head_camera.yaml
src/mcp_control_demo/config/corobot_app_mcp_control.yaml
```

字段含义：

- `T_exec_camera`: 已标定外参，满足 `p_exec = T_exec_camera * p_camera`。
- `intrinsics`: 相机内参、畸变和图像尺寸；可作为 AprilTag pose estimation 的 fallback camera params。
- `exec_frame`: CoRobot `Action.base_link` 使用的坐标系，默认 `base_link`。
- `camera_approach_axis`: 抓取接近方向，在 camera frame 中定义。
- `camera_lift_axis`: 抬升方向，在 camera frame 中定义。
- `camera_place_down_axis`: 放置下降方向，在 camera frame 中定义。

如果缺少 `T_exec_camera`，控制层必须拒绝执行；不能把 camera-frame 坐标直接发给 CoRobot controller。
