import sys
import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import httpx
from httpx._models import Response
import logging

logger = logging.getLogger(name=__name__)

app = Server("corobot_mcp_server")

# CoRobot API 配置
corobot_base_url = "http://localhost:8765"

# 策略服务器默认配置
# DEFAULT_POLICY_HOST = "10.204.143.220"
DEFAULT_POLICY_HOST = "127.0.0.1"
DEFAULT_POLICY_PORT = 8999

# set_evaluate_params 成功后，等待此时间（秒）再自动启动任务，确保参数设置已完全生效
AUTO_START_DELAY_S = 1.0

SKILL_TOOL_ENDPOINTS = {
    "get_skill_status": ("GET", "/skill/status"),
    "reset_robot": ("POST", "/skill/reset_robot"),
    "get_eef_pose": ("POST", "/skill/get_eef_pose"),
    "get_camera_views": ("GET", "/skill/camera_views"),
    "detect_tags": ("POST", "/skill/detect_tags"),
    "get_apriltag_pose": ("POST", "/skill/get_tag_pose"),
    "move_eef": ("POST", "/skill/move_eef"),
    "open_gripper": ("POST", "/skill/gripper"),
    "close_gripper": ("POST", "/skill/gripper"),
    "switch_scene": ("POST", "/skill/switch_scene"),
    "start_policy": ("POST", "/skill/start_policy"),
    "get_policy_status": ("GET", "/skill/policy_status"),
}


@app.call_tool()
async def fetch_tool(name: str, arguments: dict[str, str]) -> list[types.TextContent]:
    result: list[types.TextContent] = []
    if name == "start_task":
        await start_task(result)
    elif name == "stop_task":
        await stop_task(result)
    elif name == "reset_task":
        await reset_task(result)
    elif name == "set_prompt":
        await set_prompt(result, arguments)
    elif name == "set_evaluate_params":
        await set_evaluate_params(result, arguments)
    elif name == "get_status":
        await get_status(result)
    elif name == "get_prompt":
        await get_prompt(result)
    elif name in SKILL_TOOL_ENDPOINTS:
        await call_skill_tool(result, name, arguments)
    else:
        result.append(types.TextContent(type="text", text=f"非法的工具名请求: {name}"))
    return result


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="start_task",
            description="启动CoRobot PolicyTask，使其开始执行任务（注意：set_evaluate_params会自动启动任务，通常不需要单独调用此工具）",
            inputSchema={
                "type": "object",
                "required": [],
                "properties": {},
            },
        ),
        types.Tool(
            name="stop_task",
            description="停止CoRobot PolicyTask，使其停止执行任务（停止机器人当前任务）",
            inputSchema={
                "type": "object",
                "required": [],
                "properties": {},
            },
        ),
        types.Tool(
            name="reset_task",
            description="重置CoRobot PolicyTask，停止当前任务并重置机器人到初始位姿",
            inputSchema={
                "type": "object",
                "required": [],
                "properties": {},
            },
        ),
        types.Tool(
            name="set_prompt",
            description="设置CoRobot PolicyTask的任务提示词，一般不用",
            inputSchema={
                "type": "object",
                "required": ["prompt"],
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "任务提示词，描述要执行的任务",
                    },
                },
            },
        ),
        types.Tool(
            name="get_prompt",
            description="获取CoRobot PolicyTask的当前任务提示词",
            inputSchema={
                "type": "object",
                "required": [],
                "properties": {},
            },
        ),
        types.Tool(
            name="set_evaluate_params",
            description="每当用户输入新的任务指令时都需要该工具，用于设置CoRobot PolicyTask的任务参数，设置成功后会自动启动任务，决定了机器人执行任务的策略、提示词和步间隔。如果未提供策略服务器配置，将使用默认值（host: 127.0.0.1, port: 8999）",
            inputSchema={
                "type": "object",
                "required": ["evaluate_params"],
                "properties": {
                    "evaluate_params": {
                        "type": "object",
                        "description": "评估参数配置",
                        "required": ["prompt"],
                        "properties": {
                            "policy": {
                                "type": "object",
                                "description": "策略服务器配置（可选，未提供时使用默认值）",
                                "required": [],
                                "properties": {
                                    "host": {
                                        "type": "string",
                                        "description": "策略服务器主机地址（可选，默认: 127.0.0.1）",
                                    },
                                    "port": {
                                        "type": "integer",
                                        "description": "策略服务器端口（可选，默认: 8999）",
                                    },
                                },
                            },
                            "prompt": {
                                "type": "string",
                                "description": "任务提示词，根据用户任务指令，描述要执行的任务",
                            },
                            "step_interval": {
                                "type": "number",
                                "description": "执行步间隔（秒），可选，默认: 1.5",
                            },
                        },
                    },
                },
            },
        ),
        types.Tool(
            name="get_status",
            description="获取CoRobot PolicyTask的详细状态信息",
            inputSchema={
                "type": "object",
                "required": [],
                "properties": {},
            },
        ),
        *_mcp_control_tools(),
    ]


