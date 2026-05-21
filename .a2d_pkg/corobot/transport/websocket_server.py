# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
WebSocket服务器
用于接收外部模型服务的连接
"""

import json
import time
from collections.abc import Callable
from typing import Any

import websockets
from websockets.server import WebSocketServerProtocol

from corobot.transport.compression import DataCompressor, DataDecompressor
from corobot.utils.log_setting import CoLogger as logger


class WebSocketServer:
    """WebSocket服务器"""

    def __init__(self, config: dict[str, Any]):
        """
        初始化WebSocket服务器

        Args:
            config: 配置字典
        """
        self.config = config
        self.logger = logger

        # 服务器配置
        self.communication_config = config.get("communication", {})
        self.host = self.communication_config.get("host", "127.0.0.1")
        self.port = self.communication_config.get("port", 8766)
        self.max_clients = self.communication_config.get("max_clients", 10)

        # 压缩配置
        compression_config = self.communication_config.get("compression", {})
        self.compression_enabled = compression_config.get("enabled", True)
        if self.compression_enabled:
            compression_method = compression_config.get("method", "msgpack")
            compression_level = compression_config.get("level", 1)
            self.compressor = DataCompressor(compression_method, compression_level)
            self.decompressor = DataDecompressor()

        # 服务器状态
        self.server = None
        self.is_running = False
        self.clients: set[WebSocketServerProtocol] = set()

        # 消息处理器
        self.message_handlers: dict[str, Callable] = {}

        # 回调函数
        self.on_client_connect: Callable | None = None
        self.on_client_disconnect: Callable | None = None
        self.on_message: Callable | None = None

    def register_handler(self, message_type: str, handler: Callable):
        """
        注册消息处理器

        Args:
            message_type: 消息类型
            handler: 处理器函数
        """
        self.message_handlers[message_type] = handler
        self.logger.info(f"Registered handler for message type: {message_type}")

    async def start_server(self) -> bool:
        """
        启动WebSocket服务器

        Returns:
            bool: 启动是否成功
        """
        try:
            # 优化WebSocket配置以提高大数据传输性能
            self.server = await websockets.serve(
                self._handle_client,
                self.host,
                self.port,
                max_size=None,  # 不限制消息大小
                ping_interval=None,  # 禁用ping减少开销
                ping_timeout=None,
                compression=None,  # 禁用WebSocket内置压缩
                write_limit=16 * 1024 * 1024,  # 16MB写缓冲区（大数据优化）
                read_limit=16 * 1024 * 1024,  # 16MB读缓冲区
            )

            self.is_running = True
            self.logger.info(f"WebSocket server started on {self.host}:{self.port}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to start WebSocket server: {e}")
            return False

    async def stop_server(self):
        """停止WebSocket服务器"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.is_running = False
            self.logger.info("WebSocket server stopped")

    async def _handle_client(self, websocket: WebSocketServerProtocol, path: str):
        """
        处理客户端连接

        Args:
            websocket: WebSocket连接
            path: 连接路径
        """
        client_address = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"

        # 检查客户端数量限制
        if len(self.clients) >= self.max_clients:
            self.logger.warning(f"Max clients reached, rejecting {client_address}")
            await websocket.close(code=1013, reason="Server overloaded")
            return

        # 添加客户端
        self.clients.add(websocket)
        self.logger.info(f"Client connected: {client_address} (total: {len(self.clients)})")

        if self.on_client_connect:
            await self.on_client_connect(websocket, client_address)

        try:
            async for message_data in websocket:
                await self._process_message(websocket, message_data, client_address)

        except websockets.exceptions.ConnectionClosedError:
            self.logger.info(f"Client disconnected: {client_address}")

        except Exception as e:
            self.logger.error(f"Error handling client {client_address}: {e}")

        finally:
            # 移除客户端
            self.clients.discard(websocket)
            self.logger.info(f"Client removed: {client_address} (remaining: {len(self.clients)})")

            if self.on_client_disconnect:
                await self.on_client_disconnect(websocket, client_address)

    async def _process_message(
        self, websocket: WebSocketServerProtocol, message_data: Any, client_address: str
    ):
        """
        处理接收到的消息

        Args:
            websocket: WebSocket连接
            message_data: 消息数据
            client_address: 客户端地址
        """
        try:
            # 解压缩和反序列化
            if isinstance(message_data, bytes):
                # 压缩数据
                message = self.decompressor.decompress(message_data, self.compressor.method)
            else:
                # JSON数据
                message = json.loads(message_data)

            # 提取实际数据：优先使用顶层包含 type 的消息；否则回退到 data 包裹
            if isinstance(message, dict) and "type" in message:
                actual_message = message
            else:
                actual_message = message.get("data", message)
            message_type = actual_message.get("type", "unknown")

            self.logger.info(f"Received message from {client_address}: {message_type}")

            # 调用通用消息回调
            if self.on_message:
                await self.on_message(websocket, actual_message, client_address)

            # 调用特定类型的处理器
            if message_type in self.message_handlers:
                try:
                    response = await self.message_handlers[message_type](actual_message, websocket)
                    # 统计 response 二进制数据量MB
                    if response:
                        await self.send_message(websocket, response)
                except Exception as e:
                    self.logger.error(f"Handler for {message_type} failed: {e}")
                    error_response = {
                        "type": "error_response",
                        "request_id": actual_message.get("request_id"),
                        "data": {"error": str(e)},
                    }
                    await self.send_message(websocket, error_response)
            else:
                # 默认处理ping消息
                if message_type == "ping":
                    pong_response = {
                        "type": "pong",
                        "timestamp": time.time_ns(),
                        "request_id": actual_message.get("request_id"),
                    }
                    await self.send_message(websocket, pong_response)
                else:
                    self.logger.warning(f"No handler for message type: {message_type}")
                    await self.send_message(
                        websocket,
                        {"type": "error", "error": f"No handler for message type: {message_type}"},
                    )

        except Exception as e:
            self.logger.error(f"Error processing message from {client_address}: {e}")
            await self.send_message(
                websocket, {"type": "error", "error": f"Error processing message: {e}"}
            )

    async def send_message(
        self, websocket: WebSocketServerProtocol, message: dict[str, Any]
    ) -> bool:
        """
        发送消息给客户端

        Args:
            websocket: WebSocket连接
            message: 要发送的消息

        Returns:
            bool: 发送是否成功
        """

        # 统计send_message耗时
        t0 = time.perf_counter_ns()
        try:
            # 序列化和压缩
            if self.compression_enabled:
                compressed_data = self.compressor.compress(message)
                # 统计 compressed_data 二进制数据量MB
                compressed_data_size_mb = len(compressed_data) / 1024 / 1024
                self.logger.debug(f"compressed_data size: {compressed_data_size_mb:.3f} MB")
                await websocket.send(compressed_data)
            else:
                json_data = json.dumps(message)
                # 统计 json_data 二进制数据量MB
                json_data_size_mb = len(json_data) / 1024 / 1024
                self.logger.debug(f"json_data size: {json_data_size_mb:.3f} MB")
                await websocket.send(json_data)
            t1 = time.perf_counter_ns()
            send_ms = (t1 - t0) / 1e6
            self.logger.debug(f"timing.send_message ms | send={send_ms:.3f}")
            return True

        except websockets.exceptions.ConnectionClosed:
            self.logger.warning("WebSocket connection closed during send")
            return False

        except Exception as e:
            self.logger.error(f"Failed to send message: {e}")
            return False

    async def broadcast_message(self, message: dict[str, Any]) -> int:
        """
        向所有客户端广播消息

        Args:
            message: 要广播的消息

        Returns:
            int: 成功发送的客户端数量
        """
        if not self.clients:
            return 0

        success_count = 0
        failed_clients = set()

        for client in self.clients.copy():
            if await self.send_message(client, message):
                success_count += 1
            else:
                failed_clients.add(client)

        # 移除失败的客户端
        self.clients -= failed_clients

        if failed_clients:
            self.logger.warning(f"Removed {len(failed_clients)} failed clients")

        return success_count

    def get_server_info(self) -> dict[str, Any]:
        """
        获取服务器信息

        Returns:
            Dict[str, Any]: 服务器信息
        """
        return {
            "host": self.host,
            "port": self.port,
            "is_running": self.is_running,
            "client_count": len(self.clients),
            "max_clients": self.max_clients,
            "compression_enabled": self.compression_enabled,
        }

    def get_client_list(self) -> list:
        """
        获取客户端列表

        Returns:
            list: 客户端地址列表
        """
        return [f"{client.remote_address[0]}:{client.remote_address[1]}" for client in self.clients]
