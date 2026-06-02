"""
Batched NewAgent variant.

This keeps the original atomic tools, but encourages and safely executes
multiple deterministic atomic tool calls from a single LLM response.
"""

import asyncio
import logging
from typing import Any

from .agent import NewAgent
from .state_machine import AgentState
from ..tools.tool_types import ToolResult, ToolStatus

logger = logging.getLogger(__name__)


BATCHED_TOOL_CALL_GUIDANCE = """

Batched atomic tool-call policy:
- Keep using the registered atomic tools; do not invent high-level composite
  robot tools.
- For normal AprilTag pick-and-place tasks, use prepare_tag_pick_place first
  when it is available, then batch the returned recommended grasp/place motion
  targets into short deterministic motion sequences.
- For scene-switch button tasks, use the switch_scene tool directly with no
  arguments unless the operator explicitly overrides the recipe. Button tag,
  arm, base offset, and press timing are centralized in mcp_control_recipes.
  Do not hand-code scene-switch parameters in the prompt.
- For drawer-magazine loading tasks, use the three visible stage tools in order:
  open_drawer_for_loading -> place_workpiece_in_drawer -> close_drawer_after_loading.
  The VLA prompts and tag/offset recipe are fixed by those tools; do not split
  them into separate start_policy or hand-written motion calls.
- If a previous tool result says a drawer-loading stage was deferred or not
  executed, retry that stage before moving to the next stage or FinalizeTask.
- When the next several atomic actions are deterministic and all arguments are
  already known from prior tool results, return them together as multiple
  tool_calls in one assistant response.
- Good batch candidates are deterministic sequences such as:
  open_gripper -> move_eef(approach_camera_m) -> move_eef(grasp_camera_m)
  and move_eef(place_hover_camera_m) -> move_eef(place_camera_m) -> open_gripper.
- Use short batches with clear checkpoints. End a batch before uncertain
  perception, stale detections, recovery decisions, or task finalization.
- After release or final placement, make sure there is a recent perception
  result before calling FinalizeTask. The runtime automatically runs
  post_batch_verification after motion batches; if the latest context already
  includes that fresh perception result, call FinalizeTask directly instead of
  calling SenseEnvironment again.
- For normal pick-and-place, after the final release and the automatic
  post_batch_verification completes, the runtime may reset the robot
  immediately. Do not call reset_robot again; continue to FinalizeTask using the
  latest placement perception.
- If any tool in a batch fails or returns partial, the runtime will stop the
  remaining batch and ask you to recover from the updated state.
"""


