"""
机器人头部相机深度估计和三维坐标计算脚本

功能：
1. 从机器人头部相机获取RGB图像
2. 使用 Depth Anything 生成深度图
3. 根据深度图和相机内参计算指定像素点的三维坐标
4. 支持交互式点击图像获取三维坐标
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
from loguru import logger

# 导入 DataLoaderCoRobot 来获取相机图像
try:
    from agent_demo.machine_layer.dataloader_corobot import DataLoaderCoRobot
except ImportError:
    logger.warning("无法导入 DataLoaderCoRobot，将使用本地图像或网络摄像头")
    DataLoaderCoRobot = None

# 导入 Depth Anything 模型和 PyTorch
DepthAnythingV2 = None
torch = None
DEPTH_ANYTHING_AVAILABLE = False


def try_import_depth_anything(root_path: Optional[str] = None) -> bool:
    """尝试导入 Depth Anything；支持本地克隆路径。"""
    global torch, DepthAnythingV2, DEPTH_ANYTHING_AVAILABLE
    if root_path is not None:
        root = Path(root_path).expanduser().resolve()
        if root.exists():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
        else:
            logger.warning(f"Depth Anything 根目录不存在: {root}")
            return False

    try:
        import torch as _torch
        try:
            dpt_module = importlib.import_module("depth_anything_v2.dpt")
            ImportedDepthAnythingV2 = getattr(dpt_module, "DepthAnythingV2")
        except ImportError:
            dpt_module = importlib.import_module("depth_anything.dpt")
            ImportedDepthAnythingV2 = getattr(dpt_module, "DepthAnything")

        torch = _torch
        DepthAnythingV2 = ImportedDepthAnythingV2
        DEPTH_ANYTHING_AVAILABLE = True
        return True
    except Exception as e:
        logger.warning(f"Depth Anything 导入失败: {e}")
        DEPTH_ANYTHING_AVAILABLE = False
        return False


# 尝试从环境变量加载本地 Depth Anything
try_import_depth_anything(os.environ.get("DEPTH_ANYTHING_ROOT"))


class CameraIntrinsics:
    """相机内参类"""
    def __init__(self, fx: float, fy: float, cx: float, cy: float):
        """
        初始化相机内参
        
        Args:
            fx: 焦距 x 方向
            fy: 焦距 y 方向
            cx: 主点 x 坐标
            cy: 主点 y 坐标
        """
        self.fx = fx
        self.fy = fy
        self.cx = cx
        self.cy = cy

    @staticmethod
    def from_fov(image_width: int, image_height: int, fov_degree: float = 90) -> CameraIntrinsics:
        """
        从视场角估计相机内参
        
        Args:
            image_width: 图像宽度
            image_height: 图像高度
            fov_degree: 水平视场角（度）
        """
        fov_rad = np.radians(fov_degree)
        fx = (image_width / 2) / np.tan(fov_rad / 2)
        fy = fx  # 假设方形像素
        cx = image_width / 2
        cy = image_height / 2
        return CameraIntrinsics(fx, fy, cx, cy)

    def __repr__(self) -> str:
        return f"CameraIntrinsics(fx={self.fx:.2f}, fy={self.fy:.2f}, cx={self.cx:.2f}, cy={self.cy:.2f})"


class DepthMapProcessor:
    """深度图处理类"""
    
    def __init__(
        self,
        model_type: str = "small",
        device: str = "cuda",
        depth_anything_root: Optional[str] = None,
        min_depth: float = 0.1,
        max_depth: float = 5.0,
    ):
        """
        初始化深度估计模型
        
        Args:
            model_type: 模型类型 ("small", "base", "large")
            device: 计算设备 ("cuda", "cpu")
            depth_anything_root: Depth Anything 本地克隆目录
            min_depth: 深度映射最小值（米），相对深度最小端对应此值
            max_depth: 深度映射最大值（米），相对深度最大端对应此值
        
        注意：Depth Anything V2 Small/Base/Large 输出相对深度（无真实单位），
              min_depth/max_depth 用于将其线性映射到近似物理范围。
              如需真实度量深度，请使用 metric_depth 版本模型。
        """
        if depth_anything_root is not None:
            try_import_depth_anything(depth_anything_root)
        elif not DEPTH_ANYTHING_AVAILABLE:
            try_import_depth_anything(os.environ.get("DEPTH_ANYTHING_ROOT"))

        if not DEPTH_ANYTHING_AVAILABLE:
            raise RuntimeError("Depth Anything 未安装或无法导入。请设置 DEPTH_ANYTHING_ROOT 或 pip install depth-anything-v2 torch")
        
        self.device = device
        self.model_type = model_type
        _root = depth_anything_root or os.environ.get("DEPTH_ANYTHING_ROOT")
        self.depth_anything_root = Path(_root).expanduser().resolve() if _root else None
        self.min_depth = min_depth
        self.max_depth = max_depth
        self.model = None
        self._initialize_model()

    def _initialize_model(self):
        """初始化模型和预处理"""
        logger.info(f"初始化 Depth Anything 模型 ({self.model_type}) 在 {self.device}...")

        if DepthAnythingV2 is None:
            raise RuntimeError("DepthAnythingV2 未导入成功")

        model_configs = {
            "small": {"encoder": "vits", "features": 64, "out_channels": [48, 96, 192, 384]},
            "base": {"encoder": "vitb", "features": 128, "out_channels": [96, 192, 384, 768]},
            "large": {"encoder": "vitl", "features": 256, "out_channels": [256, 512, 1024, 1024]},
        }
        checkpoint_name = {
            "small": "depth_anything_v2_vits.pth",
            "base": "depth_anything_v2_vitb.pth",
            "large": "depth_anything_v2_vitl.pth",
        }

        if self.model_type not in model_configs:
            raise ValueError(f"不支持的模型类型: {self.model_type}")

        self.model = DepthAnythingV2(**model_configs[self.model_type])

        checkpoint_candidates = []
        if self.depth_anything_root is not None:
            checkpoint_candidates.append(self.depth_anything_root / "checkpoints" / checkpoint_name[self.model_type])
        repo_root = Path(__file__).resolve().parent.parent
        checkpoint_candidates.append(repo_root / "checkpoints" / checkpoint_name[self.model_type])

        checkpoint_path = next((path for path in checkpoint_candidates if path.exists()), None)
        if checkpoint_path is None:
            candidate_text = "\n".join(f"  - {path}" for path in checkpoint_candidates)
            raise RuntimeError(
                "找不到 Depth Anything V2 权重文件。请先下载对应 checkpoint，或把它放到以下位置之一:\n"
                f"{candidate_text}"
            )

        logger.info(f"加载权重: {checkpoint_path}")
        state_dict = torch.load(checkpoint_path, map_location="cpu")
        self.model.load_state_dict(state_dict)
        self.model.to(self.device)
        self.model.eval()
        logger.info("模型初始化完成")

    def predict_depth(self, image: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        生成深度图
        
        Args:
            image: 输入 RGB 图像 (H, W, 3)
        
        Returns:
            depth_normalized: 归一化深度图 (H, W)，范围 [0, 1]
            depth_m: 实际深度图 (H, W)，单位米
        """
        if self.model is None:
            raise RuntimeError("模型未初始化")
        
        # 推理（infer_image 内部已处理 BGR→RGB 转换和预处理）
        with torch.no_grad():
            depth_raw = self.model.infer_image(image, input_size=518)
        
        # 将深度图缩放回原始分辨率
        depth_raw = cv2.resize(depth_raw, (image.shape[1], image.shape[0]))
        
        # Depth Anything V2 输出相对深度（raw值无物理单位）
        # 做 min-max 归一化到 [0, 1]
        d_min = float(depth_raw.min())
        d_max = float(depth_raw.max())
        depth_normalized = (depth_raw - d_min) / (d_max - d_min + 1e-8)
        
        # 线性映射到指定深度范围（米）
        # 注意：这是相对尺度的近似，不是真实度量深度
        depth_m = self.min_depth + depth_normalized * (self.max_depth - self.min_depth)
        
        return depth_normalized, depth_m

    def depth_to_colormap(self, depth: np.ndarray, colormap: int = cv2.COLORMAP_TURBO) -> np.ndarray:
        """
        将深度图转换为彩色图显示
        
        Args:
            depth: 深度图 (H, W)
            colormap: OpenCV 色图类型
        
        Returns:
            彩色深度图 (H, W, 3)
        """
        # 归一化到 0-255
        depth_normalized = ((depth - depth.min()) / (depth.max() - depth.min() + 1e-5) * 255).astype(np.uint8)
        colored = cv2.applyColorMap(depth_normalized, colormap)
        return colored


