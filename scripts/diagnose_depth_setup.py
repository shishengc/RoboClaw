"""
诊断脚本 - 检查深度估计系统的依赖和配置

使用: python scripts/diagnose_depth_setup.py
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional


def print_header(text):
    """打印分隔符"""
    print("\n" + "="*60)
    print(f"  {text}")
    print("="*60)


def check_python_version():
    """检查 Python 版本"""
    print_header("1. Python 版本检查")
    py_version = sys.version_info
    print(f"当前 Python 版本: {py_version.major}.{py_version.minor}.{py_version.micro}")
    
    if py_version.major >= 3 and py_version.minor >= 10:
        print("✓ Python 版本符合要求 (>=3.10)")
        return True
    else:
        print("✗ Python 版本过低，需要升级到 3.10+")
        return False


def check_package(package_name, import_name=None):
    """检查单个包是否安装"""
    if import_name is None:
        import_name = package_name
    
    try:
        __import__(import_name)
        return True, None
    except ImportError as e:
        return False, str(e)


def check_dependencies():
    """检查所有依赖"""
    print_header("2. 依赖包检查")
    
    dependencies = [
        ("torch", "torch"),
        ("torchvision", "torchvision"),
        ("torchaudio", "torchaudio"),
        ("opencv-python", "cv2"),
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("loguru", "loguru"),
        ("pydantic", "pydantic"),
        ("depth-anything-v2", None),  # 需要特殊处理
    ]
    
    all_ok = True
    for package_name, import_name in dependencies:
        if import_name is None:
            # 特殊处理 depth-anything-v2
            try:
                __import__("depth_anything_v2")
                print(f"✓ {package_name:25} 已安装")
            except ImportError:
                print(f"✗ {package_name:25} 未安装")
                all_ok = False
        else:
            installed, error = check_package(package_name, import_name)
            if installed:
                print(f"✓ {package_name:25} 已安装")
            else:
                print(f"✗ {package_name:25} 未安装 ({error})")
                all_ok = False
    
    return all_ok


def check_gpu():
    """检查 GPU 支持"""
    print_header("3. GPU 支持检查")
    
    try:
        import torch
        
        cuda_available = torch.cuda.is_available()
        if cuda_available:
            device_count = torch.cuda.device_count()
            print(f"✓ CUDA 可用")
            print(f"✓ 检测到 {device_count} 个 GPU 设备")
            
            for i in range(device_count):
                gpu_name = torch.cuda.get_device_name(i)
                gpu_mem = torch.cuda.get_device_properties(i).total_memory / 1e9
                print(f"  GPU {i}: {gpu_name} ({gpu_mem:.2f} GB)")
            
            return True
        else:
            print("⚠ CUDA 不可用，将使用 CPU 处理（速度较慢）")
            return False
    
    except Exception as e:
        print(f"✗ GPU 检查失败: {e}")
        return False


def check_project_structure():
    """检查项目文件结构"""
    print_header("4. 项目结构检查")
    
    required_files = [
        "scripts/generate_depth_and_3d_coords.py",
        "scripts/quick_start_depth.py",
        "scripts/camera_config.py",
        "scripts/depth_estimation_examples.py",
        "scripts/advanced_grasping_examples.py",
        "src/agent_demo/machine_layer/dataloader_corobot.py",
    ]
    
    root_dir = Path(__file__).parent.parent
    all_ok = True
    
    for file_path in required_files:
        full_path = root_dir / file_path
        if full_path.exists():
            print(f"✓ {file_path}")
        else:
            print(f"✗ {file_path} 未找到")
            all_ok = False
    
    return all_ok


def check_robot_connection():
    """检查机器人连接"""
    print_header("5. 机器人连接检查")
    
    try:
        import requests
        
        robot_urls = [
            ("CoRobot API", "http://localhost:8765/"),
            ("VLA API", "http://localhost:8999/"),
        ]
        
        for name, url in robot_urls:
            try:
                response = requests.get(url, timeout=2)
                if response.status_code == 200:
                    print(f"✓ {name:15} 在线: {url}")
                else:
                    print(f"⚠ {name:15} 响应异常: HTTP {response.status_code}")
            except requests.exceptions.ConnectionError:
                print(f"✗ {name:15} 无法连接: {url}")
            except Exception as e:
                print(f"⚠ {name:15} 检查失败: {e}")
    
    except ImportError:
        print("⚠ 无法检查机器人连接 (requests 未安装)")


def check_dataloader():
    """检查 DataLoaderCoRobot 是否可用"""
    print_header("6. DataLoaderCoRobot 检查")
    
    try:
        from agent_demo.machine_layer.dataloader_corobot import DataLoaderCoRobot
        print("✓ DataLoaderCoRobot 可以导入")
        return True
    except ImportError as e:
        print(f"✗ 无法导入 DataLoaderCoRobot: {e}")
        print("\n解决方案:")
        print("  • 确保在 RoboClaw 项目根目录运行脚本")
        print("  • 检查 corobot 虚拟环境是否正确配置")
        print("  • 检查 a2d_sdk 是否已安装")
        return False


def check_depth_anything_model():
    """检查 Depth Anything 模型"""
    print_header("7. Depth Anything 模型检查")
    
    def try_import(root_path: Optional[str] = None) -> bool:
        if root_path:
            root = Path(root_path).expanduser().resolve()
            if root.exists() and str(root) not in sys.path:
                sys.path.insert(0, str(root))
        try:
            from depth_anything_v2.dpt import DepthAnythingV2
            import torch
            return True
        except Exception:
            return False

    env_root = os.environ.get("DEPTH_ANYTHING_ROOT")
    if try_import(env_root):
        print(f"✓ Depth Anything 模块可以导入 (来自 DEPTH_ANYTHING_ROOT={env_root})")
    elif try_import():
        print("✓ Depth Anything 模块可以导入")
    else:
        print("✗ 无法导入 Depth Anything")
        if env_root:
            print(f"  已尝试 DEPTH_ANYTHING_ROOT={env_root}")
        print("\n解决方案:")
        print("  pip install depth-anything-v2")
        print("  或设置 DEPTH_ANYTHING_ROOT 指向本地 Depth Anything 克隆目录")
        return False

    # 尝试测试模型加载
    try:
        from depth_anything_v2.dpt import DepthAnythingV2
        import torch
        print("\n尝试加载 small 模型...")
        model = DepthAnythingV2(
            encoder="vits",
            features=64,
            out_channels=[48, 96, 192, 384],
        )
        expected_checkpoint = None
        if env_root:
            expected_checkpoint = Path(env_root).expanduser().resolve() / "checkpoints" / "depth_anything_v2_vits.pth"
        if expected_checkpoint is not None and expected_checkpoint.exists():
            state_dict = torch.load(expected_checkpoint, map_location="cpu")
            model.load_state_dict(state_dict)
            print(f"✓ 模型加载成功: {expected_checkpoint}")
            return True

        print("⚠ 模型类可以实例化，但未找到 vits checkpoint")
        print("  需要下载 depth_anything_v2_vits.pth 并放到 checkpoints/ 目录")
        return False
    except Exception as e:
        print(f"⚠ 模型加载失败: {e}")
        print("  可能原因: 网络连接问题或磁盘空间不足")
        return False


def print_summary(results):
    """打印总结"""
    print_header("诊断结果总结")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed
    
    print(f"✓ 通过: {passed}/{total}")
    print(f"✗ 失败: {failed}/{total}")
    
    if failed == 0:
        print("\n🎉 所有检查通过！系统已准备就绪。")
        print("\n可以运行以下命令开始使用:")
        print("  python scripts/quick_start_depth.py")
    else:
        print("\n⚠ 发现问题，请按上述建议修复。")


def main():
    """主诊断程序"""
    print("="*60)
    print("深度估计系统诊断工具")
    print("="*60)
    
    results = {
        "Python 版本": check_python_version(),
        "依赖包": check_dependencies(),
        "项目结构": check_project_structure(),
        "DataLoaderCoRobot": check_dataloader(),
        "Depth Anything": check_depth_anything_model(),
    }
    
    check_gpu()
    check_robot_connection()
    
    print_summary(results)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n诊断中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n诊断过程中出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
