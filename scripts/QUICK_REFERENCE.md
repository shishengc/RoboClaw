# 快速参考卡片

## 🚀 快速开始（3 步）

### 1️⃣ 安装依赖
```bash
pip install depth-anything-v2 torch torchvision opencv-python numpy loguru
```

或一次性安装：
```bash
pip install -r scripts/requirements_depth_estimation.txt
```

### 2️⃣ 运行快速开始脚本
```bash
python scripts/quick_start_depth.py
```

### 3️⃣ 选择图像来源
- **选项 1**: 从机器人头部相机获取
- **选项 2**: 从本地文件读取
- **选项 3**: 查看示例代码

---

## 📝 常见命令

### 从机器人获取图像并生成深度图
```bash
python scripts/generate_depth_and_3d_coords.py \
    --base-url http://localhost:8765 \
    --depth-model small \
    --device cuda
```

### 从本地图像文件读取
```bash
python scripts/generate_depth_and_3d_coords.py \
    --no-robot \
    --image-path /path/to/image.jpg \
    --depth-model small
```

### 使用 CPU 处理（速度慢但节省内存）
```bash
python scripts/generate_depth_and_3d_coords.py \
    --no-robot \
    --image-path image.jpg \
    --device cpu
```

### 指定相机内参
```bash
# 方法 1：使用视场角（推荐）
python scripts/generate_depth_and_3d_coords.py ... --fov 90

# 方法 2：使用焦距
python scripts/generate_depth_and_3d_coords.py ... --focal-length 920
```

---

## 🔧 基础 Python 示例

### 查询单个点的 3D 坐标
```python
from scripts.generate_depth_and_3d_coords import PointTo3D, CameraIntrinsics
import numpy as np

# 加载深度图
depth_m = np.load("artifacts/depth_estimation/depth_map.npy")

# 创建转换器
intrinsics = CameraIntrinsics.from_fov(1280, 960, fov_degree=90)
converter = PointTo3D(intrinsics)

# 查询像素 (640, 480) 的 3D 坐标
u, v = 640, 480
depth = depth_m[v, u]
x, y, z = converter.pixel_to_3d(u, v, depth)
print(f"3D坐标: ({x:.3f}, {y:.3f}, {z:.3f}) 米")
```

### 生成点云
```python
from scripts.generate_depth_and_3d_coords import PointTo3D, CameraIntrinsics
import numpy as np

depth_m = np.load("artifacts/depth_estimation/depth_map.npy")
intrinsics = CameraIntrinsics.from_fov(depth_m.shape[1], depth_m.shape[0])
converter = PointTo3D(intrinsics)

# 生成点云
point_cloud = converter.pixel_to_3d_array(depth_m)  # 形状: (H, W, 3)

# 筛选有效点
valid_mask = depth_m > 0.1
valid_points = point_cloud[valid_mask]  # 形状: (N, 3)
print(f"点云包含 {len(valid_points)} 个点")
```

### 目标检测（抓取点定位）
```python
from scripts.advanced_grasping_examples import GraspingTargetDetector
from scripts.generate_depth_and_3d_coords import CameraIntrinsics
import cv2
import numpy as np

# 读取图像和深度图
image = cv2.imread("test.jpg")
image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
depth_m = np.load("depth.npy")

# 创建检测器
intrinsics = CameraIntrinsics.from_fov(image.shape[1], image.shape[0])
detector = GraspingTargetDetector(intrinsics)

# 检测目标
targets = detector.detect_by_brightness(image, depth_m, brightness_threshold=200)

# 获取最大目标的 3D 坐标
if targets:
    best_target = targets[0]
    x3d, y3d, z3d = best_target['position_3d']
    print(f"最大目标位置: ({x3d:.3f}, {y3d:.3f}, {z3d:.3f}) 米")
```

---

## 📚 相机配置

### 列出所有预定义配置
```bash
python scripts/camera_config.py
```

### 在代码中使用预定义配置
```python
from scripts.camera_config import (
    CameraConfigManager,
    AGIBOT_G01_HEAD,
    REALSENSE_D435,
)
from scripts.generate_depth_and_3d_coords import CameraIntrinsics

# 方法 1：使用预定义常量
config = AGIBOT_G01_HEAD
intrinsics = CameraIntrinsics(fx=config.fx, fy=config.fy, cx=config.cx, cy=config.cy)

# 方法 2：从管理器加载
manager = CameraConfigManager()
config = manager.load_config("agibot_g01_head")
```