def _mcp_control_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_skill_status",
            description="获取 mcp_control_demo deterministic skill API 状态。",
            inputSchema={"type": "object", "required": [], "properties": {}},
        ),
        types.Tool(
            name="reset_robot",
            description="按 mcp_control_demo 安全初始位姿复位机器人：先复位夹爪，再复位双臂、头部和腰部。",
            inputSchema={"type": "object", "required": [], "properties": {}},
        ),
        types.Tool(
            name="get_eef_pose",
            description="读取指定左/右臂当前夹爪中心 TCP 位姿，并返回底层 wrist/link7 位姿。",
            inputSchema={
                "type": "object",
                "required": ["arm"],
                "properties": {
                    "arm": {"type": "string", "enum": ["left", "right"]},
                },
            },
        ),
        types.Tool(
            name="get_camera_views",
            description="读取机器人三视角相机图像，默认返回 head、hand_left、hand_right 及拼接图的 base64。",
            inputSchema={
                "type": "object",
                "required": [],
                "properties": {
                    "cameras": {
                        "type": "string",
                        "default": "head,hand_left,hand_right",
                        "description": "逗号分隔的相机名，默认三视角 head,hand_left,hand_right。",
                    },
                    "format": {"type": "string", "enum": ["jpg", "jpeg", "png"], "default": "jpg"},
                    "include_images": {"type": "boolean", "default": True},
                    "concatenate": {"type": "boolean", "default": True},
                    "jpeg_quality": {"type": "integer", "minimum": 1, "maximum": 100, "default": 85},
                    "save_images": {"type": "boolean", "default": True},
                    "save_dir": {
                        "type": "string",
                        "default": "/home/ck/RoboClaw/artifacts/test_camera",
                        "description": "保存三视角图片的目录。",
                    },
                },
            },
        ),
        types.Tool(
            name="detect_tags",
            description="从 CoRobot 当前 observation 刷新 AprilTag 检测缓存。",
            inputSchema={"type": "object", "required": [], "properties": {}},
        ),
        types.Tool(
            name="get_apriltag_pose",
            description="按 tag_id 查询相机坐标系下的 AprilTag 位姿。",
            inputSchema={
                "type": "object",
                "required": ["tag_id"],
                "properties": {
                    "tag_id": {"type": "integer"},
                    "allow_stale": {"type": "boolean", "default": False},
                },
            },
        ),
        types.Tool(
            name="move_eef",
            description="以相机坐标系夹爪中心 TCP 目标点移动指定左/右臂，底层仍下发 wrist/link7 轨迹，控制频率固定 30Hz。",
            inputSchema={
                "type": "object",
                "required": ["arm", "target_position_camera_m"],
                "properties": {
                    "arm": {"type": "string", "enum": ["left", "right"]},
                    "target_position_camera_m": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "target_orientation_camera_xyzw": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "duration_s": {"type": "number", "default": 1.0},
                    "gripper_value": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
            },
        ),
        types.Tool(
            name="open_gripper",
            description="打开指定左/右夹爪，控制频率固定 30Hz。",
            inputSchema={
                "type": "object",
                "required": ["arm"],
                "properties": {
                    "arm": {"type": "string", "enum": ["left", "right"]},
                    "duration_s": {"type": "number", "default": 0.5},
                },
            },
        ),
        types.Tool(
            name="close_gripper",
            description="关闭指定左/右夹爪，控制频率固定 30Hz。",
            inputSchema={
                "type": "object",
                "required": ["arm"],
                "properties": {
                    "arm": {"type": "string", "enum": ["left", "right"]},
                    "duration_s": {"type": "number", "default": 0.5},
                },
            },
        ),
        types.Tool(
            name="switch_scene",
            description="按 AprilTag 按钮切换场景：内部固定 head_camera_optical，按压间隔 press_interval_s 可控。",
            inputSchema={
                "type": "object",
                "required": [],
                "properties": {
                    "arm": {"type": "string", "enum": ["left", "right"], "default": "right"},
                    "button_tag_id": {"type": "integer", "default": 21},
                    "base_offset_m": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": [0.0, 0.0, 0.01],
                    },
                    "move_duration_s": {"type": "number", "default": 2.0},
                    "gripper_duration_s": {"type": "number", "default": 0.5},
                    "press_interval_s": {"type": "number", "default": 3.0},
                },
            },
        ),
        types.Tool(
            name="start_policy",
            description="启动后台 policy skill：通过 websocket 发送三视角观测和 prompt，按 chunk_count 执行远端返回的 action chunk，结束或失败后自动复位机器人。",
            inputSchema={
                "type": "object",
                "required": ["prompt", "port", "chunk_count"],
                "properties": {
                    "prompt": {"type": "string", "description": "发送给远端 policy 的任务提示词。"},
                    "port": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65535,
                        "description": "本机 policy websocket 端口。",
                    },
                    "chunk_count": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "执行多少个完整 policy action chunk 后自动停止并复位。",
                    },
                },
            },
        ),
        types.Tool(
            name="get_policy_status",
            description="获取后台 policy skill 当前状态、最新执行 action 和累计 chunk 数。",
            inputSchema={"type": "object", "required": [], "properties": {}},
        ),
    ]


