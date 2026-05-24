# CoRobot MCP Server

这是一个用于连接 CoRobot 系统的 MCP (Model Context Protocol) 服务器。

## 功能

该服务器提供了以下工具，用于控制 CoRobot PolicyTask：

- `start_task`: 启动 PolicyTask 任务
- `stop_task`: 停止 PolicyTask 任务
- `reset_task`: 重置 PolicyTask 到初始状态
- `set_prompt`: 设置任务提示词
- `get_prompt`: 获取当前任务提示词
- `set_evaluate_params`: 设置评估参数（包括策略服务器配置）
- `get_status`: 获取 PolicyTask 的详细状态

同时也提供 deterministic primitive tools。这些工具不直接持有机器人环境，只转发到 CoRobot `RuleControlTask` 暴露的 `/skill/...` API：

- `get_skill_status`: 获取 deterministic skill task 状态
- `reset_robot`: 调用 deterministic skill 的安全复位，先复位夹爪，再复位双臂、头部和腰部
- `get_eef_pose`: 读取指定左/右臂当前夹爪中心 TCP 位姿，同时返回底层 wrist/link7 位姿
- `detect_tags`: 刷新 AprilTag 检测缓存
- `get_apriltag_pose`: 按 `tag_id` 查询相机坐标系位姿
- `move_eef`: 使用相机坐标系夹爪中心 TCP 目标移动左/右臂，底层仍下发 wrist/link7 轨迹
- `lift_eef`: 沿已配置 camera-frame 抬升轴移动 EEF
- `place_down`: 沿已配置 camera-frame 下降轴放置并可打开夹爪
- `open_gripper`: 打开指定夹爪
- `close_gripper`: 关闭指定夹爪
- `grasp_by_tag`: 根据 tag 坐标执行 approach、descend、close、lift

这些 primitive tools 的控制频率固定为 `30Hz`。Tool schema 不暴露 `control_hz`，如果调用参数中包含 `control_hz` 或 `control_frequency_hz` 会直接拒绝。

## 配置

服务器默认连接到 `http://localhost:8765` 的 CoRobot API。

确保 CoRobot 服务正在运行，并且可以通过该地址访问。

## 使用

该服务器通过 MCP 协议与 Olympus Agent 系统集成，在 `ormcp_services.json` 中配置后即可使用。

典型调用顺序：

1. `get_skill_status` 确认 CoRobot skill task、calibration 和 perception ready。
2. 需要回到初始姿态时调用 `reset_robot`。
3. `get_eef_pose` 可用于读取当前夹爪中心 TCP 位置并做小范围移动测试。
4. `detect_tags` 或 `get_apriltag_pose` 获取相机坐标系目标。
5. `move_eef`、`grasp_by_tag`、`lift_eef` 或 `place_down` 执行确定性 primitive。

示例：

```json
{
  "arm": "right",
  "camera_frame": "head_camera_optical",
  "target_position_camera_m": [0.02, -0.04, 0.32],
  "duration_s": 1.5
}
```
