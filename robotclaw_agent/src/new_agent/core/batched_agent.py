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
- For scene-switch button tasks, use the switch_scene tool directly with the
  button tag, base offset, and optional press_interval_s; its camera frame,
  press hold, lift height, and close-gripper value are fixed.
- When the next several atomic actions are deterministic and all arguments are
  already known from prior tool results, return them together as multiple
  tool_calls in one assistant response.
- Good batch candidates are deterministic sequences such as:
  open_gripper -> move_eef(approach_camera_m) -> move_eef(grasp_camera_m)
  and move_eef(place_hover_camera_m) -> move_eef(place_camera_m) -> open_gripper.
- Use short batches with clear checkpoints. End a batch before uncertain
  perception, stale detections, recovery decisions, or task finalization.
- After release or final placement, explicitly call SenseEnvironment with
  include_tags=true before calling FinalizeTask.
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
    }
    auto_perception_tools = {
        "reset_robot",
        "open_gripper",
        "close_gripper",
        "move_eef",
        "lift_eef",
        "place_down",
        "switch_scene",
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

                    result = await self._execute_tool_with_retry(tool_name, tool_args)
                    self._record_tool_result(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        result=result,
                        tool_call_id=tool_call.id,
                    )

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
                    await self._sense_environment(reason=checkpoint_reason)
                elif should_perceive_after_batch and not explicit_perception_in_batch:
                    await self._sense_environment(reason="post_batch_verification")
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
