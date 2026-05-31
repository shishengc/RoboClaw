#!/usr/bin/env python3
"""
新 Agent 快速演示入口

用法：
    python run_demo.py

会自动设置正确的 PYTHONPATH 并运行 ReAct 主循环演示。
"""

import os
import sys
import subprocess

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_PATH = os.path.join(PROJECT_ROOT, "src")

# 设置 PYTHONPATH
env = os.environ.copy()
env["PYTHONPATH"] = SRC_PATH

# 要运行的演示脚本
demo_script = os.path.join(PROJECT_ROOT, "debug", "run_react_demo.py")

print("正在启动新 Agent ReAct 主循环演示...\n")
subprocess.run([sys.executable, demo_script], env=env)
