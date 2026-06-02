MCP_CONTROL_TOOL_SCHEMAS = [
    {
        "name": "get_skill_status",
        "description": "获取 mcp_control_demo deterministic skill API 状态。",
        "inputSchema": {"type": "object", "required": [], "properties": {}},
    },
    {
        "name": "reset_robot",
        "description": "按 mcp_control_demo 安全初始位姿复位机器人：先复位夹爪，再复位双臂、头部和腰部。",
        "inputSchema": {"type": "object", "required": [], "properties": {}},
    },
    {
        "name": "get_eef_pose",
        "description": "读取指定左/右臂当前夹爪中心 TCP 位姿，并返回底层 wrist/link7 位姿。",
        "inputSchema": {
            "type": "object",
            "required": ["arm"],
            "properties": {
                "arm": {"type": "string", "enum": ["left", "right"]},
            },
        },
    },
    {
        "name": "get_camera_views",
        "description": "读取机器人三视角相机图像，默认返回 head、hand_left、hand_right 及拼接图的 base64。",
        "inputSchema": {
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
    },
    {
        "name": "detect_tags",
        "description": "从 CoRobot 当前 observation 刷新 AprilTag 检测缓存。",
        "inputSchema": {"type": "object", "required": [], "properties": {}},
    },
    {
        "name": "get_apriltag_pose",
        "description": "按 tag_id 查询相机坐标系下的 AprilTag 位姿。",
        "inputSchema": {
            "type": "object",
            "required": ["tag_id"],
            "properties": {
                "tag_id": {"type": "integer"},
                "allow_stale": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "move_eef",
        "description": "以相机坐标系夹爪中心 TCP 目标点移动指定左/右臂，底层仍下发 wrist/link7 轨迹，控制频率固定 30Hz。",
        "inputSchema": {
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
    },
    {
        "name": "open_gripper",
        "description": "打开指定左/右夹爪，控制频率固定 30Hz。",
        "inputSchema": {
            "type": "object",
            "required": ["arm"],
            "properties": {
                "arm": {"type": "string", "enum": ["left", "right"]},
                "duration_s": {"type": "number", "default": 0.5},
            },
        },
    },
    {
        "name": "close_gripper",
        "description": "关闭指定左/右夹爪，控制频率固定 30Hz。",
        "inputSchema": {
            "type": "object",
            "required": ["arm"],
            "properties": {
                "arm": {"type": "string", "enum": ["left", "right"]},
                "duration_s": {"type": "number", "default": 0.5},
            },
        },
    },
    {
        "name": "switch_scene",
        "description": "按 AprilTag 按钮切换场景：内部使用 head_camera_optical，按压间隔 press_interval_s 可控。",
        "inputSchema": {
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
    },
    {
        "name": "start_policy",
        "description": "启动后台 policy skill：通过 websocket 发送三视角观测和 prompt，按 chunk_count 执行远端返回的 action chunk，结束或失败后自动复位机器人。",
        "inputSchema": {
            "type": "object",
            "required": ["prompt", "port", "chunk_count"],
            "properties": {
                "prompt": {"type": "string", "description": "发送给远端 policy 的任务提示词。"},
                "port": {"type": "integer", "minimum": 1, "maximum": 65535, "description": "本机 policy websocket 端口。"},
                "chunk_count": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "执行多少个完整 policy action chunk 后自动停止并复位。",
                },
            },
        },
    },
    {
        "name": "get_policy_status",
        "description": "获取后台 policy skill 当前状态、最新执行 action 和累计 chunk 数。",
        "inputSchema": {"type": "object", "required": [], "properties": {}},
    },
]
