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
                "camera_frame": {"type": "string", "default": "head_camera_optical"},
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
                "camera_frame": {"type": "string", "default": "head_camera_optical"},
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
        "name": "lift_eef",
        "description": "按配置的 camera_lift_axis 抬升指定 EEF，控制频率固定 30Hz。",
        "inputSchema": {
            "type": "object",
            "required": ["arm", "distance_m"],
            "properties": {
                "arm": {"type": "string", "enum": ["left", "right"]},
                "camera_frame": {"type": "string", "default": "head_camera_optical"},
                "distance_m": {"type": "number"},
                "duration_s": {"type": "number", "default": 1.0},
            },
        },
    },
    {
        "name": "place_down",
        "description": "按配置的 camera_place_down_axis 下降 EEF，并可在下降后打开夹爪。",
        "inputSchema": {
            "type": "object",
            "required": ["arm", "down_distance_m"],
            "properties": {
                "arm": {"type": "string", "enum": ["left", "right"]},
                "camera_frame": {"type": "string", "default": "head_camera_optical"},
                "down_distance_m": {"type": "number"},
                "duration_s": {"type": "number", "default": 1.0},
                "open_after_down": {"type": "boolean", "default": True},
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
        "name": "grasp_by_tag",
        "description": "根据 tag_id 的相机坐标执行 approach、descend、close、lift 抓取序列。",
        "inputSchema": {
            "type": "object",
            "required": ["arm", "tag_id"],
            "properties": {
                "arm": {"type": "string", "enum": ["left", "right"]},
                "tag_id": {"type": "integer"},
                "camera_frame": {"type": "string", "default": "head_camera_optical"},
                "approach_distance_m": {"type": "number", "default": 0.06},
                "lift_height_m": {"type": "number", "default": 0.10},
                "move_duration_s": {"type": "number", "default": 1.0},
                "gripper_duration_s": {"type": "number", "default": 0.5},
            },
        },
    },
]