---

## ⚙️ 模型选择

| 模型 | 大小 | 速度 | 精度 | 推荐场景 |
|------|------|------|------|---------|
| `small` | ~25MB | ⚡⚡ 快 | ⭐⭐⭐ 中等 | 实时应用 |
| `base` | ~350MB | ⚡ 中等 | ⭐⭐⭐⭐ 高 | 平衡方案 |
| `large` | ~1.3GB | 慢 | ⭐⭐⭐⭐⭐ 最高 | 离线处理 |

---

## 📁 文件结构

```
scripts/
├── generate_depth_and_3d_coords.py    # 核心脚本
├── quick_start_depth.py               # 快速开始
├── camera_config.py                   # 相机配置管理
├── depth_estimation_examples.py       # 基础示例
├── advanced_grasping_examples.py      # 高级示例
├── DEPTH_ESTIMATION_GUIDE.md          # 详细指南
└── requirements_depth_estimation.txt  # 依赖项

artifacts/
├── depth_estimation/
│   ├── depth_colored.png              # 彩色深度图
│   ├── depth_map.npy                  # 原始深度图
│   └── depth_3d_points.txt            # 交互选中的点
├── grasping_results/                  # 抓取结果
└── point_cloud_example/               # 点云示例
```

---

## 🐛 故障排除

### 问题：导入 Depth Anything 失败
**解决方案：**
```bash
pip install depth-anything-v2 torch
```

### 问题：CUDA 内存不足
**解决方案：**
```bash
# 使用 CPU
python scripts/generate_depth_and_3d_coords.py --device cpu --depth-model small

# 或使用更小的模型
python scripts/generate_depth_and_3d_coords.py --depth-model small
```

### 问题：无法连接到机器人
**解决方案：**
```bash
# 检查机器人服务
curl http://localhost:8765/

# 使用本地图像测试
python scripts/generate_depth_and_3d_coords.py --no-robot --image-path test.jpg
```

### 问题：深度值不合理
**解决方案：**
- 调整 `--fov` 参数
- 检查相机内参设置
- 验证图像分辨率

---

## 📊 输出格式

### 深度图 (depth_map.npy)
```python
import numpy as np
depth_m = np.load("artifacts/depth_estimation/depth_map.npy")
print(depth_m.shape)      # (H, W)
print(depth_m.dtype)      # float32
print(depth_m.min(), depth_m.max())  # 最小最大深度
```

### 3D 坐标查询结果
```
深度图三维坐标结果
==============================================================

点 #1:
  像素坐标: (320, 240)
  深度值: 0.753 m
  3D坐标: X=0.085m, Y=0.064m, Z=0.753m

点 #2:
  像素坐标: (640, 480)
  深度值: 0.500 m
  3D坐标: X=0.000m, Y=0.000m, Z=0.500m
```

### 抓取结果 (grasping_results_*.json)
```json
{
  "timestamp": "2026-05-18 10:30:45",
  "targets": [
    {
      "id": 0,
      "position_3d": [0.085, 0.064, 0.753],
      "confidence": 0.92,
      "depth_m": 0.753
    }
  ]
}
```

---

## 💡 进阶技巧

### 批量处理多个图像
```bash
for img in artifacts/test_camera/*.jpg; do
    python scripts/generate_depth_and_3d_coords.py --no-robot --image-path "$img"
done
```

### 导出点云为 PLY 格式
```python
# 见 depth_estimation_examples.py 中的 save_point_cloud_ply()
```

### 自定义相机配置并保存
```python
from scripts.camera_config import CameraConfig, CameraConfigManager

config = CameraConfig(
    name="my_camera",
    resolution_width=1280,
    resolution_height=960,
    fx=920,
    fy=920,
    cx=640,
    cy=480,
    description="我的自定义相机"
)

manager = CameraConfigManager()
manager.save_config(config)
```

---

## 📖 更多信息

详见 `scripts/DEPTH_ESTIMATION_GUIDE.md`

---

**最后更新**: 2026-05-18  
**版本**: 1.0
