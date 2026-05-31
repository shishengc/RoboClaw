"""
轻量级状态机（mini Agent 版本）

为 NewAgent 的 ReAct 主循环提供简单状态跟踪。
参考 Claude Code / mini-SWE 的做法，状态主要用于日志和流程可视化。
"""

from enum import Enum, auto
import logging

logger = logging.getLogger(__name__)


class AgentState(Enum):
    """Agent 运行状态"""
    INIT = auto()       # 初始化
    READY = auto()      # 就绪，等待任务
    CHAT = auto()       # 正在与 LLM 对话/决策
    ACT = auto()        # 执行工具
    PERCEIVE = auto()   # 行动后感知环境
    FINALIZE = auto()   # 任务结束


class StateMachine:
    """
    简易状态机

    支持状态转换和当前状态查询，足够支撑 mini ReAct 循环。
    """

    def __init__(self, initial_state: AgentState = AgentState.INIT):
        self.current_state = initial_state
        logger.info(f"[StateMachine] 初始化完成，当前状态: {self.current_state.name}")

    def transition(self, new_state: AgentState) -> None:
        """转换到新状态（记录日志）"""
        old_state = self.current_state.name
        self.current_state = new_state
        logger.info(f"[StateMachine] 状态转换: {old_state} → {new_state.name}")