class BatchedNewAgent(NewAgent):
    """
    NewAgent variant optimized for fewer LLM round trips.

    Differences from NewAgent.run_once:
    - Executes all tool_calls from one LLM response as one batch.
    - Stops the remaining batch if any tool fails.
    - Defers remaining tools at critical checkpoints so perception can happen
      before continuing.
    - Runs automatic perception once per batch instead of once per action.
    - Still records every atomic tool_call in the trajectory.
    """

    critical_checkpoint_tools = {
        "close_gripper",
        "GraspAtCurrent",
        "PlaceHeldObject",
        "place_down",
        "switch_scene",
        "place_workpiece_in_drawer",
        "close_drawer_after_loading",
        "load_workpiece_to_drawer",
    }
    auto_perception_tools = {
        "reset_robot",
        "open_gripper",
        "close_gripper",
        "move_eef",
        "lift_eef",
        "place_down",
        "switch_scene",
        "open_drawer_for_loading",
        "place_workpiece_in_drawer",
        "close_drawer_after_loading",
        "load_workpiece_to_drawer",
        "ApproachTarget",
        "AlignTarget",
        "GraspAtCurrent",
        "MoveEndEffectorToWorld",
        "PlaceHeldObject",
        "ReturnToDefaultAbsPose",
        "LocateObject",
        "GraspObject",
        "PlaceObject",
    }

    def get_current_system_prompt(self) -> str:
        return super().get_current_system_prompt() + BATCHED_TOOL_CALL_GUIDANCE

    def _is_critical_checkpoint_tool(self, tool_name: str) -> bool:
        return tool_name in self.critical_checkpoint_tools

    def _should_auto_perceive_after_tool(self, tool_name: str) -> bool:
        return tool_name in self.auto_perception_tools

    def _reset_batch_runtime_guards(self) -> None:
        self._pending_deferred_tools: list[str] = []
        self._drawer_loading_state: dict[str, bool] = {
            "started": False,
            "opened": False,
            "placed": False,
            "closed": False,
        }
        self._recent_auto_perception_available = False
        self._recent_auto_perception_reason: str | None = None
        self._pick_place_flow_started = False
        self._held_object_likely = False
        self._terminal_release_pending_reset = False
        self._early_auto_reset_result: ToolResult | None = None

    def _mark_deferred_tool(self, tool_name: str) -> None:
        if tool_name not in self._pending_deferred_tools:
            self._pending_deferred_tools.append(tool_name)

    def _clear_deferred_tool(self, tool_name: str) -> None:
        self._pending_deferred_tools = [
            name for name in self._pending_deferred_tools if name != tool_name
        ]

    def _update_drawer_loading_state(self, tool_name: str, result: ToolResult) -> None:
        if not result.success:
            return
        if tool_name == "open_drawer_for_loading":
            self._drawer_loading_state.update(
                {"started": True, "opened": True, "placed": False, "closed": False}
            )
        elif tool_name == "place_workpiece_in_drawer":
            self._drawer_loading_state.update({"started": True, "placed": True})
        elif tool_name == "close_drawer_after_loading":
            self._drawer_loading_state.update({"started": True, "closed": True})
        elif tool_name == "load_workpiece_to_drawer":
            self._drawer_loading_state.update(
                {"started": True, "opened": True, "placed": True, "closed": True}
            )

    def _update_pick_place_reset_state(self, tool_name: str, result: ToolResult) -> None:
        if not result.success:
            return
        if tool_name == "prepare_tag_pick_place":
            self._pick_place_flow_started = True
        elif tool_name == "close_gripper" and self._pick_place_flow_started:
            self._held_object_likely = True
        elif tool_name == "open_gripper" and self._pick_place_flow_started and self._held_object_likely:
            self._held_object_likely = False
            self._terminal_release_pending_reset = True
        elif tool_name == "reset_robot":
            self._held_object_likely = False
            self._terminal_release_pending_reset = False

    def _validate_drawer_loading_call(self, tool_name: str) -> ToolResult | None:
        if tool_name == "place_workpiece_in_drawer" and not self._drawer_loading_state.get("opened"):
            return self._make_synthetic_tool_result(
                tool_name=tool_name,
                status=ToolStatus.FAILED,
                message=(
                    "drawer loading stage order blocked: "
                    "open_drawer_for_loading must succeed before place_workpiece_in_drawer"
                ),
                data={"error_type": "drawer_stage_order", "required_previous_tool": "open_drawer_for_loading"},
            )
        if tool_name == "close_drawer_after_loading" and not self._drawer_loading_state.get("placed"):
            return self._make_synthetic_tool_result(
                tool_name=tool_name,
                status=ToolStatus.FAILED,
                message=(
                    "drawer loading stage order blocked: "
                    "place_workpiece_in_drawer must succeed before close_drawer_after_loading"
                ),
                data={"error_type": "drawer_stage_order", "required_previous_tool": "place_workpiece_in_drawer"},
            )
        return None

    def _skip_redundant_sense_environment(self, tool_name: str) -> ToolResult | None:
        if tool_name != "SenseEnvironment" or not self._recent_auto_perception_available:
            return None
        current_task = self.memory_manager.current_task
        perception = current_task.last_perception_result if current_task else None
        if not perception:
            return None
        return self._make_synthetic_tool_result(
            tool_name=tool_name,
            status=ToolStatus.SUCCESS,
            message=(
                "Skipped redundant SenseEnvironment: latest automatic perception "
                f"({self._recent_auto_perception_reason}) is already available. "
                "Use the latest perception result and call FinalizeTask if the task is complete."
            ),
            data={
                "skipped": True,
                "reason": "recent_auto_perception_available",
                "auto_perception_reason": self._recent_auto_perception_reason,
                "latest_perception": perception,
            },
        )

    def _finalize_is_allowed(self) -> tuple[bool, str]:
        if self._pending_deferred_tools:
            return (
                False,
                "存在尚未执行的 deferred tool，必须先重试: "
                + ", ".join(self._pending_deferred_tools),
            )
        if self._drawer_loading_state.get("started"):
            if not self._drawer_loading_state.get("placed"):
                return False, "抽屉上料流程未完成：place_workpiece_in_drawer 尚未成功执行。"
            if not self._drawer_loading_state.get("closed"):
                return False, "抽屉上料流程未完成：close_drawer_after_loading 尚未成功执行。"
        return super()._finalize_is_allowed()

    def _make_synthetic_tool_result(
        self,
        *,
        tool_name: str,
        status: ToolStatus,
        message: str,
        data: dict[str, Any],
    ) -> ToolResult:
        return ToolResult(
            status=status,
            message=message,
            data=data,
            raw_output="",
            tool_name=tool_name,
        )

    def _record_tool_result(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        result: ToolResult,
        tool_call_id: str | None,
        step_type: str = "tool_call",
    ) -> None:
        self.memory_manager.add_tool_result(tool_name, result.to_dict(), tool_call_id)
        if result.status == ToolStatus.SUCCESS:
            self.memory_manager.update_plan_for_tool(tool_name, True)
        self.record_trajectory(
            step_type,
            {
                "tool": tool_name,
                "args": tool_args,
                "status": result.status.value,
                "message": result.message,
                "result": result.data,
            },
        )

    async def run_once(self, user_input: str) -> str:
        """
        ReAct loop with batched atomic tool execution.
        """
        logger.info(f"[{self.agent_name}] 收到任务: {user_input}")
        self._reset_batch_runtime_guards()

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

                self.memory_manager.add_assistant_tool_calls(
                    content=llm_res.content,
                    tool_calls=self._format_openai_tool_calls(llm_res.tool_calls),
                )
                self.record_trajectory(
                    "assistant_tool_calls",
                    {
                        "content": llm_res.content,
                        "batch_size": len(llm_res.tool_calls),
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

                should_perceive_after_batch = False
                explicit_perception_in_batch = False
                had_tool_failure = False
                checkpoint_reason: str | None = None

                for index, tool_call in enumerate(llm_res.tool_calls):
                    tool_name = tool_call.name
                    tool_args = tool_call.arguments or {}
                    self._remember_tool_targets(tool_name, tool_args)

                    self.state_machine.transition(AgentState.ACT)
                    logger.info(f"[{self.agent_name}] 执行批量工具: {tool_name}")

                    result = self._skip_redundant_sense_environment(tool_name)
                    if result is None:
                        result = self._validate_drawer_loading_call(tool_name)
                    if result is None:
                        result = await self._execute_tool_with_retry(tool_name, tool_args)
                    self._record_tool_result(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        result=result,
                        tool_call_id=tool_call.id,
                    )
                    if result.success:
                        self._clear_deferred_tool(tool_name)
                    self._update_drawer_loading_state(tool_name, result)
                    self._update_pick_place_reset_state(tool_name, result)
                    if result.success and tool_name not in {"SenseEnvironment", "FinalizeTask"}:
                        self._recent_auto_perception_available = False
                        self._recent_auto_perception_reason = None

                    if tool_name == "SenseEnvironment":
                        explicit_perception_in_batch = True
                    elif self._should_auto_perceive_after_tool(tool_name):
                        should_perceive_after_batch = True

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
                        self._defer_remaining_batch(
                            llm_res.tool_calls[index + 1:],
                            reason=f"batch stopped after failed tool: {tool_name}",
                            status=ToolStatus.PARTIAL,
                        )
                        self.record_trajectory(
                            "batch_aborted",
                            {
                                "reason": "tool_failure",
                                "failed_tool": tool_name,
                                "remaining_tools": [
                                    tc.name for tc in llm_res.tool_calls[index + 1:]
                                ],
                            },
                        )
                        break

                    if tool_name == "FinalizeTask":
                        allowed, reason = self._finalize_is_allowed()
                        if not allowed:
                            had_tool_failure = True
                            self.memory_manager.add_failure(
                                {
                                    "tool": "FinalizeTask",
                                    "args": tool_args,
                                    "status": "blocked",
                                    "message": reason,
                                }
                            )
                            self.memory_manager.add_assistant_message(
                                f"[Finalize Blocked] {reason}"
                            )
                            self.record_trajectory("finalize_blocked", {"reason": reason})
                            self._defer_remaining_batch(
                                llm_res.tool_calls[index + 1:],
                                reason=f"batch stopped after blocked FinalizeTask: {reason}",
                                status=ToolStatus.PARTIAL,
                            )
                            break

                        self._defer_remaining_batch(
                            llm_res.tool_calls[index + 1:],
                            reason="task finalized before remaining batched tools",
                            status=ToolStatus.PARTIAL,
                        )
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

                    if self._is_critical_checkpoint_tool(tool_name):
                        remaining = llm_res.tool_calls[index + 1:]
                        if remaining:
                            checkpoint_reason = f"critical_checkpoint_after_{tool_name}"
                            self._defer_remaining_batch(
                                remaining,
                                reason=(
                                    f"deferred until perception checkpoint after {tool_name}"
                                ),
                                status=ToolStatus.PARTIAL,
                            )
                            self.record_trajectory(
                                "batch_checkpoint",
                                {
                                    "tool": tool_name,
                                    "remaining_tools": [tc.name for tc in remaining],
                                },
                            )
                            break

                if had_tool_failure:
                    await self._sense_environment(reason="tool_failure_recovery")
                elif checkpoint_reason:
                    perception = await self._sense_environment(reason=checkpoint_reason)
                    if perception and perception.success:
                        self._recent_auto_perception_available = True
                        self._recent_auto_perception_reason = checkpoint_reason
                    await self._auto_reset_after_terminal_release_if_ready()
                elif should_perceive_after_batch and not explicit_perception_in_batch:
                    perception = await self._sense_environment(reason="post_batch_verification")
                    if perception and perception.success:
                        self._recent_auto_perception_available = True
                        self._recent_auto_perception_reason = "post_batch_verification"
                    await self._auto_reset_after_terminal_release_if_ready()
                self._end_llm_round(status="tool_round_completed")

            if not final_response:
                final_response = f"达到最大步骤数 {max_steps}，任务中止。"
                self.memory_manager.add_assistant_message(final_response)
                self.record_trajectory("max_steps_reached", {"max_steps": max_steps})

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

    def _defer_remaining_batch(
        self,
        remaining_tool_calls: list[Any],
        *,
        reason: str,
        status: ToolStatus,
    ) -> None:
        for tool_call in remaining_tool_calls:
            tool_name = tool_call.name
            tool_args = tool_call.arguments or {}
            self._mark_deferred_tool(tool_name)
            result = self._make_synthetic_tool_result(
                tool_name=tool_name,
                status=status,
                message=f"工具未执行：{reason}",
                data={
                    "error_type": "batch_deferred",
                    "reason": reason,
                },
            )
            self._record_tool_result(
                tool_name=tool_name,
                tool_args=tool_args,
                result=result,
                tool_call_id=tool_call.id,
                step_type="tool_call_deferred",
            )

    async def _auto_reset_after_terminal_release_if_ready(self) -> ToolResult | None:
        if not self._terminal_release_pending_reset or self._early_auto_reset_result is not None:
            return None
        if not self.config.auto_reset_on_task_complete:
            return None
        if "reset_robot" not in self.tool_registry.list_tools():
            return None

        self.record_trajectory(
            "terminal_release_auto_reset_start",
            {
                "tool": "reset_robot",
                "args": {},
                "trigger": "terminal_release_post_batch_verification",
            },
        )
        logger.info(f"[{self.agent_name}] 末端释放并完成自动感知后提前复位机器人")

        result = await self._execute_tool_with_retry("reset_robot", {})
        self._early_auto_reset_result = result
        self._terminal_release_pending_reset = False
        self.record_trajectory(
            "terminal_release_auto_reset",
            {
                "tool": "reset_robot",
                "args": {},
                "status": result.status.value,
                "message": result.message,
                "result": result.data,
            },
        )
        self.memory_manager.add_assistant_message(
            f"[Auto Reset After Release] reset_robot: {result.message}"
        )
        return result

    async def _auto_reset_after_task_completion(self, summary: str) -> ToolResult | None:
        if self._early_auto_reset_result is not None:
            self.record_trajectory(
                "post_task_auto_reset_reused",
                {
                    "tool": "reset_robot",
                    "trigger": "task_completed",
                    "reason": "already_reset_after_terminal_release",
                    "summary": summary,
                    "status": self._early_auto_reset_result.status.value,
                    "message": self._early_auto_reset_result.message,
                },
            )
            return self._early_auto_reset_result
        return await super()._auto_reset_after_task_completion(summary)
