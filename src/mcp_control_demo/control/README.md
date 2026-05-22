# mcp_control_demo.control

控制层只接受相机坐标系目标，执行前再转换成 CoRobot `Action`。

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
- `target_position_camera_m`: camera-frame `[x, y, z]`，单位米。
- `target_orientation_camera_xyzw`: 可选，缺省时保留当前 EEF 姿态。
- `gripper_value`: 可选，`0.0=open`，`1.0=close`。

## Examples

移动右臂 EEF：

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

按 tag 抓取：

```json
{
  "arm": "right",
  "tag_id": 3,
  "camera_frame": "head_camera_optical",
  "approach_distance_m": 0.06,
  "lift_height_m": 0.10,
  "move_duration_s": 1.0,
  "gripper_duration_s": 0.5
}
```
