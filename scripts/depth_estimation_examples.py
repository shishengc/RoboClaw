"""
使用示例和测试脚本 - 深度估计和三维坐标转换

这个脚本展示如何使用 generate_depth_and_3d_coords.py 中的各个组件
"""

import numpy as np
import cv2
from pathlib import Path
from scripts.generate_depth_and_3d_coords import (
    CameraIntrinsics,
    DepthMapProcessor,
    PointTo3D,
    InteractiveDepthViewer,
)


def example_1_camera_intrinsics():
    """示例1: 相机内参计算"""
    print("\n=== 示例 1: 相机内参计算 ===")
    
    # 方法1: 直接指定内参
    intrinsics_direct = CameraIntrinsics(fx=920.0, fy=920.0, cx=640.0, cy=480.0)
    print(f"直接指定内参: {intrinsics_direct}")
    
    # 方法2: 从视场角估计
    intrinsics_from_fov = CameraIntrinsics.from_fov(
        image_width=1280,
        image_height=960,
        fov_degree=90
    )
    print(f"从视场角估计: {intrinsics_from_fov}")


def example_2_point_to_3d():
    """示例2: 像素坐标转3D坐标"""
    print("\n=== 示例 2: 像素坐标转3D坐标 ===")
    
    # 创建相机内参
    intrinsics = CameraIntrinsics(fx=920.0, fy=920.0, cx=640.0, cy=480.0)
    converter = PointTo3D(intrinsics)
    
    # 示例点
    u, v = 640, 480  # 图像中心
    depth = 0.5  # 0.5 米
    
    x, y, z = converter.pixel_to_3d(u, v, depth)
    print(f"像素 ({u}, {v}), 深度 {depth} m -> 3D坐标: ({x:.3f}, {y:.3f}, {z:.3f})")
    
    # 另一个点
    u, v = 800, 300  # 右上角附近
    x, y, z = converter.pixel_to_3d(u, v, depth)
    print(f"像素 ({u}, {v}), 深度 {depth} m -> 3D坐标: ({x:.3f}, {y:.3f}, {z:.3f})")


def example_3_depth_map_generation():
    """示例3: 从图像生成深度图"""
    print("\n=== 示例 3: 深度图生成 ===")
    
    # 创建一个测试图像
    test_image = cv2.imread("artifacts/test_camera/concatenated_image.jpg")
    if test_image is None:
        print("测试图像不存在，跳过此示例")
        print("运行: python scripts/test_camera.py 来获取图像")
        return
    
    # 转换为 RGB
    test_image = cv2.cvtColor(test_image, cv2.COLOR_BGR2RGB)
    print(f"测试图像大小: {test_image.shape}")
    
    try:
        # 初始化深度处理器
        processor = DepthMapProcessor(model_type="small", device="cuda")
        
        # 生成深度图
        depth_normalized, depth_m = processor.predict_depth(test_image)
        
        print(f"深度范围: {depth_m.min():.3f} - {depth_m.max():.3f} 米")
        print(f"平均深度: {depth_m.mean():.3f} 米")
        
        # 保存结果
        depth_colored = processor.depth_to_colormap(depth_normalized)
        output_dir = Path("artifacts/depth_estimation_example")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        cv2.imwrite(str(output_dir / "depth_colored.png"), depth_colored)
        np.save(str(output_dir / "depth_map.npy"), depth_m)
        
        print(f"结果已保存到 {output_dir}")
        
    except ImportError as e:
        print(f"Depth Anything 未安装: {e}")
        print("请运行: pip install depth-anything-v2")


