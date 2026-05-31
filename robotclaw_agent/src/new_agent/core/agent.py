"""
新 Agent 主类（增强版）

已集成：
- Prompt 系统
- Tool 调用框架
- 更接近真实流程的 run_once 实现
"""

import logging
import json
import asyncio
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from .config import AgentConfig, load_agent_config
from .state_machine import StateMachine, AgentState
from ..components.tool_registry import ToolRegistry
from ..prompt.prompt_builder import build_system_prompt
from ..memory.memory_manager import MemoryManager
from ..tools.tool_types import ToolResult, ToolStatus

logger = logging.getLogger(__name__)


class NewAgent:
    """
    新一代 Agent（完整骨架）

    支持：
    - 原子化工具调用
    - 行动后感知
    - 显式任务完成确认
    """

    def __init__(self, agent_name: str = "NewAgent", config_path: str | None = None):
        self.agent_name = agent_name
        self.config: AgentConfig = load_agent_config(config_path)
        self.state_machine = StateMachine(initial_state=AgentState.INIT)
        self.tool_registry = ToolRegistry()

        self.llm_client = None
        self.memory_manager = MemoryManager()

        # 当前任务信息
        self.current_task_brief: str = ""
        self.current_action_guidance: str = ""

        # Agent 轨迹管理：trajectory 按 LLM round 组织，event_trace 保留细粒度事件流水。
        self.trajectory: list[dict] = []
        self.event_trace: list[dict] = []
        self._active_llm_round: dict[str, Any] | None = None
        self.last_trajectory_path: str | None = None
        self._observation_targets: set[str] = set()

        logger.info(f"[{self.agent_name}] Agent 初始化完成")

    # ========== 初始化 ==========

    async def init_agent(
        self,
        task_brief: str = "",
        action_guidance: str = "",
        use_real_llm: bool = False,
        openai_api_key: str | None = None,
        config_path: str | None = None,
        use_shisheng_tools: bool = False,
        shisheng_repo_root: str | None = None,
        shisheng_auto_setup: str = "0",
        use_mcp_control_tools: bool = False,
        corobot_base_url: str | None = None,
    ) -> None:
        """
        初始化 Agent

        Args:
            use_real_llm: 是否使用真实 OpenAI GPT-4o 调用
            openai_api_key: 传入 API Key（优先级高于环境变量）
        """
        if config_path:
            self.config = load_agent_config(config_path)

        self.state_machine.transition(AgentState.READY)

        if use_shisheng_tools:
            self.register_shisheng_tools(
                repo_root=shisheng_repo_root,
                auto_setup=shisheng_auto_setup,
            )
        if use_mcp_control_tools:
            self.register_mcp_control_tools(base_url=corobot_base_url)

        if use_shisheng_tools or use_mcp_control_tools:
            self.tool_registry.register_mock("FinalizeTask")
        else:
            # 注册 Mock 工具（开发阶段）
            self._register_mock_tools()

        self.current_task_brief = task_brief or "完成用户指定的机器人操作任务"
        self.current_action_guidance = action_guidance or "使用原子工具逐步完成任务，每次行动后建议感知环境。"

        # 创建任务节点
        self.memory_manager.create_task(
            task_brief=self.current_task_brief,
            action_guidance=self.current_action_guidance
        )
        self.memory_manager.set_task_plan(self._build_default_task_plan())

        # 初始化 LLM Client
        from ..llm.llm_client import LLMClient
        self.llm_client = LLMClient(
            model=self.config.model,
            mock_mode=not use_real_llm,
            api_key=openai_api_key,
            timeout_seconds=self.config.llm_timeout_seconds,
        )

        mode_str = "真实 GPT-4o" if use_real_llm else "Mock"
        logger.info(f"[{self.agent_name}] 初始化完成（{mode_str} 模式），已创建任务节点")

    def _register_mock_tools(self) -> None:
        mock_names = ["LocateObject", "GraspObject", "PlaceObject", "SenseEnvironment", "FinalizeTask"]
        for name in mock_names:
            self.tool_registry.register_mock(name)

    def start_task(
        self,
        task_brief: str,
        action_guidance: str | None = None,
        task_plan: list[str] | None = None,
    ) -> None:
        """Start a fresh task node while keeping the same tools and LLM client."""
        self.current_task_brief = task_brief or "完成用户指定的机器人操作任务"
        if action_guidance is not None:
            self.current_action_guidance = action_guidance
        self._observation_targets.clear()
        self.memory_manager.create_task(
            task_brief=self.current_task_brief,
            action_guidance=self.current_action_guidance,
        )
        self.memory_manager.set_task_plan(task_plan or self._build_default_task_plan())

    def register_shisheng_tools(
        self,
        repo_root: str | None = None,
        auto_setup: str = "0",
    ) -> None:
        """Register the real shisheng robot tools on this agent."""
        from ..tools.shisheng_tools import DEFAULT_SHISHENG_ROOT, register_shisheng_tools

        register_shisheng_tools(
            self.tool_registry,
            repo_root=repo_root or DEFAULT_SHISHENG_ROOT,
            auto_setup=auto_setup,
            include_backend=True,
            timeout_seconds=max(180.0, float(self.config.tool_timeout_seconds)),
        )

    def register_mcp_control_tools(
        self,
        base_url: str | None = None,
    ) -> None:
        """Register real CoRobot mcp_control_demo skill tools on this agent."""
        from ..tools.mcp_control_tools import DEFAULT_COROBOT_BASE_URL, register_mcp_control_agent_tools

        register_mcp_control_agent_tools(
            self.tool_registry,
            base_url=base_url or DEFAULT_COROBOT_BASE_URL,
            timeout_seconds=max(180.0, float(self.config.tool_timeout_seconds)),
        )

    # ========== Prompt 相关 ==========

    def get_current_system_prompt(self) -> str:
        """生成当前上下文的系统提示词（不包含工具列表）"""
        return build_system_prompt(
            task_brief=self.current_task_brief,
            action_guidance=self.current_action_guidance,
        )

    def _get_tools_schema(self) -> list[dict[str, Any]]:
        """
        获取当前可用工具的 Schema（供 LLM tools 参数使用）
        """
        return self.tool_registry.get_openai_tools()

    def _format_openai_tool_calls(self, tool_calls: list[Any]) -> list[dict[str, Any]]:
        """把内部 ToolCall 转换成 OpenAI assistant.tool_calls 格式。"""
        return [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": json.dumps(tool_call.arguments or {}, ensure_ascii=False),
                },
            }
            for tool_call in tool_calls
        ]

    def _build_default_task_plan(self) -> list[str]:
        return [
            "locate relevant objects and confirm the scene state",
            "grasp or manipulate the target object with an atomic action",
            "place or move the object to the requested target",
            "verify the environment state with perception",
            "finalize only after the result is verified",
        ]

    async def _chat_with_timeout(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        return await asyncio.wait_for(
            self.llm_client.chat(messages=messages, tools=tools),
            timeout=self.config.llm_timeout_seconds,
        )

    async def run_full_task_1(
        self,
        source_color: str = "red",
        target_color: str | None = "yellow",
        *,
        scene_instance_id: int = 0,
        target_x_offset: float = 0.015,
        target_y_offset: float = 0.015,
        target_z_offset: float = 0.02,
        grasp_z_offset: float = -0.02,
        settle_timeout: float = 20.0,
        preclose_distance: float = 0.050,
        preclose_xy_distance: float = 0.035,
        preclose_z_distance: float = 0.040,
        align_retries: int = 3,
        force_grasp_after_align_retries: bool = True,
        close_settle_timeout: float = 6.0,
        closed_gripper_raw_threshold: float = 0.12,
        lift_height: float = 0.17,
        place_hover_height: float = 0.08,
        place_target_z_offset: float = 0.005,
        place_y_offset: float = 0.217,
        place_z_drop: float = 0.1,
        place_descend_timeout: float = 1.0,
        release_pause: float = 2.0,
        return_after_place: bool = True,
        default_return_tolerance: float = 0.08,
        observe_after_each_action: bool = True,
        verify_placement: bool = True,
    ) -> str:
        """
        Execute the full_task_1.md tool chain deterministically through ToolRegistry.

        This path is useful before trusting the LLM to choose the whole tool chain:
        every simulator action still goes through the same registered agent tools.
        """
        valid_colors = {"red", "yellow", "purple", "green", "blue"}
        source_color = source_color.strip().lower()
        target_color = target_color.strip().lower() if target_color else ""
        if source_color not in valid_colors:
            raise ValueError(f"unsupported source_color: {source_color}")
        if target_color and target_color not in valid_colors:
            raise ValueError(f"unsupported target_color: {target_color}")
        if target_color == source_color:
            raise ValueError("source_color and target_color must be different")

        required_tools = {
            "EnsureAbsPoseRunning",
            "LocalizeTarget",
            "ApproachTarget",
            "AlignTarget",
            "CheckGraspReady",
            "GraspAtCurrent",
            "MoveEndEffectorToWorld",
            "PlaceHeldObject",
            "ReturnToDefaultAbsPose",
            "SenseEnvironment",
            "VerifyTaskState",
        }
        missing = sorted(required_tools.difference(self.tool_registry.list_tools()))
        if missing:
            raise RuntimeError(f"missing shisheng tools: {missing}. Call register_shisheng_tools() first.")

        if not self.memory_manager.current_task:
            self.memory_manager.create_task(
                task_brief=f"把 {source_color} block 放到 {target_color or '相对偏移目标'}",
                action_guidance="按照 full_task_1.md 的工具链执行真实 shisheng agent tools。",
            )
        self.memory_manager.set_task_plan(
            [
                "ensure abs_pose bridge is running",
                "localize source block",
                "approach source block",
                "align and check grasp readiness",
                "grasp at current pose",
                "lift held object",
                "place held object",
                "return to default abs pose",
            ]
        )

        source_target = f"{source_color}_block"
        grasp_offset = [target_x_offset, target_y_offset, grasp_z_offset]
        approach_offset = [target_x_offset, target_y_offset, target_z_offset]
        force_grasp = False

        async def run_tool(tool_name: str, payload: dict[str, Any] | None = None) -> ToolResult:
            payload = payload or {}
            self.state_machine.transition(AgentState.ACT)
            result = await self.tool_registry.execute(tool_name, **payload)
            self.memory_manager.add_tool_result(tool_name, result.to_dict())
            self.memory_manager.update_plan_for_tool(tool_name, result.status != ToolStatus.FAILED)
            self.record_trajectory(
                "full_task_tool",
                {
                    "tool": tool_name,
                    "args": payload,
                    "status": result.status.value,
                    "message": result.message,
                    "result": result.data,
                },
            )
            if result.status == ToolStatus.FAILED:
                self.memory_manager.add_failure(
                    {
                        "tool": tool_name,
                        "args": payload,
                        "status": result.status.value,
                        "message": result.message,
                        "data": result.data,
                    }
                )
                raise RuntimeError(f"{tool_name} failed: {result.message}")
            return result

        async def observe_after_step(reason: str) -> ToolResult | None:
            if not observe_after_each_action:
                return None
            self.state_machine.transition(AgentState.PERCEIVE)
            observation = await run_tool(
                "SenseEnvironment",
                {
                    "target": source_target,
                    "include_grasp_check": False,
                    "scene_instance_id": scene_instance_id,
                },
            )
            self.memory_manager.add_perception_result(observation.data)
            self.record_trajectory(
                "full_task_post_tool_observation",
                {
                    "reason": reason,
                    "status": observation.status.value,
                    "summary": {
                        "objects": observation.data.get("objects"),
                        "robot_state": observation.data.get("robot_state"),
                        "gripper_state": observation.data.get("gripper_state"),
                        "safety_status": observation.data.get("safety_status"),
                    },
                },
            )
            return observation

        def require_step(condition: bool, step: str, data: dict[str, Any]) -> None:
            if condition:
                return
            failure = {
                "step": step,
                "message": "post-tool verification failed",
                "data": data,
            }
            self.memory_manager.add_failure(failure)
            self.record_trajectory("full_task_step_verification_failed", failure)
            raise RuntimeError(f"{step} verification failed")

        async def verify_block_on_target() -> dict[str, Any]:
            if not target_color:
                return {
                    "verified": True,
                    "mode": "relative_offset",
                    "message": "no block target was requested; relative placement verification is not implemented",
                }

            verification = await run_tool(
                "VerifyTaskState",
                {
                    "goal": {
                        "type": "block_on_block",
                        "source": source_target,
                        "target": f"{target_color}_block",
                        "xy_tolerance_m": 0.09,
                        "z_error_tolerance_m": 0.08,
                        "min_z_delta_m": 0.015,
                        "place_target_z_offset_m": place_target_z_offset,
                    },
                    "scene_instance_id": scene_instance_id,
                },
            )
            return verification.data

        self.record_trajectory(
            "full_task_start",
            {
                "source_color": source_color,
                "target_color": target_color or None,
                "scene_instance_id": scene_instance_id,
            },
        )

        await run_tool("EnsureAbsPoseRunning")
        initial_localization = await run_tool(
            "LocalizeTarget",
            {
                "target": source_target,
                "camera": "geometry",
                "scene_instance_id": scene_instance_id,
            },
        )
        initial_right_ee_world = initial_localization.data.get("right_end_effector_world_m")
        self.record_trajectory("full_task_initial_right_ee", {"world_m": initial_right_ee_world})

        approach_result = await run_tool(
            "ApproachTarget",
            {
                "target": source_target,
                "approach_offset_m": approach_offset,
                "gripper": "open",
                "timeout": settle_timeout,
                "tolerance_m": preclose_distance,
                "scene_instance_id": scene_instance_id,
            },
        )
        await observe_after_step("after_ApproachTarget")
        require_step(
            bool(approach_result.data.get("reached")),
            "ApproachTarget",
            approach_result.data,
        )

        ready_to_close = False
        for attempt in range(1, int(align_retries) + 1):
            await run_tool(
                "AlignTarget",
                {
                    "target": source_target,
                    "method": "geometry_direct",
                    "grasp_offset_m": grasp_offset,
                    "gripper": "open",
                    "timeout": settle_timeout,
                    "tolerance_m": preclose_distance,
                    "scene_instance_id": scene_instance_id,
                },
            )
            grasp_check = await run_tool(
                "CheckGraspReady",
                {
                    "target": source_target,
                    "grasp_offset_m": grasp_offset,
                    "distance_tol_m": preclose_distance,
                    "xy_tol_m": preclose_xy_distance,
                    "z_tol_m": preclose_z_distance,
                    "scene_instance_id": scene_instance_id,
                },
            )
            ready_to_close = bool(grasp_check.data.get("ready_to_close"))
            self.record_trajectory(
                "full_task_grasp_ready_check",
                {"attempt": attempt, "ready_to_close": ready_to_close, "data": grasp_check.data},
            )
            if ready_to_close:
                break

        if not ready_to_close:
            if force_grasp_after_align_retries:
                force_grasp = True
                self.record_trajectory(
                    "full_task_force_grasp",
                    {"align_retries": align_retries, "reason": "grasp not ready after retries"},
                )
            else:
                raise RuntimeError(f"grasp is still not ready after {align_retries} alignment attempt(s)")

        await run_tool(
            "GraspAtCurrent",
            {
                "target": source_target,
                "require_grasp_ready": not force_grasp,
                "lift_after_grasp": False,
                "grasp_offset_m": grasp_offset,
                "distance_tol_m": preclose_distance,
                "xy_tol_m": preclose_xy_distance,
                "z_tol_m": preclose_z_distance,
                "timeout": close_settle_timeout,
                "closed_gripper_raw_threshold": closed_gripper_raw_threshold,
                "scene_instance_id": scene_instance_id,
            },
        )
        await observe_after_step("after_GraspAtCurrent")

        lift_result = await run_tool(
            "MoveEndEffectorToWorld",
            {
                "relative_delta_m": [0.0, 0.0, lift_height],
                "gripper": "closed",
                "timeout": settle_timeout,
                "tolerance_m": preclose_distance,
            },
        )
        await observe_after_step("after_MoveEndEffectorToWorld_lift")
        require_step(
            bool(lift_result.data.get("reached")),
            "MoveEndEffectorToWorld",
            lift_result.data,
        )

        if target_color:
            place_target = {"type": "block", "color": target_color}
            hover_height = place_hover_height
            place_timeout = settle_timeout
        else:
            place_target = {
                "type": "relative_offset",
                "offset_m": [0.0, place_y_offset, -place_z_drop],
            }
            hover_height = place_z_drop
            place_timeout = place_descend_timeout

        place_result = await run_tool(
            "PlaceHeldObject",
            {
                "held_object": source_target,
                "target": place_target,
                "held_object_grasp_offset_m": grasp_offset,
                "hover_height_m": hover_height,
                "release": True,
                "release_pause": release_pause,
                "return_after_place": False,
                "timeout": place_timeout,
                "tolerance_m": preclose_distance,
                "place_target_z_offset_m": place_target_z_offset,
                "scene_instance_id": scene_instance_id,
            },
        )
        await observe_after_step("after_PlaceHeldObject")
        require_step(
            bool(place_result.data.get("placed")) and bool(place_result.data.get("released")),
            "PlaceHeldObject",
            place_result.data,
        )

        placement_verification: dict[str, Any] | None = None
        placement_error: RuntimeError | None = None
        if verify_placement:
            placement_verification = await verify_block_on_target()
            self.record_trajectory("full_task_target_verification", placement_verification)
            if not placement_verification.get("verified"):
                self.memory_manager.add_failure(
                    {
                        "step": "target_placement_verification",
                        "message": "source block is not confirmed at the requested target",
                        "data": placement_verification,
                    }
                )
                placement_error = RuntimeError("target placement verification failed")

        if return_after_place:
            await run_tool("EnsureAbsPoseRunning")
            return_result = await run_tool(
                "ReturnToDefaultAbsPose",
                {
                    "gripper": "open",
                    "timeout": settle_timeout,
                    "tolerance_m": default_return_tolerance,
                },
            )
            await observe_after_step("after_ReturnToDefaultAbsPose")
            require_step(
                bool(return_result.data.get("reached")),
                "ReturnToDefaultAbsPose",
                return_result.data,
            )

        self.state_machine.transition(AgentState.PERCEIVE)
        final_sense = await run_tool(
            "SenseEnvironment",
            {
                "target": source_target,
                "include_grasp_check": False,
                "scene_instance_id": scene_instance_id,
            },
        )
        self.memory_manager.add_perception_result(final_sense.data)

        if placement_error is not None:
            summary = (
                f"full_task_1 未确认完成：{source_color} block 未被几何验证为已放置到 "
                f"{target_color} block 上。"
            )
            self.record_trajectory(
                "full_task_not_confirmed",
                {"summary": summary, "placement_verification": placement_verification},
            )
            self.state_machine.transition(AgentState.READY)
            self.save_trajectory(summary)
            raise placement_error

        summary = (
            f"已完成 full_task_1：{source_color} block "
            f"已放置到 {target_color} block 上。" if target_color
            else f"已完成 full_task_1：{source_color} block 已按相对偏移放置。"
        )
        self.memory_manager.mark_task_completed(summary)
        self.record_trajectory("full_task_complete", {"summary": summary})
        reset_result = await self._auto_reset_after_task_completion(summary)
        if reset_result is not None:
            summary += self._format_auto_reset_final_note(reset_result)
        self.state_machine.transition(AgentState.READY)
        self.save_trajectory(summary)
        return summary

    async def _execute_tool_with_retry(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> ToolResult:
        attempts = self.config.max_tool_retries + 1
        last_result: ToolResult | None = None

        for attempt in range(1, attempts + 1):
            try:
                result = await asyncio.wait_for(
                    self.tool_registry.execute(tool_name, **tool_args),
                    timeout=self.config.tool_timeout_seconds,
                )
            except asyncio.TimeoutError:
                result = ToolResult(
                    status=ToolStatus.FAILED,
                    message=f"工具执行超时: {tool_name}",
                    data={"error_type": "timeout", "attempt": attempt},
                    raw_output="",
                    tool_name=tool_name,
                )
            except Exception as exc:
                result = ToolResult(
                    status=ToolStatus.FAILED,
                    message=f"工具执行异常: {exc}",
                    data={"error_type": "exception", "attempt": attempt, "error": str(exc)},
                    raw_output="",
                    tool_name=tool_name,
                )

            last_result = result
            if result.status == ToolStatus.SUCCESS:
                return result

            self.record_trajectory(
                "tool_retry",
                {
                    "tool": tool_name,
                    "attempt": attempt,
                    "status": result.status.value,
                    "message": result.message,
                },
            )

        return last_result or ToolResult(
            status=ToolStatus.FAILED,
            message=f"工具执行失败: {tool_name}",
            data={"error_type": "unknown"},
            raw_output="",
            tool_name=tool_name,
        )

    async def _sense_environment(self, reason: str) -> ToolResult | None:
        if "SenseEnvironment" not in self.tool_registry.list_tools():
            return None

        self.state_machine.transition(AgentState.PERCEIVE)
        payload: dict[str, Any] = {}
        if self._observation_targets:
            payload["targets"] = sorted(self._observation_targets)
        perceive = await self._execute_tool_with_retry("SenseEnvironment", payload)
        self.memory_manager.add_perception_result(perceive.data)
        self.memory_manager.update_plan_for_tool("SenseEnvironment", perceive.success)
        self.record_trajectory(
            "perception",
            {
                "reason": reason,
                "args": payload,
                "status": perceive.status.value,
                "result": perceive.data,
            },
        )
        return perceive

    def _remember_tool_targets(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        """Track objects mentioned by tool calls so automatic perception is task-aware."""
        if tool_name == "SenseEnvironment":
            return

        def add_target(value: Any) -> None:
            if value in (None, ""):
                return
            if isinstance(value, str):
                self._observation_targets.add(value)
                return
            if isinstance(value, dict):
                if value.get("type") == "block" and value.get("color"):
                    self._observation_targets.add(f"{value['color']}_block")
                    return
                for key in ("name", "target", "source", "held_object", "object"):
                    nested = value.get(key)
                    if nested not in (None, ""):
                        add_target(nested)

        add_target(tool_args.get("target"))
        add_target(tool_args.get("held_object"))

        targets = tool_args.get("targets")
        if isinstance(targets, list):
            for target in targets:
                add_target(target)

        goal = tool_args.get("goal")
        if isinstance(goal, dict):
            add_target(goal.get("source") or goal.get("held_object"))
            add_target(goal.get("target"))

    def _finalize_is_allowed(self) -> tuple[bool, str]:
        current_task = self.memory_manager.current_task
        if not current_task or not current_task.last_perception_result:
            return False, "缺少最近一次环境感知结果，不能结束任务。"

        perception = current_task.last_perception_result
        safety = perception.get("safety_status", {})
        if safety.get("is_safe") is False:
            return False, f"安全状态未通过: {safety.get('issues', [])}"

        progress = perception.get("task_progress", {}).get("overall_completion")
        if isinstance(progress, (int, float)) and progress < self.config.finalize_min_completion:
            return False, f"任务完成度不足: {progress:.2f}"
        if current_task.failure_history and not isinstance(progress, (int, float)):
            return False, "存在工具失败记录，且最近感知没有给出明确完成度。"

        return True, "finalize allowed"

    async def _auto_reset_after_task_completion(self, summary: str) -> ToolResult | None:
        """Reset the robot after a confirmed task completion, without another LLM round."""
        if not self.config.auto_reset_on_task_complete:
            self.record_trajectory(
                "post_task_auto_reset_skipped",
                {"reason": "disabled_by_config", "summary": summary},
            )
            return None

        if "reset_robot" not in self.tool_registry.list_tools():
            self.record_trajectory(
                "post_task_auto_reset_skipped",
                {"reason": "reset_robot_not_registered", "summary": summary},
            )
            return None

        self.state_machine.transition(AgentState.ACT)
        self.record_trajectory(
            "post_task_auto_reset_start",
            {
                "tool": "reset_robot",
                "args": {},
                "trigger": "task_completed",
                "summary": summary,
            },
        )
        logger.info(f"[{self.agent_name}] 任务完成后自动复位机器人")

        result = await self._execute_tool_with_retry("reset_robot", {})
        self.record_trajectory(
            "post_task_auto_reset",
            {
                "tool": "reset_robot",
                "args": {},
                "status": result.status.value,
                "message": result.message,
                "result": result.data,
            },
        )

        if result.success:
            self.memory_manager.add_assistant_message("[Auto Reset] reset_robot completed after task completion.")
        else:
            self.memory_manager.add_assistant_message(f"[Auto Reset Failed] reset_robot: {result.message}")
            logger.warning(f"[{self.agent_name}] 任务完成后自动复位失败: {result.message}")
        return result

    def _format_auto_reset_final_note(self, result: ToolResult) -> str:
        if result.success:
            return "\n[自动复位] reset_robot 已完成。"
        return f"\n[自动复位失败] {result.message}"

    # ========== 轨迹管理 ==========
    def record_trajectory(self, step_type: str, detail: dict) -> None:
        """记录细粒度事件，并挂到当前 LLM round 下面。"""
        entry = {
            "event_step": len(self.event_trace) + 1,
            "type": step_type,
            "detail": detail,
            "state": self.state_machine.current_state.name,
            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        self.event_trace.append(entry)
        if self._active_llm_round is not None:
            self._active_llm_round.setdefault("events", []).append(deepcopy(entry))
            self._refresh_llm_round_detail(self._active_llm_round)
        logger.debug(f"[{self.agent_name}] 轨迹记录: {step_type}")

    def get_trajectory(self) -> list[dict]:
        """返回按 LLM 输入输出轮次组织的轨迹。"""
        return self.trajectory

    def get_event_trace(self) -> list[dict]:
        """返回细粒度事件流水，用于底层工具调试。"""
        return self.event_trace

    def _begin_llm_round(
        self,
        *,
        loop_step: int,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self._end_llm_round(status="superseded")
        tool_names = [
            tool.get("function", {}).get("name", "")
            for tool in tools
        ]
        llm_round = {
            "step": len(self.trajectory) + 1,
            "round": len(self.trajectory) + 1,
            "type": "llm_round",
            "loop_step": loop_step,
            "state": self.state_machine.current_state.name,
            "started_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "ended_at": None,
            "status": "running",
            "input": {
                "messages": deepcopy(messages),
                "message_count": len(messages),
                "tools": deepcopy(tools),
                "tool_names": tool_names,
                "tool_count": len(tools),
            },
            "output": None,
            "events": [],
            "detail": {
                "message_count": len(messages),
                "tool_count": len(tools),
                "tool_calls": [],
            },
        }
        self.trajectory.append(llm_round)
        self._active_llm_round = llm_round
        return llm_round

    def _record_llm_output(self, llm_res: Any) -> None:
        if self._active_llm_round is None:
            return
        tool_calls = [
            {
                "id": tool_call.id,
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            }
            for tool_call in (llm_res.tool_calls or [])
        ]
        self._active_llm_round["output"] = {
            "content": llm_res.content,
            "tool_calls": tool_calls,
            "finish_reason": llm_res.finish_reason,
            "model": llm_res.model,
            "usage": {
                "total_tokens": llm_res.total_tokens,
                "prompt_tokens": llm_res.prompt_tokens,
                "completion_tokens": llm_res.completion_tokens,
            },
        }
        self._refresh_llm_round_detail(self._active_llm_round)

    def _record_llm_error(self, error_type: str, detail: dict[str, Any]) -> None:
        if self._active_llm_round is None:
            return
        self._active_llm_round["error"] = {
            "type": error_type,
            "detail": detail,
        }
        self.record_trajectory(error_type, detail)

    def _end_llm_round(self, status: str = "completed") -> None:
        if self._active_llm_round is None:
            return
        if self._active_llm_round.get("status") == "running":
            self._active_llm_round["status"] = status
        self._active_llm_round["ended_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        self._refresh_llm_round_detail(self._active_llm_round)
        self._active_llm_round = None

    def _refresh_llm_round_detail(self, llm_round: dict[str, Any]) -> None:
        output = llm_round.get("output") or {}
        events = llm_round.get("events") or []
        tool_events = [
            event for event in events
            if event.get("type") in {"tool_call", "tool_call_deferred", "perception"}
        ]
        llm_round["detail"] = {
            "message_count": llm_round.get("input", {}).get("message_count", 0),
            "tool_count": llm_round.get("input", {}).get("tool_count", 0),
            "tool_calls": [
                call.get("name")
                for call in output.get("tool_calls", [])
            ],
            "event_count": len(events),
            "tool_event_count": len(tool_events),
            "finish_reason": output.get("finish_reason"),
        }

    def save_trajectory(self, final_response: str = "") -> str:
        """把本轮对话和执行轨迹落盘，便于调试和复盘。"""
        root = Path(__file__).resolve().parents[3]
        trajectory_dir = Path(self.config.trajectory_dir)
        if not trajectory_dir.is_absolute():
            trajectory_dir = root / trajectory_dir
        trajectory_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{self.agent_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path = trajectory_dir / filename
        payload = {
            "agent_name": self.agent_name,
            "task_brief": self.current_task_brief,
            "action_guidance": self.current_action_guidance,
            "final_response": final_response,
            "trajectory_format": "llm_rounds_v1",
            "trajectory": self.trajectory,
            "event_trace": self.event_trace,
            "contexts": self.memory_manager.get_current_contexts(
                system_prompt=self.get_current_system_prompt()
            ),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.last_trajectory_path = str(path)
        logger.info(f"[{self.agent_name}] 轨迹已保存: {path}")
        return str(path)

    # ========== 核心执行流程（增强版） ==========

    async def run_once(self, user_input: str) -> str:
        """
        真正的 ReAct 主循环（推荐的 Agent 核心流程）

        循环逻辑：
        1. 调用 LLM（带当前上下文 + 工具列表）
        2. 如果 LLM 返回 tool_calls：
           - 执行对应工具
           - 把结果写入记忆
           - 行动后主动调用 SenseEnvironment 感知
        3. 如果 LLM 调用了 FinalizeTask → 结束任务
        4. 如果 LLM 没有返回 tool_call → 结束循环（给出最终回复）
        """
        logger.info(f"[{self.agent_name}] 收到任务: {user_input}")

        # 用户输入写入记忆
        self.memory_manager.add_user_message(user_input)
        self.record_trajectory("user_input", {"input": user_input})

        final_response = ""
        max_steps = self.config.max_steps
        step_count = 0

        try:
            while step_count < max_steps:
                step_count += 1
                self.state_machine.transition(AgentState.CHAT)

                contexts = self.memory_manager.get_current_contexts(
                    system_prompt=self.get_current_system_prompt()
                )
                tools_schema = self._get_tools_schema()
                self._begin_llm_round(
                    loop_step=step_count,
                    messages=contexts,
                    tools=tools_schema,
                )

                try:
                    llm_res = await self._chat_with_timeout(
                        messages=contexts,
                        tools=tools_schema,
                    )
                except asyncio.TimeoutError:
                    final_response = "LLM 调用超时，任务中止。"
                    self.memory_manager.add_assistant_message(final_response)
                    self._record_llm_error(
                        "llm_timeout",
                        {"timeout": self.config.llm_timeout_seconds},
                    )
                    self._end_llm_round(status="llm_timeout")
                    break

                self._record_llm_output(llm_res)
                logger.info(f"[{self.agent_name}] LLM: {llm_res.content}")

                if not llm_res.has_tool_call:
                    # LLM 没有调用工具，可能是最终回复
                    final_response = llm_res.content or "任务完成。"
                    current_task = self.memory_manager.current_task
                    if current_task and current_task.failure_history:
                        final_response = (
                            "任务未确认完成：存在未解决的工具失败或完成验证阻塞，"
                            "需要重新感知、重试或人工检查。"
                        )
                    self.memory_manager.add_assistant_message(final_response)
                    self.record_trajectory("final_response", {"content": final_response})
                    self._end_llm_round(status="final_response")
                    break

                # 有 tool_call，逐个执行
                self.memory_manager.add_assistant_tool_calls(
                    content=llm_res.content,
                    tool_calls=self._format_openai_tool_calls(llm_res.tool_calls),
                )
                self.record_trajectory(
                    "assistant_tool_calls",
                    {
                        "content": llm_res.content,
                        "tool_calls": [
                            {
                                "id": tool_call.id,
                                "name": tool_call.name,
                                "arguments": tool_call.arguments,
                            }
                            for tool_call in llm_res.tool_calls
                        ],
                    },
                )

                should_perceive_after_tools = False
                had_tool_failure = False

                for tool_call in llm_res.tool_calls:
                    tool_name = tool_call.name
                    tool_args = tool_call.arguments or {}
                    self._remember_tool_targets(tool_name, tool_args)

                    # 工具调用
                    self.state_machine.transition(AgentState.ACT)
                    logger.info(f"[{self.agent_name}] 执行工具: {tool_name}")

                    result = await self._execute_tool_with_retry(tool_name, tool_args)
                    self.memory_manager.add_tool_result(tool_name, result.to_dict(), tool_call.id)
                    self.memory_manager.update_plan_for_tool(tool_name, result.success)
                    self.record_trajectory(
                        "tool_call",
                        {
                            "tool": tool_name,
                            "args": tool_args,
                            "status": result.status.value,
                            "message": result.message,
                            "result": result.data,
                        },
                    )

                    if not result.success:
                        had_tool_failure = True
                        failure = {
                            "tool": tool_name,
                            "args": tool_args,
                            "status": result.status.value,
                            "message": result.message,
                            "data": result.data,
                        }
                        self.memory_manager.add_failure(failure)
                        self.memory_manager.add_assistant_message(
                            f"[Tool Failure] {tool_name}: {result.message}"
                        )

                    # 特殊处理：显式结束任务。先写入 tool result，保证 OpenAI tool_call 消息闭合。
                    if tool_name == "FinalizeTask":
                        allowed, reason = self._finalize_is_allowed()
                        if not allowed:
                            self.memory_manager.add_failure(
                                {
                                    "tool": "FinalizeTask",
                                    "args": tool_args,
                                    "status": "blocked",
                                    "message": reason,
                                }
                            )
                            self.memory_manager.add_assistant_message(f"[Finalize Blocked] {reason}")
                            self.record_trajectory("finalize_blocked", {"reason": reason})
                            continue

                        summary = tool_args.get("completion_summary", "任务完成")
                        self.memory_manager.mark_task_completed(summary)
                        self.memory_manager.update_plan_for_tool("FinalizeTask", True)
                        self.state_machine.transition(AgentState.FINALIZE)
                        final_response = f"[任务结束] {summary}"
                        self.record_trajectory("finalize", {"summary": summary})
                        logger.info(f"[{self.agent_name}] 任务显式结束: {summary}")
                        self._end_llm_round(status="finalized")
                        reset_result = await self._auto_reset_after_task_completion(summary)
                        if reset_result is not None:
                            final_response += self._format_auto_reset_final_note(reset_result)
                        self.state_machine.transition(AgentState.READY)
                        self.save_trajectory(final_response)
                        return final_response

                    if tool_name != "SenseEnvironment":
                        should_perceive_after_tools = True

                # 行动后主动感知（新设计核心）
                # 注意：必须等本轮所有 OpenAI tool result 写完后再追加感知信息。
                if had_tool_failure:
                    await self._sense_environment(reason="tool_failure_recovery")
                elif should_perceive_after_tools:
                    await self._sense_environment(reason="post_action_verification")
                self._end_llm_round(status="tool_round_completed")

            if not final_response:
                final_response = f"达到最大步骤数 {max_steps}，任务中止。"
                self.memory_manager.add_assistant_message(final_response)
                self.record_trajectory("max_steps_reached", {"max_steps": max_steps})

            # 正常结束（LLM 没有调用工具）
            self.state_machine.transition(AgentState.READY)
            self.save_trajectory(final_response)
            return final_response
        except Exception as exc:
            final_response = f"Agent 执行异常，任务中止: {exc}"
            self.memory_manager.add_assistant_message(final_response)
            self.record_trajectory("agent_error", {"error": str(exc)})
            self._end_llm_round(status="agent_error")
            self.state_machine.transition(AgentState.READY)
            self.save_trajectory(final_response)
            raise

    async def shutdown(self) -> None:
        logger.info(f"[{self.agent_name}] Agent 已关闭")