### 工具实现 ###


async def start_task(result: list[types.TextContent]):
    """启动任务"""
    url = f"{corobot_base_url}/system/start_policytask"
    await send_request_to_corobot(result, url, "POST")


async def stop_task(result: list[types.TextContent]):
    """停止任务"""
    url = f"{corobot_base_url}/system/stop_policytask"
    await send_request_to_corobot(result, url, "POST")


async def reset_task(result: list[types.TextContent]):
    """重置任务"""
    url = f"{corobot_base_url}/system/reset_policytask"
    await send_request_to_corobot(result, url, "POST")


async def set_prompt(result: list[types.TextContent], arguments: dict):
    """设置提示词"""
    prompt = arguments.get("prompt")
    if not prompt:
        result.append(types.TextContent(type="text", text="错误: 缺少必需的参数 'prompt'"))
        return

    url = f"{corobot_base_url}/set_prompt"
    await send_request_to_corobot(result, url, "POST", {"prompt": prompt})


async def get_prompt(result: list[types.TextContent]):
    """获取提示词"""
    url = f"{corobot_base_url}/get_prompt"
    await send_request_to_corobot(result, url, "GET")


async def set_evaluate_params(result: list[types.TextContent], arguments: dict):
    """设置评估参数，成功后自动启动任务"""
    evaluate_params = arguments.get("evaluate_params")
    if not evaluate_params:
        result.append(types.TextContent(type="text", text="错误: 缺少必需的参数 'evaluate_params'"))
        logger.error("set_evaluate_params: 缺少必需的参数 'evaluate_params'")
        return

    logger.info(f"set_evaluate_params: 接收到的原始参数 = {evaluate_params}")

    # 确保 policy 配置存在，使用默认值填充缺失的字段
    if "policy" not in evaluate_params:
        evaluate_params["policy"] = {}

    policy = evaluate_params["policy"]
    if "host" not in policy or not policy["host"]:
        policy["host"] = DEFAULT_POLICY_HOST
    if "port" not in policy or not policy["port"]:
        policy["port"] = DEFAULT_POLICY_PORT

    logger.info(f"set_evaluate_params: 处理后的参数 = {evaluate_params}")

    url = f"{corobot_base_url}/set_evaluate_params"
    request_payload = {"evaluate_params": evaluate_params}
    logger.info(f"set_evaluate_params: 发送的请求体 = {request_payload}")

    success = await send_request_to_corobot(result, url, "POST", request_payload)

    # 如果设置成功，等待一段时间确保参数设置已完全生效，然后自动启动任务
    if success:
        logger.info(f"set_evaluate_params: 请求成功，等待 {AUTO_START_DELAY_S} 秒后自动启动任务")
        result.append(types.TextContent(type="text", text=f"\n[等待 {AUTO_START_DELAY_S} 秒后自动启动任务]"))
        await anyio.sleep(AUTO_START_DELAY_S)
        await start_task(result)
    else:
        logger.error("set_evaluate_params: 请求失败")