class PointTo3D:
    """图像坐标转3D世界坐标类"""
    
    def __init__(self, intrinsics: CameraIntrinsics):
        """
        初始化转换器
        
        Args:
            intrinsics: 相机内参
        """
        self.intrinsics = intrinsics

    def pixel_to_3d(self, u: int, v: int, depth_m: float) -> Tuple[float, float, float]:
        """
        将像素坐标和深度转换为3D相机坐标
        
        Args:
            u: 像素 x 坐标
            v: 像素 y 坐标
            depth_m: 深度值（米）
        
        Returns:
            (x, y, z) 相机坐标系中的三维坐标（米）
        """
        x = (u - self.intrinsics.cx) * depth_m / self.intrinsics.fx
        y = (v - self.intrinsics.cy) * depth_m / self.intrinsics.fy
        z = depth_m
        return x, y, z

    def pixel_to_3d_array(self, depth_map: np.ndarray) -> np.ndarray:
        """
        将整个深度图转换为3D点云
        
        Args:
            depth_map: 深度图 (H, W)
        
        Returns:
            点云 (H, W, 3)，每个像素的3D坐标
        """
        h, w = depth_map.shape
        u, v = np.meshgrid(np.arange(w), np.arange(h))
        
        x = (u - self.intrinsics.cx) * depth_map / self.intrinsics.fx
        y = (v - self.intrinsics.cy) * depth_map / self.intrinsics.fy
        z = depth_map
        
        point_cloud = np.stack([x, y, z], axis=-1)
        return point_cloud


