# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
数据压缩模块

提供数据压缩和解压缩功能
"""

import gzip
import json
import zlib
from typing import Any

import zstandard as zstd

from corobot.transport import msgpack_numpy
from corobot.utils.log_setting import CoLogger as logger


class DataCompressor:
    """数据压缩器

    支持的方法：
    - "json"（纯 JSON，无压缩）
    - "msgpack"（MessagePack，无压缩，支持 numpy）
    - "json+gzip" / "json+zlib" / "json+zstd"
    - "msgpack+zstd"（推荐，支持 numpy）
    """

    def __init__(self, method: str = "msgpack+zstd", level: int = 3):
        """
        初始化压缩器

        Args:
            method: 压缩方法
            level: 压缩级别（用于 zstd/gzip/zlib）
        """
        self.method = method
        self.level = level
        self.logger = logger

        # 只要含 zstd 即创建压缩器
        if "zstd" in method:
            self.compressor = zstd.ZstdCompressor(level=level)

    def compress(self, data: dict[str, Any]) -> bytes:
        """
        压缩数据

        Args:
            data: 要压缩的数据

        Returns:
            bytes: 压缩后的字节数据
        """
        try:
            if self.method == "msgpack":
                # 使用MessagePack序列化，支持 numpy
                packed_data = msgpack_numpy.packb(data)
                return packed_data

            elif self.method == "json+gzip":
                # JSON序列化后gzip压缩
                json_data = json.dumps(
                    data,
                    separators=(",", ":"),
                    default=self._json_default_encoder,
                ).encode("utf-8")
                return gzip.compress(json_data, compresslevel=self.level)

            elif self.method == "json+zlib":
                # JSON序列化后zlib压缩
                json_data = json.dumps(
                    data,
                    separators=(",", ":"),
                    default=self._json_default_encoder,
                ).encode("utf-8")
                return zlib.compress(json_data, level=self.level)

            elif self.method == "json+zstd":
                # JSON序列化后zstd压缩
                json_data = json.dumps(
                    data,
                    separators=(",", ":"),
                    default=self._json_default_encoder,
                ).encode("utf-8")
                return self.compressor.compress(json_data)

            elif self.method == "msgpack+zstd":
                # MessagePack序列化后zstd压缩，支持 numpy
                packed_data = msgpack_numpy.packb(data)
                return self.compressor.compress(packed_data)

            else:
                # 纯 JSON 序列化
                return json.dumps(
                    data,
                    separators=(",", ":"),
                ).encode("utf-8")

        except Exception as e:
            self.logger.error(f"Compression failed: {e}")
            # 降级到简单JSON
            return json.dumps(
                data,
                separators=(",", ":"),
            ).encode("utf-8")


class DataDecompressor:
    """数据解压缩器"""

    def __init__(self):
        self.logger = logger
        self.decompressor = zstd.ZstdDecompressor()

    def decompress(self, compressed_data: bytes, method: str) -> dict[str, Any]:
        """
        解压缩数据

        Args:
            compressed_data: 压缩的字节数据
            method: 压缩方法

        Returns:
            Dict[str, Any]: 解压缩后的数据
        """
        try:
            if method == "msgpack":
                # MessagePack解包，支持 numpy
                return msgpack_numpy.unpackb(compressed_data)

            elif method == "json+gzip":
                # gzip解压后JSON反序列化
                json_data = gzip.decompress(compressed_data)
                return json.loads(json_data.decode("utf-8"))

            elif method == "json+zlib":
                # zlib解压后JSON反序列化
                json_data = zlib.decompress(compressed_data)
                return json.loads(json_data.decode("utf-8"))

            elif method == "json+zstd":
                # zstd解压后JSON反序列化
                json_data = self.decompressor.decompress(compressed_data)
                return json.loads(json_data.decode("utf-8"))

            elif method == "msgpack+zstd":
                # zstd解压后MessagePack解包，支持 numpy
                packed_data = self.decompressor.decompress(compressed_data)
                return msgpack_numpy.unpackb(packed_data)

            else:
                # 默认JSON反序列化
                return json.loads(compressed_data.decode("utf-8"))

        except Exception as e:
            self.logger.error(f"Decompression failed: {e}")
            # 尝试简单JSON解析
            try:
                return json.loads(compressed_data.decode("utf-8"))
            except Exception:
                return {}
