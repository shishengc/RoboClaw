# 故障排查指南 - 深度估计系统

## 问题：无法从机器人获取图像

### 症状
```
✗ 失败
无法获取图像，请检查机器人连接
```

### 诊断步骤

#### 第 1 步：运行诊断工具

```bash
python scripts/diagnose_depth_setup.py
```

这会检查：
- ✓ Python 版本
- ✓ 依赖包
- ✓ GPU 支持
- ✓ 项目结构
- ✓ DataLoaderCoRobot 可用性
- ✓ Depth Anything 模型
- ✓ 机器人连接

#### 第 2 步：根据诊断结果采取行动

### 常见问题和解决方案

---

## ❌ 问题 1: DataLoaderCoRobot 不可用

**症状：**
```
WARNING  | 无法导入 DataLoaderCoRobot
```

**可能原因：**
1. 不在 RoboClaw 项目目录中运行
2. corobot 虚拟环境未配置
3. a2d_sdk 未安装

**解决方案：**

```bash
# 确保在项目根目录
cd ~/RoboClaw

# 检查 corobot 虚拟环境
ls ~/corobot/lib/python3.10/site-packages/a2d_sdk/

# 如果不存在，需要安装 corobot SDK
# 联系 AgiBot 获取安装文件

# 暂时不使用机器人，改用本地图像
python scripts/generate_depth_and_3d_coords.py \
    --no-robot \
    --image-path test_image.jpg
```

---

## ❌ 问题 2: 机器人服务离线

**症状：**
```
ERROR | 正在连接机器人服务: http://localhost:8765
ERROR | 无法从机器人获取图像
```

**排查步骤：**

```bash
# 1. 检查机器人服务是否运行
curl http://localhost:8765/

# 2. 检查网络连接
ping localhost

# 3. 检查机器人 IP 地址
ip addr show

# 4. 检查 DDS 环境变量
echo $LOCATOR_IP
echo $AORTA_DISCOVERY_URI

# 5. 重启机器人服务
# (联系 AgiBot 支持)
```

**解决方案：**

```bash
# 使用本地图像测试
python scripts/generate_depth_and_3d_coords.py \
    --no-robot \
    --image-path image.jpg
```

---

## ❌ 问题 3: 依赖包缺失

**症状：**
```
ERROR | 无法导入 Depth Anything 或 PyTorch
```

**解决方案：**

```bash
# 安装所有依赖
pip install -r scripts/requirements_depth_estimation.txt

# 或单独安装关键包
pip install depth-anything-v2 torch torchvision

# 验证安装
python -c "import depth_anything; print('OK')"
python -c "import torch; print('OK')"
```

---

## ❌ 问题 4: CUDA/GPU 问题

**症状：**
```
CUDA out of memory
```

**解决方案：**

```bash
# 使用 CPU 处理（速度慢但节省内存）
python scripts/generate_depth_and_3d_coords.py \
    --device cpu \
    --depth-model small

# 使用较小的模型
python scripts/generate_depth_and_3d_coords.py \
    --depth-model small

# 查看 GPU 内存使用
nvidia-smi
```

---

## ❌ 问题 5: 无法访问本地图像

**症状：**
```
ERROR | 无法读取图像
```

**解决方案：**

```bash
# 检查图像文件是否存在
ls -la /path/to/image.jpg

# 尝试从 artifacts 目录获取测试图像
python scripts/test_camera.py

# 使用网络摄像头
python scripts/generate_depth_and_3d_coords.py --no-robot
```

---

## ✅ 快速修复步骤

### 方案 A: 使用本地图像（推荐）

```bash
# 第 1 步：获取测试图像
cd ~/RoboClaw
python scripts/test_camera.py --frames 1

# 第 2 步：处理本地图像
python scripts/generate_depth_and_3d_coords.py \
    --no-robot \
    --image-path artifacts/test_camera/head_image.jpg \
    --depth-model small
```

### 方案 B: 使用网络摄像头

```bash
python scripts/generate_depth_and_3d_coords.py \
    --no-robot \
    --depth-model small
```

### 方案 C: 使用快速开始工具

```bash
python scripts/quick_start_depth.py

# 选择选项 2（从本地文件读取）
```

---

## 🔍 高级诊断

### 启用详细日志

```bash
# 通过设置日志级别查看更详细信息
LOGURU_LEVEL=DEBUG python scripts/generate_depth_and_3d_coords.py --no-robot --image-path test.jpg
```

### 检查 Python 路径

```bash
python -c "import sys; print('\n'.join(sys.path))"

# 确保包含 RoboClaw 项目路径
python -c "import agent_demo; print(agent_demo.__file__)"
```

### 验证模型下载

```bash
# 检查 Depth Anything 模型是否已下载
ls ~/.cache/huggingface/hub/

# 如果需要手动下载
python scripts/depth_estimation_examples.py
```

---

## 📋 完整的诊断检查清单

- [ ] Python 3.10+ 已安装
- [ ] 所有依赖已安装（`pip install -r requirements_depth_estimation.txt`）
- [ ] 在 RoboClaw 项目根目录运行
- [ ] 测试图像存在且可访问
- [ ] 网络连接正常
- [ ] GPU 驱动已更新（如使用 GPU）
- [ ] CUDA 配置正确（如使用 GPU）
- [ ] 足够的磁盘空间（模型~1-2GB）
- [ ] 足够的内存（8GB 以上推荐）

---

## 🆘 如果仍然无法解决

### 提交诊断信息

运行以下命令收集诊断信息：

```bash
# 保存诊断结果
python scripts/diagnose_depth_setup.py > diagnostic_report.txt 2>&1

# 显示诊断报告
cat diagnostic_report.txt
```

### 获取帮助

1. 查看 `QUICK_REFERENCE.md` 获取快速参考
2. 查看 `DEPTH_ESTIMATION_GUIDE.md` 获取详细指南
3. 查看 `README_DEPTH_ESTIMATION.md` 获取完整说明
4. 检查脚本中的代码注释
5. 运行示例代码：`python scripts/depth_estimation_examples.py`

---

## 🎯 推荐的工作流程

### 最小化设置（快速测试）

```bash
# 1. 安装最小依赖
pip install depth-anything-v2 torch opencv-python numpy

# 2. 从网络摄像头测试
python scripts/generate_depth_and_3d_coords.py --no-robot --device cpu --depth-model small

# 3. 如果成功，逐步添加更多功能
```

### 完整设置（生产使用）

```bash
# 1. 安装完整依赖
pip install -r scripts/requirements_depth_estimation.txt

# 2. 运行诊断
python scripts/diagnose_depth_setup.py

# 3. 配置相机参数
python scripts/camera_config.py

# 4. 运行快速开始
python scripts/quick_start_depth.py
```

---

## 📞 技术支持

| 问题类别 | 建议 |
|---------|------|
| 环境问题 | 运行 `diagnose_depth_setup.py` |
| 性能问题 | 使用 `--depth-model small` 和 `--device cpu` |
| 机器人连接 | 检查 DDS 配置和网络连接 |
| 模型问题 | 检查 HuggingFace 网络连接 |
| 其他问题 | 查阅完整文档或提供诊断报告 |

---

**最后更新**: 2026-05-18
