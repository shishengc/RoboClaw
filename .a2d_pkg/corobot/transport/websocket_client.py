# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
WebSocket客户端
用于与外部模型服务通信
"""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import websockets

from corobot.transport.compression import DataCompressor, DataDecompressor
from corobot.utils.log_setting import CoLogger as logger


class WebSocketClient:
    """WebSocket客户端"""

    def __init__(self, config: dict[str, Any]):
        """
        初始化WebSocket客户端

        Args:
            config: 配置字典
        """
        self.config = config
        self.logger = logger

        # 连接配置
        self.server_url = config.get("server_url", "ws://localhost:8765")
        self.timeout = config.get("timeout", 5.0)
        self.retry_count = config.get("retry_count", 3)
        self.auto_reconnect = config.get("auto_reconnect", True)

        # 压缩配置
        compression_config = config.get("compression", {})
        self.compression_enabled = compression_config.get("enabled", True)
        if self.compression_enabled:
            compression_method = compression_config.get("method", "json+zstd")
            compression_level = compression_config.get("level", 3)
            self.compressor = DataCompressor(compression_method, compression_level)
            self.decompressor = DataDecompressor()

        # 连接状态
        self.websocket = None
        self.is_connected = False
        self.connection_lock = asyncio.Lock()

        # 回调函数
        self.on_connect: Callable | None = None
        self.on_disconnect: Callable | None = None
        self.on_error: Callable | None = None

    async def connect(self) -> bool:
        """
        连接到WebSocket服务器

        Returns:
            bool: 连接是否成功
        """
        async with self.connection_lock:
            if self.is_connected:
                return True

            try:
                self.websocket = await websockets.connect(
                    self.server_url, timeout=self.timeout, ping_interval=20, ping_timeout=10
                )
                self.is_connected = True
                self.logger.info(f"Connected to WebSocket server: {self.server_url}")

                if self.on_connect:
                    await self.on_connect()

                return True

            except Exception as e:
                self.logger.error(f"Failed to connect to {self.server_url}: {e}")
                self.is_connected = False

                if self.on_error:
                    await self.on_error(e)

                return False

    async def disconnect(self):
        """断开WebSocket连接"""
        async with self.connection_lock:
            if self.websocket and self.is_connected:
                try:
                    await self.websocket.close()
                    self.logger.info("WebSocket connection closed")
                except Exception as e:
                    self.logger.error(f"Error closing WebSocket: {e}")
                finally:
                    self.websocket = None
                    self.is_connected = False

                    if self.on_disconnect:
                        await self.on_disconnect()

    async def send_message(self, message: dict[str, Any]) -> bool:
        """
        发送消息

        Args:
            message: 要发送的消息

        Returns:
            bool: 发送是否成功
        """
        if not self.is_connected:
            if not await self.connect():
                return False

        try:
            # 添加消息元数据
            message_with_meta = {
                "timestamp": time.time_ns(),
                "compressed": self.compression_enabled,
                "data": message,
            }

            # 序列化和压缩
            if self.compression_enabled:
                compressed_data = self.compressor.compress(message_with_meta)
                await self.websocket.send(compressed_data)
            else:
                json_data = json.dumps(message_with_meta)
                await self.websocket.send(json_data)

            return True

        except websockets.exceptions.ConnectionClosed:
            self.logger.warning("WebSocket connection closed during send")
            self.is_connected = False
            return False

        except Exception as e:
            self.logger.error(f"Failed to send message: {e}")
            return False

    async def receive_message(self, timeout: float | None = None) -> dict[str, Any] | None:
        """
        接收消息

        Args:
            timeout: 超时时间

        Returns:
            Optional[Dict[str, Any]]: 接收到的消息，失败返回None
        """
        if not self.is_connected:
            return None

        try:
            # 设置超时
            receive_timeout = timeout or self.timeout
            message_data = await asyncio.wait_for(self.websocket.recv(), timeout=receive_timeout)

            # 解压缩和反序列化
            if isinstance(message_data, bytes):
                # 压缩数据
                message = self.decompressor.decompress(message_data, self.compressor.method)
            else:
                # JSON数据
                message = json.loads(message_data)

            # 提取实际数据
            return message.get("data", message)

        except asyncio.TimeoutError:
            self.logger.warning(f"Receive timeout after {receive_timeout}s")
            return None

        except websockets.exceptions.ConnectionClosed:
            self.logger.warning("WebSocket connection closed during receive")
            self.is_connected = False
            return None

        except Exception as e:
            self.logger.error(f"Failed to receive message: {e}")
            return None

    async def send_and_wait(
        self, message: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any] | None:
        """
        发送消息并等待响应

        Args:
            message: 要发送的消息
            timeout: 超时时间

        Returns:
            Optional[Dict[str, Any]]: 响应消息，失败返回None
        """
        # 添加请求ID用于匹配响应
        request_id = str(time.time_ns())
        message["request_id"] = request_id

        # 发送消息
        if not await self.send_message(message):
            return None

        # 等待响应
        start_time = time.time()
        response_timeout = timeout or self.timeout

        while time.time() - start_time < response_timeout:
            response = await self.receive_message(timeout=1.0)
            if response and response.get("request_id") == request_id:
                return response

        self.logger.warning("No matching response received within timeout")
        return None

    async def send_with_retry(
        self, message: dict[str, Any], max_retries: int | None = None
    ) -> bool:
        """
        带重试的消息发送

        Args:
            message: 要发送的消息
            max_retries: 最大重试次数

        Returns:
            bool: 发送是否成功
        """
        retries = max_retries or self.retry_count

        for attempt in range(retries + 1):
            if await self.send_message(message):
                return True

            if attempt < retries:
                self.logger.warning(f"Send attempt {attempt + 1} failed, retrying...")
                await asyncio.sleep(1.0 * (attempt + 1))  # 指数退避

                # 尝试重连
                if not self.is_connected and self.auto_reconnect:
                    await self.connect()

        self.logger.error(f"Failed to send message after {retries + 1} attempts")
        return False

    async def health_check(self) -> bool:
        """
        健康检查

        Returns:
            bool: 连接是否健康
        """
        if not self.is_connected:
            return False

        try:
            # 发送ping消息
            ping_message = {"type": "ping", "timestamp": time.time_ns()}

            response = await self.send_and_wait(ping_message, timeout=2.0)
            return response is not None and response.get("type") == "pong"

        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return False

    def get_connection_info(self) -> dict[str, Any]:
        """
        获取连接信息

        Returns:
            Dict[str, Any]: 连接信息
        """
        return {
            "server_url": self.server_url,
            "is_connected": self.is_connected,
            "compression_enabled": self.compression_enabled,
            "timeout": self.timeout,
            "retry_count": self.retry_count,
        }
