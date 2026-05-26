# mcp_control_demo.control

控制层只接受相机坐标系目标，执行前再转换成 CoRobot `Action`。

对外 `move_eef/lift_eef/place_down` 的 EEF 目标统一解释为
Omnipicker 夹爪中心 TCP，不是 A2D 底层的 wrist/link7 frame。底层 A2D
controller 仍然只接收 `arm_left_link7/arm_right_link7` 的 `EEF_ABS` 轨迹；
`mcp_control_demo` 会在下发前减去固定 TCP 偏移：

```text
gripper_center_offset_link7_m = [0.0, 0.0, 0.14308]
wrist_target = desired_gripper_center - R_wrist * gripper_center_offset_link7_m
```

## 30Hz Rule

所有 primitive 固定：

```python
CONTROL_HZ = 30.0
CONTROL_DT_S = 1.0 / 30.0
```

任意 `duration_s` 都会向上取整到 30Hz 采样点：

```text
num_steps = max(1, ceil(duration_s * 30))
actual_duration_s = num_steps / 30
```

`Action.trajectory_reference_time` 永远等于 `actual_duration_s`。Tool schema 不提供 `control_hz`。

## Common Parameters

```json
{
  "arm": "right",
  "camera_frame": "head_camera_optical",
  "duration_s": 1.5
}
```

- `arm`: `left` 或 `right`。
- `target_position_camera_m`: 夹爪中心 TCP 的 camera-frame `[x, y, z]`，单位米。
- `target_orientation_camera_xyzw`: 可选，缺省时保留当前 EEF 姿态。
- `gripper_value`: 可选，`0.0=open`，`1.0=close`。

## Examples

移动右臂夹爪中心 TCP：

```json
{
  "arm": "right",
  "camera_frame": "head_camera_optical",
  "target_position_camera_m": [0.02, -0.04, 0.32],
  "duration_s": 1.5
}
```

抬升左臂 EEF：

```json
{
  "arm": "left",
  "camera_frame": "head_camera_optical",
  "distance_m": 0.08,
  "duration_s": 1.0
}
```

放下并打开右夹爪：

```json
{
  "arm": "right",
  "camera_frame": "head_camera_optical",
  "down_distance_m": 0.08,
  "open_after_down": true,
  "duration_s": 1.0
}
```
