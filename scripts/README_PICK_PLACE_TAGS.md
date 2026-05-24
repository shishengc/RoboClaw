# Tag0 抓取到 Tag1 放置脚本说明

本文档记录当前基于 mcp_control 原子动作实现的 AprilTag 抓取放置功能。

对应脚本：

```bash
scripts/test_pick_tag0_place_on_tag1_base_offset.sh
```

目标流程：

```text
从 tag id 0 的位置抓取目标物体，再放置到 tag id 1 的位置上。
```

## 控制流程

脚本不调用 `grasp_by_tag` 这种高层固定动作，而是依次调用底层原子动作：

```text
1. /skill/detect_tags
2. /skill/get_tag_pose, 查询 tag 0
3. /skill/get_tag_pose, 查询 tag 1
4. /skill/gripper, 打开右夹爪
5. /skill/move_eef, 移动到 tag 0 抓取预靠近点
6. /skill/move_eef, 移动到 tag 0 抓取点
7. /skill/gripper, 关闭右夹爪
8. /skill/move_eef, 抓取后沿 base_link +Z 抬起
9. /skill/move_eef, 移动到 tag 1 放置上方
10. /skill/move_eef, 下降到 tag 1 放置点
11. /skill/gripper, 打开右夹爪释放
```

## 基本用法

干跑，只打印计划，不执行机器人动作：

```bash
ARM=right bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
  0.00 0.00 -0.07
```

实际执行：

```bash
ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
  0.00 0.00 -0.07
```

如果抓取 offset 和放置 offset 不同，传 6 个参数：

```bash
ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
  <grasp_dx> <grasp_dy> <grasp_dz> \
  <place_dx> <place_dy> <place_dz>
```

## 参数含义

所有 `dx dy dz` 都是在 `base_link` 坐标系下的偏移，单位是米。

```text
+X: base_link X 方向，通常对应机器人前后方向
+Y: base_link Y 方向，通常对应机器人左右方向
+Z: base_link Z 方向，通常对应机器人向上方向
```

例如：

```bash
0.00 0.00 -0.07
```

表示相对 tag 中心：

```text
X 不偏移
Y 不偏移
Z 向下 7 cm
```

前三个参数是 tag 0 抓取偏移：

```text
grasp_base = tag0_base + [grasp_dx, grasp_dy, grasp_dz]
```

后三个参数是 tag 1 放置偏移：

```text
place_base = tag1_base + [place_dx, place_dy, place_dz]
```

如果只传 3 个参数，脚本会默认：

```text
place_offset = grasp_offset
```

## 常用环境变量

```bash
ARM=right
SOURCE_TAG_ID=0
DEST_TAG_ID=1
EXECUTE_PICK_PLACE=1
MOVE_DURATION_S=2.0
GRIPPER_DURATION_S=0.5
APPROACH_DISTANCE_M=0.06
LIFT_DZ_BASE_M=0.10
PLACE_HOVER_DZ_BASE_M=0.10
COROBOT_URL=http://localhost:8765
```

说明：

```text
APPROACH_DISTANCE_M:
  抓取前沿 camera_approach_axis 退开的距离，默认 0.06 m。

LIFT_DZ_BASE_M:
  抓取闭爪后沿 base_link +Z 抬起的高度，默认 0.10 m。

PLACE_HOVER_DZ_BASE_M:
  移动到 tag 1 放置点前，先到放置点上方的高度，默认 0.10 m。
```

## 已记录参数组

### 轴承装配

```bash
ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
  0.00 0.00 -0.07 \
  -0.01 -0.02 0.02
```

含义：

```text
抓取 tag 0:
  [0.00, 0.00, -0.07]

放置到 tag 1:
  [-0.01, -0.02, 0.02]
```

### 分拣

```bash
ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
  0.00 0.00 -0.05 \
  -0.01 -0.02 0.16
```

含义：

```text
抓取 tag 0:
  [0.00, 0.00, -0.05]

放置到 tag 1:
  [-0.01, -0.02, 0.16]
```

### 抽屉上下料

```bash
ARM=right EXECUTE_PICK_PLACE=1 bash scripts/test_pick_tag0_place_on_tag1_base_offset.sh \
  0.00 0.00 -0.07 \
  -0.01 -0.02 0.12
```

含义：

```text
抓取 tag 0:
  [0.00, 0.00, -0.07]

放置到 tag 1:
  [-0.01, -0.02, 0.12]
```

## 注意事项

- 执行前需要确保 CoRobot 服务已启动，默认地址是 `http://localhost:8765`。
- `tag 0` 和 `tag 1` 都需要在当前相机视野中可检测，脚本会先执行 `detect_tags`。
- 如果 `/skill/get_tag_pose` 返回 stale 或 tag 不存在，脚本会停止，不会继续移动机器人。
- `target_position_camera_m` 在控制层语义是夹爪中心 TCP 目标点，底层会转换成 wrist/link7 的 A2D 轨迹。
- 放置动作当前是先移动到 tag 1 上方，再移动到放置点，然后打开夹爪释放。
