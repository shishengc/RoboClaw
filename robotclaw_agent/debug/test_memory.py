"""
MemoryManager 专项测试

直接测试 MemoryManager 是否正常工作。
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# 直接导入具体模块，避免包级 __init__.py 的相对导入问题
from new_agent.memory.memory_manager import MemoryManager


def test_memory_manager():
    print("=" * 50)
    print("MemoryManager 专项测试")
    print("=" * 50)

    mm = MemoryManager()

    # 创建任务
    task_id = mm.create_task(
        task_brief="把方块放到旁边的盒子里",
        action_guidance="行动后必须感知"
    )
    print(f"创建任务: {task_id}")

    # 添加消息
    mm.add_user_message("把方块放到旁边的盒子里")
    mm.add_tool_result("LocateObject", {"found": True, "position": {"x": 0.3}})
    mm.add_perception_result({"task_progress": {"overall_completion": 0.3}})

    print(f"当前任务进度: {mm.get_current_task_progress()}%")

    # 获取上下文
    contexts = mm.get_current_contexts()
    print(f"上下文消息数量: {len(contexts)}")
    for i, ctx in enumerate(contexts):
        print(f"  [{i}] {ctx['role']}: {str(ctx['content'])[:60]}...")

    # 完成任务
    mm.mark_task_completed("测试完成")

    print(f"最终进度: {mm.get_current_task_progress()}%")
    print("\nMemoryManager 测试通过！")


if __name__ == "__main__":
    test_memory_manager()
