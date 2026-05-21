from typing import Any

import cv2
import numpy as np

from corobot.utils.log_setting import CoLogger as logger


def packet_to_dict(packet: Any) -> dict[str, Any] | None:
    """
    将 cosine_bus 数据包转换为字典格式

    Args:
        packet: cosine_bus 数据包

    Returns:
        包含图像数据的字典，如果转换失败则返回 None
    """
    # 验证数据包有效性
    if packet is None or str(packet.type) != "PacketType.IMAGE":
        logger.warning("无效的数据包或非图像类型")
        return None

    # 获取图像数据
    image_data = packet.get_image_data()
    if image_data is None:
        logger.warning("数据包中图像数据为空")
        return None

    try:
        # 提取图像元数据
        encoding = str(packet.encoding_format)
        color_format = str(packet.color_format)
        width = int(packet.image_width)
        height = int(packet.image_height)

        # 记录转换信息
        # logger.debug(
        #     f"转换数据包: encoding={encoding}, color_format={color_format}, width={width}, height={height}"
        # )

        # 构建结果字典
        image_dict = {
            "encoding": encoding,
            "color_format": color_format,
            "width": width,
            "height": height,
            "image_data": np.frombuffer(image_data, dtype=np.uint8),  # 保留原始数据
        }

        return image_dict

    except Exception as e:
        logger.error(f"数据包转换失败: {e}")
        return None

def packet_to_image(packet: Any) -> np.ndarray | None:
    """
    将 cosine_bus 数据包直接转换为 OpenCV 图像

    Args:
        packet: cosine_bus 数据包

    Returns:
        OpenCV 格式的图像数组，如果转换失败则返回 None
    """
    # 验证数据包有效性
    if packet is None or str(packet.type) != "PacketType.IMAGE":
        logger.warning("无效的数据包或非图像类型")
        return None

    # 获取图像数据
    image_data = packet.get_image_data()
    if image_data is None:
        logger.warning("数据包中图像数据为空")
        return None

    try:
        # 提取图像参数
        encoding = str(packet.encoding_format)
        color_format = str(packet.color_format)
        width = int(packet.image_width)
        height = int(packet.image_height)

        # 根据编码格式处理图像
        if encoding in ["EncodingFormat.JPEG", "EncodingFormat.PNG"]:
            # 处理压缩格式图像
            frame = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
            if frame is not None:
                # 转换为 BGR 格式（OpenCV 默认格式）
                if color_format == "ColorFormat.RGB":
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                return frame
            else:
                logger.warning(f"无法解码 {encoding} 格式图像")
                return None
        else:
            # 处理原始格式图像
            if color_format == "ColorFormat.RS2_FORMAT_Z16":
                # 深度图像处理
                image_depth = np.frombuffer(image_data, dtype=np.uint16)
                frame = image_depth.reshape(height, width)
                return frame
            else:
                # RGB/BGR 图像处理
                frame = np.frombuffer(image_data, dtype=np.uint8).reshape(height, width, 3)
                if color_format == "ColorFormat.RGB":
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                return frame

    except Exception as e:
        logger.error(f"图像解码失败: {e}")
        return None

def dict_to_image(image_dict: dict[str, Any]) -> np.ndarray | None:
    """
    将字典格式的图像数据转换为 OpenCV 图像

    Args:
        image_dict: 包含图像数据的字典，必须包含 encoding, color_format, width, height, image_data

    Returns:
        OpenCV 格式的图像数组，如果转换失败则返回 None
    """
    # 验证字典格式
    required_keys = ["encoding", "color_format", "width", "height", "image_data"]
    if not all(key in image_dict for key in required_keys):
        logger.error(f"字典格式错误：缺少必要的键 {required_keys}")
        return None

    try:
        # 提取图像参数
        encoding = image_dict["encoding"]
        color_format = image_dict["color_format"]
        width = image_dict["width"]
        height = image_dict["height"]
        image_data = image_dict["image_data"]

        # 根据编码格式处理图像
        if encoding in ["EncodingFormat.JPEG", "EncodingFormat.PNG"]:
            # 处理压缩格式图像
            frame = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
            if frame is not None:
                # 转换为 BGR 格式
                if color_format == "ColorFormat.RGB":
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                return frame
            else:
                logger.warning(f"无法解码 {encoding} 格式图像")
                return None
        else:
            # 处理原始格式图像
            if color_format == "ColorFormat.RS2_FORMAT_Z16":
                # 深度图像处理
                image_depth = np.frombuffer(image_data, dtype=np.uint16)
                frame = image_depth.reshape(height, width)
                return frame
            else:
                # RGB/BGR 图像处理
                frame = np.frombuffer(image_data, dtype=np.uint8).reshape(height, width, 3)
                if color_format == "ColorFormat.RGB":
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                return frame

    except Exception as e:
        logger.error(f"字典转图像失败: {e}")
        return None
