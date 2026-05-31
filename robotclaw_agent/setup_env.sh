#!/bin/bash
#
# 新 Agent 环境初始化脚本
#
# 用法：
#   chmod +x setup_env.sh
#   ./setup_env.sh
#
# 功能：
# - 创建独立的虚拟环境（推荐使用 uv，如果没有则使用 venv）
# - 安装新 Agent 必需的依赖（openai 等）
# - 不安装原项目中大量不相关的库
#

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
REQUIREMENTS_FILE="$PROJECT_ROOT/requirements.txt"

echo "========================================"
echo "新 Agent 环境初始化"
echo "========================================"

# 检查是否安装 uv（推荐）
if command -v uv &> /dev/null; then
    echo "[1/3] 检测到 uv，使用 uv 创建虚拟环境..."
    uv venv "$VENV_DIR" --python 3.12
    source "$VENV_DIR/bin/activate"
    echo "[2/3] 安装核心依赖..."
    uv pip install openai python-dotenv
else
    echo "[1/3] 未检测到 uv，使用标准 venv..."
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    echo "[2/3] 升级 pip 并安装核心依赖..."
    pip install --upgrade pip
    pip install openai python-dotenv
fi

echo "[3/3] 环境初始化完成！"

echo ""
echo "========================================"
echo "使用方法："
echo "========================================"
echo ""
echo "1. 激活虚拟环境："
echo "   source $VENV_DIR/bin/activate"
echo ""
echo "2. 设置 OpenAI API Key（二选一）："
echo "   export OPENAI_API_KEY=\"sk-your-key\""
echo "   # 或创建 .env 文件"
echo ""
echo "3. 运行真实 LLM 测试："
echo "   PYTHONPATH=src python debug/test_real_llm.py"
echo ""
echo "4. 运行 ReAct 主循环演示："
echo "   PYTHONPATH=src python debug/run_react_demo.py"
echo ""
echo "========================================"