def example_4_point_cloud_generation():
    """示例4: 生成点云"""
    print("\n=== 示例 4: 点云生成 ===")
    
    # 创建模拟深度图
    h, w = 480, 640
    depth_map = np.ones((h, w)) * 0.5  # 全为 0.5 米
    
    # 添加一些变化
    depth_map[100:200, 100:200] = 0.3  # 近处
    depth_map[300:400, 300:400] = 1.0  # 远处
    
    # 创建转换器
    intrinsics = CameraIntrinsics.from_fov(w, h, fov_degree=90)
    converter = PointTo3D(intrinsics)
    
    # 生成点云
    point_cloud = converter.pixel_to_3d_array(depth_map)
    print(f"点云形状: {point_cloud.shape}")
    print(f"点云统计:")
    print(f"  X 范围: [{point_cloud[..., 0].min():.3f}, {point_cloud[..., 0].max():.3f}]")
    print(f"  Y 范围: [{point_cloud[..., 1].min():.3f}, {point_cloud[..., 1].max():.3f}]")
    print(f"  Z 范围: [{point_cloud[..., 2].min():.3f}, {point_cloud[..., 2].max():.3f}]")
    
    # 保存点云
    output_dir = Path("artifacts/point_cloud_example")
    output_dir.mkdir(parents=True, exist_ok=True)
    np.save(str(output_dir / "point_cloud.npy"), point_cloud)
    
    # 保存为 PLY 格式（可选）
    try:
        save_point_cloud_ply(
            point_cloud.reshape(-1, 3),
            str(output_dir / "point_cloud.ply")
        )
        print(f"点云已保存到 {output_dir}")
    except Exception as e:
        print(f"保存 PLY 失败: {e}")


def save_point_cloud_ply(points: np.ndarray, filename: str):
    """保存点云为 PLY 格式"""
    with open(filename, 'w') as f:
        f.write("ply\n")
        f.write("format ascii 1.0\n")
        f.write(f"element vertex {len(points)}\n")
        f.write("property float x\n")
        f.write("property float y\n")
        f.write("property float z\n")
        f.write("end_header\n")
        
        for point in points:
            f.write(f"{point[0]:.6f} {point[1]:.6f} {point[2]:.6f}\n")


def example_5_batch_processing():
    """示例5: 批量处理多个图像"""
    print("\n=== 示例 5: 批量处理 ===")
    
    image_dir = Path("artifacts/test_camera")
    if not image_dir.exists():
        print(f"图像目录不存在: {image_dir}")
        print("运行: python scripts/test_camera.py 来获取图像")
        return
    
    image_files = list(image_dir.glob("*.jpg"))[:3]  # 只处理前3张
    print(f"找到 {len(image_files)} 张图像")
    
    try:
        processor = DepthMapProcessor(model_type="small", device="cuda")
        intrinsics = CameraIntrinsics.from_fov(1280, 960, fov_degree=90)
        converter = PointTo3D(intrinsics)
        
        output_dir = Path("artifacts/batch_depth_estimation")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        for i, image_file in enumerate(image_files):
            print(f"处理图像 {i+1}/{len(image_files)}: {image_file.name}")
            
            # 读取图像
            image = cv2.imread(str(image_file))
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            
            # 生成深度图
            depth_normalized, depth_m = processor.predict_depth(image)
            
            # 保存结果
            np.save(str(output_dir / f"depth_{i:03d}.npy"), depth_m)
            
            # 计算中心点的3D坐标
            h, w = depth_m.shape
            center_depth = depth_m[h//2, w//2]
            x, y, z = converter.pixel_to_3d(w//2, h//2, center_depth)
            print(f"  深度范围: {depth_m.min():.3f} - {depth_m.max():.3f} 米")
            print(f"  中心点3D坐标: ({x:.3f}, {y:.3f}, {z:.3f})")
        
        print(f"结果已保存到 {output_dir}")
        
    except ImportError as e:
        print(f"Depth Anything 未安装: {e}")


if __name__ == "__main__":
    print("深度估计和三维坐标转换 - 使用示例")
    print("=" * 50)
    
    # 运行所有示例
    example_1_camera_intrinsics()
    example_2_point_to_3d()
    example_3_depth_map_generation()
    example_4_point_cloud_generation()
    example_5_batch_processing()
    
    print("\n" + "=" * 50)
    print("所有示例完成！")
