# mcp_control_demo.perception

感知层从 CoRobot observation 中读取相机图像和内参，使用 AprilTag 检测，按 `tag_id` 缓存相机坐标系位姿。
如果 observation 没带 camera params，会使用 calibration 配置里的 `intrinsics` 作为 fallback。

默认参数：

```text
camera_name = head
camera_frame = head_camera_optical
tag_family = tag25h9
tag_size_m = 0.018
```

每个 tag 的核心输出：

```json
{
  "tag_id": 3,
  "tag_family": "tag25h9",
  "camera_name": "head",
  "camera_frame": "head_camera_optical",
  "position_camera_m": [0.02, -0.04, 0.32],
  "translation_m": [0.02, -0.04, 0.32],
  "distance_m": 0.323,
  "rotation_matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
  "euler_rpy_deg": [0.0, 0.0, 0.0],
  "center_px": [640.0, 360.0],
  "corners_px": [[...], [...], [...], [...]]
}
```

注意事项：

- 输出坐标是相机坐标系，不是机器人 `base_link`。
- 若 `intrinsics` 包含 `distortion_coefficients`，检测前会先做 undistort，并使用矫正后的内参估计 pose。
- 控制层会通过 calibration 的 `T_exec_camera` 转换到执行坐标系。
- 检测结果有 freshness 限制，默认 `stale_after_s=1.0`。
- `get_tag_pose` 若缓存不存在会触发一次新的 detection。