class InteractiveDepthViewer:
    """交互式深度查看器"""
    
    def __init__(self, 
                 rgb_image: np.ndarray,
                 depth_map: np.ndarray,
                 converter: PointTo3D,
                 window_name: str = "Depth and 3D Coordinates"):
        """
        初始化查看器
        
        Args:
            rgb_image: RGB 图像
            depth_map: 深度图（米）
            converter: 像素转3D转换器
            window_name: 窗口名称
        """
        self.rgb_image = rgb_image
        self.depth_map = depth_map
        self.converter = converter
        self.window_name = window_name
        
        # 生成深度彩色图
        self.depth_colored = self._depth_to_color()
        
        # 并排显示 RGB 和深度图
        self.display_image = np.hstack([self.rgb_image, self.depth_colored])
        
        self.selected_points = []

    def _depth_to_color(self) -> np.ndarray:
        """将深度图转换为彩色显示"""
        depth_normalized = ((self.depth_map - self.depth_map.min()) / 
                            (self.depth_map.max() - self.depth_map.min() + 1e-5) * 255).astype(np.uint8)
        return cv2.applyColorMap(depth_normalized, cv2.COLORMAP_TURBO)

    def _mouse_callback(self, event, x, y, flags, param):
        """鼠标回调函数"""
        if event == cv2.EVENT_LBUTTONDOWN:
            # 检查是否在深度图区域
            if x >= self.rgb_image.shape[1]:
                # 在深度图区域
                depth_x = x - self.rgb_image.shape[1]
                depth_y = y
                depth_value = self.depth_map[depth_y, depth_x]
                
                # 转换到3D坐标
                x3d, y3d, z3d = self.converter.pixel_to_3d(depth_x, depth_y, depth_value)
                
                print(f"\n点击坐标: 图像(x={depth_x}, y={depth_y})")
                print(f"深度值: {depth_value:.3f} 米")
                print(f"3D 相机坐标: X={x3d:.3f}m, Y={y3d:.3f}m, Z={z3d:.3f}m")
                
                self.selected_points.append({
                    'pixel': (depth_x, depth_y),
                    'depth': depth_value,
                    'position_3d': (x3d, y3d, z3d)
                })

    def run(self):
        """运行交互式查看器"""
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.window_name, self._mouse_callback)
        
        print("\n=============== 交互式深度查看器 ===============")
        print("使用说明：")
        print("  - 在右侧深度图上点击以获取该点的3D坐标")
        print("  - 按 'q' 退出")
        print("  - 按 's' 保存所有选中点的信息")
        print("================================================\n")
        
        while True:
            cv2.imshow(self.window_name, self.display_image)
            key = cv2.waitKey(1) & 0xFF
            
            if key == ord('q'):
                break
            elif key == ord('s'):
                self._save_results()

        cv2.destroyAllWindows()
        return self.selected_points

    def _save_results(self):
        """保存选中点的信息"""
        if not self.selected_points:
            print("未选中任何点")
            return
        
        output_file = Path("artifacts") / "depth_3d_points.txt"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            f.write("深度图三维坐标结果\n")
            f.write("=" * 50 + "\n")
            for i, point in enumerate(self.selected_points):
                f.write(f"\n点 #{i+1}:\n")
                f.write(f"  像素坐标: {point['pixel']}\n")
                f.write(f"  深度值: {point['depth']:.3f} m\n")
                f.write(f"  3D坐标: X={point['position_3d'][0]:.3f}m, "
                       f"Y={point['position_3d'][1]:.3f}m, "
                       f"Z={point['position_3d'][2]:.3f}m\n")
        
        print(f"结果已保存到: {output_file}")


