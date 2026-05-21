# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
WebSocket应用服务端

专门处理WebSocket协议的服务器应用
"""

import asyncio
import signal
import time
from typing import Any

from corobot.dataloader import DataLoaderFactory
from corobot.motion_control.async_motion_controller import AsyncMotionController
from corobot.motion_control.mock_motion_controller import MockMotionController
from corobot.protocol.protocol_schemas import STD_MODEL_OUTPUT
from corobot.transport.websocket_server import WebSocketServer
from corobot.utils.log_setting import CoLogger as logger


class WebSocketApp:
    """WebSocket应用类，专门处理WebSocket协议"""

    def __init__(self, config: dict[str, Any]):
        self.config = config or {}
        self.logger = logger

        # 子配置
        self._comm_cfg: dict[str, Any] = self.config.get("communication", {})
        self._dl_cfg: dict[str, Any] = self.config.get("dataloader", {})
        self._mc_cfg: dict[str, Any] = self.config.get("motion_controller", {})
        self._mc_timeout_s: float = float(self._mc_cfg.get("execution_timeout_s", 2.0))

        # 组件
        self._server: WebSocketServer | None = None
        self._dataloader = None
        self._controller: AsyncMotionController | None = None

        # 运行状态
        self._stopping: bool = False

    # ------------------------- 生命周期 -------------------------
    async def start(self) -> None:
        """初始化并启动服务"""
        self.logger.info("Starting WebSocketApp ...")

        # dataloader
        self._dataloader = DataLoaderFactory.create_data_loader(self._dl_cfg)
        # dataloader需要等待一会，确保数据源已经准备好
        await asyncio.sleep(1)
        self.logger.info("DataLoader initialized")

        # motion controller（按需在执行时启动/切换模式，这里仅构造）
        if self._mc_cfg.get("enabled", False):
            self._controller = AsyncMotionController(self._mc_cfg)
            self._controller.start()
            self.logger.info("MotionController initialized")
        else:
            self.logger.info("MotionController disabled")
            self._controller = MockMotionController()

        # server
        self._server = WebSocketServer(self.config)
        self._register_message_handlers(self._server)

        started = await self._server.start_server()
        if not started:
            raise RuntimeError("Failed to start WebSocket server")

        self.logger.info(f"WebSocketApp started at {self._server.host}:{self._server.port}")

    async def stop(self) -> None:
        """优雅关闭"""
        if self._stopping:
            return
        self._stopping = True

        # self._controller.stop()  # 停止控制器

        self.logger.info("Stopping WebSocketApp ...")
        # 停止 server
        if self._server and self._server.is_running:
            try:
                await self._server.stop_server()
            except RuntimeError:
                # 测试框架可能已关闭事件循环
                pass

        # 清理 dataloader
        try:
            if self._dataloader and hasattr(self._dataloader, "shutdown"):
                self._dataloader.shutdown()
        except Exception as e:
            self.logger.warning(f"Shutdown dataloader failed: {e}")

        self.logger.info("WebSocketApp stopped")

    async def run_forever(self) -> None:
        """运行直至信号退出"""
        await self.start()

        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _signal_handler() -> None:
            self.logger.info("Received stop signal")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows 等环境不支持
                pass

        await stop_event.wait()
        await self.stop()

    # ------------------------- 消息处理 -------------------------
    def _register_message_handlers(self, server: WebSocketServer) -> None:
        server.on_message = self._on_any_message
        server.register_handler("get_std_model_input", self._handle_get_std_model_input)
        server.register_handler("submit_std_model_output", self._handle_submit_std_model_output)

    async def _on_any_message(self, websocket, message: dict[str, Any], client_addr: str):
        self.logger.debug(f"on_message from {client_addr}: {message.get('type')}")

    async def _handle_get_std_model_input(self, message: dict[str, Any], websocket) -> dict[str, Any]:
        """返回 dataloader 的 STD_MODEL_INPUT"""
        request_id = message.get("request_id", "No request_id")
        filter_args = message.get("filter_args", None)  # 新增：获取过滤参数
        try:
            t1 = time.perf_counter()
            payload = self._dataloader.get_payload(filter_args=filter_args)  # 传递过滤参数
            t2 = time.perf_counter()
            self.logger.debug(f"get_payload time: {(t2 - t1) * 1000} ms")
            # Pydantic v2: 用 python 模式，保留 numpy 给通信层处理
            if hasattr(payload, "model_dump"):
                payload_dict = payload.model_dump(mode="python")
            elif hasattr(payload, "dict"):
                payload_dict = payload.dict()
            else:
                payload_dict = payload

            return {
                "type": "std_model_input",
                "request_id": request_id,
                "data": payload_dict,
            }
        except Exception as e:
            self.logger.error(f"get_std_model_input failed: {e}")
            return {
                "type": "error_response",
                "request_id": request_id,
                "data": {"error": str(e)},
            }

    async def _handle_submit_std_model_output(self, message: dict[str, Any], websocket) -> dict[str, Any]:
        """接收 STD_MODEL_OUTPUT，调用控制器执行"""
        request_id = message.get("request_id")
        data = message.get("data") or {}
        try:
            # 校验/解析为模型
            if hasattr(STD_MODEL_OUTPUT, "model_validate"):
                std_output = STD_MODEL_OUTPUT.model_validate(data)
            else:
                std_output = STD_MODEL_OUTPUT(**data)

            # 执行（放到线程，添加超时保护，防止阻塞测试/事件循环）
            result = await asyncio.wait_for(
                asyncio.to_thread(self._controller.execute, std_output),  # type: ignore[union-attr]
                timeout=self._mc_timeout_s,
            )

            return {
                "type": "execute_response",
                "request_id": request_id,
                "data": {"success": True, "result": result},
            }
        except asyncio.TimeoutError as e:
            self.logger.error(f"execute timeout: {e}")
            return {
                "type": "execute_response",
                "request_id": request_id,
                "data": {"success": False, "error": "execution_timeout"},
            }
        except Exception as e:
            self.logger.error(f"submit_std_model_output failed: {e}")
            return {
                "type": "execute_response",
                "request_id": request_id,
                "data": {"success": False, "error": str(e)},
            }
