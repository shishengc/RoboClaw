"""
快速开始脚本 - 深度估计和三维坐标计算

这个脚本提供了最简单的方式来使用深度估计功能
"""

import asyncio
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.generate_depth_and_3d_coords import (
    get_robot_image,
    DepthMapProcessor,
    PointTo3D,
    CameraIntrinsics,
    InteractiveDepthViewer,
)
import cv2
import numpy as np
from loguru import logger


async def quick_start_with_robot():
    """快速开始：从机器人获取图像"""
    print("\n" + "="*60)
    print("快速开始 - 从机器人获取图像")
    print("="*60)
    
    try:
        # 获取图像
        logger.info("从机器人获取头部相机图像...")
        image = await get_robot_image(
            base_url="http://localhost:8765",
            max_attempts=20,
            interval=0.25
        )
        
        if image is None:
            print("\n" + "="*60)
            print("❌ 无法从机器人获取图像")
            print("="*60)
            print("\n🔧 尝试以下解决方案:")
            print("\n1️⃣  使用本地图像文件：")
            print("   返回菜单选择选项 2")
            print("\n2️⃣  使用网络摄像头：")
            print("   返回菜单选择选项 2（不输入文件路径）")
            print("\n3️⃣  检查机器人服务：")
            print("   curl http://localhost:8765/")
            print("\n4️⃣  运行完整脚本（带自定义参数）：")
            print("   python scripts/generate_depth_and_3d_coords.py --no-robot --image-path image.jpg")
            print("="*60)
            return False
        
        return await process_image(image)
    
    except Exception as e:
        logger.error(f"错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def quick_start_with_file(image_path: str):
    """快速开始：从本地文件获取图像"""
    print("\n" + "="*60)
    print(f"快速开始 - 从文件读取: {image_path}")
    print("="*60)
    
    try:
        # 读取图像
        image = cv2.imread(image_path)
        if image is None:
            logger.error(f"无法读取图像: {image_path}")
            return False
        
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        
        return asyncio.run(process_image(image))
    
    except Exception as e:
        logger.error(f"错误: {e}")
        return False


async def process_image(rgb_image: np.ndarray) -> bool:
    """处理图像：生成深度图并启动交互查看器"""
    
    try:
        # 1. 初始化深度处理器
        logger.info("初始化 Depth Anything 模型...")
        processor = DepthMapProcessor(model_type="small", device="cuda")
        
        # 2. 生成深度图
        logger.info("生成深度图...")
        depth_normalized, depth_m = processor.predict_depth(rgb_image)
        logger.info(f"深度范围: {depth_m.min():.3f} - {depth_m.max():.3f} 米")
        
        # 3. 保存结果
        output_dir = Path("artifacts/depth_estimation")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        depth_colored = processor.depth_to_colormap(depth_normalized)
        cv2.imwrite(str(output_dir / "depth_colored.png"), depth_colored)
        np.save(str(output_dir / "depth_map.npy"), depth_m)
        logger.info(f"深度图已保存到 {output_dir}")
        
        # 4. 创建相机内参
        intrinsics = CameraIntrinsics.from_fov(
            image_width=rgb_image.shape[1],
            image_height=rgb_image.shape[0],
            fov_degree=90
        )
        logger.info(f"相机内参: {intrinsics}")
        
        # 5. 启动交互查看器
        converter = PointTo3D(intrinsics)
        viewer = InteractiveDepthViewer(rgb_image, depth_m, converter)
        selected_points = viewer.run()
        
        logger.info(f"共选中 {len(selected_points)} 个点")
        return True
    
    except ImportError as e:
        logger.error(f"依赖项缺失: {e}")
        logger.info("请运行: pip install depth-anything-v2")
        return False
    
    except Exception as e:
        logger.error(f"处理图像出错: {e}")
        return False


def print_menu():
    """打印菜单"""
    print("\n" + "="*60)
    print("深度估计和三维坐标计算 - 快速开始")
    print("="*60)
    print("请选择一个选项:")
    print("  1. 从机器人头部相机获取图像")
    print("  2. 从本地文件读取图像")
    print("  3. 查看示例代码")
    print("  4. 运行诊断工具")
    print("  5. 查看故障排查指南")
    print("  0. 退出")
    print("-"*60)


async def main():
    """主程序"""
    
    while True:
        print_menu()
        choice = input("请输入选择 (0-5): ").strip()
        
        if choice == "1":
            success = await quick_start_with_robot()
            if success:
                print("\n✓ 完成！")
            else:
                print("\n✗ 失败")
                print("\n💡 提示: 尝试选项 4 运行诊断工具，或选项 5 查看故障排查指南")
        
        elif choice == "2":
            image_path = input("请输入图像路径（或按 Enter 使用网络摄像头）: ").strip()
            if image_path == "":
                # 使用网络摄像头
                print("使用网络摄像头...")
                import cv2
                cap = cv2.VideoCapture(0)
                if cap.isOpened():
                    ret, frame = cap.read()
                    cap.release()
                    if ret:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        success = await process_image(frame)
                        if success:
                            print("\n✓ 完成！")
                        else:
                            print("\n✗ 失败")
                    else:
                        print("无法读取摄像头帧")
                else:
                    print("无法打开网络摄像头")
            elif image_path:
                success = quick_start_with_file(image_path)
                if success:
                    print("\n✓ 完成！")
                else:
                    print("\n✗ 失败")
            else:
                print("请输入有效的路径")
        
        elif choice == "3":
            print_example_code()
        
        elif choice == "4":
            run_diagnostic_tool()
        
        elif choice == "5":
            show_troubleshooting_guide()
        
        elif choice == "0":
            print("退出")
            break
        
        else:
            print("无效选择，请重试")


def run_diagnostic_tool():
    """运行诊断工具"""
    print("\n" + "="*60)
    print("运行诊断工具...")
    print("="*60)
    
    try:
        import subprocess
        subprocess.run([sys.executable, "scripts/diagnose_depth_setup.py"])
    except Exception as e:
        print(f"无法运行诊断工具: {e}")
        print("\n手动运行:")
        print("  python scripts/diagnose_depth_setup.py")


def show_troubleshooting_guide():
    """显示故障排查指南"""
    print("\n" + "="*60)
    print("故障排查指南")
    print("="*60)
    
    try:
        troubleshooting_file = Path(__file__).parent / "TROUBLESHOOTING.md"
        if troubleshooting_file.exists():
            with open(troubleshooting_file, 'r', encoding='utf-8') as f:
                content = f.read()
                print(content)
        else:
            print("故障排查文件未找到")
    except Exception as e:
        print(f"无法读取故障排查指南: {e}")
    
    input("\n按 Enter 返回菜单...")


def print_example_code():
    """打印示例代码"""
    examples = {
        "1": ("简单示例 - 查询单个点的3D坐标", """
import cv2
import numpy as np
from scripts.generate_depth_and_3d_coords import PointTo3D, CameraIntrinsics

# 加载深度图
depth_m = np.load("artifacts/depth_estimation/depth_map.npy")

# 创建转换器
intrinsics = CameraIntrinsics.from_fov(
    depth_m.shape[1], 
    depth_m.shape[0], 
    fov_degree=90
)
converter = PointTo3D(intrinsics)

# 查询像素 (320, 240) 的 3D 坐标
u, v = 320, 240
depth = depth_m[v, u]
x, y, z = converter.pixel_to_3d(u, v, depth)
print(f"3D坐标: ({x:.3f}, {y:.3f}, {z:.3f}) 米")
        """),
        
        "2": ("生成点云", """
import numpy as np
from scripts.generate_depth_and_3d_coords import PointTo3D, CameraIntrinsics

# 加载深度图
depth_m = np.load("artifacts/depth_estimation/depth_map.npy")

# 生成点云
intrinsics = CameraIntrinsics.from_fov(
    depth_m.shape[1], 
    depth_m.shape[0]
)
converter = PointTo3D(intrinsics)
point_cloud = converter.pixel_to_3d_array(depth_m)

# 筛选有效点
valid_mask = depth_m > 0.1
valid_points = point_cloud[valid_mask]
print(f"点云包含 {len(valid_points)} 个有效点")
        """),
        
        "3": ("批量处理图像", """
import cv2
from pathlib import Path
from scripts.generate_depth_and_3d_coords import DepthMapProcessor

processor = DepthMapProcessor(model_type="small", device="cuda")

image_dir = Path("artifacts/test_camera")
for image_file in image_dir.glob("*.jpg"):
    image = cv2.imread(str(image_file))
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    
    depth_normalized, depth_m = processor.predict_depth(image)
    print(f"{image_file.name}: 深度范围 {depth_m.min():.3f}-{depth_m.max():.3f}m")
        """),
    }
    
    print("\n" + "="*60)
    print("示例代码")
    print("="*60)
    
    for key, (title, code) in examples.items():
        print(f"\n{key}. {title}")
        print("-" * 40)
        print(code)
    
    print("\n按 Enter 返回菜单...")
    input()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n程序已中断")
        sys.exit(0)
    except Exception as e:
        logger.error(f"发生错误: {e}")
        sys.exit(1)