async def get_robot_image(base_url: str = "http://localhost:8765",
                         max_attempts: int = 20,
                         interval: float = 0.25) -> Optional[np.ndarray]:
    """
    从机器人头部相机获取图像
    
    Args:
        base_url: 机器人 API 服务地址
        max_attempts: 最大尝试次数
        interval: 重试间隔（秒）
    
    Returns:
        RGB 图像或 None
    """
    if DataLoaderCoRobot is None:
        logger.error("="*60)
        logger.error("无法从机器人获取图像")
        logger.error("="*60)
        logger.error("原因: DataLoaderCoRobot 不可用")
        logger.error("")
        logger.error("请尝试以下方案:")
        logger.error("1. 使用本地图像文件:")
        logger.error("   python scripts/generate_depth_and_3d_coords.py --no-robot --image-path image.jpg")
        logger.error("")
        logger.error("2. 使用网络摄像头:")
        logger.error("   python scripts/generate_depth_and_3d_coords.py --no-robot")
        logger.error("")
        logger.error("3. 运行快速开始工具:")
        logger.error("   python scripts/quick_start_depth.py")
        logger.error("="*60)
        return None
    
    dataloader = None
    try:
        logger.info(f"正在连接机器人服务: {base_url}")
        dataloader = DataLoaderCoRobot(format="jpg", base_url=base_url)
        logger.info("DataLoaderCoRobot 初始化成功")
        
        for attempt in range(1, max_attempts + 1):
            data = await dataloader.get_latest_concatenate_image_base64()
            if data is None or data.head_image is None:
                logger.info(f"[{attempt}/{max_attempts}] 等待相机帧...")
                await asyncio.sleep(interval)
                continue
            
            logger.info(f"[{attempt}/{max_attempts}] 获取到头部相机图像，形状: {data.head_image.shape}")
            return data.head_image
        
        logger.error("未能获取相机图像 - 已达到最大尝试次数")
        return None
    
    except Exception as e:
        logger.error(f"获取机器人图像出错: {e}")
        logger.error("请检查:")
        logger.error(f"  • 机器人服务是否在线: {base_url}")
        logger.error("  • 网络连接是否正常")
        logger.error("  • 机器人 DDS 环境是否配置正确")
        return None
    
    finally:
        if dataloader is not None:
            try:
                dataloader.shutdown()
            except Exception as e:
                logger.warning(f"关闭 DataLoaderCoRobot 时出错: {e}")