async def get_status(result: list[types.TextContent]):
    """获取状态"""
    # #region agent log
    import json
    import time

    try:
        with open("/home/agiuser/Project_Olympus_zyl/.cursor/debug.log", "a") as f:
            f.write(
                json.dumps(
                    {
                        "id": f"log_{int(time.time()*1000)}",
                        "timestamp": int(time.time() * 1000),
                        "location": "server.py:225",
                        "message": "get_status called",
                        "data": {"function": "get_status"},
                        "runId": "debug",
                        "hypothesisId": "A",
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    url = f"{corobot_base_url}/status"
    await send_request_to_corobot(result, url, "GET")
    # #region agent log
    try:
        with open("/home/agiuser/Project_Olympus_zyl/.cursor/debug.log", "a") as f:
            f.write(
                json.dumps(
                    {
                        "id": f"log_{int(time.time()*1000)}",
                        "timestamp": int(time.time() * 1000),
                        "location": "server.py:228",
                        "message": "get_status completed",
                        "data": {"function": "get_status"},
                        "runId": "debug",
                        "hypothesisId": "A",
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


async def call_skill_tool(result: list[types.TextContent], name: str, arguments: dict):
    """Forward mcp_control_demo primitive tools to the CoRobot skill API."""
    method, path = SKILL_TOOL_ENDPOINTS[name]
    payload = dict(arguments or {})
    if name == "open_gripper":
        payload["gripper_value"] = 0.0
    elif name == "close_gripper":
        payload["gripper_value"] = 1.0
    for fixed_key in (
        "camera_frame",
        "close_gripper_value",
        "lift_dz_base_m",
        "press_hold_s",
    ):
        payload.pop(fixed_key, None)
    if "control_hz" in payload or "control_frequency_hz" in payload:
        result.append(types.TextContent(type="text", text="错误: 控制频率固定为 30Hz，不能通过工具参数覆盖"))
        return
    url = f"{corobot_base_url}{path}"
    await send_request_to_corobot(
        result,
        url,
        method,
        json_data=payload if method != "GET" else None,
        query_params=payload if method == "GET" else None,
    )


# 给CoRobot发HTTP请求
async def send_request_to_corobot(
    result: list[types.TextContent],
    url: str,
    method: str = "POST",
    json_data: dict | None = None,
    query_params: dict | None = None,
) -> bool:
    """
    发送HTTP请求到CoRobot
    返回: True表示成功，False表示失败
    """
    headers = {"Content-Type": "application/json"}
    timeout = httpx.Timeout(timeout=180.0)

    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        try:
            if method == "GET":
                response_data: Response = await client.get(url, headers=headers, params=query_params)
            else:
                response_data: Response = await client.post(url, headers=headers, json=json_data)

            response_data.raise_for_status()  # 如果状态码不是2xx会抛出异常

            # 尝试解析JSON响应
            try:
                response_json = response_data.json()
                # 格式化输出
                if isinstance(response_json, dict):
                    if "success" in response_json and "data" in response_json:
                        if response_json["success"]:
                            result.append(
                                types.TextContent(
                                    type="text",
                                    text=f"成功: {response_json.get('data', response_json)}",
                                )
                            )
                            return True
                        else:
                            result.append(
                                types.TextContent(
                                    type="text",
                                    text=f"失败: {response_json.get('message', response_json.get('data', response_json))}",
                                )
                            )
                            return False
                    else:
                        result.append(types.TextContent(type="text", text=str(response_json)))
                        return True  # 没有success字段时，假设成功
                else:
                    result.append(types.TextContent(type="text", text=str(response_json)))
                    return True  # 非字典响应，假设成功
            except Exception:
                # 如果不是JSON，返回文本
                result.append(types.TextContent(type="text", text=response_data.text))
                return True  # 非JSON响应，假设成功

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP错误 {e.response.status_code}: {e.response.text}"
            logger.error(error_msg)
            result.append(types.TextContent(type="text", text=error_msg))
            return False
        except httpx.RequestError as e:
            error_msg = f"请求失败: {e}"
            logger.error(error_msg)
            result.append(
                types.TextContent(type="text", text=f"连接CoRobot失败，请确保CoRobot服务正在运行在 {corobot_base_url}")
            )
            return False
        except Exception as e:
            error_msg = f"未知错误: {e}"
            logger.error(error_msg)
            result.append(types.TextContent(type="text", text=error_msg))
            return False


def main() -> int:
    async def arun():
        async with stdio_server() as streams:
            await app.run(streams[0], streams[1], app.create_initialization_options())

    anyio.run(arun)

    return 0


if __name__ == "__main__":
    sys.exit(main())  # type: ignore[call-arg]
