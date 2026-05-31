"""
LLM Client - 支持真实 OpenAI 调用

当前支持：
- Mock 模式（开发调试）
- 真实 OpenAI GPT-4o 调用（生产使用）
"""

import logging
import os
import json
import hashlib
from typing import Any
from openai import AsyncOpenAI
from .llm_response import LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class LLMClient:
    """
    LLM 调用客户端

    支持两种模式：
    1. mock_mode=True  → 模拟返回（快速开发）
    2. mock_mode=False → 真实调用 OpenAI GPT-4o
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        mock_mode: bool = True,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
    ):
        self.model = model
        self.mock_mode = mock_mode
        self.timeout_seconds = timeout_seconds
        self._total_tokens_used = 0
        self._mock_step = 0

        if not mock_mode:
            # 真实调用模式
            key = api_key or os.getenv("OPENAI_API_KEY")
            if not key:
                raise ValueError(
                    "真实调用模式需要 OpenAI API Key。"
                    "请设置环境变量 OPENAI_API_KEY 或传入 api_key 参数。"
                )
            self.client = AsyncOpenAI(api_key=key, timeout=timeout_seconds)
            logger.info(f"[LLMClient] 真实模式初始化完成，模型: {model}")
        else:
            self.client = None
            logger.info("[LLMClient] Mock 模式初始化完成")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs
    ) -> LLMResponse:
        if self.mock_mode:
            return await self._mock_chat(messages, tools)
        else:
            return await self._real_chat(messages, tools, **kwargs)

    # ==================== Mock 模式 ====================
    async def _mock_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        logger.info("[LLMClient] 使用 Mock 模式返回")
        from uuid import uuid4

        step = self._mock_step
        self._mock_step += 1

        if step == 0:
            tool_call = ToolCall(
                id=f"call_{uuid4().hex[:8]}",
                name="LocateObject",
                arguments={"object_name": "red_cube"}
            )
            return LLMResponse(
                content="先定位红色方块的位置。",
                tool_calls=[tool_call],
                total_tokens=64,
                model=self.model,
                finish_reason="tool_calls"
            )
        elif step == 1:
            tool_call = ToolCall(
                id=f"call_{uuid4().hex[:8]}",
                name="GraspObject",
                arguments={"object_name": "red_cube"}
            )
            return LLMResponse(
                content="抓取红色方块。",
                tool_calls=[tool_call],
                total_tokens=64,
                model=self.model,
                finish_reason="tool_calls"
            )
        elif step == 2:
            tool_call = ToolCall(
                id=f"call_{uuid4().hex[:8]}",
                name="PlaceObject",
                arguments={"target": "box"}
            )
            return LLMResponse(
                content="将方块放置到盒子里。",
                tool_calls=[tool_call],
                total_tokens=64,
                model=self.model,
                finish_reason="tool_calls"
            )
        elif step == 3:
            tool_call = ToolCall(
                id=f"call_{uuid4().hex[:8]}",
                name="FinalizeTask",
                arguments={"completion_summary": "已成功将红色方块放入旁边的盒子中。"}
            )
            return LLMResponse(
                content="任务已完成，调用 FinalizeTask 结束。",
                tool_calls=[tool_call],
                total_tokens=64,
                model=self.model,
                finish_reason="tool_calls"
            )
        else:
            # 后续不再调用工具，直接给出最终回复
            return LLMResponse(
                content="任务已通过原子操作和感知验证完成。",
                tool_calls=None,
                total_tokens=32,
                model=self.model,
                finish_reason="stop"
            )

    # ==================== 真实调用模式 ====================
    async def _real_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        prompt_cache_key: str | None = None,
    ) -> LLMResponse:
        """
        真实调用 OpenAI API
        """
        logger.info(f"[LLMClient] 真实调用 {self.model}，tools 数量: {len(tools) if tools else 0}")

        try:
            request_args: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "max_completion_tokens": max_tokens,
            }
            prompt_cache_key = prompt_cache_key or self._build_prompt_cache_key(messages, tools)
            if prompt_cache_key:
                request_args["extra_body"] = {"prompt_cache_key": prompt_cache_key}

            if not self.model.startswith(("gpt-5", "o")):
                request_args["temperature"] = temperature

            response = await self.client.chat.completions.create(**request_args)

            message = response.choices[0].message
            usage = response.usage

            tool_calls = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except Exception:
                        args = {"raw": tc.function.arguments}

                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=args
                        )
                    )

            result = LLMResponse(
                content=message.content,
                tool_calls=tool_calls,
                total_tokens=usage.total_tokens if usage else 0,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                finish_reason=response.choices[0].finish_reason or "stop",
                model=response.model
            )

            self._total_tokens_used += result.total_tokens
            logger.info(
                "[LLMClient] 调用成功，token 使用: %s，prompt_cache_key: %s",
                result.total_tokens,
                prompt_cache_key,
            )
            return result

        except Exception as e:
            logger.error(f"[LLMClient] OpenAI 调用失败: {e}")
            raise

    def _build_prompt_cache_key(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> str | None:
        if not messages:
            return None

        stable_prefix = {
            "model": self.model,
            "system": messages[0].get("content", ""),
            "tools": tools or [],
        }
        serialized = json.dumps(
            stable_prefix,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
        return f"robotclaw:{self.model}:{digest}"[:64]