async def main():
    parser = argparse.ArgumentParser(description="机器人头部相机深度估计和三维坐标计算")
    parser.add_argument("--image-path", type=str, default=None, help="输入图像路径（用于测试）")
    parser.add_argument("--depth-model", choices=["small", "base", "large"], default="small", 
                       help="Depth Anything 模型类型")
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda", help="计算设备")
    parser.add_argument("--no-robot", action="store_true", help="不使用机器人，仅从文件读取")
    parser.add_argument("--base-url", type=str, default="http://localhost:8765", help="机器人 API URL")
    parser.add_argument("--output-dir", type=str, default="artifacts/depth_estimation", help="输出目录")
    parser.add_argument("--focal-length", type=float, default=None, help="相机焦距（像素）")
    parser.add_argument("--fov", type=float, default=90, help="相机水平视场角（度）")
    parser.add_argument("--depth-anything-root", type=str, default=None, help="Depth Anything 本地克隆目录")
    parser.add_argument("--min-depth", type=float, default=0.1, help="深度映射最小值（米），默认 0.1")
    parser.add_argument("--max-depth", type=float, default=3.0, help="深度映射最大值（米），默认 3.0（机器人抢据场景）")
    
    args = parser.parse_args()
    
    # 配置输出目录
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 获取图像
    if args.no_robot or DataLoaderCoRobot is None:
        logger.info("从本地文件读取图像")
        if args.image_path is None:
            # 使用网络摄像头
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                logger.error("无法打开摄像头")
                return 1
            ret, rgb_image = cap.read()
            cap.release()
            if not ret:
                logger.error("无法读取摄像头帧")
                return 1
            rgb_image = cv2.cvtColor(rgb_image, cv2.COLOR_BGR2RGB)
        else:
            rgb_image = cv2.imread(args.image_path)
            if rgb_image is None:
                logger.error(f"无法读取图像: {args.image_path}")
                return 1
            rgb_image = cv2.cvtColor(rgb_image, cv2.COLOR_BGR2RGB)
    else:
        # 从机器人获取图像
        rgb_image = await get_robot_image(args.base_url)
        if rgb_image is None:
            logger.error("无法从机器人获取图像")
            return 1
    
    logger.info(f"图像大小: {rgb_image.shape}")
    
    # 初始化深度估计模型
    if not DEPTH_ANYTHING_AVAILABLE:
        logger.error("请先安装 Depth Anything V2，或把本地 clone 根目录加入 DEPTH_ANYTHING_ROOT")
        return 1
    
    try:
        depth_processor = DepthMapProcessor(
            model_type=args.depth_model,
            device=args.device,
            depth_anything_root=args.depth_anything_root,
            min_depth=args.min_depth,
            max_depth=args.max_depth,
        )
    except Exception as e:
        logger.error(f"初始化深度模型失败: {e}")
        return 1
    
    # 生成深度图
    logger.info("生成深度图...")
    try:
        depth_normalized, depth_m = depth_processor.predict_depth(rgb_image)
    except Exception as e:
        logger.error(f"深度估计失败: {e}")
        return 1
    
    logger.info(f"深度范围: {depth_m.min():.3f} - {depth_m.max():.3f} 米")
    
    # 保存深度图
    depth_colored = depth_processor.depth_to_colormap(depth_normalized)
    cv2.imwrite(str(output_dir / "depth_colored.png"), depth_colored)
    np.save(str(output_dir / "depth_map.npy"), depth_m)
    logger.info(f"深度图已保存到 {output_dir}")
    
    # 配置相机内参
    if args.focal_length is not None:
        intrinsics = CameraIntrinsics(
            fx=args.focal_length,
            fy=args.focal_length,
            cx=rgb_image.shape[1] / 2,
            cy=rgb_image.shape[0] / 2
        )
    else:
        intrinsics = CameraIntrinsics.from_fov(rgb_image.shape[1], rgb_image.shape[0], args.fov)
    
    logger.info(f"相机内参: {intrinsics}")
    
    # 创建点转3D转换器
    converter = PointTo3D(intrinsics)
    
    # 启动交互式查看器
    viewer = InteractiveDepthViewer(rgb_image, depth_m, converter)
    selected_points = viewer.run()
    
    logger.info(f"共选中 {len(selected_points)} 个点")

    return 0


if __name__ == "__main__":
    if not DEPTH_ANYTHING_AVAILABLE:
        logger.error("Depth Anything 或 PyTorch 未安装")
        logger.info("请运行: pip install depth-anything-v2 torch torchvision")
        sys.exit(1)
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
